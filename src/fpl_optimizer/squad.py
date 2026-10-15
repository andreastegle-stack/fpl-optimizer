"""Squad state for one entry, as of the last deadline plus recorded pending transfers.

There is no FPL login, so the squad is read from the public picks of the last
gameweek whose deadline has passed. Transfers Andreas made since then are told to
Claude in chat and recorded in ``data/pending_transfers.json``; they are applied on
top. Once the next deadline passes, the picks endpoint reflects them and the
pending entries for that gameweek are ignored automatically.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .api import FplApi

CHIP_LABELS = {"wildcard": "Wildcard", "freehit": "Free Hit", "bboost": "Bench Boost", "3xc": "Triple Captain"}


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def last_deadline_event(events: list[dict], now: datetime) -> int:
    """Id of the latest gameweek whose deadline has passed (0 before GW1)."""
    passed = [e["id"] for e in events if parse_time(e["deadline_time"]) <= now]
    return max(passed, default=0)


def next_event(events: list[dict], now: datetime) -> dict | None:
    upcoming = [e for e in events if parse_time(e["deadline_time"]) > now]
    return min(upcoming, key=lambda e: e["id"]) if upcoming else None


def selling_price(purchase: int, now_cost: int) -> int:
    """FPL sell price in tenths: you keep half the profit, rounded down; losses are taken in full."""
    if now_cost > purchase:
        return purchase + (now_cost - purchase) // 2
    return now_cost


def free_transfers_for(history: list[dict], chips: list[dict], event: int, max_ft: int) -> int:
    """Free transfers available at ``event``, replayed from the entry history.

    1 FT from GW2, +1 per gameweek, capped at ``max_ft``. A wildcard or free hit keeps
    the banked count unchanged (no +1 that week); transfers beyond the FTs are hits.
    """
    by_event = {h["event"]: h for h in history}
    chip_by_event = {c["event"]: c["name"] for c in chips}
    ft = 1  # available at GW2
    for gw in range(2, event):
        if chip_by_event.get(gw) in ("wildcard", "freehit"):
            continue
        made = by_event.get(gw, {}).get("event_transfers", 0)
        ft = min(max(ft - made, 0) + 1, max_ft)
    return ft


@dataclass
class ChipInfo:
    name: str
    start_event: int
    stop_event: int
    used_event: int | None

    @property
    def label(self) -> str:
        return CHIP_LABELS.get(self.name, self.name)


@dataclass
class SquadState:
    entry_id: int
    entry_name: str
    last_deadline: int
    next_event: int
    players: list[int]
    purchase_price: dict[int, int]
    selling_price: dict[int, int]
    bank: int  # tenths of £m
    free_transfers: int  # available for next_event, after pending transfers
    pending_hit_cost: int
    pending: list[dict] = field(default_factory=list)
    ignored_pending: list[dict] = field(default_factory=list)
    chips: list[ChipInfo] = field(default_factory=list)

    def available_chips(self, event: int) -> list[ChipInfo]:
        return [c for c in self.chips if c.used_event is None and c.start_event <= event <= c.stop_event]


def load_pending(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("transfers", [])


def build_squad_state(
    api: FplApi,
    entry_id: int,
    pending_path: Path,
    now: datetime | None = None,
    bootstrap: dict | None = None,
) -> SquadState:
    now = now or datetime.now(timezone.utc)
    bootstrap = bootstrap or api.bootstrap()
    events = bootstrap["events"]
    elements = {e["id"]: e for e in bootstrap["elements"]}
    last = last_deadline_event(events, now)
    nxt = next_event(events, now)
    if last == 0 or nxt is None:
        raise RuntimeError("Season not in progress: no passed deadline or no upcoming gameweek")

    entry = api.entry(entry_id)
    history = api.entry_history(entry_id)
    picks = api.picks(entry_id, last)
    transfers = api.transfers(entry_id)

    squad = [p["element"] for p in picks["picks"]]
    # A free hit squad reverts after the gameweek; the real squad is the one before it.
    if picks.get("active_chip") == "freehit" and last > 1:
        squad = [p["element"] for p in api.picks(entry_id, last - 1)["picks"]]

    fh_events = {c["event"] for c in history["chips"] if c["name"] == "freehit"}
    purchase: dict[int, int] = {}
    for t in sorted(transfers, key=lambda t: t["time"]):
        if t["event"] in fh_events:
            continue
        purchase[t["element_in"]] = t["element_in_cost"]
    for p in squad:
        if p not in purchase:
            el = elements[p]
            purchase[p] = el["now_cost"] - el["cost_change_start"]

    bank = next((h["bank"] for h in history["current"] if h["event"] == last), entry.get("last_deadline_bank", 0))
    max_ft = 1 + bootstrap["game_settings"].get("max_extra_free_transfers", 4)
    ft = free_transfers_for(history["current"], history["chips"], nxt["id"], max_ft)

    pending, ignored = [], []
    for t in load_pending(pending_path):
        (pending if t["event"] > last else ignored).append(t)
    for t in pending:
        if t["event"] != nxt["id"]:
            raise ValueError(f"Pending transfer for GW{t['event']} but the next deadline is GW{nxt['id']}: {t}")
        if t["out"] not in squad:
            raise ValueError(f"Pending transfer sells {t['out']} who is not in the squad: {t}")
        if t["in"] in squad:
            raise ValueError(f"Pending transfer buys {t['in']} who is already in the squad: {t}")
        out_sell = selling_price(purchase[t["out"]], elements[t["out"]]["now_cost"])
        in_cost = t.get("in_cost", elements[t["in"]]["now_cost"])
        bank += out_sell - in_cost
        squad = [t["in"] if p == t["out"] else p for p in squad]
        purchase[t["in"]] = in_cost
    if bank < 0:
        raise ValueError(f"Pending transfers leave a negative bank ({bank / 10:.1f}m); check data/pending_transfers.json")

    hits = max(len(pending) - ft, 0)
    ft_left = max(ft - len(pending), 0)

    chips = []
    for c in bootstrap["chips"]:
        used = next(
            (h["event"] for h in history["chips"] if h["name"] == c["name"] and c["start_event"] <= h["event"] <= c["stop_event"]),
            None,
        )
        chips.append(ChipInfo(c["name"], c["start_event"], c["stop_event"], used))

    return SquadState(
        entry_id=entry_id,
        entry_name=entry["name"],
        last_deadline=last,
        next_event=nxt["id"],
        players=squad,
        purchase_price={p: purchase[p] for p in squad},
        selling_price={p: selling_price(purchase[p], elements[p]["now_cost"]) for p in squad},
        bank=bank,
        free_transfers=ft_left,
        pending_hit_cost=4 * hits,
        pending=pending,
        ignored_pending=ignored,
        chips=chips,
    )
