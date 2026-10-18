# proyecto2/tests/calculations/test_peer_panel_fnd0255_pg.py
# -*- coding: utf-8 -*-
"""
FND-0255: the per-run peer panel (src/calculations/peer_panel.py) must give the SAME answers as the per-fund queries it replaces, and must never leak.

Differential tests on a Postgres toy database, legacy path vs cached path on the same connection: capture's benchmark and the full capture / persistence outputs
EXACTLY equal; persistence's window return equal to 1e-12 (the legacy mean followed the database's undefined GROUP BY order). The panel carries the awkward
cases of the real data: mid-month and month-end dates, funds that start late or have gaps, a peer with too few months, EUR and USD classes, a class with an
unknown currency, a peer whose LAST point has no convertible rate (the legacy code drops that peer from the window), both annualisation modes.
R-7: no run_pipeline / core.io imports.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_P2 = Path(__file__).resolve().parent.parent.parent
_REPO = _P2.parent
for _p in (str(_P2), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from shared import config  # noqa: E402
from shared import eur_nav  # noqa: E402
from src.calculations import capture_ratios, peer_panel, persistence  # noqa: E402

NATURE = "Mixtos"
OTHER = "Renta Variable"


def _month_ends(start, end):
    return list(pd.date_range(start, end, freq="ME"))


def _build(conn, seed=7):
    rng = np.random.RandomState(seed)
    for t in ("fund_master", "series_macro", "fund_nav_monthly"):
        conn.execute(f"DROP TABLE IF EXISTS {t}")
    conn.execute("CREATE TABLE fund_master (isin text PRIMARY KEY, fund_nature text, fund_currency varchar(3))")
    conn.execute("CREATE TABLE series_macro (date date, indicator text, geography text, value double precision, unit text, source text, "
                 "PRIMARY KEY (date, indicator, geography))")
    conn.execute("CREATE TABLE fund_nav_monthly (isin text, date date, nav double precision, nav_currency varchar(3), PRIMARY KEY (isin, date))")
    funds = []                       # (isin, nature, currency, start, end, mid_month?, gaps?)
    for i in range(14):
        ccy = "USD" if i % 4 == 1 else ("GBP" if i == 6 else "EUR")
        funds.append((f"MX{i:02d}", NATURE, ccy, "2017-06-30" if i % 3 else "2019-03-31", "2026-09-30", i % 5 == 2, i % 4 == 3))
    funds.append(("MXNULL", NATURE, None, "2017-06-30", "2026-09-30", False, False))             # unknown class currency: excluded under the EUR view
    funds.append(("MXSHORT", NATURE, "EUR", "2025-01-31", "2026-09-30", False, False))             # < 33 months in any window
    funds.append(("MXLATE", NATURE, "USD", "2017-06-30", "2026-12-15", False, False))             # last point far past the last FX rate: not convertible
    for i in range(6):
        funds.append((f"RV{i:02d}", OTHER, "EUR" if i % 2 else "USD", "2018-01-31", "2026-09-30", False, False))
    rows, master = [], []
    for isin, nat, ccy, start, end, mid, gaps in funds:
        master.append((isin, nat, ccy))
        dates = _month_ends(start, end)
        if isin == "MXLATE":
            dates = _month_ends(start, "2026-09-30") + [pd.Timestamp("2026-12-15")]
        nav, level = [], 100.0 * (1 + rng.rand())
        for k, d in enumerate(dates):
            level *= 1 + rng.normal(0.004, 0.035)
            if gaps and k % 11 == 5:
                continue                                                                  # a missing month
            dd = d - pd.Timedelta(days=15) if (mid and k % 3 == 0) else d                    # some points mid-month
            rows.append((isin, dd.date(), float(level), None))
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO fund_master VALUES (%s,%s,%s)", master)
        cur.executemany("INSERT INTO fund_nav_monthly VALUES (%s,%s,%s,%s)", rows)
        days = pd.bdate_range("2017-05-01", "2026-09-30")
        for ccy, base in (("USD", 1.10), ("GBP", 0.85)):
            cur.executemany("INSERT INTO series_macro VALUES (%s,%s,%s,%s,'ccy_per_eur','BCE')",
                            [(d.date(), config.EUR_FX_DAILY_INDICATOR, ccy, base * (1 + 0.0004 * k)) for k, d in enumerate(days)])
    conn.commit()
    eur_nav.reset_cache()
    return [f[0] for f in funds if f[1] == NATURE]


@pytest.fixture
def db(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    isins = _build(conn)
    peer_panel.reset()
    yield conn, isins
    peer_panel.reset()
    eur_nav.reset_cache()


def _nav(conn, isin):
    rows = conn.execute("SELECT date, nav FROM fund_nav_monthly WHERE isin = %s ORDER BY date", (isin,)).fetchall()
    return pd.DataFrame(rows, columns=["date", "nav"]).assign(date=lambda d: pd.to_datetime(d["date"]))


def _both(conn, fn):
    """(legacy result, cached result) of fn() on the same connection; the cache is active only for the second."""
    peer_panel.reset()
    eur_nav.reset_cache()
    legacy = fn()
    assert peer_panel.start_run(conn)
    cached = fn()
    peer_panel.reset()
    return legacy, cached


@pytest.mark.parametrize("eur_on", [False, True])
def test_capture_benchmark_is_bit_identical_for_every_fund(db, monkeypatch, eur_on):
    conn, isins = db
    monkeypatch.setattr(config, "EUR_NAV_CONVERSION_ENABLED", eur_on)
    monkeypatch.setattr(config, "FX_CONTRIBUTION_EUR_VIEW_ENABLED", eur_on)
    for isin in isins + ["NOT_A_FUND"]:
        legacy, cached = _both(conn, lambda: capture_ratios.load_peer_benchmark(conn, NATURE, isin))
        pd.testing.assert_series_equal(legacy, cached, check_exact=True)
        assert not legacy.empty


@pytest.mark.parametrize("eur_on", [False, True])
@pytest.mark.parametrize("by_dates", [False, True])
def test_persistence_window_return_matches_for_every_fund_and_window(db, monkeypatch, eur_on, by_dates):
    conn, isins = db
    monkeypatch.setattr(config, "EUR_NAV_CONVERSION_ENABLED", eur_on)
    monkeypatch.setattr(config, "FX_CONTRIBUTION_EUR_VIEW_ENABLED", eur_on)
    monkeypatch.setattr(config, "ANNUALIZATION_BY_DATES_ENABLED", by_dates)
    windows = [(pd.Timestamp("2018-01-31"), pd.Timestamp("2021-01-31")), (pd.Timestamp("2020-06-30"), pd.Timestamp("2023-06-30")),
               (pd.Timestamp("2023-09-30"), pd.Timestamp("2026-09-30")),           # ends at the last common month
               (pd.Timestamp("2023-12-15"), pd.Timestamp("2026-12-15"))]            # reaches MXLATE's unconvertible last point
    n_checked = 0
    for isin in isins[:8] + ["NOT_A_FUND"]:
        for s, e in windows:
            legacy, cached = _both(conn, lambda: persistence._category_return_in_window(conn, NATURE, isin, s, e))
            assert (legacy is None) == (cached is None), (isin, s, e)
            if legacy is not None:
                assert math.isclose(legacy, cached, rel_tol=1e-12), (isin, s, e, legacy, cached)
                n_checked += 1
    assert n_checked >= 20


@pytest.mark.parametrize("eur_on", [False, True])
def test_full_capture_and_persistence_outputs_are_equal_for_every_fund(db, monkeypatch, eur_on):
    conn, isins = db
    monkeypatch.setattr(config, "EUR_NAV_CONVERSION_ENABLED", eur_on)
    monkeypatch.setattr(config, "FX_CONTRIBUTION_EUR_VIEW_ENABLED", eur_on)
    produced = 0
    for isin in isins:
        nav = _nav(conn, isin)
        for fn in (capture_ratios.compute_capture_ratios, persistence.compute_persistence):
            legacy, cached = _both(conn, lambda: fn(isin, NATURE, nav, conn))
            assert legacy == cached, (isin, fn.__name__)
            produced += len(legacy)
    assert produced > 20                                   # the comparison is not vacuous: metrics were actually produced


def test_the_unconvertible_last_point_drops_the_peer_exactly_as_the_legacy_code_did(db, monkeypatch):
    conn, isins = db
    monkeypatch.setattr(config, "EUR_NAV_CONVERSION_ENABLED", True)
    monkeypatch.setattr(config, "FX_CONTRIBUTION_EUR_VIEW_ENABLED", True)
    s, e = pd.Timestamp("2023-12-15"), pd.Timestamp("2026-12-15")
    on, on_c = _both(conn, lambda: persistence._category_return_in_window(conn, NATURE, "MX00", s, e))
    monkeypatch.setattr(config, "EUR_NAV_CONVERSION_ENABLED", False)
    monkeypatch.setattr(config, "FX_CONTRIBUTION_EUR_VIEW_ENABLED", False)
    off, off_c = _both(conn, lambda: persistence._category_return_in_window(conn, NATURE, "MX00", s, e))
    assert math.isclose(on, on_c, rel_tol=1e-12) and math.isclose(off, off_c, rel_tol=1e-12)
    assert on != off                                       # the EUR view changed the answer, so the equality above was not trivial


# ---------------------------------------------------------------- activation: opt-in, per connection, switchable, released
class _Counting:
    """A connection wrapper that counts the statements that read fund_nav_monthly."""

    def __init__(self, conn):
        self._c, self.reads = conn, 0

    def execute(self, sql, *a, **k):
        if "fund_nav_monthly" in sql:
            self.reads += 1
        return self._c.execute(sql, *a, **k)

    def __getattr__(self, name):
        return getattr(self._c, name)


def test_by_default_nothing_is_cached_and_every_call_reads_the_database(db):
    conn, isins = db
    c = _Counting(conn)
    for isin in isins[:3]:
        capture_ratios.load_peer_benchmark(c, NATURE, isin)
    assert c.reads == 3 and not peer_panel.active_for(c)


def test_a_run_reads_each_nature_once_not_once_per_fund_or_window(db, monkeypatch):
    conn, isins = db
    monkeypatch.setattr(config, "EUR_NAV_CONVERSION_ENABLED", True)
    monkeypatch.setattr(config, "FX_CONTRIBUTION_EUR_VIEW_ENABLED", True)
    c = _Counting(conn)
    assert peer_panel.start_run(c)
    for isin in isins:
        capture_ratios.load_peer_benchmark(c, NATURE, isin)
        for s, e in ((pd.Timestamp("2018-01-31"), pd.Timestamp("2021-01-31")), (pd.Timestamp("2021-01-31"), pd.Timestamp("2024-01-31"))):
            persistence._category_return_in_window(c, NATURE, isin, s, e)
    capture_ratios.load_peer_benchmark(c, OTHER, "RV00")
    assert c.reads == 3                                    # NATURE for capture, NATURE for persistence, OTHER for capture: one read each


def test_the_cache_is_bound_to_its_connection(db):
    conn, isins = db
    other = _Counting(conn)
    assert peer_panel.start_run(conn)
    assert peer_panel.active_for(conn) and not peer_panel.active_for(other)
    capture_ratios.load_peer_benchmark(other, NATURE, isins[0])
    capture_ratios.load_peer_benchmark(other, NATURE, isins[1])
    assert other.reads == 2                                # a different connection object always takes the legacy path


def test_reset_deactivates_and_the_kill_switch_prevents_activation(db, monkeypatch):
    conn, _ = db
    assert peer_panel.start_run(conn) and peer_panel.active_for(conn)
    peer_panel.reset()
    assert not peer_panel.active_for(conn)
    monkeypatch.setattr(config, "PEER_PANEL_CACHE_ENABLED", False)
    assert peer_panel.start_run(conn) is False and not peer_panel.active_for(conn)


@pytest.mark.parametrize("flag", ["CAPTURE_MONTH_END_ENABLED", "PERSISTENCE_FIRST_LAST_NAV_ENABLED"])
def test_a_legacy_flag_combination_takes_the_sql_path_even_while_active(db, monkeypatch, flag):
    conn, isins = db
    monkeypatch.setattr(config, "EUR_NAV_CONVERSION_ENABLED", False)
    monkeypatch.setattr(config, "FX_CONTRIBUTION_EUR_VIEW_ENABLED", False)
    monkeypatch.setattr(config, flag, False)
    c = _Counting(conn)
    peer_panel.start_run(c)
    capture_ratios.load_peer_benchmark(c, NATURE, isins[0])
    persistence._category_return_in_window(c, NATURE, isins[0], pd.Timestamp("2018-01-31"), pd.Timestamp("2021-01-31"))
    assert c.reads >= (1 if flag == "CAPTURE_MONTH_END_ENABLED" else 1)
    reads_before = c.reads
    capture_ratios.load_peer_benchmark(c, NATURE, isins[1])
    persistence._category_return_in_window(c, NATURE, isins[1], pd.Timestamp("2018-01-31"), pd.Timestamp("2021-01-31"))
    assert c.reads > reads_before                          # the accelerated half is cached, the legacy half keeps reading


def test_the_kill_switch_is_not_part_of_the_p2_fingerprint_bundle():
    assert "PEER_PANEL_CACHE_ENABLED" not in config.P2_BUNDLE_FLAGS      # enabling it must not recompute a single fund
    assert config.PEER_PANEL_CACHE_ENABLED is True
