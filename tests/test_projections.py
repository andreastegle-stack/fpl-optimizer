from datetime import datetime, timezone

import pandas as pd

from fpl_optimizer.availability import availability, expected_back
from fpl_optimizer.projections import baseline, form


def _data():
    # Two clubs, four past gameweeks, one future gameweek with an easy (2) and hard (5) side.
    kick = lambda gw: pd.Timestamp("2026-09-01T15:00:00Z") + pd.Timedelta(days=7 * gw)
    rows = []
    for gw in range(1, 5):
        rows.append({"element": 1, "gw": gw, "fixture": gw, "kickoff": kick(gw), "minutes": 90, "points": 6, "team": 1})
        rows.append({"element": 2, "gw": gw, "fixture": gw, "kickoff": kick(gw), "minutes": 90, "points": 2, "team": 2})
        rows.append({"element": 3, "gw": gw, "fixture": gw, "kickoff": kick(gw), "minutes": 0, "points": 0, "team": 2})
    matches = pd.DataFrame(rows)
    fixtures = pd.DataFrame(
        [{"fixture": 5, "gw": 5, "team_h": 1, "team_a": 2, "diff_h": 2, "diff_a": 5, "kickoff": kick(5)}]
    )
    players = pd.DataFrame({"element": [1, 2, 3], "element_type": [3, 3, 3], "team": [1, 2, 2]})
    return matches, fixtures, players, kick(5) - pd.Timedelta(hours=2)


def test_baseline_orders_players_and_zeroes_non_players():
    matches, fixtures, players, deadline = _data()
    xp = baseline.project(matches, fixtures, players, [5], deadline)
    assert xp.at[1, 5] > xp.at[2, 5] > 0
    assert xp.at[3, 5] == 0


def test_baseline_fixture_difficulty_matters():
    matches, fixtures, players, deadline = _data()
    easy = baseline.project(matches, fixtures, players, [5], deadline).at[1, 5]
    fixtures[["diff_h", "diff_a"]] = 5
    hard = baseline.project(matches, fixtures, players, [5], deadline).at[1, 5]
    assert easy > hard


def test_form_is_recent_points_per_fixture():
    matches, fixtures, players, deadline = _data()
    xp = form.project(matches, fixtures, players, [5], deadline)
    assert xp.at[1, 5] == 6.0
    assert xp.at[2, 5] == 2.0


def test_availability_injury_and_override():
    now = datetime(2026, 10, 15, tzinfo=timezone.utc)
    events = [{"id": g, "deadline_time": f"2026-10-{10 + 7 * (g - 7):02d}T10:00:00Z"} for g in (8, 9, 10)]
    players = pd.DataFrame(
        [
            {"element": 1, "status": "a", "chance_of_playing_next_round": None, "news": ""},
            {"element": 2, "status": "d", "chance_of_playing_next_round": 50, "news": "Knock - 50% chance of playing"},
            {"element": 3, "status": "i", "chance_of_playing_next_round": 0, "news": "Hamstring - Expected back 25 Oct"},
            {"element": 4, "status": "a", "chance_of_playing_next_round": None, "news": ""},
        ]
    )
    overrides = [{"element": 4, "availability": 0.0, "until_gw": 8}]
    av = availability(players, events, [8, 9, 10], now, overrides)
    assert list(av.loc[1]) == [1.0, 1.0, 1.0]
    assert av.at[2, 8] == 0.5 and av.at[2, 10] > av.at[2, 8]
    assert av.at[3, 8] == 0.0 and av.at[3, 10] == 1.0  # GW10 deadline 31 Oct is after 25 Oct
    assert av.at[4, 8] == 0.0 and av.at[4, 9] == 1.0


def test_expected_back_rolls_into_next_year():
    now = datetime(2026, 11, 20, tzinfo=timezone.utc)
    assert expected_back("Expected back 05 Jan", now).year == 2027
