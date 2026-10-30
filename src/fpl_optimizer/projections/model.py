"""Our own expected-points model, v1: the baseline plus home/away and opponent strength.

Same building blocks as the baseline (appearance points from recent minutes, rates
per 90 shrunk towards last season and the position average), but each player's
non-appearance points are split by where they come from, so a fixture can move
them differently:

* **attacking** (goals, assists) scale with how many goals the player's team is
  expected to score: the venue (home teams score more) times the opponent's
  defensive weakness;
* **defensive** (clean sheet, goals conceded, saves, defensive contributions)
  scale with the chance of a clean sheet, ``exp(-goals expected against)``, from
  the venue and the opponent's attacking strength;
* **other** (bonus, cards) is left as it is.

Team ratings and the home/away goal rates are fitted only on matches played before
the deadline, shrunk towards the league average so a few results do not swing them.
The player's own rate already reflects his team's average fixture, so only the
difference from an average fixture is applied.

Status (30.10.2026): ``project_v1`` does not pass the gate, so it is not registered
(``project = None``) and advice keeps using the baseline. Backtest 2025-26, GW5-38
(tuned on 2024-25 only): next-GW RMSE 2.715 vs baseline 2.714, six-GW RMSE 9.404 vs
9.358. The baseline already gets most of the home advantage through FPL's fixture
difficulty, which is lower for home teams. See the PR that added this module for the
variants tried. To register a model, set ``project`` to it; the CI gate then
backtests it and fails the PR unless it beats both references.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..history import components, player_fixtures, team_at

RECENT = 5
SHRINK_MINUTES = 1800  # weight of the prior per component, in minutes of this season
PRIOR_SHRINK_MINUTES = 900  # last season's rate is itself shrunk to the position average
TEAM_SHRINK_GOALS = 10.0  # weight of the league average in a team's rating, in goals
MULT_CLIP = (0.5, 1.8)


def team_ratings(fixtures: pd.DataFrame, deadline: pd.Timestamp) -> tuple[pd.DataFrame, float, float]:
    """Attack (goals scored vs expected) and defence (goals conceded vs expected) per
    team from finished matches before the deadline, venue-adjusted and shrunk.
    Returns (ratings, home goals per match, away goals per match)."""
    played = fixtures[(fixtures["kickoff"] < deadline) & fixtures["score_h"].notna()]
    if played.empty:
        return pd.DataFrame(columns=["attack", "defence"]), 1.5, 1.2
    mu_h, mu_a = float(played["score_h"].mean()), float(played["score_a"].mean())
    home = pd.DataFrame({"team": played["team_h"], "gf": played["score_h"], "ga": played["score_a"], "exp_f": mu_h, "exp_a": mu_a})
    away = pd.DataFrame({"team": played["team_a"], "gf": played["score_a"], "ga": played["score_h"], "exp_f": mu_a, "exp_a": mu_h})
    t = pd.concat([home, away]).groupby("team").sum()
    k = TEAM_SHRINK_GOALS
    ratings = pd.DataFrame(
        {"attack": (t["gf"] + k) / (t["exp_f"] + k), "defence": (t["ga"] + k) / (t["exp_a"] + k)}
    )
    return ratings, mu_h, mu_a


def player_rates(past: pd.DataFrame, by_id: pd.DataFrame) -> pd.DataFrame:
    element_type = by_id["element_type"]
    comp = components(past, element_type)
    m = past.assign(**{c: comp[c] for c in ("att", "def", "other")})
    season = m.groupby("element").agg(minutes=("minutes", "sum"), att=("att", "sum"), dfn=("def", "sum"), oth=("other", "sum"))
    recent = m.groupby("element").tail(RECENT).groupby("element").agg(
        xmins=("minutes", "mean"),
        p60=("minutes", lambda s: (s >= 60).mean()),
        psub=("minutes", lambda s: ((s > 0) & (s < 60)).mean()),
    )
    df = season.join(recent)
    df["element_type"] = element_type.reindex(df.index)
    df = df.dropna(subset=["element_type"])

    pos = df.groupby("element_type")[["att", "dfn", "oth", "minutes"]].sum()
    for col, prior_col in (("att", "prior_att90"), ("dfn", "prior_def90"), ("oth", "prior_other90")):
        pos_avg = (pos[col] / pos["minutes"].clip(lower=1) * 90).reindex(df["element_type"]).to_numpy()
        prior = pd.Series(pos_avg, index=df.index)
        if prior_col in by_id:
            lm = by_id["prior_minutes"].reindex(df.index).fillna(0)
            lr = by_id[prior_col].reindex(df.index).fillna(0)
            prior = (lr * lm + prior * PRIOR_SHRINK_MINUTES) / (lm + PRIOR_SHRINK_MINUTES)
        raw = df[col] / df["minutes"].clip(lower=1) * 90
        df[f"{col}90"] = (raw * df["minutes"] + prior * SHRINK_MINUTES) / (df["minutes"] + SHRINK_MINUTES)
    return df


def project_v1(
    matches: pd.DataFrame,
    fixtures: pd.DataFrame,
    players: pd.DataFrame,
    target_gws: list[int],
    deadline: pd.Timestamp,
) -> pd.DataFrame:
    first = target_gws[0]
    past = matches[matches["gw"] < first]
    by_id = players.set_index("element")
    rates = player_rates(past, by_id)
    # Players with no row in the last two gameweeks have left the league (see baseline).
    last_seen = past.groupby("element")["gw"].max()
    rates = rates[last_seen.reindex(rates.index) >= past["gw"].max() - 1]

    teams = by_id["team"] if "team" in by_id else team_at(matches, first)
    teams = teams.reindex(rates.index).dropna().astype(int)
    pf = player_fixtures(fixtures, teams, target_gws).join(rates, on="element")

    ratings, mu_h, mu_a = team_ratings(fixtures, deadline)
    mu = (mu_h + mu_a) / 2
    rating = lambda team, col: ratings[col].reindex(team).fillna(1.0).to_numpy()
    venue_for = np.where(pf["home"], mu_h, mu_a)
    venue_against = np.where(pf["home"], mu_a, mu_h)
    # Goals the player's team scores / concedes here, relative to an average fixture
    # for that team (its own strength is already in the player's rates).
    att_mult = venue_for / mu * rating(pf["opponent"], "defence")
    own_def = rating(pf["team"], "defence")
    lam_against = venue_against * rating(pf["opponent"], "attack") * own_def
    def_mult = np.exp(-lam_against) / np.exp(-mu * own_def)
    att_mult = np.clip(att_mult, *MULT_CLIP)
    def_mult = np.clip(def_mult, *MULT_CLIP)

    per90 = pf["xmins"] / 90
    pf["xp"] = 2 * pf["p60"] + pf["psub"] + per90 * (pf["att90"] * att_mult + pf["dfn90"] * def_mult + pf["oth90"])
    out = pf.pivot_table(index="element", columns="gw", values="xp", aggfunc="sum")
    return out.reindex(index=rates.index, columns=target_gws).fillna(0.0)


project = None
