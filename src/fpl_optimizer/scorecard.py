"""Weekly scorecard: the advice vs what Andreas did vs the gameweek average.

The advice for a gameweek is the last snapshot saved before its deadline
(``history/advice/gwNN.json``). After the gameweek, the advised starting XI is
scored with the real points (no automatic substitutions, advised hits deducted).
"""

from __future__ import annotations

import json
from pathlib import Path

from .api import FplApi


def load_snapshot(advice_dir: Path, gw: int) -> dict | None:
    path = advice_dir / f"gw{gw:02d}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def advised_points(advice: dict, live_points: dict[int, int]) -> int:
    week = advice["recommended"]["weeks"][0]
    scoring = list(week["lineup"]) + (list(week["bench"]) if week["chip"] == "bboost" else [])
    pts = sum(live_points.get(p, 0) for p in scoring)
    pts += live_points.get(week["captain"], 0) * (2 if week["chip"] == "3xc" else 1)
    return pts - 4 * week["hits"]


def followed(advice: dict, actual_in: set[int], actual_out: set[int], captain: int | None) -> str:
    week = advice["recommended"]["weeks"][0]
    same_moves = set(week["buys"]) == actual_in and set(week["sells"]) == actual_out
    same_cap = captain == week["captain"]
    if same_moves and same_cap:
        return "ja"
    if same_moves or same_cap or set(week["buys"]) & actual_in:
        return "delvis"
    return "nei"


def build(api: FplApi, entry_id: int, bootstrap: dict, advice_dir: Path) -> list[dict]:
    history = api.entry_history(entry_id)["current"]
    transfers = api.transfers(entry_id)
    events = {e["id"]: e for e in bootstrap["events"]}
    rows = []
    for h in history:
        gw = h["event"]
        ev = events.get(gw, {})
        if not ev.get("finished") and not ev.get("is_current"):
            continue
        row = {
            "gw": gw,
            "you": h["points"] - h["event_transfers_cost"],
            "average": ev.get("average_entry_score"),
            "finished": bool(ev.get("finished")),
            "advice_xp": None,
            "advice_pts": None,
            "followed": None,
        }
        advice = load_snapshot(advice_dir, gw)
        if advice:
            live = {e["id"]: e["stats"]["total_points"] for e in api.event_live(gw)["elements"]}
            row["advice_xp"] = advice["recommended"]["weeks"][0]["xp"]
            row["advice_pts"] = advised_points(advice, live)
            picks = api.picks(entry_id, gw)
            captain = next((p["element"] for p in picks["picks"] if p["is_captain"]), None)
            # Transfers already made (pending) when the advice was given are not part of it.
            pending = {(t["in"], t["out"]) for t in advice["state"].get("pending", [])}
            moves = [t for t in transfers if t["event"] == gw and (t["element_in"], t["element_out"]) not in pending]
            row["followed"] = followed(advice, {t["element_in"] for t in moves}, {t["element_out"] for t in moves}, captain)
        rows.append(row)
    return rows
