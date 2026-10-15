"""Model gate (CI): our model may only drive advice once it beats both references.

Rules (CLAUDE.md, "Model gate"):
* If a model is registered (``projections.model.project`` is set), it must beat
  both FPL ``form`` and the ``baseline`` on next-gameweek RMSE *and* six-gameweek
  RMSE over a full past season. Otherwise the check fails.
* ``projection_source = "model"`` in ``config/settings.json`` is refused unless the
  model passes.
* With no model yet the check passes and reports baseline vs form.

Writes the result table to ``$GITHUB_STEP_SUMMARY`` when running in Actions.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .backtest import load_backtest_season, run_backtest, to_markdown
from .config import CACHE_DIR, load_settings

GATE_METRICS = ("rmse_next", "rmse_6gw")
REFERENCES = ("form", "baseline")


def evaluate(results: dict, projection_source: str) -> tuple[bool, list[str]]:
    notes: list[str] = []
    ok = True
    if "model" in results:
        for metric in GATE_METRICS:
            for ref in REFERENCES:
                m, r = results["model"][metric], results[ref][metric]
                if m < r:
                    notes.append(f"PASS model {metric} {m:.3f} < {ref} {r:.3f}")
                else:
                    ok = False
                    notes.append(f"FAIL model {metric} {m:.3f} does not beat {ref} {r:.3f}")
    else:
        notes.append("No model registered yet: advice uses the baseline.")
        if projection_source == "model":
            ok = False
            notes.append("FAIL projection_source is 'model' but no model is registered.")
    if projection_source == "model" and "model" in results and not ok:
        notes.append("FAIL projection_source is 'model' but the model does not pass the gate.")
    return ok, notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backtest projection methods and enforce the model gate.")
    parser.add_argument("--out", default="results/backtest.json")
    args = parser.parse_args(argv)

    settings = load_settings()
    season = settings["backtest_season"]
    matches, fixtures, players = load_backtest_season(season, CACHE_DIR / "vaastav")
    results = run_backtest(matches, fixtures, players)
    ok, notes = evaluate(results, settings["projection_source"])
    results["_season"] = season
    results["_gate"] = {"passed": ok, "notes": notes, "projection_source": settings["projection_source"]}

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")

    report = to_markdown(results, season) + "\n\n" + "\n".join(f"- {n}" for n in notes)
    report += "\n\n_fpl_xp is FPL's own published expected points from the historical data, for information only._\n"
    print(report)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("## Model gate\n\n" + report)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
