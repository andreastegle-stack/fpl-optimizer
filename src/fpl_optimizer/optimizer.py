"""Multi-gameweek transfer planner (mixed-integer program solved with HiGHS).

The formulation follows the approach of open-fpl-solver by Solio Analytics
(https://github.com/solioanalytics/open-fpl-solver, Apache-2.0): squad, lineup,
captain, vice and bench variables per gameweek, transfers in/out, bank, free
transfer roll-over with the 1..5 clamp, penalised transfers (hits) and chips. It
is rewritten here around our own projections and simplified for one manager; see
NOTICE.

Objective per gameweek ``w`` (discounted by ``decay ** (w - first)``):

    lineup xP + captain xP (+ again with Triple Captain)
    + bench_weight * bench xP (all 15 count in the lineup with Bench Boost)
    - (hit_cost + hit_margin) * hits
    + V(free transfers next week) - V(free transfers this week)
    - chip hold value (points we expect to get from the chip later in its window)

``V`` (``ft_values``) is the value of holding k free transfers. It is what makes the
planner value rolling a transfer: using one has to gain more than the drop in ``V``
or it is better to keep it. ``V`` grows with diminishing steps, so banking a fourth
or fifth transfer is worth less than a second, and a hit taken now is not "paid
back" by an unrealistic value for transfers saved at the end of the horizon.

``hit_margin`` is a safety margin on top of the real 4-point cost: projections are
uncertain, so a hit is only recommended when it is expected to gain clearly more
than it costs. Reported expected points use the real cost.

Lineup, captain and bench for a gameweek only ever use that gameweek's projections:
the solver picks the squads, then ``next_gw_lineup`` sets XI, bench order, captain
and vice from that week's xP alone.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import highspy
import numpy as np
import pandas as pd

SQUAD_SIZE = 15
LINEUP_SIZE = 11
SQUAD_SELECT = {1: 2, 2: 5, 3: 5, 4: 3}
PLAY_MIN = {1: 1, 2: 3, 3: 2, 4: 1}
PLAY_MAX = {1: 1, 2: 5, 3: 5, 4: 3}
MAX_PER_TEAM = 3


@dataclass
class Problem:
    """Everything the solver needs. Prices in tenths of £m."""

    gws: list[int]
    xp: pd.DataFrame  # index element, columns gws
    element_type: dict[int, int]
    team: dict[int, int]
    buy_price: dict[int, int]
    sell_price: dict[int, int]  # for the current squad
    squad: list[int]
    bank: int
    free_transfers: int
    max_ft: int = 5
    # chip name -> gameweeks (within gws) it may be played in
    chip_gws: dict[str, list[int]] = field(default_factory=dict)
    # chip name -> expected points given up by playing it now rather than later
    chip_hold: dict[str, float] = field(default_factory=dict)
    decay: float = 0.9
    ft_values: tuple[float, ...] = (0.0, 0.0, 1.5, 2.5, 3.2, 3.6)  # value of holding 0..5 FTs
    bench_weight: float = 0.08
    hit_cost: int = 4
    hit_margin: float = 2.0
    time_limit_s: float = 60
    mip_gap: float = 0.002


@dataclass
class Constraints:
    """Extra constraints on the first gameweek, used to generate alternatives."""

    transfers_first: int | None = None  # exact number of (non-wildcard) transfers
    min_transfers_first: int | None = None
    exclude_in_sets: list[frozenset[int]] = field(default_factory=list)
    no_chip_first: bool = False
    chip_first: str | None = None  # force this chip in the first gameweek


@dataclass
class WeekPlan:
    gw: int
    buys: list[int]
    sells: list[int]
    chip: str | None
    squad: list[int]
    lineup: list[int]
    bench: list[int]  # bench order: GK first, then outfield 1..3
    captain: int
    vice: int
    hits: int
    free_transfers: int  # available going into this week
    bank: int
    xp: float  # expected points of the lineup this week incl. captain, minus hits


@dataclass
class Solution:
    objective: float
    weeks: list[WeekPlan]

    @property
    def first(self) -> WeekPlan:
        return self.weeks[0]

    @property
    def total_xp(self) -> float:
        return sum(w.xp for w in self.weeks)


def solve(prob: Problem, cons: Constraints | None = None) -> Solution | None:
    cons = cons or Constraints()
    gws = prob.gws
    w0 = gws[0]
    players = list(prob.xp.index)
    xp = {(p, w): float(prob.xp.at[p, w]) for p in players for w in gws}
    ptype = prob.element_type
    teams = sorted({prob.team[p] for p in players})
    initial = set(prob.squad)
    all_w = [w0 - 1, *gws]
    allowed = {c: [w for w in gws if w in set(prob.chip_gws.get(c, []))] for c in ("wildcard", "freehit", "bboost", "3xc")}
    if cons.no_chip_first:
        allowed = {c: [w for w in ws if w != w0] for c, ws in allowed.items()}
    if cons.transfers_first is not None:
        allowed["wildcard"] = [w for w in allowed["wildcard"] if w != w0]
        allowed["freehit"] = [w for w in allowed["freehit"] if w != w0]
    if cons.chip_first and w0 not in allowed[cons.chip_first]:
        return None
    fh_weeks, tc_weeks = allowed["freehit"], allowed["3xc"]

    h = highspy.Highs()
    h.setOptionValue("output_flag", False)
    h.setOptionValue("time_limit", float(prob.time_limit_s))
    h.setOptionValue("mip_rel_gap", prob.mip_gap)
    S = h.qsum
    add = h.addConstr

    def binv(*keys_lists):
        return {k: h.addBinary() for k in itertools.product(*keys_lists)}

    squad = binv(players, all_w)
    lineup = binv(players, gws)
    bench = binv(players, gws)
    captain = binv(players, gws)
    tin = binv(players, gws)
    tout = binv(players, gws)
    squad_fh = binv(players, fh_weeks)
    use_tc = binv(players, tc_weeks)
    # Chip indicators exist only in weeks the chip may be played; elsewhere they are 0.
    chip_vars = {c: {w: (h.addBinary() if w in allowed[c] else 0) for w in gws} for c in ("wildcard", "freehit", "bboost")}
    chip_vars["3xc"] = {w: (S(use_tc[p, w] for p in players) if w in tc_weeks else 0) for w in gws}
    wc, fh, bb, tc = (chip_vars[c] for c in ("wildcard", "freehit", "bboost", "3xc"))
    itb = {w: h.addVariable(lb=0) for w in all_w}
    ft = {w: h.addIntegral(lb=0, ub=prob.max_ft) for w in [*gws, gws[-1] + 1]}
    ft_state = binv([*gws[1:], gws[-1] + 1], range(prob.max_ft + 1))
    ft_value = {w0: prob.ft_values[min(prob.free_transfers, prob.max_ft)]}
    for w in [*gws[1:], gws[-1] + 1]:
        add(ft[w] == S(k * ft_state[w, k] for k in range(prob.max_ft + 1)))
        add(S(ft_state[w, k] for k in range(prob.max_ft + 1)) == 1)
        ft_value[w] = S(prob.ft_values[k] * ft_state[w, k] for k in range(prob.max_ft + 1))
    above = {w: h.addBinary() for w in gws}
    below = {w: h.addBinary() for w in gws}
    trc = {w: h.addIntegral(lb=0, ub=SQUAD_SIZE) for w in gws}
    hits = {w: h.addIntegral(lb=0, ub=SQUAD_SIZE) for w in gws}

    # Initial state
    for p in players:
        add(squad[p, w0 - 1] == (1 if p in initial else 0))
        if p in initial:  # selling then re-buying would need a second sell price
            add(S(tin[p, w] for w in gws) == 0)
    add(itb[w0 - 1] == prob.bank)
    add(ft[w0] == prob.free_transfers)
    sell = {p: prob.sell_price.get(p, prob.buy_price[p]) for p in players}

    for w in gws:
        is_fh = w in fh_weeks
        add(S(squad[p, w] for p in players) == SQUAD_SIZE)
        add(S(lineup[p, w] for p in players) == LINEUP_SIZE + (SQUAD_SIZE - LINEUP_SIZE) * bb[w])
        add(S(bench[p, w] for p in players) == (SQUAD_SIZE - LINEUP_SIZE) * (1 - bb[w]))
        add(S(captain[p, w] for p in players) == 1)
        for t, n in SQUAD_SELECT.items():
            of_type = [p for p in players if ptype[p] == t]
            add(S(squad[p, w] for p in of_type) == n)
            add(S(lineup[p, w] for p in of_type) >= PLAY_MIN[t])
            add(S(lineup[p, w] for p in of_type) <= PLAY_MAX[t] + bb[w] * (n - PLAY_MAX[t]))
            if is_fh:
                add(S(squad_fh[p, w] for p in of_type) == n * fh[w])
        for t in teams:
            of_team = [p for p in players if prob.team[p] == t]
            add(S(squad[p, w] for p in of_team) <= MAX_PER_TEAM)
            if is_fh:
                add(S(squad_fh[p, w] for p in of_team) <= MAX_PER_TEAM * fh[w])
        for p in players:
            # The squad that plays this week: the Free Hit squad if one is played.
            if is_fh:
                add(lineup[p, w] + bench[p, w] <= squad[p, w] + fh[w])
                add(lineup[p, w] + bench[p, w] <= squad_fh[p, w] + 1 - fh[w])
                add(squad_fh[p, w] <= fh[w])
                add(tin[p, w] <= 1 - fh[w])
                add(tout[p, w] <= 1 - fh[w])
            else:
                add(lineup[p, w] + bench[p, w] <= squad[p, w])
            add(captain[p, w] <= lineup[p, w])
            if w in tc_weeks:
                add(use_tc[p, w] <= captain[p, w])
            add(squad[p, w] == squad[p, w - 1] + tin[p, w] - tout[p, w])
            add(tin[p, w] + tout[p, w] <= 1)

        sold = S(sell[p] * tout[p, w] for p in players)
        bought = S(prob.buy_price[p] * tin[p, w] for p in players)
        add(itb[w] == itb[w - 1] + sold - bought)
        if is_fh:
            # The Free Hit squad must be affordable with the squad value plus bank.
            add(S(prob.buy_price[p] * squad_fh[p, w] for p in players) <= S(sell[p] * squad[p, w - 1] for p in players) + itb[w - 1])

        n_tr = S(tout[p, w] for p in players)
        add(trc[w] >= n_tr - SQUAD_SIZE * wc[w])
        add(trc[w] <= n_tr)
        add(trc[w] <= SQUAD_SIZE * (1 - wc[w]))
        add(hits[w] >= trc[w] - ft[w])

        # Free transfer roll-over: next = clamp(ft - transfers + 1, 1, max_ft); a
        # wildcard or free hit keeps the count unchanged.
        raw = ft[w] - trc[w] + 1 - wc[w] - fh[w]
        big = 20
        add(raw >= prob.max_ft + 1 - big * (1 - above[w]))
        add(raw <= prob.max_ft + big * above[w])
        add(raw <= 0 + big * (1 - below[w]))
        add(raw >= 1 - big * below[w])
        nxt = ft[w + 1]
        add(nxt <= prob.max_ft + big * (1 - above[w]))
        add(nxt >= prob.max_ft - big * (1 - above[w]))
        add(nxt <= 1 + big * (1 - below[w]))
        add(nxt >= 1 - big * (1 - below[w]))
        add(nxt - raw <= big * (above[w] + below[w]))
        add(raw - nxt <= big * (above[w] + below[w]))

        chips_now = [chip_vars[c][w] for c in chip_vars if w in allowed[c]]
        if len(chips_now) > 1:
            add(S(chips_now) <= 1)  # one chip per gameweek

    for c, var in chip_vars.items():
        if len(allowed[c]) > 1:
            add(S(var[w] for w in allowed[c]) <= 1)

    # Constraints for alternatives
    if cons.transfers_first is not None:
        add(S(tout[p, w0] for p in players) == cons.transfers_first)
    if cons.min_transfers_first is not None:
        add(S(tout[p, w0] for p in players) >= cons.min_transfers_first)
    for in_set in cons.exclude_in_sets:
        if in_set:
            add(S(tin[p, w0] for p in in_set if p in players) <= len(in_set) - 1)
    if cons.chip_first:
        add(chip_vars[cons.chip_first][w0] == 1)

    terms = []
    for w in gws:
        d = prob.decay ** (w - w0)
        week = S(xp[p, w] * (lineup[p, w] + captain[p, w] + prob.bench_weight * bench[p, w]) for p in players)
        if w in tc_weeks:
            week = week + S(xp[p, w] * use_tc[p, w] for p in players)
        week = week - (prob.hit_cost + prob.hit_margin) * hits[w] + ft_value[w + 1] - ft_value[w]
        holds = [prob.chip_hold.get(c, 0.0) * var[w] for c, var in chip_vars.items() if w in allowed[c]]
        if holds:
            week = week - S(holds)
        terms.append(d * week)
    h.maximize(S(terms))

    if h.getInfo().primal_solution_status != 2:
        return None

    def on(v) -> bool:
        return not isinstance(v, int) and h.val(v) > 0.5

    weeks = []
    for w in gws:
        played_fh = on(fh[w])
        sq = [p for p in players if on(squad_fh[p, w] if played_fh else squad[p, w])]
        chip = next((c for c in ("wildcard", "freehit", "bboost") if on(chip_vars[c][w])), None)
        if w in tc_weeks and any(on(use_tc[p, w]) for p in players):
            chip = "3xc"
        lineup_ids, bench_ids, cap, vc = next_gw_lineup(sq, prob.xp[w], ptype)
        n_hits = int(round(h.val(hits[w])))
        scoring = lineup_ids + (bench_ids if chip == "bboost" else [])
        week_xp = sum(xp[p, w] for p in scoring) + xp[cap, w] * (2 if chip == "3xc" else 1) - prob.hit_cost * n_hits
        weeks.append(
            WeekPlan(
                gw=w,
                buys=[p for p in players if on(tin[p, w])],
                sells=[p for p in players if on(tout[p, w])],
                chip=chip,
                squad=sq,
                lineup=lineup_ids,
                bench=bench_ids,
                captain=cap,
                vice=vc,
                hits=n_hits,
                free_transfers=int(round(h.val(ft[w]))),
                bank=int(round(h.val(itb[w]))),
                xp=week_xp,
            )
        )
    return Solution(objective=h.getObjectiveValue(), weeks=weeks)


def next_gw_lineup(squad: list[int], xp: pd.Series, ptype: dict[int, int]):
    """Best XI, bench order and captaincy for one gameweek from that gameweek's xP only.

    Greedy is optimal here: fill the formation minimums with the best player of each
    position, then the best remaining outfielders (a 2/5/5/3 squad never hits a maximum).
    """
    by = sorted(squad, key=lambda p: -float(xp.get(p, 0.0)))
    gks = [p for p in by if ptype[p] == 1]
    outfield = [p for p in by if ptype[p] != 1]
    chosen = [gks[0]]
    for t, n in ((2, 3), (3, 2), (4, 1)):
        chosen += [p for p in outfield if ptype[p] == t][:n]
    rest = [p for p in outfield if p not in chosen]
    chosen += rest[: LINEUP_SIZE - len(chosen)]
    bench = [gks[1]] + [p for p in outfield if p not in chosen]
    lineup = sorted(chosen, key=lambda p: (ptype[p], -float(xp.get(p, 0.0))))
    ranked = sorted(lineup, key=lambda p: -float(xp.get(p, 0.0)))
    return lineup, bench, ranked[0], ranked[1]


def select_pool(xp: pd.DataFrame, element_type: dict[int, int], buy_price: dict[int, int], squad: list[int], sizes: dict[int, int], decay: float) -> list[int]:
    """Players worth considering: the current squad plus the best per position by
    discounted horizon xP and by xP per £m (to keep cheap enablers in play)."""
    weights = np.array([decay**i for i in range(xp.shape[1])])
    score = pd.Series(xp.values @ weights, index=xp.index)
    pool = set(squad)
    for t, n in sizes.items():
        ids = [p for p in xp.index if element_type.get(p) == t]
        s = score.reindex(ids)
        pool |= set(s.nlargest(n).index)
        pool |= set((s / pd.Series({p: buy_price[p] for p in ids})).nlargest(max(n // 3, 4)).index)
    return sorted(p for p in pool if p in xp.index)
