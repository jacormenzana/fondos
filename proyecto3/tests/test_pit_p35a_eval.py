"""Package L step F: the pure evaluation pieces of proyecto3/src/pit_p35a_eval.py that apply the adoption rule of PREREG_P3_5A to a variant's monthly series. Synthetic data only (R-7).
Invariants pinned: (1) the episode window is the calendar months of the episode plus 12 months, in the formation-date convention of the pool drawdown (the return of a month belongs to the formation
date one month earlier); (2) the vectorised stressed-window drawdown equals `pit_selection_test.tail_stress_max_dd` window by window; (3) the stress path is a REALISED fund's path, per nature, weighted by
the portfolio's nature shares; (4) the ceiling search with no stress is `pit_crisis_diagnostic.best_static_mix`, the stress can only lower it, and an infeasible stress gives NaN; (5) the non-inferiority
test passes exactly when the lower bound clears the margin; (6) non-crisis months exclude every episode and its recovery."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from proyecto3.src import pit_crisis_diagnostic as D  # noqa: E402
from proyecto3.src import pit_p35a_eval as E  # noqa: E402
from proyecto3.src import pit_selection_test as T  # noqa: E402


def months(n=40, start="2015-01-31"):
    return pd.date_range(start, periods=n, freq="ME")


# ---- basic statistics ---------------------------------------------------------------------------------------------------------------------------------------------------
def test_ann_return_and_max_drawdown_hand_cases():
    assert E.ann_return(np.array([0.01] * 12)) == pytest.approx(1.01 ** 12 - 1)
    assert E.ann_return(np.array([0.10, -0.10])) == pytest.approx((1.1 * 0.9) ** 6 - 1)
    assert E.max_dd(np.array([0.10, -0.20, 0.05])) == pytest.approx(-0.20)         # peak 1.10 -> 0.88
    assert E.max_dd(np.array([-0.05, 0.10])) == pytest.approx(-0.05)               # the starting capital is the first peak
    assert E.max_dd(np.array([0.01, 0.02])) == 0.0
    assert np.isnan(E.max_dd(np.array([0.01, np.nan])))                            # a hole is never filled


def test_the_episode_window_is_the_episode_months_plus_twelve_in_formation_dates():
    first, last = E.episode_formation_range("2015-07-01", "2015-08-31", recovery_months=12)
    assert first == pd.Timestamp("2015-06-30") and last == pd.Timestamp("2016-07-31")
    f2, l2 = E.episode_formation_range("2022-01-01", "2023-01-31", recovery_months=0)
    assert f2 == pd.Timestamp("2021-12-31") and l2 == pd.Timestamp("2022-12-31")
    s = pd.Series(0.0, index=months(80, "2015-01-31"))
    s.loc["2015-08-31"] = -0.10                                                     # the return of September 2015, inside the window
    s.loc["2016-09-30"] = -0.20                                                     # after the window
    assert E.window_dd(s, first, last) == pytest.approx(-0.10)


def test_non_crisis_months_exclude_every_episode_and_its_recovery():
    idx = months(120, "2014-01-31")
    mask = E.crisis_mask(idx, [("2015-07-01", "2015-08-31", 12), ("2018-11-01", "2018-11-30", 0)])
    inside = pd.DatetimeIndex(["2015-06-30", "2015-12-31", "2016-07-31", "2018-10-31"])
    outside = pd.DatetimeIndex(["2015-05-31", "2016-08-31", "2018-11-30", "2014-02-28"])
    assert mask[idx.isin(inside)].all() and not mask[idx.isin(outside)].any()


# ---- stressed windows ------------------------------------------------------------------------------------------------------------------------------------------------------
def test_the_vectorised_stressed_window_dd_equals_the_scalar_function_window_by_window():
    rng = np.random.default_rng(4)
    port = rng.normal(0.004, 0.02, 40)
    stress = rng.normal(-0.01, 0.06, (40, 12))
    got = E.stressed_window_dds(port, stress, 0.10, months=12)
    for i in range(40 - 11):
        idx = months(12)
        want = T.tail_stress_max_dd(pd.Series(port[i:i + 12], index=idx), pd.Series(stress[i], index=idx), 0.10)
        assert got[i] == pytest.approx(want, abs=1e-12)
    assert np.isnan(got[40 - 11:]).all()                                            # no full window left at the end


def test_the_stress_path_is_a_realised_fund_path_per_nature_weighted_by_the_nature_shares():
    at = months(14)
    funds = {"A1": ("Mixtos", 0.01), "A2": ("Mixtos", 0.00), "A3": ("Mixtos", -0.02), "B1": ("Renta Variable", 0.02), "B2": ("Renta Variable", -0.05)}
    R1 = pd.DataFrame({k: v[1] for k, v in funds.items()}, index=at)
    nature = pd.Series({k: v[0] for k, v in funds.items()})
    W = pd.DataFrame(np.nan, index=at, columns=list(funds))
    W.loc[:, "A1"], W.loc[:, "B1"] = 0.6, 0.4                                        # 60 % Mixtos, 40 % Renta Variable
    pool = {t: set(funds) for t in at}
    sp = E.stress_paths(R1, at, nature, pool, W, q=0.0, months=12)                   # q = 0: the WORST realised path of each nature
    assert sp.shape == (14, 12)
    assert sp[0, 0] == pytest.approx(0.6 * -0.02 + 0.4 * -0.05)                      # A3 and B2 are the worst funds of their natures
    assert np.isnan(sp[3:]).all()                                                    # fewer than 12 months of returns left
    assert not np.isnan(sp[:3]).any()


# ---- the ceiling --------------------------------------------------------------------------------------------------------------------------------------------------------------
def sub_series(seed=1, n=120):
    rng = np.random.default_rng(seed)
    return np.column_stack([rng.normal(0.002, 0.004, n), rng.normal(0.004, 0.02, n), rng.normal(0.006, 0.04, n)])


def test_the_unstressed_ceiling_is_best_static_mix_of_the_diagnostic():
    S = sub_series()
    idx = months(len(S), "2010-01-31")
    want = D.best_static_mix(pd.DataFrame(S, index=idx, columns=["Defensiva", "Equilibrada", "Dinamica"]), -0.15, 0.1)
    got = E.ceiling(S, None, 0.10, dd_cap=-0.15, step=0.1)
    assert got["ann"] == pytest.approx(want["ann"]) and tuple(got["mix"]) == pytest.approx((want["Defensiva"], want["Equilibrada"], want["Dinamica"]))
    assert got["dd_unstressed"] >= -0.15 - 1e-12


def test_the_stress_can_only_lower_the_ceiling_and_an_impossible_stress_gives_nan():
    S = sub_series()
    free = E.ceiling(S, None, 0.10)
    mild = np.full((len(S), 12, 3), -0.01)
    stressed = E.ceiling(S, mild, 0.10)
    assert stressed["ann"] <= free["ann"] + 1e-12 and stressed["dd_stressed"] >= -0.15 - 1e-12
    deadly = np.full((len(S), 12, 3), -0.20)                                         # every window loses a fifth in the stressed tenth for every mix
    assert np.isnan(E.ceiling(S, deadly, 0.50)["ann"])


def test_the_ceiling_difference_bootstrap_is_zero_for_identical_series_and_positive_otherwise():
    S = sub_series()
    assert E.ceiling_diff_se_pp(S, S.copy(), n_boot=40) == pytest.approx(0.0, abs=1e-12)
    other = sub_series(seed=2)
    se = E.ceiling_diff_se_pp(S, other, n_boot=60)
    assert np.isfinite(se) and se > 0


# ---- non-inferiority ------------------------------------------------------------------------------------------------------------------------------------------------------------
def test_non_inferiority_passes_exactly_when_the_lower_bound_clears_the_margin():
    flat_up = E.noninferiority(np.full(120, 0.001), level=0.0045)                    # constant +0.1 pp a month: SE 0, mean 1.2 pp a year
    assert flat_up["mean_pp"] == pytest.approx(1.2) and flat_up["se_pp"] == pytest.approx(0.0, abs=1e-9) and flat_up["pass"] is True
    flat_down = E.noninferiority(np.full(120, -0.001), level=0.0045)
    assert flat_down["pass"] is False and flat_down["lb_pp"] == pytest.approx(-1.2)
    tiny = E.noninferiority(np.full(120, -0.00005), level=0.0045)                    # -0.06 pp a year, SE 0: above the -0.10 margin
    assert tiny["pass"] is True
    rng = np.random.default_rng(0)
    noisy = E.noninferiority(rng.normal(0.0, 0.01, 200), level=0.0045)               # mean about zero, a wide interval: the lower bound is below -0.10
    assert noisy["lb_pp"] < noisy["mean_pp"] and noisy["pass"] is False
