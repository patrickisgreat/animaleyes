"""Camera-driven motion gate via ONVIF events (Reolink E1 Pro).

Polling the camera's own motion / dog-cat detection over ONVIF is near-free (a LAN SOAP
call that the camera holds open until something happens), and it replaces the noisy 64x36
frame-differencing as the trigger for waking the LLM. That keeps us from spending Opus calls
on shadows and IR flicker all night: WATCHING is only entered while the camera reports
motion or an animal.

The class mirrors MotionDetector's read interface (`motion_within`, `last_fraction`,
`threshold`) so the state machine can use either one interchangeably.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import threading
import time
import urllib.request
import uuid
from datetime import UTC, datetime
from urllib.parse import unquote, urlsplit

from .camera import arp_lookup, local_subnet, sweep

log = logging.getLogger(__name__)

ONVIF_PORT = 8000
# Topics whose "true" state means something is at the bowl worth a look. Motion is broad (so
# we never miss tiny Grrr); DogCatDetect is the camera's animal AI, a useful extra signal.
MOTION_TOPIC = "CellMotionDetector/Motion"
ANIMAL_TOPIC = "DogCatDetect"


def camera_host(stream_url: str, mac: str) -> str:
    """Resolve the camera's current IP the same way camera.py does (MAC via ARP)."""
    host = urlsplit(stream_url.replace("{host}", "placeholder")).hostname
    if "{host}" not in stream_url:
        return host or ""
    ip = arp_lookup(mac)
    if ip is None:
        sweep(local_subnet(), ONVIF_PORT)
        ip = arp_lookup(mac)
    if ip is None:
        raise RuntimeError(f"camera {mac} not found on the LAN for ONVIF")
    return ip


def camera_credentials(stream_url: str) -> tuple[str, str]:
    """The user:password embedded in the RTSP URL (password is URL-encoded there)."""
    parts = urlsplit(stream_url)
    return (parts.username or "admin", unquote(parts.password or ""))


class ReolinkEvents(threading.Thread):
    """Maintains an ONVIF PullPoint subscription and tracks the last motion/animal time."""

    def __init__(self, stream_url: str, mac: str, clock=lambda: datetime.now(UTC)):
        super().__init__(name="camera-events", daemon=True)
        self.stream_url = stream_url
        self.mac = mac
        self.clock = clock
        self.user, self.password = camera_credentials(stream_url)
        self._lock = threading.Lock()
        self._last_motion_at: datetime | None = None
        self.motion_state = False
        self.animal_state = False
        self.last_fraction = 0.0  # 1.0 while motion, for dashboard parity with frame-diff
        self.threshold = 0.0  # accepted and ignored; the camera decides what motion is
        self.last_error: str | None = None
        self.stop_event = threading.Event()

    # read interface used by the state machine ---------------------------
    def motion_within(self, now: datetime, hold_s: float) -> bool:
        # `now` is accepted for interface parity with MotionDetector; event times are tracked
        # on the wall clock (UTC), so compare against real elapsed time.
        with self._lock:
            last = self._last_motion_at
        if last is None:
            return False
        return (datetime.now(UTC) - last).total_seconds() <= hold_s

    def _mark_motion(self) -> None:
        with self._lock:
            self._last_motion_at = datetime.now(UTC)
            self.last_fraction = 1.0

    # ONVIF plumbing ----------------------------------------------------
    def _security_header(self) -> str:
        nonce = os.urandom(16)
        created = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        digest = base64.b64encode(
            hashlib.sha1(nonce + created.encode() + self.password.encode()).digest()
        ).decode()
        return (
            '<wsse:Security s:mustUnderstand="1" '
            'xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd" '
            'xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">'
            f"<wsse:UsernameToken><wsse:Username>{self.user}</wsse:Username>"
            '<wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">'
            f"{digest}</wsse:Password>"
            '<wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">'
            f"{base64.b64encode(nonce).decode()}</wsse:Nonce>"
            f"<wsu:Created>{created}</wsu:Created></wsse:UsernameToken></wsse:Security>"
        )

    def _soap(self, url: str, body: str, action: str, to: str) -> str:
        addr = (
            f'<a:Action s:mustUnderstand="1">{action}</a:Action>'
            f"<a:MessageID>urn:uuid:{uuid.uuid4()}</a:MessageID>"
            "<a:ReplyTo><a:Address>http://www.w3.org/2005/08/addressing/anonymous</a:Address></a:ReplyTo>"
            f'<a:To s:mustUnderstand="1">{to}</a:To>'
        )
        env = (
            '<?xml version="1.0"?>'
            '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
            'xmlns:a="http://www.w3.org/2005/08/addressing">'
            f"<s:Header>{self._security_header()}{addr}</s:Header>"
            f"<s:Body>{body}</s:Body></s:Envelope>"
        )
        req = urllib.request.Request(
            url, data=env.encode(), headers={"Content-Type": "application/soap+xml; charset=utf-8"}
        )
        with urllib.request.urlopen(req, timeout=40) as resp:
            return resp.read().decode(errors="replace")

    def _subscribe(self, event_url: str) -> str:
        body = (
            '<CreatePullPointSubscription xmlns="http://www.onvif.org/ver10/events/wsdl">'
            "<InitialTerminationTime>PT300S</InitialTerminationTime>"
            "</CreatePullPointSubscription>"
        )
        resp = self._soap(
            event_url,
            body,
            "http://www.onvif.org/ver10/events/wsdl/EventPortType/CreatePullPointSubscriptionRequest",
            event_url,
        )
        import re

        m = re.search(
            r"<[^>]*Address>(http[^<]*(?:PullSub|Subscription)[^<]*)</[^>]*Address>", resp
        )
        if not m:
            raise RuntimeError(f"no subscription address in response: {resp[:200]}")
        return m.group(1)

    def _pull(self, sub_url: str) -> None:
        body = (
            '<PullMessages xmlns="http://www.onvif.org/ver10/events/wsdl">'
            "<Timeout>PT30S</Timeout><MessageLimit>30</MessageLimit></PullMessages>"
        )
        resp = self._soap(
            sub_url,
            body,
            "http://www.onvif.org/ver10/events/wsdl/PullPointSubscription/PullMessagesRequest",
            sub_url,
        )
        self._apply(resp)

    def _apply(self, resp: str) -> None:
        import re

        # Each NotificationMessage pairs a Topic with SimpleItem state(s). Walk them in order.
        for msg in re.split(r"<wsnt:NotificationMessage", resp)[1:]:
            topic_m = re.search(r"<wsnt:Topic[^>]*>([^<]+)</wsnt:Topic>", msg)
            topic = topic_m.group(1) if topic_m else ""
            state_m = re.search(r'SimpleItem Name="(?:IsMotion|State)" Value="(true|false)"', msg)
            if not state_m:
                continue
            active = state_m.group(1) == "true"
            if MOTION_TOPIC in topic:
                self.motion_state = active
                if active:
                    self._mark_motion()
            elif ANIMAL_TOPIC in topic:
                self.animal_state = active
                if active:
                    self._mark_motion()

    def run(self) -> None:
        backoff = 2.0
        while not self.stop_event.is_set():
            try:
                host = camera_host(self.stream_url, self.mac)
                event_url = f"http://{host}:{ONVIF_PORT}/onvif/event_service"
                sub_url = self._subscribe(event_url)
                log.info("camera events subscribed at %s", sub_url)
                self.last_error = None
                backoff = 2.0
                renew_at = time.monotonic() + 240
                while not self.stop_event.is_set():
                    self._pull(sub_url)
                    if time.monotonic() > renew_at:
                        break  # re-subscribe before the 300s termination time
            except Exception as exc:  # noqa: BLE001 - keep the event loop alive
                self.last_error = str(exc)
                log.warning("camera events error: %s", exc)
                self.stop_event.wait(backoff)
                backoff = min(backoff * 2, 30.0)

    def stop(self) -> None:
        self.stop_event.set()
