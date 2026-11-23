"""Package L, feasibility study F of the drawdown-management RFC (PREREG_P3_10_FEAS): pure pieces of proyecto3/src/pit_cdar.py on synthetic data.

Every test here uses made-up monthly returns; nothing reads the sealed run (R-7: no pipeline, no database).
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from proyecto3.src import pit_cdar as C
from proyecto3.src import pit_crisis_diagnostic as D
from proyecto3.src import ranking_levers as RL


def _idx(n, start="2000-01-31"):
    return pd.date_range(start, periods=n, freq="ME")


# --------------------------------------------------------------------------------------------------------------------------- CDaR
def test_cdar_hand_computed():
    # levels: 1.0, 1.1, 0.99, 0.891, 1.1 ... returns +10 %, -10 %, -10 %, +23.45 %
    r = pd.Series([0.10, -0.10, -0.10, 0.2345679])
    cum = (1 + r).cumprod()
    d = cum / cum.cummax() - 1.0                   # 0, -0.1, -0.19, ~0.0
    assert C.cdar(r, 0.5) == pytest.approx(np.sort(d.to_numpy())[:2].mean())     # worst 2 of 4 at alpha 0.5
    assert C.cdar(r, 0.75) == pytest.approx(d.min())                              # worst 1 of 4


def test_cdar_is_at_most_the_mean_drawdown_and_at_least_the_max_drawdown():
    rng = np.random.default_rng(1)
    r = pd.Series(rng.normal(0.004, 0.03, 200))
    cum = (1 + r).cumprod()
    d = cum / cum.cummax() - 1.0
    assert d.min() <= C.cdar(r, 0.90) <= C.cdar(r, 0.50) <= 0.0


# ------------------------------------------------------------------------------------------------------------ stress months
def test_stress_months_matches_l3_pool_state_arithmetic():
    rng = np.random.default_rng(2)
    r = pd.Series(rng.normal(0.0, 0.025, 120), index=_idx(120))
    s = C.stress_months(r)
    for i in range(11, 120):
        want = RL.pool_state(r.iloc[i - 11:i + 1]) in ("stress", "recovery")
        assert bool(s.iloc[i]) == want, i


def test_stress_months_needs_a_full_window_and_never_looks_ahead():
    r = pd.Series([-0.05] * 6 + [0.0] * 20, index=_idx(26))
    s = C.stress_months(r)
    assert not s.iloc[:11].any()                         # fewer than 12 returns: no stress, never a guess
    r2 = r.copy()
    r2.iloc[15:] = 0.5                                   # change the FUTURE of month 14
    assert bool(C.stress_months(r2).iloc[14]) == bool(s.iloc[14])
    assert bool(s.iloc[11])                              # the first full window of a -5 % run IS stress ...
    r3 = r.copy()
    r3.iloc[3] = np.nan                                  # ... and a hole in the window withdraws the call instead of guessing
    assert not C.stress_months(r3).iloc[11]


def test_stress_episodes_are_runs_of_consecutive_months():
    s = pd.Series([False, True, True, False, True, False, False, True, True, True], index=_idx(10))
    ids = C.stress_episodes(s)
    assert ids.tolist() == [0, 1, 1, 0, 2, 0, 0, 3, 3, 3]
    first = C.stress_episodes(pd.Series([True, True, False, True], index=_idx(4)))
    assert first.tolist() == [1, 1, 0, 2]                # a series that STARTS in stress opens episode 1


def test_gap_tolerance_merges_runs_separated_by_a_short_rebound():
    s = pd.Series([True, False, True, False, False, True, False, False, False, True], index=_idx(10))
    assert C.stress_episodes(s).tolist() == [1, 0, 2, 0, 0, 3, 0, 0, 0, 4]            # default: consecutive only
    assert C.stress_episodes(s, gap=1).tolist() == [1, 0, 1, 0, 0, 2, 0, 0, 0, 3]     # a one-month rebound does not split an episode
    assert C.stress_episodes(s, gap=2).tolist() == [1, 0, 1, 0, 0, 1, 0, 0, 0, 2]
    assert C.stress_episodes(s, gap=3).tolist() == [1, 0, 1, 0, 0, 1, 0, 0, 0, 1]
    assert C.stress_episodes(pd.Series([False, True, True], index=_idx(3)), gap=2).tolist() == [0, 1, 1]


# ----------------------------------------------------------------------------------------------- convex candidates
def _panel(n=90, seed=3):
    rng = np.random.default_rng(seed)
    idx = _idx(n)
    pool = pd.Series(rng.normal(0.003, 0.02, n), index=idx)
    pool.iloc[60:66] = -0.04                                           # a stress run inside the window ending at month 80
    stress = C.stress_months(pool)
    bleed = 0.1 * pool + np.where(stress.to_numpy(), -0.03, 0.04)      # beta ~ 0.1, total return > 0, but it loses MORE than the pool in the stress months
    cols = {
        "HEDGE": -0.5 * pool + 0.003 + rng.normal(0, 0.002, n),         # negative beta, beats the pool in stress, positive total
        "SAME": pool + rng.normal(0, 0.002, n),                         # beta ~ 1: not a candidate
        "DECLINER": 0.0 * pool - 0.004 + rng.normal(0, 0.001, n),       # beta ~ 0 but loses every month: NOT a candidate (owner point 1)
        "BLEED": bleed,                                                 # passes beta and total return, fails only the E[r | stress] > E[pool | stress] condition
    }
    return pd.DataFrame(cols, index=idx), pool, stress


def test_only_the_convex_fund_is_a_candidate_and_a_chronic_decliner_is_not():
    R, pool, stress = _panel()
    t = R.index[80]
    assert stress.iloc[60:80].sum() >= C.MIN_STRESS
    assert C.convex_candidates(R, pool, stress, t) == ["HEDGE"]
    # BLEED is built to pass every condition but one: check the preconditions here so the test cannot pass for another reason
    win = slice(21, 81)
    m = stress.iloc[win].to_numpy()
    p, b = pool.iloc[win].to_numpy(), R["BLEED"].iloc[win].to_numpy()
    assert np.prod(1 + b) - 1 > 0                                                  # total return positive
    assert np.cov(b[m], p[m])[0, 1] / np.var(p[m], ddof=1) <= C.BETA_MAX         # stress beta low
    assert b[m].mean() < p[m].mean()                                              # but it bleeds more than the pool in stress


def test_candidate_needs_the_minimum_of_stress_months():
    R, pool, stress = _panel()
    t = R.index[80]
    assert C.convex_candidates(R, pool, stress, t, min_stress=int(stress.iloc[21:81].sum()) + 1) == []


def test_candidate_needs_a_full_window():
    R, pool, stress = _panel()
    R2 = R.copy()
    R2.iloc[:15, 0] = np.nan                                           # HEDGE has no return in rows 0..14: the window of rows 21..80 is full, the one of rows 11..70 is not
    assert C.convex_candidates(R2, pool, stress, R.index[80]) == ["HEDGE"]
    assert "HEDGE" not in C.convex_candidates(R2, pool, stress, R.index[70])
    R3 = R.copy()
    R3.iloc[40:42, 0] = np.nan                                         # a hole inside the window: not evaluable
    assert "HEDGE" not in C.convex_candidates(R3, pool, stress, R.index[80])


def test_candidates_at_t_do_not_change_when_the_future_changes():
    R, pool, stress = _panel()
    t = R.index[80]
    before = C.convex_candidates(R, pool, stress, t)
    R2, p2 = R.copy(), pool.copy()
    R2.iloc[81:] = 9.0
    p2.iloc[81:] = -0.9
    assert C.convex_candidates(R2, p2, C.stress_months(p2), t) == before


# --------------------------------------------------------------------------------------------------------- exclusion
def test_classify_exclusion_separates_dd_only_short_and_other():
    reason = pd.Series(["max_drawdown", "max_drawdown", "short_max_drawdown_6m", "return_ann_real", "eligible", "nav_stale"], index=list("abcdef"))
    f_dd = pd.Series([True, True, False, False, False, False], index=list("abcdef"))
    f_other = pd.Series([False, True, False, True, False, False], index=list("abcdef"))
    out = C.classify_exclusion(reason, f_dd, f_other)
    assert out.to_dict() == {"a": "DD_ONLY", "b": "OTHER", "c": "SHORT", "d": "OTHER", "e": "ELIGIBLE", "f": "OTHER"}
    # an unknown verdict is never read as a clean one: a fund missing from f_other or from f_dd is not DD_ONLY
    r2 = pd.Series(["max_drawdown", "max_drawdown"], index=["x", "y"])
    got = C.classify_exclusion(r2, pd.Series([True], index=["x"]), pd.Series([False], index=["y"]))
    assert got.to_dict() == {"x": "OTHER", "y": "OTHER"}


# -------------------------------------------------------------------------------------------------------- F1b gate
def _obs(values, clusters):
    return pd.DataFrame({"d": values, "cluster": clusters})


def test_bootstrap_lower_bound_is_below_the_mean_and_deterministic():
    d = [0.01, 0.02, -0.005, 0.015, 0.03, 0.0, 0.01, 0.02]
    c = [1, 1, 2, 2, 3, 3, 4, 4]
    a = C.cluster_bootstrap(_obs(d, c))
    assert a["lb"] <= a["mean"] and a["n_clusters"] == 4
    # determinism: with 30 clusters the bound is continuous, so an unseeded generator would change it between two calls (with 4 clusters the few distinct resamples can coincide by luck)
    rng = np.random.default_rng(11)
    big = _obs(rng.normal(0.01, 0.03, 90), np.repeat(np.arange(30), 3))
    assert C.cluster_bootstrap(big) == C.cluster_bootstrap(big)
    assert C.cluster_bootstrap(big, seed=1)["lb"] != C.cluster_bootstrap(big, seed=2)["lb"]


def test_verdict_with_few_episodes_needs_the_sign_count():
    # 4 episodes: pooled mean positive but only 2 of 4 episodes positive -> sign count 50 % < 75 % -> not passed even with a positive bound
    rows = _obs([0.05, 0.05, -0.01, -0.01, 0.05, 0.05, -0.01, -0.01], [1, 1, 2, 2, 3, 3, 4, 4])
    v = C.persistence_verdict(rows, n_candidate_cells=8)
    assert v["evaluable"] and v["n_clusters"] == 4 and v["sign_count"] == 2
    assert v["passed"] is False
    good = _obs([0.02] * 8, [1, 1, 2, 2, 3, 3, 4, 4])
    assert C.persistence_verdict(good, n_candidate_cells=8)["passed"] is True


def test_sign_count_alone_can_fail_a_positive_bound():
    # 7 episodes (< 8): 5 clearly positive, 2 slightly negative -> the bound is positive but only 5 / 7 = 71 % < 75 % of the episodes are positive
    d = [0.05] * 5 + [-0.0001] * 2
    c = list(range(1, 6)) + [6, 7]
    v = C.persistence_verdict(_obs(d, c), n_candidate_cells=7)
    assert v["lb"] > 0 and v["mean"] > 0 and v["sign_count"] == 5 and v["passed"] is False
    d2 = [0.05] * 6 + [-0.0001]
    v2 = C.persistence_verdict(_obs(d2, list(range(1, 8))), n_candidate_cells=7)
    assert v2["sign_count"] == 6 and v2["passed"] is True                       # 6 / 7 = 86 %


def test_with_many_episodes_the_lower_bound_decides():
    noisy = _obs([0.03, -0.02] * 5, list(range(1, 11)))                          # mean +0.005 but the bound is below 0
    v = C.persistence_verdict(noisy, n_candidate_cells=10)
    assert v["mean"] > 0 and v["lb"] <= 0 and v["n_clusters"] == 10 and v["passed"] is False
    clean = C.persistence_verdict(_obs([0.02] * 10, list(range(1, 11))), n_candidate_cells=10)
    assert clean["lb"] > 0 and clean["passed"] is True                          # 10 episodes: no sign-count rule, the bound decides


def test_verdict_is_not_evaluable_when_more_than_30_percent_is_dropped():
    rows = _obs([0.02] * 6, [1, 1, 2, 2, 3, 3])
    v = C.persistence_verdict(rows, n_candidate_cells=10)                  # 4 of 10 cells dropped = 40 %
    assert v["evaluable"] is False and v["passed"] is False and v["dropped_share"] == pytest.approx(0.4)
    ok = C.persistence_verdict(rows, n_candidate_cells=8)                  # 2 of 8 = 25 %
    assert ok["evaluable"] is True


def test_pairing_falls_back_cell_then_sub_then_drops_and_reports_the_level():
    cell = pd.Series({"c1": "D|RF", "c2": "D|RF", "k1": "D|RF", "k2": "D|MX", "c3": "E|MX", "z": "E|MX"})
    sub = pd.Series({"c1": "D", "c2": "D", "k1": "D", "k2": "D", "c3": "E", "z": "E"})
    X = pd.Series({"c1": 0.04, "c2": 0.02, "k1": 0.01, "k2": 0.00, "c3": 0.05, "z": np.nan})
    rows = C.paired_differences(["c1", "c2", "c3"], ["c1", "c2", "k1", "k2", "c3", "z"], cell, sub, X)
    by_cell = {r["cell"]: r for r in rows}
    assert by_cell["D|RF"]["level"] == "cell" and by_cell["D|RF"]["d"] == pytest.approx(0.03 - 0.01)
    assert "E|MX" not in by_cell                                         # the only control (z) has no forward excess, the sub has no other fund: dropped
    rows2 = C.paired_differences(["c1", "c2"], ["c1", "c2", "k2"], cell, sub, X)     # no control in D|RF, one in sub D
    assert rows2[0]["level"] == "sub" and rows2[0]["d"] == pytest.approx(0.03 - 0.0)


def test_forward_excess_uses_only_the_stress_months_after_t_and_labels_the_first_one():
    idx = _idx(10)
    R = pd.DataFrame({"A": [0.0] * 10, "B": [0.0] * 10}, index=idx)
    pool = pd.Series(0.0, index=idx)
    R.iloc[3, 0], pool.iloc[3] = 0.05, 0.01                     # month 3: A beats the pool by 0.04
    R.iloc[5, 0], pool.iloc[5] = -0.01, -0.03                   # month 5: A beats the pool by 0.02
    R.iloc[6, 1] = np.nan                                       # B has no return in month 6
    R.iloc[4, 0], pool.iloc[4] = 0.9, 0.0                       # month 4 is NOT a stress month: it must not count
    stress = pd.Series(False, index=idx)
    stress.iloc[[3, 5, 6]] = True
    x, first = C.forward_stress_excess(R, pool, stress, idx[1], horizon=6)
    assert first == 3
    assert x["A"] == pytest.approx((0.04 + 0.02 + 0.0) / 3)
    assert x["B"] == pytest.approx((-0.01 + 0.03) / 2)          # month 6 skipped (NaN); months 3 and 5 count: 0 - 0.01 and 0 - (-0.03)
    none, f0 = C.forward_stress_excess(R, pool, stress, idx[6], horizon=3)
    assert f0 == 0 and none.isna().all()


def test_design_power_counts_stress_months_and_episodes():
    s = pd.Series([False] * 3 + [True] * 4 + [False] * 2 + [True] * 2 + [False] * 3 + [True] * 5, index=_idx(19))
    p = C.design_power(s, window=10, min_stress=6)
    assert p["n_stress_months"] == 11 and p["n_episodes"] == 3 and p["episode_lengths"] == [4, 2, 5]
    assert p["enough_episodes"] is False                          # 3 < MIN_EPISODES
    merged = C.design_power(s, window=10, min_stress=6, gap=2)    # gaps of 2 and 3 months: only the first two runs (gap 2) merge
    assert merged["n_episodes"] == 2 and merged["episode_lengths"] == [6, 5] and merged["n_stress_months"] == 11
    assert C.design_power(pd.Series([True, False] * 9 + [True], index=_idx(19)), window=10, min_stress=6)["enough_episodes"] is True


def test_thin_window_puts_the_column_in_cash_below_the_minimum():
    idx = _idx(3)
    out = C.thin_window({idx[0]: ["a", "b"], idx[1]: ["a", "b", "c"], idx[2]: []}, min_candidates=3)
    assert out == {idx[0]: [], idx[1]: ["a", "b", "c"], idx[2]: []}


# ----------------------------------------------------------------------------------------- mix optimiser, attribution
def test_optimise_mix_reproduces_the_existing_three_pool_frontier():
    rng = np.random.default_rng(4)
    idx = _idx(150)
    S = pd.DataFrame({"Defensiva": rng.normal(0.002, 0.006, 150), "Equilibrada": rng.normal(0.003, 0.015, 150),
                      "Dinamica": rng.normal(0.004, 0.03, 150)}, index=idx)
    want = D.best_static_mix(S, -0.15)
    got = C.optimise_mix(S, -0.15)
    assert got["ann"] == pytest.approx(want["ann"], abs=1e-12)
    assert got["max_dd"] == pytest.approx(want["max_dd"], abs=1e-12)
    for c in S.columns:
        assert got["weights"][c] == pytest.approx(want[c])


def test_the_drawdown_cap_binds_in_the_optimiser():
    rng = np.random.default_rng(9)
    idx = _idx(150)
    S = pd.DataFrame({"Defensiva": rng.normal(0.002, 0.006, 150), "Equilibrada": rng.normal(0.004, 0.02, 150),
                      "Dinamica": rng.normal(0.012, 0.07, 150)}, index=idx)
    free = C.optimise_mix(S, -0.99)
    capped = C.optimise_mix(S, -0.15)
    assert free["max_dd"] < -0.15                       # the cap really binds on this data
    assert capped["max_dd"] >= -0.15 and capped["ann"] < free["ann"]


def test_optimise_mix_with_a_fourth_column_never_does_worse_than_without():
    rng = np.random.default_rng(5)
    idx = _idx(150)
    S = pd.DataFrame({"Defensiva": rng.normal(0.002, 0.006, 150), "Equilibrada": rng.normal(0.003, 0.015, 150),
                      "Dinamica": rng.normal(0.004, 0.03, 150), "Convex": rng.normal(0.001, 0.01, 150)}, index=idx)
    assert C.optimise_mix(S, -0.15)["ann"] >= C.optimise_mix(S[["Defensiva", "Equilibrada", "Dinamica"]], -0.15)["ann"] - 1e-12


def test_active_plus_cash_equals_the_total_gain_exactly():
    rng = np.random.default_rng(6)
    idx = _idx(120)
    r_new = pd.Series(rng.normal(0.004, 0.01, 120), index=idx)
    r_front = pd.Series(rng.normal(0.003, 0.01, 120), index=idx)
    state = pd.Series(np.where(rng.random(120) < 0.6, "ACTIVE", "CASH"), index=idx)
    g = C.attribute_gain(r_new, r_front, state)
    assert g["ACTIVE"] + g["CASH"] == pytest.approx(g["total"], abs=1e-12)
    assert g["total"] == pytest.approx(12 * (r_new - r_front).mean(), abs=1e-12)


def test_an_all_cash_basket_has_zero_active_gain():
    idx = _idx(24)
    r_new = pd.Series(0.01, index=idx)
    r_front = pd.Series(0.004, index=idx)
    g = C.attribute_gain(r_new, r_front, pd.Series("CASH", index=idx))
    assert g["ACTIVE"] == 0.0 and g["CASH"] == pytest.approx(g["total"])


def test_brinson_split_sums_to_the_monthly_gain_each_month():
    rng = np.random.default_rng(7)
    idx = _idx(60)
    S = pd.DataFrame(rng.normal(0.003, 0.012, (60, 4)), index=idx, columns=["Defensiva", "Equilibrada", "Dinamica", "Convex"])
    w_front = pd.Series({"Defensiva": 0.4, "Equilibrada": 0.4, "Dinamica": 0.2})
    w_new = pd.Series({"Defensiva": 0.3, "Equilibrada": 0.3, "Dinamica": 0.2, "Convex": 0.2})
    sleeve, realloc = C.brinson_split(S, w_new, w_front)
    r_new = S[list(w_new.index)] @ w_new
    r_front = S[list(w_front.index)] @ w_front
    assert (sleeve + realloc - (r_new - r_front)).abs().max() < 1e-12
    # the naive sleeve term WITHOUT the "- R_front" base does not close the identity (the owner's erratum)
    naive = w_new["Convex"] * S["Convex"]
    assert (naive + realloc - (r_new - r_front)).abs().max() > 1e-4


# ----------------------------------------------------------------------------------------------------- baskets
def _basket_world():
    idx = _idx(12)
    R = pd.DataFrame({"A": 0.01, "B": 0.02, "C": -0.01}, index=idx)
    cash = pd.Series(0.001, index=idx)
    fee = pd.Series({"A": 0.02, "B": 0.0, "C": 0.01})
    return idx, R, cash, fee


def test_basket_is_cash_when_empty_and_pays_entry_and_exit_fees():
    idx, R, cash, fee = _basket_world()
    cands = {idx[0]: [], idx[1]: ["A", "B"], idx[2]: ["A", "B"], idx[3]: []}
    W = C.basket_weights(cands, idx[:4])
    series, state = C.basket_series(W, R, cash, fee, fee)
    # decision at idx[0] -> earns idx[1]: cash, no trade
    assert series[idx[1]] == pytest.approx(0.001) and state[idx[1]] == "CASH"
    # decision at idx[1] -> earns idx[2]: mean(A,B) = 0.015, entry cost 0.5*0.02 + 0.5*0 = 0.01
    assert series[idx[2]] == pytest.approx(0.015 - 0.01) and state[idx[2]] == "ACTIVE"
    # decision at idx[2] unchanged -> earns idx[3] with no trade cost
    assert series[idx[3]] == pytest.approx(0.015)
    # decision at idx[3] empty -> earns idx[4]: cash, exit cost 0.5*0.02 + 0.5*0
    assert series[idx[4]] == pytest.approx(0.001 - 0.01) and state[idx[4]] == "CASH"


def test_a_missing_fee_is_refused_never_filled_with_zero():
    idx, R, cash, fee = _basket_world()
    W = C.basket_weights({idx[0]: ["A", "B"]}, idx[:1])
    with pytest.raises(ValueError, match="entry fee"):
        C.basket_series(W, R, cash, fee.drop("A"), fee)
    with pytest.raises(ValueError, match="exit fee"):
        C.basket_series(W, R, cash, fee, fee.drop("B"))
    nan_fee = fee.copy()
    nan_fee["A"] = np.nan
    with pytest.raises(ValueError, match="entry fee"):
        C.basket_series(W, R, cash, nan_fee, fee)


def test_quarterly_membership_trades_no_more_than_monthly():
    idx, R, cash, fee = _basket_world()
    cands = {t: (["A", "B"] if i % 2 == 0 else ["B", "C"]) for i, t in enumerate(idx[:-1])}
    Wm = C.basket_weights(cands, idx[:-1], "monthly")
    Wq = C.basket_weights(cands, idx[:-1], "quarterly")
    def traded(W):
        return W.diff().abs().fillna(W.abs()).to_numpy().sum()
    assert traded(Wq) <= traded(Wm)
    assert (Wq.iloc[1] == Wq.iloc[0]).all() and (Wq.iloc[2] == Wq.iloc[0]).all()     # carried inside the quarter


def test_random_basket_draws_stay_inside_the_excluded_set_and_keep_sizes():
    idx = _idx(6)
    letters = list("abcdefghij")
    excluded = {t: letters[i:i + 5] for i, t in enumerate(idx)}              # the set CHANGES every month: a draw from the whole history would leave it
    observed = {idx[0]: [], idx[1]: ["b", "c"], idx[2]: ["c", "d", "e"], idx[3]: [], idx[4]: ["e"], idx[5]: ["f", "g"]}
    rng = np.random.default_rng(0)
    for _ in range(50):
        draw = C.random_candidates(observed, excluded, rng)
        for t in idx:
            assert len(draw[t]) == len(observed[t]) and set(draw[t]) <= set(excluded[t]) and len(set(draw[t])) == len(draw[t])


def test_null_is_scored_on_the_observed_active_months_only():
    """A null that scored the TOTAL gain would count the cash months: with an all-cash observed state the null ACTIVE gain is exactly 0."""
    rng = np.random.default_rng(8)
    idx = _idx(80)
    S3 = pd.DataFrame(rng.normal(0.003, 0.012, (80, 3)), index=idx, columns=["Defensiva", "Equilibrada", "Dinamica"])
    basket = pd.Series(rng.normal(0.012, 0.002, 80), index=idx)                  # a good basket: the optimiser really uses the fourth column
    state_cash = pd.Series("CASH", index=idx)
    out = C.gain_by_state(S3, basket, state_cash, dd_limit=-0.30)
    assert out["total"] > 0.01 and out["weights"]["Convex"] > 0                   # the gain is real, so the state split is not trivially zero
    r_new, r_front = out["r_new"], out["r_front"]                                 # the series behind the numbers, for the stressed drawdown
    assert (1 + r_new).prod() ** (12 / len(r_new)) - 1 == pytest.approx(out["ann_new"], abs=1e-12)
    assert (1 + r_front).prod() ** (12 / len(r_front)) - 1 == pytest.approx(out["ann_front"], abs=1e-12)
    assert out["ACTIVE"] == 0.0
    state_act = pd.Series("ACTIVE", index=idx)
    out2 = C.gain_by_state(S3, basket, state_act, dd_limit=-0.30)
    assert out2["CASH"] == 0.0 and out2["ACTIVE"] == pytest.approx(out2["total"])


def test_reach_counts_months_with_enough_funds_after_the_manager_cap():
    idx = _idx(3)
    cands = {idx[0]: ["a", "b", "c"], idx[1]: ["a", "b", "c"], idx[2]: ["a", "b"]}
    mgr = pd.Series({"a": "M1", "b": "M1", "c": "M1"})                     # all three share one manager: at most 2 admissible
    assert C.reach(cands, mgr, max_per_manager=2, min_funds=3) == 0.0
    mgr2 = pd.Series({"a": "M1", "b": "M2", "c": "M3"})
    assert C.reach(cands, mgr2, max_per_manager=2, min_funds=3) == pytest.approx(2 / 3)


# ---------------------------------------------------------------------------------------- sets, observations, basket variants
def test_excluded_sets_need_no_eligible_row_anywhere_and_all_rows_in_class():
    t = pd.Timestamp("2010-01-31")
    rows = pd.DataFrame([
        # a: DD_ONLY in both of its subs -> set (a) and (b)
        (t, "Defensiva", "a", "Mixtos", False, "DD_ONLY"), (t, "Equilibrada", "a", "Mixtos", False, "DD_ONLY"),
        # b: DD_ONLY in one sub, SHORT in the other -> (b) only
        (t, "Defensiva", "b", "Mixtos", False, "DD_ONLY"), (t, "Equilibrada", "b", "Mixtos", False, "SHORT"),
        # c: eligible in one sub -> already available, in neither set
        (t, "Defensiva", "c", "Mixtos", False, "DD_ONLY"), (t, "Equilibrada", "c", "Mixtos", True, "ELIGIBLE"),
        # d: DD_ONLY in one sub, OTHER in the other -> in neither
        (t, "Defensiva", "d", "Mixtos", False, "DD_ONLY"), (t, "Equilibrada", "d", "Mixtos", False, "OTHER"),
        # e: the sealed `eligible` flag says eligible although the class says DD_ONLY (inconsistent input): the flag wins, the fund is already available
        (t, "Defensiva", "e", "Mixtos", True, "DD_ONLY"),
    ], columns=["as_of", "sub", "isin", "nature", "eligible", "cls"])
    a, b = C.excluded_sets(rows)
    assert a[t] == ["a"] and b[t] == ["a", "b"]


def test_primary_cells_use_the_first_sub_that_maps_the_nature():
    rows = pd.DataFrame({"isin": ["x", "x", "y"], "sub": ["Equilibrada", "Dinamica", "Dinamica"], "nature": ["Mixtos", "Mixtos", "Alternativo"]})
    cell, sub = C.primary_cells(rows, ("Defensiva", "Equilibrada", "Dinamica"))
    assert cell.to_dict() == {"x": "Equilibrada|Mixtos", "y": "Dinamica|Alternativo"} and sub.to_dict() == {"x": "Equilibrada", "y": "Dinamica"}


def _stress_world():
    idx = _idx(40)
    pool = pd.Series(0.0, index=idx)
    stress = pd.Series(False, index=idx)
    stress.iloc[20:24] = True                                               # episode 1 (months 20-23)
    stress.iloc[30:32] = True                                               # episode 2
    R = pd.DataFrame(0.0, index=idx, columns=["c1", "c2", "k1", "k2"])
    R.loc[stress, ["c1", "c2"]] = 0.02                                      # candidates beat the pool by 2 pp in stress
    R.loc[stress, ["k1", "k2"]] = -0.01                                     # controls lose 1 pp
    return idx, R, pool, stress


def test_persistence_observations_pair_candidates_with_controls_by_episode():
    idx, R, pool, stress = _stress_world()
    ep = C.stress_episodes(stress, gap=2)
    cell = pd.Series({"c1": "D|X", "c2": "D|X", "k1": "D|X", "k2": "D|X"})
    sub = pd.Series({"c1": "D", "c2": "D", "k1": "D", "k2": "D"})
    t1, t2 = idx[12], idx[26]                                               # t1 sees episode 1 first, t2 sees episode 2 first
    obs, n_cells, no_fwd = C.persistence_observations(R, pool, stress, ep, {t1: ["c1", "c2"], t2: ["c1", "c2"], idx[35]: ["c1"]},
                                                      {t1: list(R.columns), t2: list(R.columns), idx[35]: list(R.columns)}, cell, sub, [t1, t2, idx[35]])
    assert n_cells == 2 and no_fwd == 1                                     # idx[35] has no stress month ahead: skipped, not dropped
    assert (obs["d"] - 0.03).abs().max() < 1e-12                            # 0.02 - (-0.01)
    assert sorted(obs["cluster"]) == [1, 2] and obs["level"].tolist() == ["cell", "cell"]


def test_persistence_observations_count_a_cell_without_controls_as_dropped():
    idx, R, pool, stress = _stress_world()
    ep = C.stress_episodes(stress, gap=2)
    cell = pd.Series({"c1": "D|X", "k1": "E|Y"})
    sub = pd.Series({"c1": "D", "k1": "E"})
    obs, n_cells, _ = C.persistence_observations(R[["c1", "k1"]], pool, stress, ep, {idx[12]: ["c1"]}, {idx[12]: ["c1", "k1"]}, cell, sub, [idx[12]])
    assert n_cells == 1 and len(obs) == 0                                   # the only other fund is in another sub: no pairing at any level


def test_basket_by_decision_date_aligns_the_return_and_haircuts_only_active_months():
    idx = _idx(8)
    R = pd.DataFrame({"A": 0.01, "B": 0.03}, index=idx)
    cash = pd.Series(0.001, index=idx)
    zero = pd.Series(0.0, index=["A", "B"])
    stress_path = pd.Series(-0.10, index=idx)
    cands = {idx[0]: ["A", "B"], idx[1]: ["A", "B"], idx[2]: ["A", "B"], idx[3]: ["A", "B"], idx[4]: [], idx[5]: ["A"], idx[6]: ["A"]}
    basket, state, W = C.basket_by_decision_date(cands, list(idx[:7]), R, cash, zero, zero, "monthly", min_candidates=2, haircut_share=0.2, stress_path=stress_path)
    # the decision at idx[1] earns the return OF idx[2] and is indexed at idx[1]; mean(A,B) = 0.02, haircut 20 % to -0.10 -> 0.8 * 0.02 + 0.2 * -0.10
    assert basket[idx[1]] == pytest.approx(0.8 * 0.02 + 0.2 * -0.10) and state[idx[1]] == "ACTIVE"
    # idx[4]: empty -> cash, NO haircut; idx[5]: one candidate < min_candidates 2 -> thin window -> cash, NO haircut
    assert basket[idx[4]] == pytest.approx(0.001) and state[idx[4]] == "CASH"
    assert basket[idx[5]] == pytest.approx(0.001) and state[idx[5]] == "CASH"


def test_basket_by_decision_date_refuses_a_missing_stress_path_in_an_active_month():
    idx = _idx(4)
    R = pd.DataFrame({"A": 0.01, "B": 0.03}, index=idx)
    zero = pd.Series(0.0, index=["A", "B"])
    with pytest.raises(ValueError, match="stress path"):
        C.basket_by_decision_date({idx[0]: ["A", "B"], idx[1]: ["A", "B"]}, list(idx[:2]), R, pd.Series(0.0, index=idx), zero, zero, "monthly", 2,
                                  haircut_share=0.2, stress_path=pd.Series([np.nan] * 4, index=idx))
