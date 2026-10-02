"""Process entry point: wires camera, motion, LLM, feeder, store, Slack, the state machine,
and the dashboard together. The machine ticks on a background thread; uvicorn owns the main
thread so signals shut everything down cleanly.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

from .camera import Camera, FrameBuffer
from .config import DEFAULT_CONFIG_PATH, ConfigStore, Secrets
from .dashboard import create_app
from .detect import Identifier
from .events import ReolinkEvents
from .feeder import DryRunFeeder, FeederError, PetlibroCli
from .machine import Machine
from .motion import MotionDetector
from .notify import SlackNotifier
from .store import Store
from .vision import ClaudeIdentifier

log = logging.getLogger("animaleyes")

DATA_DIR = Path(os.environ.get("ANIMALEYES_DATA", "data"))
TICK_S = 0.5
LOOP_ERROR_REPEAT_S = 600


class UnavailableFeeder:
    """Stands in for the real feeder when PETLIBRO_SERIAL is missing, so DRY_RUN still works."""

    def __init__(self, error: str):
        self.error = error

    def current_plate(self) -> int:
        raise FeederError(self.error)

    def rotate(self) -> None:
        raise FeederError(self.error)

    def open_now(self, plate: int) -> None:
        raise FeederError(self.error)

    def close(self) -> None:
        raise FeederError(self.error)

    def manual_feed_active(self) -> bool:
        raise FeederError(self.error)


def _yolo(config: ConfigStore):
    from .yolo import YoloConfig, YoloIdentifier

    s = config.load()
    return YoloIdentifier(
        s.YOLO_MODEL,
        YoloConfig(min_conf=s.YOLO_MIN_CONF, grrr_max_box_fraction=s.GRRR_MAX_BOX_FRACTION),
    )


def _claude(config: ConfigStore, store: Store):
    def descriptions() -> dict[str, str]:
        s = config.load()
        return {"grrr": s.GRRR_DESC, "bowie": s.BOWIE_DESC, "cat": s.CAT_DESC}

    return ClaudeIdentifier(
        DATA_DIR / "reference",
        store,
        model=lambda: config.load().LLM_MODEL,
        descriptions=descriptions,
    )


def build_identifier(name: str, config: ConfigStore, store: Store):
    """Select the detector backend by config. Backends share the Identifier interface, so the
    state machine is unchanged whichever is chosen."""
    if name == "yolo":
        log.info("identifier: YOLO (local only)")
        return _yolo(config)
    if name == "cascade":
        from .cascade import CascadeIdentifier

        log.info("identifier: cascade (YOLO gate → Claude confirm)")
        return CascadeIdentifier(
            gate=_yolo(config),
            confirm=_claude(config, store),
            config=config,
            training_dir=DATA_DIR / "training",
        )
    log.info("identifier: Claude (%s)", config.load().LLM_MODEL)
    return _claude(config, store)


def build(
    secrets: Secrets,
) -> tuple[Machine, Camera, ConfigStore, Store, Identifier, ReolinkEvents | None]:
    config = ConfigStore(DEFAULT_CONFIG_PATH)
    store = Store(DATA_DIR / "db" / "animaleyes.sqlite")
    frames = FrameBuffer()
    motion = MotionDetector()
    camera = Camera(secrets, frames, on_frame=motion.feed)
    llm = build_identifier(config.load().IDENTIFIER, config, store)
    events: ReolinkEvents | None = None
    if secrets.kasa_stream_url:
        # Camera-driven motion gate (ONVIF). Keeps the LLM from firing on frame-diff noise.
        events = ReolinkEvents(secrets.kasa_stream_url, secrets.kasa_camera_mac)
    try:
        feeder = PetlibroCli(secrets.petlibro_serial)
    except FeederError as exc:
        log.warning("real feeder unavailable: %s", exc)
        feeder = UnavailableFeeder(str(exc))  # type: ignore[assignment]
    machine = Machine(
        config=config,
        store=store,
        frames=frames,
        motion=motion,
        llm=llm,
        feeder=feeder,
        dry_feeder=DryRunFeeder(),
        notifier=SlackNotifier(secrets.slack_webhook),
        clock=datetime.now,
        frames_dir=DATA_DIR / "frames",
        dashboard_url=secrets.dash_public_url,
        events=events,
    )
    return machine, camera, config, store, llm, events


def run_loop(machine: Machine, stop: threading.Event) -> None:
    last_error_at = 0.0
    while not stop.is_set():
        try:
            machine.tick()
        except Exception:  # noqa: BLE001 - the loop must survive anything
            log.exception("tick failed")
            if time.monotonic() - last_error_at > LOOP_ERROR_REPEAT_S:
                last_error_at = time.monotonic()
                machine.notifier.send("animaleyes loop error, see logs")
        stop.wait(TICK_S)


def main() -> None:
    load_dotenv()
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    secrets = Secrets.from_env()
    if not (secrets.dash_user and secrets.dash_password):
        raise SystemExit("DASH_USER and DASH_PASSWORD must be set")
    machine, camera, config, store, llm, events = build(secrets)
    if secrets.kasa_stream_url:
        camera.start()
        if events is not None:
            events.start()
    else:
        log.warning("KASA_STREAM_URL is not set; running without a camera")
    machine.start()
    stop = threading.Event()
    worker = threading.Thread(target=run_loop, args=(machine, stop), name="machine", daemon=True)
    worker.start()
    app = create_app(
        machine,
        store,
        config,
        secrets,
        DATA_DIR / "frames",
        reload_references=llm.reload_references,
        reference_dir=DATA_DIR / "reference",
        training_dir=DATA_DIR / "training",
    )
    try:
        uvicorn.run(
            app,
            host=os.environ.get("HOST", "127.0.0.1"),
            port=int(os.environ.get("PORT", "8081")),
            log_level="warning",
        )
    finally:
        stop.set()
        camera.stop()
        if events is not None:
            events.stop()
        worker.join(timeout=5)


if __name__ == "__main__":
    main()
