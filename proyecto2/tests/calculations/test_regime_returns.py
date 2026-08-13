# proyecto2/tests/calculations/test_regime_returns.py
# -*- coding: utf-8 -*-
"""
Tests for src/calculations/regime_returns.compute_regime_returns (P3-01, P3-02).

R-7: imports ONLY regime_returns — no pipeline.py, no core.io, no DB.
Run from repo root:
    python -m pytest proyecto2/tests/calculations/test_regime_returns.py -v
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# regime_returns.py imports from shared.config; ensure repo root is reachable.
_ROOT = Path(__file__).resolve().parent.parent.parent.parent  # c:\desarrollo\fondos
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.calculations.regime_returns import (
    compute_regime_returns,
    MIN_OBS_REGIME,
    MIN_NAV_TOTAL,
    _REGIME_SUFFIX,
)

N_REGIMES = len(_REGIME_SUFFIX)  # 7


# ============================================================
# Fixtures
# ============================================================

def _nav_df(n: int = 80, start: str = "2018-01-31") -> pd.DataFrame:
    """Synthetic monthly NAV: monotone growth, no zeros."""
    dates = pd.date_range(start=start, periods=n, freq="ME")
    nav   = 100.0 * np.cumprod(1 + np.random.default_rng(42).normal(0.005, 0.02, n))
    return pd.DataFrame({"date": dates, "nav": nav})


def _regime_df_all_covered(n: int = 80, start: str = "2018-01-31") -> pd.DataFrame:
    """
    All 7 regimes get at least MIN_OBS_REGIME months each.
    Distribute rounds evenly across the month sequence.
    """
    dates   = pd.date_range(start=start, periods=n, freq="ME")
    regimes = list(_REGIME_SUFFIX.keys())
    labels  = [regimes[i % N_REGIMES] for i in range(n)]
    return pd.DataFrame({"regime": labels}, index=dates)


def _regime_df_none_covered(n: int = 80, start: str = "2018-01-31") -> pd.DataFrame:
    """
    Only 1 regime appears (all n months in 'Expansion').
    Other 6 regimes have 0 months → none qualify (Expansion gets n months but
    we set n < MIN_OBS_REGIME to make ALL fail: use n=1 for the one regime).
    Actually: assign all months to one regime so only that regime qualifies;
    to have NONE qualify we keep n < MIN_OBS_REGIME for every regime.
    """
    dates  = pd.date_range(start=start, periods=n, freq="ME")
    # Give each regime exactly MIN_OBS_REGIME - 1 months (just below threshold)
    per_regime = MIN_OBS_REGIME - 1  # 11
    labels = []
    for regime in _REGIME_SUFFIX.keys():
        labels.extend([regime] * per_regime)
    # Fill remainder with first regime (still < MIN_OBS_REGIME per regime
    # since we'll trim to n months total — but we need n >= MIN_NAV_TOTAL)
    # Simplest: just cycle but ensure no regime hits the threshold
    labels_trimmed = (labels * ((n // len(labels)) + 1))[:n]
    # Clamp each regime count to per_regime by careful construction
    final = []
    counts: dict[str, int] = {r: 0 for r in _REGIME_SUFFIX}
    for lbl in labels_trimmed:
        if counts[lbl] < per_regime:
            final.append(lbl)
            counts[lbl] += 1
        else:
            # Spill to first under-threshold regime
            for r in _REGIME_SUFFIX:
                if counts[r] < per_regime:
                    final.append(r)
                    counts[r] += 1
                    break
    # Pad with "Expansion" (already at per_regime cap, but we only have
    # 7*(MIN_OBS-1) = 77 months; pad remaining with a non-qualifying value
    # by just repeating — they'll still be capped at per_regime since the
    # regime_df inner-joins with nav_df which has n rows)
    while len(final) < n:
        final.append(list(_REGIME_SUFFIX.keys())[0])
    df = pd.DataFrame({"regime": final[:n]}, index=dates)
    return df


def _regime_df_k_covered(k: int, n: int = 120, start: str = "2015-01-31") -> pd.DataFrame:
    """
    Exactly k of 7 regimes get MIN_OBS_REGIME months; the rest get 0 months.
    Uses n months total; n should be >= MIN_NAV_TOTAL + k*MIN_OBS_REGIME.
    """
    dates   = pd.date_range(start=start, periods=n, freq="ME")
    regimes = list(_REGIME_SUFFIX.keys())
    covered = regimes[:k]
    labels  = []
    for r in covered:
        labels.extend([r] * MIN_OBS_REGIME)
    # Fill remainder with covered[0] (already covered; extra months are fine)
    rem = n - len(labels)
    filler = covered[0] if covered else regimes[0]
    labels += [filler] * rem
    return pd.DataFrame({"regime": labels[:n]}, index=dates)


def _extract_coverage(result: list[tuple]) -> float | None:
    """Pull regime_coverage_ratio value from compute_regime_returns output."""
    for name, val, _ in result:
        if name == "regime_coverage_ratio":
            return val
    return None


# ============================================================
# Tests — edge cases (empty / short data)
# ============================================================

class TestEdgeCases:
    def test_empty_nav_returns_empty(self):
        nav = pd.DataFrame(columns=["date", "nav"])
        reg = _regime_df_all_covered()
        assert compute_regime_returns(nav, reg) == []

    def test_none_regime_returns_empty(self):
        nav = _nav_df()
        assert compute_regime_returns(nav, None) == []

    def test_empty_regime_returns_empty(self):
        nav = _nav_df()
        reg = pd.DataFrame(columns=["regime"])
        assert compute_regime_returns(nav, reg) == []

    def test_short_nav_returns_empty(self):
        nav = _nav_df(n=MIN_NAV_TOTAL - 1)
        reg = _regime_df_all_covered(n=MIN_NAV_TOTAL - 1)
        assert compute_regime_returns(nav, reg) == []


# ============================================================
# Tests — regime_coverage_ratio present and correct
# ============================================================

class TestCoverageRatioPresent:
    def test_coverage_ratio_always_in_output(self):
        """regime_coverage_ratio must appear whenever compute_regime_returns returns metrics."""
        nav = _nav_df()
        reg = _regime_df_all_covered()
        result = compute_regime_returns(nav, reg)
        assert result, "Expected non-empty metrics"
        assert _extract_coverage(result) is not None, "regime_coverage_ratio missing"

    def test_coverage_ratio_real_flag_zero(self):
        """regime_coverage_ratio uses real_flag=0 (nominal metric)."""
        nav = _nav_df()
        reg = _regime_df_all_covered()
        for name, val, rf in compute_regime_returns(nav, reg):
            if name == "regime_coverage_ratio":
                assert rf == 0

    def test_coverage_ratio_range(self):
        """Value must be in [0.0, 1.0]."""
        nav = _nav_df()
        reg = _regime_df_all_covered()
        cov = _extract_coverage(compute_regime_returns(nav, reg))
        assert 0.0 <= cov <= 1.0


# ============================================================
# Tests — correct values
# ============================================================

class TestCoverageRatioValues:
    def test_all_regimes_covered(self):
        """All 7 regimes with ≥12 months → ratio = 1.0."""
        nav = _nav_df(n=120, start="2015-01-31")
        reg = _regime_df_all_covered(n=120, start="2015-01-31")
        result = compute_regime_returns(nav, reg)
        cov = _extract_coverage(result)
        assert cov == pytest.approx(1.0), f"Expected 1.0, got {cov}"

    def test_zero_regimes_covered(self):
        """All regimes below threshold → ratio = 0.0."""
        nav = _nav_df(n=MIN_NAV_TOTAL, start="2020-01-31")
        reg = _regime_df_none_covered(n=MIN_NAV_TOTAL, start="2020-01-31")
        result = compute_regime_returns(nav, reg)
        cov = _extract_coverage(result)
        assert cov == pytest.approx(0.0), f"Expected 0.0, got {cov}"

    def test_k_of_7_regimes_covered(self):
        """k covered regimes → ratio = k/7."""
        for k in range(1, N_REGIMES):
            nav = _nav_df(n=120, start="2015-01-31")
            reg = _regime_df_k_covered(k=k, n=120, start="2015-01-31")
            result = compute_regime_returns(nav, reg)
            cov = _extract_coverage(result)
            expected = k / N_REGIMES
            assert cov == pytest.approx(expected, abs=1e-9), (
                f"k={k}: expected {expected:.4f}, got {cov}"
            )

    def test_exactly_min_obs_qualifies(self):
        """Exactly MIN_OBS_REGIME months in a regime qualifies (boundary)."""
        nav  = _nav_df(n=MIN_NAV_TOTAL + MIN_OBS_REGIME, start="2020-01-31")
        n    = len(nav)
        dates = pd.date_range(start="2020-01-31", periods=n, freq="ME")
        # One regime gets exactly MIN_OBS_REGIME months; rest get 0
        labels = ["Expansion"] * MIN_OBS_REGIME + [
            "Recalentamiento"
        ] * (n - MIN_OBS_REGIME)
        reg = pd.DataFrame({"regime": labels}, index=dates)
        result = compute_regime_returns(nav, reg)
        cov = _extract_coverage(result)
        # Exactly 1 regime has the threshold → coverage = 1/7
        assert cov == pytest.approx(1 / N_REGIMES, abs=1e-9)

    def test_one_below_threshold_not_counted(self):
        """MIN_OBS_REGIME - 1 months does not qualify."""
        n     = MIN_NAV_TOTAL + MIN_OBS_REGIME
        nav   = _nav_df(n=n, start="2020-01-31")
        dates = pd.date_range(start="2020-01-31", periods=n, freq="ME")
        # Expansion gets MIN_OBS_REGIME - 1; rest get 0
        labels = ["Expansion"] * (MIN_OBS_REGIME - 1) + [
            "Recalentamiento"
        ] * (n - (MIN_OBS_REGIME - 1))
        reg = pd.DataFrame({"regime": labels}, index=dates)
        result = compute_regime_returns(nav, reg)
        cov = _extract_coverage(result)
        # Expansion is below threshold, Recalentamiento has enough
        assert cov == pytest.approx(1 / N_REGIMES, abs=1e-9)


# ============================================================
# Helpers — crisis stress (P3-02)
# ============================================================

def _extract_crisis(result: list[tuple], metric: str):
    """Pull a crisis_stress_score_* value from compute_regime_returns output."""
    for name, val, _ in result:
        if name == metric:
            return val
    return "MISSING"  # sentinel so tests can distinguish None from absent


def _crisis_regime_df(n_crisis: int, n_total: int = 120,
                      start: str = "2015-01-31") -> pd.DataFrame:
    """
    Build a regime DataFrame where the first n_crisis months are
    'Crisis_Financiera' and the rest are 'Expansion'.
    """
    dates  = pd.date_range(start=start, periods=n_total, freq="ME")
    labels = ["Crisis_Financiera"] * n_crisis + ["Expansion"] * (n_total - n_crisis)
    return pd.DataFrame({"regime": labels[:n_total]}, index=dates)


# ============================================================
# Tests — crisis_stress_score (P3-02)
# ============================================================

class TestCrisisStressScore:
    def test_crisis_metrics_absent_when_below_threshold(self):
        """If Crisis_Financiera months < MIN_OBS_REGIME, no crisis metrics emitted."""
        n = MIN_NAV_TOTAL + MIN_OBS_REGIME
        nav = _nav_df(n=n, start="2020-01-31")
        reg = _crisis_regime_df(n_crisis=MIN_OBS_REGIME - 1, n_total=n, start="2020-01-31")
        result = compute_regime_returns(nav, reg)
        assert _extract_crisis(result, "crisis_stress_score_mdd") == "MISSING"
        assert _extract_crisis(result, "crisis_stress_score_ttr") == "MISSING"

    def test_crisis_metrics_present_at_threshold(self):
        """Exactly MIN_OBS_REGIME crisis months in the merged log-return series → metrics emitted.
        Uses n_crisis+1 in regime_df because the first NAV row is consumed by the shift
        and dropped from r_log, so the inner-join with regime_df loses one crisis month."""
        n = MIN_NAV_TOTAL + MIN_OBS_REGIME + 10
        nav = _nav_df(n=n, start="2015-01-31")
        reg = _crisis_regime_df(n_crisis=MIN_OBS_REGIME + 1, n_total=n, start="2015-01-31")
        result = compute_regime_returns(nav, reg)
        assert _extract_crisis(result, "crisis_stress_score_mdd") != "MISSING"
        assert _extract_crisis(result, "crisis_stress_score_ttr") != "MISSING"

    def test_crisis_mdd_real_flag_zero(self):
        """crisis_stress_score_mdd must use real_flag=0."""
        n = 120
        nav = _nav_df(n=n, start="2015-01-31")
        reg = _crisis_regime_df(n_crisis=MIN_OBS_REGIME * 2, n_total=n, start="2015-01-31")
        for name, val, rf in compute_regime_returns(nav, reg):
            if name == "crisis_stress_score_mdd":
                assert rf == 0

    def test_crisis_ttr_real_flag_zero(self):
        """crisis_stress_score_ttr must use real_flag=0."""
        n = 120
        nav = _nav_df(n=n, start="2015-01-31")
        reg = _crisis_regime_df(n_crisis=MIN_OBS_REGIME * 2, n_total=n, start="2015-01-31")
        for name, val, rf in compute_regime_returns(nav, reg):
            if name == "crisis_stress_score_ttr":
                assert rf == 0

    def test_crisis_mdd_is_non_positive(self):
        """MDD is always ≤ 0 by definition (drawdown ratio from peak)."""
        n = 120
        nav = _nav_df(n=n, start="2015-01-31")
        reg = _crisis_regime_df(n_crisis=MIN_OBS_REGIME * 2, n_total=n, start="2015-01-31")
        mdd = _extract_crisis(compute_regime_returns(nav, reg), "crisis_stress_score_mdd")
        assert mdd != "MISSING"
        assert mdd <= 0.0

    def test_crisis_monotone_nav_zero_drawdown(self):
        """Strictly increasing NAV during crisis → MDD = 0 (no drawdown).
        Uses n_crisis+1 to ensure MIN_OBS_REGIME months survive the r_log inner-join."""
        n = 120
        dates = pd.date_range(start="2015-01-31", periods=n, freq="ME")
        # Monotone increasing prices: 100, 101, 102, ...
        nav_series = 100.0 + np.arange(n, dtype=float)
        nav = pd.DataFrame({"date": dates, "nav": nav_series})
        reg = _crisis_regime_df(n_crisis=MIN_OBS_REGIME + 1, n_total=n, start="2015-01-31")
        mdd = _extract_crisis(compute_regime_returns(nav, reg), "crisis_stress_score_mdd")
        assert mdd != "MISSING"
        assert mdd == pytest.approx(0.0, abs=1e-9)

    def test_crisis_ttr_none_when_no_recovery(self):
        """
        Permanently declining NAV during crisis → TTR stored as None (no recovery).
        Uses n_crisis+1 to ensure MIN_OBS_REGIME months survive the r_log inner-join.
        """
        n = 120
        dates  = pd.date_range(start="2015-01-31", periods=n, freq="ME")
        # Monotone decreasing: 100, 99, 98, ... (never recovers)
        nav_series = 100.0 - np.arange(n, dtype=float) * 0.5
        nav = pd.DataFrame({"date": dates, "nav": nav_series})
        reg = _crisis_regime_df(n_crisis=MIN_OBS_REGIME + 1, n_total=n, start="2015-01-31")
        ttr = _extract_crisis(compute_regime_returns(nav, reg), "crisis_stress_score_ttr")
        assert ttr != "MISSING"
        assert ttr is None  # np.nan stored as None in the tuple

    def test_crisis_ttr_finite_when_recovery_occurs(self):
        """
        NAV that dips then recovers → TTR is a finite positive number.
        Construct: first half declining, second half rising above original peak.
        """
        n        = 120
        n_crisis = MIN_OBS_REGIME * 2  # 24 crisis months
        dates    = pd.date_range(start="2015-01-31", periods=n, freq="ME")
        # Build prices: decline for n_crisis//2, then recover above start
        prices   = np.ones(n) * 100.0
        for i in range(1, n_crisis // 2 + 1):
            prices[i] = prices[i - 1] * 0.97   # decline 3%/month
        for i in range(n_crisis // 2 + 1, n):
            prices[i] = prices[i - 1] * 1.04   # recover 4%/month
        nav = pd.DataFrame({"date": dates, "nav": prices})
        reg = _crisis_regime_df(n_crisis=n_crisis, n_total=n, start="2015-01-31")
        ttr = _extract_crisis(compute_regime_returns(nav, reg), "crisis_stress_score_ttr")
        assert ttr != "MISSING"
        assert ttr is not None and ttr > 0
