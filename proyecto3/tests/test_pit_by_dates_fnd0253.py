# proyecto3/tests/test_pit_by_dates_fnd0253.py
# -*- coding: utf-8 -*-
"""
FND-0253 on the PIT side: with ANNUALIZATION_BY_DATES_ENABLED the expanding metrics (pit_metrics) and the peer window returns
(pit_peer_metrics) follow P2's by-dates convention. Two proofs, both against P2's own functions as the oracle:

  * a DENSE panel is bit-identical with the switch ON or OFF (the switch is a no-op for a fund without NAV gaps);
  * on a panel WITH gaps, the vectorised PIT result equals P2 evaluated on the NAV truncated at every observation.

No DB, no pipeline imports (R-7).

    python -m pytest proyecto3/tests/test_pit_by_dates_fnd0253.py -v
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
from proyecto3.src.pit_peer_metrics import alpha_persistence
from shared import config
from shared.config import RISK_FREE_RATE_ANN

p2_returns = load_p2_calc("returns")
p2_persistence = load_p2_calc("persistence")

N = 120
DATES = pd.date_range("2008-01-31", periods=N, freq=pd.offsets.MonthEnd())
TOL = dict(rel=1e-9, abs=1e-9)


@pytest.fixture(autouse=True)
def _eur_view_off(monkeypatch):
    monkeypatch.setattr(config, "EUR_NAV_CONVERSION_ENABLED", False, raising=False)


@pytest.fixture
def by_dates(monkeypatch):
    return lambda on: monkeypatch.setattr(config, "ANNUALIZATION_BY_DATES_ENABLED", on, raising=False)


def _panel(gap=True):
    rng = np.random.default_rng(23)
    cols = {}
    for name, (start, mu, sd) in {"EQ": (0, 0.006, 0.04), "BOND": (8, 0.002, 0.01), "MIX": (15, 0.003, 0.02), "GAP": (4, 0.004, 0.03)}.items():
        s = pd.Series(100.0 * np.cumprod(1.0 + rng.normal(mu, sd, N)), index=DATES)
        s.iloc[:start] = np.nan
        cols[name] = s
    panel = pd.DataFrame(cols)
    if gap:
        panel.loc[DATES[40:47], "GAP"] = np.nan                                  # a 7-month hole
        panel.loc[DATES[80:82], "MIX"] = np.nan                                  # a 2-month hole
    return panel


# ------------------------------------------------------------ pit_metrics
def test_dense_panel_expanding_metrics_are_identical_with_the_switch_on(by_dates):
    panel = _panel(gap=False)
    by_dates(False)
    off = expanding_risk_metrics(panel)
    by_dates(True)
    on = expanding_risk_metrics(panel)
    assert set(off) == set(on)
    for k in off:
        pd.testing.assert_frame_equal(off[k], on[k], check_exact=True)


def test_gappy_panel_expanding_metrics_match_p2_by_dates_at_every_truncation(by_dates):
    panel = _panel(gap=True)
    by_dates(True)
    res = expanding_risk_metrics(panel)
    checked = moved = 0
    for isin in ("GAP", "MIX", "EQ"):
        col = panel[isin].dropna()
        for t in col.index[2:]:
            s = col.loc[:t]
            exp_ret = p2_returns.annualized_return(s.reset_index(drop=True), dates=s.index)
            exp_vol = p2_returns.annualized_volatility(s.reset_index(drop=True), dates=s.index)
            exp_sh = p2_returns.sharpe_ratio(s.reset_index(drop=True), RISK_FREE_RATE_ANN, dates=s.index)
            for metric, exp in (("return_ann", exp_ret), ("vol_ann", exp_vol), ("sharpe", exp_sh)):
                got = res[metric].loc[t, isin]
                if exp is None or np.isnan(exp):
                    assert np.isnan(got), f"{metric} {isin} {t.date()}: expected NaN, got {got}"
                else:
                    assert got == pytest.approx(exp, **TOL), f"{metric} {isin} {t.date()}: {got} != {exp}"
            checked += 1
    assert checked > 200
    by_dates(False)
    off = expanding_risk_metrics(panel)
    after_gap = DATES[60]
    assert off["return_ann"].loc[after_gap, "GAP"] != res["return_ann"].loc[after_gap, "GAP"]     # the switch really changes a gappy fund
    assert off["return_ann"].loc[DATES[30], "GAP"] == res["return_ann"].loc[DATES[30], "GAP"]     # ... and not before its first gap
    assert off["return_ann"].loc[after_gap, "EQ"] == res["return_ann"].loc[after_gap, "EQ"]       # ... and never a dense fund


# ------------------------------------------------------------ pit_peer_metrics
class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakePeerConn:
    """The persistence peer-window query of P2, served from the panel truncated at t, WITH the window dates (8 columns, like Postgres)."""

    def __init__(self, panel, nature, t):
        self.panel, self.nature, self.t = panel, nature, t

    def execute(self, sql, params=None):
        sql_l = " ".join(sql.split()).lower()
        if "group by fnm.isin" not in sql_l:
            raise AssertionError(f"unexpected SQL: {sql_l[:80]}")
        nat, excl, start, end, min_n = params
        rows = []
        for c in self.panel.columns:
            if self.nature[c] != nat or c == excl:
                continue
            s = self.panel[c].loc[:self.t].dropna()
            s = s[(s.index >= pd.Timestamp(start)) & (s.index <= pd.Timestamp(end))]
            if len(s) >= min_n:
                rows.append((c, float(s.min()), float(s.max()), len(s), float(s.iloc[0]), float(s.iloc[-1]), s.index[0], s.index[-1]))
        return _Rows(rows)


def _nav_df(panel, isin, t):
    s = panel[isin].loc[:t].dropna()
    return pd.DataFrame({"date": s.index, "nav": s.to_numpy()})


def _peer_panel(gap):
    rng = np.random.default_rng(31)
    cols, nature = {}, {}
    for i in range(7):
        s = pd.Series(100.0 * np.cumprod(1.0 + rng.normal(0.003 + 0.0007 * i, 0.02 + 0.003 * i, N)), index=DATES)
        s.iloc[: i * 3] = np.nan
        cols[f"A{i}"] = s
        nature[f"A{i}"] = "NAT_A"
    panel = pd.DataFrame(cols)
    if gap:
        panel.loc[DATES[50:55], "A2"] = np.nan
        panel.loc[DATES[70:73], "A4"] = np.nan
    return panel, pd.Series(nature)


@pytest.mark.parametrize("first_last", [True, False], ids=["first_last", "min_max"])
def test_dense_peer_panel_alpha_persistence_is_identical_with_the_switch_on(by_dates, monkeypatch, first_last):
    monkeypatch.setattr(config, "PERSISTENCE_FIRST_LAST_NAV_ENABLED", first_last)
    panel, nature = _peer_panel(gap=False)
    by_dates(False)
    off = alpha_persistence(panel, nature)
    by_dates(True)
    on = alpha_persistence(panel, nature)
    for k in off:
        pd.testing.assert_frame_equal(off[k], on[k], check_exact=True)


@pytest.mark.parametrize("first_last", [True, False], ids=["first_last", "min_max"])
def test_gappy_peer_panel_alpha_persistence_matches_p2_by_dates(by_dates, monkeypatch, first_last):
    monkeypatch.setattr(config, "PERSISTENCE_FIRST_LAST_NAV_ENABLED", first_last)
    panel, nature = _peer_panel(gap=True)
    by_dates(True)
    res = alpha_persistence(panel, nature)
    checked = scored = 0
    for isin in panel.columns:
        for i in (70, 85, 100, 119):
            t = DATES[i]
            if np.isnan(panel.loc[t, isin]):
                continue
            conn = _FakePeerConn(panel, nature, t)
            out = {m: v for m, v, _ in p2_persistence.compute_persistence(isin, nature[isin], _nav_df(panel, isin, t), conn)}
            for key in ("alpha_persistence", "alpha_persistence_n"):
                exp, got = out.get(key), res[key].loc[t, isin]
                if exp is None or (isinstance(exp, float) and np.isnan(exp)):
                    assert np.isnan(got), f"{key} {isin} {t.date()}: expected NaN, got {got}"
                else:
                    assert got == pytest.approx(exp, **TOL), f"{key} {isin} {t.date()}: {got} != {exp}"
            checked += 1
            scored += "alpha_persistence" in out
    assert checked > 15 and scored > 5
