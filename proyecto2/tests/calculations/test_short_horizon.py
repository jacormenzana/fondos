# proyecto2/tests/calculations/test_short_horizon.py
# -*- coding: utf-8 -*-
"""
Tests for src/calculations/short_horizon.py (v24 daily-NAV metrics).

R-7 compliant: no imports of pipeline.py, core.io, or any project-level I/O.
Run from the proyecto2/ directory:
    python -m pytest tests/calculations/test_short_horizon.py
"""

import math
import numpy as np
import pandas as pd
import pytest

from src.calculations.short_horizon import (
    _cumulative_return,
    _daily_returns,
    _liquidity_flag,
    _ac1_adjusted_vol,
    compute_short_horizon_metrics,
    _LIQUIDITY_THRESHOLD_DEFAULT,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_nav_df(navs: list[float]) -> pd.DataFrame:
    """Build a minimal nav_df as expected by compute_short_horizon_metrics."""
    dates = pd.date_range("2025-01-01", periods=len(navs), freq="B")
    return pd.DataFrame({"date": dates, "nav": navs})


# ---------------------------------------------------------------------------
# _cumulative_return
# ---------------------------------------------------------------------------

def test_cumulative_return_positive():
    nav = pd.Series([100.0, 110.0, 120.0])
    assert round(_cumulative_return(nav), 6) == round(0.20, 6)


def test_cumulative_return_negative():
    nav = pd.Series([100.0, 90.0, 80.0])
    assert round(_cumulative_return(nav), 6) == round(-0.20, 6)


def test_cumulative_return_flat():
    nav = pd.Series([100.0, 100.0, 100.0])
    assert _cumulative_return(nav) == 0.0


def test_cumulative_return_single_row_returns_nan():
    nav = pd.Series([100.0])
    assert math.isnan(_cumulative_return(nav))


# ---------------------------------------------------------------------------
# _liquidity_flag
# ---------------------------------------------------------------------------

def test_liquidity_flag_liquid_series():
    """Series with no repeated NAVs → flag near 0."""
    nav = pd.Series([100.0, 101.0, 102.0, 103.0, 104.0, 105.0])
    flag = _liquidity_flag(nav)
    assert flag == 0.0


def test_liquidity_flag_fully_stale():
    """Series where all sessions have the same NAV → flag = 1.0."""
    nav = pd.Series([100.0] * 10)
    flag = _liquidity_flag(nav)
    assert flag == 1.0


def test_liquidity_flag_partially_stale():
    """3 out of 7 return sessions are zero → flag ~ 3/7 ≈ 0.4286."""
    # returns: [+1%, 0, 0, +1%, 0, +1%, +1%]  => 3 zeros out of 7
    navs = [100.0, 101.0, 101.0, 101.0, 102.01, 102.01, 103.03, 104.06]
    nav = pd.Series(navs)
    flag = _liquidity_flag(nav)
    # 3 zeros in 7 observed returns (pct_change drops first)
    assert flag == round(3 / 7, 4)


def test_liquidity_flag_exceeds_threshold():
    """Flag above default threshold (0.20) = daily data untrusted."""
    navs = [100.0, 100.0, 100.0, 100.0, 101.0]  # 3/4 zeros → 0.75
    nav = pd.Series(navs)
    flag = _liquidity_flag(nav)
    assert flag > _LIQUIDITY_THRESHOLD_DEFAULT


def test_liquidity_flag_below_threshold():
    """Flag at zero is clearly below threshold — data trusted."""
    nav = pd.Series([100.0, 101.0, 102.0, 103.0])
    flag = _liquidity_flag(nav)
    assert flag <= _LIQUIDITY_THRESHOLD_DEFAULT


def test_liquidity_flag_too_short_returns_nan():
    nav = pd.Series([100.0])
    assert math.isnan(_liquidity_flag(nav))


# ---------------------------------------------------------------------------
# _ac1_adjusted_vol
# ---------------------------------------------------------------------------

def test_ac1_adjusted_vol_too_short():
    """< 10 observations → NaN."""
    rets = pd.Series([0.01, -0.01, 0.02, -0.02, 0.01])
    assert math.isnan(_ac1_adjusted_vol(rets))


def test_ac1_adjusted_vol_zero_sigma():
    """Constant returns (σ=0) → NaN (no meaningful vol)."""
    rets = pd.Series([0.0] * 20)
    assert math.isnan(_ac1_adjusted_vol(rets))


def test_ac1_adjusted_vol_no_positive_autocorr():
    """IID returns: no positive AC → vol_adj == vol_raw (no inflation)."""
    rng = np.random.default_rng(42)
    raw = rng.standard_normal(200) * 0.01
    rets = pd.Series(raw)
    # Force rho1 < 0 by alternating signs (oscillating = negative autocorr)
    alternating = pd.Series([0.01 if i % 2 == 0 else -0.01 for i in range(50)])
    vol_adj = _ac1_adjusted_vol(alternating)
    vol_raw = float(alternating.std(ddof=1)) * (252 ** 0.5)
    # When rho1 <= 0, factor=1 → vol_adj == vol_raw (within round(6) precision)
    assert abs(vol_adj - vol_raw) < 1e-5


def test_ac1_adjusted_vol_positive_autocorr():
    """
    AR(1) process with positive persistence → vol_adj > vol_raw.
    Mimics an illiquid fund where yesterday's return predicts today's.
    """
    rng = np.random.default_rng(7)
    n = 150
    rets = np.zeros(n)
    rets[0] = rng.standard_normal() * 0.005
    phi = 0.6  # strong positive autocorrelation
    for i in range(1, n):
        rets[i] = phi * rets[i - 1] + rng.standard_normal() * 0.003

    rets_s = pd.Series(rets)
    vol_adj = _ac1_adjusted_vol(rets_s)
    vol_raw = float(rets_s.std(ddof=1)) * (252 ** 0.5)

    assert not math.isnan(vol_adj)
    # With phi=0.6, rho1 should be positive → vol_adj > vol_raw
    assert vol_adj > vol_raw


# ---------------------------------------------------------------------------
# compute_short_horizon_metrics (integration)
# ---------------------------------------------------------------------------

def test_compute_metrics_empty_returns_empty():
    df = pd.DataFrame(columns=["date", "nav"])
    result = compute_short_horizon_metrics(df, ipc_df=None)
    assert result == []


def test_compute_metrics_single_row_returns_empty():
    df = _make_nav_df([100.0])
    result = compute_short_horizon_metrics(df, ipc_df=None)
    assert result == []


def test_compute_metrics_returns_expected_metric_names():
    """Full 30-day series should produce all expected nominal metrics."""
    navs = [100.0 + i * 0.5 + (0.2 if i % 5 == 0 else 0) for i in range(30)]
    df = _make_nav_df(navs)
    result = compute_short_horizon_metrics(df, ipc_df=None)

    names = {m for m, _, _ in result}
    assert "short_return_cum" in names
    assert "short_max_drawdown" in names
    assert "short_vol_ann" in names
    assert "short_vol_adj" in names
    assert "short_liquidity_flag" in names


def test_compute_metrics_real_flag_convention():
    """Nominal metrics carry real_flag=0; real metrics real_flag=1."""
    navs = [100.0 + i * 0.3 for i in range(30)]
    df = _make_nav_df(navs)

    # Provide a minimal IPC DataFrame so real metrics are produced
    ipc_dates = pd.date_range("2024-12-31", periods=4, freq="ME")
    ipc_df = pd.DataFrame({"date": ipc_dates, "ipc_index": [100.0, 100.3, 100.6, 100.9]})

    result = compute_short_horizon_metrics(df, ipc_df=ipc_df)
    names_by_flag: dict[int, set] = {0: set(), 1: set()}
    for m, _, rf in result:
        names_by_flag[rf].add(m)

    # real_flag=0 should contain nominal metrics
    assert "short_return_cum" in names_by_flag[0]
    assert "short_max_drawdown" in names_by_flag[0]
    # real_flag=1 should contain at least the real cumulative return
    assert "short_return_cum_real" in names_by_flag[1]


def test_compute_metrics_drawdown_non_positive():
    """Max drawdown must be <= 0 for any NAV series."""
    import random
    random.seed(99)
    navs = [100.0]
    for _ in range(29):
        navs.append(navs[-1] * (1 + random.uniform(-0.03, 0.03)))
    df = _make_nav_df(navs)
    result = compute_short_horizon_metrics(df, ipc_df=None)

    dd_vals = [v for m, v, _ in result if m == "short_max_drawdown"]
    assert len(dd_vals) == 1
    assert dd_vals[0] <= 0.0


def test_compute_metrics_liquidity_flag_in_range():
    """Liquidity flag must be in [0, 1]."""
    navs = [100.0 + i * 0.1 for i in range(40)]
    df = _make_nav_df(navs)
    result = compute_short_horizon_metrics(df, ipc_df=None)

    liq_vals = [v for m, v, _ in result if m == "short_liquidity_flag"]
    assert len(liq_vals) == 1
    assert 0.0 <= liq_vals[0] <= 1.0


def test_compute_metrics_cumulative_return_matches_manual():
    """Cumulative return should equal nav[-1]/nav[0] - 1."""
    navs = [100.0, 102.0, 98.0, 103.0, 105.0]
    df = _make_nav_df(navs)
    result = compute_short_horizon_metrics(df, ipc_df=None)

    expected_cum = 105.0 / 100.0 - 1.0
    cum_vals = [v for m, v, _ in result if m == "short_return_cum"]
    assert len(cum_vals) == 1
    assert abs(cum_vals[0] - expected_cum) < 1e-6


# ---------------------------------------------------------------------------
# Per-horizon minimum obs (pipeline enforces this; here we test the
# underlying calc function's own minimum: needs >= 2 rows)
# ---------------------------------------------------------------------------

def test_compute_metrics_two_rows_produces_partial_results():
    """With exactly 2 rows, we get return and drawdown but AC vol is NaN (< 10 obs)."""
    df = _make_nav_df([100.0, 105.0])
    result = compute_short_horizon_metrics(df, ipc_df=None)
    names = {m for m, _, _ in result}
    assert "short_return_cum" in names
    assert "short_max_drawdown" in names
    # short_vol_adj requires >= 10 obs for AC calc — not emitted with only 2 rows
    # (short_vol_ann is emitted when len(rets)>=2, vol_adj only when not nan)
    vol_adj_present = "short_vol_adj" in names
    # It should be absent (NaN excluded from results by the function)
    assert not vol_adj_present


# ---------------------------------------------------------------------------
# Liquidity-trust gate (P3 circuit-breaker threshold)
# ---------------------------------------------------------------------------

def test_liquidity_trust_threshold_value():
    """DEFAULT threshold must be exactly 0.20 as documented."""
    assert _LIQUIDITY_THRESHOLD_DEFAULT == 0.20


def test_gate_bypassed_when_illiquid():
    """
    Simulates the P3 gate logic: if liq_flag > threshold, daily gate is
    skipped (fail-open).  We verify a series where flag > 0.20 produces a
    liq_flag metric exceeding the threshold so the caller can gate correctly.
    """
    # 6 out of 9 returns are zero (stale fund)
    navs = [100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 101.0, 102.0, 103.0]
    df = _make_nav_df(navs)
    result = compute_short_horizon_metrics(df, ipc_df=None)

    liq_vals = [v for m, v, _ in result if m == "short_liquidity_flag"]
    assert len(liq_vals) == 1
    flag = liq_vals[0]
    # Gate should recognize this as untrusted daily data
    assert flag > _LIQUIDITY_THRESHOLD_DEFAULT
