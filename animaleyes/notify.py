"""Slack incoming-webhook notifier. Never raises: a broken Slack must not stop feeding."""

from __future__ import annotations

import json
import logging
import urllib.request
from typing import Protocol

log = logging.getLogger(__name__)


class Notifier(Protocol):
    def send(self, text: str) -> None: ...


class SlackNotifier:
    def __init__(self, webhook_url: str, timeout_s: float = 10.0):
        self.webhook_url = webhook_url
        self.timeout_s = timeout_s

    def send(self, text: str) -> None:
        if not self.webhook_url:
            log.info("slack (no webhook configured): %s", text)
            return
        body = json.dumps({"text": text}).encode("utf-8")
        request = urllib.request.Request(
            self.webhook_url, data=body, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                response.read()
        except Exception as exc:  # noqa: BLE001
            log.warning("slack send failed: %s", exc)


class LogNotifier:
    """Used by tests and tools; records what would have been sent."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    def send(self, text: str) -> None:
        self.sent.append(text)
        log.info("notify: %s", text)
