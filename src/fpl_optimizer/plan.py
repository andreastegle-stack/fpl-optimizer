"""Live run: fetch data, project, optimise, and produce the advice (a JSON-able dict)."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone

import pandas as pd

from . import optimizer as opt
from .api import FplApi
from .availability import availability, load_overrides
from .config import CACHE_DIR, OVERRIDES_PATH, PENDING_PATH
from .history import download_season, live_fixtures, live_matches, live_players, previous_season, season_rates, with_prior
from .projections import methods as projection_methods
from .squad import CHIP_LABELS, SquadState, build_squad_state, parse_time

POS = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
LAST_GW = 38


def gather(api: FplApi, settings: dict, now: datetime) -> dict:
    bootstrap = api.bootstrap()
    state = build_squad_state(api, settings["entry_id"], PENDING_PATH, now=now, bootstrap=bootstrap)
    elements = {e["id"]: e for e in bootstrap["elements"]}
    wanted = sorted({e["id"] for e in bootstrap["elements"] if e["minutes"] > 0} | set(state.players))
    summaries = api.element_summaries(wanted)
    return {
        "season": current_season(bootstrap),
        "bootstrap": bootstrap,
        "state": state,
        "elements": elements,
        "matches": live_matches(summaries, elements),
        "fixtures": live_fixtures(api.fixtures()),
        "players": with_prior(live_players(bootstrap), last_season_rates(current_season(bootstrap))),
    }


def current_season(bootstrap: dict) -> str:
    start = parse_time(bootstrap["events"][0]["deadline_time"]).year
    return f"{start}-{str(start + 1)[2:]}"


def last_season_rates(season: str) -> pd.DataFrame | None:
    """Last season's per-90 rates from the historical dataset (None if unavailable:
    the baseline then falls back to position averages)."""
    try:
        return season_rates(download_season(previous_season(season), CACHE_DIR / "vaastav"))
    except Exception:
        return None


def project_live(data: dict, settings: dict, gws: list[int], now: datetime) -> pd.DataFrame:
    """Projections times availability, for every player in ``players``."""
    source = settings["projection_source"]
    method = projection_methods()[source]
    events = data["bootstrap"]["events"]
    deadline = pd.Timestamp(parse_time(next(e for e in events if e["id"] == gws[0])["deadline_time"]))
    raw = method(data["matches"], data["fixtures"], data["players"], gws, deadline)
    raw = raw.reindex(data["players"]["element"]).fillna(0.0)
    avail = availability(data["players"], events, gws, now, load_overrides(OVERRIDES_PATH)).reindex(raw.index).fillna(1.0)
    return raw * avail


def chip_hold_values(
    state: SquadState, xp_all: pd.DataFrame, horizon_end: int, ptype: dict[int, int], fixed: dict[str, float], fixture_count: dict[int, int]
) -> tuple[dict, dict]:
    """Which chips may be played in which horizon weeks, and what playing one now costs.

    Triple Captain and Bench Boost: the best gain the current squad is projected to get
    from the chip in the rest of its window after the horizon (0 if the window ends
    inside the horizon). Wildcard and Free Hit: fixed values from settings while
    weeks remain after the horizon. Free Hit is only offered in blank or double
    gameweeks (not 10 fixtures), or in the last week before it expires.
    """
    gws = [g for g in xp_all.columns if g <= horizon_end]
    chip_gws, hold = {}, {}
    for chip in state.chips:
        if chip.used_event is not None:
            continue
        weeks = [g for g in gws if chip.start_event <= g <= chip.stop_event]
        if chip.name == "freehit":
            weeks = [g for g in weeks if fixture_count.get(g, 0) != 10 or g == chip.stop_event]
        if not weeks:
            continue
        chip_gws.setdefault(chip.name, []).extend(weeks)
        later = [g for g in xp_all.columns if horizon_end < g <= chip.stop_event]
        if not later:
            hold[chip.name] = 0.0
        elif chip.name == "3xc":
            hold[chip.name] = max(float(xp_all.loc[state.players, g].max()) for g in later)
        elif chip.name == "bboost":
            vals = []
            for g in later:
                _, bench, _, _ = opt.next_gw_lineup(state.players, xp_all[g], ptype)
                vals.append(float(xp_all.loc[bench, g].sum()))
            hold[chip.name] = max(vals)
        else:
            hold[chip.name] = float(fixed.get(chip.name, 0.0))
    return chip_gws, hold


def build_problem(data: dict, settings: dict, xp: pd.DataFrame, chip_gws: dict, chip_hold: dict) -> opt.Problem:
    state: SquadState = data["state"]
    s = settings["solver"]
    players = data["players"].set_index("element")
    ptype = players["element_type"].to_dict()
    buy = players["now_cost"].to_dict()
    sizes = {int(k): v for k, v in s["pool_size"].items()}
    # Players who cannot be bought (left the league) are only kept if already owned.
    xp = xp[(players["status"].reindex(xp.index) != "u") | xp.index.isin(state.players)]
    pool = opt.select_pool(xp, ptype, buy, state.players, sizes, s["decay"])
    return opt.Problem(
        gws=list(xp.columns),
        xp=xp.loc[pool],
        element_type={p: ptype[p] for p in pool},
        team={p: int(players.at[p, "team"]) for p in pool},
        buy_price={p: int(buy[p]) for p in pool},
        sell_price=state.selling_price,
        squad=state.players,
        bank=state.bank,
        free_transfers=state.free_transfers,
        max_ft=1 + data["bootstrap"]["game_settings"].get("max_extra_free_transfers", 4),
        chip_gws=chip_gws,
        chip_hold=chip_hold,
        decay=s["decay"],
        ft_values=tuple(s["ft_values"]),
        bench_weight=s["bench_weight"],
        hit_cost=s["hit_cost"],
        hit_margin=s["hit_margin"],
        time_limit_s=s["time_limit_s"],
        mip_gap=s["mip_gap"],
    )


def alternatives(prob: opt.Problem, best: opt.Solution, state: SquadState) -> list[tuple[str, opt.Solution]]:
    """Two or three meaningfully different first-week options."""
    first = best.first
    n = len(first.sells) if first.chip not in ("wildcard", "freehit") else None
    tries: list[tuple[str, opt.Constraints]] = []
    if first.chip:
        tries.append(("no_chip", opt.Constraints(no_chip_first=True)))
    if n:
        tries.append(("roll", opt.Constraints(transfers_first=0, no_chip_first=bool(first.chip))))
        tries.append(("other_transfer", opt.Constraints(transfers_first=n, exclude_in_sets=[frozenset(first.buys)])))
    else:
        tries.append(("one_transfer", opt.Constraints(transfers_first=1)))
        tries.append(("two_transfers", opt.Constraints(transfers_first=2)))
    if n is not None and n <= state.free_transfers and state.free_transfers < 5:
        tries.append(("hit", opt.Constraints(transfers_first=state.free_transfers + 1)))

    found: list[tuple[str, opt.Solution]] = []
    seen = {_signature(first)}
    for label, cons in tries:
        sol = opt.solve(prob, cons)
        if sol is None or _signature(sol.first) in seen:
            continue
        seen.add(_signature(sol.first))
        found.append((label, sol))
        if len(found) == 3:
            break
    return found


def _signature(week: opt.WeekPlan) -> tuple:
    return (frozenset(week.buys), frozenset(week.sells), week.chip)


def make_advice(api: FplApi, settings: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    data = gather(api, settings, now)
    state: SquadState = data["state"]
    gw0 = state.next_event
    horizon = list(range(gw0, min(gw0 + settings["horizon"], LAST_GW + 1)))
    last_chip_gw = max([c.stop_event for c in state.available_chips(gw0)] + [horizon[-1]])
    xp_all = project_live(data, settings, list(range(gw0, min(last_chip_gw, LAST_GW) + 1)), now)
    ptype = data["players"].set_index("element")["element_type"].to_dict()
    fixture_count = data["fixtures"].groupby("gw").size().to_dict()
    chip_gws, chip_hold = chip_hold_values(state, xp_all, horizon[-1], ptype, settings["chips"]["hold_value"], fixture_count)

    prob = build_problem(data, settings, xp_all[horizon], chip_gws, chip_hold)
    best = opt.solve(prob)
    if best is None:
        raise RuntimeError("Optimiser found no feasible plan")
    alts = alternatives(prob, best, state)
    return serialise(data, settings, now, horizon, xp_all[horizon], best, alts, chip_gws, chip_hold)


def serialise(data, settings, now, horizon, xp, best, alts, chip_gws, chip_hold) -> dict:
    state: SquadState = data["state"]
    elements = data["elements"]
    teams = {t["id"]: t["short_name"] for t in data["bootstrap"]["teams"]}
    events = {e["id"]: e for e in data["bootstrap"]["events"]}
    gw0 = horizon[0]

    referenced: set[int] = set(state.players)
    for _, sol in [("best", best), *alts]:
        for w in sol.weeks:
            referenced |= set(w.squad) | set(w.buys) | set(w.sells)
    fixtures = data["fixtures"]
    players = {}
    for p in sorted(referenced):
        e = elements[p]
        opp = []
        for g in horizon:
            fx = fixtures[(fixtures["gw"] == g) & ((fixtures["team_h"] == e["team"]) | (fixtures["team_a"] == e["team"]))]
            opp.append(
                [
                    {
                        "opp": teams[r.team_a] if r.team_h == e["team"] else teams[r.team_h],
                        "home": bool(r.team_h == e["team"]),
                        "fdr": int(r.diff_h if r.team_h == e["team"] else r.diff_a),
                    }
                    for r in fx.itertuples()
                ]
            )
        players[p] = {
            "name": e["web_name"],
            "team": teams[e["team"]],
            "pos": POS[e["element_type"]],
            "price": e["now_cost"] / 10,
            "sell_price": state.selling_price[p] / 10 if p in state.selling_price else None,
            "status": e["status"],
            "chance": e["chance_of_playing_next_round"],
            "news": e["news"],
            "form": float(e["form"]),
            "xp": {g: round(float(xp.at[p, g]), 2) if p in xp.index else 0.0 for g in horizon},
            "fixtures": dict(zip(horizon, opp)),
        }

    def plan(label: str, sol) -> dict:
        return {
            "label": label,
            "objective": round(sol.objective, 2),
            "xp_next": round(sol.first.xp, 2),
            "xp_horizon": round(sol.total_xp, 2),
            "weeks": [asdict(w) | {"xp": round(w.xp, 2)} for w in sol.weeks],
        }

    flags = []
    for p in state.players:
        e = elements[p]
        if e["status"] != "a" or (e["chance_of_playing_next_round"] is not None and e["chance_of_playing_next_round"] < 100):
            flags.append({"element": p, "status": e["status"], "chance": e["chance_of_playing_next_round"], "news": e["news"]})

    deadline = events[gw0]["deadline_time"]
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "gw": gw0,
        "deadline": deadline,
        "horizon": horizon,
        "projection_source": settings["projection_source"],
        "entry": {"id": state.entry_id, "name": state.entry_name},
        "state": {
            "last_deadline": state.last_deadline,
            "squad": state.players,
            "bank": state.bank / 10,
            "free_transfers": state.free_transfers,
            "pending": state.pending,
            "pending_hit_cost": state.pending_hit_cost,
            "ignored_pending": state.ignored_pending,
        },
        "chips": [
            {
                "name": c.name,
                "label": CHIP_LABELS.get(c.name, c.name),
                "start": c.start_event,
                "stop": c.stop_event,
                "used": c.used_event,
                "allowed_now": c.name in chip_gws and c.start_event <= gw0 <= c.stop_event,
                "hold_value": round(chip_hold.get(c.name, 0.0), 1) if c.start_event <= gw0 <= c.stop_event else None,
            }
            for c in state.chips
        ],
        "recommended": plan("recommended", best),
        "alternatives": [plan(label, sol) for label, sol in alts],
        "players": players,
        "flags": flags,
    }
