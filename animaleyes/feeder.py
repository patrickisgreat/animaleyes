"""The PetLibro Polar, driven through petlibro-cli as a subprocess.

Only the state machine may call `open_now`. Every method sends at most one action to the
cloud; there is no retry here because the cloud call itself is idempotent-hostile (a second
`feed` would open again).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from typing import Protocol

log = logging.getLogger(__name__)

# Signs that the box needs to (re)authenticate: an expired/invalid token (code 1009) or no
# cached token yet (first run, or the token volume was reset). Both are safe to recover from
# by logging in and retrying, because they fail before the feeder action runs.
TOKEN_ERRORS = ("code 1009", "No cached token", "login` first", "Token expired")


class FeederError(Exception):
    pass


class Feeder(Protocol):
    def current_plate(self) -> int: ...
    def rotate(self) -> None: ...
    def open_now(self, plate: int) -> None: ...
    def close(self) -> None: ...
    def manual_feed_active(self) -> bool: ...


class PetlibroCli:
    def __init__(self, serial: str, executable: str = "petlibro-cli", timeout_s: float = 40.0):
        if not serial:
            raise FeederError("PETLIBRO_SERIAL is not set")
        self.serial = serial
        self.executable = executable
        self.timeout_s = timeout_s

    def _run(self, *args: str, relogin: bool = True) -> str:
        cmd = [self.executable, *args]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self.timeout_s, env=os.environ.copy()
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise FeederError(f"{' '.join(cmd)}: {exc}") from exc
        if proc.returncode == 0:
            return proc.stdout
        stderr = proc.stderr.strip()
        if relogin and any(sign in stderr for sign in TOKEN_ERRORS):
            # Self-heal auth: no token yet, or an expired/invalid one. Safe to log in and
            # retry once because these errors occur before the feeder action runs, so no
            # double-feed. POC: re-login signs the phone app out of this PetLibro account;
            # use a second shared account for the box so this is harmless.
            log.warning("petlibro auth needed; logging in and retrying")
            self._run("login", relogin=False)
            return self._run(*args, relogin=False)
        raise FeederError(f"{' '.join(cmd)} failed: {stderr[-300:]}")

    def current_plate(self) -> int:
        real_info = json.loads(self._run("status", self.serial))
        plate = real_info.get("platePosition")
        if plate not in (1, 2, 3):
            raise FeederError(f"unexpected platePosition {plate!r}")
        return int(plate)

    def rotate(self) -> None:
        self._run("rotate", self.serial, "--no-dry-run")

    def manual_feed_active(self) -> bool:
        """True if the lid is currently open (an active manual feed exists).

        `close --dry-run` fetches the wet feeding plan: it prints the active feedId when one
        exists, or errors "No active manual feed" when the lid is closed. We never actuate.
        """
        try:
            out = self._run("close", self.serial, "--dry-run")
        except FeederError as exc:
            if "No active manual feed" in str(exc):
                return False
            raise
        return "feedId" in out

    def open_now(self, plate: int) -> None:
        try:
            self._run("feed", self.serial, "--plate", str(plate), "--no-dry-run")
        except FeederError as exc:
            # Resilience: a slow open can time out on the HTTP read while the lid still opens.
            # Rather than report failure (and have the machine retry, risking a double feed),
            # confirm with the device: if a manual feed is now active, the open succeeded.
            if "timed out" in str(exc).lower() and self.manual_feed_active():
                log.warning("feed response timed out but a manual feed is active; treating as open")
                return
            raise

    def close(self) -> None:
        try:
            self._run("close", self.serial, "--no-dry-run")
        except FeederError as exc:
            if "No active manual feed" in str(exc):
                log.info("close: lid already closed")
                return
            raise


class DryRunFeeder:
    """Logs what the real feeder would do and simulates the tray so the machine can be exercised."""

    def __init__(self, plate: int = 1):
        self.plate = plate
        self.calls: list[str] = []
        self._open = False

    def current_plate(self) -> int:
        return self.plate

    def rotate(self) -> None:
        self.plate = self.plate % 3 + 1
        self.calls.append("rotate")
        log.info("DRY_RUN rotate -> plate %d", self.plate)

    def open_now(self, plate: int) -> None:
        self.calls.append(f"open:{plate}")
        self._open = True
        log.info("DRY_RUN open plate %d", plate)

    def close(self) -> None:
        self.calls.append("close")
        self._open = False
        log.info("DRY_RUN close")

    def manual_feed_active(self) -> bool:
        return self._open
