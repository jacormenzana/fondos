"""FND-0429 / WP5: the ranking-layer test harness (proyecto3/src/pit_selection_test.py). No database, synthetic data (R-7).

Pins: the outcome definition on a hand-computed NAV, the top-quintile rule, the peer-cell collapse, the calibration of the permuted-label control (a null effect passes at about alpha),
the power of the planted-alpha control, and BLINDNESS: the feasibility report and the real-score path never carry a point estimate."""
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from proyecto3.src import pit_selection_test as T  # noqa: E402


# ---- outcomes --------------------------------------------------------------------------------------------------------------------------------------
def _nav(n_months=60, step=0.01, start="2020-01-31"):
    idx = pd.date_range(start, periods=n_months, freq="ME")
    return pd.DataFrame({"AAA": 100.0 * np.cumprod(np.r_[1.0, 1.0 + step * np.sin(np.arange(1, n_months))])}, index=idx)


def test_forward_return_and_volatility_follow_their_definitions():
    nav = _nav(60)
    m = T.monthly_nav(nav)
    as_ord = [int(m.index[40])]
    o = T.outcomes_frame(m, as_ord, fwd_months=12, vol_window=36, min_obs=30)
    r = nav["AAA"].pct_change()
    fwd = nav["AAA"].iloc[52] / nav["AAA"].iloc[40] - 1.0
    vol = r.iloc[5:41].std(ddof=1) * math.sqrt(12.0)                      # the 36 returns ending at month 40
    assert len(o) == 1 and abs(o["fwd"].iloc[0] - fwd) < 1e-12 and abs(o["vol"].iloc[0] - vol) < 1e-12 and abs(o["outcome"].iloc[0] - fwd / vol) < 1e-12


def test_a_missing_month_is_never_filled_and_a_flat_fund_has_no_outcome():
    nav = _nav(60).drop(index=pd.Timestamp("2022-06-30"))                   # one month without NAV
    m = T.monthly_nav(nav)
    assert m.isna().any().any() and len(m) == 60                              # the full ordinal range stays, the hole is NaN
    flat = pd.DataFrame({"F": 100.0}, index=pd.date_range("2020-01-31", periods=60, freq="ME"))
    assert T.outcomes_frame(T.monthly_nav(flat), [int(T.monthly_nav(flat).index[40])]).empty     # volatility 0 < floor: no ratio


def test_the_last_twelve_months_have_no_forward_outcome():
    m = T.monthly_nav(_nav(60))
    assert T.outcomes_frame(m, [int(m.index[55])]).empty


# ---- selection -------------------------------------------------------------------------------------------------------------------------------------
def test_top_mask_takes_ceil_of_a_fifth_of_every_group_by_key():
    g = np.array([0] * 7 + [1] * 10 + [2] * 3)
    key = np.arange(len(g), dtype=float)[::-1].copy()
    m = T.top_mask(g, key)
    assert [int(m[g == k].sum()) for k in (0, 1, 2)] == [2, 2, 1]                     # ceil(0.2 * 7) = 2, ceil(0.2 * 10) = 2, ceil(0.2 * 3) = 1
    first = np.flatnonzero(g == 0)
    assert m[first[0]] and m[first[1]] and not m[first[2]]                             # highest keys come first in each group


# ---- panel and cells --------------------------------------------------------------------------------------------------------------------------------
def _synthetic(n_dates=60, n_fam=120, seed=0, signal=0.0, nature_split=True):
    """Scores and outcomes for `n_fam` families over `n_dates` months in one sub-portfolio. outcome = noise + signal * score."""
    rng = np.random.default_rng(seed)
    rows = []
    base = 12 * 2005 + 0
    nat = np.where(np.arange(n_fam) % 2 == 0, "A", "B") if nature_split else np.full(n_fam, "A")
    for t in range(n_dates):
        score = rng.random(n_fam)
        out = rng.normal(0, 1, n_fam) + signal * score
        for i in range(n_fam):
            rows.append((pd.Timestamp(2005, 1, 31) + pd.offsets.MonthEnd(t), f"ISIN{i:04d}", f"FAM{i:04d}", "Defensiva", nat[i], float(score[i]), True, float(out[i]), 0.1 + 0.001 * i))
    df = pd.DataFrame(rows, columns=["as_of", "isin", "fund_family_id", "subportfolio", "fund_nature", "score_final", "eligible", "outcome", "vol"])
    from shared.period_key import period_ordinals
    outcomes = df.assign(as_ord=np.asarray(period_ordinals(df["as_of"])))[["as_ord", "isin", "vol", "outcome"]]
    return df.drop(columns=["outcome", "vol"]), outcomes, pd.Series(False, index=df["isin"].unique())


def test_the_panel_has_one_row_per_unit_month_and_centres_every_cell():
    units, outcomes, fx = _synthetic(n_dates=24, n_fam=90)
    p = T.build_panel(units, outcomes, fx)
    assert len(p.score) == 24 * 90 and p.n_dates == 24 and p.n_families == 90 and p.valid.all()
    assert p.cell_levels[1] + p.cell_levels[2] + p.cell_levels[3] == pytest.approx(1.0)
    by = pd.DataFrame({"g": p.group_id, "ex": p.excess}).groupby("g")["ex"].median()
    assert by.abs().max() < 0.5                                                          # excess is relative to the cell median, so it centres on zero inside a group


def test_a_small_cell_collapses_to_the_nature_level():
    units, outcomes, fx = _synthetic(n_dates=6, n_fam=20)                                  # 10 families per nature and tercile cells of about 3: below the 15 minimum
    p = T.build_panel(units, outcomes, fx)
    assert p.cell_levels[3] == pytest.approx(1.0) or p.cell_levels[2] > 0 and p.cell_levels[1] == 0.0


def test_the_cheapest_class_of_a_family_is_the_unit():
    s = pd.DataFrame({"as_of": [pd.Timestamp("2020-01-31")] * 3, "subportfolio": ["Defensiva"] * 3, "fund_family_id": ["F1", "F1", "F2"], "isin": ["B", "A", "C"], "eligible": [True, True, True]})
    u = T.pick_units(s, pd.Series({"A": 1.5, "B": 0.4, "C": 2.0}))
    assert sorted(u["isin"]) == ["B", "C"]


# ---- the controls ------------------------------------------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def null_panel():
    units, outcomes, fx = _synthetic(n_dates=48, n_fam=100, seed=7, signal=0.0)
    return T.build_panel(units, outcomes, fx)


def test_a_permuted_label_control_passes_at_about_alpha_on_a_null_world(null_panel):
    boot = T.make_boot(null_panel.n_dates, null_panel.n_families, n_boot=400)
    r = T.permutation_control(null_panel, boot, n_perm=200, alpha=0.05, seed=3)
    assert r["pass_rate"] <= 0.12 and r["null_estimate_sd"] > 0                          # calibrated within noise (200 draws: sd 0.015)


def test_the_planted_alpha_control_detects_a_large_effect_and_not_a_zero_one(null_panel):
    boot = T.make_boot(null_panel.n_dates, null_panel.n_families, n_boot=400)
    se = T.bootstrap_se(T.sums_for(null_panel, T.top_mask(null_panel.group_id, null_panel.score)), boot)["se"]
    strong = T.planted_control(null_panel, boot, delta=12 * se, n_rep=40, seed=5)
    none = T.planted_control(null_panel, boot, delta=0.0, n_rep=60, seed=5)
    assert strong["detection_rate"] >= 0.95 and none["detection_rate"] <= 0.2


def test_a_real_signal_is_visible_in_the_statistic_but_only_through_estimate():
    units, outcomes, fx = _synthetic(n_dates=36, n_fam=120, seed=11, signal=1.5)
    p = T.build_panel(units, outcomes, fx)
    est = T.estimate(T.sums_for(p, T.top_mask(p.group_id, p.score)))
    assert 0.4 < est < 0.8                                                               # expected about 1.5 * (0.9 - 0.5) = 0.6: top-quintile mean score 0.9 against a cell median near 0.5


# ---- blindness and feasibility --------------------------------------------------------------------------------------------------------------------------
def test_feasibility_never_returns_a_point_estimate(null_panel):
    boot = T.make_boot(null_panel.n_dates, null_panel.n_families, n_boot=300)
    r = T.feasibility(null_panel, boot, last_nav_ord=12 * 2026, first_ord=12 * 2005)
    assert not [k for k in r if "est" in k or "mean" in k or "diff" in k] and r["mde_spread"] == pytest.approx((T.z_of(0.95) + T.z_of(0.80)) * r["se_used"])
    assert r["independent_36m_windows"] > 0
    assert "estimate" not in T.bootstrap_se.__code__.co_names                            # the SE routine cannot call the point-estimate function


def test_the_sharpe_difference_feasibility_returns_only_the_interval_width():
    rng = np.random.default_rng(1)
    idx = pd.date_range("2010-01-31", periods=120, freq="ME")
    a, b = pd.Series(rng.normal(0.004, 0.02, 120), index=idx), pd.Series(rng.normal(0.004, 0.02, 120), index=idx)
    r = T.sharpe_difference_feasibility(a, b, pd.Series(0.001, index=idx), n_boot=500)
    assert set(r) == {"n_months", "ci90_half_width", "se", "mde_delta_sharpe"} and r["se"] > 0


def test_wilson_upper_bound_is_above_the_rate():
    assert T.wilson_upper(10, 200) > 0.05 and T.wilson_upper(0, 200) > 0.0


# ---- the verdict path (Amendment 1) -----------------------------------------------------------------------------------------------------------------
def test_forward_max_drawdown_follows_the_path_and_is_nan_with_a_hole():
    idx = pd.date_range("2020-01-31", periods=6, freq="ME")
    nav = pd.DataFrame({"A": [100.0, 110.0, 99.0, 105.0, 120.0, 118.0], "B": [100.0, 101.0, np.nan, 103.0, 104.0, 105.0]}, index=idx)
    m = T.monthly_nav(nav)
    dd = T.forward_max_drawdown(m, [int(m.index[0])], months=3)
    assert dd["A"].iloc[0] == pytest.approx(99.0 / 110.0 - 1.0)                     # peak 110, trough 99 inside the 4-point path
    assert np.isnan(dd["B"].iloc[0])                                                # a missing month: no drawdown, no fill


def test_outcomes_carry_the_forward_drawdown_only_on_request():
    m = T.monthly_nav(_nav(60))
    a = [int(m.index[40])]
    assert "mdd" not in T.outcomes_frame(m, a).columns
    o = T.outcomes_frame(m, a, with_dd=True)
    assert "mdd" in o.columns and (o["mdd"] <= 0).all()


def test_the_nature_free_peers_keep_a_nature_offset_that_the_signed_peers_remove():
    units, outcomes, fx = _synthetic(n_dates=24, n_fam=120, seed=21)
    odd = outcomes["isin"].str[-4:].astype(int) % 2 == 1                              # the B nature
    outcomes = outcomes.assign(outcome=outcomes["outcome"] + np.where(odd, 5.0, 0.0))
    a = T.build_panel(units, outcomes, fx, peers="nature")
    b = T.build_panel(units, outcomes, fx, peers="no_nature")
    fam_odd = np.array([int(f[-4:]) % 2 == 1 for f in units["fund_family_id"]])
    assert abs(np.nanmean(a.excess[fam_odd])) < 0.4                                    # centred inside its own nature
    assert np.nanmean(b.excess[fam_odd]) > 1.5 and np.nanmean(b.excess[~fam_odd]) < -1.5       # the offset stays when the nature is not a peer criterion
    with pytest.raises(ValueError):
        T.build_panel(units, outcomes, fx, peers="other")


def test_the_panel_carries_drawdown_excess_and_the_worst_decile_gap_when_outcomes_have_mdd():
    units, outcomes, fx = _synthetic(n_dates=24, n_fam=90, seed=5)
    rng = np.random.default_rng(1)
    outcomes = outcomes.assign(mdd=-np.abs(rng.normal(0.05, 0.02, len(outcomes))))
    p = T.build_panel(units, outcomes, fx)
    assert p.excess_dd is not None and np.isfinite(p.excess_dd).all() and (p.q10_gap < 0).all()
    r = T.m2_estimate(p, T.make_boot(p.n_dates, p.n_families, n_boot=200))
    assert {"estimate", "se", "lower_bound", "dd_excess_mean", "q10_gap_mean"} <= set(r) and r["lower_bound"] < r["estimate"]


def test_attrition_adjustment_only_lowers_the_estimate_and_can_flip_its_sign():
    rows = T.attrition_adjusted({"estimate": 0.10, "se": 0.04, "q10_gap_mean": -2.0})
    by = {r["rate"]: r for r in rows}
    assert by[0.025]["estimate_adjusted"] == pytest.approx(0.975 * 0.10 - 0.025 * 2.0) and not by[0.025]["flips_sign"]
    assert by[0.05]["estimate_adjusted"] == pytest.approx(0.95 * 0.10 - 0.05 * 2.0) and by[0.05]["flips_sign"]       # 0.095 - 0.100 < 0
    assert all(r["estimate_adjusted"] < 0.10 for r in rows)


def _m2(est, lower, dd=0.0):
    return {"estimate": est, "lower_bound": lower, "se": 0.03, "dd_excess_mean": dd, "q10_gap_mean": -1.0}


def test_the_outcome_rule_branches():
    good = _m2(0.2, 0.1)
    assert T.verdict(good, _m2(0.1, 0.0), _m2(0.18, 0.08), 0.006)["outcome"] == "CONTINUE"
    assert T.verdict(_m2(0.2, -0.01), _m2(0.1, 0.0), _m2(0.2, -0.01), 0.006)["outcome"] == "INCONCLUSIVE"       # positive, lower bound not above zero
    assert T.verdict(_m2(-0.05, -0.1), _m2(-0.1, -0.2), _m2(-0.04, -0.1), 0.006)["outcome"] == "PIVOT"          # not positive; the survivorship adjustment cannot flip it up
    assert T.verdict(good, _m2(-0.1, -0.2), _m2(0.18, 0.08), 0.006)["outcome"] == "INCONCLUSIVE"                # 36-month sign differs
    assert T.verdict(good, _m2(0.1, 0.0), _m2(0.1, -0.05), 0.006)["outcome"] == "INCONCLUSIVE"                  # the nature-free reading changes
    assert T.verdict(_m2(0.2, 0.1, dd=-0.03), _m2(0.1, 0.0), _m2(0.2, 0.1, dd=-0.03), 0.006)["outcome"] == "INCONCLUSIVE"   # drawdown condition fails
    assert T.verdict(good, _m2(0.1, 0.0), _m2(0.18, 0.08), 0.027)["outcome"] == "PIVOT"                          # champion drawdown worse than the pool by more than 2 pp
    v = T.verdict(good, _m2(0.1, 0.0), _m2(0.18, 0.08), 0.006, m1_diff=-0.1)
    assert v["outcome"] == "CONTINUE" and "CONSTRUCTION-LOSS" in v["note"] and T.verdict(good, _m2(0.1, 0.0), _m2(0.18, 0.08), 0.006, m1_diff=0.1)["note"] is None
