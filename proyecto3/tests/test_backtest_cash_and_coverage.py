# proyecto3/tests/test_backtest_cash_and_coverage.py
# -*- coding: utf-8 -*-
"""
Backtester cash leg and coverage reporting -- FND-0187 (residue to cash), FND-0188 (no silent
rescale / unscored regimes reported), FND-0197 (cash accrues the ECB deposit rate).

Pure functions on in-memory frames; no DB (R-7). Run from repo root:
    python -m pytest proyecto3/tests/test_backtest_cash_and_coverage.py -v
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto3.src import backtesting as bt
from proyecto3.src.backtesting import Backtester, _cash_return, _portfolio_return

MONTHS = pd.date_range("2020-01-31", periods=14, freq=pd.offsets.MonthEnd())


@pytest.fixture(autouse=True)
def _reset_warn_once():
    bt._WARNED.clear()
    yield
    bt._WARNED.clear()


def _rates(value, start="2019-12-31", periods=20):
    idx = pd.date_range(start, periods=periods, freq=pd.offsets.MonthEnd())
    return pd.Series(value, index=idx, dtype=float)


def _nav(a_end=110.0, b_end=100.0):
    """A goes 100 -> a_end between the first and the 2nd date; B 100 -> b_end."""
    nav = pd.DataFrame({"A": 100.0, "B": 100.0}, index=MONTHS)
    nav.loc[MONTHS[1]:, "A"] = a_end
    nav.loc[MONTHS[1]:, "B"] = b_end
    return nav


# ---------------- cash leg (FND-0197) ----------------

def test_cash_accrues_monthly_rate_compounded():
    r = _cash_return(_rates(3.6), MONTHS[0], MONTHS[12])
    assert r == pytest.approx(1.003 ** 12 - 1, abs=1e-12)


def test_cash_uses_the_rate_in_force_at_the_start_of_each_month_not_a_future_one():
    # rate jumps from 0 to 12% only AT 2020-02-29; the month ending 2020-02-29 started 2020-01-31 -> 0%.
    s = _rates(0.0)
    s.loc["2020-02-29":] = 12.0
    one_month = _cash_return(s, pd.Timestamp("2020-01-31"), pd.Timestamp("2020-02-29"))
    assert one_month == pytest.approx(0.0, abs=1e-12)
    two_months = _cash_return(s, pd.Timestamp("2020-01-31"), pd.Timestamp("2020-03-31"))
    assert two_months == pytest.approx(0.01, abs=1e-12)          # 2nd month accrues 12%/12


def test_cash_handles_negative_rates():
    r = _cash_return(_rates(-0.5), MONTHS[0], MONTHS[12])
    assert np.isfinite(r) and r < 0
    assert r == pytest.approx((1 - 0.005 / 12) ** 12 - 1, abs=1e-12)


def test_cash_without_series_accrues_zero_and_warns_once(caplog):
    with caplog.at_level("WARNING", logger="proyecto3.src.backtesting"):
        for _ in range(5):
            assert _cash_return(pd.Series(dtype=float), MONTHS[0], MONTHS[3]) == 0.0
    assert sum("no rate_deposit series" in r.getMessage() for r in caplog.records) == 1


def test_cash_before_series_start_accrues_zero():
    s = _rates(6.0, start="2020-06-30", periods=10)
    r = _cash_return(s, MONTHS[0], MONTHS[4])                    # all 4 months precede the series
    assert r == 0.0


# ---------------- portfolio return: no silent rescale (FND-0188) ----------------

def test_fully_invested_portfolio_return_is_the_weighted_fund_return():
    ret, share = _portfolio_return(_nav(), {"A": 0.5, "B": 0.5}, MONTHS[0], 1, _rates(0.0))
    assert ret == pytest.approx(0.05, abs=1e-9)
    assert share == pytest.approx(1.0)


def test_unallocated_weight_is_cash_not_rescaled_to_100pct():
    # 60% invested (only A) + 40% cash at 0% -> 0.6 * 10%, NOT 10% (the old silent renormalisation).
    ret, share = _portfolio_return(_nav(), {"A": 0.6}, MONTHS[0], 1, _rates(0.0))
    assert ret == pytest.approx(0.06, abs=1e-9)
    assert share == pytest.approx(1.0)


def test_cash_share_earns_the_deposit_rate():
    ret, _ = _portfolio_return(_nav(), {"A": 0.6}, MONTHS[0], 1, _rates(3.6))
    assert ret == pytest.approx(0.6 * 0.10 + 0.4 * 0.003, abs=1e-9)


def test_cash_lowers_realised_volatility_vs_fully_invested():
    nav = _nav()
    nav["A"] = [100, 110, 99, 120, 108, 130, 117, 140, 126, 150, 135, 160, 144, 170]
    rets_full, rets_cash = [], []
    for d in MONTHS[:-1]:
        rets_full.append(_portfolio_return(nav, {"A": 1.0}, d, 1, _rates(0.0))[0])
        rets_cash.append(_portfolio_return(nav, {"A": 0.6}, d, 1, _rates(0.0))[0])
    assert np.std(rets_cash) < np.std(rets_full)


def test_fund_without_nav_keeps_its_weight_in_cash_and_coverage_reports_it():
    nav = _nav()
    nav["B"] = np.nan                                            # B has no NAV in the window
    ret, share = _portfolio_return(nav, {"A": 0.5, "B": 0.5}, MONTHS[0], 1, _rates(0.0))
    assert ret == pytest.approx(0.05, abs=1e-9)                  # 0.5*10% + 0.5 cash@0, not 10%
    assert share == pytest.approx(0.5)


def test_empty_portfolio_or_no_covered_fund_is_not_fabricated_as_cash():
    assert _portfolio_return(_nav(), {}, MONTHS[0], 1, _rates(3.6)) == (None, None)
    nav = _nav()
    nav[["A", "B"]] = np.nan
    ret, share = _portfolio_return(nav, {"A": 0.5, "B": 0.5}, MONTHS[0], 1, _rates(3.6))
    assert ret is None and share == 0.0


def test_window_beyond_history_returns_none():
    assert _portfolio_return(_nav(), {"A": 1.0}, MONTHS[-1], 1, _rates(0.0)) == (None, None)


# ---------------- coverage report (FND-0188) ----------------

def test_summary_reports_months_without_scored_candidates():
    idx = pd.date_range("2020-01-31", periods=4, freq=pd.offsets.MonthEnd())
    res = pd.DataFrame({
        "regime": ["Expansion", "Expansion", "Crisis_Financiera", "Crisis_Financiera"],
        "n_funds": [10, 10, 0, 0],
        "cash_weight": [0.0, 0.0, None, None],
        "ret_1m": [0.01, 0.02, None, None], "bench_1m": [0.0, 0.0, None, None], "excess_1m": [0.01, 0.02, None, None],
        "ret_3m": [None] * 4, "bench_3m": [None] * 4, "excess_3m": [None] * 4,
        "ret_12m": [None] * 4, "bench_12m": [None] * 4, "excess_12m": [None] * 4,
        "cov_12m": [1.0, 1.0, None, None],
    }, index=idx)
    text = Backtester.summary(object.__new__(Backtester), res)
    assert "COBERTURA" in text and "Crisis_Financiera: 2" in text and "de 4 meses" in text


def test_summary_without_gaps_has_no_coverage_warning():
    idx = pd.date_range("2020-01-31", periods=2, freq=pd.offsets.MonthEnd())
    res = pd.DataFrame({
        "regime": ["Expansion"] * 2, "n_funds": [10, 10],
        "ret_1m": [0.01, 0.02], "bench_1m": [0.0, 0.0], "excess_1m": [0.01, 0.02],
        "ret_3m": [None] * 2, "bench_3m": [None] * 2, "excess_3m": [None] * 2,
        "ret_12m": [None] * 2, "bench_12m": [None] * 2, "excess_12m": [None] * 2,
        "cov_12m": [1.0, 1.0],
    }, index=idx)
    assert "COBERTURA" not in Backtester.summary(object.__new__(Backtester), res)
