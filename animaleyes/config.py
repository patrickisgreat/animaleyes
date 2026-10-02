"""Runtime settings from config.toml plus secrets from the environment.

Settings are re-read on every loop so dashboard edits take effect without a restart.
"""

from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import asdict, dataclass, fields
from datetime import time
from pathlib import Path

import tomli_w

log = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path(os.environ.get("ANIMALEYES_CONFIG", "config.toml"))


@dataclass
class Settings:
    ENABLED: bool = True
    DRY_RUN: bool = True
    ACTIVE_START: str = "21:00"
    ACTIVE_END: str = "06:00"
    MIN_GAP_MIN: int = 60
    LEAVE_TIMEOUT_S: int = 120
    FEEDING_MAX_MIN: int = 15
    FEEDING_POLL_S: int = 20
    CONFIRMATIONS_REQUIRED: int = 3
    GRRR_MIN_CONF: float = 0.65  # Grrr reads ~0.72-0.9 in IR; 0.8 was a touch high
    MOTION_PIXEL_FRACTION: float = 0.02
    MOTION_HOLD_S: int = 20
    MOTION_SOURCE: str = "camera"  # "camera" = ONVIF motion events; "frames" = frame-diff
    FEED_RETRY_BACKOFF_S: int = 120  # after a failed open, wait before trying to open again
    LID_POLL_S: int = 20  # how often to read the feeder's real lid state (0 = never)
    # Dashboard auth: "tailscale" = trust any device on the tailnet (no password), basic-auth
    # fallback off-tailnet; "basic" = always require the password; "none" = open (don't).
    DASH_AUTH: str = "tailscale"
    LLM_MIN_INTERVAL_S: int = 3
    HEARTBEAT_MIN: int = 5
    LLM_MODEL: str = "claude-opus-5"
    # "cascade" = local YOLO gate (dog/cat/none, free) that only escalates to Claude when a
    # dog is present (accurate + cheap); "claude" = always Claude; "yolo" = local only.
    IDENTIFIER: str = "cascade"
    YOLO_MODEL: str = "yolo11n.pt"  # pretrained COCO; dog/cat/person
    YOLO_MIN_CONF: float = 0.45
    GRRR_MAX_BOX_FRACTION: float = 0.5  # YOLO-only mode: dog box this fraction or smaller = Grrr
    # The open decision needs Grrr confirmed at the bowl. The bowl is closed/empty before a
    # feed, so she hovers near it rather than head-in-bowl; requiring at_bowl blocks nearly
    # every feed, so default off (presence is enough; identity still has to be Grrr).
    OPEN_REQUIRES_AT_BOWL: bool = False
    # Auto-collect labelled IR crops for a future local classifier: when the cascade's Claude
    # step confidently says Grrr/Bowie, save that frame under data/training/<animal>/.
    COLLECT_TRAINING: bool = True
    TRAINING_MIN_CONF: float = 0.6
    TRAINING_MIN_GAP_S: int = 5  # don't save more than one crop this often (avoid bursts)

    def active_window(self) -> tuple[time, time]:
        return parse_hhmm(self.ACTIVE_START), parse_hhmm(self.ACTIVE_END)

    def is_active_at(self, t: time) -> bool:
        """True when `t` is inside [ACTIVE_START, ACTIVE_END], which may cross midnight."""
        start, end = self.active_window()
        if start <= end:
            return start <= t < end
        return t >= start or t < end


def parse_hhmm(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def coerce(settings: Settings, key: str, raw: object) -> object:
    """Convert a dashboard form value to the field's declared type."""
    field_type = {f.name: f.type for f in fields(settings)}[key]
    if field_type in ("bool", bool):
        return str(raw).lower() in ("1", "true", "on", "yes")
    if field_type in ("int", int):
        return int(raw)  # type: ignore[call-overload]
    if field_type in ("float", float):
        return float(raw)  # type: ignore[arg-type]
    value = str(raw)
    if key in ("ACTIVE_START", "ACTIVE_END"):
        parse_hhmm(value)  # validate
    return value


class ConfigStore:
    """Loads and saves config.toml. Unknown keys are preserved on save."""

    def __init__(self, path: Path = DEFAULT_CONFIG_PATH):
        self.path = path

    def load(self) -> Settings:
        if not self.path.exists():
            return Settings()
        known = {f.name for f in fields(Settings)}
        try:
            data = tomllib.loads(self.path.read_text())
            settings = Settings(**{k: v for k, v in data.items() if k in known})
            self._last_good = settings
            return settings
        except Exception as exc:
            # A malformed config.toml (e.g. a hand/dashboard edit with a syntax error) must
            # never crash-loop the daemon. Fall back to the last good settings, else defaults.
            log.error("config.toml invalid (%s); using last-good/defaults", exc)
            return getattr(self, "_last_good", None) or Settings()

    def update(self, changes: dict[str, object]) -> Settings:
        current = self.load()
        for key, raw in changes.items():
            if key not in {f.name for f in fields(Settings)}:
                raise KeyError(key)
            setattr(current, key, coerce(current, key, raw))
        self.path.write_text(tomli_w.dumps(asdict(current)))
        return current


@dataclass(frozen=True)
class Secrets:
    kasa_stream_url: str
    kasa_email: str
    kasa_password: str
    anthropic_api_key: str
    petlibro_serial: str
    slack_webhook: str
    dash_user: str
    dash_password: str
    dash_public_url: str
    kasa_camera_mac: str = ""

    @classmethod
    def from_env(cls) -> Secrets:
        env = os.environ.get
        return cls(
            kasa_stream_url=env("KASA_STREAM_URL", ""),
            kasa_email=env("KASA_EMAIL", ""),
            kasa_password=env("KASA_PASSWORD", ""),
            anthropic_api_key=env("ANTHROPIC_API_KEY", ""),
            petlibro_serial=env("PETLIBRO_SERIAL", ""),
            slack_webhook=env("SLACK_WEBHOOK", ""),
            dash_user=env("DASH_USER", ""),
            dash_password=env("DASH_PASSWORD", ""),
            dash_public_url=env("DASH_PUBLIC_URL", "http://localhost:8081").rstrip("/"),
            kasa_camera_mac=env("KASA_CAMERA_MAC", ""),
        )
