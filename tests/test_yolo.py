"""YOLO mapping logic — tested without loading PyTorch via the pure function and an injected
fake model, so these run in CI with no heavy deps."""

from __future__ import annotations

from datetime import datetime

from animaleyes.camera import Frame
from animaleyes.yolo import Detection, YoloConfig, YoloIdentifier, verdict_from_detections

CFG = YoloConfig(min_conf=0.4, grrr_max_box_fraction=0.18)


def test_small_dog_is_grrr() -> None:
    v = verdict_from_detections([Detection("dog", 0.9, box_fraction=0.08)], CFG)
    assert v.animal == "grrr" and v.at_bowl is True and v.other_animals_present == []


def test_large_dog_is_bowie() -> None:
    v = verdict_from_detections([Detection("dog", 0.9, box_fraction=0.40)], CFG)
    assert v.animal == "bowie"


def test_cat_is_cat() -> None:
    v = verdict_from_detections([Detection("cat", 0.8, box_fraction=0.2)], CFG)
    assert v.animal == "cat"


def test_grrr_with_cat_present_is_vetoed_via_other_animals() -> None:
    v = verdict_from_detections(
        [Detection("dog", 0.9, box_fraction=0.08), Detection("cat", 0.7, box_fraction=0.15)], CFG
    )
    assert v.animal == "grrr" and "cat" in v.other_animals_present
    assert v.is_grrr(0.5) is False  # other animal present blocks a feed


def test_low_confidence_and_person_are_ignored() -> None:
    v = verdict_from_detections(
        [Detection("dog", 0.2, box_fraction=0.08), Detection("person", 0.99, box_fraction=0.5)], CFG
    )
    assert v.animal == "none"


class _FakeResult:
    names = {0: "dog"}

    class _Box:
        cls = [0]
        conf = [0.9]
        xyxy = [[0.0, 0.0, 100.0, 100.0]]  # 100x100 box

    boxes = [_Box()]


class _FakeModel:
    def predict(self, img, verbose=False):
        return [_FakeResult()]


def test_identifier_uses_injected_model_and_frame_size() -> None:
    # 1000x1000 frame, 100x100 box => box_fraction 0.01 => Grrr.
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (1000, 1000)).save(buf, format="JPEG")
    ident = YoloIdentifier("unused.pt", CFG, model=_FakeModel())
    v = ident.identify([Frame(jpeg=buf.getvalue(), at=datetime.now())])
    assert v.animal == "grrr"
