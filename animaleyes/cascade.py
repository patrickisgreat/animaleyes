"""Cascade detector: a free local YOLO gate that only escalates to Claude when needed.

YOLO (local, $0) decides dog / cat / nothing on every check. We only pay for a Claude call
when a *dog* is actually at the bowl — the one case that needs real discrimination (Grrr vs
Bowie, and catching a cat that YOLO misread as a small dog). A cat or an empty scene is
handled locally for free. In practice Claude runs only while a dog is in view (a handful of
checks per visit) instead of on every motion, cutting cost while keeping Claude's accuracy.

Every confident Claude dog verdict also saves the judged frame to data/training/<animal>/,
pre-labelled, so a classifier that eventually replaces Claude trains itself from real IR frames.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from .camera import Frame
from .detect import FeedingVerdict, Identifier, Verdict

log = logging.getLogger(__name__)


class CascadeIdentifier:
    def __init__(
        self,
        gate: Identifier,
        confirm: Identifier,
        config=None,
        training_dir: Path | None = None,
    ):
        self.gate = gate  # local YOLO
        self.confirm = confirm  # Claude
        self.config = config
        self.training_dir = training_dir
        self.reference_counts: dict[str, int] = getattr(confirm, "reference_counts", {})
        self._last_saved = 0.0

    def identify(self, frames: list[Frame]) -> Verdict:
        v = self.gate.identify(frames)
        # YOLO sees a dog (grrr/bowie by its rough size guess) → ask Claude who it really is.
        if v.animal in ("grrr", "bowie"):
            log.debug("cascade: dog seen locally, confirming with Claude")
            v = self.confirm.identify(frames)
            self._maybe_collect(v, frames)
            return v
        # Cat or nothing → decided locally, no paid call.
        return v

    def _maybe_collect(self, verdict: Verdict, frames: list[Frame]) -> None:
        if self.training_dir is None or not frames or self.config is None:
            return
        s = self.config.load()
        if not s.COLLECT_TRAINING or verdict.animal not in ("grrr", "bowie"):
            return
        if verdict.confidence < s.TRAINING_MIN_CONF:
            return
        now = time.monotonic()
        if now - self._last_saved < s.TRAINING_MIN_GAP_S:
            return
        self._last_saved = now
        try:
            frame = frames[-1]
            d = self.training_dir / verdict.animal
            d.mkdir(parents=True, exist_ok=True)
            name = f"{frame.at.strftime('%Y%m%d-%H%M%S')}_{int(verdict.confidence * 100)}.jpg"
            (d / name).write_bytes(frame.jpeg)
        except OSError as exc:
            log.warning("could not save training frame: %s", exc)

    def feeding_check(self, frames: list[Frame]) -> FeedingVerdict:
        # Presence/absence during a feed only needs "is a dog still there", which YOLO does
        # for free; the identity was already settled when the lid opened.
        return self.gate.feeding_check(frames)

    def verify_food(self, frames: list[Frame]) -> FeedingVerdict:
        # Judging whether the served bowl has food needs real vision — route it to Claude
        # (YOLO can't see food). This runs only while a plate is being verified, so it's rare.
        return self.confirm.verify_food(frames)

    def reload_references(self) -> None:
        if hasattr(self.confirm, "reload_references"):
            self.confirm.reload_references()
            self.reference_counts = getattr(self.confirm, "reference_counts", {})
