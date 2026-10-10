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


def test_classifier_identity_beats_the_size_rule() -> None:
    """Grrr close to the camera has a big box; with the classifier sure it is her, she is Grrr."""
    cfg = YoloConfig(min_conf=0.4, grrr_max_box_fraction=0.18, cls_min_conf=0.6)
    d = Detection("dog", 0.8, box_fraction=0.55, identity="grrr", identity_conf=0.93)
    v = verdict_from_detections([d], cfg)
    assert v.animal == "grrr" and v.confidence == 0.93
    assert "id 0.93" in v.reason


def test_unsure_classifier_never_guesses_a_dog() -> None:
    cfg = YoloConfig(min_conf=0.4, cls_min_conf=0.6)
    d = Detection("dog", 0.8, box_fraction=0.1, identity="grrr", identity_conf=0.41)
    v = verdict_from_detections([d], cfg)
    assert v.animal == "unsure"
    assert v.is_grrr(0.3, require_at_bowl=False) is False  # unsure can never open the feeder


def test_unsure_classifier_on_a_cat_is_still_a_cat() -> None:
    cfg = YoloConfig(min_conf=0.4, cls_min_conf=0.6)
    d = Detection("cat", 0.9, box_fraction=0.1, identity="tallulah", identity_conf=0.3)
    assert verdict_from_detections([d], cfg).animal == "cat"


def test_identifier_classifies_each_detected_animals_crop() -> None:
    """The detector finds the box; the classifier names the animal from its crop."""
    from datetime import datetime

    from animaleyes.camera import Frame
    from animaleyes.yolo import YoloIdentifier

    class _Box:
        def __init__(self, cls, conf, xyxy):
            self.cls, self.conf, self.xyxy = [cls], [conf], [xyxy]

    class _Res:
        names = {0: "dog"}

        def __init__(self):
            self.boxes = [_Box(0, 0.8, [0.0, 0.0, 60.0, 30.0])]

    class _Det:
        def predict(self, img, verbose=False):
            return [_Res()]

    class _Probs:
        top1 = 1
        top1conf = 0.88

    class _ClsRes:
        names = {0: "bowie", 1: "grrr"}
        probs = _Probs()

    class _Cls:
        def __init__(self):
            self.crops = []

        def predict(self, img, verbose=False, imgsz=224):
            self.crops.append(img.size)
            return [_ClsRes()]

    cfg = YoloConfig(min_conf=0.4, grrr_max_box_fraction=0.18, classifier_path="x.pt")
    clf = _Cls()
    ident = YoloIdentifier("m.pt", cfg, model=_Det(), classifier=clf)
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (100, 50), "black").save(buf, format="JPEG")
    v = ident.identify([Frame(jpeg=buf.getvalue(), at=datetime(2026, 10, 9, 1, 0))])
    assert v.animal == "grrr" and v.confidence == 0.88
    assert clf.crops and clf.crops[0][0] <= 100  # it classified a crop, not nothing


def _frame_1000() -> Frame:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (1000, 1000)).save(buf, format="JPEG")
    return Frame(jpeg=buf.getvalue(), at=datetime.now())


def test_settings_are_reread_on_every_look() -> None:
    """Dashboard edits take effect on the next tick, no restart: here the threshold moves above
    the detection and back."""
    live = {"min_conf": 0.4}
    ident = YoloIdentifier(
        "unused.pt",
        CFG,
        model=_FakeModel(),
        settings=lambda: YoloConfig(min_conf=live["min_conf"], grrr_max_box_fraction=0.18),
    )
    assert ident.identify([_frame_1000()]).animal == "grrr"
    live["min_conf"] = 0.95  # the fake dog is 0.9
    assert ident.identify([_frame_1000()]).animal == "none"
    live["min_conf"] = 0.4
    assert ident.identify([_frame_1000()]).animal == "grrr"


def test_classifier_path_set_later_is_loaded_and_a_bad_one_is_tried_once() -> None:
    """Enabling the identity model from Settings after startup loads it; a path that fails to
    load falls back to the size rule and is not retried every tick."""

    class _Probs:
        top1 = 0
        top1conf = 0.99

    class _ClsRes:
        names = {0: "bowie"}
        probs = _Probs()

    class _Cls:
        def predict(self, img, verbose=False, imgsz=224):
            return [_ClsRes()]

    live = {"path": ""}
    loads: list[str] = []
    ident = YoloIdentifier(
        "unused.pt",
        CFG,
        model=_FakeModel(),
        settings=lambda: YoloConfig(
            min_conf=0.4, grrr_max_box_fraction=0.18, classifier_path=live["path"]
        ),
    )

    def fake_load(path: str):
        loads.append(path)
        if path == "broken.pt":
            raise OSError("no such file")
        return _Cls()

    ident._load_classifier = fake_load  # type: ignore[method-assign]
    assert ident.identify([_frame_1000()]).animal == "grrr"  # size rule: tiny box
    live["path"] = "broken.pt"
    assert ident.identify([_frame_1000()]).animal == "grrr"  # fell back to the size rule
    assert ident.identify([_frame_1000()]).animal == "grrr"
    assert loads == ["broken.pt"]  # warned once, not retried
    live["path"] = "good.pt"
    assert ident.identify([_frame_1000()]).animal == "bowie"  # classifier wins over size
    assert ident.identify([_frame_1000()]).animal == "bowie"
    assert loads == ["broken.pt", "good.pt"]  # loaded once, reused
