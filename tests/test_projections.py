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


def test_components_split_points_by_source():
    from fpl_optimizer.history import components

    m = pd.DataFrame(
        [
            # DEF: 90 min, goal, clean sheet, 10 defensive contributions, 1 bonus -> 2+6+4+2+1 = 15
            {"element": 1, "minutes": 90, "points": 15, "goals_scored": 1, "assists": 0, "clean_sheets": 1, "goals_conceded": 0, "saves": 0, "penalties_saved": 0, "defensive_contribution": 10},
            # GK: 90 min, 6 saves, conceded 3 -> 2 + 2 - 1 = 3
            {"element": 2, "minutes": 90, "points": 3, "goals_scored": 0, "assists": 0, "clean_sheets": 0, "goals_conceded": 3, "saves": 6, "penalties_saved": 0, "defensive_contribution": 0},
            # MID sub: 20 min, assist -> 1 + 3 = 4
            {"element": 3, "minutes": 20, "points": 4, "goals_scored": 0, "assists": 1, "clean_sheets": 0, "goals_conceded": 0, "saves": 0, "penalties_saved": 0, "defensive_contribution": 0},
        ]
    )
    c = components(m, pd.Series({1: 2, 2: 1, 3: 3}))
    assert list(c.loc[0]) == [2, 6, 6, 1]
    assert list(c.loc[1]) == [2, 0, 1, 0]
    assert list(c.loc[2]) == [1, 3, 0, 0]


def test_team_ratings_are_venue_adjusted_and_shrunk():
    from fpl_optimizer.projections.model import team_ratings

    kick = pd.Timestamp("2026-09-01T15:00:00Z")
    rows = [
        {"fixture": i, "gw": i, "team_h": h, "team_a": a, "diff_h": 3, "diff_a": 3, "kickoff": kick + pd.Timedelta(days=i), "score_h": sh, "score_a": sa}
        for i, (h, a, sh, sa) in enumerate([(1, 2, 4, 0), (2, 1, 0, 3), (3, 4, 1, 1), (4, 3, 2, 1)], start=1)
    ]
    rows.append({"fixture": 9, "gw": 9, "team_h": 1, "team_a": 3, "diff_h": 3, "diff_a": 3, "kickoff": kick + pd.Timedelta(days=30), "score_h": None, "score_a": None})
    r, mu_h, mu_a = team_ratings(pd.DataFrame(rows), kick + pd.Timedelta(days=10))
    assert mu_h > mu_a
    assert r.at[1, "attack"] > 1 > r.at[2, "attack"]
    assert r.at[1, "defence"] < 1 < r.at[2, "defence"]
    assert r.at[1, "attack"] < 2  # shrunk towards the league average
