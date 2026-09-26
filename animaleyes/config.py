"""Runtime settings from config.toml plus secrets from the environment.

Settings are re-read on every loop so dashboard edits take effect without a restart.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, dataclass, fields
from datetime import time
from pathlib import Path

import tomli_w

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
    GRRR_MIN_CONF: float = 0.8
    MOTION_PIXEL_FRACTION: float = 0.02
    MOTION_HOLD_S: int = 20
    LLM_MIN_INTERVAL_S: int = 3
    HEARTBEAT_MIN: int = 5
    LLM_MODEL: str = "claude-opus-5"

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
        data = tomllib.loads(self.path.read_text())
        known = {f.name for f in fields(Settings)}
        return Settings(**{k: v for k, v in data.items() if k in known})

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
