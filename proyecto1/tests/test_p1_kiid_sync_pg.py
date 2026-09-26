# proyecto1/tests/test_p1_kiid_sync_pg.py
# -*- coding: utf-8 -*-
"""
Postgres migration Phase 5c (2026-09-20) — dedicated regression tests for
harvest.p1_kiid_sync's write functions (_lifecycle_activate, _lifecycle_retire,
cmd_backfill_lifecycle) and its dialect-aware read (compute_delta).

_lifecycle_activate()/_lifecycle_retire() do NOT call conn.commit() themselves (the calling
command does), so they're safe to test via the bare pg_conn SAVEPOINT fixture. compute_delta() is
read-only, also safe under pg_conn. cmd_backfill_lifecycle() DOES commit internally, so its test
uses pg_conn_module_schema/pg_session_conn instead — same discipline as every other
commit-owning write function in this migration.

This module had zero test coverage before this port.
"""
from __future__ import annotations

import importlib.util
import os
import sqlite3
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_HARVEST_DIR = os.path.normpath(os.path.join(_TESTS_DIR, "..", "harvest"))
_P1_DIR = os.path.normpath(os.path.join(_TESTS_DIR, ".."))
if _P1_DIR not in sys.path:
    sys.path.insert(0, _P1_DIR)

_spec = importlib.util.spec_from_file_location(
    "p1_kiid_sync_pg_test", os.path.join(_HARVEST_DIR, "p1_kiid_sync.py")
)
_pks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_pks)
_lifecycle_activate = _pks._lifecycle_activate
_lifecycle_retire = _pks._lifecycle_retire
compute_delta = _pks.compute_delta
cmd_backfill_lifecycle = _pks.cmd_backfill_lifecycle


def _make_lifecycle_table(conn):
    conn.execute("""
        CREATE TABLE kiid_lifecycle (
            isin TEXT NOT NULL,
            start_date DATE NOT NULL,
            end_date DATE,
            status TEXT NOT NULL DEFAULT 'commercializing',
            href TEXT,
            retire_dir TEXT,
            PRIMARY KEY (isin, start_date)
        )
    """)


def test_lifecycle_activate_inserts_and_is_idempotent(pg_conn):
    _make_lifecycle_table(pg_conn)
    _lifecycle_activate(pg_conn, "A1", "https://example.com/a1.pdf", "2026-01-01")
    _lifecycle_activate(pg_conn, "A1", "https://example.com/a1.pdf", "2026-01-01")  # same PK, no-op

    rows = pg_conn.execute("SELECT isin, status, href FROM kiid_lifecycle").fetchall()
    assert rows == [("A1", "commercializing", "https://example.com/a1.pdf")]


def test_lifecycle_retire_updates_active_row(pg_conn):
    _make_lifecycle_table(pg_conn)
    _lifecycle_activate(pg_conn, "R1", "https://example.com/r1.pdf", "2026-01-01")

    _lifecycle_retire(pg_conn, "R1", "2026-06-01", "20260601")

    row = pg_conn.execute(
        "SELECT status, end_date, retire_dir FROM kiid_lifecycle WHERE isin = 'R1'"
    ).fetchone()
    assert row[0] == "retired"
    assert str(row[1]) == "2026-06-01"
    assert row[2] == "20260601"


def test_lifecycle_retire_inserts_approximated_row_when_no_active_period(pg_conn):
    """File predates the lifecycle table entirely — no active row to UPDATE, so _lifecycle_retire
    falls back to an approximated INSERT using end_date as start_date."""
    _make_lifecycle_table(pg_conn)

    _lifecycle_retire(pg_conn, "R2", "2026-06-01", "20260601")

    row = pg_conn.execute(
        "SELECT start_date, end_date, status FROM kiid_lifecycle WHERE isin = 'R2'"
    ).fetchone()
    assert str(row[0]) == "2026-06-01"
    assert str(row[1]) == "2026-06-01"
    assert row[2] == "retired"


def test_compute_delta_injected_connection(pg_conn):
    pg_conn.execute("""
        CREATE TABLE db_document_catalogue (
            harvest_ts text NOT NULL,
            gestora_label text NOT NULL,
            gestora_value text,
            cod_db text NOT NULL,
            fund_name text,
            isin varchar(12),
            link_label text NOT NULL,
            href text NOT NULL,
            cod_doc text,
            cod_sus text,
            cod_cont text,
            idioma text,
            PRIMARY KEY (harvest_ts, cod_db, href)
        )
    """)
    pg_conn.execute("""
        INSERT INTO db_document_catalogue
            (harvest_ts, gestora_label, cod_db, link_label, href, isin, cod_sus)
        VALUES
            ('20260101_000000', 'G', 'F1', 'KIID', 'https://x/1', 'DELTA001', 'KIID'),
            ('20260101_000000', 'G', 'F2', 'KIID', 'https://x/2', 'DELTA002', 'KIID'),
            ('20260101_000000', 'G', 'F3', 'LIIC', 'https://x/3', 'DELTA003', 'LIIC')
    """)

    with patch.object(_pks, "KIID_DIR", Path("/nonexistent/kiid/dir/for/test")):
        delta = compute_delta(conn=pg_conn)

    assert delta["target"] == {"DELTA001": "https://x/1", "DELTA002": "https://x/2"}
    assert delta["missing"] == {"DELTA001", "DELTA002"}
    assert delta["local"] == set()


def test_cmd_backfill_lifecycle_postgres_populates_from_disk(
    pg_session_conn, pg_conn_module_schema, tmp_path,
):
    """End-to-end: cmd_backfill_lifecycle() commits internally, so this uses
    pg_conn_module_schema/pg_session_conn. Confirms the DDL guard skip, the ?->%s + ON CONFLICT
    translation (both the active and retired branches), and that KIID_DIR/KIID_RETIRED_BASE are
    scanned correctly via monkeypatched paths."""
    conn = pg_session_conn
    schema = pg_conn_module_schema
    conn.execute(f"SET search_path = {schema}")
    _make_lifecycle_table(conn)
    conn.execute("""
        CREATE TABLE db_document_catalogue (
            harvest_ts text NOT NULL, gestora_label text NOT NULL, gestora_value text,
            cod_db text NOT NULL, fund_name text, isin varchar(12), link_label text NOT NULL,
            href text NOT NULL, cod_doc text, cod_sus text, cod_cont text, idioma text,
            PRIMARY KEY (harvest_ts, cod_db, href)
        )
    """)

    kiid_dir = tmp_path / "kiid"
    kiid_dir.mkdir()
    (kiid_dir / "BF001.pdf").write_bytes(b"%PDF-1.4 fake")

    retired_base = tmp_path / "kiid_retired"
    retired_dir = retired_base / "20260601"
    retired_dir.mkdir(parents=True)
    (retired_dir / "BF002.pdf").write_bytes(b"%PDF-1.4 fake retired")

    with patch.object(_pks, "KIID_DIR", kiid_dir), patch.object(_pks, "KIID_RETIRED_BASE", retired_base):
        cmd_backfill_lifecycle(None, conn=conn)

    active = conn.execute(
        "SELECT isin, status FROM kiid_lifecycle WHERE isin = 'BF001'"
    ).fetchone()
    assert active == ("BF001", "commercializing")

    retired = conn.execute(
        "SELECT isin, status, retire_dir FROM kiid_lifecycle WHERE isin = 'BF002'"
    ).fetchone()
    assert retired == ("BF002", "retired", "20260601")

    # Idempotency: re-running must not insert duplicates (ON CONFLICT DO NOTHING / the
    # "already had a row" skip logic).
    with patch.object(_pks, "KIID_DIR", kiid_dir), patch.object(_pks, "KIID_RETIRED_BASE", retired_base):
        cmd_backfill_lifecycle(None, conn=conn)
    n = conn.execute("SELECT COUNT(*) FROM kiid_lifecycle").fetchone()[0]
    assert n == 2


def test_cmd_backfill_lifecycle_sqlite_own_connection_path(tmp_path):
    """Own-connection SQLite path (conn=None, the CLI's default) — confirms the dialect-aware
    refactor (conn injection, own_conn bookkeeping) made no behavior change here. Zero prior test
    coverage for this function existed before this port."""
    db_path = str(tmp_path / "kiid_sync_test.sqlite")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE db_document_catalogue (
            harvest_ts TEXT NOT NULL, gestora_label TEXT NOT NULL, gestora_value TEXT,
            cod_db TEXT NOT NULL, fund_name TEXT, isin TEXT, link_label TEXT NOT NULL,
            href TEXT NOT NULL, cod_doc TEXT, cod_sus TEXT, cod_cont TEXT, idioma TEXT
        )
    """)
    conn.commit()
    conn.close()

    kiid_dir = tmp_path / "kiid_sqlite"
    kiid_dir.mkdir()
    (kiid_dir / "SQ001.pdf").write_bytes(b"%PDF-1.4 fake")
    retired_base = tmp_path / "kiid_retired_sqlite"

    with patch.object(_pks, "DB_PATH", db_path), \
         patch.object(_pks, "KIID_DIR", kiid_dir), \
         patch.object(_pks, "KIID_RETIRED_BASE", retired_base):
        cmd_backfill_lifecycle(None, backend="sqlite")   # SQLite path on purpose (FND-0102)

    conn2 = sqlite3.connect(db_path)
    row = conn2.execute("SELECT isin, status FROM kiid_lifecycle WHERE isin = 'SQ001'").fetchone()
    conn2.close()
    assert row == ("SQ001", "commercializing")
