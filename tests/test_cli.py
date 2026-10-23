from fpl_optimizer.cli import snapshot_policy


def advice(captain=1, lineup=(1, 2, 3), buys=(), chip=None):
    week = {"buys": list(buys), "sells": [], "chip": chip, "captain": captain, "vice": 2, "lineup": list(lineup)}
    return {"recommended": {"weeks": [week]}}


def test_morning_keeps_snapshot_current_until_the_deadline_run():
    assert snapshot_policy("morning", {}, advice()) == (True, None)
    assert snapshot_policy("morning", advice() | {"emailed": False}, advice()) == (True, None)
    assert snapshot_policy("morning", advice() | {"deadline_run": True}, advice()) == (False, None)


def test_deadline_emails_once():
    assert snapshot_policy("deadline", {}, advice()) == (True, "advice")
    assert snapshot_policy("deadline", advice() | {"deadline_run": True, "emailed": True}, advice()) == (True, None)


def test_refresh_emails_only_when_advice_changed():
    sent = advice() | {"deadline_run": True, "emailed": True}
    assert snapshot_policy("refresh", sent, advice()) == (True, None)
    assert snapshot_policy("refresh", sent, advice(captain=3)) == (True, "changed")
    assert snapshot_policy("refresh", sent, advice(lineup=(1, 2, 4))) == (True, "changed")


def test_refresh_sends_the_normal_email_if_the_deadline_run_was_missed():
    assert snapshot_policy("refresh", advice(), advice()) == (True, "advice")
