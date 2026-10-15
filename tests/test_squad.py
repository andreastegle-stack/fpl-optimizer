from datetime import datetime, timezone

from fpl_optimizer.squad import free_transfers_for, last_deadline_event, next_event, selling_price


def test_selling_price_keeps_half_the_profit_rounded_down():
    assert selling_price(50, 53) == 51  # +0.3m -> +0.1m
    assert selling_price(50, 54) == 52
    assert selling_price(50, 50) == 50


def test_selling_price_takes_losses_in_full():
    assert selling_price(50, 47) == 47


def _hist(transfers_by_gw):
    return [{"event": gw, "event_transfers": n} for gw, n in transfers_by_gw.items()]


def test_free_transfers_replay_andreas_2026():
    # GW2: 1 used, GW3: 0, GW4: 2, GW5: 0, GW6: 2 -> 1 FT for GW7
    history = _hist({1: 0, 2: 1, 3: 0, 4: 2, 5: 0, 6: 2})
    assert free_transfers_for(history, [], 7, max_ft=5) == 1


def test_free_transfers_cap_and_hits():
    history = _hist({gw: 0 for gw in range(1, 10)})
    assert free_transfers_for(history, [], 10, max_ft=5) == 5
    history = _hist({1: 0, 2: 3})  # two hits: next week still gets 1
    assert free_transfers_for(history, [], 3, max_ft=5) == 1


def test_wildcard_keeps_banked_transfers():
    history = _hist({1: 0, 2: 0, 3: 9, 4: 0})
    chips = [{"event": 3, "name": "wildcard"}]
    assert free_transfers_for(history, chips, 4, max_ft=5) == 2
    assert free_transfers_for(history, chips, 5, max_ft=5) == 3


def test_deadlines():
    events = [
        {"id": 1, "deadline_time": "2026-08-21T17:30:00Z"},
        {"id": 2, "deadline_time": "2026-08-28T17:30:00Z"},
    ]
    now = datetime(2026, 8, 25, tzinfo=timezone.utc)
    assert last_deadline_event(events, now) == 1
    assert next_event(events, now)["id"] == 2
