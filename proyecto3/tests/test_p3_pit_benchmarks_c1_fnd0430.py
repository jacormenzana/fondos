"""FND-0430 / P3-1.4: the mechanical C1 rule of scripts/audit/p3/p3_pit_benchmarks.py (pure part, no database, R-7).

Rule (decision card, confirmed 2026-11-18): PROCEED when (a) the gross construction gap R1 - matched pool has its interval below zero and the net gap does not reverse it, or (b) the
champion's annual cost drag exceeds R1's by more than the threshold; CANCEL only when both fail and the Sharpe construction gap lies inside +-delta; otherwise INCONCLUSIVE."""
import sys
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "audit" / "p3"))
sys.path.insert(0, str(ROOT))
import p3_pit_benchmarks as P  # noqa: E402

ARGS = Namespace(delta=0.10, drag_threshold=0.0025)


def gap(point, lo, hi):
    return {"point": point, "ci_low": lo, "ci_high": hi, "prob_positive": 0.5, "n": 100, "n_draws": 5, "draw_sd": 0.0}


def gaps(gross_ret, gross_sharpe, net_ret_point=-0.01):
    base = {"ann_return": gap(0.0, -0.1, 0.1), "sharpe": gap(0.0, -0.1, 0.1)}
    return {"gross": {"construction R1 - matched pool": {"ann_return": gross_ret, "sharpe": gross_sharpe},
                      "caps R2 - matched pool (fresh random each month)": base, "persistence R1 - R2 (mean of the R2 draws)": base},
            "net": {"construction R1 - matched pool": {"ann_return": gap(net_ret_point, -0.02, 0.0), "sharpe": base["sharpe"]}}}


def arms(champion_drag, r1_drag):
    return {"champion_band0": {"annual_cost_drag": champion_drag}, "R1_persistent": {"annual_cost_drag": r1_drag}, "pool_matched": {"annual_cost_drag": r1_drag}}


def test_a_gross_gap_below_zero_with_the_net_not_reversed_proceeds():
    c = P.c1_rule(gaps(gap(-0.013, -0.019, -0.008), gap(-0.18, -0.30, -0.06)), arms(0.0100, 0.0100), ARGS)
    assert c["outcome"] == "PROCEED" and c["a_gross_construction_gap_below_zero_and_net_not_reversed"] and not c["b_exceeds_threshold"]


def test_a_net_gap_that_reverses_the_sign_blocks_branch_a():
    c = P.c1_rule(gaps(gap(-0.013, -0.019, -0.008), gap(0.0, -0.3, 0.3), net_ret_point=+0.004), arms(0.0100, 0.0100), ARGS)
    assert not c["a_gross_construction_gap_below_zero_and_net_not_reversed"] and c["outcome"] == "INCONCLUSIVE"


def test_a_cost_drag_excess_proceeds_even_with_no_construction_gap():
    c = P.c1_rule(gaps(gap(0.0, -0.01, 0.01), gap(0.0, -0.05, 0.05)), arms(0.0096, 0.0024), ARGS)             # 0.72 pp a year of excess drag
    assert c["outcome"] == "PROCEED" and c["b_exceeds_threshold"] and c["b_champion_drag_minus_R1_drag"] > 0.0025


def test_cancel_needs_both_branches_failed_and_the_sharpe_gap_inside_the_margin():
    c = P.c1_rule(gaps(gap(0.0, -0.01, 0.01), gap(0.0, -0.05, 0.05)), arms(0.0100, 0.0090), ARGS)
    assert c["outcome"] == "CANCEL" and c["sharpe_gap_inside_equivalence_margin"]


def test_a_wide_interval_that_is_neither_negative_nor_equivalent_is_inconclusive_and_goes_to_the_owner():
    c = P.c1_rule(gaps(gap(0.0, -0.02, 0.02), gap(0.0, -0.3, 0.3)), arms(0.0100, 0.0090), ARGS)
    assert c["outcome"] == "INCONCLUSIVE" and "owner decides" in c["reading"]


def test_negating_a_gap_flips_the_point_and_swaps_the_interval():
    n = P._neg(gap(0.02, 0.01, 0.04))
    assert (n["point"], n["ci_low"], n["ci_high"], n["prob_positive"]) == (-0.02, -0.04, -0.01, 0.5)
