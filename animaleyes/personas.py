"""The animal roster ("personas"): who the system knows about and how to describe them.

Each persona has a stable internal `key` (used for reference/training photo folders and in the
detector's vocabulary), an editable display `name`, a `description` fed to Claude, and a
`feedable` flag. Exactly one persona is feedable — the target (Grrr) — and that is a locked
product invariant: the dashboard can rename, re-describe, add, and remove animals, but it can
never make a second animal feedable. Everything that isn't the target is simply never fed.

Stored as data/personas.json so edits survive restarts. The feed decision in the state machine
keys off the stable target key, so renaming the cat to "Chicken" or adding another pet changes
only identification and the dashboard, never who gets fed.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger(__name__)

TARGET_KEY = "grrr"  # the one animal that may be fed; locked (see product invariants)

# Seeded on first run. Keys are stable; names/descriptions are editable afterwards.
DEFAULT_PERSONAS: list[dict] = [
    {
        "key": "grrr",
        "name": "Grrr",
        "description": (
            "Grrr: a tiny, old, black schnoodle. Small body, short legs, scruffy/curly coat. "
            "The ONLY animal that may be fed."
        ),
        "feedable": True,
    },
    {
        "key": "bowie",
        "name": "Bowie",
        "description": (
            "Bowie: a medium-sized, tan/blonde, lanky dog with a smooth short coat. Much larger "
            "and longer-legged than Grrr. Never fed."
        ),
        "feedable": False,
    },
    {
        "key": "cat",
        "name": "Chicken",
        "description": (
            "Chicken: the cat. Sleek short coat, upright triangular ears, short muzzle, long "
            "tail. Never fed."
        ),
        "feedable": False,
    },
]


@dataclass
class Persona:
    key: str
    name: str
    description: str = ""
    feedable: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "animal"


class PersonaStore:
    """Loads/saves the persona roster. Target stays feedable; nothing else can become feedable."""

    def __init__(self, path: Path, seed: dict[str, str] | None = None):
        self.path = path
        self._seed_descriptions = seed or {}
        self._lock = threading.Lock()

    def _seed(self) -> list[Persona]:
        personas = []
        for d in DEFAULT_PERSONAS:
            desc = self._seed_descriptions.get(d["key"]) or d["description"]
            personas.append(Persona(d["key"], d["name"], desc, d["feedable"]))
        return personas

    def load(self) -> list[Persona]:
        with self._lock:
            if not self.path.exists():
                personas = self._seed()
                self._write(personas)
                return personas
            try:
                raw = json.loads(self.path.read_text())
                personas = [
                    Persona(
                        key=str(p["key"]),
                        name=str(p.get("name", p["key"])),
                        description=str(p.get("description", "")),
                        feedable=bool(p.get("feedable", False)),
                    )
                    for p in raw
                ]
            except (OSError, ValueError, KeyError) as exc:
                log.error("personas.json invalid (%s); reseeding defaults", exc)
                personas = self._seed()
                self._write(personas)
            return self._normalise(personas)

    def _normalise(self, personas: list[Persona]) -> list[Persona]:
        """Enforce the invariant: exactly the target key is feedable, everyone else never is."""
        for p in personas:
            p.feedable = p.key == TARGET_KEY
        return personas

    def _write(self, personas: list[Persona]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps([p.to_dict() for p in personas], indent=2))

    def save(self, personas: list[Persona]) -> list[Persona]:
        with self._lock:
            personas = self._normalise(personas)
            self._write(personas)
            return personas

    # convenience views ------------------------------------------------------
    def keys(self) -> list[str]:
        return [p.key for p in self.load()]

    def names(self) -> dict[str, str]:
        return {p.key: p.name for p in self.load()}

    def descriptions(self) -> dict[str, str]:
        return {p.key: p.description for p in self.load()}

    def upsert(self, name: str, description: str, key: str | None = None) -> Persona:
        """Add a new (never-fed) persona, or edit an existing one's name/description."""
        personas = self.load()
        if key:  # edit
            for p in personas:
                if p.key == key:
                    p.name = name.strip() or p.name
                    p.description = description
                    self.save(personas)
                    return p
            raise KeyError(key)
        # add: generate a unique key from the name
        existing = {p.key for p in personas}
        base = slugify(name)
        new_key = base
        n = 2
        while new_key in existing:
            new_key = f"{base}-{n}"
            n += 1
        persona = Persona(new_key, name.strip() or new_key, description, feedable=False)
        personas.append(persona)
        self.save(personas)
        return persona

    def delete(self, key: str) -> None:
        if key == TARGET_KEY:
            raise ValueError("cannot delete the feedable animal")
        personas = [p for p in self.load() if p.key != key]
        self.save(personas)
