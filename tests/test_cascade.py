from __future__ import annotations

from animaleyes.cascade import CascadeIdentifier
from animaleyes.detect import FeedingVerdict, Verdict


class _Fake:
    def __init__(self, verdict):
        self.verdict = verdict
        self.calls = 0
        self.reference_counts = {}
        self.feeding = FeedingVerdict(grrr_at_bowl=True)

    def identify(self, frames):
        self.calls += 1
        return self.verdict

    def feeding_check(self, frames):
        self.feeding_calls = getattr(self, "feeding_calls", 0) + 1
        return self.feeding


def _blind_cascade(tmp_path, confirm_verdict, **settings):
    from animaleyes.config import ConfigStore

    cfg = ConfigStore(tmp_path / "c.toml")
    cfg.update({"COLLECT_TRAINING": False, **settings})
    clock = [0.0]
    gate = _Fake(Verdict(animal="none"))
    confirm = _Fake(confirm_verdict)
    return CascadeIdentifier(gate, confirm, config=cfg, monotonic=lambda: clock[0]), confirm, clock


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


def test_blind_gate_still_gets_grrr_confirmed_quickly(tmp_path):
    """Tight framing: a dog filling the frame is "nothing" to the gate. Claude must get a look,
    and keep answering once it has seen her, so two confirmations land back to back."""
    c, confirm, clock = _blind_cascade(tmp_path, Verdict(animal="grrr", confidence=0.9))
    assert c.identify([]).animal == "grrr"  # first check of the visit probes straight away
    clock[0] = 3
    assert c.identify([]).animal == "grrr"  # sticky: gate still blind, Claude answers again
    assert confirm.calls == 2


def test_blind_gate_probes_are_throttled_when_nothing_is_there(tmp_path):
    c, confirm, clock = _blind_cascade(tmp_path, Verdict(animal="none"), CASCADE_PROBE_S=10)
    for t in (0, 3, 6, 9):
        clock[0] = t
        assert c.identify([]).animal == "none"
    assert confirm.calls == 1  # one look, then quiet until the next probe is due
    clock[0] = 12
    c.identify([])
    assert confirm.calls == 2


def test_probing_can_be_turned_off(tmp_path):
    c, confirm, _ = _blind_cascade(
        tmp_path, Verdict(animal="grrr", confidence=0.9), CASCADE_PROBE_S=0
    )
    assert c.identify([]).animal == "none"
    assert confirm.calls == 0


def test_feeding_presence_falls_back_to_claude_when_the_gate_cannot_see_her():
    gate = _Fake(Verdict(animal="none"))
    gate.feeding = FeedingVerdict(grrr_at_bowl=False, reason="no animal detected")
    confirm = _Fake(Verdict(animal="grrr"))
    confirm.feeding = FeedingVerdict(grrr_at_bowl=True, bowl="food", reason="head in bowl")
    v = CascadeIdentifier(gate, confirm).feeding_check([])
    assert v.grrr_at_bowl is True and v.bowl == "food"


def test_feeding_presence_stays_free_when_the_gate_sees_her():
    gate = _Fake(Verdict(animal="grrr"))
    confirm = _Fake(Verdict(animal="grrr"))
    CascadeIdentifier(gate, confirm).feeding_check([])
    assert getattr(confirm, "feeding_calls", 0) == 0


def test_an_animal_the_gate_cannot_name_goes_to_claude():
    gate = _Fake(Verdict(animal="unsure", confidence=0.4))
    confirm = _Fake(Verdict(animal="grrr", confidence=0.9))
    assert CascadeIdentifier(gate, confirm).identify([]).animal == "grrr"
    assert confirm.calls == 1
