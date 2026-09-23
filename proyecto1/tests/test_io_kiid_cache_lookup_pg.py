# proyecto1/tests/test_io_kiid_cache_lookup_pg.py
# -*- coding: utf-8 -*-
"""
Postgres migration Stage 9 (2026-09-23) — regression tests for the two `?`-placeholder queries in
core/io.py that Stages 0-8 never covered, because every earlier stage tested ported FUNCTIONS
directly and nothing drove run_block.py's real cache lookup against a live Postgres connection.

What went wrong (found only by Stage 9's end-to-end CLI rehearsal):
  - get_kiid_for_isin()'s cache lookup used a bare `?`. On psycopg3 that is a hard syntax error,
    and the surrounding broad `except Exception:` silently turned it into a cache MISS — every fund
    re-read and re-parsed its PDF from disk (~1-3 s instead of ~2-4 ms) and emitted spurious
    extra classifier incidents, with no error anywhere.
  - find_kiid_links_from_db() had the same pattern and would have returned "no links" for every
    lookup.

Both read only (no commit), so the standard SAVEPOINT-based pg_conn fixture is safe here. Each test
builds a minimal scratch table in its own throwaway schema (DDL is transactional in Postgres, so
the savepoint rollback removes it) rather than touching the live-seeded tables.
"""
from __future__ import annotations

import importlib.util
import logging
import os
import sqlite3
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR = os.path.normpath(os.path.join(_TESTS_DIR, "..", "core"))
_P1_DIR = os.path.normpath(os.path.join(_TESTS_DIR, ".."))
for _p in (_P1_DIR, _CORE_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# core/io.py's module name collides with the stdlib `io` (already in sys.modules) — load by path.
_spec = importlib.util.spec_from_file_location("core_io_kiid_cache_test", os.path.join(_CORE_DIR, "io.py"))
_core_io = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_core_io)
get_kiid_for_isin = _core_io.get_kiid_for_isin
find_kiid_links_from_db = _core_io.find_kiid_links_from_db


def test_pg_cache_lookup_hits_instead_of_silently_missing(pg_conn):
    """The core regression: with a cached, non-NULL Raw_KIID_Text present, a Postgres connection
    must be served from the cache (KIID_Source == 'CACHE'), not fall through to a file re-parse."""
    pg_conn.execute("CREATE SCHEMA io_cache_t")
    pg_conn.execute("SET search_path = io_cache_t")
    pg_conn.execute(
        "CREATE TABLE fund_kiid_metadata (isin text, kiid_class integer, raw_kiid_text text, "
        "kiid_pdf_hash text, kiid_url text, kiid_downloaded_at text, dla2_table_text text, "
        "kiid_status text)"
    )
    pg_conn.execute(
        "INSERT INTO fund_kiid_metadata VALUES "
        "('LU0000000001', 1, 'texto cacheado', 'h1', 'http://x', '2026-01-01T00:00:00', NULL, 'OK')"
    )

    text, meta = get_kiid_for_isin("LU0000000001", None, pg_conn)

    assert text == "texto cacheado"
    assert meta["KIID_Status"] == "CACHED"
    assert meta["KIID_Source"] == "CACHE"
    assert meta["KIID_PDF_Hash"] == "h1"


def test_pg_find_kiid_links_from_db_returns_hrefs_of_the_latest_harvest(pg_conn):
    pg_conn.execute("CREATE SCHEMA io_links_t")
    pg_conn.execute("SET search_path = io_links_t")
    pg_conn.execute(
        "CREATE TABLE db_document_catalogue (isin text, cod_sus text, href text, harvest_ts text)"
    )
    pg_conn.execute(
        "INSERT INTO db_document_catalogue VALUES "
        "('LU0000000001', 'KIID', 'http://new-kiid', '2026-09-01'),"
        "('LU0000000001', 'KIID', 'http://old-kiid', '2026-08-01'),"   # superseded harvest
        "('LU0000000001', 'PROSP', 'http://prospectus', '2026-09-01'),"  # not a KIID
        "('LU0000000002', 'KIID', 'http://other-fund', '2026-09-01')"
    )

    assert find_kiid_links_from_db("LU0000000001", pg_conn) == ["http://new-kiid"]


def test_sqlite_lookup_failure_is_logged_not_silent(caplog):
    """A failing cache lookup must still degrade to a cache miss (the fallback path is
    intentional), but never silently. A SQLite connection with no fund_kiid_metadata table makes
    the query raise; both swallowed-exception sites must now emit a WARNING."""
    conn = sqlite3.connect(":memory:")   # deliberately no tables at all
    with caplog.at_level(logging.WARNING):
        links = find_kiid_links_from_db("LU0000000001", conn)
        text, meta = get_kiid_for_isin("LU0000000001", None, conn, kiid_source="remote")

    assert links == []                                   # degraded, as before
    assert text is None and meta["KIID_Status"] != "CACHED"
    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "[KIID-LINKS-DB-FAIL]" in messages
    assert "[KIID-CACHE-LOOKUP-FAIL]" in messages
