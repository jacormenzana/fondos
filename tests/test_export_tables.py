"""
tests/test_export_tables.py — shared.export_tables (the Excel export engine behind P1's
export_p1.py). Added during the Postgres migration's Stage 9 (2026-09-23): this module was the
plan's original "Stage 7 — Export/reporting port" and was silently dropped when Stage 7 was
renumbered, so it reached Stage 9 still using a raw sqlite3.connect(), `PRAGMA table_info` and
pd.read_sql_query, with zero tests.

Part A (SQLite) locks the pre-existing behavior and runs everywhere. Part B targets the four
Postgres-specific hazards the port had to handle, each of which fails SILENTLY or per-table rather
than loudly:
  1. exclude_cols / include_cols are spelled the SQLite way (`Raw_KIID_Text`); Postgres folds to
     lowercase, so an exact-match exclusion would quietly NOT apply and export_p1 would write
     ~58M characters of KIID text into the Excel (~500 MB vs ~10 MB — the bug the module's v17
     header documents).
  2. `numeric` arrives as Decimal, which pandas writes to Excel as TEXT, not a number.
  3. timestamptz arrives tz-aware, which DataFrame.to_excel refuses outright.
  4. A failed statement aborts the WHOLE Postgres transaction, so without a rollback the
     "one bad table doesn't abort the rest" guarantee silently degrades to "every later table
     fails too".
"""
from __future__ import annotations

import io
import os
import sqlite3
import sys
import uuid
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.export_tables import (  # noqa: E402
    TableExportConfig,
    _drop_excluded,
    _read_table_df,
    export_tables,
)

openpyxl = pytest.importorskip("openpyxl")


def _sheet_rows(path: Path, sheet: str) -> list[list]:
    wb = openpyxl.load_workbook(path)
    try:
        return [list(r) for r in wb[sheet].iter_rows(values_only=True)]
    finally:
        wb.close()


def _sheet_names(path: Path) -> list[str]:
    wb = openpyxl.load_workbook(path)
    try:
        return list(wb.sheetnames)
    finally:
        wb.close()


# =============================================================================
# Part A — SQLite (existing behavior must not change)
# =============================================================================

@pytest.fixture()
def sqlite_db(tmp_path: Path) -> Path:
    db = tmp_path / "t.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE fund_master (ISIN TEXT, Fund_Name TEXT, Fund_Nature TEXT, "
        "Raw_KIID_Text TEXT, Score REAL)"
    )
    conn.executemany(
        "INSERT INTO fund_master VALUES (?,?,?,?,?)",
        [("B02", "Beta", "Mixtos", "long text b", 2.5),
         ("A01", "Alpha", "Renta Variable", "long text a", 1.25)],
    )
    conn.commit()
    conn.close()
    return db


def test_sqlite_exclude_cols_order_by_and_numeric_cells(sqlite_db, tmp_path):
    out = tmp_path / "o.xlsx"
    export_tables(
        [TableExportConfig(table="fund_master", sheet_name="S1",
                           exclude_cols=["Raw_KIID_Text"], order_by="ISIN")],
        out, sqlite_db, verbose=False, backend="sqlite",
    )
    rows = _sheet_rows(out, "S1")
    assert rows[0] == ["ISIN", "Fund_Name", "Fund_Nature", "Score"]
    assert [r[0] for r in rows[1:]] == ["A01", "B02"]
    assert isinstance(rows[1][3], float) and rows[1][3] == 1.25


def test_sqlite_include_cols_is_case_insensitive_and_orders_as_requested(sqlite_db, tmp_path):
    out = tmp_path / "o.xlsx"
    export_tables(
        [TableExportConfig(table="fund_master", sheet_name="S1",
                           include_cols=["fund_name", "ISIN"], order_by="ISIN")],
        out, sqlite_db, verbose=False, backend="sqlite",
    )
    rows = _sheet_rows(out, "S1")
    # Columns are quoted with their REAL spelling, in the caller's requested order.
    assert rows[0] == ["Fund_Name", "ISIN"]
    assert rows[1] == ["Alpha", "A01"]


def test_sqlite_one_bad_table_does_not_abort_the_others(sqlite_db, tmp_path):
    out = tmp_path / "o.xlsx"
    export_tables(
        [TableExportConfig(table="no_such_table", sheet_name="BAD"),
         TableExportConfig(table="fund_master", sheet_name="GOOD")],
        out, sqlite_db, verbose=False, backend="sqlite",
    )
    assert _sheet_names(out) == ["GOOD"]


def test_sqlite_missing_database_raises_before_writing_anything(tmp_path):
    out = tmp_path / "o.xlsx"
    with pytest.raises(FileNotFoundError):
        export_tables([TableExportConfig(table="fund_master")], out,
                      tmp_path / "does_not_exist.sqlite", verbose=False, backend="sqlite")
    assert not out.exists()


# =============================================================================
# Part B — Postgres
# =============================================================================

_TABLE_DDL = (
    "CREATE TABLE {t} (isin text, fund_name text, raw_kiid_text text, score double precision, "
    "cost numeric(10,4), d date, created_at timestamptz)"
)
_INSERT = (
    "INSERT INTO {t} VALUES ('A01','Alpha','long text a',1.25,0.0150,'2026-07-01',"
    "'2026-07-01 10:00:00+00')"
)


def test_pg_include_cols_resolve_case_insensitively_to_the_real_lowercase_names(pg_conn):
    """The caller (export_p1) spells columns the SQLite way. `"ISIN"` quoted is a different,
    non-existent identifier on Postgres — build_query must quote the REAL spelling."""
    pg_conn.execute(_TABLE_DDL.format(t="export_case_t"))
    pg_conn.execute(_INSERT.format(t="export_case_t"))
    q = TableExportConfig(table="export_case_t",
                          include_cols=["ISIN", "Fund_Name", "Cost", "Not_A_Column"]).build_query(pg_conn)
    df = _read_table_df(pg_conn, q)
    assert list(df.columns) == ["isin", "fund_name", "cost"]
    assert len(df) == 1


def test_pg_exclude_cols_apply_despite_case_difference(pg_conn):
    """Regression guard for the ~500 MB-instead-of-~10 MB export: `Raw_KIID_Text` must drop the
    Postgres column `raw_kiid_text`."""
    pg_conn.execute(_TABLE_DDL.format(t="export_excl_t"))
    pg_conn.execute(_INSERT.format(t="export_excl_t"))
    df = _read_table_df(pg_conn, "SELECT * FROM export_excl_t")
    assert "raw_kiid_text" in df.columns
    out = _drop_excluded(df, ["Raw_KIID_Text"])
    assert "raw_kiid_text" not in out.columns
    assert "isin" in out.columns


def test_pg_numeric_becomes_float_and_timestamptz_becomes_naive_local_time(pg_conn):
    pg_conn.execute(_TABLE_DDL.format(t="export_types_t"))
    pg_conn.execute(_INSERT.format(t="export_types_t"))
    df = _read_table_df(pg_conn, "SELECT * FROM export_types_t")

    # numeric(10,4) arrives as Decimal from psycopg3; coerce_float must turn it into float64.
    assert str(df["cost"].dtype) == "float64", df["cost"].dtype
    assert not isinstance(df["cost"].iloc[0], Decimal)

    # 10:00 UTC on 2026-07-01 is 12:00 in Europe/Madrid (CEST, +02) — naive, no tz attached.
    ts = df["created_at"].iloc[0]
    assert pd.Timestamp(ts).tzinfo is None
    assert pd.Timestamp(ts) == pd.Timestamp("2026-07-01 12:00:00")

    # The actual failure this prevents: to_excel raises on tz-aware datetimes.
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as w:
        df.to_excel(w, sheet_name="S", index=False)
    assert buf.getvalue()


@pytest.fixture()
def committed_pg_table():
    """A real, COMMITTED table in `public` (get_connection's search_path resolves it) — export_tables
    opens its OWN connection, so a savepoint-scoped pg_conn table would be invisible to it.
    Dropped in a finally so a failing test can't leave debris in a persistent dev database."""
    dsn = os.environ.get("FONDOS_TEST_PG_DSN")
    if not dsn:
        pytest.skip("FONDOS_TEST_PG_DSN not set — point it at a Postgres 15+ instance")
    psycopg = pytest.importorskip("psycopg")
    name = f"export_e2e_{uuid.uuid4().hex[:10]}"
    admin = psycopg.connect(dsn, autocommit=True)
    try:
        admin.execute(_TABLE_DDL.format(t=f"public.{name}"))
        admin.execute(_INSERT.format(t=f"public.{name}"))
        yield name, dsn
    finally:
        admin.execute(f"DROP TABLE IF EXISTS public.{name}")
        admin.close()


def test_pg_end_to_end_one_bad_table_does_not_poison_the_rest(committed_pg_table, tmp_path, monkeypatch):
    """The bad table comes FIRST. Without export_tables' conn.rollback() in its except handler, the
    aborted transaction would make the good table's query fail with InFailedSqlTransaction and the
    file would contain no sheets at all."""
    name, dsn = committed_pg_table
    monkeypatch.setenv("FONDOS_PG_DSN", dsn)
    out = tmp_path / "o.xlsx"
    export_tables(
        [TableExportConfig(table="no_such_table_xyz", sheet_name="BAD"),
         TableExportConfig(table=name, sheet_name="GOOD", exclude_cols=["Raw_KIID_Text"])],
        out, Path("unused-for-postgres"), verbose=False, backend="postgres",
    )
    assert _sheet_names(out) == ["GOOD"]
    rows = _sheet_rows(out, "GOOD")
    assert "raw_kiid_text" not in rows[0]                       # exclusion applied across the case gap
    assert rows[0] == ["isin", "fund_name", "score", "cost", "d", "created_at"]
    assert rows[1][0] == "A01"
    assert isinstance(rows[1][3], float) and abs(rows[1][3] - 0.015) < 1e-9   # numeric, not text
