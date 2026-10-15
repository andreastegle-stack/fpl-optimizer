"""Common data shapes shared by live runs and backtests.

Projection methods only ever see these three tables, built either from the live FPL
API or from a past season in the vaastav/Fantasy-Premier-League dataset. That keeps
the backtest honest: a method is scored on exactly the inputs it gets in production.

``matches``  one row per player per team fixture already played
             (element, gw, fixture, kickoff, minutes, points, team)
``fixtures`` one row per fixture (fixture, gw, team_h, team_a, diff_h, diff_a, kickoff)
``players``  one row per player (element, element_type, team, web_name, ...)
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import requests

POSITION_TYPES = {"GK": 1, "GKP": 1, "DEF": 2, "MID": 3, "FWD": 4}
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
                }
            )
    df = pd.DataFrame(rows, columns=["element", "gw", "fixture", "kickoff", "minutes", "points", "team", "opponent", "was_home"])
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
    points per 90 over a whole season. Used as the baseline's starting point."""
    season_dir = Path(season_dir)
    raw = pd.read_csv(season_dir / "merged_gw.csv", usecols=["element", "minutes", "total_points"])
    app = (raw["minutes"] >= 60) * 2 + ((raw["minutes"] > 0) & (raw["minutes"] < 60)) * 1
    raw = raw.assign(rest=raw["total_points"] - app)
    per = raw.groupby("element").agg(prior_minutes=("minutes", "sum"), rest=("rest", "sum"))
    codes = pd.read_csv(season_dir / "players_raw.csv").set_index("id")["code"]
    per["code"] = codes.reindex(per.index).values
    per = per.dropna(subset=["code"]).astype({"code": int})
    per["prior_rate90"] = per["rest"] / per["prior_minutes"].clip(lower=1) * 90
    return per.set_index("code")[["prior_minutes", "prior_rate90"]]


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
    home = fx.rename(columns={"team_h": "team", "diff_h": "difficulty"})[["fixture", "gw", "team", "difficulty"]].assign(home=True)
    away = fx.rename(columns={"team_a": "team", "diff_a": "difficulty"})[["fixture", "gw", "team", "difficulty"]].assign(home=False)
    team_fx = pd.concat([home, away], ignore_index=True)
    pt = teams.rename("team").reset_index()
    return pt.merge(team_fx, on="team", how="inner")
