"""The feeding state machine. This is the only code allowed to open or close the feeder.

States: OUTSIDE_WINDOW, IDLE, WATCHING, OPENING, FEEDING, CLOSING, COOLDOWN, DONE.
Every transition is written to the events table with a reason. Anything a restart must
remember (state, open timestamps, counters, plates) is persisted in the Store before the
side effect that depends on it, so a crash between "send feed" and "record feed" resumes as
FEEDING rather than opening again.

All time comes from the injected clock and all outside contact goes through injected
protocols, so the whole thing runs under test with fakes.
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Callable
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from .camera import FrameBuffer
from .config import ConfigStore, Settings
from .feeder import Feeder, FeederError
from .motion import MotionDetector
from .notify import Notifier
from .store import Store
from .vision import FeedingVerdict, Identifier, Verdict

log = logging.getLogger(__name__)

CAMERA_OFFLINE_S = 60
VETO_REPEAT_S = 300
NONE_LEFT_REPEAT_S = 3600
VERDICT_HISTORY = 6


class State(StrEnum):
    OUTSIDE_WINDOW = "OUTSIDE_WINDOW"
    IDLE = "IDLE"
    WATCHING = "WATCHING"
    OPENING = "OPENING"
    FEEDING = "FEEDING"
    CLOSING = "CLOSING"
    COOLDOWN = "COOLDOWN"
    DONE = "DONE"


class Machine:
    def __init__(
        self,
        config: ConfigStore,
        store: Store,
        frames: FrameBuffer,
        motion: MotionDetector,
        llm: Identifier,
        feeder: Feeder,
        dry_feeder: Feeder,
        notifier: Notifier,
        clock: Callable[[], datetime],
        frames_dir: Path,
        dashboard_url: str = "",
    ):
        self.config = config
        self.store = store
        self.frames = frames
        self.motion = motion
        self.llm = llm
        self.real_feeder = feeder
        self.dry_feeder = dry_feeder
        self.notifier = notifier
        self.clock = clock
        self.frames_dir = frames_dir
        self.dashboard_url = dashboard_url
        self.verdicts: deque[Verdict] = deque(maxlen=VERDICT_HISTORY)
        self.last_verdict: Verdict | FeedingVerdict | None = None
        self.last_llm_at: datetime | None = None
        self.last_feeding_verdict: FeedingVerdict | None = None
        self.last_heartbeat_at: datetime | None = None
        self.camera_offline = False
        self.done_motion_seen = False
        self.started_at = clock()
        self.settings: Settings = config.load()

    # persisted state ---------------------------------------------------
    @property
    def state(self) -> State:
        return State(self.store.get("state", State.IDLE))

    def _when(self, key: str) -> datetime | None:
        raw = self.store.get(key)
        return datetime.fromisoformat(raw) if raw else None

    def _transition(self, new_state: State, reason: str, **data: Any) -> None:
        old = self.state
        if old == new_state:
            return
        log.info("%s -> %s: %s %s", old, new_state, reason, data or "")
        self.store.set("state", new_state)
        self.store.add_event(
            self.clock(), "transition", reason, {"from": old, "to": new_state, **data}
        )

    def _feeder(self) -> Feeder:
        return self.dry_feeder if self.settings.DRY_RUN else self.real_feeder

    # startup -----------------------------------------------------------
    def start(self) -> None:
        self.settings = self.config.load()
        now = self.clock()
        state = self.state
        if state in (State.OPENING, State.FEEDING):
            # The feed command may already have gone out. Assume the lid is open; never re-send.
            self.store.set("state", State.FEEDING)
            self.store.set("grrr_last_seen_at", now.isoformat())
            resumed = "resumed FEEDING after restart; lid assumed open"
        elif state == State.CLOSING:
            resumed = "resumed CLOSING after restart"
        else:
            resumed = f"resumed {state}"
        event_id = self.store.add_event(now, "startup", resumed, self._status_data())
        self._notify(f"animaleyes started: {resumed}", event_id)

    # main loop ---------------------------------------------------------
    def tick(self) -> None:
        self.settings = self.config.load()
        self.motion.threshold = self.settings.MOTION_PIXEL_FRACTION
        now = self.clock()
        self._check_camera(now)
        self._heartbeat(now)
        self._consume_dashboard_requests(now)

        state = self.state
        lid_open = state in (State.FEEDING, State.CLOSING)
        if not self.settings.ENABLED and not lid_open:
            self._transition(State.OUTSIDE_WINDOW, "ENABLED is false")
            return
        if not self.settings.is_active_at(now.time()) and not lid_open:
            self._transition(State.OUTSIDE_WINDOW, "outside active window")
            return

        handler = {
            State.OUTSIDE_WINDOW: self._tick_outside_window,
            State.IDLE: self._tick_idle,
            State.WATCHING: self._tick_watching,
            State.OPENING: self._tick_opening,
            State.FEEDING: self._tick_feeding,
            State.CLOSING: self._tick_closing,
            State.COOLDOWN: self._tick_cooldown,
            State.DONE: self._tick_done,
        }[state]
        handler(now)

    def _tick_outside_window(self, now: datetime) -> None:
        self.store.set("feeds_this_window", 0)
        self.verdicts.clear()
        self._transition(State.IDLE, "active window started")

    def _tick_idle(self, now: datetime) -> None:
        if not self.store.loaded_plates():
            self._transition(State.DONE, "no loaded plates")
            return
        if self._motion(now):
            self.verdicts.clear()
            self._transition(State.WATCHING, "motion detected", fraction=self.motion.last_fraction)

    def _tick_watching(self, now: datetime) -> None:
        if not self._motion(now):
            self._transition(State.IDLE, f"no motion for {self.settings.MOTION_HOLD_S}s")
            return
        if not self._llm_due(now, self.settings.LLM_MIN_INTERVAL_S):
            return
        verdict = self.llm.identify(self.frames.latest(3))
        self.last_llm_at = now
        self.last_verdict = verdict
        self.verdicts.append(verdict)
        self._check_veto(now)
        if self._grrr_confirmed():
            blocker = self._open_blocker(now)
            if blocker:
                self._log_once(now, "grrr_blocked", blocker, VETO_REPEAT_S)
                return
            self._transition(State.OPENING, "grrr confirmed", verdict=verdict.to_dict())
            self._tick_opening(now, trigger="grrr confirmed")

    def _tick_opening(self, now: datetime, trigger: str = "resumed") -> None:
        feeder = self._feeder()
        mode = "DRY_RUN" if self.settings.DRY_RUN else "LIVE"
        frames = self._save_frames("open")
        try:
            plate = self._plate_under_lid(feeder)
            self.store.set("opening_plate", plate)
            feeder.open_now(plate)
        except FeederError as exc:
            event_id = self.store.add_event(
                now, "feed_failed", str(exc), {"trigger": trigger, "mode": mode}, frames
            )
            self._notify(f"FEED FAILED ({mode}): {exc}", event_id)
            self._transition(State.IDLE, "feed failed", error=str(exc))
            return
        loaded = self.store.loaded_plates()
        self.store.set("feeding_plate", plate)
        self.store.set("opened_at", now.isoformat())
        self.store.set("last_open_at", now.isoformat())
        self.store.set("grrr_last_seen_at", now.isoformat())
        self.store.set("feeds_this_window", self.store.get_int("feeds_this_window") + 1)
        self.last_feeding_verdict = None
        self.last_llm_at = now
        event_id = self.store.add_event(
            now,
            "open",
            trigger,
            {
                "plate": plate,
                "mode": mode,
                "loaded_plates": loaded,
                "feeds_this_window": self.store.get_int("feeds_this_window"),
            },
            frames,
        )
        self._notify(f"OPENED plate {plate} ({mode}): {trigger}. Loaded plates: {loaded}", event_id)
        self._transition(State.FEEDING, "lid open", plate=plate)

    def _tick_feeding(self, now: datetime) -> None:
        opened_at = self._when("opened_at") or now
        last_seen = self._when("grrr_last_seen_at") or opened_at
        if now - opened_at >= timedelta(minutes=self.settings.FEEDING_MAX_MIN):
            self._transition(
                State.CLOSING, f"FEEDING_MAX_MIN ({self.settings.FEEDING_MAX_MIN}) reached"
            )
            self._tick_closing(now)
            return
        if now - last_seen >= timedelta(seconds=self.settings.LEAVE_TIMEOUT_S):
            self._transition(State.CLOSING, f"grrr absent for {self.settings.LEAVE_TIMEOUT_S}s")
            self._tick_closing(now)
            return
        if not self._llm_due(now, self.settings.FEEDING_POLL_S):
            return
        verdict = self.llm.feeding_check(self.frames.latest(3))
        self.last_llm_at = now
        self.last_verdict = verdict
        self.last_feeding_verdict = verdict
        if verdict.grrr_at_bowl:
            self.store.set("grrr_last_seen_at", now.isoformat())
        for animal in verdict.other_animals_present:
            if animal in ("bowie", "cat"):
                self._log_once(
                    now,
                    f"{animal}_during_feed",
                    verdict.reason,
                    VETO_REPEAT_S,
                    frames=self._save_frames(f"{animal}_during_feed"),
                    notify=True,
                )

    def _tick_closing(self, now: datetime) -> None:
        feeder = self._feeder()
        plate = self.store.get_int("feeding_plate", 0)
        opened_at = self._when("opened_at") or now
        bowl = self.last_feeding_verdict.bowl if self.last_feeding_verdict else "unsure"
        # "empty" or "unsure" advances to the next plate next time; only a clearly still-full
        # bowl is offered again. Wrongly advancing costs one bowl; wrongly staying starves.
        plate_status = "loaded" if bowl == "food" else "eaten"
        error = None
        try:
            feeder.close()
        except FeederError as exc:
            error = str(exc)
        if plate:
            self.store.set_plate(plate, plate_status, now)
        session_s = int((now - opened_at).total_seconds())
        data = {
            "plate": plate,
            "bowl": bowl,
            "plate_status": plate_status,
            "session_s": session_s,
            "loaded_plates": self.store.loaded_plates(),
            "mode": "DRY_RUN" if self.settings.DRY_RUN else "LIVE",
        }
        frames = self._save_frames("close")
        if error:
            event_id = self.store.add_event(now, "close_failed", error, data, frames)
            self._notify(f"CLOSE FAILED after {session_s}s: {error}", event_id)
        else:
            event_id = self.store.add_event(now, "close", f"bowl {bowl}", data, frames)
            self._notify(
                f"CLOSED plate {plate} after {session_s}s, bowl {bowl} -> {plate_status}. "
                f"Loaded plates left: {self.store.loaded_plates()}",
                event_id,
            )
        self.verdicts.clear()
        self._transition(State.COOLDOWN, "lid closed", **data)

    def _tick_cooldown(self, now: datetime) -> None:
        last_open = self._when("last_open_at")
        if last_open and now - last_open < timedelta(minutes=self.settings.MIN_GAP_MIN):
            return
        if self.store.loaded_plates():
            self._transition(State.IDLE, f"MIN_GAP_MIN ({self.settings.MIN_GAP_MIN}) elapsed")
        else:
            self._transition(State.DONE, "no loaded plates left")

    def _tick_done(self, now: datetime) -> None:
        if self.store.loaded_plates():
            self._transition(State.IDLE, "plates loaded")
            return
        motion = self._motion(now)
        if not motion:
            self.done_motion_seen = False
            return
        if self.done_motion_seen:
            return
        self.done_motion_seen = True
        verdict = self.llm.identify(self.frames.latest(3))
        self.last_llm_at = now
        self.last_verdict = verdict
        if verdict.animal == "grrr":
            self._log_once(
                now,
                "wanted_food_none_left",
                verdict.reason,
                NONE_LEFT_REPEAT_S,
                frames=self._save_frames("none_left"),
                notify=True,
            )

    # helpers -----------------------------------------------------------
    def _motion(self, now: datetime) -> bool:
        return self.motion.motion_within(now, self.settings.MOTION_HOLD_S)

    def _llm_due(self, now: datetime, interval_s: float) -> bool:
        return self.last_llm_at is None or now - self.last_llm_at >= timedelta(seconds=interval_s)

    def _grrr_confirmed(self) -> bool:
        needed = self.settings.CONFIRMATIONS_REQUIRED
        recent = list(self.verdicts)[-needed:]
        return len(recent) >= needed and all(v.is_grrr(self.settings.GRRR_MIN_CONF) for v in recent)

    def _open_blocker(self, now: datetime) -> str | None:
        if not self.store.loaded_plates():
            return "no loaded plates"
        last_open = self._when("last_open_at")
        if last_open and now - last_open < timedelta(minutes=self.settings.MIN_GAP_MIN):
            return f"last open {int((now - last_open).total_seconds() // 60)} min ago < MIN_GAP_MIN"
        return None

    def _check_veto(self, now: datetime) -> None:
        recent = list(self.verdicts)[-2:]
        if len(recent) < 2:
            return
        animals = {v.animal for v in recent}
        if len(animals) == 1 and animals <= {"bowie", "cat"}:
            animal = recent[-1].animal
            self._log_once(
                now,
                "veto",
                f"{animal}: {recent[-1].reason}",
                VETO_REPEAT_S,
                data={"animal": animal, "verdict": recent[-1].to_dict()},
                frames=self._save_frames(f"veto_{animal}"),
                notify=True,
            )

    def _plate_under_lid(self, feeder: Feeder) -> int:
        """Rotate until a loaded plate is under the lid. Manual feeds accept any plate."""
        loaded = self.store.loaded_plates()
        plate = feeder.current_plate()
        if not loaded:
            return plate
        for _ in range(3):
            if plate in loaded:
                return plate
            feeder.rotate()
            plate = feeder.current_plate()
        raise FeederError(f"no loaded plate reachable; tray reports plate {plate}, loaded {loaded}")

    def _log_once(
        self,
        now: datetime,
        kind: str,
        reason: str,
        repeat_s: int,
        *,
        data: dict[str, Any] | None = None,
        frames: list[str] | None = None,
        notify: bool = False,
    ) -> None:
        last = self.store.last_event_at(kind)
        if last and now - last < timedelta(seconds=repeat_s):
            return
        event_id = self.store.add_event(now, kind, reason, data, frames)
        if notify:
            self._notify(f"{kind}: {reason}", event_id)

    def _save_frames(self, tag: str) -> list[str]:
        frames = self.frames.latest(3)
        names: list[str] = []
        self.frames_dir.mkdir(parents=True, exist_ok=True)
        for i, frame in enumerate(frames):
            name = f"{frame.at.strftime('%Y%m%d-%H%M%S')}_{tag}_{i}.jpg"
            (self.frames_dir / name).write_bytes(frame.jpeg)
            names.append(name)
        return names

    def _notify(self, text: str, event_id: int | None = None) -> None:
        # No credentials in the link; the browser asks for the dashboard's basic auth.
        if event_id and self.dashboard_url:
            text = f"{text}\n{self.dashboard_url}/events/{event_id}"
        self.notifier.send(text)

    def _check_camera(self, now: datetime) -> None:
        last = self.frames.last_frame_at() or self.started_at
        offline = now - last > timedelta(seconds=CAMERA_OFFLINE_S)
        if offline and not self.camera_offline:
            self.camera_offline = True
            event_id = self.store.add_event(
                now, "camera_offline", f"no frame for {CAMERA_OFFLINE_S}s"
            )
            self._notify("CAMERA OFFLINE: no frames", event_id)
        elif not offline and self.camera_offline:
            self.camera_offline = False
            event_id = self.store.add_event(now, "camera_online", "frames resumed")
            self._notify("camera back online", event_id)

    def _heartbeat(self, now: datetime) -> None:
        interval = timedelta(minutes=self.settings.HEARTBEAT_MIN)
        if self.last_heartbeat_at and now - self.last_heartbeat_at < interval:
            return
        self.last_heartbeat_at = now
        self.store.set("heartbeat_at", now.isoformat())
        status = self._status_data()
        self.notifier.send(
            f"♥ {status['state']} plates={status['loaded_plates']} "
            f"feeds={status['feeds_this_window']} "
            f"camera={'OFFLINE' if self.camera_offline else 'ok'}"
        )

    def _consume_dashboard_requests(self, now: datetime) -> None:
        if self.store.get("plates_updated"):
            self.store.set("plates_updated", None)
            self.store.set("feeds_this_window", 0)
            if self.state in (State.DONE, State.COOLDOWN, State.WATCHING, State.IDLE):
                self.store.add_event(
                    now,
                    "plates_set",
                    "plates set from dashboard",
                    {"loaded_plates": self.store.loaded_plates()},
                )
                self._transition(State.IDLE, "plates set from dashboard")
        if self.store.get("feed_now_requested"):
            self.store.set("feed_now_requested", None)
            if self.state in (State.FEEDING, State.CLOSING, State.OPENING):
                self.store.add_event(now, "feed_now_ignored", "lid already open")
                return
            self._transition(State.OPENING, "manual feed from dashboard")
            self._tick_opening(now, trigger="manual feed from dashboard")

    def request_feed_now(self) -> None:
        self.store.set("feed_now_requested", 1)

    def _status_data(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "loaded_plates": self.store.loaded_plates(),
            "plates": self.store.plates(),
            "feeds_this_window": self.store.get_int("feeds_this_window"),
            "last_open_at": self.store.get("last_open_at"),
            "opened_at": self.store.get("opened_at") if self.state == State.FEEDING else None,
            "feeding_plate": self.store.get("feeding_plate")
            if self.state == State.FEEDING
            else None,
        }

    def next_allowed_feed(self) -> datetime | None:
        last_open = self._when("last_open_at")
        if not last_open:
            return None
        return last_open + timedelta(minutes=self.settings.MIN_GAP_MIN)
