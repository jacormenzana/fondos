# proyecto2/tests/calculations/test_ratio_vol_floor_fnd0405.py
# -*- coding: utf-8 -*-
"""FND-0405: P2 does not publish a Sharpe / return-to-volatility ratio whose own annualised volatility is below RATIO_MIN_VOL_ANN (0.001), behind RATIO_VOL_FLOOR_ENABLED (rollback = False).

R-7: no DB, no pipeline; synthetic series. A NaN ratio is written as a NULL row (never a stale value); the Sortino keeps its own guard on the downside deviation (FND-0075), unchanged here.
"""
import math

import numpy as np
import pandas as pd
import pytest

from shared import config
from shared.ratio_floor import degenerate_vol_mask, ratio_vol_floor, vol_is_degenerate
from src.calculations.returns import ret_vol_simple, sharpe_ratio, sharpe_ratio_from_returns, sortino_ratio_from_returns


def _returns(vol_monthly: float, n: int = 36, mean: float = 0.003) -> np.ndarray:
    """n monthly returns with exactly the standard deviation `vol_monthly` (ddof=1) around `mean`."""
    x = np.where(np.arange(n) % 2 == 0, 1.0, -1.0)
    x = (x - x.mean()) / x.std(ddof=1)
    return mean + vol_monthly * x


def _nav(rets: np.ndarray) -> pd.Series:
    return pd.Series(100.0 * np.cumprod(1.0 + rets))


ANN = math.sqrt(12)
LOW, OK = 0.0001, 0.01                      # monthly std: annualised 0.00035 (under the floor) and 0.035 (far above it)


def test_the_shipped_floor_is_the_sortino_guards_number_and_ships_on():
    from src.calculations import returns
    assert config.RATIO_VOL_FLOOR_ENABLED is True and config.RATIO_MIN_VOL_ANN == 0.001 == returns._MIN_DOWNSIDE_DEV_ANN


def test_the_helper_reads_the_switch_at_call_time_and_nan_is_never_degenerate(monkeypatch):
    monkeypatch.setattr(config, "RATIO_VOL_FLOOR_ENABLED", True)
    assert ratio_vol_floor() == 0.001
    assert vol_is_degenerate(0.0) and vol_is_degenerate(0.0009999) and not vol_is_degenerate(0.001) and not vol_is_degenerate(0.5) and not vol_is_degenerate(float("nan"))
    assert list(degenerate_vol_mask(np.array([0.0, 0.0005, 0.001, 0.2, np.nan]))) == [True, True, False, False, False]
    monkeypatch.setattr(config, "RATIO_VOL_FLOOR_ENABLED", False)
    assert ratio_vol_floor() == 0.0 and not vol_is_degenerate(0.0) and not degenerate_vol_mask(np.array([0.0, 0.0005])).any()


def test_a_sharpe_on_a_near_zero_volatility_is_not_published_and_an_ordinary_one_is(monkeypatch):
    monkeypatch.setattr(config, "RATIO_VOL_FLOOR_ENABLED", True)
    assert math.isnan(sharpe_ratio_from_returns(_returns(LOW), 0.0, periods_per_year=12))
    assert math.isfinite(sharpe_ratio_from_returns(_returns(OK), 0.0, periods_per_year=12))
    assert math.isnan(sharpe_ratio(_nav(_returns(LOW)), 0.0, dates=None))
    assert math.isfinite(sharpe_ratio(_nav(_returns(OK)), 0.0, dates=None))


def test_the_return_to_volatility_ratio_follows_the_same_floor(monkeypatch):
    monkeypatch.setattr(config, "RATIO_VOL_FLOOR_ENABLED", True)
    assert math.isnan(ret_vol_simple(_nav(_returns(LOW))))
    assert math.isfinite(ret_vol_simple(_nav(_returns(OK))))


def test_the_boundary_is_the_annualised_volatility_not_the_monthly_one(monkeypatch):
    monkeypatch.setattr(config, "RATIO_VOL_FLOOR_ENABLED", True)
    just_under, just_over = 0.00099 / ANN, 0.00101 / ANN                          # annualised 0.099% and 0.101%
    assert math.isnan(sharpe_ratio_from_returns(_returns(just_under), 0.0, 12)) and math.isfinite(sharpe_ratio_from_returns(_returns(just_over), 0.0, 12))


def test_switching_it_off_restores_the_previous_behaviour_exactly(monkeypatch):
    monkeypatch.setattr(config, "RATIO_VOL_FLOOR_ENABLED", False)
    r = _returns(LOW)
    assert math.isfinite(sharpe_ratio_from_returns(r, 0.0, 12)) and math.isfinite(ret_vol_simple(_nav(r)))               # the figure the floor withdraws
    monkeypatch.setattr(config, "RATIO_VOL_FLOOR_ENABLED", True)
    assert math.isnan(sharpe_ratio_from_returns(r, 0.0, 12)) and math.isnan(ret_vol_simple(_nav(r)))                    # the same input, the floor back on


def test_a_volatility_above_the_floor_gives_the_same_number_with_the_floor_on_and_off(monkeypatch):
    r = _returns(OK)
    monkeypatch.setattr(config, "RATIO_VOL_FLOOR_ENABLED", True)
    on = sharpe_ratio_from_returns(r, 0.02, 12)
    monkeypatch.setattr(config, "RATIO_VOL_FLOOR_ENABLED", False)
    assert on == sharpe_ratio_from_returns(r, 0.02, 12)                                                                   # the floor changes nothing for an ordinary fund


def test_the_sortino_keeps_its_own_guard_and_is_not_changed_by_the_sharpe_floor(monkeypatch):
    r = _returns(OK)
    monkeypatch.setattr(config, "RATIO_VOL_FLOOR_ENABLED", True)
    on = sortino_ratio_from_returns(r, 0.0, 12)
    monkeypatch.setattr(config, "RATIO_VOL_FLOOR_ENABLED", False)
    assert on == sortino_ratio_from_returns(r, 0.0, 12)


def test_a_withdrawn_ratio_is_written_as_a_null_row_never_as_a_stale_value():
    from src.writers.metrics_writer import _metric_row_tuples
    rows = _metric_row_tuples("X", [{"metric": "sharpe", "value": float("nan"), "real_flag": 0}, {"metric": "vol_ann", "value": 0.0005, "real_flag": 0}],
                              "since_inception", "2026-11-10", "v1", "alg", "batch")
    assert rows[0][3] is None and rows[1][3] == 0.0005                                                                    # the upsert is `value = excluded.value`: the old Sharpe is replaced by NULL
