"""Small synthetic problems that pin down the planner's behaviour."""

import pandas as pd

from fpl_optimizer import optimizer as opt

GWS = [10, 11, 12]


def make_problem(xp_overrides=None, free_transfers=1, bank=0, **kwargs):
    # 20 players over 10 clubs: 3 GK, 6 DEF, 6 MID, 5 FWD; all cost 5.0m.
    types = [1] * 3 + [2] * 6 + [3] * 6 + [4] * 5
    ids = list(range(1, len(types) + 1))
    xp = pd.DataFrame({w: [2.0] * len(ids) for w in GWS}, index=ids)
    for p, vals in (xp_overrides or {}).items():
        xp.loc[p] = vals
    squad = [1, 2, 4, 5, 6, 7, 8, 10, 11, 12, 13, 14, 16, 17, 18]
    return opt.Problem(
        gws=GWS,
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
    prob = make_problem({12: [12.0, 2.0, 2.0]}, chip_gws={"3xc": GWS}, chip_hold={"3xc": 20.0})
    assert opt.solve(prob).first.chip is None
    prob.chip_hold = {"3xc": 5.0}
    assert opt.solve(prob).first.chip == "3xc"


def test_alternative_constraints():
    prob = make_problem({9: [8.0, 8.0, 8.0]})
    roll = opt.solve(prob, opt.Constraints(transfers_first=0))
    assert not roll.first.buys
    other = opt.solve(prob, opt.Constraints(transfers_first=1, exclude_in_sets=[frozenset({9})]))
    assert other.first.buys and 9 not in other.first.buys
