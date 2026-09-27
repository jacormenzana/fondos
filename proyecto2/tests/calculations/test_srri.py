import numpy as np
import pandas as pd

from src.calculations.srri import (
    compute_srri,
    srri_metrics,
    volatility_to_srri,
)


# ---------------------------------------------------------------------------
# volatility_to_srri — ESMA bucket mapping
# ---------------------------------------------------------------------------

def test_volatility_to_srri_buckets():
    assert volatility_to_srri(0.003) == 1
    assert volatility_to_srri(0.08) == 4
    assert volatility_to_srri(0.30) == 7


def test_volatility_to_srri_is_conservative_on_nan_and_negative():
    assert volatility_to_srri(float("nan")) == 0
    assert volatility_to_srri(-0.01) == 0


# ---------------------------------------------------------------------------
# compute_srri — FND-0094 (2026-09-27, root cause)
# ---------------------------------------------------------------------------
# rolling_1y (12-month trailing window) typically slices to 13 monthly NAV rows in practice (the
# window boundary plus month-end alignment), which pct_change+dropna turns into exactly 12 returns
# -- precisely the method's own stated minimum ("minimo 12 retornos mensuales"). The bug: the guard
# above that computation checked `< 14`, one more than its own comment said ("minimo 13 NAV"), so a
# real 13-row window was rejected before ever reaching the returns check. Measured live: 3,702/3,704
# active funds had srri_nav=0 for rolling_1y before this fix.

def test_13_nav_rows_compute_a_real_srri_not_the_insufficient_data_sentinel():
    """The exact regression: a 13-row series is exactly compute_srri's own stated minimum."""
    nav = pd.Series([100.0] + [100.0 * (1.001 ** i) for i in range(1, 13)])
    assert len(nav) == 13
    result = compute_srri(nav)
    assert result["srri"] != 0, f"expected a real bucket, got INSUF_DATA: {result}"
    assert result["method"] != "INSUF_DATA"
    assert result["n_periods"] == 12


def test_12_nav_rows_is_genuinely_insufficient_not_a_regression():
    """crisis_2022 (an exactly-12-calendar-month window) slices to 12 NAV rows -> 11 returns, one
    short of the method's own 12-return minimum. This is a structural property of a 12-month window
    sampled at month-end, not something compute_srri's threshold alone can fix -- stays 0/INSUF."""
    nav = pd.Series([100.0 * (1.001 ** i) for i in range(12)])
    assert len(nav) == 12
    result = compute_srri(nav)
    assert result["srri"] == 0
    assert result["method"] == "INSUF_DATA"   # rejected at the (correct) 13-row floor


def test_fewer_than_13_nav_rows_is_still_insufficient():
    nav = pd.Series([100.0, 101.0, 102.0])
    result = compute_srri(nav)
    assert result["srri"] == 0
    assert result["method"] == "INSUF_DATA"




def test_a_realistic_low_vol_fund_maps_to_a_low_bucket():
    # ~1% monthly noise -> low annualized vol -> low SRRI bucket, computed from a real 13-row series.
    rng = np.random.RandomState(0)
    nav = pd.Series(100.0 * np.cumprod(1 + rng.normal(0, 0.001, 13)))
    result = compute_srri(nav)
    assert result["srri"] in (1, 2, 3)
    assert result["method"].startswith("MONTHLY_ANN_SQRT")


def test_anomalous_scale_jump_is_flagged_not_bucketed_as_srri_7():
    """FIX-P2-NAV-SCALE-1: a >500% annualized vol reading is a data-corruption signature (e.g. a
    NAV scale mix-up), not a genuinely risky fund -- must stay 0/ANOMALOUS_VOL, not bucket 7.
    Erratic alternating swings, not smooth multiplicative growth (constant-ratio growth has ZERO
    dispersion -- std() measures spread, not magnitude, so it would misleadingly bucket as SRRI 1)."""
    nav = pd.Series([100.0, 1.0, 100.0, 1.0, 100.0, 1.0,
                     100.0, 1.0, 100.0, 1.0, 100.0, 1.0, 100.0])
    result = compute_srri(nav)
    assert result["srri"] == 0
    assert result["method"] == "ANOMALOUS_VOL"


def test_srri_metrics_returns_the_bucket_and_the_volatility_used():
    nav = pd.Series([100.0 * (1.001 ** i) for i in range(13)])
    result = compute_srri(nav)
    rows = srri_metrics(nav)
    assert rows == [
        ("srri_nav", float(result["srri"]), 0),
        ("srri_volatility", result["volatility_ann"], 0),
    ]
