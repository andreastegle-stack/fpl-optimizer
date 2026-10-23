"""Command line entry point.

    python -m fpl_optimizer run --mode morning|deadline|refresh [--site site]
    python -m fpl_optimizer test-email

``run`` fetches data, plans, renders the report into ``--site`` and saves the
gameweek's advice snapshot in ``history/advice`` (see ``snapshot_policy``). The
deadline run sends the email; the refresh 40 minutes before the deadline sends a
second one only if the advice changed.
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from . import notify, report, review, scorecard
from .api import FplApi
from .config import ADVICE_DIR, REVIEW_DIR, ROOT, load_settings
from .plan import make_advice

REPO_URL = "https://github.com/andreastegle-stack/fpl-optimizer"


def latest_review(api: FplApi, entry_id: int, bootstrap: dict) -> tuple[dict | None, dict | None]:
    """The review of the last final gameweek (built and stored once), and the live
    status of a gameweek that has started but is not final yet."""
    reviews = review.load_reviews(REVIEW_DIR)
    gw = review.final_gameweek(bootstrap["events"])
    latest = None
    if gw is not None:
        path = REVIEW_DIR / f"gw{gw:02d}.json"
        if path.exists():
            latest = json.loads(path.read_text(encoding="utf-8"))
        else:
            advice = scorecard.load_snapshot(ADVICE_DIR, gw)
            latest = review.build(api, entry_id, gw, bootstrap, advice, [r for r in reviews if r["gw"] < gw])
            REVIEW_DIR.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(latest, indent=1, ensure_ascii=False), encoding="utf-8")
    return latest, review.live_status(api, entry_id, bootstrap)


def signature(advice: dict) -> tuple:
    """What Andreas acts on in the first week: moves, chip, captaincy and the XI."""
    w = advice["recommended"]["weeks"][0]
    return (tuple(sorted(w["buys"])), tuple(sorted(w["sells"])), w["chip"], w["captain"], w["vice"], tuple(sorted(w["lineup"])))


def snapshot_policy(mode: str, previous: dict, advice: dict) -> tuple[bool, str | None]:
    """(save this advice as the gameweek's snapshot?, email to send: None/"advice"/"changed").

    The snapshot is what the scorecard and the review judge, so it should be the
    latest advice before the deadline: morning runs keep it current until a deadline
    run has saved the final one. If the deadline run was missed, the refresh sends
    the normal email instead.
    """
    if mode == "morning":
        return not previous.get("deadline_run"), None
    if mode == "deadline":
        return True, None if previous.get("emailed") else "advice"
    if not previous.get("emailed"):
        return True, "advice"
    return True, "changed" if signature(previous) != signature(advice) else None


def run(mode: str, site: Path, backtest_path: Path) -> dict:
    settings = load_settings()
    api = FplApi()
    last_review, live = latest_review(api, settings["entry_id"], api.bootstrap())
    advice = json.loads(json.dumps(make_advice(api, settings, datetime.now(timezone.utc)), default=str))
    advice["mode"] = mode

    snapshot = ADVICE_DIR / f"gw{advice['gw']:02d}.json"
    previous = json.loads(snapshot.read_text(encoding="utf-8")) if snapshot.exists() else {}
    save, email = snapshot_policy(mode, previous, advice)
    if save:
        advice["deadline_run"] = mode in ("deadline", "refresh") or previous.get("deadline_run", False)
        advice["refresh_run"] = mode == "refresh"
        advice["emailed"] = previous.get("emailed", False)
        if email:
            sent = notify.send(advice, settings["report_url"], last_review, changed=email == "changed")
            advice["emailed"] = advice["emailed"] or sent
        ADVICE_DIR.mkdir(parents=True, exist_ok=True)
        snapshot.write_text(json.dumps(advice, indent=1, ensure_ascii=False), encoding="utf-8")

    cards = scorecard.build(api, settings["entry_id"], api.bootstrap(), ADVICE_DIR)
    backtest = json.loads(backtest_path.read_text(encoding="utf-8")) if backtest_path.exists() else None
    report.render(advice, cards, backtest, site, REPO_URL, last_review, live)
    (site / "advice.json").write_text(json.dumps(advice, indent=1, ensure_ascii=False), encoding="utf-8")
    if backtest_path.exists():
        shutil.copy(backtest_path, site / "backtest.json")
    (site / ".nojekyll").write_text("", encoding="utf-8")
    return advice


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fpl-optimizer")
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="plan, render the report and save the snapshot")
    r.add_argument("--mode", choices=["morning", "deadline", "refresh"], default="morning")
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
