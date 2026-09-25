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

TOKEN_EXPIRED = "code 1009"


class FeederError(Exception):
    pass


class Feeder(Protocol):
    def current_plate(self) -> int: ...
    def rotate(self) -> None: ...
    def open_now(self, plate: int) -> None: ...
    def close(self) -> None: ...


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
        if TOKEN_EXPIRED in stderr and relogin:
            # POC: re-login signs the phone app out of this PetLibro account. Use a second
            # shared account for the box so this is harmless.
            log.warning("petlibro token expired; logging in again")
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

    def open_now(self, plate: int) -> None:
        self._run("feed", self.serial, "--plate", str(plate), "--no-dry-run")

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

    def current_plate(self) -> int:
        return self.plate

    def rotate(self) -> None:
        self.plate = self.plate % 3 + 1
        self.calls.append("rotate")
        log.info("DRY_RUN rotate -> plate %d", self.plate)

    def open_now(self, plate: int) -> None:
        self.calls.append(f"open:{plate}")
        log.info("DRY_RUN open plate %d", plate)

    def close(self) -> None:
        self.calls.append("close")
        log.info("DRY_RUN close")
