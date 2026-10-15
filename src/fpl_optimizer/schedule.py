"""Decide what a scheduled GitHub Actions run should do. Standard library only, so
the frequent check runs without installing dependencies.

* ``deadline``: 0.75-3.5 hours before the next deadline, unless that gameweek's
  deadline run already happened (its snapshot says so).
* ``morning``: the daily run after the 00:00 UK price change.
* ``skip``: anything else.

Usage: ``PYTHONPATH=src python -m fpl_optimizer.schedule [--morning]``. Prints the
mode and writes ``mode=...`` to ``$GITHUB_OUTPUT`` when set.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ADVICE_DIR = Path(os.environ.get("FPL_ADVICE_DIR", ROOT / "history" / "advice"))
WINDOW_HOURS = (0.75, 3.5)


def next_deadline(events: list[dict], now: datetime) -> tuple[int, datetime] | None:
    upcoming = []
    for e in events:
        dl = datetime.fromisoformat(e["deadline_time"].replace("Z", "+00:00"))
        if dl > now:
            upcoming.append((e["id"], dl))
    return min(upcoming) if upcoming else None


def deadline_done(gw: int, advice_dir: Path = ADVICE_DIR) -> bool:
    path = advice_dir / f"gw{gw:02d}.json"
    return path.exists() and json.loads(path.read_text(encoding="utf-8")).get("deadline_run", False)


def decide(events: list[dict], now: datetime, morning: bool, advice_dir: Path = ADVICE_DIR) -> str:
    nxt = next_deadline(events, now)
    if nxt:
        gw, dl = nxt
        hours = (dl - now).total_seconds() / 3600
        if WINDOW_HOURS[0] <= hours <= WINDOW_HOURS[1] and not deadline_done(gw, advice_dir):
            return "deadline"
    return "morning" if morning else "skip"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--morning", action="store_true", help="this trigger is the daily morning schedule")
    args = parser.parse_args()
    req = urllib.request.Request("https://fantasy.premierleague.com/api/bootstrap-static/", headers={"User-Agent": "fpl-optimizer"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        events = json.load(resp)["events"]
    mode = decide(events, datetime.now(timezone.utc), args.morning)
    print(mode)
    if out := os.environ.get("GITHUB_OUTPUT"):
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"mode={mode}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
