"""Review of the previous gameweek: result, decisions, model vs reality, lessons.

Decisions are judged on process, not luck: each one is compared with what the
model expected *before the deadline* (the advice snapshot for that gameweek), and
the outcome is shown next to it. Big misses per player are split into variance
(chances not taken, clean sheet lost) and the model being wrong (minutes, role).

A review is built once the gameweek is final (FPL ``finished`` and
``data_checked``) and stored per gameweek next to the advice snapshots, so the
season's reviews accumulate. ``compute`` is pure (all inputs passed in) so the
logic is testable without the API.
"""

from __future__ import annotations

import json
from pathlib import Path

from .optimizer import PLAY_MIN, next_gw_lineup

BIG_MISS = 3.0  # points between projection and outcome before we explain it
CHIP_NO = {"wildcard": "Wildcard", "freehit": "Free Hit", "bboost": "Bench Boost", "3xc": "Triple Captain"}
POS = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}


def autosub(xi: list[int], bench: list[int], minutes: dict[int, int], ptype: dict[int, int]) -> list[int]:
    """FPL automatic substitutions: a starter with 0 minutes is replaced by the first
    bench player (in order) who played and keeps the formation valid."""
    final = list(xi)
    used: set[int] = set()
    for p in xi:
        if minutes.get(p, 0) > 0:
            continue
        for b in bench:
            if b in used or minutes.get(b, 0) == 0:
                continue
            if (ptype[p] == 1) != (ptype[b] == 1):
                continue  # keeper only for keeper
            trial = [b if x == p else x for x in final]
            counts = {t: sum(1 for x in trial if ptype[x] == t) for t in PLAY_MIN}
            if all(counts[t] >= PLAY_MIN[t] for t in PLAY_MIN):
                final = trial
                used.add(b)
                break
    return final


def _xp(advice: dict | None, p: int, gw: int) -> float | None:
    if not advice:
        return None
    info = advice.get("players", {}).get(str(p))
    if not info:
        return None
    v = info["xp"].get(str(gw))
    return None if v is None else float(v)


def _xp_horizon(advice: dict | None, p: int) -> float | None:
    if not advice or str(p) not in advice.get("players", {}):
        return None
    return float(sum(advice["players"][str(p)]["xp"].values()))


def explain_player(xp: float | None, s: dict, ptype: int) -> tuple[str | None, str | None]:
    """(cause, kind) for a big miss. kind is "flaks", "uflaks" (variance) or "modell"."""
    if xp is None:
        return None, None
    pts = s["total_points"]
    diff = pts - xp
    minutes = s["minutes"]
    ga = s["goals_scored"] + s["assists"]
    xga = float(s.get("expected_goals", 0) or 0) + float(s.get("expected_assists", 0) or 0)
    if minutes == 0 and xp >= 1.0:
        return "Spilte ikke", "modell"
    if abs(diff) < BIG_MISS:
        return None, None
    if diff < 0:
        if minutes < 60 and xp >= 2.5:
            return f"Bare {minutes} minutter", "modell"
        if xga >= 0.5 and ga == 0:
            return f"Fikk sjanser (xG+xA {xga:.1f}), men ingen mål eller målgivende", "uflaks"
        if ptype in (1, 2) and s.get("goals_conceded", 0) >= 2:
            return f"Laget slapp inn {s['goals_conceded']} mål", "uflaks"
        return "Lite involvert i kampen", "uflaks"
    if ga > xga + 0.5:
        return f"{ga} mål/målgivende på xG+xA {xga:.1f}", "flaks"
    if s.get("clean_sheets") and ptype in (1, 2):
        return "Holdt nullen", "flaks"
    if s.get("bonus", 0) >= 2:
        return f"{s['bonus']} bonuspoeng", "flaks"
    return "Bedre kamp enn ventet", "flaks"


def compute(
    gw: int,
    picks: dict,
    prev_overall_rank: int | None,
    live: dict[int, dict],
    transfers: list[dict],
    advice: dict | None,
    event: dict,
    meta: dict[int, dict],
    top10k_total: int | None = None,
) -> dict:
    """Build the review dict. ``meta``: element id -> {name, team, element_type}."""
    hist = picks["entry_history"]
    chip = picks.get("active_chip")
    ptype = {p: meta[p]["element_type"] for p in meta}
    pts = {p: live.get(p, {}).get("total_points", 0) for p in meta}
    minutes = {p: live.get(p, {}).get("minutes", 0) for p in meta}
    order = sorted(picks["picks"], key=lambda x: x["position"])
    starters = [x["element"] for x in order[:11]]
    bench = [x["element"] for x in order[11:]]
    captain = next(x["element"] for x in order if x["is_captain"])
    cap_mult = 3 if chip == "3xc" else 2
    subs_in = [s["element_in"] for s in picks.get("automatic_subs", [])]
    subs_out = [s["element_out"] for s in picks.get("automatic_subs", [])]
    scorers = [p for p in starters if p not in subs_out] + subs_in
    if chip == "bboost":
        scorers = starters + bench
    name = lambda p: meta[p]["name"]

    result = {
        "points": hist["points"] - hist["event_transfers_cost"],
        "gross": hist["points"],
        "hits": hist["event_transfers_cost"] // 4,
        "average": event.get("average_entry_score"),
        "highest": event.get("highest_score"),
        "gw_rank": hist.get("rank"),
        "overall_rank": hist.get("overall_rank"),
        "prev_overall_rank": prev_overall_rank,
        "total": hist.get("total_points"),
        "top10k_total": top10k_total,
        "chip": CHIP_NO.get(chip) if chip else None,
        "bench_points": hist.get("points_on_bench"),
    }

    # Transfers
    tr = []
    for t in transfers:
        if t["event"] != gw:
            continue
        i, o = t["element_in"], t["element_out"]
        xi, xo = _xp(advice, i, gw), _xp(advice, o, gw)
        hi, ho = _xp_horizon(advice, i), _xp_horizon(advice, o)
        tr.append(
            {
                "in": name(i), "out": name(o),
                "pts_in": pts.get(i, 0), "pts_out": pts.get(o, 0),
                "xp_in": xi, "xp_out": xo,
                "exp_gain_horizon": (hi - ho) if hi is not None and ho is not None else None,
            }
        )
    hit_cost = 4 * result["hits"]

    # Captain
    xp_starters = {p: _xp(advice, p, gw) for p in starters}
    known = all(v is not None for v in xp_starters.values())
    best_actual = max(scorers, key=lambda p: pts[p])
    model_pick = max(starters, key=lambda p: xp_starters[p]) if known else None
    cap = {
        "name": name(captain),
        "chip": chip == "3xc",
        "pts": pts[captain],
        "total": pts[captain] * cap_mult,
        "xp": xp_starters.get(captain),
        "best_name": name(best_actual),
        "best_pts": pts[best_actual],
        "lost_vs_best": (pts[best_actual] - pts[captain]) * (cap_mult - 1),
        "model_pick": name(model_pick) if model_pick else None,
        "model_pick_xp": xp_starters[model_pick] if model_pick else None,
        "model_pick_pts": pts[model_pick] if model_pick else None,
        "vs_model": (pts[captain] - pts[model_pick]) * (cap_mult - 1) if model_pick else None,
    }

    # Bench and starting XI
    xi_pts = sum(pts[p] for p in scorers)
    bench_info = {"points_left": result["bench_points"], "chip": chip == "bboost"}
    if chip != "bboost":
        squad = starters + bench
        best_xi, _, _, _ = next_gw_lineup(squad, pts_series(pts, squad), ptype)
        bench_info["hindsight_gain"] = sum(pts[p] for p in best_xi) - xi_pts
        xp_all = {p: _xp(advice, p, gw) for p in squad}
        if all(v is not None for v in xp_all.values()):
            m_xi, m_bench, _, _ = next_gw_lineup(squad, pts_series(xp_all, squad), ptype)
            m_final = autosub(m_xi, m_bench, minutes, ptype)
            bench_info["model_xi_gain"] = sum(pts[p] for p in m_final) - xi_pts
            bench_info["model_xi_expected_gain"] = sum(xp_all[p] for p in m_xi) - sum(xp_all[p] for p in starters)
            bench_info["model_changes"] = [
                {"in": name(a), "out": name(b)} for a, b in zip([p for p in m_xi if p not in starters], [p for p in starters if p not in m_xi])
            ]

    # Model vs reality per player
    rows = []
    for p in scorers:
        s = live.get(p, {"total_points": 0, "minutes": 0, "goals_scored": 0, "assists": 0})
        xp = _xp(advice, p, gw)
        cause, kind = explain_player(xp, s, ptype[p])
        rows.append(
            {
                "name": name(p), "pos": POS[ptype[p]], "xp": xp, "pts": s["total_points"], "minutes": s["minutes"],
                "captain": p == captain, "cause": cause, "kind": kind,
            }
        )
    rows.sort(key=lambda r: -abs(r["pts"] - (r["xp"] or r["pts"])))

    # Advice (from the snapshot) vs what was done
    adv = None
    if advice:
        from .scorecard import advised_points

        week = advice["recommended"]["weeks"][0]
        adv = {
            "points": advised_points(advice, pts),
            "xp": week["xp"],
            "captain": advice["players"][str(week["captain"])]["name"],
            "buys": [advice["players"][str(p)]["name"] for p in week["buys"]],
            "sells": [advice["players"][str(p)]["name"] for p in week["sells"]],
            "chip": CHIP_NO.get(week["chip"]) if week["chip"] else None,
        }

    review = {
        "gw": gw,
        "final": True,
        "has_projections": advice is not None,
        "result": result,
        "transfers": tr,
        "hit_cost": hit_cost,
        "captain": cap,
        "bench": bench_info,
        "players": rows,
        "advice": adv,
    }
    review["lessons"] = lessons(review)
    review["summary"] = summary(review)
    return review


def pts_series(values: dict[int, float], squad: list[int]):
    import pandas as pd

    return pd.Series({p: float(values.get(p, 0.0) or 0.0) for p in squad})


def lessons(review: dict, history: list[dict] | None = None) -> list[str]:
    """At most two lessons, only where the data supports one (largest impact first)."""
    found: list[tuple[float, str]] = []
    b = review["bench"]
    if b.get("model_xi_expected_gain", 0) > 0.5 and b.get("model_xi_gain", 0) >= 3:
        changes = ", ".join(f"{c['in']} inn for {c['out']}" for c in b.get("model_changes", []))
        found.append((b["model_xi_gain"], f"Modellens startellever ({changes}) var ventet å gi mer, og ga {b['model_xi_gain']} poeng mer. Når laget avviker fra modellen, bør det være en grunn modellen ikke vet om (si den til Claude)."))
    c = review["captain"]
    if c.get("model_pick") and c["model_pick"] != c["name"] and (c["model_pick_xp"] or 0) - (c["xp"] or 0) > 0.5 and (c["vs_model"] or 0) <= -3:
        found.append((-c["vs_model"], f"Kapteinen avvek fra modellens toppvalg ({c['model_pick']}), og det kostet {-c['vs_model']} poeng."))
    if review["hit_cost"]:
        gains = [t["exp_gain_horizon"] for t in review["transfers"] if t["exp_gain_horizon"] is not None]
        if gains and sum(gains) < review["hit_cost"]:
            found.append((review["hit_cost"] - sum(gains), f"Minuspoengene ({review['hit_cost']}) var ventet å gi bare {sum(gains):.1f} poeng tilbake over seks runder."))
    for h in repeated_minutes_misses([*(history or []), review]):
        found.append((2.0, h))
    found.sort(key=lambda x: -x[0])
    return [text for _, text in found[:2]]


def repeated_minutes_misses(reviews: list[dict]) -> list[str]:
    """Players the model got wrong on minutes in at least two of the last three reviews."""
    last = sorted(reviews, key=lambda r: r["gw"])[-3:]
    counts: dict[str, int] = {}
    for r in last:
        for p in r.get("players", []):
            if p.get("kind") == "modell":
                counts[p["name"]] = counts.get(p["name"], 0) + 1
    return [
        f"Modellen har bommet på spilletiden til {n} i {k} av de siste {len(last)} rundene. Vet du noe om rollen hans, si det til Claude."
        for n, k in counts.items()
        if k >= 2
    ]


def summary(review: dict) -> list[str]:
    """Two or three lines for the deadline email."""
    r = review["result"]
    lines = [f"GW{review['gw']}: {r['points']} poeng (snitt {r['average']})" + (f", plass {_rank(r['prev_overall_rank'])} → {_rank(r['overall_rank'])}." if r.get("prev_overall_rank") else ".")]
    c = review["captain"]
    lines.append(f"Kaptein {c['name']}: {c['total']} poeng{' med Triple Captain' if c['chip'] else ''}" + (f"; beste i laget var {c['best_name']} med {c['best_pts']}." if c["lost_vs_best"] > 0 else "."))
    if review["lessons"]:
        lines.append(review["lessons"][0])
    return lines


def _rank(n: int | None) -> str:
    if n is None:
        return "–"
    return f"{n / 1e6:.2f} mill".replace(".", ",") if n >= 1e6 else f"{n:,}".replace(",", " ")


def load_reviews(review_dir: Path) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(review_dir).glob("gw*.json"))]


def final_gameweek(events: list[dict]) -> int | None:
    done = [e["id"] for e in events if e.get("finished") and e.get("data_checked")]
    return max(done, default=None)


def build(api, entry_id: int, gw: int, bootstrap: dict, advice: dict | None, history_reviews: list[dict]) -> dict:
    picks = api.picks(entry_id, gw)
    history = api.entry_history(entry_id)["current"]
    prev = next((h["overall_rank"] for h in history if h["event"] == gw - 1), None)
    live = {e["id"]: e["stats"] for e in api.event_live(gw)["elements"]}
    teams = {t["id"]: t["short_name"] for t in bootstrap["teams"]}
    meta = {e["id"]: {"name": e["web_name"], "team": teams[e["team"]], "element_type": e["element_type"]} for e in bootstrap["elements"]}
    event = next(e for e in bootstrap["events"] if e["id"] == gw)
    try:
        # Overall league, page 200 of 50 = around rank 10 000.
        page = api.classic_standings(314, 200)
        top10k = page["standings"]["results"][-1]["total"]
    except Exception:
        top10k = None
    review = compute(gw, picks, prev, live, api.transfers(entry_id), advice, event, meta, top10k)
    review["lessons"] = lessons(review, history_reviews)
    review["summary"] = summary(review)
    return review


def live_status(api, entry_id: int, bootstrap: dict) -> dict | None:
    """Points so far for a gameweek that has started but is not final yet."""
    current = next((e for e in bootstrap["events"] if e.get("is_current")), None)
    if not current or (current.get("finished") and current.get("data_checked")):
        return None
    try:
        hist = api.picks(entry_id, current["id"])["entry_history"]
    except FileNotFoundError:
        return None
    return {"gw": current["id"], "final": False, "points": hist["points"] - hist["event_transfers_cost"], "average": current.get("average_entry_score")}
