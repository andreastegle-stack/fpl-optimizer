"""FPL ``form`` as a projection: mean points per team fixture over the last 30 days.

In 2026/27 FPL's own ``ep_next`` is just ``form``, so this is the reference our
model has to beat. Every future gameweek gets ``form`` per fixture (two in a double,
zero in a blank). Reconstructed from match history so it can be backtested.
"""

from __future__ import annotations

import pandas as pd

from ..history import player_fixtures, team_at

FORM_DAYS = 30


def project(
    matches: pd.DataFrame,
    fixtures: pd.DataFrame,
    players: pd.DataFrame,
    target_gws: list[int],
    deadline: pd.Timestamp,
) -> pd.DataFrame:
    first = target_gws[0]
    past = matches[(matches["gw"] < first) & (matches["kickoff"] < deadline)]
    window = past[past["kickoff"] >= deadline - pd.Timedelta(days=FORM_DAYS)]
    form = window.groupby("element")["points"].mean()
    form = form.reindex(past["element"].unique()).fillna(0.0)

    teams = players.set_index("element")["team"] if "team" in players else team_at(matches, first)
    teams = teams.reindex(form.index).dropna().astype(int)
    pf = player_fixtures(fixtures, teams, target_gws)
    pf["xp"] = pf["element"].map(form)
    out = pf.pivot_table(index="element", columns="gw", values="xp", aggfunc="sum")
    return out.reindex(index=form.index, columns=target_gws).fillna(0.0)
