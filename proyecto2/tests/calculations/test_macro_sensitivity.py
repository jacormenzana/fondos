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

from src.calculations.macro_sensitivity import compute_macro_sensitivity, MIN_OBS


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
