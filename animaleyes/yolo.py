"""Local YOLO detector backend — a free, real-time alternative to the cloud LLM.

A pretrained COCO model gives us dog / cat / person with bounding boxes. We map that onto the
project's identities: a cat is the cat, a person is ignored, and a dog is Grrr or Bowie by
box size (Grrr is tiny, Bowie medium/lanky — at a fixed camera distance the box area separates
them). The mapping is a pure function (`verdict_from_detections`) so it is unit-tested without
loading PyTorch; the model is lazy-imported only when this backend is actually selected.

Enable with IDENTIFIER=yolo (see config). Grrr-vs-Bowie currently uses the size heuristic; a
fine-tuned classifier on reference crops is the planned upgrade and slots in behind this same
interface.
"""

from __future__ import annotations

import io
import logging
import time
from dataclasses import dataclass

from .camera import Frame
from .detect import FeedingVerdict, Verdict

log = logging.getLogger(__name__)

# COCO class names we care about.
DOG, CAT, PERSON = "dog", "cat", "person"


CROP_MARGIN = 0.12  # pad a detection box by this fraction of its size before classifying


def crop_box(img, box: tuple[float, float, float, float]):
    """Crop a detection box out of a PIL image with a margin, clamped to the frame. Shared by
    training and inference so the classifier sees the same framing both times."""
    x1, y1, x2, y2 = box
    mx, my = (x2 - x1) * CROP_MARGIN, (y2 - y1) * CROP_MARGIN
    w, h = img.size
    return img.crop((max(0, int(x1 - mx)), max(0, int(y1 - my)), min(w, int(x2 + mx)), min(h, int(y2 + my))))


@dataclass
class Detection:
    label: str
    confidence: float
    box_fraction: float  # bbox area / image area, 0..1
    in_bowl_zone: bool = True


@dataclass
class YoloConfig:
    min_conf: float = 0.4
    # A dog whose box covers at most this fraction of the frame is Grrr (tiny); larger is Bowie.
    grrr_max_box_fraction: float = 0.18


def verdict_from_detections(dets: list[Detection], cfg: YoloConfig) -> Verdict:
    """Pure mapping from detections to a Verdict. No model, no I/O — fully testable."""
    animals = [d for d in dets if d.confidence >= cfg.min_conf and d.label in (DOG, CAT)]
    if not animals:
        return Verdict(animal="none", confidence=0.0, at_bowl=False, reason="no animal detected")

    cats = [d for d in animals if d.label == CAT]
    dogs = [d for d in animals if d.label == DOG]

    present: list[str] = []
    if cats:
        present.append("cat")
    # A dog is Grrr or Bowie by size.
    dog_identity = None
    best_dog = max(dogs, key=lambda d: d.confidence) if dogs else None
    if best_dog is not None:
        dog_identity = "grrr" if best_dog.box_fraction <= cfg.grrr_max_box_fraction else "bowie"
        present.append(dog_identity)

    # Choose the subject: the dog if present (that's who we might feed), else the cat.
    if best_dog is not None:
        subject, conf, in_zone = dog_identity, best_dog.confidence, best_dog.in_bowl_zone
    else:
        subject, conf, in_zone = "cat", max(c.confidence for c in cats), cats[0].in_bowl_zone

    others = [a for a in present if a != subject]
    reason = (
        f"yolo: {subject} (conf {conf:.2f}, box {best_dog.box_fraction:.2f})"
        if best_dog
        else f"yolo: cat (conf {conf:.2f})"
    )
    return Verdict(
        animal=subject,
        confidence=conf,
        at_bowl=in_zone,
        other_animals_present=others,
        reason=reason,
    )


class YoloIdentifier:
    """Runs a local YOLO model and maps its detections onto the Identifier interface."""

    def __init__(self, model_path: str, cfg: YoloConfig, model=None):
        self.cfg = cfg
        self.model_path = model_path
        self._model = model  # injectable for tests; lazy-loaded otherwise
        self.reference_counts: dict[str, int] = {}  # for dashboard parity with Claude

    def _ensure_model(self):
        if self._model is None:
            from ultralytics import YOLO  # lazy: only needed when this backend is used

            log.info("loading YOLO model %s", self.model_path)
            self._model = YOLO(self.model_path)
        return self._model

    def _detect(self, frame: Frame) -> list[Detection]:
        from PIL import Image

        img = Image.open(io.BytesIO(frame.jpeg)).convert("RGB")
        w, h = img.size
        area = float(w * h) or 1.0
        started = time.perf_counter()
        results = self._ensure_model().predict(img, verbose=False)
        took_ms = (time.perf_counter() - started) * 1000
        dets: list[Detection] = []
        for r in results:
            names = r.names
            for b in r.boxes:
                label = names[int(b.cls[0])]
                if label not in (DOG, CAT, PERSON):
                    continue
                x1, y1, x2, y2 = (float(v) for v in b.xyxy[0])
                frac = max(0.0, (x2 - x1) * (y2 - y1)) / area
                dets.append(Detection(label=label, confidence=float(b.conf[0]), box_fraction=frac))
        # One line per look, so a missed visit can be explained afterwards: what the gate saw
        # (including detections under YOLO_MIN_CONF, which it then ignores) and how long it took.
        seen = ", ".join(f"{d.label} {d.confidence:.2f} box {d.box_fraction:.2f}" for d in dets)
        log.info(
            "yolo saw: %s (%.0f ms, min conf %.2f)", seen or "nothing", took_ms, self.cfg.min_conf
        )
        return dets

    def identify(self, frames: list[Frame]) -> Verdict:
        if not frames:
            return Verdict(animal="none", reason="no frame")
        return verdict_from_detections(self._detect(frames[-1]), self.cfg)

    def feeding_check(self, frames: list[Frame]) -> FeedingVerdict:
        # YOLO can't judge whether the bowl still has food; report presence only and let the
        # motion/absence timing decide. "bowl: unsure" keeps the eaten/offer-again logic safe.
        if not frames:
            return FeedingVerdict(grrr_at_bowl=False, reason="no frame")
        v = verdict_from_detections(self._detect(frames[-1]), self.cfg)
        return FeedingVerdict(
            grrr_at_bowl=(v.animal == "grrr"),
            bowl="unsure",
            other_animals_present=[a for a in v.other_animals_present if a != "grrr"],
            reason=v.reason,
        )

    def verify_food(self, frames: list[Frame]) -> FeedingVerdict:
        # YOLO/COCO has no food class, so it can't tell a served bowl from an empty plate.
        # "unsure" means the state machine won't rotate the tray on YOLO's say-so.
        return FeedingVerdict(bowl="unsure", reason="yolo cannot judge food in the bowl")

    def reload_references(self) -> None:
        pass  # no reference imagery for the detector backend
