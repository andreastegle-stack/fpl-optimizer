"""Availability: injury/suspension news and Andreas' overrides applied to projections.

The backtest has no historical injury news, so projection methods ignore it; the
live run multiplies projections by an availability factor per player per gameweek:

* FPL ``chance_of_playing_next_round`` for the next gameweek (no value and status
  ``a`` means fully available; ``u`` means gone for the season);
* "Expected back DD Mon" in the news: 0 until that date, then available;
* otherwise a doubt recovers linearly to 100% over three gameweeks;
* overrides from ``data/overrides.json`` win over all of the above.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

RECOVERY_GWS = 3
_BACK = re.compile(r"[Ee]xpected back (\d{1,2}) ([A-Z][a-z]{2})")
_MONTHS = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def load_overrides(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("overrides", [])


def expected_back(news: str, now: datetime) -> datetime | None:
    m = _BACK.search(news or "")
    if not m or m.group(2) not in _MONTHS:
        return None
    day, month = int(m.group(1)), _MONTHS[m.group(2)]
    year = now.year + (1 if month < now.month - 1 else 0)
    try:
        return datetime(year, month, day, tzinfo=now.tzinfo)
    except ValueError:
        return None


def availability(players: pd.DataFrame, events: list[dict], gws: list[int], now: datetime, overrides: list[dict]) -> pd.DataFrame:
    """Factor in [0, 1] per player (rows) per gameweek (columns)."""
    deadline = {e["id"]: datetime.fromisoformat(e["deadline_time"].replace("Z", "+00:00")) for e in events}
    rows = {}
    for p in players.itertuples():
        chance = p.chance_of_playing_next_round
        if p.status == "a" and (chance is None or pd.isna(chance)):
            rows[p.element] = [1.0] * len(gws)
            continue
        if p.status == "u":
            rows[p.element] = [0.0] * len(gws)
            continue
        c = (0.0 if p.status in ("i", "s", "n") else 0.5) if chance is None or pd.isna(chance) else float(chance) / 100
        back = expected_back(p.news, now)
        factors = []
        for h, gw in enumerate(gws):
            if back is not None:
                factors.append(1.0 if deadline.get(gw, now) >= back else 0.0)
            else:
                factors.append(min(1.0, c + (1 - c) * h / RECOVERY_GWS))
        rows[p.element] = factors
    out = pd.DataFrame.from_dict(rows, orient="index", columns=gws)

    for o in overrides:
        first = o.get("from_gw", gws[0])
        last = o.get("until_gw", gws[-1])
        for gw in gws:
            if first <= gw <= last and o["element"] in out.index:
                out.loc[o["element"], gw] = float(o["availability"])
    return out
