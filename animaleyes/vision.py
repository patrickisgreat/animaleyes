"""Animal identification with Claude.

Every request has the same prefix: a frozen system prompt and the reference photos, marked
for prompt caching so they are billed once per cache lifetime. The most recent camera frames
and the question come after the cache breakpoint. Output is constrained to a JSON schema so
the state machine never has to parse prose.
"""

from __future__ import annotations

import base64
import io
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

import anthropic
from PIL import Image

from .camera import Frame
from .store import Store

log = logging.getLogger(__name__)

ANIMALS = ("grrr", "bowie", "cat")
ANIMAL_DESCRIPTIONS = {
    "grrr": "Grrr: a tiny, old, black schnoodle. Small body, short legs, scruffy coat. "
    "The ONLY animal that may be fed.",
    "bowie": "Bowie: a medium-sized, blonde, lanky dog. Much larger and longer-legged than Grrr.",
    "cat": "The cat. Never fed.",
}
REFERENCE_MAX_EDGE = 640
VERDICT_MAX_TOKENS = 300

SYSTEM_PROMPT = """You identify which household animal, if any, is at a pet food bowl, from
security-camera frames. You are given labelled reference photos of each animal, then the
most recent frames from the camera (oldest first).

Rules:
- "unsure" is a valid and preferred answer when frames are dark, blurry, partial, or the
  animal is too far away to judge. Never guess between animals; choose "unsure".
- At night the camera uses infrared and the image is greyscale: a black coat is NOT a cue.
  Use body size, leg length, body proportions, ear and muzzle shape, and posture.
- Bowie is much larger and lankier than Grrr. Grrr is small and low to the ground.
- POC fallback when few or no reference photos are provided: identify by species and size
  alone. The ONLY small dog in this home is Grrr, so classify any clearly small dog as
  "grrr", a clearly larger or lankier dog as "bowie", and a cat as "cat". A cat is not a
  small dog: tell them apart by the cat's shorter muzzle, triangular upright ears, long
  tail, and lighter, more fluid gait. If you cannot tell a small dog from a cat, choose
  "unsure" — never feed on a guess.
- "at_bowl" is true only when the animal's head is at or in the bowl area, not merely nearby.
- Answer with a single JSON object matching the schema you were given, nothing else."""

IDENTIFY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "animal": {"type": "string", "enum": ["grrr", "bowie", "cat", "none", "unsure"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "at_bowl": {"type": "boolean"},
        "other_animals_present": {
            "type": "array",
            "items": {"type": "string", "enum": ["grrr", "bowie", "cat"]},
        },
        "reason": {"type": "string"},
    },
    "required": ["animal", "confidence", "at_bowl", "other_animals_present", "reason"],
    "additionalProperties": False,
}

FEEDING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "grrr_at_bowl": {"type": "boolean"},
        "bowl": {"type": "string", "enum": ["food", "empty", "unsure"]},
        "other_animals_present": {
            "type": "array",
            "items": {"type": "string", "enum": ["bowie", "cat"]},
        },
        "reason": {"type": "string"},
    },
    "required": ["grrr_at_bowl", "bowl", "other_animals_present", "reason"],
    "additionalProperties": False,
}

IDENTIFY_QUESTION = (
    "Which animal, if any, is in the most recent frames, and is it at the bowl? "
    "List any other animals visible in other_animals_present."
)
FEEDING_QUESTION = (
    "The feeder lid is open. Is the small black dog (Grrr) still at the bowl? "
    "Is there still food visible in the open bowl, or has it been eaten? "
    "List Bowie or the cat in other_animals_present if they are visible."
)

# USD per million tokens: (input, output). Cache writes with a 1h TTL cost 2x input,
# cache reads cost 0.1x input. Used for the dashboard estimate only.
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
}
CACHE_TTL = "1h"


@dataclass
class Verdict:
    animal: str = "unsure"
    confidence: float = 0.0
    at_bowl: bool = False
    other_animals_present: list[str] = field(default_factory=list)
    reason: str = ""

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Verdict:
        return cls(
            animal=str(data.get("animal", "unsure")),
            confidence=float(data.get("confidence", 0.0)),
            at_bowl=bool(data.get("at_bowl", False)),
            other_animals_present=[str(a) for a in data.get("other_animals_present", [])],
            reason=str(data.get("reason", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "animal": self.animal,
            "confidence": self.confidence,
            "at_bowl": self.at_bowl,
            "other_animals_present": self.other_animals_present,
            "reason": self.reason,
        }

    def is_grrr(self, min_confidence: float) -> bool:
        return (
            self.animal == "grrr"
            and self.confidence >= min_confidence
            and self.at_bowl
            and not self.other_animals_present
        )


@dataclass
class FeedingVerdict:
    grrr_at_bowl: bool = False
    bowl: str = "unsure"
    other_animals_present: list[str] = field(default_factory=list)
    reason: str = ""

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> FeedingVerdict:
        return cls(
            grrr_at_bowl=bool(data.get("grrr_at_bowl", False)),
            bowl=str(data.get("bowl", "unsure")),
            other_animals_present=[str(a) for a in data.get("other_animals_present", [])],
            reason=str(data.get("reason", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "grrr_at_bowl": self.grrr_at_bowl,
            "bowl": self.bowl,
            "other_animals_present": self.other_animals_present,
            "reason": self.reason,
        }


class Identifier(Protocol):
    def identify(self, frames: list[Frame]) -> Verdict: ...
    def feeding_check(self, frames: list[Frame]) -> FeedingVerdict: ...


def estimate_cost_usd(model: str, usage: dict[str, int]) -> float:
    price_in, price_out = PRICES.get(model, PRICES["claude-opus-5"])
    return (
        usage.get("input_tokens", 0) * price_in
        + usage.get("cache_creation_input_tokens", 0) * price_in * 2.0
        + usage.get("cache_read_input_tokens", 0) * price_in * 0.1
        + usage.get("output_tokens", 0) * price_out
    ) / 1_000_000


def to_jpeg(data: bytes, max_edge: int = REFERENCE_MAX_EDGE) -> bytes:
    image = Image.open(io.BytesIO(data)).convert("RGB")
    image.thumbnail((max_edge, max_edge))
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=80)
    return out.getvalue()


def image_block(jpeg: bytes) -> dict[str, Any]:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": base64.standard_b64encode(jpeg).decode("ascii"),
        },
    }


def load_reference_blocks(reference_dir: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Content blocks for every reference photo, grouped and labelled per animal."""
    blocks: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for animal in ANIMALS:
        paths = sorted(
            p
            for p in (reference_dir / animal).glob("*")
            if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
        )
        counts[animal] = len(paths)
        blocks.append(
            {"type": "text", "text": f"Reference photos of {ANIMAL_DESCRIPTIONS[animal]}"}
        )
        for path in paths:
            blocks.append(image_block(to_jpeg(path.read_bytes())))
        if not paths:
            blocks.append({"type": "text", "text": "(no reference photos yet)"})
    blocks.append(
        {
            "type": "text",
            "text": "End of reference photos.",
            "cache_control": {"type": "ephemeral", "ttl": CACHE_TTL},
        }
    )
    return blocks, counts


class ClaudeIdentifier:
    def __init__(
        self,
        reference_dir: Path,
        store: Store,
        model: Callable[[], str],
        clock: Callable[[], datetime] = datetime.now,
        client: anthropic.Anthropic | None = None,
    ):
        self.reference_dir = reference_dir
        self.store = store
        self.model = model
        self.clock = clock
        self.client = client or anthropic.Anthropic()
        self.reference_blocks: list[dict[str, Any]] = []
        self.reference_counts: dict[str, int] = {}
        self.reload_references()

    def reload_references(self) -> None:
        self.reference_blocks, self.reference_counts = load_reference_blocks(self.reference_dir)
        log.info("reference photos: %s", self.reference_counts)

    def identify(self, frames: list[Frame]) -> Verdict:
        data = self._ask("identify", frames, IDENTIFY_QUESTION, IDENTIFY_SCHEMA)
        return Verdict.from_json(data) if data else Verdict(reason="llm call failed")

    def feeding_check(self, frames: list[Frame]) -> FeedingVerdict:
        data = self._ask("feeding", frames, FEEDING_QUESTION, FEEDING_SCHEMA)
        return FeedingVerdict.from_json(data) if data else FeedingVerdict(reason="llm call failed")

    def _ask(
        self, purpose: str, frames: list[Frame], question: str, schema: dict[str, Any]
    ) -> dict[str, Any] | None:
        if not frames:
            return None
        model = self.model()
        content: list[dict[str, Any]] = list(self.reference_blocks)
        content.append({"type": "text", "text": "Current camera frames, oldest first:"})
        content.extend(image_block(frame.jpeg) for frame in frames)
        content.append({"type": "text", "text": question})
        try:
            response = self.client.messages.create(
                model=model,
                max_tokens=VERDICT_MAX_TOKENS,
                system=[
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral", "ttl": CACHE_TTL},
                    }
                ],
                messages=[{"role": "user", "content": content}],
                output_config={
                    "effort": "low",
                    "format": {"type": "json_schema", "schema": schema},
                },
            )
        except anthropic.APIError as exc:
            log.warning("llm %s call failed: %s", purpose, exc)
            self.store.add_llm_call(self.clock(), purpose, model, {"error": str(exc)}, {}, 0.0)
            return None
        usage = response.usage.model_dump(exclude_none=True)
        usage = {k: v for k, v in usage.items() if isinstance(v, int)}
        cost = estimate_cost_usd(model, usage)
        if response.stop_reason == "refusal":
            verdict: dict[str, Any] = {"error": "refusal"}
        else:
            text = next((b.text for b in response.content if b.type == "text"), "{}")
            verdict = _parse_json(text)
        log.info("llm %s -> %s (cost $%.4f, usage %s)", purpose, verdict, cost, usage)
        self.store.add_llm_call(self.clock(), purpose, model, verdict, usage, cost)
        return None if "error" in verdict else verdict


def _parse_json(text: str) -> dict[str, Any]:
    import json

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"error": f"non-json response: {text[:200]}"}
    return parsed if isinstance(parsed, dict) else {"error": "non-object response"}
