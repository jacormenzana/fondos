# proyecto1/tests/test_normalize_db_casing_v20_pg.py
# -*- coding: utf-8 -*-
"""
Postgres migration Phase 5c (2026-09-20) — dedicated regression tests for
core.normalize_db_casing_v20.run(), the write function behind that maintenance CLI script.

run() commits internally, so the Postgres-backed test uses pg_conn_module_schema/pg_session_conn
(never bare pg_conn) — same discipline as every other write-path function in this migration.

run() had ZERO test coverage before this port (it's a standalone one-shot CLI tool, never
previously exercised by the test suite), so this file also adds the first SQLite-path test as
part of proving the dialect-aware refactor made no behavior change there.
"""
from __future__ import annotations

import importlib.util
import os
import sqlite3
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR = os.path.normpath(os.path.join(_TESTS_DIR, "..", "core"))
_P1_DIR = os.path.normpath(os.path.join(_TESTS_DIR, ".."))
if _P1_DIR not in sys.path:
    sys.path.insert(0, _P1_DIR)

_spec = importlib.util.spec_from_file_location(
    "normalize_db_casing_v20_pg_test", os.path.join(_CORE_DIR, "normalize_db_casing_v20.py")
)
_ndc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ndc)
run = _ndc.run


def test_run_sqlite_normalizes_casing_and_legacy_values(tmp_path):
    """Own-connection SQLite path (conn=None, the CLI's default) — confirms the dialect-aware
    refactor (table_columns()/executemany() dispatch, conn injection) made no behavior change
    here: wrong-case values get canonicalized, legacy values get remapped, already-canonical
    values are left untouched, and the report counts match."""
    db_path = str(tmp_path / "casing_test.sqlite")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE fund_master (
            ISIN TEXT PRIMARY KEY,
            Hedging_Policy TEXT
        )
    """)
    conn.executemany(
        "INSERT INTO fund_master (ISIN, Hedging_Policy) VALUES (?, ?)",
        [("H1", "UNHEDGED"), ("H2", "PARTIAL"), ("H3", "Hedged")],
    )
    conn.commit()
    conn.close()

    report = run(db_path, dry_run=False)

    assert report.get("Hedging_Policy") == 2, "H1 and H2 need correction; H3 is already canonical"

    conn2 = sqlite3.connect(db_path)
    rows = dict(conn2.execute("SELECT ISIN, Hedging_Policy FROM fund_master").fetchall())
    conn2.close()
    assert rows["H1"] == "Unhedged", "wrong-case value must be canonicalized"
    assert rows["H2"] == "Partially Hedged", "legacy value 'PARTIAL' must be remapped then canonicalized"
    assert rows["H3"] == "Hedged", "already-canonical value must be untouched"


def test_run_postgres_normalizes_casing_and_legacy_values(pg_session_conn, pg_conn_module_schema):
    """Same scenario as the SQLite test, injected connection (conn=pg_session_conn), isolated
    schema. Confirms: (1) table_columns() resolves fund_master's lowercase Postgres columns
    against DOMAIN_VALUES' mixed-case keys via the caller-side case-fold; (2) the executemany()
    dispatch and %s placeholder translation work; (3) conn.commit() runs clean (own_conn=False,
    so run() must NOT close the shared session connection)."""
    conn = pg_session_conn
    schema = pg_conn_module_schema
    conn.execute(f"SET search_path = {schema}")
    conn.execute("""
        CREATE TABLE fund_master (
            isin TEXT PRIMARY KEY,
            hedging_policy TEXT
        )
    """)
    conn.execute("""
        INSERT INTO fund_master (isin, hedging_policy) VALUES
        ('H1', 'UNHEDGED'), ('H2', 'PARTIAL'), ('H3', 'Hedged')
    """)

    report = run(None, dry_run=False, conn=conn)

    assert report.get("Hedging_Policy") == 2, "H1 and H2 need correction; H3 is already canonical"

    rows = dict(conn.execute("SELECT isin, hedging_policy FROM fund_master").fetchall())
    assert rows["H1"] == "Unhedged"
    assert rows["H2"] == "Partially Hedged"
    assert rows["H3"] == "Hedged"

    # own_conn must be False here — confirm the shared session connection is still usable.
    conn.execute("SELECT 1")


def test_run_dry_run_makes_no_changes(pg_session_conn, pg_conn_module_schema):
    """dry_run=True must report the would-be corrections without writing anything, on Postgres
    too — exercises the early-exit-before-commit branch under the injected-connection path."""
    conn = pg_session_conn
    schema = pg_conn_module_schema
    conn.execute(f"SET search_path = {schema}")
    conn.execute("""
        CREATE TABLE fund_master (
            isin TEXT PRIMARY KEY,
            hedging_policy TEXT
        )
    """)
    conn.execute("INSERT INTO fund_master (isin, hedging_policy) VALUES ('H1', 'UNHEDGED')")

    report = run(None, dry_run=True, conn=conn)

    assert report.get("Hedging_Policy") == 1
    value = conn.execute("SELECT hedging_policy FROM fund_master WHERE isin = 'H1'").fetchone()[0]
    assert value == "UNHEDGED", "dry_run must not mutate fund_master"
