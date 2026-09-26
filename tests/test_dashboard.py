from __future__ import annotations

import asyncio
import secrets as secrets_lib
from datetime import datetime

from fastapi.testclient import TestClient

from animaleyes.camera import Frame, FrameBuffer
from animaleyes.config import Secrets
from animaleyes.dashboard import create_app, mjpeg
from tests.conftest import Harness

# Generated per run so no credential-looking literal lives in the repo.
PASSWORD = secrets_lib.token_urlsafe(16)


def client(h: Harness) -> TestClient:
    secrets = Secrets(
        kasa_stream_url="",
        kasa_email="",
        kasa_password="",
        anthropic_api_key="",
        petlibro_serial="",
        slack_webhook="",
        dash_user="me",
        dash_password=PASSWORD,
        dash_public_url="http://dash",
    )
    app = create_app(h.machine, h.store, h.config, secrets, h.tmp_path / "frames")
    c = TestClient(app)
    c.auth = ("me", PASSWORD)
    return c


GUARDED = (
    "/",
    "/api/status",
    "/api/events",
    "/frame.jpg",
    "/stream.mjpg",
    "/events/1",
    "/api/config",
)


def test_every_route_requires_basic_auth(h: Harness) -> None:
    c = client(h)
    for path in GUARDED:
        r = c.get(path, auth=None)
        assert r.status_code == 401
        assert r.headers["www-authenticate"].startswith("Basic")
        assert c.get(path, auth=("me", "wrong")).status_code == 401
        assert c.get(path, auth=("you", PASSWORD)).status_code == 401
    assert c.get("/").status_code == 200
    assert c.get("/healthz", auth=None).json() == {"ok": True}


def test_no_password_configured_locks_everyone_out(h: Harness) -> None:
    secrets = Secrets("", "", "", "", "", "", dash_user="", dash_password="", dash_public_url="")
    c = TestClient(create_app(h.machine, h.store, h.config, secrets, h.tmp_path / "frames"))
    assert c.get("/", auth=("", "")).status_code == 401


def test_mjpeg_yields_each_new_frame_once() -> None:
    frames = FrameBuffer()
    frames.push(Frame(jpeg=b"\xff\xd8one\xff\xd9", at=datetime(2026, 9, 26, 21, 0, 0)))

    async def collect() -> list[bytes]:
        parts = []
        async for part in mjpeg(frames, poll_s=0.01, max_s=0.1):
            parts.append(part)
            if len(parts) == 1:
                frames.push(Frame(jpeg=b"\xff\xd8two\xff\xd9", at=datetime(2026, 9, 26, 21, 0, 1)))
        return parts

    parts = asyncio.run(collect())
    assert len(parts) == 2
    assert parts[0].startswith(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: 7\r\n\r\n")
    assert b"one" in parts[0] and b"two" in parts[1]


def test_status_and_frame(h: Harness) -> None:
    h.load_plates(1)
    h.tick(motion=False)
    c = client(h)
    s = c.get("/api/status").json()
    assert s["state"] == "IDLE"
    assert s["loaded_plates"] == [1]
    assert s["dry_run"] is False
    assert c.get("/frame.jpg").headers["content-type"] == "image/jpeg"


def test_setting_plates_and_config_reach_the_machine(h: Harness) -> None:
    c = client(h)
    h.tick(motion=False)
    assert h.state() == "DONE"
    r = c.post("/api/plates", json={"loaded": [2, 3]})
    assert r.json()["plates"] == {"1": "empty", "2": "loaded", "3": "loaded"}
    h.tick(motion=False)
    assert h.state() == "IDLE"

    r = c.post(
        "/api/config",
        json={"MIN_GAP_MIN": "45", "DRY_RUN": True, "ACTIVE_START": "20:30"},
    )
    assert r.status_code == 200
    h.tick(motion=False)
    assert h.machine.settings.MIN_GAP_MIN == 45
    assert h.machine.settings.DRY_RUN is True
    assert c.post("/api/config", json={"ACTIVE_END": "nope"}).status_code == 400
    assert c.post("/api/config", json={"BOGUS": 1}).status_code == 400


def test_feed_now_and_event_page(h: Harness) -> None:
    c = client(h)
    h.tick(motion=False)
    c.post("/api/feed-now")
    h.tick(motion=False)
    assert h.state() == "FEEDING"
    events = c.get("/api/events").json()
    open_event = next(e for e in events if e["kind"] == "open")
    page = c.get(f"/events/{open_event['id']}")
    assert page.status_code == 200 and "manual feed" in page.text
    assert c.get(f"/frames/{open_event['frames'][0]}").status_code == 200
    assert c.get("/frames/../config.toml").status_code in (404, 400)
