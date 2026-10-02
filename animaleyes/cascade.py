"""Cascade detector: a free local YOLO gate that only escalates to Claude when needed.

YOLO (local, $0) decides dog / cat / nothing on every check. We only pay for a Claude call
when a *dog* is actually at the bowl — the one case that needs real discrimination (Grrr vs
Bowie, and catching a cat that YOLO misread as a small dog). A cat or an empty scene is
handled locally for free. In practice Claude runs only during Grrr's actual feeding visits
(a couple of times a night) instead of on every motion, cutting cost from dollars to pennies
while keeping Claude's accuracy for the decision that matters.
"""

from __future__ import annotations

import logging

from .camera import Frame
from .detect import FeedingVerdict, Identifier, Verdict

log = logging.getLogger(__name__)


class CascadeIdentifier:
    def __init__(self, gate: Identifier, confirm: Identifier):
        self.gate = gate  # local YOLO
        self.confirm = confirm  # Claude
        self.reference_counts: dict[str, int] = getattr(confirm, "reference_counts", {})

    def identify(self, frames: list[Frame]) -> Verdict:
        v = self.gate.identify(frames)
        # YOLO sees a dog (grrr/bowie by its rough size guess) → ask Claude who it really is.
        if v.animal in ("grrr", "bowie"):
            log.debug("cascade: dog seen locally, confirming with Claude")
            return self.confirm.identify(frames)
        # Cat or nothing → decided locally, no paid call.
        return v

    def feeding_check(self, frames: list[Frame]) -> FeedingVerdict:
        # Presence/absence during a feed only needs "is a dog still there", which YOLO does
        # for free; the identity was already settled when the lid opened.
        return self.gate.feeding_check(frames)

    def reload_references(self) -> None:
        if hasattr(self.confirm, "reload_references"):
            self.confirm.reload_references()
            self.reference_counts = getattr(self.confirm, "reference_counts", {})
