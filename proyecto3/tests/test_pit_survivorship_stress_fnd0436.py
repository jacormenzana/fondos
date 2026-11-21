"""FND-0436 (Package L step B): survivorship evaluation pieces of proyecto3/src/pit_selection_test.py. Synthetic numbers only (R-7): no database, no pipeline import.
Invariants pinned: (1) `attrition_adjusted` with k = 1 is exactly what it was, and a larger k (the selection propensity of dead funds) only lowers the adjusted values; the share a*k never
exceeds 0.5; (2) `survivorship_breakdown` returns the share at which the adjusted estimate / lower bound reaches zero, consistent with `attrition_adjusted`; (3) the stress path is a REALISED
fund's path (never a pointwise percentile) and a fund with a hole is dropped, never filled; (4) `tail_stress_max_dd` is the maximum drawdown of the whole mixed portfolio, equals the
unstressed one at share 0, only deepens as the share grows when the stress path is worse, and refuses misaligned or non-finite input."""
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from proyecto3.src import pit_selection_test as T  # noqa: E402

M2 = {"estimate": 0.10, "se": 0.04, "q10_gap_mean": -2.0}
Z = T.z_of(0.95)


# ---- attrition_adjusted with the propensity factor k -------------------------------------------------------------------------------------------------
def test_k_equal_one_reproduces_the_unadjusted_rows():
    base = T.attrition_adjusted(M2)
    one = T.attrition_adjusted(M2, k=1.0)
    for a, b in zip(base, one):
        assert a["estimate_adjusted"] == b["estimate_adjusted"] and a["lower_bound_adjusted"] == b["lower_bound_adjusted"] and a["flips_sign"] == b["flips_sign"]


def test_a_larger_k_only_lowers_the_adjusted_values_and_scales_the_share():
    r1 = {r["rate"]: r for r in T.attrition_adjusted(M2, k=1.0)}
    r2 = {r["rate"]: r for r in T.attrition_adjusted(M2, k=2.0)}
    r3 = {r["rate"]: r for r in T.attrition_adjusted(M2, k=3.0)}
    for rate in r1:
        assert r2[rate]["estimate_adjusted"] < r1[rate]["estimate_adjusted"] and r3[rate]["estimate_adjusted"] < r2[rate]["estimate_adjusted"]
        assert r2[rate]["lower_bound_adjusted"] < r1[rate]["lower_bound_adjusted"]
    assert r2[0.05]["share"] == pytest.approx(0.10) and r2[0.05]["estimate_adjusted"] == pytest.approx(0.90 * 0.10 + 0.10 * -2.0)


def test_the_attrition_share_is_bounded_at_one_half_and_k_must_be_positive():
    with pytest.raises(ValueError, match="share"):
        T.attrition_adjusted(M2, rates=(0.30,), k=2.0)                              # 0.6 > 0.5
    T.attrition_adjusted(M2, rates=(0.25,), k=2.0)                                  # exactly 0.5 is allowed
    with pytest.raises(ValueError):
        T.attrition_adjusted(M2, k=0.0)
    with pytest.raises(ValueError):
        T.attrition_adjusted(M2, k=-1.0)


# ---- survivorship_breakdown --------------------------------------------------------------------------------------------------------------------------
def test_the_breakdown_share_is_where_the_adjusted_estimate_and_lower_bound_reach_zero():
    b = T.survivorship_breakdown(M2)
    assert b["estimate_flip"] == pytest.approx(0.10 / (0.10 + 2.0))
    lower = 0.10 - Z * 0.04
    assert b["lower_bound_flip"] == pytest.approx(lower / (lower + 2.0))
    assert b["lower_bound_flip"] < b["estimate_flip"]                                 # the lower bound breaks first
    at_est = T.attrition_adjusted(M2, rates=(b["estimate_flip"],))[0]                # consistency with attrition_adjusted
    at_low = T.attrition_adjusted(M2, rates=(b["lower_bound_flip"],))[0]
    assert at_est["estimate_adjusted"] == pytest.approx(0.0, abs=1e-12)
    assert at_low["lower_bound_adjusted"] == pytest.approx(0.0, abs=1e-12)


def test_the_breakdown_edge_cases():
    assert T.survivorship_breakdown({"estimate": -0.05, "se": 0.04, "q10_gap_mean": -2.0})["estimate_flip"] == 0.0     # already not positive
    assert T.survivorship_breakdown({"estimate": 0.05, "se": 0.04, "q10_gap_mean": -2.0})["lower_bound_flip"] == 0.0   # lower bound already <= 0 (0.05 - 0.066)
    never = T.survivorship_breakdown({"estimate": 0.10, "se": 0.01, "q10_gap_mean": 0.20})                              # the dead funds would have done better: nothing flips
    assert math.isinf(never["estimate_flip"]) and math.isinf(never["lower_bound_flip"])
    with pytest.raises(ValueError, match="q10_gap_mean"):
        T.survivorship_breakdown({"estimate": 0.1, "se": 0.04})                      # a missing gap is an error, never a silent zero (FND-0431 E-14)


# ---- realised_quantile_path ------------------------------------------------------------------------------------------------------------------------------
def _paths(n=11, months=3):
    idx = pd.date_range("2008-01-31", periods=months, freq="ME")
    return pd.DataFrame({f"F{i:02d}": [0.01 * (i - 5)] * months for i in range(n)}, index=idx)         # F00 worst ... F10 best, cumulative return rises with i


def test_the_stress_path_is_the_realised_path_of_one_fund():
    p = _paths()
    path = T.realised_quantile_path(p, q=0.10)
    assert path.name == "F01"                                                        # floor(0.10 * (11 - 1)) = 1 -> the second worst
    assert path.equals(p["F01"])                                                      # the fund's own path, not a pointwise percentile
    assert T.realised_quantile_path(p, q=0.0).name == "F00"


def test_a_fund_with_a_hole_is_dropped_never_filled():
    p = _paths()
    p.loc[p.index[1], "F00"] = np.nan
    assert T.realised_quantile_path(p, q=0.0).name == "F01"                          # F00 is gone, F01 is now the worst
    with pytest.raises(ValueError):
        T.realised_quantile_path(pd.DataFrame({"A": [np.nan, 0.1]}), q=0.10)         # nothing left


# ---- tail_stress_max_dd ------------------------------------------------------------------------------------------------------------------------------
IDX = pd.date_range("2008-01-31", periods=3, freq="ME")
PORT = pd.Series([0.01, 0.01, 0.01], index=IDX)
STRESS = pd.Series([-0.20, 0.0, 0.0], index=IDX)


def test_share_zero_is_the_unstressed_drawdown_and_the_hand_case_is_exact():
    assert T.tail_stress_max_dd(PORT, STRESS, 0.0) == pytest.approx(0.0)              # a path that only rises has no drawdown
    got = T.tail_stress_max_dd(PORT, STRESS, 0.10)
    assert got == pytest.approx(0.9 * 0.01 + 0.1 * -0.20)                              # first month return -0.011 is the only loss (no prior peak above the start)
    assert got == pytest.approx(-0.011)


def test_the_stress_only_deepens_the_drawdown_as_the_share_grows_when_the_stress_path_is_worse():
    dds = [T.tail_stress_max_dd(PORT, STRESS, s) for s in (0.0, 0.05, 0.10, 0.20, 0.50)]
    assert all(b <= a + 1e-15 for a, b in zip(dds, dds[1:])) and dds[-1] < dds[0]
    bad_port = pd.Series([0.02, -0.04, 0.02], index=IDX)
    worse_every_month = pd.Series([-0.20, -0.05, -0.05], index=IDX)                   # below the portfolio in EVERY month: the monotone property needs it
    dds = [T.tail_stress_max_dd(bad_port, worse_every_month, s) for s in (0.0, 0.10, 0.20, 0.50)]
    assert all(b < a for a, b in zip(dds, dds[1:]))
    # the property is conditional: a stress path that is better than the portfolio in some month can soften the drawdown (STRESS earns 0.0 where bad_port loses 4 %)
    assert T.tail_stress_max_dd(bad_port, STRESS, 0.10) > T.tail_stress_max_dd(bad_port, STRESS, 0.0)


def test_the_stress_refuses_a_share_above_one_half_misaligned_or_non_finite_input():
    with pytest.raises(ValueError, match="share"):
        T.tail_stress_max_dd(PORT, STRESS, 0.6)
    with pytest.raises(ValueError, match="share"):
        T.tail_stress_max_dd(PORT, STRESS, -0.1)
    with pytest.raises(ValueError, match="index"):
        T.tail_stress_max_dd(PORT, STRESS.iloc[:2], 0.1)
    with pytest.raises(ValueError, match="finite"):
        T.tail_stress_max_dd(PORT.where(PORT.index != IDX[1]), STRESS, 0.1)
