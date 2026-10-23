"""When the Advice workflow runs. Standard library only, so the checks run without
installing dependencies.

GitHub's ``schedule`` trigger is best effort: on 23.10.2026 the half-hourly cron
fired about five times in a day and missed the whole deadline window. So the
deadline runs no longer depend on cron firing at the right time:

* Any Advice run (cron, the daily desktop task, a manual run) calls ``kick``, which
  starts the *deadline timer* when a deadline is less than ``TIMER_LEAD_HOURS`` away.
* The timer (``deadline-timer.yml``) is a job that sleeps until each target time and
  then starts Advice itself: ``deadline`` 2 hours before the deadline (final advice
  and the email) and ``refresh`` 40 minutes before (fresh injury news; a second
  email only if the advice changed). A job may run for at most 6 hours, so a timer
  that has further to go sleeps 5.5 hours and starts a fresh timer.
* Cron stays as a backup: a scheduled check inside 0.25-3.5 hours of the deadline
  still runs the deadline advice if it has not happened.

Usage (``PYTHONPATH=src``)::

    python -m fpl_optimizer.schedule decide [--morning]   # mode for this trigger
    python -m fpl_optimizer.schedule kick                  # start the timer if due
    python -m fpl_optimizer.schedule timer                 # the timer itself
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ADVICE_DIR = Path(os.environ.get("FPL_ADVICE_DIR", ROOT / "history" / "advice"))
WINDOW_HOURS = (0.25, 3.5)  # cron backup: run the deadline advice inside this window
TARGETS = (("deadline", timedelta(hours=2)), ("refresh", timedelta(minutes=40)))
TOO_LATE = timedelta(minutes=12)  # a run needs a few minutes before the deadline
TIMER_LEAD_HOURS = 36
JOB_BUDGET = timedelta(hours=5, minutes=30)  # GitHub's job limit is 6 hours
FLAGS = {"deadline": "deadline_run", "refresh": "refresh_run"}


def next_deadline(events: list[dict], now: datetime) -> tuple[int, datetime] | None:
    upcoming = []
    for e in events:
        dl = datetime.fromisoformat(e["deadline_time"].replace("Z", "+00:00"))
        if dl > now:
            upcoming.append((e["id"], dl))
    return min(upcoming) if upcoming else None


def snapshot_flags(gw: int, advice_dir: Path = ADVICE_DIR) -> dict:
    path = advice_dir / f"gw{gw:02d}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def deadline_done(gw: int, advice_dir: Path = ADVICE_DIR) -> bool:
    return bool(snapshot_flags(gw, advice_dir).get("deadline_run", False))


def decide(events: list[dict], now: datetime, morning: bool, advice_dir: Path = ADVICE_DIR) -> str:
    nxt = next_deadline(events, now)
    if nxt:
        gw, dl = nxt
        hours = (dl - now).total_seconds() / 3600
        if WINDOW_HOURS[0] <= hours <= WINDOW_HOURS[1] and not deadline_done(gw, advice_dir):
            return "deadline"
    return "morning" if morning else "skip"


def pending_targets(deadline: datetime, now: datetime, done: dict) -> list[tuple[datetime, str]]:
    """The timer's remaining (time, mode) steps for one deadline, oldest first.

    A step whose time has passed still runs straight away if there is time left
    before the deadline; ``refresh`` makes a missed ``deadline`` step redundant only
    once it has run itself.
    """
    steps = []
    for mode, before in TARGETS:
        if done.get(FLAGS[mode]):
            continue
        at = max(deadline - before, now)
        if at <= deadline - TOO_LATE:
            steps.append((at, mode))
    # If the deadline step is late enough to collide with refresh, refresh covers it.
    if len(steps) == 2 and steps[0][0] >= steps[1][0]:
        steps = [(steps[0][0], "deadline")]
    return steps


def timer_plan(deadline: datetime, now: datetime, done: dict) -> tuple[list[tuple[datetime, str]], datetime | None]:
    """Steps this timer job runs itself, and when it hands over to a fresh timer
    (``None`` if it finishes within its time budget)."""
    end = now + JOB_BUDGET
    steps = pending_targets(deadline, now, done)
    mine = [s for s in steps if s[0] <= end]
    handover = end if len(mine) < len(steps) else None
    return mine, handover


def should_kick(events: list[dict], now: datetime, advice_dir: Path = ADVICE_DIR) -> bool:
    nxt = next_deadline(events, now)
    if not nxt:
        return False
    gw, dl = nxt
    return dl - now <= timedelta(hours=TIMER_LEAD_HOURS) and bool(pending_targets(dl, now, snapshot_flags(gw, advice_dir)))


def _events() -> list[dict]:
    req = urllib.request.Request("https://fantasy.premierleague.com/api/bootstrap-static/", headers={"User-Agent": "fpl-optimizer"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)["events"]


def dispatch(workflow: str, inputs: dict | None = None) -> None:
    """Start a workflow run through the GitHub API (needs ``actions: write``)."""
    repo = os.environ["GITHUB_REPOSITORY"]
    ref = os.environ.get("GITHUB_REF_NAME", "main")
    body = json.dumps({"ref": ref, "inputs": inputs or {}}).encode()
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req, timeout=60):
        pass
    print(f"Dispatched {workflow} {inputs or {}}")


def _sleep_until(at: datetime) -> None:
    wait = (at - datetime.now(timezone.utc)).total_seconds()
    if wait > 0:
        print(f"Sleeping {wait / 60:.0f} min until {at:%Y-%m-%d %H:%M} UTC")
        time.sleep(wait)


def run_timer() -> None:
    now = datetime.now(timezone.utc)
    nxt = next_deadline(_events(), now)
    if not nxt:
        print("No upcoming deadline")
        return
    gw, dl = nxt
    steps, handover = timer_plan(dl, now, snapshot_flags(gw))
    print(f"GW{gw} deadline {dl:%Y-%m-%d %H:%M} UTC; steps {[(f'{t:%H:%M}', m) for t, m in steps]}; handover {handover}")
    for at, mode in steps:
        _sleep_until(at)
        dispatch("advice.yml", {"mode": mode})
    if handover:
        _sleep_until(handover)
        dispatch("deadline-timer.yml")


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    d = sub.add_parser("decide")
    d.add_argument("--morning", action="store_true", help="this trigger is the daily morning schedule")
    sub.add_parser("kick")
    sub.add_parser("timer")
    args = parser.parse_args()

    if args.cmd == "timer":
        run_timer()
        return 0
    events = _events()
    now = datetime.now(timezone.utc)
    if args.cmd == "kick":
        if should_kick(events, now):
            dispatch("deadline-timer.yml")
        else:
            print("Deadline timer not needed now")
        return 0
    mode = decide(events, now, getattr(args, "morning", False))
    print(mode)
    if out := os.environ.get("GITHUB_OUTPUT"):
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"mode={mode}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
