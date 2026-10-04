# proyecto2/tests/calculations/test_macro_sensitivity.py
# -*- coding: utf-8 -*-
"""
Tests for P3-03 and P3-04 scenario metrics in compute_macro_sensitivity.

R-7: imports ONLY macro_sensitivity — no pipeline.py, no core.io, no DB.
Run from repo root:
    python -m pytest proyecto2/tests/calculations/test_macro_sensitivity.py -v
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# macro_sensitivity.py imports shared.config; ensure repo root is reachable.
_ROOT = Path(__file__).resolve().parent.parent.parent.parent  # c:\desarrollo\fondos
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.calculations.macro_sensitivity import (
    compute_macro_sensitivity,
    MIN_OBS,
    _PER_FUND_MIN_COVERAGE,
)


# ============================================================
# Fixtures
# ============================================================

def _nav_df(n: int = MIN_OBS + 10, start: str = "2015-01-31") -> pd.DataFrame:
    dates = pd.date_range(start=start, periods=n, freq="ME")
    nav   = 100.0 * np.cumprod(
        1 + np.random.default_rng(7).normal(0.005, 0.02, n)
    )
    return pd.DataFrame({"date": dates, "nav": nav})


def _macro_with_oil_and_hy(n: int = MIN_OBS + 10,
                            start: str = "2015-01-31") -> pd.DataFrame:
    """Two low-collinearity factors: oil_yoy and spread_hy."""
    dates = pd.date_range(start=start, periods=n, freq="ME")
    rng   = np.random.default_rng(42)
    return pd.DataFrame(
        {
            "oil_yoy":   rng.normal(0, 0.05, n),
            "spread_hy": rng.normal(0, 0.02, n),
        },
        index=dates,
    )


def _macro_oil_only(n: int = MIN_OBS + 10,
                    start: str = "2015-01-31") -> pd.DataFrame:
    """Only oil_yoy — no spread_hy factor."""
    dates = pd.date_range(start=start, periods=n, freq="ME")
    return pd.DataFrame(
        {"oil_yoy": np.random.default_rng(11).normal(0, 0.05, n)},
        index=dates,
    )


def _extract(result: list[tuple], metric: str):
    for name, val, _ in result:
        if name == metric:
            return val
    return "MISSING"


# ============================================================
# Tests — P3-03: energy_sensitivity_pct
# ============================================================

class TestEnergySensitivity:
    def test_present_when_beta_oil_computed(self):
        """energy_sensitivity_pct must be emitted when beta_oil is in the OLS output."""
        nav   = _nav_df()
        macro = _macro_with_oil_and_hy()
        result = compute_macro_sensitivity(nav, macro)
        assert _extract(result, "energy_sensitivity_pct") != "MISSING"

    def test_value_equals_beta_oil_times_025(self):
        """energy_sensitivity_pct == beta_oil × 0.25."""
        nav   = _nav_df()
        macro = _macro_with_oil_and_hy()
        result = compute_macro_sensitivity(nav, macro)
        beta_oil = _extract(result, "beta_oil")
        esp      = _extract(result, "energy_sensitivity_pct")
        assert beta_oil != "MISSING", "beta_oil not in result"
        assert esp      != "MISSING", "energy_sensitivity_pct not in result"
        assert esp == pytest.approx(beta_oil * 0.25, rel=1e-9)

    def test_real_flag_zero(self):
        """energy_sensitivity_pct must use real_flag=0."""
        nav   = _nav_df()
        macro = _macro_with_oil_and_hy()
        for name, val, rf in compute_macro_sensitivity(nav, macro):
            if name == "energy_sensitivity_pct":
                assert rf == 0

    def test_absent_when_short_nav(self):
        """Below MIN_OBS months → no metrics at all → energy_sensitivity_pct absent."""
        nav   = _nav_df(n=MIN_OBS - 1)
        macro = _macro_with_oil_and_hy(n=MIN_OBS - 1)
        result = compute_macro_sensitivity(nav, macro)
        assert result == []

    def test_absent_when_no_oil_factor(self):
        """If oil_yoy not in macro_df → beta_oil absent → energy_sensitivity_pct absent."""
        dates = pd.date_range("2015-01-31", periods=MIN_OBS + 10, freq="ME")
        macro = pd.DataFrame(
            {"spread_hy": np.random.default_rng(3).normal(0, 0.02, MIN_OBS + 10)},
            index=dates,
        )
        nav = _nav_df()
        result = compute_macro_sensitivity(nav, macro)
        assert _extract(result, "energy_sensitivity_pct") == "MISSING"


# ============================================================
# Tests — P3-04: hy_spread_sensitivity_pct
# ============================================================

class TestHYSpreadSensitivity:
    def test_present_when_beta_spread_hy_computed(self):
        """hy_spread_sensitivity_pct must be emitted when beta_spread_hy is in OLS output."""
        nav   = _nav_df()
        macro = _macro_with_oil_and_hy()
        result = compute_macro_sensitivity(nav, macro)
        assert _extract(result, "hy_spread_sensitivity_pct") != "MISSING"

    def test_value_equals_beta_spread_hy_times_3(self):
        """hy_spread_sensitivity_pct == beta_spread_hy × 3.0."""
        nav   = _nav_df()
        macro = _macro_with_oil_and_hy()
        result = compute_macro_sensitivity(nav, macro)
        beta_hy = _extract(result, "beta_spread_hy")
        hys     = _extract(result, "hy_spread_sensitivity_pct")
        assert beta_hy != "MISSING", "beta_spread_hy not in result"
        assert hys     != "MISSING", "hy_spread_sensitivity_pct not in result"
        assert hys == pytest.approx(beta_hy * 3.0, rel=1e-9)

    def test_real_flag_zero(self):
        """hy_spread_sensitivity_pct must use real_flag=0."""
        nav   = _nav_df()
        macro = _macro_with_oil_and_hy()
        for name, val, rf in compute_macro_sensitivity(nav, macro):
            if name == "hy_spread_sensitivity_pct":
                assert rf == 0

    def test_absent_when_no_spread_hy_factor(self):
        """If spread_hy not in macro_df → beta_spread_hy absent → hy_spread_sensitivity_pct absent."""
        nav   = _nav_df()
        macro = _macro_oil_only()
        result = compute_macro_sensitivity(nav, macro)
        assert _extract(result, "hy_spread_sensitivity_pct") == "MISSING"

    def test_both_present_together(self):
        """Both scenario metrics are present together in a standard run."""
        nav   = _nav_df()
        macro = _macro_with_oil_and_hy()
        result = compute_macro_sensitivity(nav, macro)
        assert _extract(result, "energy_sensitivity_pct")    != "MISSING"
        assert _extract(result, "hy_spread_sensitivity_pct") != "MISSING"


# ============================================================
# Tests — per-fund windowed factor selection (Layer 2 fix)
# ============================================================

def _nav_df_range(start: str, end: str) -> pd.DataFrame:
    """NAV from start to end (month-end frequency)."""
    dates = pd.date_range(start=start, end=end, freq="ME")
    nav   = 100.0 * np.cumprod(
        1 + np.random.default_rng(17).normal(0.005, 0.02, len(dates))
    )
    return pd.DataFrame({"date": dates, "nav": nav})


def _macro_stale_after(
    full_start: str,
    full_end: str,
    stale_col: str,
    stale_after: str,
) -> pd.DataFrame:
    """
    Returns a macro DataFrame spanning full_start..full_end where the column
    `stale_col` has data only up to stale_after (NaN after that), and a second
    column 'oil_yoy' that is fully populated throughout.
    """
    dates = pd.date_range(start=full_start, end=full_end, freq="ME")
    rng   = np.random.default_rng(55)
    oil   = rng.normal(0, 0.05, len(dates))
    stale = rng.normal(0, 0.02, len(dates))
    stale_ts = pd.Timestamp(stale_after)
    stale_col_data = np.where(pd.DatetimeIndex(dates) > stale_ts, np.nan, stale)
    return pd.DataFrame(
        {"oil_yoy": oil, stale_col: stale_col_data},
        index=dates,
    )


def _macro_short_start(
    full_start: str,
    full_end: str,
    short_col: str,
    short_start: str,
) -> pd.DataFrame:
    """
    Returns a macro DataFrame spanning full_start..full_end where `short_col`
    only starts at short_start (NaN before that), and 'oil_yoy' is fully covered.
    """
    dates = pd.date_range(start=full_start, end=full_end, freq="ME")
    rng   = np.random.default_rng(77)
    oil   = rng.normal(0, 0.05, len(dates))
    short = rng.normal(0, 0.02, len(dates))
    short_ts = pd.Timestamp(short_start)
    short_col_data = np.where(pd.DatetimeIndex(dates) < short_ts, np.nan, short)
    return pd.DataFrame(
        {"oil_yoy": oil, short_col: short_col_data},
        index=dates,
    )


class TestPerFundWindowedSelection:
    """
    Validates that a stale or short-starting factor is dropped per-fund
    rather than silently truncating the regression window for all funds.
    """

    def test_stale_factor_dropped_window_not_truncated(self):
        """
        A factor stale after 2021-06 should be dropped for a fund active through 2026.
        The resulting OLS window must NOT be truncated at 2021-06 — macro_n_obs must
        span the fund's full history (not capped at the stale factor's end date).
        """
        # Fund: 2010-01 to 2026-06 → ~198 monthly returns
        nav   = _nav_df_range("2010-01-31", "2026-06-30")
        macro = _macro_stale_after(
            full_start="2010-01-31",
            full_end="2026-06-30",
            stale_col="ipc_yoy_jp",
            stale_after="2021-06-30",
        )
        # ipc_yoy_jp covers 2010-01 to 2021-06 ≈ 138/198 months → 70% < _PER_FUND_MIN_COVERAGE
        # → must be dropped; oil_yoy covers all 198 months → kept
        result = compute_macro_sensitivity(nav, macro)
        assert result, "OLS should run despite stale factor"
        n_obs = _extract(result, "macro_n_obs")
        assert n_obs != "MISSING"
        # Window must NOT be capped at stale factor's end; n_obs should be close to full fund span
        stale_end_months = len(
            pd.date_range("2010-01-31", "2021-06-30", freq="ME")
        )
        assert n_obs > stale_end_months, (
            f"n_obs={n_obs} should exceed stale-factor window={stale_end_months}"
        )
        # Stale factor produces no beta; oil_yoy does
        assert _extract(result, "beta_oil") != "MISSING"
        assert _extract(result, "beta_ipc_yoy_jp") == "MISSING"  # not in _FACTOR_TO_METRIC

    def test_fully_covered_factor_retained(self):
        """A factor fully covered throughout the fund's window is kept."""
        nav   = _nav_df_range("2015-01-31", "2026-06-30")
        macro = _macro_with_oil_and_hy(
            n=len(pd.date_range("2015-01-31", "2026-06-30", freq="ME")),
            start="2015-01-31",
        )
        result = compute_macro_sensitivity(nav, macro)
        assert _extract(result, "beta_oil")      != "MISSING"
        assert _extract(result, "beta_spread_hy") != "MISSING"

    def test_stale_factor_below_min_obs_dropped(self):
        """A factor with < MIN_OBS non-null months in the fund's window is dropped."""
        # Fund 2015-01 to 2026-06 (~136 months).
        # Factor has data only for last 20 months → 20 < MIN_OBS(60) → dropped.
        nav = _nav_df_range("2015-01-31", "2026-06-30")
        macro = _macro_short_start(
            full_start="2015-01-31",
            full_end="2026-06-30",
            short_col="dxy_yoy",
            short_start="2024-11-30",  # only ~20 months → < MIN_OBS
        )
        result = compute_macro_sensitivity(nav, macro)
        # oil_yoy retained; dxy_yoy has < MIN_OBS months → dropped
        assert _extract(result, "beta_oil") != "MISSING"
        assert _extract(result, "beta_dxy")  == "MISSING"

    def test_late_start_factor_below_coverage_dropped(self):
        """
        A factor that starts only after ~20% of the fund's window is below
        _PER_FUND_MIN_COVERAGE and dropped (even if its non-null count >= MIN_OBS).
        """
        # Fund 2010-01 to 2026-06 (~198 months).
        # Factor starts 2016-06 → non-null ≈ 121 months / 198 = 61% < 0.85 → dropped.
        nav = _nav_df_range("2010-01-31", "2026-06-30")
        macro = _macro_short_start(
            full_start="2010-01-31",
            full_end="2026-06-30",
            short_col="dxy_yoy",
            short_start="2016-06-30",
        )
        result = compute_macro_sensitivity(nav, macro)
        assert _extract(result, "beta_oil") != "MISSING"
        assert _extract(result, "beta_dxy")  == "MISSING"

    def test_near_threshold_factor_retained(self):
        """
        A factor that covers >= _PER_FUND_MIN_COVERAGE of the fund's window is kept.
        """
        # Fund 2010-01 to 2026-06 (~198 months).
        # Factor starts 2012-01 → ~174/198 = 88% ≥ _PER_FUND_MIN_COVERAGE → kept.
        nav = _nav_df_range("2010-01-31", "2026-06-30")
        macro = _macro_short_start(
            full_start="2010-01-31",
            full_end="2026-06-30",
            short_col="dxy_yoy",
            short_start="2012-01-31",
        )
        result = compute_macro_sensitivity(nav, macro)
        assert _extract(result, "beta_oil") != "MISSING"
        assert _extract(result, "beta_dxy")  != "MISSING"

    def test_all_factors_fully_covered_window_unchanged(self):
        """
        When all factors are fully covered (no NaNs), behaviour is identical
        to the pre-fix code: OLS runs on the full intersection window.
        """
        n   = MIN_OBS + 20
        nav = _nav_df(n=n)
        mac = _macro_with_oil_and_hy(n=n)
        result = compute_macro_sensitivity(nav, mac)
        assert _extract(result, "macro_n_obs") == pytest.approx(n - 1, abs=1)

    def test_zero_variance_factor_dropped_no_lapack_error(self):
        """
        A factor that is exactly zero throughout the fund's window (e.g. d_rate_eu
        during the ECB zero-rate period) must be dropped before VIF/lstsq.
        Passing a zero-norm column to lstsq causes LAPACK DLASCLS 'parameter 4/5
        illegal value' warnings — the zero-variance guard prevents that.
        """
        nav = _nav_df_range("2016-01-31", "2022-06-30")
        n   = len(nav)
        dates = pd.date_range("2016-01-31", periods=n, freq="ME")
        macro = pd.DataFrame(
            {
                "d_rate_eu": np.zeros(n),           # all-zero column → DLASCLS trigger
                "oil_yoy":   np.random.default_rng(99).normal(0, 0.05, n),
            },
            index=dates,
        )
        result = compute_macro_sensitivity(nav, macro)
        # d_rate_eu must be dropped silently; oil_yoy kept; OLS must complete cleanly
        assert result, "OLS should succeed with zero-variance factor removed"
        assert _extract(result, "beta_oil") != "MISSING"

    def test_all_factors_pruned_returns_empty_no_lapack_error(self):
        """
        When every factor fails the per-fund coverage threshold (e.g. all are
        stale after the first month of the fund's window), compute_macro_sensitivity
        must return [] cleanly — no DLASCLS / LAPACK crash from a zero-column matrix.
        """
        nav = _nav_df_range("2022-01-31", "2026-06-30")
        # Both factors have data only up to 2022-02 → 1-2 months → < MIN_OBS → pruned
        macro = _macro_stale_after(
            full_start="2022-01-31",
            full_end="2026-06-30",
            stale_col="ipc_yoy_jp",
            stale_after="2022-02-28",
        )
        # oil_yoy is fully covered but the second factor is not; here we make oil_yoy
        # also stale so ALL columns are pruned.
        macro["oil_yoy"] = np.where(
            pd.DatetimeIndex(macro.index) > pd.Timestamp("2022-02-28"),
            np.nan,
            macro["oil_yoy"],
        )
        result = compute_macro_sensitivity(nav, macro)
        assert result == [], f"Expected [] when all factors pruned, got {result}"


# ============================================================
# Tests — FND-0176: zero-filled / near-constant factor guard
# ============================================================

def test_longest_zero_run():
    from src.calculations.macro_sensitivity import _longest_zero_run
    assert _longest_zero_run(np.array([1.0, 0.0, 0.0, 2.0, 0.0])) == 2
    assert _longest_zero_run(np.array([0.0] * 5)) == 5
    assert _longest_zero_run(np.array([1.0, 2.0])) == 0
    assert _longest_zero_run(np.array([1e-5, 0.0, 1e-5])) == 1      # near-zero is not zero


def test_zero_filled_factor_is_dropped_not_exploded():
    """a zero-filled factor (gold_yoy here) (a few ~1e-5 blips keep std > 1e-10) must not yield a huge beta."""
    n = MIN_OBS + 10
    macro = _macro_oil_only(n)
    z = np.zeros(n)
    z[3], z[8], z[15] = 2e-5, -1e-5, 3e-5            # std ~ 4e-6: passes the old std > 1e-10 guard
    macro["gold_yoy"] = z
    assert macro["gold_yoy"].std() > 1e-10
    result = compute_macro_sensitivity(_nav_df(n), macro)
    assert _extract(result, "beta_gold") == "MISSING"
    assert _extract(result, "beta_oil") != "MISSING"
    assert all(abs(v) < 5 for name, v, _ in result if name.startswith("beta_") and v is not None)


def test_rate_change_factor_with_long_zero_run_is_kept():
    """d_rate_* are legitimately flat for years (d_rate_eu: 41 zero months on live) - never dropped by the gate."""
    n = MIN_OBS + 10
    macro = _macro_oil_only(n)
    d = np.random.default_rng(5).normal(0, 0.2, n)
    d[:40] = 0.0
    macro["d_rate_eu"] = d
    result = compute_macro_sensitivity(_nav_df(n), macro)
    assert _extract(result, "beta_rate_eu") != "MISSING"


# ============================================================
# Tests — FND-0208: pre-OLS condition guard + post-OLS beta circuit breaker
# ============================================================

def test_near_constant_protected_factor_is_dropped_by_condition_guard():
    """d_rate_eu is VIF-protected and zero-run-exempt; a near-constant NON-zero one (collinear with the intercept)
    must still be dropped by the condition-index guard, with the other betas intact and bounded."""
    n = MIN_OBS + 10
    macro = _macro_with_oil_and_hy(n)
    macro["d_rate_eu"] = 0.3 + 1e-6 * np.random.default_rng(3).normal(size=n)
    diag: dict = {}
    result = compute_macro_sensitivity(_nav_df(n), macro, diagnostics=diag)
    assert diag["cond_dropped"] == ["d_rate_eu"]
    assert _extract(result, "beta_rate_eu") == "MISSING"
    assert _extract(result, "beta_oil") != "MISSING"
    assert all(abs(v) < 5 for name, v, _ in result if name.startswith("beta_") and v is not None)


def test_healthy_design_untouched_and_no_diagnostics():
    n = MIN_OBS + 10
    diag: dict = {}
    result = compute_macro_sensitivity(_nav_df(n), _macro_with_oil_and_hy(n), diagnostics=diag)
    assert diag == {}
    assert _extract(result, "beta_oil") not in ("MISSING", None)


def test_out_of_range_beta_is_returned_as_none_with_derived_none(monkeypatch):
    import src.calculations.macro_sensitivity as ms
    n = MIN_OBS + 10
    real_ols = ms._ols

    def _huge_oil(y, X):
        out = real_ols(y, X)
        out["beta"] = out["beta"].copy()
        out["beta"][1] = 1127.0                      # first factor column (oil_yoy)
        return out

    monkeypatch.setattr(ms, "_ols", _huge_oil)
    diag: dict = {}
    result = compute_macro_sensitivity(_nav_df(n), _macro_with_oil_and_hy(n), diagnostics=diag)
    assert diag["beta_nulled"] == ["beta_oil"]
    assert _extract(result, "beta_oil") is None
    assert _extract(result, "energy_sensitivity_pct") is None
    assert _extract(result, "beta_spread_hy") not in ("MISSING", None)
    assert _extract(result, "hy_spread_sensitivity_pct") is not None
