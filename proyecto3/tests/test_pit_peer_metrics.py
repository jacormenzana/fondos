# proyecto3/tests/test_pit_peer_metrics.py
# -*- coding: utf-8 -*-
"""
PIT peer-relative metrics (proyecto3/src/pit_peer_metrics.py) pinned to P2's own functions -- FND-0159, d1b.

Oracle = P2's persistence / capture_ratios / momentum modules (loaded by path; they import only
shared.config) run against a fake connection that serves the fund panel TRUNCATED at t. P2's known
defects (FND-0200 MIN/MAX peer return, FND-0202 raw-date vs month-end join) are replicated on purpose,
so equality here also proves they are replicated faithfully. No DB, no pipeline imports (R-7).

    python -m pytest proyecto3/tests/test_pit_peer_metrics.py -v
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto3.src.pit_metrics import expanding_risk_metrics, load_p2_calc
from proyecto3.src.pit_peer_metrics import alpha_persistence, capture_ratios, momentum_rank

p2_persistence = load_p2_calc("persistence")
p2_capture = load_p2_calc("capture_ratios")
p2_momentum = load_p2_calc("momentum")
p2_returns = load_p2_calc("returns")

N_MONTHS = 150
TOL = dict(rel=1e-9, abs=1e-9)


# ---------------- fixture ----------------

def _dates() -> pd.DatetimeIndex:
    me = pd.date_range("2008-01-31", periods=N_MONTHS, freq=pd.offsets.MonthEnd())
    # 70% of observations month-end dated, the rest mid-month (real data is mixed: FND-0202)
    return pd.DatetimeIndex([d if i % 10 < 7 else d - pd.Timedelta(days=12) for i, d in enumerate(me)])


def _panel():
    rng = np.random.default_rng(11)
    dates = _dates()
    spec = {  # isin: (start_idx, nature, drift, vol)
        **{f"A{i}": (i * 3, "NAT_A", 0.003 + 0.0008 * i, 0.02 + 0.004 * i) for i in range(8)},
        **{f"B{i}": (i * 5, "NAT_B", 0.002 + 0.001 * i, 0.015) for i in range(4)},   # < MIN_PEERS funds
    }
    cols = {}
    for isin, (start, nat, mu, sd) in spec.items():
        nav = 100.0 * np.cumprod(1.0 + rng.normal(mu, sd, N_MONTHS))
        s = pd.Series(nav, index=dates)
        s.iloc[:start] = np.nan
        cols[isin] = s
    panel = pd.DataFrame(cols)
    panel.loc[dates[60:63], "A2"] = np.nan                                   # internal gap
    nature = pd.Series({k: v[1] for k, v in spec.items()})
    return panel, nature


@pytest.fixture(scope="module")
def data():
    return _panel()


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakePeerConn:
    """Serves the three P2 peer queries from a panel truncated at t."""

    def __init__(self, panel, nature, t):
        self.panel, self.nature, self.t = panel, nature, t

    def _peer_cols(self, nat, exclude):
        return [c for c in self.panel.columns if self.nature[c] == nat and c != exclude]

    def execute(self, sql, params=None):
        sql_l = " ".join(sql.split()).lower()
        if "group by fnm.isin" in sql_l:                                     # persistence window query
            nat, excl, start, end, min_n = params
            rows = []
            for c in self._peer_cols(nat, excl):
                s = self.panel[c].loc[:self.t].dropna()
                s = s[(s.index >= pd.Timestamp(start)) & (s.index <= pd.Timestamp(end))]
                if len(s) >= min_n:
                    rows.append((c, float(s.min()), float(s.max()), len(s), float(s.iloc[0]), float(s.iloc[-1])))
            return _Rows(rows)
        if "order by fnm.isin, fnm.date" in sql_l:                           # capture peer panel
            nat, excl = params
            rows = []
            for c in self._peer_cols(nat, excl):
                s = self.panel[c].loc[:self.t].dropna()
                rows += [(c, d.date(), float(v)) for d, v in s.items()]
            return _Rows(rows)
        if "from fund_metrics" in sql_l:                                     # momentum category returns
            horizon, nat = params
            rows = []
            for c in self.panel.columns:
                if self.nature[c] != nat:
                    continue
                s = self.panel[c].loc[:self.t].dropna()
                if len(s) >= 2:
                    rows.append((c, p2_returns.annualized_return(s.reset_index(drop=True))))
            return _Rows(rows)
        raise AssertionError(f"unexpected SQL: {sql_l[:80]}")


def _nav_df(panel, isin, t):
    s = panel[isin].loc[:t].dropna()
    return pd.DataFrame({"date": s.index, "nav": s.to_numpy()})


def _eq(got, exp, label):
    if exp is None or (isinstance(exp, float) and np.isnan(exp)):
        assert np.isnan(got), f"{label}: expected NaN, got {got}"
    else:
        assert got == pytest.approx(exp, **TOL), f"{label}: {got} != {exp}"


def _check_points(panel):
    """Observation dates (per fund) used as truncation points."""
    return [45, 54, 60, 66, 80, 100, 125, N_MONTHS - 1]


# ---------------- alpha_persistence ----------------

@pytest.mark.parametrize("first_last", [False, True], ids=["min_max_p2_today", "first_last_FND0200_flag"])
def test_alpha_persistence_matches_p2_at_every_truncation(data, monkeypatch, first_last):
    from shared import config
    monkeypatch.setattr(config, "PERSISTENCE_FIRST_LAST_NAV_ENABLED", first_last)
    panel, nature = data
    res = alpha_persistence(panel, nature)
    dates = panel.index
    checked = scored = 0
    for isin in panel.columns:
        for i in _check_points(panel):
            t = dates[i]
            if np.isnan(panel.loc[t, isin]):
                continue
            conn = _FakePeerConn(panel, nature, t)
            out = dict((m, v) for m, v, _ in p2_persistence.compute_persistence(isin, nature[isin], _nav_df(panel, isin, t), conn))
            _eq(res["alpha_persistence"].loc[t, isin], out.get("alpha_persistence"), f"persistence {isin} {t.date()}")
            _eq(res["alpha_persistence_n"].loc[t, isin], out.get("alpha_persistence_n"), f"persistence_n {isin} {t.date()}")
            checked += 1
            scored += "alpha_persistence" in out
    assert checked > 60 and scored > 20                                      # the scored branch really ran


def test_persistence_replicates_the_min_max_peer_bias():
    # Peer NAV: 100 -> 200 -> 100 over the window. last/first = 0.0% but MAX/MIN = +100%/n-years: the
    # replicated P2 formula (FND-0200) must see a hugely positive peer return, so the fund must NOT beat it.
    n = 60
    dates = pd.date_range("2010-01-31", periods=n, freq=pd.offsets.MonthEnd())
    flat_up = pd.Series(100.0 * 1.004 ** np.arange(n), index=dates)         # steady fund
    peers = {f"P{i}": pd.Series(np.where(np.arange(n) == 18, 200.0, 100.0), index=dates) for i in range(5)}
    panel = pd.DataFrame({"F": flat_up, **peers})
    nature = pd.Series({c: "X" for c in panel.columns})
    res = alpha_persistence(panel, nature, min_windows=1)
    last = dates[-1]
    # fund +~5%/yr vs peers' MAX/MIN-based return of +100%**(12/36)-1 ~ 26%: persistence 0 where scored
    assert res["alpha_persistence"].loc[last, "F"] == 0.0


def test_persistence_needs_enough_history_and_windows(data):
    panel, nature = data
    res = alpha_persistence(panel, nature)
    young = panel["A7"].dropna().index                                       # starts at index 21
    assert res["alpha_persistence"].loc[young[:41], "A7"].isna().all()


# ---------------- capture ratios ----------------

def test_capture_ratios_match_p2_at_every_truncation(data):
    panel, nature = data
    res = capture_ratios(panel, nature)
    dates = panel.index
    checked = scored = 0
    for isin in panel.columns:
        for i in _check_points(panel):
            t = dates[i]
            if np.isnan(panel.loc[t, isin]):
                continue
            conn = _FakePeerConn(panel, nature, t)
            out = {m: v for m, v, _ in p2_capture.compute_capture_ratios(isin, nature[isin], _nav_df(panel, isin, t), conn)}
            for name in ("upside_capture", "downside_capture", "capture_ratio"):
                _eq(res[name].loc[t, isin], out.get(name), f"{name} {isin} {t.date()}")
            checked += 1
            scored += "capture_ratio" in out
    assert checked > 60 and scored > 20


def test_capture_only_uses_month_end_dated_observations():
    # FND-0202 replicated: a fund whose observations are ALL mid-month never joins the month-end peer benchmark
    n = 80
    me = pd.date_range("2010-01-31", periods=n, freq=pd.offsets.MonthEnd())
    rng = np.random.default_rng(3)
    peers = {f"P{i}": pd.Series(100.0 * np.cumprod(1 + rng.normal(0.004, 0.03, n)), index=me) for i in range(4)}
    mid = pd.Series(100.0 * np.cumprod(1 + rng.normal(0.004, 0.03, n)), index=me - pd.Timedelta(days=10))
    raw = pd.DataFrame({**peers}).reindex(me.union(me - pd.Timedelta(days=10)))
    raw["MID"] = mid
    nature = pd.Series({c: "X" for c in raw.columns})
    res = capture_ratios(raw, nature)
    assert res["capture_ratio"]["MID"].isna().all()
    assert res["capture_ratio"]["P0"].notna().any()


# ---------------- momentum_rank ----------------

def _return_ann(panel):
    return expanding_risk_metrics(panel, None, None)["return_ann"]


def test_momentum_rank_matches_p2_with_unlimited_staleness(data):
    panel, nature = data
    ra = _return_ann(panel)
    at = pd.DatetimeIndex([panel.index[i] for i in (70, 100, 130, N_MONTHS - 1)])
    res = momentum_rank(ra, nature, at)
    checked = ranked = 0
    for t in at:
        conn = _FakePeerConn(panel, nature, t)
        for isin in panel.columns:
            if panel[isin].loc[:t].dropna().empty:
                continue
            p2_momentum.reset_category_returns_cache()
            nav_df = _nav_df(panel, isin, t)
            out = {m: v for m, v, _ in p2_momentum.compute_momentum(isin, nature[isin], nav_df, conn)}
            _eq(res.loc[t, isin], out.get("momentum_rank"), f"momentum_rank {isin} {t.date()}")
            checked += 1
            ranked += "momentum_rank" in out
    assert checked > 30 and ranked > 20


def test_momentum_rank_needs_min_peers(data):
    panel, nature = data
    res = momentum_rank(_return_ann(panel), nature, pd.DatetimeIndex([panel.index[-1]]))
    assert res[[c for c in panel.columns if nature[c] == "NAT_B"]].isna().all().all()   # only 4 funds
    assert res[[c for c in panel.columns if nature[c] == "NAT_A"]].notna().all().all()


def test_momentum_rank_staleness_drops_retired_funds(data):
    panel, nature = data
    retired = panel.copy()
    retired.loc[retired.index[90:], "A0"] = np.nan                           # A0 stops reporting at index 90
    ra = _return_ann(retired)
    t = retired.index[-1]
    unlimited = momentum_rank(ra, nature, pd.DatetimeIndex([t]))
    limited = momentum_rank(ra, nature, pd.DatetimeIndex([t]), max_stale_days=60)
    assert not np.isnan(unlimited.loc[t, "A0"])                              # P2: kept with its last value
    assert np.isnan(limited.loc[t, "A0"])                                    # PIT: not observable any more
    # the rank base shrinks from 8 to 7 funds: A1's rank = (#peers below it among the 7 observable) / 7
    obs = ra.loc[t, [c for c in panel.columns if nature[c] == "NAT_A" and c != "A0"]]
    assert limited.loc[t, "A1"] == pytest.approx((obs < obs["A1"]).sum() / 7)


def _p2_month_end_value(panel, nature, isin, t):
    """P2 capture_ratios (flag on) on the panel truncated at the month-end t; None when the fund has no bar that month."""
    s = panel[isin].loc[:t].dropna()
    if s.empty or (s.index[-1] + pd.offsets.MonthEnd(0)) != t:
        return None
    conn = _FakePeerConn(panel, nature, t)
    nav_df = pd.DataFrame({"date": s.index, "nav": s.to_numpy()})
    return {m: v for m, v, _ in p2_capture.compute_capture_ratios(isin, nature[isin], nav_df, conn)}


def test_month_end_capture_matches_p2_with_the_flag_at_every_month_end_truncation(data, monkeypatch):
    # FND-0227: the PIT replica of the FND-0202 fix is pinned to P2's own function on the truncated panel
    from shared import config
    monkeypatch.setattr(config, "CAPTURE_MONTH_END_ENABLED", True)
    panel, nature = data
    res = capture_ratios(panel, nature)
    checked = scored = 0
    for isin in panel.columns:
        for i in _check_points(panel):
            t = panel.index[i] + pd.offsets.MonthEnd(0)
            out = _p2_month_end_value(panel, nature, isin, t)
            if out is None:
                continue
            for name in ("upside_capture", "downside_capture", "capture_ratio"):
                _eq(res[name].loc[t, isin], out.get(name), f"{name} {isin} {t.date()}")
            checked += 1
            scored += "capture_ratio" in out
    assert checked > 50 and scored > 20


def test_month_end_capture_includes_mid_month_dated_funds(monkeypatch):
    # the old join dropped every observation that is not month-end dated; the fix keeps them
    from shared import config
    n = 80
    me = pd.date_range("2010-01-31", periods=n, freq=pd.offsets.MonthEnd())
    rng = np.random.default_rng(3)
    cols = {f"P{i}": pd.Series(100.0 * np.cumprod(1.0 + rng.normal(0.003, 0.02, n)), index=me) for i in range(5)}
    cols["MID"] = pd.Series(100.0 * np.cumprod(1.0 + rng.normal(0.003, 0.02, n)), index=me - pd.Timedelta(days=12))
    panel = pd.DataFrame(cols).sort_index()
    nature = pd.Series({c: "X" for c in panel.columns})
    monkeypatch.setattr(config, "CAPTURE_MONTH_END_ENABLED", False)
    assert capture_ratios(panel, nature)["capture_ratio"]["MID"].notna().sum() == 0
    monkeypatch.setattr(config, "CAPTURE_MONTH_END_ENABLED", True)
    assert capture_ratios(panel, nature)["capture_ratio"]["MID"].notna().sum() > 20


def test_month_end_capture_does_not_change_when_later_data_is_added(data, monkeypatch):
    from shared import config
    monkeypatch.setattr(config, "CAPTURE_MONTH_END_ENABLED", True)
    panel, nature = data
    full = capture_ratios(panel, nature)
    cut = panel.iloc[:100]
    part = capture_ratios(cut, nature)
    for name in full:
        pd.testing.assert_frame_equal(part[name], full[name].loc[part[name].index])


def test_month_end_capture_reports_only_month_end_rows(data, monkeypatch):
    from shared import config
    monkeypatch.setattr(config, "CAPTURE_MONTH_END_ENABLED", True)
    panel, nature = data
    idx = capture_ratios(panel, nature)["capture_ratio"].index
    assert (idx == idx + pd.offsets.MonthEnd(0)).all() and idx.is_monotonic_increasing
