from __future__ import annotations

from animaleyes.cascade import CascadeIdentifier
from animaleyes.detect import FeedingVerdict, Verdict


class _Fake:
    def __init__(self, verdict):
        self.verdict = verdict
        self.calls = 0
        self.reference_counts = {}

    def identify(self, frames):
        self.calls += 1
        return self.verdict

    def feeding_check(self, frames):
        return FeedingVerdict(grrr_at_bowl=True)


def test_cat_is_decided_locally_without_calling_claude():
    gate = _Fake(Verdict(animal="cat", confidence=0.9))
    confirm = _Fake(Verdict(animal="grrr", confidence=0.9))
    v = CascadeIdentifier(gate, confirm).identify([])
    assert v.animal == "cat"
    assert confirm.calls == 0  # no paid call for a cat


def test_nothing_is_decided_locally_without_calling_claude():
    gate = _Fake(Verdict(animal="none"))
    confirm = _Fake(Verdict(animal="grrr", confidence=0.9))
    assert CascadeIdentifier(gate, confirm).identify([]).animal == "none"
    assert confirm.calls == 0


def test_a_dog_escalates_to_claude_for_the_real_identity():
    # YOLO guessed "bowie" by size, but Claude is the authority and says Grrr.
    gate = _Fake(Verdict(animal="bowie", confidence=0.8))
    confirm = _Fake(Verdict(animal="grrr", confidence=0.86, at_bowl=True))
    v = CascadeIdentifier(gate, confirm).identify([])
    assert v.animal == "grrr"
    assert confirm.calls == 1


def test_is_grrr_can_skip_the_at_bowl_requirement():
    v = Verdict(animal="grrr", confidence=0.7, at_bowl=False)
    assert v.is_grrr(0.65, require_at_bowl=True) is False
    assert v.is_grrr(0.65, require_at_bowl=False) is True


def test_cascade_saves_confident_dog_as_training_frame(tmp_path):
    from datetime import datetime

    from animaleyes.camera import Frame
    from animaleyes.config import ConfigStore

    cfg = ConfigStore(tmp_path / "c.toml")  # defaults: COLLECT_TRAINING=True, min_conf=0.6
    tdir = tmp_path / "training"
    c = CascadeIdentifier(
        _Fake(Verdict(animal="grrr", confidence=0.9)),  # YOLO: a dog
        _Fake(Verdict(animal="grrr", confidence=0.86)),  # Claude: Grrr
        config=cfg,
        training_dir=tdir,
    )
    c.identify([Frame(jpeg=b"\xff\xd8x\xff\xd9", at=datetime(2026, 10, 2, 21, 0, 0))])
    assert len(list((tdir / "grrr").glob("*.jpg"))) == 1


def test_cascade_does_not_save_low_confidence(tmp_path):
    from datetime import datetime

    from animaleyes.camera import Frame
    from animaleyes.config import ConfigStore

    tdir = tmp_path / "training"
    c = CascadeIdentifier(
        _Fake(Verdict(animal="bowie", confidence=0.9)),
        _Fake(Verdict(animal="grrr", confidence=0.4)),  # below TRAINING_MIN_CONF
        config=ConfigStore(tmp_path / "c.toml"),
        training_dir=tdir,
    )
    c.identify([Frame(jpeg=b"\xff\xd8x\xff\xd9", at=datetime(2026, 10, 2, 21, 0, 0))])
    assert not (tdir / "grrr").exists() or not list((tdir / "grrr").glob("*.jpg"))
