"""Small synthetic problems that pin down the planner's behaviour."""

import pandas as pd

from fpl_optimizer import optimizer as opt

GWS = [10, 11, 12]


def make_problem(xp_overrides=None, free_transfers=1, bank=0, gws=GWS, **kwargs):
    # 20 players over 10 clubs: 3 GK, 6 DEF, 6 MID, 5 FWD; all cost 5.0m.
    types = [1] * 3 + [2] * 6 + [3] * 6 + [4] * 5
    ids = list(range(1, len(types) + 1))
    xp = pd.DataFrame({w: [2.0] * len(ids) for w in gws}, index=ids)
    for p, vals in (xp_overrides or {}).items():
        xp.loc[p] = vals
    squad = [1, 2, 4, 5, 6, 7, 8, 10, 11, 12, 13, 14, 16, 17, 18]
    return opt.Problem(
        gws=gws,
        xp=xp,
        element_type=dict(zip(ids, types)),
        team={p: p % 10 for p in ids},
        buy_price={p: 50 for p in ids},
        sell_price={p: 50 for p in squad},
        squad=squad,
        bank=bank,
        free_transfers=free_transfers,
        **kwargs,
    )


def test_no_transfer_when_nothing_is_better():
    sol = opt.solve(make_problem())
    assert sol is not None
    assert all(not w.buys and not w.sells for w in sol.weeks)
    assert len(sol.first.lineup) == 11 and len(sol.first.bench) == 4


def test_clear_upgrade_is_bought_with_free_transfer():
    # Player 9 (DEF, not owned) is projected 8 per week vs 2 for our defenders.
    sol = opt.solve(make_problem({9: [8.0, 8.0, 8.0]}))
    assert sol.first.buys == [9]
    assert sol.first.hits == 0
    assert sol.first.captain == 9


def test_marginal_upgrade_is_not_worth_a_hit():
    # +0.5 per week for three weeks does not pay for a 4-point hit.
    sol = opt.solve(make_problem({9: [2.5, 2.5, 2.5]}, free_transfers=0))
    assert not sol.first.buys


def test_rolls_transfer_for_bigger_gain_later():
    # Player 9 is only good from GW11; with 1 FT now, buy in GW11, not GW10.
    sol = opt.solve(make_problem({9: [2.0, 9.0, 9.0]}))
    assert not sol.first.buys
    assert sol.weeks[1].buys == [9]


def test_lineup_and_captain_use_next_gameweek_only():
    # Player 13 (owned MID) is poor next week but great later: captain must be
    # someone else next week even though 13 has the best horizon total.
    sol = opt.solve(make_problem({13: [0.5, 10.0, 10.0], 12: [6.0, 2.0, 2.0]}))
    first = sol.first
    assert first.captain == 12
    assert 13 in first.bench


def test_triple_captain_only_when_beating_hold_value():
    tc = ("3xc", 1)
    prob = make_problem({12: [12.0, 2.0, 2.0]}, chip_gws={tc: GWS}, chip_hold={tc: 20.0})
    assert opt.solve(prob).first.chip is None
    prob.chip_hold = {tc: 5.0}
    assert opt.solve(prob).first.chip == "3xc"


# A horizon across the chip-set boundary: set 1 runs to GW19, set 2 from GW20.
CROSS = [16, 17, 18, 19, 20, 21]
TC1, TC2 = ("3xc", 1), ("3xc", 20)


def chips_by_gw(sol):
    return {w.gw: w.chip for w in sol.weeks if w.chip}


def test_both_triple_captains_usable_one_per_window():
    # Player 12 (owned MID) has a big week in each window.
    prob = make_problem(
        {12: [2.0, 12.0, 2.0, 2.0, 2.0, 12.0]},
        gws=CROSS,
        chip_gws={TC1: [16, 17, 18, 19], TC2: [20, 21]},
        chip_hold={TC1: 0.0, TC2: 0.0},
    )
    assert chips_by_gw(opt.solve(prob)) == {17: "3xc", 21: "3xc"}


def test_one_chip_instance_is_played_at_most_once():
    # Two big weeks inside set 1's window, but only one set-1 Triple Captain.
    prob = make_problem({12: [2.0, 12.0, 11.0, 2.0, 2.0, 2.0]}, gws=CROSS, chip_gws={TC1: [16, 17, 18, 19]}, chip_hold={TC1: 0.0})
    assert chips_by_gw(opt.solve(prob)) == {17: "3xc"}


def test_chip_is_not_played_outside_its_window():
    # Only set 1 is available, and the big week is GW21: the chip cannot follow it there.
    prob = make_problem({12: [2.0, 2.0, 2.0, 2.0, 2.0, 12.0]}, gws=CROSS, chip_gws={TC1: [16, 17, 18, 19]}, chip_hold={TC1: 0.0})
    assert chips_by_gw(opt.solve(prob)).get(21) is None


def test_each_chip_instance_has_its_own_hold_value():
    # Set 1 expires inside the horizon (hold 0) and is spent; set 2 has a big week
    # later in its window (hold 20) and is kept.
    prob = make_problem(
        {12: [2.0, 12.0, 2.0, 2.0, 2.0, 12.0]},
        gws=CROSS,
        chip_gws={TC1: [16, 17, 18, 19], TC2: [20, 21]},
        chip_hold={TC1: 0.0, TC2: 20.0},
    )
    assert chips_by_gw(opt.solve(prob)) == {17: "3xc"}


def test_one_chip_per_gameweek_across_sets():
    # Both sets in the horizon, Bench Boost and Triple Captain both tempting in GW20:
    # still only one chip that week.
    prob = make_problem(
        {12: [2.0, 2.0, 2.0, 2.0, 12.0, 2.0]},
        gws=CROSS,
        chip_gws={TC2: [20, 21], ("bboost", 20): [20, 21]},
        chip_hold={TC2: 0.0, ("bboost", 20): 0.0},
    )
    chips = chips_by_gw(opt.solve(prob))
    assert chips.get(20) == "3xc" and chips.get(21) == "bboost"


def test_alternative_constraints():
    prob = make_problem({9: [8.0, 8.0, 8.0]})
    roll = opt.solve(prob, opt.Constraints(transfers_first=0))
    assert not roll.first.buys
    other = opt.solve(prob, opt.Constraints(transfers_first=1, exclude_in_sets=[frozenset({9})]))
    assert other.first.buys and 9 not in other.first.buys


def test_chip_hold_values_are_per_instance():
    from fpl_optimizer.plan import chip_hold_values
    from fpl_optimizer.squad import ChipInfo, SquadState

    squad = [1, 2, 4, 5, 6, 7, 8, 10, 11, 12, 13, 14, 16, 17, 18]
    gws = list(range(16, 39))
    xp_all = pd.DataFrame({g: [2.0] * 20 for g in gws}, index=range(1, 21))
    xp_all.loc[12, 25] = 9.0  # the squad's best week after the horizon, in set 2's window
    state = SquadState(
        entry_id=1, entry_name="t", last_deadline=15, next_event=16, players=squad,
        purchase_price={}, selling_price={}, bank=0, free_transfers=1, pending_hit_cost=0,
        chips=[
            ChipInfo("3xc", 1, 19, None),
            ChipInfo("3xc", 20, 38, None),
            ChipInfo("wildcard", 1, 19, 3),  # used: not offered
            ChipInfo("wildcard", 20, 38, None),
        ],
    )
    ptype = {p: t for p, t in zip(range(1, 21), [1] * 3 + [2] * 6 + [3] * 6 + [4] * 5)}
    chip_gws, hold = chip_hold_values(state, xp_all, 21, ptype, {"wildcard": 15.0}, {g: 10 for g in gws})
    assert chip_gws == {TC1: [16, 17, 18, 19], TC2: [20, 21], ("wildcard", 20): [20, 21]}
    assert hold == {TC1: 0.0, TC2: 9.0, ("wildcard", 20): 15.0}


def test_free_hit_from_second_set():
    # Our whole squad blanks in GW20; the set-1 Free Hit cannot reach it, the set-2 one can.
    squad = [1, 2, 4, 5, 6, 7, 8, 10, 11, 12, 13, 14, 16, 17, 18]
    overrides = {p: [2.0, 2.0, 2.0, 2.0, 0.0, 2.0] for p in squad}
    prob = make_problem(
        overrides,
        gws=CROSS,
        chip_gws={("freehit", 1): [19], ("freehit", 20): [20]},
        chip_hold={("freehit", 1): 0.0, ("freehit", 20): 0.0},
    )
    sol = opt.solve(prob)
    assert chips_by_gw(sol) == {20: "freehit"}
    assert {3, 9, 15, 19, 20} <= set(sol.weeks[4].lineup)  # every unowned player starts in the blank week
    assert all(not w.buys for w in sol.weeks[:5])  # without spending transfers on it
