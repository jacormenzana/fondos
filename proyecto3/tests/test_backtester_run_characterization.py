# proyecto3/tests/test_backtester_run_characterization.py
# -*- coding: utf-8 -*-
"""
Characterization tests for Backtester.run()/summary() -- FND-0191 step (c), Wave B.

They pin the CURRENT behaviour to numbers computed independently in this file (closed-form fund growth),
not to a recorded snapshot, so the point-in-time engine + vectorized return layer (FND-0159 / FND-0191)
can be rewritten and still be checked: same fixture -> same numbers (or an explicitly justified change).

The Backtester is built with __new__ and injected fakes (NAV matrix, cash series, regime history,
selection cache): no DB, no pipeline imports (R-7).

    python -m pytest proyecto3/tests/test_backtester_run_characterization.py -v
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
from proyecto3.src.backtesting import Backtester, FORWARD_WINDOWS

N_MONTHS = 30
DATES = pd.date_range("2018-01-31", periods=N_MONTHS, freq=pd.offsets.MonthEnd())
GROWTH = {"A": 0.010, "B": 0.020, "C": -0.010, "D": 0.005, "E": 0.030, "F": 0.000}   # monthly, unselected F
CASH_RATE_PCT = 2.4                                                                    # -> 0.2% per month


def _nav_matrix() -> pd.DataFrame:
    i = np.arange(N_MONTHS)
    return pd.DataFrame({k: 100.0 * (1.0 + g) ** i for k, g in GROWTH.items()}, index=DATES)


def _fund_ret(isin: str, months: int) -> float:
    return (1.0 + GROWTH[isin]) ** months - 1.0


def _cash_ret(months: int) -> float:
    return (1.0 + CASH_RATE_PCT / 100.0 / 12.0) ** months - 1.0


def _selection(rows_by_sub: dict) -> dict:
    cols = ["isin", "weight"]
    return {sub: pd.DataFrame(rows, columns=cols) for sub, rows in rows_by_sub.items()}


FULL = _selection({
    "Defensiva":   [("A", 0.5), ("B", 0.5)],
    "Equilibrada": [("C", 0.4), ("D", 0.6)],
    "Dinamica":    [("E", 1.0)],
})
EMPTY = {"Defensiva": pd.DataFrame(), "Equilibrada": pd.DataFrame(), "Dinamica": pd.DataFrame()}
# sub weights of FULL's regime: .2/.5/.3 -> master A .10 B .10 C .20 D .30 E .30 (sums to 1.0)
FULL_MASTER = {"A": 0.10, "B": 0.10, "C": 0.20, "D": 0.30, "E": 0.30}


class _FakeClassifier:
    def __init__(self, hist):
        self._hist = hist

    def classify_historical(self):
        return self._hist.copy()


def _hist(regimes, weights=(0.2, 0.5, 0.3), start=0):
    idx = DATES[start:start + len(regimes)]
    return pd.DataFrame({
        "regime": regimes,
        "weight_defensive": weights[0], "weight_balanced": weights[1], "weight_dynamic": weights[2],
    }, index=idx)


def _backtester(hist, selections) -> Backtester:
    b = object.__new__(Backtester)
    b.conn = None
    b.score_version = "v1"
    b._nav = _nav_matrix()
    b._cash = pd.Series(CASH_RATE_PCT, index=pd.date_range("2017-12-31", periods=40, freq=pd.offsets.MonthEnd()))
    b._clf = _FakeClassifier(hist)
    b._selection_cache = dict(selections)
    return b


@pytest.fixture(autouse=True)
def _quiet_warn_once():
    bt._WARNED.clear()
    yield
    bt._WARNED.clear()


# ---------------- output shape ----------------

def test_run_returns_one_row_per_month_with_the_documented_columns():
    res = _backtester(_hist(["Expansion"] * 6), {"Expansion": FULL}).run(start_date="2018-01-01")
    assert len(res) == 6 and res.index.name == "date"
    expected = {"regime", "n_funds", "cash_weight"}
    for w in FORWARD_WINDOWS:
        expected |= {f"ret_{w}m", f"cov_{w}m", f"bench_{w}m", f"excess_{w}m"}
    assert expected <= set(res.columns)


def test_start_and_end_date_filter_the_history():
    b = _backtester(_hist(["Expansion"] * 12), {"Expansion": FULL})
    res = b.run(start_date="2018-04-01", end_date="2018-09-30")
    assert list(res.index) == list(DATES[3:9])


def test_empty_classification_returns_empty_frame():
    b = _backtester(pd.DataFrame(), {})
    assert b.run().empty


# ---------------- returns pinned to closed-form numbers ----------------

@pytest.mark.parametrize("w", FORWARD_WINDOWS)
def test_fully_invested_return_is_the_master_weighted_fund_return(w):
    res = _backtester(_hist(["Expansion"] * 6), {"Expansion": FULL}).run(start_date="2018-01-01")
    expected = sum(wt * _fund_ret(i, w) for i, wt in FULL_MASTER.items())
    if w <= N_MONTHS - 6:
        assert res[f"ret_{w}m"].iloc[0] == pytest.approx(expected, abs=1e-6)
        assert res[f"cov_{w}m"].iloc[0] == pytest.approx(1.0)


def test_benchmark_is_the_equal_weighted_mean_of_all_valid_funds():
    res = _backtester(_hist(["Expansion"] * 3), {"Expansion": FULL}).run(start_date="2018-01-01")
    for w in FORWARD_WINDOWS:
        expected = np.mean([_fund_ret(i, w) for i in GROWTH])
        assert res[f"bench_{w}m"].iloc[0] == pytest.approx(expected, abs=1e-6)


def test_excess_is_return_minus_benchmark():
    res = _backtester(_hist(["Expansion"] * 3), {"Expansion": FULL}).run(start_date="2018-01-01")
    for w in FORWARD_WINDOWS:
        assert res[f"excess_{w}m"].iloc[0] == pytest.approx(res[f"ret_{w}m"].iloc[0] - res[f"bench_{w}m"].iloc[0])


def test_windows_running_past_the_nav_history_are_empty():
    res = _backtester(_hist(["Expansion"] * 6, start=N_MONTHS - 6), {"Expansion": FULL}).run(start_date="2018-01-01")
    last = res.iloc[-1]
    assert all(pd.isna(last[f"ret_{w}m"]) for w in FORWARD_WINDOWS)
    assert all(pd.isna(last[f"bench_{w}m"]) for w in FORWARD_WINDOWS)
    # 2 months before the end: 1m window exists, 3m/12m do not
    two_before = res.iloc[-3]
    assert not pd.isna(two_before["ret_1m"]) and pd.isna(two_before["ret_3m"])


# ---------------- per-date sub-portfolio weights (Fase 6 hook) ----------------

def test_sub_weights_are_taken_per_date_not_per_regime_label():
    hist = _hist(["Expansion"] * 2)
    hist.iloc[1, hist.columns.get_loc("weight_defensive")] = 0.5
    hist.iloc[1, hist.columns.get_loc("weight_balanced")] = 0.2        # D/C shrink, A/B grow on the 2nd date
    res = _backtester(hist, {"Expansion": FULL}).run(start_date="2018-01-01")
    m1 = {"A": 0.25, "B": 0.25, "C": 0.08, "D": 0.12, "E": 0.30}
    expected_2nd = sum(wt * _fund_ret(i, 1) for i, wt in m1.items())
    assert res["ret_1m"].iloc[1] == pytest.approx(expected_2nd, abs=1e-6)
    assert res["ret_1m"].iloc[1] != pytest.approx(res["ret_1m"].iloc[0], abs=1e-9)


# ---------------- cash residue and unscored regimes (FND-0187/0188/0197) ----------------

def test_underinvested_portfolio_holds_the_residue_in_cash_at_the_deposit_rate():
    # Defensiva only (sub weight 0.6) -> 60% invested, 40% cash
    sel = _selection({"Defensiva": [("A", 0.5), ("B", 0.5)], "Equilibrada": [], "Dinamica": []})
    res = _backtester(_hist(["Expansion"] * 3, weights=(0.6, 0.25, 0.15)), {"Expansion": sel}).run(start_date="2018-01-01")
    for w in FORWARD_WINDOWS:
        invested = 0.3 * _fund_ret("A", w) + 0.3 * _fund_ret("B", w)
        assert res[f"ret_{w}m"].iloc[0] == pytest.approx(invested + 0.4 * _cash_ret(w), abs=1e-6)
    assert res["cash_weight"].iloc[0] == pytest.approx(0.4, abs=1e-9)
    assert res["n_funds"].iloc[0] == 2


def test_unscored_regime_months_are_not_evaluated_and_are_reported(caplog):
    hist = _hist(["Expansion"] * 3 + ["Crisis_Financiera"] * 3)
    b = _backtester(hist, {"Expansion": FULL, "Crisis_Financiera": EMPTY})
    with caplog.at_level("WARNING", logger="proyecto3.src.backtesting"):
        res = b.run(start_date="2018-01-01")
    crisis = res[res["regime"] == "Crisis_Financiera"]
    assert (crisis["n_funds"] == 0).all()
    assert crisis[[f"ret_{w}m" for w in FORWARD_WINDOWS]].isna().all().all()
    assert any("Crisis_Financiera" in r.getMessage() and "NOT evaluated" in r.getMessage() for r in caplog.records)
    text = b.summary(res)
    assert "COBERTURA" in text and "Crisis_Financiera: 3" in text


# ---------------- selection caching + real engine integration ----------------

def _candidates(n=6):
    rows = [{
        "isin": f"X{i}", "score_total": 1.0 + 0.1 * i, "fund_name": f"Fund {i}", "fund_nature": f"N{i % 2}",
        "management_company": f"M{i}", "fund_family_id": f"FAM{i}",
    } for i in range(n)]
    return pd.DataFrame(rows)


def test_selection_is_computed_once_per_regime_label(monkeypatch):
    calls = []

    def fake_load(conn, score_version, regime):
        calls.append(regime)
        return {"Defensiva": _candidates(), "Equilibrada": pd.DataFrame(), "Dinamica": pd.DataFrame()}

    monkeypatch.setattr(bt, "_load_candidates", fake_load)
    b = _backtester(_hist(["Expansion"] * 5 + ["Contraccion"] * 3), {})
    nav = _nav_matrix()
    for i in range(6):                                   # the engine picks X0..X5 -> give them NAV
        nav[f"X{i}"] = 100.0 * (1.0 + 0.01 * (i + 1)) ** np.arange(N_MONTHS)
    b._nav = nav
    res = b.run(start_date="2018-01-01")
    assert sorted(calls) == ["Contraccion", "Expansion"]  # once each, not once per month
    assert len(res) == 8


def test_real_engine_selection_feeds_the_return_and_leaves_missing_sub_weights_in_cash(monkeypatch):
    monkeypatch.setattr(bt, "_load_candidates", lambda conn, v, r: {
        "Defensiva": _candidates(), "Equilibrada": pd.DataFrame(), "Dinamica": pd.DataFrame()})
    b = _backtester(_hist(["Expansion"] * 2, weights=(0.6, 0.25, 0.15)), {})
    nav = _nav_matrix()
    for i in range(6):
        nav[f"X{i}"] = 100.0 * (1.0 + 0.01 * (i + 1)) ** np.arange(N_MONTHS)
    b._nav = nav
    res = b.run(start_date="2018-01-01")
    assert res["n_funds"].iloc[0] == 6
    assert res["cash_weight"].iloc[0] == pytest.approx(0.4, abs=1e-3)       # Equilibrada+Dinamica empty
    assert res["ret_1m"].iloc[0] > 0
