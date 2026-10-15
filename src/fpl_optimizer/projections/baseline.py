"""Simple baseline: points per game adjusted for expected minutes and fixtures.

This is what the advice uses until our own model beats it (and FPL form) in the
backtest gate. Per player, using only matches before the target gameweek:

* appearance points: share of the last ``RECENT`` team fixtures with 60+ minutes
  (2 pts) and with 1-59 minutes (1 pt);
* everything else (goals, assists, clean sheets, bonus, defensive contributions,
  cards...) as a season-to-date rate per 90 minutes, shrunk towards a prior so a
  few lucky matches do not dominate. The prior is the player's own rate last season
  (when ``players`` carries ``prior_rate90``/``prior_minutes``), itself shrunk
  towards the position average; players new to the league get the position average;
* that rate times the expected minutes (mean of the last ``RECENT`` fixtures,
  zeros included) and a fixture-difficulty multiplier.

A double gameweek sums its fixtures; a blank gameweek is 0.
"""

from __future__ import annotations

import pandas as pd

from ..history import player_fixtures, team_at

RECENT = 5
SHRINK_MINUTES = 1800  # weight of the prior, in minutes of this season's data (tuned on 2024-25)
PRIOR_SHRINK_MINUTES = 900  # last season's rate is itself shrunk to the position average
FDR_SLOPE = 0.1  # +10% per difficulty step easier than 3, -10% per step harder


def player_rates(matches: pd.DataFrame, element_type: pd.Series, last_season: pd.DataFrame | None = None) -> pd.DataFrame:
    m = matches.copy()
    m["app"] = (m["minutes"] >= 60) * 2 + ((m["minutes"] > 0) & (m["minutes"] < 60)) * 1
    m["rest"] = m["points"] - m["app"]

    season = m.groupby("element").agg(minutes=("minutes", "sum"), rest=("rest", "sum"))
    recent = m.groupby("element").tail(RECENT).groupby("element").agg(
        xmins=("minutes", "mean"),
        p60=("minutes", lambda s: (s >= 60).mean()),
        psub=("minutes", lambda s: ((s > 0) & (s < 60)).mean()),
    )
    df = season.join(recent)
    df["element_type"] = element_type.reindex(df.index)
    df = df.dropna(subset=["element_type"])

    pos = df.groupby("element_type")[["rest", "minutes"]].sum()
    prior = (pos["rest"] / pos["minutes"].clip(lower=1) * 90).rename("prior")
    df = df.join(prior, on="element_type")
    if last_season is not None:
        ls = last_season.reindex(df.index)
        lm = ls["prior_minutes"].fillna(0)
        lr = ls["prior_rate90"].fillna(0)
        df["prior"] = (lr * lm + df["prior"] * PRIOR_SHRINK_MINUTES) / (lm + PRIOR_SHRINK_MINUTES)
    raw_rate = df["rest"] / df["minutes"].clip(lower=1) * 90
    df["rate90"] = (raw_rate * df["minutes"] + df["prior"] * SHRINK_MINUTES) / (df["minutes"] + SHRINK_MINUTES)
    return df


def project(
    matches: pd.DataFrame,
    fixtures: pd.DataFrame,
    players: pd.DataFrame,
    target_gws: list[int],
    deadline: pd.Timestamp,
) -> pd.DataFrame:
    """Expected points per player (rows) per target gameweek (columns)."""
    first = target_gws[0]
    past = matches[matches["gw"] < first]
    by_id = players.set_index("element")
    last_season = by_id[["prior_minutes", "prior_rate90"]] if "prior_rate90" in by_id else None
    rates = player_rates(past, by_id["element_type"], last_season)
    # A player with no row in the last two gameweeks has left the league (or the
    # dataset); FPL keeps a row for every registered player, even at 0 minutes.
    last_seen = past.groupby("element")["gw"].max()
    rates = rates[last_seen.reindex(rates.index) >= past["gw"].max() - 1]

    teams = players.set_index("element")["team"] if "team" in players else team_at(matches, first)
    teams = teams.reindex(rates.index).dropna().astype(int)
    pf = player_fixtures(fixtures, teams, target_gws).join(rates, on="element")

    mult = 1 + FDR_SLOPE * (3 - pf["difficulty"])
    pf["xp"] = 2 * pf["p60"] + pf["psub"] + pf["rate90"] * pf["xmins"] / 90 * mult
    out = pf.pivot_table(index="element", columns="gw", values="xp", aggfunc="sum")
    out = out.reindex(index=rates.index, columns=target_gws).fillna(0.0)
    return out
