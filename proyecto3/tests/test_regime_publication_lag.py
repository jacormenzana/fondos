# proyecto3/tests/test_regime_publication_lag.py
# -*- coding: utf-8 -*-
"""
Point-in-time publication lags for the regime inputs -- FND-0194.

The value dated month m is observable only from m+lag; lags are applied to the RAW series before
yoy/diff derivation, only when requested (backtester), never on the live path.
Pure: a fake connection returns canned series_macro rows (R-7, no DB).

    python -m pytest proyecto3/tests/test_regime_publication_lag.py -v
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto3.src.regime_classifier import _apply_publication_lags, _load_macro_series
from shared.config import REGIME_PUBLICATION_LAG_MONTHS

N = 40
MONTHS = pd.date_range("2019-01-31", periods=N, freq=pd.offsets.MonthEnd())


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, *_args, **_kw):
        return _Result(self._rows)


def _rows():
    rows = []
    for i, d in enumerate(MONTHS):
        day = d.date()
        rows.append((day, "ipc_index", "ES", 100.0 + i ** 1.5))
        rows.append((day, "ipc_index", "EU", 100.0 + 0.8 * i ** 1.5))
        rows.append((day, "cli", "EU", 99.0 + np.sin(i / 3.0)))
        rows.append((day, "rate_deposit", "EU", 0.5 + 0.01 * i))
        rows.append((day, "oil_wti", "GLOBAL", 50.0 + i))
    return rows


@pytest.fixture(scope="module")
def plain():
    return _load_macro_series(_FakeConn(_rows()))


@pytest.fixture(scope="module")
def lagged():
    return _load_macro_series(_FakeConn(_rows()), {"ipc_index": 2, "cli": 2})


def test_default_is_unlagged_and_unchanged(plain):
    same = _load_macro_series(_FakeConn(_rows()), None)
    pd.testing.assert_frame_equal(plain, same)
    assert plain.index.max() == MONTHS[-1]


def test_empty_lag_dict_is_a_no_op(plain):
    pd.testing.assert_frame_equal(plain, _load_macro_series(_FakeConn(_rows()), {}))


def test_lagged_series_value_at_t_is_the_unlagged_value_of_t_minus_lag(plain, lagged):
    t = MONTHS[30]
    prev = MONTHS[28]                                            # lag = 2 months
    assert lagged.loc[t, "cli_eu"] == pytest.approx(plain.loc[prev, "cli_eu"])
    assert lagged.loc[t, "ipc_yoy_es"] == pytest.approx(plain.loc[prev, "ipc_yoy_es"])


def test_derived_yoy_is_computed_after_the_shift(plain, lagged):
    t = MONTHS[35]
    raw = {d: 100.0 + i ** 1.5 for i, d in enumerate(MONTHS)}
    expected = raw[MONTHS[33]] / raw[MONTHS[21]] - 1.0           # yoy of the value visible at t
    assert lagged.loc[t, "ipc_yoy_es"] == pytest.approx(expected)


def test_market_series_are_not_lagged(plain, lagged):
    for col in ("oil_yoy", "rate_deposit"):
        pd.testing.assert_series_equal(plain[col], lagged[col])


def test_no_future_months_are_invented(plain, lagged):
    assert lagged.index.max() == plain.index.max()


def test_lagged_series_has_no_value_before_it_could_have_been_published(plain, lagged):
    # first CLI observation (Jan-2019) is only visible from Mar-2019
    assert pd.isna(lagged.loc[MONTHS[0], "cli_eu"]) and pd.isna(lagged.loc[MONTHS[1], "cli_eu"])
    assert not pd.isna(lagged.loc[MONTHS[2], "cli_eu"])


def test_apply_publication_lags_only_touches_listed_indicators():
    df = pd.DataFrame({
        "date": [MONTHS[0], MONTHS[0], MONTHS[1]],
        "indicator": ["cli", "oil_wti", "cli"],
        "geography": ["EU", "GLOBAL", "EU"],
        "value": [1.0, 2.0, 3.0],
    })
    out = _apply_publication_lags(df, {"cli": 1})
    cli = out[out["indicator"] == "cli"].sort_values("date")
    assert list(cli["date"]) == [MONTHS[1]]                      # Jan->Feb kept, Feb->Mar dropped (> cap)
    assert list(out[out["indicator"] == "oil_wti"]["date"]) == [MONTHS[0]]


def test_config_lags_cover_the_release_lagged_inputs():
    for ind in ("ipc_index", "cli", "m3_yoy", "m2_yoy", "m2_global_yoy"):
        assert REGIME_PUBLICATION_LAG_MONTHS.get(ind, 0) >= 1
    for market in ("oil_wti", "vix", "spread_hy", "rate_deposit", "dxy", "gold"):
        assert market not in REGIME_PUBLICATION_LAG_MONTHS
