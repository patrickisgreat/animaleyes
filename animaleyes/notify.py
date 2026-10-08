"""Notifiers. Never raise: a broken channel must not stop feeding.

Two severities:
- send()  — routine chatter (heartbeats, opens/closes). Goes to Slack/log only.
- alert()  — something needs attention NOW (camera offline, feed/close failed, no food left).
             Goes everywhere, including email and phone (SMS via a carrier email gateway), so a
             problem reaches the human even when they aren't watching the dashboard.
"""

from __future__ import annotations

import json
import logging
import smtplib
import urllib.parse
import urllib.request
from email.message import EmailMessage
from typing import Protocol

log = logging.getLogger(__name__)


class Notifier(Protocol):
    def send(self, text: str) -> None: ...
    def alert(self, text: str) -> None: ...


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

    def alert(self, text: str) -> None:
        self.send(f"🚨 {text}")


class EmailNotifier:
    """SMTP email + SMS. alert() emails; routine send() is a no-op so phones aren't spammed.

    SMS works by emailing a carrier's email-to-text gateway (e.g. 5551234567@vtext.com for
    Verizon, @txt.att.net for AT&T, @tmomail.net for T-Mobile) — just add that address to the
    recipients, no Twilio account needed.
    """

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        sender: str,
        recipients: list[str],
        timeout_s: float = 20.0,
    ):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.sender = sender or user
        self.recipients = [r.strip() for r in recipients if r.strip()]
        self.timeout_s = timeout_s

    @property
    def enabled(self) -> bool:
        return bool(self.host and self.recipients)

    def send(self, text: str) -> None:
        pass  # routine chatter is not worth an email/text

    def alert(self, text: str) -> None:
        if not self.enabled:
            return
        msg = EmailMessage()
        msg["Subject"] = "animaleyes alert"
        msg["From"] = self.sender
        msg["To"] = ", ".join(self.recipients)
        msg.set_content(text)
        try:
            if self.port == 465:
                smtp: smtplib.SMTP = smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout_s)
            else:
                smtp = smtplib.SMTP(self.host, self.port, timeout=self.timeout_s)
                smtp.starttls()
            with smtp:
                if self.user:
                    smtp.login(self.user, self.password)
                smtp.send_message(msg)
        except Exception as exc:  # noqa: BLE001 - a broken mail server must not stop feeding
            log.warning("email alert failed: %s", exc)


class PushoverNotifier:
    """Pushover push notifications. alert() uses Emergency priority (2): it repeats and overrides
    the phone's silent/Do-Not-Disturb until acknowledged — the channel meant to actually wake you.
    Routine send() is a no-op so only real problems push. Needs an app token + your user key."""

    API = "https://api.pushover.net/1/messages.json"

    def __init__(
        self,
        token: str,
        user: str,
        timeout_s: float = 10.0,
        retry_s: int = 60,
        expire_s: int = 3600,
    ):
        self.token = token
        self.user = user
        self.timeout_s = timeout_s
        self.retry_s = max(30, retry_s)  # Pushover requires retry >= 30s
        self.expire_s = min(10800, expire_s)  # and expire <= 3h

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.user)

    def _payload(self, text: str) -> dict[str, str | int]:
        return {
            "token": self.token,
            "user": self.user,
            "title": "animaleyes",
            "message": text[:1024],
            "priority": 2,  # emergency: repeat until acknowledged
            "retry": self.retry_s,
            "expire": self.expire_s,
        }

    def send(self, text: str) -> None:
        pass  # routine chatter should not push

    def alert(self, text: str) -> None:
        if not self.enabled:
            return
        body = urllib.parse.urlencode(self._payload(text)).encode("utf-8")
        try:
            with urllib.request.urlopen(self.API, data=body, timeout=self.timeout_s) as resp:
                resp.read()
        except Exception as exc:  # noqa: BLE001 - a broken push service must not stop feeding
            log.warning("pushover alert failed: %s", exc)


class MultiNotifier:
    """Fans send()/alert() out to every channel; one failing channel never stops the others."""

    def __init__(self, notifiers: list[Notifier]):
        self.notifiers = notifiers

    def send(self, text: str) -> None:
        for n in self.notifiers:
            try:
                n.send(text)
            except Exception as exc:  # noqa: BLE001
                log.warning("notifier send failed: %s", exc)

    def alert(self, text: str) -> None:
        for n in self.notifiers:
            try:
                n.alert(text)
            except Exception as exc:  # noqa: BLE001
                log.warning("notifier alert failed: %s", exc)


class LogNotifier:
    """Used by tests and tools; records what would have been sent."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.alerts: list[str] = []

    def send(self, text: str) -> None:
        self.sent.append(text)
        log.info("notify: %s", text)

    def alert(self, text: str) -> None:
        self.alerts.append(text)
        self.sent.append(text)
        log.info("ALERT: %s", text)
