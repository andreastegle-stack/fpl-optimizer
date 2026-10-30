import json
from datetime import datetime, timezone

from fpl_optimizer.report import fmt, no_datetime
from fpl_optimizer.schedule import decide, pending_targets, should_kick, timer_plan
from fpl_optimizer.scorecard import advised_points, followed

EVENTS = [{"id": 8, "deadline_time": "2026-10-23T17:30:00Z"}]


def test_deadline_window(tmp_path):
    at = lambda h, m=0: datetime(2026, 10, 23, h, m, tzinfo=timezone.utc)
    assert decide(EVENTS, at(14, 35), morning=False, advice_dir=tmp_path) == "deadline"  # 2h55 before
    assert decide(EVENTS, at(12, 0), morning=False, advice_dir=tmp_path) == "skip"
    assert decide(EVENTS, at(1, 30), morning=True, advice_dir=tmp_path) == "morning"
    assert decide(EVENTS, at(16, 47), morning=False, advice_dir=tmp_path) == "deadline"  # the 23.10 run 43 min before
    assert decide(EVENTS, at(17, 20), morning=False, advice_dir=tmp_path) == "skip"  # too late


def test_deadline_runs_once(tmp_path):
    (tmp_path / "gw08.json").write_text(json.dumps({"deadline_run": True}))
    now = datetime(2026, 10, 23, 15, 5, tzinfo=timezone.utc)
    assert decide(EVENTS, now, morning=False, advice_dir=tmp_path) == "skip"


def test_norwegian_formatting():
    assert no_datetime("2026-10-23T17:30:00Z") == "fredag 23. oktober kl. 19:30"
    assert fmt(-1.26, signed=True) == "−1,3"
    assert fmt(3.0) == "3,0"


def _advice(chip=None, hits=0):
    week = {"lineup": list(range(1, 12)), "bench": [12, 13, 14, 15], "captain": 1, "vice": 2, "chip": chip, "hits": hits, "buys": [20], "sells": [5]}
    return {"recommended": {"weeks": [week]}}


def test_advised_points_counts_captain_chips_and_hits():
    live = {p: 2 for p in range(1, 16)} | {1: 10}
    assert advised_points(_advice(), live) == 10 * 2 + 10 * 2
    assert advised_points(_advice(chip="3xc"), live) == 10 * 3 + 10 * 2
    assert advised_points(_advice(chip="bboost"), live) == 10 * 2 + 10 * 2 + 8
    assert advised_points(_advice(hits=1), live) == 36


def test_followed():
    a = _advice()
    assert followed(a, {20}, {5}, 1) == "ja"
    assert followed(a, {20}, {5}, 3) == "delvis"
    assert followed(a, set(), set(), 3) == "nei"


DL = datetime(2026, 10, 23, 17, 30, tzinfo=timezone.utc)
UTC = lambda h, m=0, d=23: datetime(2026, 10, d, h, m, tzinfo=timezone.utc)


def test_timer_targets_two_hours_and_forty_minutes_before():
    steps = pending_targets(DL, UTC(6, 22), {})
    assert steps == [(UTC(15, 30), "deadline"), (UTC(16, 50), "refresh")]
    assert pending_targets(DL, UTC(6, 22), {"deadline_run": True}) == [(UTC(16, 50), "refresh")]
    assert pending_targets(DL, UTC(6, 22), {"deadline_run": True, "refresh_run": True}) == []


def test_timer_catches_up_when_started_late():
    # Started 1 hour before: the deadline step runs now, refresh at 16:50.
    assert pending_targets(DL, UTC(16, 30), {}) == [(UTC(16, 30), "deadline"), (UTC(16, 50), "refresh")]
    # Started 25 minutes before: one run now covers both.
    assert pending_targets(DL, UTC(17, 5), {}) == [(UTC(17, 5), "deadline")]
    # Too close to the deadline: nothing.
    assert pending_targets(DL, UTC(17, 25), {}) == []


def test_timer_hands_over_after_its_job_budget():
    mine, handover = timer_plan(DL, UTC(6, 22), {})
    assert mine == [] and handover == UTC(11, 52)
    mine, handover = timer_plan(DL, UTC(11, 52), {})
    assert [m for _, m in mine] == ["deadline", "refresh"] and handover is None


def test_kick_only_when_deadline_is_near_and_steps_remain(tmp_path):
    assert should_kick(EVENTS, UTC(6, 22), advice_dir=tmp_path)
    assert should_kick(EVENTS, UTC(6, 0, d=22), advice_dir=tmp_path)  # 35.5 h before
    assert not should_kick(EVENTS, UTC(1, 0, d=22), advice_dir=tmp_path)  # 40.5 h before
    (tmp_path / "gw08.json").write_text(json.dumps({"deadline_run": True, "refresh_run": True}))
    assert not should_kick(EVENTS, UTC(6, 22), advice_dir=tmp_path)
