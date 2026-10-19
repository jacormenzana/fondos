# proyecto2/tests/calculations/test_momentum_units_fnd0302.py
# -*- coding: utf-8 -*-
"""
FND-0302 / FND-0297: momentum_1y / momentum_3y compare the fund with its peers in the SAME window and the SAME units.

The old code subtracted the peers' stored rolling_Ny return_ann (ANNUALISED, date window) from the fund's CUMULATIVE return over the last N*12
ROWS, so a fund that earned exactly what its peers earned showed a large positive "excess" (+0.14 for 3 years at 0.5%/month). Fake connection,
no DB (R-7).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.calculations import momentum as mom
from src.calculations.returns import annualized_return, trailing_window


@pytest.fixture(autouse=True)
def _fresh_cache():
    mom.reset_category_returns_cache()
    yield
    mom.reset_category_returns_cache()


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Conn:
    """Serves load_category_returns: {horizon: [(isin, return_ann), ...]} in the order given; records the SQL."""

    def __init__(self, by_horizon):
        self.by_horizon, self.sql = by_horizon, []

    def execute(self, sql, params=None):
        self.sql.append(" ".join(sql.split()))
        return _Rows(list(self.by_horizon.get(params[0], [])))


def _nav(months=120, g=0.005):
    dates = pd.date_range("2016-01-31", periods=months, freq="ME")
    return pd.DataFrame({"date": dates, "nav": 100.0 * (1.0 + g) ** np.arange(months)})


def _stored_rolling(nav_df, months):
    """What run_pipeline stores as the fund's own rolling_Ny return_ann: the same window and annualised_return."""
    w = trailing_window(nav_df, months)
    return annualized_return(w["nav"], 12, dates=w["date"])


def _peers(value, n=6):
    return [(f"P{i}", value) for i in range(n)]


def test_a_fund_that_earns_what_its_peers_earn_has_zero_momentum():
    nav = _nav()
    conn = _Conn({"rolling_1y": _peers(_stored_rolling(nav, 12)), "rolling_3y": _peers(_stored_rolling(nav, 36))})
    out = {m: v for m, v, _ in mom.compute_momentum("F", "Mixtos", nav, conn)}
    assert out["momentum_1y"] == pytest.approx(0.0, abs=1e-12)
    assert out["momentum_3y"] == pytest.approx(0.0, abs=1e-12)
    # the defect it replaces: cumulative minus annualised
    old_3y = float(nav["nav"].iloc[-1] / nav["nav"].iloc[-37] - 1) - _stored_rolling(nav, 36)
    assert old_3y > 0.10


def test_excess_is_the_annualised_difference_and_the_peer_mean_ignores_row_order():
    nav = _nav()
    own = _stored_rolling(nav, 36)
    values = [own - 0.03, own - 0.01, own + 0.00, own + 0.02, own + 0.05, own + 0.07]
    rows = [(f"P{i}", v) for i, v in enumerate(values)]
    a = {m: v for m, v, _ in mom.compute_momentum("F", "Mixtos", nav, _Conn({"rolling_3y": rows}))}
    mom.reset_category_returns_cache()
    b = {m: v for m, v, _ in mom.compute_momentum("F", "Mixtos", nav, _Conn({"rolling_3y": rows[::-1]}))}
    assert a["momentum_3y"] == b["momentum_3y"]                                  # FND-0297: bit-identical whatever the server's row order
    assert a["momentum_3y"] == pytest.approx(own - float(np.mean(values)), abs=1e-12)


def test_history_must_span_the_window_and_gaps_use_the_same_date_window():
    short = _nav(months=30)                                                      # 2.5 years: no 3-year momentum, still a 1-year one
    conn = _Conn({"rolling_1y": _peers(0.05), "rolling_3y": _peers(0.05)})
    out = {m for m, _, _ in mom.compute_momentum("F", "Mixtos", short, conn)}
    assert "momentum_1y" in out and "momentum_3y" not in out
    gappy = _nav(months=120).drop(index=range(100, 106)).reset_index(drop=True)  # 6 missing months inside the last 3 years
    conn = _Conn({"rolling_3y": _peers(_stored_rolling(gappy, 36))})
    out = {m: v for m, v, _ in mom.compute_momentum("F", "Mixtos", gappy, conn)}
    assert out["momentum_3y"] == pytest.approx(0.0, abs=1e-12)                   # by-dates annualisation, same as the peers' stored value


def test_unsorted_nav_and_too_few_peers():
    nav = _nav()
    shuffled = nav.sample(frac=1.0, random_state=3).reset_index(drop=True)
    conn = _Conn({"rolling_3y": _peers(_stored_rolling(nav, 36))})
    out = {m: v for m, v, _ in mom.compute_momentum("F", "Mixtos", shuffled, conn)}
    assert out["momentum_3y"] == pytest.approx(0.0, abs=1e-12)
    mom.reset_category_returns_cache()
    few = {m for m, _, _ in mom.compute_momentum("F", "Mixtos", nav, _Conn({"rolling_3y": _peers(0.05, n=2)}))}
    assert "momentum_3y" not in few


def test_category_returns_query_has_a_fixed_row_order():
    conn = _Conn({"rolling_1y": _peers(0.05)})
    mom.load_category_returns(conn, "Mixtos", "rolling_1y")
    assert "ORDER BY fmet.isin" in conn.sql[0]


def test_momentum_rank_is_unchanged():
    nav = _nav()
    rows = [(f"P{i}", 0.01 * i) for i in range(1, 7)] + [("F", 0.035)]
    out = {m: v for m, v, _ in mom.compute_momentum("F", "Mixtos", nav, _Conn({"since_inception": rows}))}
    assert out["momentum_rank"] == pytest.approx(3 / 7)                          # 3 of the 7 values (0.01, 0.02, 0.03) are below 0.035
