"""Custom dashboard colour themes, created and edited from the UI.

The two built-in themes (sage, aurora) live in the frontend's CSS. Custom themes are additive:
each is a named set of colour tokens (hex strings) that the dashboard applies at runtime by
setting the matching CSS variables. Stored as data/themes.json so they survive restarts and are
shared across every device that opens the dashboard. Pure presentation — nothing here touches
the feeder or identification.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# The colour tokens a theme defines, mapped to the CSS variables the stylesheet reads. Keys are
# what the API/UI use; values are the --c-* custom properties set on :root to apply a theme.
TOKENS: dict[str, str] = {
    "bg": "--c-bg",
    "surface": "--c-surface",
    "surface2": "--c-surface2",
    "edge": "--c-edge",
    "ink": "--c-ink",
    "muted": "--c-muted",
    "bad": "--c-bad",
    "teal": "--c-teal",
    "sage": "--c-sage",
    "pearl": "--c-pearl",
    "beige": "--c-beige",
    "ash": "--c-ash",
    "accentInk": "--c-accent-ink",
    "title1": "--c-title1",
    "title2": "--c-title2",
}

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{6})$")


def clean_colors(raw: dict) -> dict[str, str]:
    """Keep only known tokens with valid #rrggbb values."""
    return {k: str(v) for k, v in (raw or {}).items() if k in TOKENS and _HEX.match(str(v))}


@dataclass
class Theme:
    id: str
    name: str
    colors: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class ThemeStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()  # reentrant: upsert/delete call load() while held

    def load(self) -> list[Theme]:
        with self._lock:
            if not self.path.exists():
                return []
            try:
                raw = json.loads(self.path.read_text())
                return [
                    Theme(str(t["id"]), str(t.get("name", "Custom")), clean_colors(t.get("colors")))
                    for t in raw
                ]
            except (OSError, ValueError, KeyError) as exc:
                log.error("themes.json invalid (%s); treating as empty", exc)
                return []

    def _write(self, themes: list[Theme]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps([t.to_dict() for t in themes], indent=2))

    def upsert(self, name: str, colors: dict, theme_id: str | None = None) -> Theme:
        with self._lock:
            themes = self.load()
            colors = clean_colors(colors)
            name = name.strip() or "Custom"
            if theme_id:
                for t in themes:
                    if t.id == theme_id:
                        t.name, t.colors = name, colors
                        self._write(themes)
                        return t
                raise KeyError(theme_id)
            theme = Theme(id=uuid.uuid4().hex[:8], name=name, colors=colors)
            themes.append(theme)
            self._write(themes)
            return theme

    def delete(self, theme_id: str) -> None:
        with self._lock:
            self._write([t for t in self.load() if t.id != theme_id])
