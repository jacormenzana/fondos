"""FND-0242: Sortino needs enough downside observations, behind SORTINO_MIN_DOWNSIDE_COUNT_ENABLED.

A denominator built from 2-4 shortfalls below the MAR is noise (relative SE ~ 0.7 / sqrt(k)); the FND-0075 magnitude floor is a cliff that
lets one such fund through (Sortino 12.95, Monetario peer kurtosis 0.8 -> 36.8). Default OFF must be the stored behaviour bit-for-bit;
the regime path must be untouched (P3 reads its percentiles); scalar and rolling must still agree.
"""
import math

import numpy as np
import pandas as pd
import pytest

from shared import config, sortino_reliability
from src.calculations.returns import sharpe_ratio, sortino_ratio, sortino_ratio_from_returns
from src.calculations.rolling_stats import _roll_sortino
from src.utils import family_versions

RF = 0.025


@pytest.fixture
def switch(monkeypatch):
    return lambda on: monkeypatch.setattr(config, "SORTINO_MIN_DOWNSIDE_COUNT_ENABLED", on, raising=False)


def _nav(rets):
    return pd.Series(100.0 * np.concatenate([[1.0], np.cumprod(1 + np.asarray(rets, dtype=float))]))


def _money_fund(n=51, k=4, up=0.0033, down=0.0005):
    """A money fund far above the MAR (0.208%/month) with only k shortfalls of ~0.16% each."""
    r = np.full(n, up)
    r[np.linspace(5, n - 5, k).astype(int)] = down
    return r


# ---------------------------------------------------------------- the switch and its wiring
def test_switch_is_off_by_default_in_the_bundle_and_mapped_to_the_families_it_changes():
    assert config.SORTINO_MIN_DOWNSIDE_COUNT_ENABLED is False
    assert "SORTINO_MIN_DOWNSIDE_COUNT_ENABLED" in config.P2_BUNDLE_FLAGS
    assert family_versions.flag_families("SORTINO_MIN_DOWNSIDE_COUNT_ENABLED") == ("risk", "rolling")
    assert (config.SORTINO_MIN_DOWNSIDE_OBS, config.SORTINO_MIN_DOWNSIDE_SHARE) == (3, 0.15)


@pytest.mark.parametrize("n, expected", [(11, 3), (12, 3), (19, 3), (24, 4), (36, 6), (60, 9), (120, 18), (171, 26)])
def test_the_minimum_count_is_window_scaled_with_a_floor_of_three(n, expected):
    assert sortino_reliability.min_downside_obs(n) == expected


def test_off_everything_is_reliable_and_on_it_counts_returns_strictly_below_the_mar():
    three = np.array([0.03] * 7 + [0.01, 0.015, 0.0])           # 3 below a 2% MAR, n = 10 -> needs max(3, 2) = 3
    two = np.array([0.03] * 8 + [0.01, 0.0])
    tied = np.array([0.03] * 7 + [0.02, 0.02, 0.0])             # a return EQUAL to the MAR is not a shortfall
    for r in (three, two, tied):
        assert sortino_reliability.downside_count_reliable(r, 0.02, enabled=False)
    assert sortino_reliability.downside_count_reliable(three, 0.02, enabled=True)
    assert not sortino_reliability.downside_count_reliable(two, 0.02, enabled=True)
    assert not sortino_reliability.downside_count_reliable(tied, 0.02, enabled=True)


# ---------------------------------------------------------------- the case that exposed it
def test_off_a_money_fund_with_four_shortfalls_keeps_a_large_ratio_which_on_removes(switch):
    nav = _nav(_money_fund())
    switch(False)
    legacy = sortino_ratio(nav, RF)
    assert legacy > 5                                     # finite and big: the FND-0075 magnitude floor does not catch it
    switch(True)
    assert math.isnan(sortino_ratio(nav, RF))             # k = 4 < max(3, ceil(0.15 * 50)) = 8


def test_on_the_cliff_is_gone_the_decision_does_not_depend_on_the_denominator_magnitude(switch):
    """Shortfalls of any size, same count: the rule decides on k, not on 0.00101 vs 0.0009."""
    switch(True)
    for down in (-0.02, 0.0, 0.0005, 0.0019):
        assert math.isnan(sortino_ratio(_nav(_money_fund(k=4, down=down)), RF)), down


def test_on_a_well_estimated_fund_is_unchanged(switch):
    rng = np.random.default_rng(0)
    nav = _nav(rng.normal(0.004, 0.03, 120))               # ~45% of months below the MAR
    switch(False)
    off = sortino_ratio(nav, RF)
    switch(True)
    assert sortino_ratio(nav, RF) == off and not math.isnan(off)


def test_off_is_the_stored_formula_for_every_k(switch):
    """Off must equal (annualised return - rf) / downside deviation, with only the FND-0075 magnitude floor."""
    from src.calculations.returns import annualized_return, downside_deviation_ann
    switch(False)
    for k in range(0, 12):
        nav = _nav(_money_fund(n=60, k=k, down=-0.01))
        dd = downside_deviation_ann(nav.pct_change().dropna().to_numpy(), RF / 12, 12)
        expected = math.nan if math.isnan(dd) else (annualized_return(nav) - RF) / dd
        got = sortino_ratio(nav, RF)
        assert (math.isnan(got) and math.isnan(expected)) or got == expected, k


# ---------------------------------------------------------------- scalar, rolling and regime paths
@pytest.mark.parametrize("on", [False, True])
@pytest.mark.parametrize("k", [2, 4, 8, 12])
def test_rolling_and_scalar_paths_agree_under_both_settings(switch, on, k):
    switch(on)
    nav = _nav(_money_fund(n=51, k=k, down=0.0))
    s = sortino_ratio(nav, RF)
    r = _roll_sortino(nav.to_numpy(), 12, RF)
    assert (math.isnan(s) and math.isnan(r)) or s == pytest.approx(r, rel=1e-9)


def test_the_regime_path_is_untouched_by_the_switch(switch):
    rets = _money_fund(n=8, k=3, down=0.0)                 # few months by design (one macro regime)
    switch(False)
    off = sortino_ratio_from_returns(rets, RF)
    switch(True)
    assert sortino_ratio_from_returns(rets, RF) == off


def test_sharpe_is_untouched(switch):
    nav = _nav(_money_fund())
    switch(False)
    off = sharpe_ratio(nav, RF)
    switch(True)
    assert sharpe_ratio(nav, RF) == off
