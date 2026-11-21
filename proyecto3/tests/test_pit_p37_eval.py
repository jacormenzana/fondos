"""Package L, P3-7 (doc/decisiones/PREREG_P3_7.md sections 5 and 11): the evaluation of an arm (`proyecto3/src/pit_p37_eval.py`).
Invariants pinned: (1) a pool series is the gross return of its weights minus the stated one-off fees of what it trades and minus 25 bp of its turnover, the same arithmetic as the champion; (2) condition 1 is
RELATIVE under the stress (D3): the unstressed drawdown at 25 bp must be within 15 % and the stressed worst window no deeper than the baseline's by more than 0.5 pp, over the full sample and in each informative
episode; (3) condition 2 needs a Holm-adjusted lower bound above 0 AND a point estimate of at least 0.5 pp a year at the stated fees; (4) condition 3 is the non-crisis degradation and the share of 36-month
windows beating the target; (5) condition 4 needs BOTH matched pools beaten at the arm's own costs (D2) and a pool with a better draw average blocks it; (6) the candidate flag is the conjunction and nothing
else; (7) the Amendment 5 side verdict applies its rules as written (turnover -30 %, drag -0.50 pp, net return and drawdown tolerances, 25 and 100 bp checks) and is separate from the candidate flag.
Synthetic series only (R-7)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto3.src import pit_p37_eval as V  # noqa: E402

N = 120
IDX = pd.date_range("2010-01-31", periods=N, freq="ME")
EPISODES = {"ep1": ("2015-07-01", "2015-08-31"), "ep2": ("2018-01-01", "2018-03-31")}
DESIGN = ("2012-01-01", "2012-12-31")
CRISIS = [("2012-01-01", "2012-12-31", 0), ("2015-07-01", "2015-08-31", 12)]


def make_series(mean=0.004, sd=0.01, seed=1, turnover=0.30, fee_drag=0.002):
    rng = np.random.default_rng(seed)
    gross = pd.Series(mean + rng.normal(0, sd, N), index=IDX)
    tu = pd.Series(turnover, index=IDX)
    return pd.DataFrame({"gross": gross, "turnover": tu, "cash": 0.0005, "net25": gross - 0.0025 * tu, "netfee": gross - fee_drag * tu / 0.30,
                         "pool_net25": gross, "pool_netfee": gross})


def arm(series, stress=None):
    s = np.zeros((N, 12)) if stress is None else stress
    return {"series": series, "stress": {"master_p10": s, "master_p05": s}, "meta": {"holding": {"mean_months": 6.9}}}


def target():
    return pd.Series(0.003, index=IDX)


def evaluate(a, b, pools, name="A1", **kw):
    return V.evaluate_arm(name, a, b, target(), pools, episodes=EPISODES, design=DESIGN, crisis_windows=CRISIS, holm_level=0.05 / 8, **kw)


def pools_for(champ_ann_fee, persistent_mean, redraw_mean, n=20):
    return {"persistent": np.full(n, persistent_mean), "redraw": np.full(n, redraw_mean), "persistent_turnover": 0.2, "redraw_turnover": 0.6, "champion_ann_fee_ref": champ_ann_fee}


# ================================================================ pools ==============================================================================================
def test_a_pool_series_is_gross_minus_fund_by_fund_one_off_fees_and_25bp_of_turnover():
    at = pd.date_range("2020-01-31", periods=3, freq="ME")
    W = pd.DataFrame({"A": [0.5, 0.5, np.nan], "B": [0.5, 0.0, 1.0]}, index=at)
    R1 = pd.DataFrame({"A": [0.01, 0.02, 0.0], "B": [0.03, 0.01, 0.02]}, index=at)
    cash = pd.Series(0.0, index=at)
    entry, exit_ = pd.Series({"A": 0.02, "B": 0.0}), pd.Series({"A": 0.0, "B": 0.01})
    s = V.pool_series(W, R1, cash, entry, exit_, 0.03, 0.0, 25.0)
    assert list(s.columns) == ["gross", "turnover", "net25", "netfee"]
    assert s["net25"].iloc[0] == pytest.approx(s["gross"].iloc[0] - 0.0025 * s["turnover"].iloc[0])
    assert (s["gross"] - s["netfee"]).iloc[0] == pytest.approx(0.5 * 0.02 + 0.5 * 0.0)            # month 1 buys A and B: the entry fee of each, weighted
    assert (s["gross"] - s["netfee"]).iloc[1] > 0                                                   # month 2 sells half of B (exit 1 %) and rebalances A


def test_pool_comparison_reports_the_average_and_the_share_of_draws_beaten():
    c = V.pool_comparison(0.030, np.array([0.01, 0.02, 0.04, 0.05]))
    assert c["pool_mean_ann"] == pytest.approx(0.03) and c["share_draws_beaten"] == 0.5 and c["beats_mean"] is False
    assert V.pool_comparison(0.0301, np.array([0.01, 0.02, 0.04, 0.05]))["beats_mean"] is True
    assert V.pool_comparison(0.02, np.array([np.nan, np.nan]))["beats_mean"] is False              # no usable draw never counts as beaten


# ================================================================ conditions ==========================================================================================
def test_an_arm_equal_to_the_baseline_is_never_a_candidate():
    b = arm(make_series())
    r = evaluate(arm(make_series()), b, pools_for(0.0, -0.5, -0.5))
    assert r["c2"] is False and r["candidate"] is False and r["ret_mean_pp"] == pytest.approx(0.0)


def test_a_clear_and_material_gain_passes_condition_2_and_the_conjunction():
    b = arm(make_series(seed=1))
    g = make_series(seed=1)
    g["netfee"] = g["netfee"] + 0.0020                                           # +2.4 pp a year, a steady paired gain
    g["net25"] = g["net25"] + 0.0020
    g["gross"] = g["gross"] + 0.0020
    r = evaluate(arm(g), b, pools_for(0.0, -0.5, -0.5))
    assert r["ret_mean_pp"] == pytest.approx(2.4, abs=0.05) and r["ret_lb_pp"] > 0 and r["c2"] is True
    assert r["c1"] and r["c3"] and r["c4"] and r["candidate"] is True


def test_a_significant_but_immaterial_gain_fails_the_0_5_pp_floor():
    b = arm(make_series(seed=1))
    g = make_series(seed=1)
    g["netfee"] = g["netfee"] + 0.0002                                           # +0.24 pp: below the materiality floor even if the bound were positive
    r = evaluate(arm(g), b, pools_for(0.0, -0.5, -0.5))
    assert r["ret_lb_pp"] > 0 and r["ret_mean_pp"] < 0.5 and r["c2"] is False


def test_a_material_gain_with_a_lower_bound_below_zero_fails_the_significance_leg():
    noisy = make_series(seed=1)
    g = noisy.copy()
    rng = np.random.default_rng(9)
    noise = rng.normal(0, 0.02, N)
    g["netfee"] = g["netfee"] + 0.0006 + noise                                   # +0.7 pp on average but a very noisy paired difference
    r = evaluate(arm(g), arm(noisy), pools_for(0.0, -0.5, -0.5))
    assert r["ret_mean_pp"] > 0.5 and r["ret_lb_pp"] <= 0 and r["c2"] is False


def test_condition_1_is_relative_under_the_stress():
    b = arm(make_series(seed=1), stress=np.full((N, 12), -0.010))               # the baseline's stressed worst window is deep
    g = make_series(seed=1)
    stress_ok, stress_bad = np.full((N, 12), -0.0101), np.full((N, 12), -0.030)
    ok = evaluate(arm(g, stress=stress_ok), b, pools_for(0.0, -0.5, -0.5))
    bad = evaluate(arm(g, stress=stress_bad), b, pools_for(0.0, -0.5, -0.5))
    assert ok["stress_rel_ok"] is True and bad["stress_rel_ok"] is False
    assert bad["c1"] is False and bad["candidate"] is False
    assert ok["stressed_worst_25bp"] - b_worst(b) > -0.005 and bad["stressed_worst_25bp"] - b_worst(b) < -0.005


def b_worst(b):
    return float(np.nanmin(V.E.stressed_window_dds(b["series"]["net25"].to_numpy(), b["stress"]["master_p10"], V.STRESS_SHARE)))


def test_condition_1_also_needs_the_unstressed_drawdown_within_15_percent():
    b = arm(make_series(seed=1))
    deep = make_series(seed=1)
    deep.loc[deep.index[40:46], "net25"] = -0.06                                 # six months of -6 %: the 25 bp drawdown is deeper than 15 %
    r = evaluate(arm(deep), b, pools_for(0.0, -0.5, -0.5))
    assert r["maxdd_25bp"] < -0.15 and r["c1"] is False


def test_condition_3_catches_a_non_crisis_degradation():
    b = arm(make_series(seed=1))
    g = make_series(seed=1)
    g["netfee"] = g["netfee"] - 0.0010                                           # -1.2 pp a year outside the crisis windows
    r = evaluate(arm(g), b, pools_for(0.0, -0.5, -0.5))
    assert r["noncrisis_degradation_pp"] > 0.35 and r["c3"] is False


def test_condition_4_needs_both_pools_beaten():
    b = arm(make_series(seed=1))
    g = make_series(seed=1)
    g["netfee"] = g["netfee"] + 0.0020
    ann = V.E.ann_return(g["netfee"].to_numpy())
    both = evaluate(arm(g), b, pools_for(ann, ann - 0.01, ann - 0.01))
    persistent_better = evaluate(arm(g), b, pools_for(ann, ann + 0.01, ann - 0.01))
    redraw_better = evaluate(arm(g), b, pools_for(ann, ann - 0.01, ann + 0.01))
    assert both["c4"] is True and persistent_better["c4"] is False and redraw_better["c4"] is False
    assert persistent_better["beat_persistent"] is False and persistent_better["beat_redraw"] is True


def test_the_candidate_flag_is_the_conjunction_of_the_four_conditions():
    b = arm(make_series(seed=1))
    g = make_series(seed=1)
    g["netfee"] = g["netfee"] + 0.0020
    r = evaluate(arm(g), b, pools_for(0.0, 9.0, 9.0))                            # every pool beats the arm: only condition 4 fails
    assert r["c1"] and r["c2"] and r["c3"] and not r["c4"] and r["candidate"] is False


# ================================================================ Amendment 5 side verdict ============================================================================
def a5(s_v, s_b):
    return V.amendment5_verdict(s_v, s_b)


def a5_pair():
    b = make_series(seed=2, turnover=0.32)
    b["netfee"] = b["gross"] - 0.0030                                            # a cost drag of 0.30 % a month
    v = b.copy()
    v["turnover"] = 0.15
    v["netfee"] = v["gross"] - 0.0010                                            # 0.10 % a month: 2.4 pp a year less drag
    v["net25"] = v["gross"] - 0.0025 * 0.15
    return v, b


def test_a_band_that_cuts_turnover_and_cost_passes_amendment_5_as_written():
    v, b = a5_pair()
    r = a5(v, b)
    assert r["turnover_cut"] == pytest.approx(1 - 0.15 / 0.32) and r["rule1"] is True and r["rule2"] is True and r["rule3"] is True and r["pass"] is True


@pytest.mark.parametrize("field,mutate", [
    ("rule1", lambda v, b: v.__setitem__("turnover", b["turnover"] * 0.9)),                       # turnover cut below 30 %
    ("rule2", lambda v, b: v.__setitem__("netfee", b["netfee"] - 0.0003)),                          # net return 0.36 pp below the baseline, beyond the 0.10 tolerance
    ("rule3", lambda v, b: v.__setitem__("net25", b["net25"] - 0.0003)),                            # 25 bp net return below the baseline by more than 0.15 pp
])
def test_amendment_5_fails_on_each_rule_it_states(field, mutate):
    v, b = a5_pair()
    assert a5(v, b)[field] is True
    mutate(v, b)
    r = a5(v, b)
    assert r[field] is False and r["pass"] is False


def test_the_amendment_5_drawdown_rule_is_not_worse_when_the_baseline_is_already_beyond_the_cap():
    b = make_series(seed=2, turnover=0.32)
    b["netfee"] = b["gross"] - 0.0030
    b.loc[b.index[30:40], "netfee"] = -0.04                                       # the baseline's drawdown at the stated fees is far beyond 15 %
    v = b.copy()
    v["turnover"] = 0.15
    v["netfee"] = b["netfee"] + 0.0005
    assert V.E.max_dd(b["netfee"].to_numpy()) < -0.15
    assert a5(v, b)["rule2"] is True                                              # not worse than the baseline: the absolute cap does not apply
    v["netfee"] = b["netfee"] - 0.0003
    assert a5(v, b)["rule2"] is False
