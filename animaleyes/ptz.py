"""Pan/tilt control for the Reolink camera over ONVIF.

The E1 Pro is a pan/tilt camera; this drives it so the angle can be adjusted from the dashboard
(and presets saved, e.g. a "feeder view"). It only moves the camera — never the feeder — so it
is safe to call directly from the API without going through the state machine.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import time
import urllib.request
from datetime import UTC, datetime

from .events import camera_credentials, camera_host

log = logging.getLogger(__name__)

ONVIF_PORT = 8000
SCHEMA = "http://www.onvif.org/ver10/schema"
PTZ_NS = "http://www.onvif.org/ver20/ptz/wsdl"


class Ptz:
    def __init__(self, stream_url: str, mac: str, profile: str = "000"):
        self.stream_url = stream_url
        self.mac = mac
        self.profile = profile
        self.user, self.password = camera_credentials(stream_url)

    # ONVIF plumbing --------------------------------------------------------
    def _security(self) -> str:
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

    def _soap(self, body: str) -> str:
        host = camera_host(self.stream_url, self.mac)
        url = f"http://{host}:{ONVIF_PORT}/onvif/ptz_service"
        env = (
            '<?xml version="1.0"?>'
            '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">'
            f"<s:Header>{self._security()}</s:Header><s:Body>{body}</s:Body></s:Envelope>"
        )
        req = urllib.request.Request(
            url, data=env.encode(), headers={"Content-Type": "application/soap+xml; charset=utf-8"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.read().decode(errors="replace")

    # controls --------------------------------------------------------------
    def move(self, pan: float, tilt: float) -> None:
        self._soap(
            f'<ContinuousMove xmlns="{PTZ_NS}"><ProfileToken>{self.profile}</ProfileToken>'
            f'<Velocity><PanTilt x="{pan}" y="{tilt}" xmlns="{SCHEMA}"/></Velocity></ContinuousMove>'
        )

    def stop(self) -> None:
        self._soap(
            f'<Stop xmlns="{PTZ_NS}"><ProfileToken>{self.profile}</ProfileToken>'
            "<PanTilt>true</PanTilt></Stop>"
        )

    def nudge(self, pan: float, tilt: float, ms: int) -> None:
        """A short move then stop — one tap = one step, which works well on touch."""
        self.move(pan, tilt)
        time.sleep(max(0.05, min(2.0, ms / 1000)))
        self.stop()

    def presets(self) -> list[dict[str, str]]:
        import re

        resp = self._soap(
            f'<GetPresets xmlns="{PTZ_NS}"><ProfileToken>{self.profile}</ProfileToken></GetPresets>'
        )
        out = []
        for m in re.finditer(r'<[^>]*Preset token="([^"]+)"[^>]*>(.*?)</[^>]*Preset>', resp, re.S):
            name = re.search(r"<[^>]*Name>([^<]*)</[^>]*Name>", m.group(2))
            out.append({"token": m.group(1), "name": (name.group(1) if name else m.group(1))})
        return out

    def set_preset(self, name: str) -> None:
        self._soap(
            f'<SetPreset xmlns="{PTZ_NS}"><ProfileToken>{self.profile}</ProfileToken>'
            f"<PresetName>{name}</PresetName></SetPreset>"
        )

    def goto(self, token: str) -> None:
        self._soap(
            f'<GotoPreset xmlns="{PTZ_NS}"><ProfileToken>{self.profile}</ProfileToken>'
            f"<PresetToken>{token}</PresetToken></GotoPreset>"
        )
