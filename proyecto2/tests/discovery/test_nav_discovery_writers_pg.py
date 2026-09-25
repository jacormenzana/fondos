# proyecto2/tests/discovery/test_nav_discovery_writers_pg.py
# -*- coding: utf-8 -*-
"""
Postgres migration Phase 5c (2026-09-20) — dedicated regression tests for
src/discovery/nav_discovery.py's DB-writing functions: _write_nav_source,
_write_nav_rows (monthly), _write_nav_rows_daily, _ensure_data_status_column,
_overwrite_nav_rows_monthly, _auto_freeze_stale_navs. This is the last P2 write-path
file in the "P2 write paths" tranche — the SQLite-only siblings
(test_nav_discovery_freeze_20260915.py, test_nav_discovery_splice_20260915.py,
test_nav_monthly_write_20260913.py) are unchanged and still cover the :memory: path.

All six functions here either commit internally (_write_nav_source,
_ensure_data_status_column, _auto_freeze_stale_navs) or are called by a caller that
commits in batches (_write_nav_rows, _write_nav_rows_daily, _overwrite_nav_rows_monthly)
-- every one of them needs writes to be commit-visible, so this uses
pg_conn_module_schema/pg_session_conn -- never bare pg_conn/SAVEPOINT -- same discipline
as every other commit-owning function in this migration. Unlike
preserve_and_write() (shared/statistical_audit/persistence.py), none of these functions
has a "two statements must commit/rollback together" atomicity contract, so none needs
the autocommit=False toggle that test needed -- pg_conn_module_schema's default
autocommit=True is fine here (a bare conn.commit() call is a harmless no-op when nothing
is pending, same as verified for macro_discovery.py's _write_inflation/_write_macro).

fund_nav_monthly / fund_nav_daily rename every column to lowercase in the target schema
(isin/date/nav/nav_currency/nav_type/is_estimated/data_source vs SQLite's
ISIN/Date/NAV/NAV_Currency/NAV_Type/Is_Estimated/Data_Source) -- the production port
branches on this for every SQL string, including the DELETE ... WHERE isin=... subquery
inside the nav_sources UPDATE (not exercised directly here, that lives in run_load()/
run_update(), out of scope -- see the module-level write-path grep in the porting task).
nav_sources itself is NOT case-folded -- its columns were already lowercase in SQLite.

Real, non-cosmetic dialect finding from this port: `date` is a native Postgres `date`
column (not text) in fund_nav_monthly/fund_nav_daily, so `_write_nav_rows`'s open-month
cleanup (SQLite: substr(Date,1,7)) needs an explicit substr(date::text,1,7) cast on
Postgres -- a bare substr() on a date value is a type error there, not a silent truncation.
"""
from __future__ import annotations

from datetime import date, timedelta

from src.discovery.nav_discovery import (
    _FROZEN_NAV_DAYS,
    _auto_freeze_stale_navs,
    _ensure_data_status_column,
    _overwrite_nav_rows_monthly,
    _write_nav_rows,
    _write_nav_rows_daily,
    _write_nav_source,
)


def _make_nav_sources(conn, with_data_status=True):
    data_status_col = (
        ", data_status text DEFAULT 'OK' "
        "CHECK (data_status IN ('OK','FORCE_REFRESH','RECALCULATE_MONTHLY',"
        "'RECALCULATE_METRICS','PENDING','STALE_FROZEN'))"
        if with_data_status else ""
    )
    conn.execute(f"""
        CREATE TABLE nav_sources (
            isin           varchar(12) PRIMARY KEY,
            source         text,
            source_id      text,
            first_nav_date date,
            last_nav_date  date,
            nav_count      integer,
            discovered_at  date,
            last_checked   date,
            status         text CHECK (status IN ('OK','NOT_FOUND','ERROR'))
            {data_status_col}
        )
    """)


def _make_fund_nav_monthly(conn):
    conn.execute("""
        CREATE TABLE fund_nav_monthly (
            isin          varchar(12) NOT NULL,
            date          date NOT NULL,
            nav           double precision NOT NULL,
            nav_currency  varchar(3),
            nav_type      text DEFAULT 'NAV',
            is_estimated  smallint DEFAULT 0,
            data_source   text,
            ingested_at   timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (isin, date)
        )
    """)


def _make_fund_nav_daily(conn):
    conn.execute("""
        CREATE TABLE fund_nav_daily (
            isin          varchar(12) NOT NULL,
            date          date NOT NULL,
            nav           double precision NOT NULL,
            nav_currency  varchar(3),
            nav_type      text DEFAULT 'TOTAL_RETURN_IDX',
            is_estimated  smallint DEFAULT 0,
            data_source   text,
            ingested_at   timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (isin, date)
        )
    """)


def _row(isin="LU1", d="2026-06-30", nav=100.0, source="MORNINGSTAR_CHART"):
    return {
        "ISIN": isin, "Date": d, "NAV": nav, "NAV_Currency": "EUR",
        "NAV_Type": "TOTAL_RETURN_IDX", "Is_Estimated": 0, "Data_Source": source,
    }


def test_write_nav_source_upsert_not_found_guard_and_dry_run(
    pg_session_conn, pg_conn_module_schema,
):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_nav_sources(conn)

    # First discovery: ISIN resolved OK with a code.
    _write_nav_source(conn, "LU1", "MORNINGSTAR", "ABC123",
                       None, None, None, "OK", dry_run=False)
    row = conn.execute(
        "SELECT source_id, status FROM nav_sources WHERE isin='LU1'"
    ).fetchone()
    assert row == ("ABC123", "OK")

    # Root-cause regression guard (same invariant as the SQLite sibling): a later
    # re-check that comes back NOT_FOUND (e.g. transient Akamai bot-challenge
    # misclassified upstream) must NOT overwrite an already-resolved code/status --
    # P#1 COALESCE + the explicit CASE degrade guard, both survive the ? -> %s port.
    _write_nav_source(conn, "LU1", "MORNINGSTAR", "",
                       None, None, None, "NOT_FOUND", dry_run=False)
    row = conn.execute(
        "SELECT source_id, status FROM nav_sources WHERE isin='LU1'"
    ).fetchone()
    assert row == ("ABC123", "OK")

    # A genuinely fresh NOT_FOUND (no prior resolved code) must still write through.
    _write_nav_source(conn, "LU2", "MORNINGSTAR", "",
                       None, None, None, "NOT_FOUND", dry_run=False)
    row = conn.execute(
        "SELECT source_id, status FROM nav_sources WHERE isin='LU2'"
    ).fetchone()
    assert row == ("", "NOT_FOUND")

    # Normal update: new range/count propagate via COALESCE.
    _write_nav_source(conn, "LU1", "MORNINGSTAR", "ABC123",
                       "2020-01-31", "2026-06-30", 78, "OK", dry_run=False)
    row = conn.execute(
        "SELECT first_nav_date, last_nav_date, nav_count FROM nav_sources WHERE isin='LU1'"
    ).fetchone()
    assert row == (date(2020, 1, 31), date(2026, 6, 30), 78)

    # dry_run writes nothing.
    _write_nav_source(conn, "LU3", "MORNINGSTAR", "XYZ",
                       None, None, None, "OK", dry_run=True)
    count = conn.execute("SELECT COUNT(*) FROM nav_sources WHERE isin='LU3'").fetchone()[0]
    assert count == 0


def test_write_nav_rows_open_month_dedup_and_conflict_do_nothing(
    pg_session_conn, pg_conn_module_schema,
):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_fund_nav_monthly(conn)

    # Seed a provisional open-month row with an earlier Date under the same YYYY-MM.
    n = _write_nav_rows(conn, [_row(d="2026-06-15", nav=99.0)], dry_run=False)
    assert n == 1

    # A later ingest within the SAME still-open month must replace the provisional
    # row (FIX-NAV-OPEN-MONTH-1), not accumulate a second row for June.
    n = _write_nav_rows(conn, [_row(d="2026-06-30", nav=101.5)], dry_run=False)
    assert n == 1
    rows = conn.execute(
        "SELECT date, nav FROM fund_nav_monthly WHERE isin='LU1'"
    ).fetchall()
    assert rows == [(date(2026, 6, 30), 101.5)]

    # ON CONFLICT DO NOTHING (INSERT OR IGNORE parity): two rows sharing the same
    # (isin, date) key in the SAME call -- the DELETE-by-month step clears any prior
    # row first, but within one executemany batch the second insert still collides
    # with the first (already landed moments earlier) and must be silently skipped,
    # not raise a unique-violation.
    _write_nav_rows(conn, [
        _row(d="2026-07-31", nav=111.0),
        _row(d="2026-07-31", nav=222.0),
    ], dry_run=False)
    rows_july = conn.execute(
        "SELECT nav FROM fund_nav_monthly WHERE isin='LU1' AND date='2026-07-31'"
    ).fetchall()
    assert len(rows_july) == 1
    assert rows_july[0][0] == 111.0  # first row in the batch wins, second is a no-op

    assert _write_nav_rows(conn, [], dry_run=False) == 0
    assert _write_nav_rows(conn, [_row(isin="LU9")], dry_run=True) == 0
    count_lu9 = conn.execute("SELECT COUNT(*) FROM fund_nav_monthly WHERE isin='LU9'").fetchone()[0]
    assert count_lu9 == 0


def test_write_nav_rows_daily_deletes_stale_source_and_upserts(
    pg_session_conn, pg_conn_module_schema,
):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_fund_nav_daily(conn)

    # Legacy sal-service row (non-chart source) at the same date must be purged
    # before the chartservice batch lands -- mixing scales produces phantom -99%
    # returns (see _write_nav_rows_daily's own docstring).
    conn.execute(
        "INSERT INTO fund_nav_daily (isin, date, nav, nav_currency, nav_type, "
        "is_estimated, data_source) VALUES ('LU1', '2026-06-30', 9123.0, 'EUR', "
        "'TOTAL_RETURN_IDX', 0, 'MORNINGSTAR')"
    )

    n = _write_nav_rows_daily(conn, [_row(d="2026-06-30", nav=9.5)], dry_run=False)
    assert n == 1
    rows = conn.execute(
        "SELECT nav, data_source FROM fund_nav_daily WHERE isin='LU1'"
    ).fetchall()
    assert rows == [(9.5, "MORNINGSTAR_CHART")]

    # ON CONFLICT DO UPDATE (INSERT OR REPLACE parity): re-fetching the same date
    # with a corrected value must overwrite in place.
    _write_nav_rows_daily(conn, [_row(d="2026-06-30", nav=9.8)], dry_run=False)
    value = conn.execute(
        "SELECT nav FROM fund_nav_daily WHERE isin='LU1' AND date='2026-06-30'"
    ).fetchone()[0]
    assert value == 9.8

    assert _write_nav_rows_daily(conn, [], dry_run=False) == 0


def test_ensure_data_status_column_issues_no_ddl_on_postgres(pg_session_conn, pg_conn_module_schema):
    # The column/index belong to db/pg/35_control.sql. Even a no-op ALTER ... IF NOT EXISTS needs table
    # ownership, so runtime DDL would fail under the least-privilege fondos_app role (FND-0072).
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_nav_sources(conn, with_data_status=False)

    def _cols():
        return {r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            f"WHERE table_schema='{pg_conn_module_schema}' AND table_name='nav_sources'"
        ).fetchall()}

    before = _cols()
    _ensure_data_status_column(conn)
    _ensure_data_status_column(conn)
    assert _cols() == before and "data_status" not in _cols()


def test_overwrite_nav_rows_monthly_replaces_full_history(
    pg_session_conn, pg_conn_module_schema,
):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_fund_nav_monthly(conn)

    _write_nav_rows(conn, [
        _row(d="2026-04-30", nav=90.0),
        _row(d="2026-05-31", nav=95.0),
        _row(d="2026-06-30", nav=100.0),
    ], dry_run=False)
    count = conn.execute("SELECT COUNT(*) FROM fund_nav_monthly WHERE isin='LU1'").fetchone()[0]
    assert count == 3

    written = _overwrite_nav_rows_monthly(conn, "LU1", [
        _row(d="2026-05-31", nav=96.0),
        _row(d="2026-06-30", nav=101.0),
    ], dry_run=False)
    assert written == 2
    rows = conn.execute(
        "SELECT date, nav FROM fund_nav_monthly WHERE isin='LU1' ORDER BY date"
    ).fetchall()
    assert rows == [(date(2026, 5, 31), 96.0), (date(2026, 6, 30), 101.0)]

    assert _overwrite_nav_rows_monthly(conn, "LU1", [_row()], dry_run=True) == 1
    count_after_dry = conn.execute(
        "SELECT COUNT(*) FROM fund_nav_monthly WHERE isin='LU1'"
    ).fetchone()[0]
    assert count_after_dry == 2  # dry_run must not have touched anything


def _insert_nav_source(conn, isin, last_nav_date, last_checked,
                        data_status="OK", status="OK"):
    conn.execute(
        "INSERT INTO nav_sources (isin, source, source_id, last_nav_date, "
        "last_checked, status, data_status) VALUES (%s, 'MORNINGSTAR', %s, %s, %s, %s, %s)",
        (isin, f"ms_{isin}", last_nav_date, last_checked, status, data_status),
    )


def test_auto_freeze_stale_navs_marks_qualifying_sources_only(
    pg_session_conn, pg_conn_module_schema,
):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_nav_sources(conn)

    old_nav = (date.today() - timedelta(days=_FROZEN_NAV_DAYS + 30)).isoformat()
    recent_check = (date.today() - timedelta(days=5)).isoformat()
    recent_nav = (date.today() - timedelta(days=10)).isoformat()
    old_check = (date.today() - timedelta(days=90)).isoformat()

    _insert_nav_source(conn, "LU_FROZEN", old_nav, recent_check)
    _insert_nav_source(conn, "LU_ACTIVE", recent_nav, recent_check)
    _insert_nav_source(conn, "LU_UNCHECKED", old_nav, old_check)
    _insert_nav_source(conn, "LU_NOTFOUND", old_nav, recent_check, status="NOT_FOUND")

    frozen = _auto_freeze_stale_navs(conn, dry_run=False)

    assert sorted(r[0] for r in frozen) == ["LU_FROZEN"]
    statuses = dict(conn.execute(
        "SELECT isin, data_status FROM nav_sources ORDER BY isin"
    ).fetchall())
    assert statuses == {
        "LU_ACTIVE": "OK", "LU_FROZEN": "STALE_FROZEN",
        "LU_NOTFOUND": "OK", "LU_UNCHECKED": "OK",
    }

    # Idempotent: already-frozen fund is not re-detected/re-logged.
    assert _auto_freeze_stale_navs(conn, dry_run=False) == []


def test_auto_freeze_stale_navs_dry_run_never_writes(
    pg_session_conn, pg_conn_module_schema,
):
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    _make_nav_sources(conn)

    old_nav = (date.today() - timedelta(days=_FROZEN_NAV_DAYS + 30)).isoformat()
    recent_check = (date.today() - timedelta(days=5)).isoformat()
    _insert_nav_source(conn, "LU_DRYRUN", old_nav, recent_check)

    assert _auto_freeze_stale_navs(conn, dry_run=True) == []
    status = conn.execute(
        "SELECT data_status FROM nav_sources WHERE isin='LU_DRYRUN'"
    ).fetchone()[0]
    assert status == "OK"
