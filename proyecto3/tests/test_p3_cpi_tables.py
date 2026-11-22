"""Summary tables of the baseline model against inflation and money growth (scripts/audit/p3/p3_cpi_tables.py), owner request 2026-11-22: CPI measured UNLAGGED over the SAME months as the returns, shown for
Spain alone and for the Spain + euro-area average side by side; M2 and M3 (euro area, year-on-year) on the same basis. Display only: no model, no production input.
Invariants pinned: (1) the return of the formation date t is earned in the month t+1 and every benchmark of that month is the reading OF that month (no lag); (2) CPI is the year-on-year change of the index
against the same month a year earlier, per region, and the average is the plain mean of the two; (3) Fees = Gross - Net exactly, and Net - benchmark is a plain subtraction in percentage points; (4) a benchmark
with no reading in a month (M2 / M3 of the last month) is averaged over the months it has, never filled; (5) a regime cycle is a run of consecutive months under the same label and the cycles tile the
sample exactly. Synthetic frames only (R-7)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts" / "audit" / "p3"))

import p3_cpi_tables as T  # noqa: E402


def index_levels(n=40, es_growth=0.004, eu_growth=0.002):
    idx = pd.date_range("2020-01-31", periods=n, freq="ME")
    return pd.DataFrame({"ES": 100.0 * (1 + es_growth) ** np.arange(n), "EU": 100.0 * (1 + eu_growth) ** np.arange(n)}, index=idx)


def test_cpi_is_the_year_on_year_change_of_the_same_month_with_no_lag():
    lv = index_levels()
    y = T.cpi_yoy(lv)
    assert y["ES"].iloc[:12].isna().all() and y["ES"].notna().iloc[12:].all()
    m = lv.index[20]
    assert y.loc[m, "ES"] == pytest.approx(lv.loc[m, "ES"] / lv.loc[lv.index[8], "ES"] - 1.0)       # the same month a year earlier, no shift
    assert y.loc[m, "AVG"] == pytest.approx((y.loc[m, "ES"] + y.loc[m, "EU"]) / 2.0)
    assert list(y.columns) == ["ES", "EU", "AVG"]


def test_the_return_of_a_formation_date_is_earned_in_the_following_month():
    idx = pd.date_range("2021-01-31", periods=3, freq="ME")
    assert list(T.return_months(idx)) == list(pd.date_range("2021-02-28", periods=3, freq="ME"))


def series_and_macro(n=30):
    idx = pd.date_range("2020-01-31", periods=n, freq="ME")
    rng = np.random.default_rng(2)
    s = pd.DataFrame({"gross": 0.004 + rng.normal(0, 0.01, n), "netfee": 0.0}, index=idx)
    s["netfee"] = s["gross"] - 0.003
    rm = T.return_months(idx)
    macro = pd.DataFrame({"ES": 0.03, "EU": 0.02, "AVG": 0.025, "M2": 0.05, "M3": 0.04}, index=rm)
    return s, macro


def test_a_period_block_is_plain_arithmetic_on_the_same_months():
    s, macro = series_and_macro()
    b = T.period_block(s, macro)
    assert b["months"] == 30
    assert b["Fees"] == pytest.approx(b["Gross"] - b["Net"])                       # Net = Gross - Fees, exactly
    assert b["CPI_ES"] == pytest.approx(3.0) and b["CPI_AVG"] == pytest.approx(2.5) and b["M2"] == pytest.approx(5.0) and b["M3"] == pytest.approx(4.0)
    assert b["Net-CPI_ES"] == pytest.approx(b["Net"] - 3.0) and b["Net-CPI_AVG"] == pytest.approx(b["Net"] - 2.5)
    assert b["Net-(CPI+M2)_ES"] == pytest.approx(b["Net"] - 3.0 - 5.0) and b["Net-(CPI+M3)_AVG"] == pytest.approx(b["Net"] - 2.5 - 4.0)


def test_a_benchmark_missing_in_a_month_is_averaged_over_the_months_it_has_and_never_filled():
    s, macro = series_and_macro()
    macro.loc[macro.index[-1], ["M2", "M3"]] = np.nan                                # the last month: M2 and M3 not published yet
    macro.loc[macro.index[0], "M3"] = 0.10
    b = T.period_block(s, macro)
    assert b["M2"] == pytest.approx(5.0) and b["months"] == 30
    assert b["M3"] == pytest.approx((0.10 + 0.04 * 28) / 29 * 100)                  # 29 months with a reading
    assert b["M3_months"] == 29 and b["M2_months"] == 29 and b["CPI_months"] == 30


def test_cycles_tile_the_sample_and_report_their_first_and_last_return_month():
    s, macro = series_and_macro(12)
    regime = pd.Series(["A", "A", "B", "B", "B", "A", "C", "C", "A", "A", "A", "B"], index=s.index)
    c = T.cycles(s, macro, regime)
    assert list(c["regime"]) == ["A", "B", "A", "C", "A", "B"] and list(c["months"]) == [2, 3, 1, 2, 3, 1]
    assert int(c["months"].sum()) == 12
    assert c["start"].iloc[0] == "202002" and c["end"].iloc[0] == "202003"          # formation 2020-01 earns February, formation 2020-02 earns March
    assert c["start"].iloc[-1] == "202101" and c["end"].iloc[-1] == "202101"
    assert c["Net"].notna().all()


def test_by_regime_groups_every_month_of_a_regime_and_counts_its_cycles():
    s, macro = series_and_macro(12)
    regime = pd.Series(["A", "A", "B", "B", "B", "A", "C", "C", "A", "A", "A", "B"], index=s.index)
    r = T.by_regime(s, macro, regime)
    assert int(r.loc["A", "months"]) == 6 and int(r.loc["A", "cycles"]) == 3 and int(r.loc["B", "months"]) == 4 and int(r.loc["C", "months"]) == 2 and int(r["months"].sum()) == 12


def test_yearly_uses_the_year_of_the_month_the_return_was_earned():
    idx = pd.date_range("2020-11-30", periods=4, freq="ME")                          # formation Nov, Dec, Jan, Feb: returns earned Dec 2020, Jan, Feb, Mar 2021
    s = pd.DataFrame({"gross": [0.01] * 4, "netfee": [0.005] * 4}, index=idx)
    macro = pd.DataFrame({"ES": 0.03, "EU": 0.02, "AVG": 0.025, "M2": 0.05, "M3": 0.04}, index=T.return_months(idx))
    y = T.yearly(s, macro)
    assert list(y.index) == [2020, 2021] and list(y["months"]) == [1, 3]
