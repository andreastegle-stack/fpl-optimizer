"""Render the advice as a static Norwegian page (GitHub Pages, phone first)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from jinja2 import Environment, PackageLoader, select_autoescape

OSLO = ZoneInfo("Europe/Oslo")
WEEKDAYS = ["mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag", "søndag"]
MONTHS = ["januar", "februar", "mars", "april", "mai", "juni", "juli", "august", "september", "oktober", "november", "desember"]
POS_NO = {"GK": "Keeper", "DEF": "Forsvar", "MID": "Midtbane", "FWD": "Angrep"}
CHIP_NO = {"wildcard": "Wildcard", "freehit": "Free Hit", "bboost": "Bench Boost", "3xc": "Triple Captain"}
ALT_TITLES = {
    "roll": "Spar byttet",
    "other_transfer": "Et annet bytte",
    "one_transfer": "Ett bytte nå",
    "two_transfers": "To bytter nå",
    "hit": "Ekstra bytte (−4)",
    "no_chip": "Uten chip denne runden",
}
STATUS_NO = {"d": "Usikker", "i": "Skadet", "s": "Utestengt", "u": "Ikke tilgjengelig", "n": "Ikke tilgjengelig"}


def no_datetime(value: str | datetime) -> str:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    dt = dt.astimezone(OSLO)
    return f"{WEEKDAYS[dt.weekday()]} {dt.day}. {MONTHS[dt.month - 1]} kl. {dt:%H:%M}"


def fmt(x: float, signed: bool = False) -> str:
    s = f"{x:+.1f}" if signed else f"{x:.1f}"
    return s.replace("-", "−").replace(".", ",")


def describe_week(advice: dict, week: dict) -> dict:
    """Plain-language summary of one gameweek in a plan."""
    players = {str(k): v for k, v in advice["players"].items()}
    P = lambda p: players[str(p)]
    moves = []
    sells = sorted(week["sells"], key=lambda p: P(p)["pos"])
    buys = list(week["buys"])
    # Pair each sale with a purchase in the same position for display.
    for s in sells:
        b = next((x for x in buys if P(x)["pos"] == P(s)["pos"]), buys[0] if buys else None)
        if b is not None:
            buys.remove(b)
        moves.append({"out": P(s), "in": P(b) if b else None, "gain": (sum(P(b)["xp"].values()) - sum(P(s)["xp"].values())) if b else 0.0})
    return {
        "gw": week["gw"],
        "moves": moves,
        "chip": CHIP_NO.get(week["chip"]) if week["chip"] else None,
        "hits": week["hits"],
        "free_transfers": week["free_transfers"],
        "xp": week["xp"],
    }


def plan_reason(advice: dict, plan: dict, ref: dict | None) -> str:
    first = plan["weeks"][0]
    n = len(first["sells"])
    ft = first["free_transfers"]
    parts = []
    if first["chip"] in ("wildcard", "freehit"):
        parts.append(f"Bruker {CHIP_NO[first['chip']]} og bygger om laget ({n} endringer).")
    elif n == 0:
        nxt = plan["weeks"][1]["free_transfers"] if len(plan["weeks"]) > 1 else None
        if ft == 0:
            parts.append("Du har ingen gratis bytter igjen denne runden, og ingen bytte er verdt 4 minuspoeng.")
        else:
            parts.append("Ingen bytte gir nok gevinst nå.")
        if nxt:
            parts.append(f"Neste runde har du {nxt} gratis bytte{'r' if nxt > 1 else ''}.")
    else:
        parts.append(f"{n} bytte{'r' if n > 1 else ''}" + (f", koster {4 * first['hits']} minuspoeng." if first["hits"] else " med gratis bytte."))
    if first["chip"] in ("bboost", "3xc"):
        parts.append(f"Bruker {CHIP_NO[first['chip']]} denne runden.")
    if ref is not None:
        d6 = plan["xp_horizon"] - ref["xp_horizon"]
        d1 = plan["xp_next"] - ref["xp_next"]
        parts.append(f"{fmt(d1, True)} forventede poeng neste runde og {fmt(d6, True)} over {len(plan['weeks'])} runder sammenlignet med anbefalingen.")
        if d6 > 0:
            parts.append("Litt flere forventede poeng totalt, men gevinsten er mindre enn sikkerhetsmarginen for minuspoeng og verdien av å spare gratis bytter.")
    return " ".join(parts)


def lineup_rows(advice: dict, week: dict) -> dict:
    players = {str(k): v for k, v in advice["players"].items()}
    gw = str(week["gw"])

    def row(p, role=""):
        info = players[str(p)]
        xp = info["xp"].get(gw, info["xp"].get(int(gw), 0.0)) if isinstance(info["xp"], dict) else 0.0
        fx = info["fixtures"].get(gw, info["fixtures"].get(int(gw), []))
        return {
            "name": info["name"],
            "team": info["team"],
            "pos": info["pos"],
            "xp": xp,
            "fixtures": ", ".join(f"{f['opp']} ({'h' if f['home'] else 'b'})" for f in fx) or "ingen kamp",
            "role": role,
            "flag": STATUS_NO.get(info["status"]) if info["status"] != "a" else None,
        }

    roles = {week["captain"]: "C", week["vice"]: "V"}
    lineup = [row(p, roles.get(p, "")) for p in week["lineup"]]
    groups = []
    for pos in ("GK", "DEF", "MID", "FWD"):
        groups.append({"label": POS_NO[pos], "players": [r for r in lineup if r["pos"] == pos]})
    bench = [row(p) for p in week["bench"]]
    return {"groups": groups, "bench": bench, "captain": row(week["captain"]), "vice": row(week["vice"])}


def render(advice: dict, scorecard: list[dict], backtest: dict | None, out_dir: Path, repo_url: str) -> Path:
    env = Environment(loader=PackageLoader("fpl_optimizer", "templates"), autoescape=select_autoescape(["html"]))
    env.filters["n"] = fmt
    env.filters["signed"] = lambda x: fmt(x, True)
    rec = advice["recommended"]
    first = rec["weeks"][0]
    players = {str(k): v for k, v in advice["players"].items()}

    flags = []
    for f in advice["flags"]:
        info = players[str(f["element"])]
        flags.append({"name": info["name"], "team": info["team"], "status": STATUS_NO.get(f["status"], f["status"]), "chance": f["chance"], "news": f["news"]})
    for p in first["buys"]:
        info = players[str(p)]
        if info["status"] != "a":
            flags.append({"name": info["name"] + " (foreslått kjøp)", "team": info["team"], "status": STATUS_NO.get(info["status"], ""), "chance": info["chance"], "news": info["news"]})

    gw0 = advice["gw"]
    chips = []

    def planned_gw(c: dict) -> int | None:
        # Match on the chip's own window: a horizon past GW19 can plan both chip sets.
        return next((w["gw"] for w in rec["weeks"] if w["chip"] == c["name"] and c["start"] <= w["gw"] <= c["stop"]), None)

    current = [c for c in advice["chips"] if c["start"] <= gw0 <= c["stop"] or c["used"] is not None and c["stop"] >= gw0]
    later_set = sorted({c["start"] for c in advice["chips"] if c["start"] > gw0})
    for c in sorted(current, key=lambda c: c["start"]):
        if c["stop"] < gw0 and c["used"] is None:
            status = f"Utløpt etter GW{c['stop']}"
        elif c["used"] is not None:
            status = f"Brukt i GW{c['used']}"
        elif c["start"] > gw0:
            status = f"Ny chip fra GW{c['start']}"
        elif (gw := planned_gw(c)) is not None:
            status = f"Planlagt i GW{gw}" if gw != gw0 else "Bruk nå"
        else:
            status = f"Tilgjengelig til og med GW{c['stop']}"
        chips.append({"label": CHIP_NO.get(c["name"], c["label"]), "window": f"GW{c['start']}–{c['stop']}", "status": status, "hold": c.get("hold_value")})
    next_chip_set = later_set[0] if later_set else None

    bt = None
    if backtest:
        names = [n for n in ("form", "baseline", "model") if n in backtest]
        label = {"form": "FPL form", "baseline": "Grunnmodell", "model": "Vår modell"}
        bt = {
            "season": backtest.get("_season", ""),
            "gws": backtest.get("_eval_gws"),
            "cols": [label[n] for n in names],
            "rows": [
                {"label": "Gjennomsnittlig bom neste runde (lavere er bedre)", "vals": [backtest[n]["rmse_next"] for n in names]},
                {"label": "Bom på sum over 6 runder (lavere er bedre)", "vals": [backtest[n]["rmse_6gw"] for n in names]},
                {"label": "Rangerer spillerne riktig (0–1, høyere er bedre)", "vals": [backtest[n]["spearman_next"] for n in names]},
                {"label": "Snittpoeng for topp-20-valgene (høyere er bedre)", "vals": [backtest[n]["top20_pts"] for n in names]},
            ],
            "gate": backtest.get("_gate"),
        }

    context = {
        "advice": advice,
        "deadline": no_datetime(advice["deadline"]),
        "generated": no_datetime(advice["generated_at"]),
        "rec": rec,
        "rec_week": describe_week(advice, first),
        "rec_reason": plan_reason(advice, rec, None),
        "lineup": lineup_rows(advice, first),
        "later": [describe_week(advice, w) for w in rec["weeks"][1:]],
        "alts": [
            {
                "title": ALT_TITLES.get(a["label"], a["label"]) + (f" (−{4 * a['weeks'][0]['hits']})" if a["weeks"][0]["hits"] and a["label"] != "hit" else ""),
                "plan": a,
                "week": describe_week(advice, a["weeks"][0]),
                "lineup": lineup_rows(advice, a["weeks"][0]),
                "reason": plan_reason(advice, a, rec),
            }
            for a in advice["alternatives"]
        ],
        "flags": flags,
        "chips": chips,
        "next_chip_set": next_chip_set,
        "scorecard": sorted(scorecard, key=lambda r: -r["gw"]),
        "backtest": bt,
        "source": "grunnmodellen (poeng per kamp justert for spilletid og kampprogram)" if advice["projection_source"] == "baseline" else "vår egen modell",
        "repo_url": repo_url,
        "n_weeks": len(rec["weeks"]),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "index.html"
    out.write_text(env.get_template("report.html.j2").render(**context), encoding="utf-8")
    return out
