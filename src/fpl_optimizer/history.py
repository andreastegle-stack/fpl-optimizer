"""Common data shapes shared by live runs and backtests.

Projection methods only ever see these three tables, built either from the live FPL
API or from a past season in the vaastav/Fantasy-Premier-League dataset. That keeps
the backtest honest: a method is scored on exactly the inputs it gets in production.

``matches``  one row per player per team fixture already played
             (element, gw, fixture, kickoff, minutes, points, team, opponent, was_home,
             and the scoring stats in ``STATS``)
``fixtures`` one row per fixture (fixture, gw, team_h, team_a, diff_h, diff_a, kickoff,
             score_h, score_a; scores are empty until the match is played)
``players``  one row per player (element, element_type, team, web_name, ...)
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import requests

POSITION_TYPES = {"GK": 1, "GKP": 1, "DEF": 2, "MID": 3, "FWD": 4}
STATS = ["goals_scored", "assists", "clean_sheets", "goals_conceded", "saves", "penalties_saved", "defensive_contribution"]
# 2025/26 onwards scoring (CLAUDE.md); read from game_config where it matters live.
GOAL_PTS = {1: 10, 2: 6, 3: 5, 4: 4}
CS_PTS = {1: 4, 2: 4, 3: 1, 4: 0}
DEFCON_THRESHOLD = {2: 10, 3: 12, 4: 12}


def components(m: pd.DataFrame, element_type: pd.Series) -> pd.DataFrame:
    """Split each match's points into appearance, attacking (goals, assists),
    defensive (clean sheet, goals conceded, saves, penalty saves, defensive
    contributions) and the rest (bonus, cards, own goals...)."""
    t = element_type.reindex(m["element"]).to_numpy()
    t = pd.Series(t, index=m.index)
    app = (m["minutes"] >= 60) * 2 + ((m["minutes"] > 0) & (m["minutes"] < 60)) * 1
    att = m["goals_scored"] * t.map(GOAL_PTS).fillna(0) + m["assists"] * 3
    gk_def = t.isin([1, 2])
    defence = (
        m["clean_sheets"] * t.map(CS_PTS).fillna(0)
        - gk_def * (m["goals_conceded"] // 2)
        + (t == 1) * (m["saves"] // 3)
        + m["penalties_saved"] * 5
        + 2 * (m["defensive_contribution"] >= t.map(DEFCON_THRESHOLD).fillna(99))
    )
    return pd.DataFrame({"app": app, "att": att, "def": defence, "other": m["points"] - app - att - defence}, index=m.index)
VAASTAV_RAW = "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data"


def live_matches(summaries: dict[int, dict], elements: dict[int, dict]) -> pd.DataFrame:
    rows = []
    for element, summary in summaries.items():
        for h in summary["history"]:
            rows.append(
                {
                    "element": element,
                    "gw": h["round"],
                    "fixture": h["fixture"],
                    "kickoff": h["kickoff_time"],
                    "minutes": h["minutes"],
                    "points": h["total_points"],
                    "team": None,
                    "opponent": h["opponent_team"],
                    "was_home": h["was_home"],
                    **{s: h.get(s, 0) or 0 for s in STATS},
                }
            )
    df = pd.DataFrame(rows, columns=["element", "gw", "fixture", "kickoff", "minutes", "points", "team", "opponent", "was_home", *STATS])
    df["kickoff"] = pd.to_datetime(df["kickoff"], utc=True)
    df["team"] = df["element"].map(lambda e: elements[e]["team"])
    return df.sort_values(["element", "kickoff"]).reset_index(drop=True)


def live_fixtures(fixtures: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(
        [
            {
                "fixture": f["id"],
                "gw": f["event"],
                "team_h": f["team_h"],
                "team_a": f["team_a"],
                "diff_h": f["team_h_difficulty"],
                "diff_a": f["team_a_difficulty"],
                "kickoff": f["kickoff_time"],
                "score_h": f["team_h_score"] if f.get("finished") else None,
                "score_a": f["team_a_score"] if f.get("finished") else None,
            }
            for f in fixtures
        ]
    )
    df["kickoff"] = pd.to_datetime(df["kickoff"], utc=True)
    return df


def live_players(bootstrap: dict) -> pd.DataFrame:
    cols = [
        "id", "code", "web_name", "element_type", "team", "now_cost", "status", "chance_of_playing_next_round",
        "news", "form", "ep_next", "points_per_game", "minutes", "total_points", "selected_by_percent",
    ]
    df = pd.DataFrame(bootstrap["elements"])[cols].rename(columns={"id": "element"})
    df["form"] = df["form"].astype(float)
    df["ep_next"] = df["ep_next"].astype(float)
    return df


def download_season(season: str, dest: Path) -> Path:
    """Fetch the three CSVs we need for one past season (cached in ``dest``)."""
    dest = Path(dest) / season
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("gws/merged_gw.csv", "fixtures.csv", "teams.csv", "players_raw.csv"):
        target = dest / Path(name).name
        if target.exists() and target.stat().st_size > 0:
            continue
        resp = requests.get(f"{VAASTAV_RAW}/{season}/{name}", timeout=120)
        resp.raise_for_status()
        target.write_bytes(resp.content)
    return dest


def load_season(season_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Load a vaastav season into (matches, fixtures, players).

    ``matches`` also carries ``xp`` (FPL's own published expected points for that
    gameweek), used only as an informational reference in the backtest.
    """
    season_dir = Path(season_dir)
    raw = pd.read_csv(season_dir / "merged_gw.csv")
    raw = raw[raw["position"].isin(POSITION_TYPES)]
    teams = pd.read_csv(season_dir / "teams.csv")
    team_id = dict(zip(teams["name"], teams["id"]))

    matches = pd.DataFrame(
        {
            "element": raw["element"].astype(int),
            "gw": raw["GW"].astype(int),
            "fixture": raw["fixture"].astype(int),
            "kickoff": pd.to_datetime(raw["kickoff_time"], utc=True),
            "minutes": raw["minutes"].astype(int),
            "points": raw["total_points"].astype(int),
            "team": raw["team"].map(team_id).astype(int),
            "opponent": raw["opponent_team"].astype(int),
            "was_home": raw["was_home"].astype(bool),
            "xp": raw["xP"].astype(float),
            "element_type": raw["position"].map(POSITION_TYPES).astype(int),
            **{s: (raw[s] if s in raw else pd.Series(0, index=raw.index)).fillna(0).astype(int) for s in STATS},
        }
    ).sort_values(["element", "kickoff"]).reset_index(drop=True)

    fx = pd.read_csv(season_dir / "fixtures.csv")
    fixtures = pd.DataFrame(
        {
            "fixture": fx["id"].astype(int),
            "gw": fx["event"].astype(int),
            "team_h": fx["team_h"].astype(int),
            "team_a": fx["team_a"].astype(int),
            "diff_h": fx["team_h_difficulty"].astype(int),
            "diff_a": fx["team_a_difficulty"].astype(int),
            "kickoff": pd.to_datetime(fx["kickoff_time"], utc=True),
            "score_h": fx["team_h_score"].astype(float),
            "score_a": fx["team_a_score"].astype(float),
        }
    )

    last = matches.groupby("element").tail(1)
    codes = pd.read_csv(season_dir / "players_raw.csv").set_index("id")["code"]
    players = pd.DataFrame(
        {
            "element": last["element"].values,
            "element_type": last["element_type"].values,
            "web_name": raw.groupby("element")["name"].last().reindex(last["element"]).values,
            "code": codes.reindex(last["element"]).values,
        }
    )
    return matches, fixtures, players


def season_rates(season_dir: Path) -> pd.DataFrame:
    """Per player (by FPL ``code``, stable across seasons): minutes and non-appearance
    points per 90 over a whole season, in total and split into attacking, defensive
    and other points. Used as the projections' starting point."""
    season_dir = Path(season_dir)
    m, _, _ = load_season(season_dir)
    comp = components(m, m.groupby("element")["element_type"].last())
    m = m.assign(rest=m["points"] - comp["app"], att=comp["att"], dfn=comp["def"], oth=comp["other"])
    per = m.groupby("element").agg(prior_minutes=("minutes", "sum"), rest=("rest", "sum"), att=("att", "sum"), dfn=("dfn", "sum"), oth=("oth", "sum"))
    codes = pd.read_csv(season_dir / "players_raw.csv").set_index("id")["code"]
    per["code"] = codes.reindex(per.index).values
    per = per.dropna(subset=["code"]).astype({"code": int})
    per90 = 90 / per["prior_minutes"].clip(lower=1)
    per["prior_rate90"] = per["rest"] * per90
    per["prior_att90"] = per["att"] * per90
    per["prior_def90"] = per["dfn"] * per90
    per["prior_other90"] = per["oth"] * per90
    return per.set_index("code")[["prior_minutes", "prior_rate90", "prior_att90", "prior_def90", "prior_other90"]]


def with_prior(players: pd.DataFrame, rates: pd.DataFrame | None) -> pd.DataFrame:
    """Attach last season's rates to ``players`` (matched on ``code``)."""
    if rates is None or "code" not in players:
        return players
    return players.join(rates, on="code")


def previous_season(season: str) -> str:
    start = int(season[:4]) - 1
    return f"{start}-{str(start + 1)[2:]}"


def team_at(matches: pd.DataFrame, before_gw: int) -> pd.Series:
    """Each player's club as of the last fixture before ``before_gw``."""
    past = matches[matches["gw"] < before_gw]
    return past.groupby("element")["team"].last()


def player_fixtures(fixtures: pd.DataFrame, teams: pd.Series, gws: list[int]) -> pd.DataFrame:
    """One row per (player, fixture) in ``gws`` with the difficulty from the player's side."""
    fx = fixtures[fixtures["gw"].isin(gws)]
    cols = ["fixture", "gw", "team", "opponent", "difficulty"]
    home = fx.rename(columns={"team_h": "team", "team_a": "opponent", "diff_h": "difficulty"})[cols].assign(home=True)
    away = fx.rename(columns={"team_a": "team", "team_h": "opponent", "diff_a": "difficulty"})[cols].assign(home=False)
    team_fx = pd.concat([home, away], ignore_index=True)
    pt = teams.rename("team").reset_index()
    return pt.merge(team_fx, on="team", how="inner")
