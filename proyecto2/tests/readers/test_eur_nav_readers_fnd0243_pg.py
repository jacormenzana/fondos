# proyecto2/tests/readers/test_eur_nav_readers_fnd0243_pg.py
# -*- coding: utf-8 -*-
"""
FND-0243 step 3 on Postgres: the real reader SQL (db_readers.load_nav / load_nav_daily, persistence's peer window,
capture's peer benchmark) with the EUR view switched on and off. ECB-style rates in series_macro (units of currency
per EUR) that DRIFT month by month, so a missing conversion cannot pass by accident. R-7: no run_pipeline/core.io.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

_P2 = Path(__file__).resolve().parent.parent.parent
_REPO = _P2.parent
for _p in (str(_P2), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from shared import config  # noqa: E402
from shared import eur_nav  # noqa: E402
from src.readers.db_readers import load_nav, load_nav_daily  # noqa: E402
from src.calculations import persistence, capture_ratios  # noqa: E402

MONTHS = pd.date_range("2023-10-31", "2026-09-30", freq="ME")     # 36 month-ends (persistence window)


def _k(d: pd.Timestamp) -> int:
    return (d.year - 2023) * 12 + d.month - 10


def _rate(k: int) -> float:
    return 1.25 * (1 + 0.005 * k)


def _nav_class(base: float, k: int) -> float:
    return base * (1 + 0.01 * k)


@pytest.fixture
def db(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    for t in ("fund_master", "series_macro", "fund_nav_monthly", "fund_nav_daily"):
        conn.execute(f"DROP TABLE IF EXISTS {t}")
    conn.execute("CREATE TABLE fund_master (isin text PRIMARY KEY, fund_nature text, fund_currency varchar(3))")
    conn.execute("CREATE TABLE series_macro (date date, indicator text, geography text, value double precision, "
                 "unit text, source text, PRIMARY KEY (date, indicator, geography))")
    for t in ("fund_nav_monthly", "fund_nav_daily"):
        conn.execute(f"CREATE TABLE {t} (isin text, date date, nav double precision, nav_currency varchar(3), "
                     "PRIMARY KEY (isin, date))")
    conn.execute("INSERT INTO fund_master VALUES ('EURF','Mixtos','EUR'),('USDF','Mixtos','USD'),('NULLF','Mixtos',NULL)")
    days = pd.bdate_range("2023-10-02", "2026-09-30")
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO series_macro VALUES (%s, %s, 'USD', %s, 'ccy_per_eur', 'BCE')",
                        [(d.date(), config.EUR_FX_DAILY_INDICATOR, _rate(_k(d))) for d in days])
        for isin, base in (("EURF", 100.0), ("USDF", 125.0), ("NULLF", 50.0)):
            cur.executemany("INSERT INTO fund_nav_monthly VALUES (%s,%s,%s,NULL)",
                            [(isin, m.date(), _nav_class(base, _k(m))) for m in MONTHS])
            cur.executemany("INSERT INTO fund_nav_daily VALUES (%s,%s,%s,NULL)",
                            [(isin, d.date(), base) for d in days[-10:]])
    eur_nav.reset_cache()
    yield conn
    eur_nav.reset_cache()


def test_switch_off_readers_return_the_stored_class_currency_nav(db, monkeypatch):
    monkeypatch.setattr(config, "EUR_NAV_CONVERSION_ENABLED", False)
    usd = load_nav(db, "USDF")
    assert usd["nav"].round(10).tolist() == [round(_nav_class(125.0, _k(m)), 10) for m in MONTHS]
    assert len(load_nav(db, "NULLF")) == len(MONTHS)          # an unknown currency is only excluded when ON


def test_switch_on_converts_usd_and_excludes_unknown_currency(db, monkeypatch):
    monkeypatch.setattr(config, "EUR_NAV_CONVERSION_ENABLED", True)
    usd = load_nav(db, "USDF")
    expected = [_nav_class(125.0, _k(m)) / _rate(_k(m)) for m in MONTHS]
    assert usd["nav"].tolist() == pytest.approx(expected)
    assert usd.attrs[eur_nav.ATTR] == eur_nav.STATUS_CONVERTED
    eur = load_nav(db, "EURF")
    assert eur["nav"].tolist() == pytest.approx([_nav_class(100.0, _k(m)) for m in MONTHS])
    assert eur.attrs[eur_nav.ATTR] == eur_nav.STATUS_EUR
    excluded = load_nav(db, "NULLF")
    assert excluded.empty and eur_nav.is_excluded(excluded)
    daily = load_nav_daily(db, "USDF")
    assert daily["nav"].tolist() == pytest.approx([125.0 / _rate(_k(MONTHS[-1]))] * 10)


def test_persistence_peer_window_converts_first_and_last_nav(db, monkeypatch):
    start, end = MONTHS[0], MONTHS[-1]
    years = len(MONTHS) / 12
    monkeypatch.setattr(config, "EUR_NAV_CONVERSION_ENABLED", False)
    r_off = persistence._category_return_in_window(db, "Mixtos", "EURF", start, end)
    raw = (_nav_class(1, _k(end)) / _nav_class(1, 0)) ** (1 / years) - 1      # USDF and NULLF share the path
    assert r_off == pytest.approx(raw)
    monkeypatch.setattr(config, "EUR_NAV_CONVERSION_ENABLED", True)
    eur_nav.reset_cache()
    r_on = persistence._category_return_in_window(db, "Mixtos", "EURF", start, end)
    first = _nav_class(125.0, 0) / _rate(0)
    last = _nav_class(125.0, _k(end)) / _rate(_k(end))
    assert r_on == pytest.approx((last / first) ** (1 / years) - 1)          # USDF only, in EUR
    assert r_on < r_off


def test_capture_peer_benchmark_uses_eur_returns(db, monkeypatch):
    monkeypatch.setattr(config, "EUR_NAV_CONVERSION_ENABLED", True)
    bench = capture_ratios.load_peer_benchmark(db, "Mixtos", "EURF")
    # peers = USDF only (NULLF excluded): monthly EUR return of USDF
    k = 5
    exp = (_nav_class(1, k) / _rate(k)) / (_nav_class(1, k - 1) / _rate(k - 1)) - 1
    assert bench.loc[MONTHS[k]] == pytest.approx(exp)
