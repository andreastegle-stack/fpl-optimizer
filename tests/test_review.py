"""Review logic: decision attribution for transfers, captain and bench."""

from fpl_optimizer.review import autosub, compute, explain_player, lessons, repeated_minutes_misses

GW = 9
# 15 players: 1-2 GK, 3-7 DEF, 8-12 MID, 13-15 FWD. Starters 1,3,4,5,8,9,10,11,13,14,15; bench 2,6,7,12.
TYPES = {1: 1, 2: 1, 3: 2, 4: 2, 5: 2, 6: 2, 7: 2, 8: 3, 9: 3, 10: 3, 11: 3, 12: 3, 13: 4, 14: 4, 15: 4}
ORDER = [1, 3, 4, 5, 8, 9, 10, 11, 13, 14, 15, 2, 6, 7, 12]
META = {p: {"name": f"P{p}", "team": "T", "element_type": t} for p, t in TYPES.items()} | {20: {"name": "P20", "team": "T", "element_type": 3}}


def stats(points, minutes=90, goals=0, assists=0, xg=0.0, xa=0.0, cs=0, conceded=0, bonus=0):
    return {"total_points": points, "minutes": minutes, "goals_scored": goals, "assists": assists,
            "expected_goals": str(xg), "expected_assists": str(xa), "clean_sheets": cs, "goals_conceded": conceded, "bonus": bonus}


def picks(captain=13, chip=None, subs=(), cost=0, bench_pts=0):
    return {
        "active_chip": chip,
        "automatic_subs": [{"element_in": i, "element_out": o} for i, o in subs],
        "entry_history": {"points": 60, "event_transfers_cost": cost, "rank": 1, "overall_rank": 900_000, "total_points": 500, "points_on_bench": bench_pts},
        "picks": [{"element": p, "position": i + 1, "is_captain": p == captain} for i, p in enumerate(ORDER)],
    }


def advice(xp: dict[int, float], captain=13, buys=(), sells=()):
    players = {str(p): {"name": f"P{p}", "xp": {str(GW): xp.get(p, 2.0), str(GW + 1): xp.get(p, 2.0)}} for p in list(TYPES) + [20]}
    lineup = [p for p in ORDER[:11]]
    week = {"lineup": lineup, "bench": ORDER[11:], "captain": captain, "vice": 14, "chip": None, "hits": 0,
            "buys": list(buys), "sells": list(sells), "xp": 50.0}
    return {"players": players, "recommended": {"weeks": [week]}}


def run(live, adv=None, transfers=(), **pk):
    live = {p: live.get(p, stats(2)) for p in META}
    return compute(GW, picks(**pk), 1_000_000, live, list(transfers), adv, {"average_entry_score": 50}, META)


def test_captain_attribution_against_best_and_model():
    live = {13: stats(2), 14: stats(12, goals=2, xg=1.5)}
    r = run(live, advice({13: 5.0, 14: 7.0}), captain=13)
    c = r["captain"]
    assert c["best_name"] == "P14" and c["lost_vs_best"] == 10
    assert c["model_pick"] == "P14" and c["vs_model"] == -10
    assert any("toppvalg" in l for l in r["lessons"])


def test_triple_captain_triples_the_loss():
    live = {13: stats(2), 14: stats(12)}
    r = run(live, advice({}), captain=13, chip="3xc")
    assert r["captain"]["lost_vs_best"] == 20


def test_captain_matching_model_gives_no_lesson_even_if_unlucky():
    live = {13: stats(1, xg=1.2), 14: stats(12)}
    r = run(live, advice({13: 7.0, 14: 4.0}), captain=13)
    assert r["captain"]["vs_model"] == 0
    assert not any("toppvalg" in l for l in r["lessons"])


def test_bench_knowable_vs_hindsight():
    # Model rated bench MID 12 above starter MID 11, and 12 scored 9 vs 1.
    live = {11: stats(1), 12: stats(9)}
    r = run(live, advice({12: 6.0, 11: 1.5, 6: 1.0, 7: 1.0}), bench_pts=9)
    b = r["bench"]
    assert b["hindsight_gain"] == 8
    assert b["model_xi_gain"] == 8 and b["model_xi_expected_gain"] > 0
    assert b["model_changes"] == [{"in": "P12", "out": "P11"}]
    assert any("startellever" in l for l in r["lessons"])


def test_bench_luck_is_not_a_lesson():
    # Bench player scored big but the model also preferred the starter.
    live = {11: stats(1), 12: stats(9)}
    r = run(live, advice({11: 5.0, 12: 2.0}))
    assert r["bench"]["hindsight_gain"] == 8
    assert r["bench"]["model_xi_gain"] == 0
    assert r["lessons"] == []


def test_autosubs_keep_formation():
    xi = ORDER[:11]
    minutes = {p: 90 for p in TYPES} | {3: 0}
    # First bench outfielder is 6 (DEF): replaces DEF 3. Keeper 2 cannot replace an outfielder.
    assert autosub(xi, ORDER[11:], minutes, TYPES) == [1, 6, 4, 5, 8, 9, 10, 11, 13, 14, 15]
    # Only three defenders start: a midfielder may not replace one of them.
    assert autosub(xi, [12], minutes, TYPES) == xi


def test_transfer_expected_gain_and_hit_lesson():
    tr = [{"event": GW, "element_in": 20, "element_out": 11}]
    adv = advice({20: 3.0, 11: 2.5})
    live = {20: stats(8), 11: stats(2)}
    r = run(live, adv, transfers=tr, cost=4)
    t = r["transfers"][0]
    assert t["pts_in"] == 8 and t["pts_out"] == 2
    assert abs(t["exp_gain_horizon"] - 1.0) < 1e-9
    assert any("Minuspoengene" in l for l in r["lessons"])


def test_no_projections_is_labelled():
    r = run({}, None)
    assert r["has_projections"] is False
    assert r["captain"]["model_pick"] is None
    assert "model_xi_gain" not in r["bench"]


def test_explain_separates_luck_from_model():
    assert explain_player(5.0, stats(0, minutes=0), 3) == ("Spilte ikke", "modell")
    assert explain_player(5.0, stats(1, minutes=20), 3)[1] == "modell"
    assert explain_player(5.0, stats(2, xg=0.6, xa=0.2), 3)[1] == "uflaks"
    assert explain_player(3.0, stats(13, goals=2, xg=0.3), 4)[1] == "flaks"
    assert explain_player(4.0, stats(5), 3) == (None, None)


def test_repeated_minutes_misses_need_two_of_three():
    miss = {"name": "P5", "kind": "modell"}
    reviews = [{"gw": 6, "players": [miss]}, {"gw": 7, "players": []}, {"gw": 8, "players": [miss]}]
    assert len(repeated_minutes_misses(reviews)) == 1
    assert repeated_minutes_misses(reviews[1:2]) == []
    recent = [{"gw": 7, "players": [miss]}, {"gw": 8, "players": [miss]}]
    assert len(lessons({"bench": {}, "captain": {}, "hit_cost": 0, "transfers": [], "gw": 9, "players": []}, recent)) == 1


def test_summary_for_email_is_short():
    live = {13: stats(2), 14: stats(12)}
    r = run(live, advice({13: 5.0, 14: 7.0}), captain=13)
    assert 2 <= len(r["summary"]) <= 3
    assert r["summary"][0].startswith("GW9: 60 poeng (snitt 50), plass 1,00 mill → 900 000")
    assert "Kaptein P13: 4 poeng" in r["summary"][1]
