# proyecto3/tests/test_pit_metrics_ratio_floor_fnd0405.py
# -*- coding: utf-8 -*-
"""FND-0405: the PIT backtest applies the SAME volatility floor as production (plan G-02: the backtest evaluates the production model), from the same two constants read at call time, and the floor is part of the
PIT risk cache key. R-7: no DB; a synthetic panel through the real expanding-metrics code, pinned to P2's own scalar function (the oracle test_pit_metrics.py uses).
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import numpy as np
import pandas as pd
import pytest

from proyecto3.src.pit_metrics import expanding_risk_metrics, load_p2_calc
from shared import config

returns = load_p2_calc("returns")
N = 48
DATES = pd.date_range("2018-01-31", periods=N, freq=pd.offsets.MonthEnd())


@pytest.fixture(autouse=True)
def _row_convention(monkeypatch):
    monkeypatch.setattr(config, "ANNUALIZATION_BY_DATES_ENABLED", False)       # the stored-row convention, as test_pit_metrics.py states it


def _panel():
    rng = np.random.default_rng(5)
    flat = 100.0 * np.cumprod(1.0 + 0.002 + 0.00005 * rng.standard_normal(N))        # monthly std 0.005%: annualised ~0.017%, far under the floor
    normal = 100.0 * np.cumprod(1.0 + 0.004 + 0.012 * rng.standard_normal(N))        # annualised ~4%
    return pd.DataFrame({"FLAT": flat, "NORM": normal}, index=DATES)


def _sharpe(monkeypatch, enabled: bool) -> pd.DataFrame:
    monkeypatch.setattr(config, "RATIO_VOL_FLOOR_ENABLED", enabled)
    return expanding_risk_metrics(_panel(), None, None)["sharpe"]


def test_the_backtest_withdraws_the_sharpe_under_the_floor_and_keeps_the_ordinary_one(monkeypatch):
    on, off = _sharpe(monkeypatch, True), _sharpe(monkeypatch, False)
    assert on["FLAT"].iloc[24:].isna().all() and off["FLAT"].iloc[24:].notna().all()           # the figure production no longer publishes
    pd.testing.assert_series_equal(on["NORM"], off["NORM"])                                    # an ordinary fund is untouched


def test_the_backtest_equals_productions_scalar_function_at_the_last_date_with_the_floor_on(monkeypatch):
    monkeypatch.setattr(config, "RATIO_VOL_FLOOR_ENABLED", True)
    panel = _panel()
    got = expanding_risk_metrics(panel, None, None)["sharpe"].iloc[-1]
    want = {isin: returns.sharpe_ratio_from_returns(panel[isin].pct_change().dropna().to_numpy(), config.RISK_FREE_RATE_ANN, 12) for isin in ("FLAT", "NORM")}
    assert np.isnan(want["FLAT"]) and np.isnan(got["FLAT"])                                    # production and the backtest both withdraw it
    assert np.isfinite(want["NORM"]) and got["NORM"] == pytest.approx(want["NORM"], rel=1e-6)   # and agree on the ordinary fund


def test_the_floor_value_is_part_of_the_pit_risk_cache_key_only_while_it_is_on(monkeypatch):
    from proyecto3.src.pit_run import risk_cache_flags
    monkeypatch.setattr(config, "RATIO_VOL_FLOOR_ENABLED", True)
    assert risk_cache_flags(config)["ratio_vol_floor"] == 0.001
    monkeypatch.setattr(config, "RATIO_MIN_VOL_ANN", 0.003)
    assert risk_cache_flags(config)["ratio_vol_floor"] == 0.003                              # a different floor is a different Sharpe: a different key
    monkeypatch.setattr(config, "RATIO_VOL_FLOOR_ENABLED", False)
    assert "ratio_vol_floor" not in risk_cache_flags(config)                                 # OFF: today's keys, the warm cache stays valid
