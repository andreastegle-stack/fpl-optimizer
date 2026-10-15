"""Backtest projection methods on a full past season.

For every gameweek ``t`` in the evaluation range each method gets only the matches
played before ``t``'s deadline and projects ``t..t+5``. We then compare with what
actually happened.

Scored on the *pool*: players with at least one minute in their last five fixtures
before the deadline (the players a manager would realistically consider). Every
method is scored on the same pool.

Metrics
* ``rmse_next`` / ``mae_next``: error on next-gameweek points (lineup, captain).
* ``rmse_6gw``: error on total points over the next six gameweeks (transfers).
* ``spearman_next``: mean per-gameweek rank correlation (does it order players right).
* ``captain_pts``: season total of the actual points scored by each method's top pick.
* ``top20_pts``: mean actual points of each method's 20 highest projections.

``fpl_xp`` (FPL's own published expected points, kept in the historical data) is
shown for information only. In 2026/27 FPL's ``ep_next`` is just form, so the gate
compares against ``form``.

Usage::

    python -m fpl_optimizer.backtest --season 2025-26 --out results/backtest.json
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from .history import download_season, load_season, previous_season, season_rates, with_prior
from .projections import methods as projection_methods

HORIZON = 6
FIRST_EVAL_GW = 5  # every method gets four gameweeks of history first
LOWER_IS_BETTER = {"rmse_next", "mae_next", "rmse_6gw"}


def _spearman(a: pd.Series, b: pd.Series) -> float:
    if a.nunique() < 2 or b.nunique() < 2:
        return float("nan")
    return float(np.corrcoef(a.rank(), b.rank())[0, 1])


def load_backtest_season(season: str, data_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Matches, fixtures and players for ``season``, with last season's rates attached."""
    matches, fixtures, players = load_season(download_season(season, data_dir))
    try:
        rates = season_rates(download_season(previous_season(season), data_dir))
    except Exception:  # the very first season in the dataset has no predecessor
        rates = None
    return matches, fixtures, with_prior(players, rates)


def deadlines(fixtures: pd.DataFrame) -> dict[int, pd.Timestamp]:
    """Deadline approximated as 90 minutes before the first kickoff of each gameweek."""
    return (fixtures.groupby("gw")["kickoff"].min() - pd.Timedelta(minutes=90)).to_dict()


def run_backtest(matches: pd.DataFrame, fixtures: pd.DataFrame, players: pd.DataFrame, include_model: bool = True) -> dict:
    last_gw = int(fixtures["gw"].max())
    actual = matches.groupby(["element", "gw"])["points"].sum()
    dl = deadlines(fixtures)
    methods = projection_methods(include_model)
    xp_ref = matches.groupby(["element", "gw"])["xp"].first() if "xp" in matches else None

    per_gw: dict[str, list[dict]] = {name: [] for name in [*methods, *(["fpl_xp"] if xp_ref is not None else [])]}
    for t in range(FIRST_EVAL_GW, last_gw + 1):
        gws = list(range(t, min(t + HORIZON, last_gw + 1)))
        past = matches[matches["gw"] < t]
        recent = past.groupby("element").tail(5)
        pool = recent.groupby("element")["minutes"].sum()
        pool = pool[pool > 0].index

        act_next = actual.xs(t, level="gw").reindex(pool).fillna(0.0) if t in actual.index.get_level_values("gw") else pd.Series(0.0, index=pool)
        full_horizon = len(gws) == HORIZON
        act_6 = None
        if full_horizon:
            act_6 = actual[actual.index.get_level_values("gw").isin(gws)].groupby(level="element").sum().reindex(pool).fillna(0.0)

        preds: dict[str, pd.DataFrame] = {name: fn(matches, fixtures, players, gws, dl[t]) for name, fn in methods.items()}
        for name, proj in preds.items():
            proj = proj.reindex(pool).fillna(0.0)
            per_gw[name].append(_score(proj[t], act_next, proj[gws].sum(axis=1) if full_horizon else None, act_6))
        if xp_ref is not None:
            xp_next = xp_ref.xs(t, level="gw").reindex(pool).fillna(0.0) if t in xp_ref.index.get_level_values("gw") else pd.Series(0.0, index=pool)
            per_gw["fpl_xp"].append(_score(xp_next, act_next, None, None))

    return {name: _aggregate(rows) for name, rows in per_gw.items()} | {"_eval_gws": [FIRST_EVAL_GW, last_gw]}


def _score(pred_next: pd.Series, act_next: pd.Series, pred_6: pd.Series | None, act_6: pd.Series | None) -> dict:
    err = pred_next - act_next
    top = pred_next.sort_values(ascending=False)
    row = {
        "sse_next": float((err**2).sum()),
        "sae_next": float(err.abs().sum()),
        "n_next": int(len(err)),
        "spearman_next": _spearman(pred_next, act_next),
        "captain_pts": float(act_next[top.index[0]]) if len(top) else 0.0,
        "top20_pts": float(act_next[top.index[:20]].mean()) if len(top) else 0.0,
    }
    if pred_6 is not None and act_6 is not None:
        row |= {"sse_6gw": float(((pred_6 - act_6) ** 2).sum()), "n_6gw": int(len(act_6))}
    return row


def _aggregate(rows: list[dict]) -> dict:
    n = sum(r["n_next"] for r in rows)
    n6 = sum(r.get("n_6gw", 0) for r in rows)
    return {
        "rmse_next": math.sqrt(sum(r["sse_next"] for r in rows) / n),
        "mae_next": sum(r["sae_next"] for r in rows) / n,
        "rmse_6gw": math.sqrt(sum(r.get("sse_6gw", 0) for r in rows) / n6) if n6 else None,
        "spearman_next": float(np.nanmean([r["spearman_next"] for r in rows])),
        "captain_pts": sum(r["captain_pts"] for r in rows),
        "top20_pts": float(np.mean([r["top20_pts"] for r in rows])),
        "gameweeks": len(rows),
    }


def to_markdown(results: dict, season: str) -> str:
    first, last = results["_eval_gws"]
    names = [k for k in results if not k.startswith("_")]
    cols = [
        ("rmse_next", "RMSE next GW (lower is better)"),
        ("mae_next", "MAE next GW"),
        ("rmse_6gw", "RMSE 6-GW total"),
        ("spearman_next", "Rank corr. next GW"),
        ("captain_pts", "Captain pick, season pts"),
        ("top20_pts", "Top-20 picks, mean pts"),
    ]
    lines = [f"Backtest {season}, GW{first}-{last}", "", "| Metric | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for key, label in cols:
        vals = []
        for name in names:
            v = results[name].get(key)
            vals.append("n/a" if v is None else f"{v:.0f}" if key == "captain_pts" else f"{v:.3f}")
        lines.append(f"| {label} | " + " | ".join(vals) + " |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--season", default="2025-26")
    parser.add_argument("--data", default=".cache/vaastav")
    parser.add_argument("--out", default="results/backtest.json")
    args = parser.parse_args(argv)

    matches, fixtures, players = load_backtest_season(args.season, Path(args.data))
    results = run_backtest(matches, fixtures, players)
    results["_season"] = args.season
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(to_markdown(results, args.season))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
