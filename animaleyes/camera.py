"""Frame ingestion from a Kasa camera through an ffmpeg subprocess.

ffmpeg decodes the stream and emits JPEGs at ~2 fps on stdout; we split them on JPEG
markers and keep the most recent ones in a ring buffer. No OpenCV, no video decoding in
Python. If ffmpeg dies or the camera goes away we restart it with backoff.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from .config import Secrets

log = logging.getLogger(__name__)

JPEG_START = b"\xff\xd8"
JPEG_END = b"\xff\xd9"
FRAME_WIDTH = 640  # keeps LLM image tokens low; the bowl area stays legible


@dataclass(frozen=True)
class Frame:
    jpeg: bytes
    at: datetime


class FrameBuffer:
    """Thread-safe ring buffer of the most recent frames."""

    def __init__(self, size: int = 12):
        self._frames: deque[Frame] = deque(maxlen=size)
        self._lock = threading.Lock()

    def push(self, frame: Frame) -> None:
        with self._lock:
            self._frames.append(frame)

    def latest(self, n: int = 1) -> list[Frame]:
        with self._lock:
            return list(self._frames)[-n:]

    def last_frame_at(self) -> datetime | None:
        with self._lock:
            return self._frames[-1].at if self._frames else None


def kasa_legacy_password(password: str) -> str:
    """Older Kasa cameras want base64(sha256(account password)) as the basic-auth password."""
    digest = hashlib.sha256(password.encode("utf-8")).digest()
    return base64.b64encode(digest).decode("ascii")


def build_ffmpeg_command(secrets: Secrets, fps: int = 2, width: int = FRAME_WIDTH) -> list[str]:
    """ffmpeg invocation for either an RTSP (newer Kasa) or HTTPS :19443 (older Kasa) stream."""
    url = secrets.kasa_stream_url
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin"]
    if url.startswith("rtsp://"):
        cmd += ["-rtsp_transport", "tcp"]
    elif url.startswith("https://"):
        if secrets.kasa_email and secrets.kasa_password:
            token = base64.b64encode(
                f"{secrets.kasa_email}:{kasa_legacy_password(secrets.kasa_password)}".encode()
            ).decode("ascii")
            cmd += ["-headers", f"Authorization: Basic {token}\r\n"]
    cmd += [
        "-i",
        url,
        "-an",
        "-vf",
        f"fps={fps},scale={width}:-2",
        "-q:v",
        "5",
        "-f",
        "image2pipe",
        "-vcodec",
        "mjpeg",
        "-",
    ]
    return cmd


def split_jpegs(buffer: bytearray) -> list[bytes]:
    """Pull complete JPEGs off the front of `buffer`, leaving any partial tail in place."""
    frames: list[bytes] = []
    while True:
        start = buffer.find(JPEG_START)
        if start < 0:
            buffer.clear()
            return frames
        end = buffer.find(JPEG_END, start + 2)
        if end < 0:
            if start:
                del buffer[:start]
            return frames
        frames.append(bytes(buffer[start : end + 2]))
        del buffer[: end + 2]


class Camera(threading.Thread):
    """Runs ffmpeg forever, pushing frames into `buffer` and calling `on_frame` for each."""

    def __init__(
        self,
        secrets: Secrets,
        buffer: FrameBuffer,
        on_frame: Callable[[Frame], None] | None = None,
        clock: Callable[[], datetime] = datetime.now,
    ):
        super().__init__(name="camera", daemon=True)
        self.secrets = secrets
        self.buffer = buffer
        self.on_frame = on_frame
        self.clock = clock
        self.stop_event = threading.Event()
        self.last_error: str | None = None

    def run(self) -> None:
        backoff = 2.0
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                self._stream_once()
            except Exception as exc:  # noqa: BLE001 - keep the camera loop alive
                self.last_error = str(exc)
                log.warning("camera stream failed: %s", exc)
            if self.stop_event.is_set():
                return
            healthy_for = time.monotonic() - started
            backoff = 2.0 if healthy_for > 60 else min(backoff * 2, 30.0)
            log.info("camera reconnecting in %.0fs", backoff)
            self.stop_event.wait(backoff)

    def _stream_once(self) -> None:
        cmd = build_ffmpeg_command(self.secrets)
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert proc.stdout is not None
        pending = bytearray()
        try:
            while not self.stop_event.is_set():
                chunk = proc.stdout.read(65536)
                if not chunk:
                    break
                pending += chunk
                for jpeg in split_jpegs(pending):
                    frame = Frame(jpeg=jpeg, at=self.clock())
                    self.buffer.push(frame)
                    if self.on_frame:
                        self.on_frame(frame)
        finally:
            proc.kill()
            stderr = proc.stderr.read().decode("utf-8", "replace").strip() if proc.stderr else ""
            proc.wait()
        if stderr:
            raise RuntimeError(f"ffmpeg exited: {stderr[-500:]}")
        raise RuntimeError("ffmpeg stream ended")

    def stop(self) -> None:
        self.stop_event.set()
