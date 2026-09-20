# proyecto1/tests/test_benchmark_loader_pg.py
# -*- coding: utf-8 -*-
"""
Postgres migration Phase 5c (2026-09-20) — dedicated regression tests for
src.loaders.benchmark_loader's write function (_write_benchmark) and its
placeholder-parameterized read (_get_isins_for_load with isin_filter).

_write_benchmark commits internally, so its test uses pg_conn_module_schema/pg_session_conn
(never bare pg_conn) — same discipline as every other write-path function in this migration.
"""
from __future__ import annotations

import importlib.util
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_LOADERS_DIR = os.path.normpath(os.path.join(_TESTS_DIR, "..", "src", "loaders"))
_P1_DIR = os.path.normpath(os.path.join(_TESTS_DIR, ".."))
if _P1_DIR not in sys.path:
    sys.path.insert(0, _P1_DIR)

_spec = importlib.util.spec_from_file_location(
    "benchmark_loader_pg_test", os.path.join(_LOADERS_DIR, "benchmark_loader.py")
)
_bl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bl)
_write_benchmark = _bl._write_benchmark
_get_isins_for_load = _bl._get_isins_for_load


def _make_fund_benchmarks(conn):
    conn.execute("""
        CREATE TABLE fund_benchmarks (
            isin TEXT NOT NULL,
            source TEXT NOT NULL,
            benchmark_raw TEXT,
            benchmark_id TEXT,
            benchmark_name TEXT,
            provider TEXT,
            asset_class TEXT,
            confidence TEXT,
            benchmark_role TEXT DEFAULT 'asset_proxy',
            extracted_at TEXT,
            PRIMARY KEY (isin, source)
        )
    """)


def test_write_benchmark_insert_path_creates_new_row(pg_session_conn, pg_conn_module_schema):
    """First write for an ISIN with no existing fund_benchmarks row — confirms the ON CONFLICT
    DO UPDATE branch's INSERT path (no conflict) works, and normalize_benchmark's result columns
    land correctly. Uses a raw_name that won't normalize (no canonical match) to exercise the
    RAW_ONLY status without depending on benchmark_normalizer's exact vocabulary."""
    conn = pg_session_conn
    schema = pg_conn_module_schema
    conn.execute(f"SET search_path = {schema}")
    _make_fund_benchmarks(conn)

    status = _write_benchmark(conn, "NEW1", "Some Totally Unrecognized Index Name XYZ", None, dry_run=False)

    assert status in ("RAW_ONLY", "NORMALIZADO")
    row = conn.execute(
        "SELECT source, benchmark_raw, confidence, benchmark_role FROM fund_benchmarks WHERE isin = 'NEW1'"
    ).fetchone()
    assert row[0] == "MORNINGSTAR"
    assert row[1] == "Some Totally Unrecognized Index Name XYZ"
    assert row[3] == "asset_proxy", "new row must get the column DEFAULT"


def test_write_benchmark_update_path_replaces_row_and_resets_benchmark_role(
    pg_session_conn, pg_conn_module_schema,
):
    """Confirms the faithful INSERT OR REPLACE -> ON CONFLICT DO UPDATE translation: a second
    write for the same (ISIN, source) fully replaces benchmark_raw/id/name/etc AND resets
    benchmark_role to its DEFAULT — replicating SQLite's REPLACE-is-DELETE+INSERT behavior for
    the column omitted from the write's own column list, not silently preserving whatever a prior
    (unrelated) process had set it to."""
    conn = pg_session_conn
    schema = pg_conn_module_schema
    conn.execute(f"SET search_path = {schema}")
    _make_fund_benchmarks(conn)

    conn.execute("""
        INSERT INTO fund_benchmarks
            (isin, source, benchmark_raw, benchmark_id, benchmark_name,
             provider, asset_class, confidence, benchmark_role, extracted_at)
        VALUES
            ('U1', 'MORNINGSTAR', 'Old Raw Name', 'OLD_ID', 'Old Name',
             'OldProv', 'Equity', 'HIGH', 'hurdle_rate', '2020-01-01T00:00:00')
    """)

    status = _write_benchmark(conn, "U1", "New Raw Name Index", None, dry_run=False)

    assert status in ("RAW_ONLY", "NORMALIZADO")
    row = conn.execute(
        "SELECT benchmark_raw, benchmark_role FROM fund_benchmarks WHERE isin = 'U1'"
    ).fetchone()
    assert row[0] == "New Raw Name Index", "benchmark_raw must be replaced by the new write"
    assert row[1] == "asset_proxy", (
        "benchmark_role must be reset to DEFAULT on every write, matching SQLite's "
        "INSERT OR REPLACE (DELETE+INSERT) semantics for a column outside the write's own list"
    )
    # Exactly one row for this (isin, source) — proves it's a replace, not an accidental 2nd row.
    n = conn.execute("SELECT COUNT(*) FROM fund_benchmarks WHERE isin = 'U1'").fetchone()[0]
    assert n == 1


def test_write_benchmark_dry_run_makes_no_changes(pg_session_conn, pg_conn_module_schema):
    conn = pg_session_conn
    schema = pg_conn_module_schema
    conn.execute(f"SET search_path = {schema}")
    _make_fund_benchmarks(conn)

    status = _write_benchmark(conn, "D1", "Some Index Name", None, dry_run=True)

    assert status.startswith("RAW_ONLY") or status.startswith("NORMALIZADO")
    n = conn.execute("SELECT COUNT(*) FROM fund_benchmarks WHERE isin = 'D1'").fetchone()[0]
    assert n == 0, "dry_run must not write anything"


def test_get_isins_for_load_isin_filter(pg_conn):
    """Read-only, safe under the standard bare pg_conn SAVEPOINT fixture. Confirms the ?->%s
    translation for the isin_filter branch."""
    pg_conn.execute("""
        CREATE TABLE nav_sources (
            isin TEXT PRIMARY KEY,
            source TEXT,
            source_id TEXT,
            status TEXT
        )
    """)
    pg_conn.execute("""
        INSERT INTO nav_sources (isin, source_id, status) VALUES
        ('F1', 'MSID1', 'OK'),
        ('F2', 'MSID2', 'NOT_FOUND')
    """)

    result = _get_isins_for_load(pg_conn, isin_filter="F1")
    assert result == [("F1", "MSID1")]

    result_missing = _get_isins_for_load(pg_conn, isin_filter="F2")
    assert result_missing == [], "status != 'OK' must be excluded"
