"""Command line entry point.

    python -m fpl_optimizer run --mode morning|deadline [--site site]
    python -m fpl_optimizer test-email

``run`` fetches data, plans, renders the report into ``--site`` and saves the
gameweek's advice snapshot in ``history/advice`` (always on a deadline run, and on a
morning run only if none exists yet). A deadline run also sends the email once.
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from . import notify, report, scorecard
from .api import FplApi
from .config import ADVICE_DIR, ROOT, load_settings
from .plan import make_advice

REPO_URL = "https://github.com/andreastegle-stack/fpl-optimizer"


def run(mode: str, site: Path, backtest_path: Path) -> dict:
    settings = load_settings()
    api = FplApi()
    advice = json.loads(json.dumps(make_advice(api, settings, datetime.now(timezone.utc)), default=str))
    advice["mode"] = mode

    snapshot = ADVICE_DIR / f"gw{advice['gw']:02d}.json"
    previous = json.loads(snapshot.read_text(encoding="utf-8")) if snapshot.exists() else {}
    if mode == "deadline" or not previous:
        advice["deadline_run"] = mode == "deadline"
        advice["emailed"] = previous.get("emailed", False)
        if mode == "deadline" and not advice["emailed"]:
            advice["emailed"] = notify.send(advice, settings["report_url"])
        ADVICE_DIR.mkdir(parents=True, exist_ok=True)
        snapshot.write_text(json.dumps(advice, indent=1, ensure_ascii=False), encoding="utf-8")

    cards = scorecard.build(api, settings["entry_id"], api.bootstrap(), ADVICE_DIR)
    backtest = json.loads(backtest_path.read_text(encoding="utf-8")) if backtest_path.exists() else None
    report.render(advice, cards, backtest, site, REPO_URL)
    (site / "advice.json").write_text(json.dumps(advice, indent=1, ensure_ascii=False), encoding="utf-8")
    if backtest_path.exists():
        shutil.copy(backtest_path, site / "backtest.json")
    (site / ".nojekyll").write_text("", encoding="utf-8")
    return advice


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fpl-optimizer")
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="plan, render the report and save the snapshot")
    r.add_argument("--mode", choices=["morning", "deadline"], default="morning")
    r.add_argument("--site", default=str(ROOT / "site"))
    r.add_argument("--backtest", default=str(ROOT / "results" / "backtest.json"))
    sub.add_parser("test-email", help="send the latest advice as a test email")
    args = parser.parse_args(argv)

    if args.cmd == "run":
        advice = run(args.mode, Path(args.site), Path(args.backtest))
        week = advice["recommended"]["weeks"][0]
        print(f"GW{advice['gw']}: {len(week['sells'])} transfer(s), chip {week['chip']}, captain {week['captain']}")
        return 0
    if args.cmd == "test-email":
        latest = sorted(ADVICE_DIR.glob("gw*.json"))
        if not latest:
            print("No advice snapshot yet; run first.")
            return 1
        advice = json.loads(latest[-1].read_text(encoding="utf-8"))
        return 0 if notify.send(advice, load_settings()["report_url"]) else 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
