from __future__ import annotations

from datetime import time

import pytest
from PIL import Image

from animaleyes.camera import (
    arp_lookup,
    build_ffmpeg_command,
    kasa_legacy_password,
    resolve_stream_url,
    split_jpegs,
)
from animaleyes.config import Secrets, Settings
from animaleyes.motion import changed_fraction
from animaleyes.vision import Verdict, estimate_cost_usd


def test_active_window_crossing_midnight() -> None:
    s = Settings(ACTIVE_START="21:00", ACTIVE_END="06:00")
    assert s.is_active_at(time(23, 0))
    assert s.is_active_at(time(2, 0))
    assert not s.is_active_at(time(12, 0))
    assert not s.is_active_at(time(6, 0))


def test_active_window_same_day() -> None:
    s = Settings(ACTIVE_START="08:00", ACTIVE_END="17:00")
    assert s.is_active_at(time(9, 0))
    assert not s.is_active_at(time(20, 0))


def test_split_jpegs_handles_partial_tail() -> None:
    a = b"\xff\xd8AAA\xff\xd9"
    b = b"\xff\xd8BBB\xff\xd9"
    buf = bytearray(b"junk" + a + b + b"\xff\xd8partial")
    assert split_jpegs(buf) == [a, b]
    assert bytes(buf) == b"\xff\xd8partial"
    buf += b"\xff\xd9"
    assert split_jpegs(buf) == [b"\xff\xd8partial\xff\xd9"]


def secrets(url: str, email: str = "", password: str = "") -> Secrets:
    return Secrets(
        kasa_stream_url=url,
        kasa_email=email,
        kasa_password=password,
        anthropic_api_key="",
        petlibro_serial="",
        slack_webhook="",
        dash_user="",
        dash_password="",
        dash_public_url="",
    )


def test_ffmpeg_command_rtsp_uses_tcp() -> None:
    cmd = build_ffmpeg_command(secrets("rtsp://u:p@cam/stream1"))
    assert "-rtsp_transport" in cmd and "-headers" not in cmd
    assert cmd[-1] == "-"


def test_ffmpeg_command_legacy_kasa_uses_basic_auth_with_hashed_password() -> None:
    cmd = build_ffmpeg_command(secrets("https://cam:19443/https/stream/mixed", "me@x.com", "pw"))
    header = cmd[cmd.index("-headers") + 1]
    assert header.startswith("Authorization: Basic ")
    import base64
    import hashlib

    assert kasa_legacy_password("pw") == base64.b64encode(hashlib.sha256(b"pw").digest()).decode()


def test_changed_fraction_zero_for_identical_and_one_for_inverted() -> None:
    black = Image.new("L", (64, 36), 0)
    white = Image.new("L", (64, 36), 255)
    assert changed_fraction(black, black) == 0.0
    assert changed_fraction(black, white) == 1.0


def test_verdict_is_grrr_requires_confidence_bowl_and_solitude() -> None:
    v = Verdict(animal="grrr", confidence=0.9, at_bowl=True)
    assert v.is_grrr(0.8)
    assert not Verdict(animal="grrr", confidence=0.7, at_bowl=True).is_grrr(0.8)
    assert not Verdict(animal="grrr", confidence=0.9, at_bowl=False).is_grrr(0.8)
    assert not Verdict(
        animal="grrr", confidence=0.9, at_bowl=True, other_animals_present=["cat"]
    ).is_grrr(0.8)


def test_cost_estimate_counts_cache_reads_cheaply() -> None:
    cold = estimate_cost_usd(
        "claude-opus-5",
        {"input_tokens": 1000, "cache_creation_input_tokens": 3000, "output_tokens": 100},
    )
    warm = estimate_cost_usd(
        "claude-opus-5",
        {"input_tokens": 1000, "cache_read_input_tokens": 3000, "output_tokens": 100},
    )
    assert warm < cold
    assert round(warm, 6) == round((1000 * 5 + 300 * 5 + 100 * 25) / 1e6, 6)


ARP = """IP address       HW type     Flags       HW address            Mask     Device
10.0.0.1         0x1         0x2         4c:d7:4a:7a:4e:0f     *        eno2
10.0.0.99        0x1         0x0         a8:42:a1:d1:d0:26     *        eno2
10.0.0.216       0x1         0x2         a8:42:a1:d1:d0:26     *        eno2
"""
CAM_MAC = "A8:42:A1:D1:D0:26"


def test_arp_lookup_skips_incomplete_entries_and_ignores_mac_case(tmp_path) -> None:
    table = tmp_path / "arp"
    table.write_text(ARP)
    assert arp_lookup(CAM_MAC, table) == "10.0.0.216"
    assert arp_lookup("00:00:00:00:00:00", table) is None


def test_stream_url_without_placeholder_is_used_as_is() -> None:
    url = "rtsp://u:p@10.0.0.5:554/stream1"
    assert resolve_stream_url(url, "", lookup=lambda m: 1 / 0) == url


def test_camera_is_found_by_mac_after_a_rescan_on_the_stream_port() -> None:
    table: dict[str, str] = {}
    knocked: list[int] = []

    def rescan(port: int) -> None:
        knocked.append(port)
        table[CAM_MAC] = "10.0.0.240"  # the camera took a new lease

    url = resolve_stream_url(
        "https://{host}:19443/https/stream/mixed", CAM_MAC, lookup=table.get, rescan=rescan
    )
    assert url == "https://10.0.0.240:19443/https/stream/mixed"
    assert knocked == [19443]


def test_missing_camera_or_mac_is_an_error_not_a_guess() -> None:
    with pytest.raises(RuntimeError, match="not found"):
        resolve_stream_url("rtsp://{host}/s", CAM_MAC, lookup=lambda m: None, rescan=lambda p: None)
    with pytest.raises(RuntimeError, match="KASA_CAMERA_MAC"):
        resolve_stream_url("rtsp://{host}/s", "")
