"""Motion detection by frame differencing on a tiny greyscale copy of each frame."""

from __future__ import annotations

import io
import threading
from datetime import datetime, timedelta

from PIL import Image, ImageChops

from .camera import Frame

THUMB = (64, 36)
PIXEL_DELTA = 25  # 0..255 greyscale change that counts as "this pixel moved"


def thumbnail(jpeg: bytes) -> Image.Image:
    return Image.open(io.BytesIO(jpeg)).convert("L").resize(THUMB)


def changed_fraction(a: Image.Image, b: Image.Image) -> float:
    diff = ImageChops.difference(a, b)
    histogram = diff.histogram()
    changed = sum(histogram[PIXEL_DELTA:])
    return changed / (THUMB[0] * THUMB[1])


class MotionDetector:
    def __init__(self) -> None:
        self._previous: Image.Image | None = None
        self._lock = threading.Lock()
        self.last_fraction = 0.0
        self.last_motion_at: datetime | None = None
        self.threshold = 0.02

    def feed(self, frame: Frame) -> float:
        current = thumbnail(frame.jpeg)
        with self._lock:
            fraction = changed_fraction(self._previous, current) if self._previous else 0.0
            self._previous = current
            self.last_fraction = fraction
            if fraction >= self.threshold:
                self.last_motion_at = frame.at
        return fraction

    def motion_within(self, now: datetime, hold_s: float) -> bool:
        with self._lock:
            last = self.last_motion_at
        return last is not None and now - last <= timedelta(seconds=hold_s)
