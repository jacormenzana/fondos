# tests/test_nav_month_end_fnd0300.py
# -*- coding: utf-8 -*-
"""
FND-0300: one definition of the date a monthly NAV stands for (shared/nav_month_end.py).

  * labels: a NAV within 5 days of its month-end is that month-end; the open month and mid-month NAVs keep their date;
  * switch OFF = identity (the same object), so every reader is bit-identical to the stored convention;
  * persistence_windows OFF reproduces the legacy DateOffset loop exactly (including its month-end drift); ON counts whole months;
  * the point of the fix: with the switch ON, P2 persistence and the PIT alpha_persistence give the SAME result whether a fund's months are
    stamped on the calendar month-end or 1-3 days earlier (the last business day), which is what the FND-0193 alignment gate tests.
Pure: no DB, no pipeline imports (R-7).
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared import config
from shared.nav_month_end import (
    MONTH_CLOSE_TOLERANCE_DAYS, align_nav_long, align_nav_wide, month_end_labels, persistence_windows,
)


@pytest.fixture
def align_on(monkeypatch):
    monkeypatch.setattr(config, "NAV_MONTH_END_ALIGN_ENABLED", True)


@pytest.fixture
def align_off(monkeypatch):
    monkeypatch.setattr(config, "NAV_MONTH_END_ALIGN_ENABLED", False)


# ---------------- labels ----------------

def test_month_end_labels_close_the_month_within_the_tolerance():
    d = pd.to_datetime(["2026-02-27", "2026-02-28", "2026-03-31", "2026-04-25", "2026-04-24", "2026-10-18", "2024-02-26"])
    got = month_end_labels(d)
    exp = pd.to_datetime(["2026-02-28", "2026-02-28", "2026-03-31", "2026-04-30", "2026-04-24", "2026-10-18", "2024-02-29"])
    assert list(got) == list(exp)
    assert MONTH_CLOSE_TOLERANCE_DAYS == 5


def test_tolerance_is_the_complete_month_rule_of_the_pit():
    from proyecto3.src.pit_backtest import last_complete_month_end
    for d in pd.date_range("2025-01-01", "2026-12-31", freq="D"):
        closes = month_end_labels([d])[0] != d or d.is_month_end
        assert (last_complete_month_end(d) == d + pd.offsets.MonthEnd(0)) == closes, d


def test_align_long_is_identity_while_off(align_off):
    df = pd.DataFrame({"date": pd.to_datetime(["2026-02-27", "2026-03-31"]), "nav": [1.0, 2.0]})
    assert align_nav_long(df) is df
    assert align_nav_long(df, force=True)["date"].tolist() == [pd.Timestamp("2026-02-28"), pd.Timestamp("2026-03-31")]


def test_align_long_keyed_keeps_one_row_per_fund_month(align_on):
    df = pd.DataFrame({"isin": ["A", "A", "B", "B"],
                       "date": pd.to_datetime(["2026-01-30", "2026-01-31", "2026-01-29", "2026-02-27"]),
                       "nav": [1.0, 2.0, 3.0, 4.0]})
    out = align_nav_long(df, key="isin")
    assert out[["isin", "nav"]].values.tolist() == [["A", 2.0], ["B", 3.0], ["B", 4.0]]       # A's later row of the month wins
    assert out["date"].tolist() == pd.to_datetime(["2026-01-31", "2026-01-31", "2026-02-28"]).tolist()


def test_align_wide_collapses_mixed_stamps(align_on):
    wide = pd.DataFrame({"A": [1.0, np.nan, 2.0], "B": [np.nan, 5.0, np.nan]},
                        index=pd.to_datetime(["2026-02-27", "2026-02-28", "2026-03-31"]))
    out = align_nav_wide(wide)
    assert list(out.index) == list(pd.to_datetime(["2026-02-28", "2026-03-31"]))
    assert out.loc["2026-02-28"].tolist() == [1.0, 5.0]


# ---------------- window grid ----------------

def _legacy_windows(first, last, w, s):
    out, ws = [], pd.Timestamp(first)
    while True:
        we = ws + pd.DateOffset(months=w)
        if we > pd.Timestamp(last):
            return out
        out.append((ws, we))
        ws += pd.DateOffset(months=s)


@pytest.mark.parametrize("first", ["2005-08-31", "2005-08-29", "2006-01-31", "2004-02-29", "2010-06-15"])
def test_windows_off_reproduce_the_legacy_loop(first):
    last = pd.Timestamp("2026-09-30")
    assert list(persistence_windows(first, last, 36, 6, aligned=False)) == _legacy_windows(first, last, 36, 6)


def test_windows_on_count_whole_months_and_do_not_drift():
    wins = list(persistence_windows("2005-08-29", "2026-09-30", 36, 6, aligned=True))
    assert wins[0] == (pd.Timestamp("2005-08-01"), pd.Timestamp("2008-08-31"))
    assert all(lo.day == 1 and hi.is_month_end for lo, hi in wins)
    assert all((hi.to_period("M") - lo.to_period("M")).n == 36 for lo, hi in wins)
    assert [lo.month for lo, _ in wins[:4]] == [8, 2, 8, 2]                    # the legacy grid drifts 31 -> 28 -> 28
    assert wins[-1][1] <= pd.Timestamp("2026-09-30")
    # the open month (last NAV 2026-10-18) does not close a window ending 2026-10-31
    assert list(persistence_windows("2023-10-31", "2026-10-18", 36, 6, aligned=True)) == []


# ---------------- stamp invariance (the fix) ----------------

N = 132


def _panels(seed=7):
    """Calendar month-end panel A and the same values stamped 0-3 days earlier per fund and month (panel B, raw union index)."""
    rng = np.random.default_rng(seed)
    me = pd.date_range("2009-01-31", periods=N, freq=pd.offsets.MonthEnd())
    funds = {f"F{i}": (i * 4, 0.002 + 0.0007 * i, 0.02 + 0.003 * i) for i in range(9)}
    a = pd.DataFrame(index=me, dtype=float)
    long_b = []
    for isin, (start, mu, sd) in funds.items():
        nav = 100.0 * np.cumprod(1.0 + rng.normal(mu, sd, N))
        nav[:start] = np.nan
        a[isin] = nav
        shift = rng.integers(0, 4, N)
        for k in range(start, N):
            long_b.append((isin, me[k] - pd.Timedelta(days=int(shift[k])), nav[k]))
    b = pd.DataFrame(long_b, columns=["isin", "date", "nav"]).pivot_table(index="date", columns="isin", values="nav", aggfunc="last")
    b = b.reindex(columns=a.columns)
    nature = pd.Series("X", index=a.columns)
    return a, b, nature


def _by_month(frame):
    """{isin: Series indexed by calendar month} of a wide result (each fund's own observations)."""
    return {c: frame[c].dropna().set_axis(frame[c].dropna().index.to_period("M")) for c in frame.columns}


def test_pit_reader_labels_make_the_panel_and_alpha_persistence_stamp_invariant(align_on):
    """The PIT reader (pit_inputs.load_nav_panel) labels the long rows (align_nav_long keyed by fund) before the pivot: the panel of the
    business-day stamps IS the calendar panel, so every PIT metric is invariant. (On an UNLABELLED raw panel the persistence windows agree
    but a window is credited at the first observation dated >= its end, one month later for a fund stamped before the month-end: the
    labelling at the reader is what makes it exact.)"""
    from proyecto3.src.pit_peer_metrics import alpha_persistence
    a, b, nature = _panels()
    long_b = b.stack().rename("nav").reset_index().rename(columns={"level_1": "isin"})
    long_b.columns = ["date", "isin", "nav"]
    wide_b = align_nav_long(long_b, key="isin").pivot_table(index="date", columns="isin", values="nav", aggfunc="last")
    wide_b = wide_b.reindex(columns=a.columns)
    wide_b.columns.name = None
    pd.testing.assert_frame_equal(wide_b, a, check_names=False, check_freq=False)
    assert align_nav_wide(b).equals(a)
    ra, rb = alpha_persistence(a, nature), alpha_persistence(wide_b, nature)
    for key in ("alpha_persistence", "alpha_persistence_n"):
        pd.testing.assert_frame_equal(ra[key], rb[key], check_names=False, check_freq=False)
    assert ra["alpha_persistence"].notna().sum().sum() > 100


def test_pit_alpha_persistence_depends_on_the_stamp_with_the_switch_off(align_off):
    """The defect the switch removes (documents why the FND-0193 gate failed)."""
    from proyecto3.src.pit_peer_metrics import alpha_persistence
    a, b, nature = _panels()
    ra, rb = alpha_persistence(a, nature), alpha_persistence(b, nature)
    ma, mb = _by_month(ra["alpha_persistence_n"]), _by_month(rb["alpha_persistence_n"])
    assert any(not ma[c].reindex(mb[c].index).equals(mb[c]) for c in a.columns)


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _PeerConn:
    """Serves P2's persistence window query (first/last NAV path) from a RAW-dated panel, like fund_nav_monthly."""

    def __init__(self, panel):
        self.panel = panel

    def execute(self, sql, params=None):
        nat, excl, start, end, min_n = params
        rows = []
        for c in self.panel.columns:
            if c == excl:
                continue
            s = self.panel[c].dropna()
            s = s[(s.index >= pd.Timestamp(start)) & (s.index <= pd.Timestamp(end))]
            if len(s) >= min_n:
                rows.append((c, float(s.min()), float(s.max()), len(s), float(s.iloc[0]), float(s.iloc[-1]),
                             s.index[0].date(), s.index[-1].date()))
        return _Rows(rows)


def test_p2_persistence_is_invariant_to_the_stamp_with_the_switch_on(align_on, monkeypatch):
    from proyecto3.src.pit_metrics import load_p2_calc
    monkeypatch.setattr(config, "EUR_NAV_CONVERSION_ENABLED", False)
    monkeypatch.setattr(config, "PERSISTENCE_FIRST_LAST_NAV_ENABLED", True)
    p2 = load_p2_calc("persistence")
    a, b, _ = _panels()
    scored = 0
    for isin in a.columns:
        nav_a = pd.DataFrame({"date": a[isin].dropna().index, "nav": a[isin].dropna().to_numpy()})
        raw_b = pd.DataFrame({"date": b[isin].dropna().index, "nav": b[isin].dropna().to_numpy()})
        nav_b = align_nav_long(raw_b)                                           # what db_readers.load_nav returns with the switch on
        out_a = p2.compute_persistence(isin, "X", nav_a, _PeerConn(a))
        out_b = p2.compute_persistence(isin, "X", nav_b, _PeerConn(b))          # peers keep their RAW stamps (SQL path)
        assert out_a == out_b, isin
        scored += bool(out_a)
    assert scored >= 5
