# proyecto1/tests/test_p1_db_harvest_report_pg.py
# -*- coding: utf-8 -*-
"""
Postgres migration (addendum, 2026-09-20, plan §Addendum Stage 5) — dedicated regression tests for
harvest.p1_db_harvest.cmd_report_codsus() (Phase 3 codSus discovery report). Had ZERO test
coverage on either dialect before this port — this file covers both.

cmd_report_codsus() is read-only (no conn.commit() anywhere in it), so pg_conn (SAVEPOINT,
autocommit=False) is the correct fixture — matches production's actual connection mode, unlike
pg_conn_module_schema which would break any bare-SAVEPOINT-dependent code the same way it did for
_finalize_data_quality_issues()/log_ingestion() earlier this session (not applicable here since
this function has none, but the fixture choice reasoning is the same: use pg_conn_module_schema
only for functions that themselves commit).

Also regression-tests the connection-leak fix: the original code opened 3 separate sqlite3.connect()
calls (one leaked, never explicitly closed) for what is now a single reused `conn` — verified here
by injecting one connection and confirming every section of the report runs correctly against it.
"""
from __future__ import annotations

import importlib.util
import io
import os
import sqlite3
import sys
from contextlib import redirect_stdout
from unittest.mock import patch

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_HARVEST_DIR = os.path.normpath(os.path.join(_TESTS_DIR, "..", "harvest"))
_P1_DIR = os.path.normpath(os.path.join(_TESTS_DIR, ".."))
if _P1_DIR not in sys.path:
    sys.path.insert(0, _P1_DIR)

_spec = importlib.util.spec_from_file_location(
    "p1_db_harvest_report_pg_test", os.path.join(_HARVEST_DIR, "p1_db_harvest.py")
)
_pdh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_pdh)
cmd_report_codsus = _pdh.cmd_report_codsus


def _make_catalogue_sqlite(conn):
    conn.executescript("""
        CREATE TABLE db_document_catalogue (
            harvest_ts text NOT NULL, gestora_label text, gestora_value text,
            cod_db text, fund_name text, isin text, link_label text, href text NOT NULL,
            cod_doc text, cod_sus text, cod_cont text, idioma text
        );
        CREATE TABLE fund_master (ISIN text PRIMARY KEY, Fund_Name text);
    """)
    rows = [
        ("20260920_100000", "G1", "KIID", "LU0000000001", "D1", "KIID", "C1", "ES"),
        ("20260920_100000", "G1", "LIIC", "LU0000000001", "D2", "LIIC", "C2", "ES"),
        ("20260920_100000", "G1", "KIID", "LU0000000002", "D3", "KIID", "C1", "ES"),
    ]
    for i, (ts, geslbl, sus, isin, doc, lbl, cont, idio) in enumerate(rows):
        conn.execute(
            "INSERT INTO db_document_catalogue "
            "(harvest_ts, gestora_label, gestora_value, cod_db, fund_name, isin, link_label, "
            " href, cod_doc, cod_sus, cod_cont, idioma) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (ts, geslbl, "V1", "F1", "Fund One", isin, lbl, f"href{i}", doc, sus, cont, idio),
        )
    conn.execute("INSERT INTO fund_master VALUES ('LU0000000001', 'Fund One')")
    # LU0000000002 deliberately absent from fund_master -> exercises the reconciliation section
    conn.commit()


def test_report_sqlite_runs_end_to_end(tmp_path):
    """Own-connection SQLite path — confirms the dialect-aware refactor made no behavior change."""
    db_path = str(tmp_path / "report_test.sqlite")
    conn = sqlite3.connect(db_path)
    _make_catalogue_sqlite(conn)
    conn.close()

    buf = io.StringIO()
    with patch.object(_pdh, "DB_PATH", db_path):
        with redirect_stdout(buf):
            cmd_report_codsus(None)
    output = buf.getvalue()
    assert "codSus DISCOVERY REPORT" in output
    assert "Funds (unique ISIN): 2" in output
    assert "In harvest, not in fund_master (new funds): 1" in output


def test_report_postgres_runs_end_to_end_on_one_reused_connection(pg_conn):
    """The connection-leak-fix regression proof: everything (including §6 reconciliation, which
    used to open 2 MORE connections) runs correctly against the single injected `conn`."""
    pg_conn.execute("""
        CREATE TABLE db_document_catalogue (
            harvest_ts text NOT NULL, gestora_label text, gestora_value text,
            cod_db text, fund_name text, isin varchar(12), link_label text, href text NOT NULL,
            cod_doc text, cod_sus text, cod_cont text, idioma text
        )
    """)
    pg_conn.execute("CREATE TABLE fund_master (isin varchar(12) PRIMARY KEY, fund_name text)")

    rows = [
        ("20260920_100000", "KIID", "LU0000000001", "D1", "KIID", "href0"),
        ("20260920_100000", "LIIC", "LU0000000001", "D2", "LIIC", "href1"),
        ("20260920_100000", "KIID", "LU0000000002", "D3", "KIID", "href2"),
    ]
    for ts, sus, isin, doc, lbl, href in rows:
        pg_conn.execute(
            "INSERT INTO db_document_catalogue "
            "(harvest_ts, gestora_label, gestora_value, cod_db, fund_name, isin, link_label, "
            " href, cod_doc, cod_sus, cod_cont, idioma) "
            "VALUES (%s,'G1','V1','F1','Fund One',%s,%s,%s,%s,%s,'C1','ES')",
            (ts, isin, lbl, href, doc, sus),
        )
    pg_conn.execute("INSERT INTO fund_master VALUES ('LU0000000001', 'Fund One')")

    buf = io.StringIO()
    with redirect_stdout(buf):
        cmd_report_codsus(None, conn=pg_conn)
    output = buf.getvalue()

    assert "codSus DISCOVERY REPORT" in output
    assert "Funds (unique ISIN): 2" in output
    assert "In harvest, not in fund_master (new funds): 1" in output
    assert not pg_conn.closed, "an injected connection must never be closed by the function itself"


def test_report_postgres_no_harvest_data_exits_cleanly(pg_conn):
    """The 'no data' early-return path — own_conn=False here too (injected conn), must not close
    the caller's connection even on the early-return branch."""
    pg_conn.execute("""
        CREATE TABLE db_document_catalogue (
            harvest_ts text NOT NULL, gestora_label text, gestora_value text,
            cod_db text, fund_name text, isin varchar(12), link_label text, href text NOT NULL,
            cod_doc text, cod_sus text, cod_cont text, idioma text
        )
    """)
    buf = io.StringIO()
    with redirect_stdout(buf):
        cmd_report_codsus(None, conn=pg_conn)
    assert not pg_conn.closed
