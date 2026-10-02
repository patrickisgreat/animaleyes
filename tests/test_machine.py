from __future__ import annotations

from datetime import datetime

from tests.conftest import BOWIE, CAT, EATING, GONE_EMPTY, GONE_FULL, GRRR, Harness


def confirm_grrr(h: Harness) -> None:
    """Enough motion + Grrr verdicts to satisfy CONFIRMATIONS_REQUIRED at the LLM rate limit."""
    h.llm.identify_result = GRRR
    h.run(seconds=12)


def test_starts_idle_inside_window_and_watches_on_motion(h: Harness) -> None:
    h.load_plates(1)
    h.tick(motion=False)
    assert h.state() == "IDLE"
    h.tick(motion=True)
    assert h.state() == "WATCHING"


def test_outside_window_makes_no_llm_calls(tmp_path) -> None:
    h = Harness(tmp_path, start=datetime(2026, 9, 24, 12, 0))
    h.load_plates(1)
    h.llm.identify_result = GRRR
    h.run(seconds=30)
    assert h.state() == "OUTSIDE_WINDOW"
    assert h.llm.identify_calls == 0


def test_two_chamber_night(h: Harness) -> None:
    h.load_plates(1, 2)
    confirm_grrr(h)
    assert h.state() == "FEEDING"
    assert h.feeder.opens == ["open:1"]
    assert h.store.get_int("feeds_this_window") == 1

    # She eats, then leaves; the empty bowl is marked eaten and the lid closes.
    h.llm.feeding_result = EATING
    h.run(seconds=60)
    h.llm.feeding_result = GONE_EMPTY
    h.run(seconds=125)
    assert h.state() == "COOLDOWN"
    assert h.feeder.calls[-1] == "close"
    assert h.store.plates()[1] == "eaten"
    assert h.store.loaded_plates() == [2]

    # After MIN_GAP_MIN she comes back: the tray rotates to plate 2 and opens.
    h.clock.advance(minutes=61)
    h.tick(motion=False)
    assert h.state() == "IDLE"
    confirm_grrr(h)
    assert h.state() == "FEEDING"
    assert h.feeder.opens == ["open:1", "open:2"]
    assert "rotate" in h.feeder.calls

    h.llm.feeding_result = GONE_EMPTY
    h.run(seconds=125)
    h.clock.advance(minutes=61)
    h.tick(motion=False)
    assert h.state() == "DONE"
    assert h.store.get_int("feeds_this_window") == 2
    assert [e.kind for e in h.store.events(kinds=("open", "close"))] == [
        "close",
        "open",
        "close",
        "open",
    ]


def test_min_gap_blocks_a_second_open(h: Harness) -> None:
    h.load_plates(1, 2)
    confirm_grrr(h)
    h.llm.feeding_result = GONE_EMPTY
    h.run(seconds=125)
    assert h.state() == "COOLDOWN"
    h.clock.advance(minutes=30)
    confirm_grrr(h)
    assert h.state() == "COOLDOWN"
    assert h.feeder.opens == ["open:1"]
    h.clock.advance(minutes=31)
    h.tick(motion=False)
    assert h.state() == "IDLE"
    confirm_grrr(h)
    assert h.feeder.opens == ["open:1", "open:2"]


def test_min_gap_applies_even_when_grrr_is_confirmed_in_watching(h: Harness) -> None:
    h.load_plates(1, 2)
    h.store.set("last_open_at", h.clock.now.isoformat())
    confirm_grrr(h)
    assert h.state() == "WATCHING"
    assert h.feeder.opens == []
    assert h.events("grrr_blocked")


def test_leave_timeout_closes_and_presence_resets_it(h: Harness) -> None:
    h.load_plates(1)
    confirm_grrr(h)
    h.llm.feeding_result = GONE_FULL
    h.run(seconds=60)
    assert h.state() == "FEEDING"
    h.llm.feeding_result = EATING
    h.run(seconds=30)
    h.llm.feeding_result = GONE_FULL
    h.run(seconds=100)
    assert h.state() == "FEEDING", "presence must reset the leave timer"
    h.run(seconds=40)
    assert h.state() == "COOLDOWN"
    # She left food behind, so plate 1 is offered again next time.
    assert h.store.plates()[1] == "loaded"


def test_feeding_max_closes_even_while_eating(h: Harness) -> None:
    h.load_plates(1)
    confirm_grrr(h)
    h.llm.feeding_result = EATING
    h.run(seconds=15 * 60 + 5, step=5)
    assert h.state() == "COOLDOWN"
    assert [e.reason for e in h.store.events(kinds=("transition",)) if e.data["to"] == "CLOSING"][
        0
    ].startswith("FEEDING_MAX_MIN")


def test_veto_during_feed_notifies_and_keeps_feeding(h: Harness) -> None:
    h.load_plates(1)
    confirm_grrr(h)
    h.llm.feeding_result = EATING.__class__(
        grrr_at_bowl=True, bowl="food", other_animals_present=["bowie"], reason="bowie hovering"
    )
    h.run(seconds=45)
    assert h.state() == "FEEDING"
    assert len(h.events("bowie_during_feed")) == 1
    assert any("bowie_during_feed" in s for s in h.notifier.sent)
    assert h.feeder.calls[-1].startswith("open")


def test_veto_in_watching_for_bowie_and_cat(h: Harness) -> None:
    h.load_plates(1)
    h.llm.identify_result = BOWIE
    h.run(seconds=8)
    assert h.state() == "WATCHING"
    assert len(h.events("veto")) == 1
    assert h.events("veto")[0].data["animal"] == "bowie"
    h.llm.identify_result = CAT
    h.run(seconds=8)
    assert len(h.events("veto")) == 1  # rate-limited within VETO_REPEAT_S
    assert h.feeder.opens == []


def test_restart_mid_session_cannot_double_feed(h: Harness) -> None:
    h.load_plates(1, 2)
    confirm_grrr(h)
    assert h.state() == "FEEDING"
    h.machine = h.new_machine()
    h.machine.start()
    assert h.state() == "FEEDING"
    h.llm.feeding_result = EATING
    h.run(seconds=30)
    assert h.feeder.opens == ["open:1"]
    assert any("resumed FEEDING" in s for s in h.notifier.sent)


def test_restart_after_feed_sent_but_before_record_resumes_feeding(h: Harness) -> None:
    h.load_plates(1)
    h.store.set("state", "OPENING")
    h.machine = h.new_machine()
    h.machine.start()
    h.run(seconds=5)
    assert h.state() == "FEEDING"
    assert h.feeder.opens == []


def test_feed_failure_notifies_and_returns_to_idle_without_retry(h: Harness) -> None:
    h.load_plates(1)
    h.feeder.fail_open = True
    confirm_grrr(h)
    assert h.state() in ("IDLE", "WATCHING")
    assert len(h.events("feed_failed")) == 1
    assert any("FEED FAILED" in s for s in h.notifier.sent)
    assert h.store.get_int("feeds_this_window") == 0


def test_done_reports_hungry_grrr_once_per_hour(h: Harness) -> None:
    h.tick(motion=False)
    assert h.state() == "DONE"
    h.llm.identify_result = GRRR
    h.run(seconds=10)
    assert len(h.events("wanted_food_none_left")) == 1
    assert h.llm.identify_calls == 1
    h.run(seconds=30, motion=False)
    h.run(seconds=10)
    assert h.llm.identify_calls == 2
    assert len(h.events("wanted_food_none_left")) == 1
    assert h.feeder.opens == []


def test_setting_plates_from_dashboard_leaves_done(h: Harness) -> None:
    h.tick(motion=False)
    assert h.state() == "DONE"
    h.store.set_plate(3, "loaded", h.clock.now)
    h.store.set("plates_updated", 1)
    h.tick(motion=False)
    assert h.state() == "IDLE"


def test_manual_feed_now_opens_whatever_plate_is_under_the_lid(h: Harness) -> None:
    h.tick(motion=False)
    h.machine.request_feed_now()
    h.tick(motion=False)
    assert h.state() == "FEEDING"
    assert h.feeder.opens == ["open:1"]


def test_dry_run_never_touches_the_real_feeder(h: Harness) -> None:
    h.config.update({"DRY_RUN": True})
    h.load_plates(1)
    confirm_grrr(h)
    assert h.state() == "FEEDING"
    assert h.feeder.calls == []
    assert h.events("open")[0].data["mode"] == "DRY_RUN"


def test_watching_returns_to_idle_without_motion(h: Harness) -> None:
    h.load_plates(1)
    h.tick(motion=True)
    assert h.state() == "WATCHING"
    h.run(seconds=25, motion=False)
    assert h.state() == "IDLE"


def test_camera_offline_is_reported_once(h: Harness) -> None:
    h.load_plates(1)
    h.tick(motion=False)
    h.clock.advance(seconds=120)
    h.machine.tick()
    h.clock.advance(seconds=10)
    h.machine.tick()
    assert len(h.events("camera_offline")) == 1
    h.tick(motion=False)
    assert len(h.events("camera_online")) == 1


def test_manual_rotate_runs_on_tick_and_logs(h: Harness) -> None:
    h.machine.request_manual_action("rotate")
    h.tick(motion=False)
    assert "rotate" in h.feeder.calls
    assert len(h.events("manual_rotate")) == 1


def test_manual_open_and_close_route_through_the_machine(h: Harness) -> None:
    h.machine.request_manual_action("open")
    h.tick(motion=False)
    assert h.feeder.opens == ["open:1"]
    assert len(h.events("manual_open")) == 1
    h.machine.request_manual_action("close")
    h.tick(motion=False)
    assert "close" in h.feeder.calls
    assert len(h.events("manual_close")) == 1


def test_manual_action_honors_dry_run(h: Harness) -> None:
    h.config.update({"DRY_RUN": True})
    h.machine.request_manual_action("open")
    h.tick(motion=False)
    assert h.feeder.calls == []  # real feeder untouched in dry run
    assert h.events("manual_open")[0].data["mode"] == "DRY_RUN"


def test_manual_action_failure_logs_and_does_not_raise(h: Harness) -> None:
    h.feeder.fail_open = True
    h.machine.request_manual_action("open")
    h.tick(motion=False)
    assert len(h.events("manual_open_failed")) == 1
    assert h.events("manual_open") == []


def test_unknown_manual_action_rejected(h: Harness) -> None:
    import pytest

    with pytest.raises(ValueError):
        h.machine.request_manual_action("explode")


def test_two_compartment_absence_algorithm(h: Harness) -> None:
    """The owner's POC algorithm: open 1 -> gone 10 min -> close -> return opens+rotates to
    2 -> gone 10 min -> close -> DONE, never opening again. LEAVE_TIMEOUT_S=600, MIN_GAP_MIN=0."""
    h.config.update({"LEAVE_TIMEOUT_S": 600, "MIN_GAP_MIN": 0})
    h.load_plates(1, 2)

    confirm_grrr(h)
    assert h.state() == "FEEDING"
    assert h.feeder.opens == ["open:1"]

    # Gone just over 10 minutes -> close plate 1.
    h.llm.feeding_result = GONE_EMPTY
    h.run(seconds=610, motion=False)
    assert "close" in h.feeder.calls
    h.tick(motion=False)
    assert h.state() == "IDLE"  # MIN_GAP_MIN=0: ready again immediately, no hour-long wait

    # Dog returns -> tray rotates to compartment 2 and opens.
    confirm_grrr(h)
    assert h.state() == "FEEDING"
    assert h.feeder.opens == ["open:1", "open:2"]
    assert "rotate" in h.feeder.calls

    # Gone 10 min again -> close plate 2 -> no plates left -> DONE.
    h.llm.feeding_result = GONE_EMPTY
    h.run(seconds=610, motion=False)
    h.tick(motion=False)
    assert h.state() == "DONE"
    assert h.store.get_int("feeds_this_window") == 2

    # Terminated: even a confirmed dog does not open a third time.
    confirm_grrr(h)
    assert h.state() == "DONE"
    assert h.feeder.opens == ["open:1", "open:2"]


class _FakeEvents:
    def __init__(self):
        self.last_error = None
        self.motion_state = False
        self.animal_state = False
        self.on = False

    def motion_within(self, now, hold_s):
        return self.on


def test_camera_events_drive_motion_when_source_is_camera(h: Harness) -> None:
    ev = _FakeEvents()
    h.machine.events = ev
    h.config.update({"MOTION_SOURCE": "camera"})
    h.load_plates(1)
    h.tick(motion=False)  # frame-diff off, camera quiet
    assert h.state() == "IDLE"
    ev.on = True
    h.tick(motion=False)  # camera reports motion -> WATCHING (no frame-diff needed)
    assert h.state() == "WATCHING"


def test_falls_back_to_frame_diff_when_events_unhealthy(h: Harness) -> None:
    ev = _FakeEvents()
    ev.last_error = "subscription failed"
    ev.on = True  # would say motion, but it's broken so must be ignored
    h.machine.events = ev
    h.config.update({"MOTION_SOURCE": "camera"})
    h.load_plates(1)
    h.tick(motion=False)
    assert h.state() == "IDLE"  # broken events ignored, frame-diff quiet
    h.tick(motion=True)  # frame-diff fallback still feeds the dog
    assert h.state() == "WATCHING"
