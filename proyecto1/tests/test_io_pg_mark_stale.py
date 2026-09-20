# proyecto1/tests/test_io_pg_mark_stale.py
# -*- coding: utf-8 -*-
"""
Postgres migration Phase 5c (2026-09-20) — dedicated regression test for
core.io.mark_stale_for_refresh(), the write function behind mark_stale.py.

Deliberately uses pg_conn_module_schema (isolated, disposable schema), NOT the standard pg_conn
SAVEPOINT fixture: mark_stale_for_refresh() calls conn.commit() internally. A commit ends the
enclosing transaction, which destroys any SAVEPOINT taken before it — running this function against
the pg_conn fixture's SAVEPOINT-wrapped live-seeded fund_kiid_metadata would commit real changes
permanently, bypassing the fixture's rollback entirely.

This is not a hypothetical concern: it happened once, live, while first verifying this exact
function manually (see project memory for 2026-09-20) — 5 real fund_kiid_metadata rows were
permanently flipped to FORCE_REFRESH before the mistake was caught, identified via SQLite as an
unaffected ground truth, and reverted. This test file exists specifically so that mistake is never
repeated for this function.
"""
from __future__ import annotations

import importlib.util
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR = os.path.normpath(os.path.join(_TESTS_DIR, "..", "core"))
_P1_DIR = os.path.normpath(os.path.join(_TESTS_DIR, ".."))
if _P1_DIR not in sys.path:
    sys.path.insert(0, _P1_DIR)

# core/io.py's own module name ('io') collides with the Python stdlib module of the same name,
# which is virtually guaranteed to already be cached in sys.modules by the time this test file
# imports anything (pytest itself imports stdlib io early) — a plain `from io import ...` silently
# resolves to the WRONG module (confirmed live: "cannot import name 'mark_stale_for_refresh' from
# 'io' (...\Lib\io.py)"). Loading by explicit file path sidesteps the name collision entirely.
_spec = importlib.util.spec_from_file_location(
    "core_io_pg_test", os.path.join(_CORE_DIR, "io.py")
)
_core_io = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_core_io)
mark_stale_for_refresh = _core_io.mark_stale_for_refresh


def test_mark_stale_for_refresh_picks_oldest_first_respects_limit_and_nulls_first(
    pg_session_conn, pg_conn_module_schema,
):
    """Confirms: (1) only KIID_Status IN ('OK','CACHED') rows are eligible — an already
    FORCE_REFRESH row is left alone; (2) NULLS FIRST ordering treats a NULL
    kiid_downloaded_at as the oldest possible value, tying for selection with a genuinely old
    date; (3) max_funds correctly caps how many rows get flipped, leaving newer-dated rows
    untouched."""
    conn = pg_session_conn
    schema = pg_conn_module_schema
    conn.execute(f"SET search_path = {schema}")
    conn.execute("""
        CREATE TABLE fund_kiid_metadata (
            isin TEXT PRIMARY KEY,
            kiid_class SMALLINT,
            kiid_status TEXT,
            kiid_downloaded_at TIMESTAMPTZ
        )
    """)
    conn.execute("""
        INSERT INTO fund_kiid_metadata (isin, kiid_class, kiid_status, kiid_downloaded_at) VALUES
        ('T1', 1, 'OK', '2020-01-01'),
        ('T2', 1, 'OK', '2024-01-01'),
        ('T3', 1, 'CACHED', NULL),
        ('T4', 1, 'FORCE_REFRESH', '2020-01-01'),
        ('T5', 1, 'OK', '2023-01-01')
    """)

    n = mark_stale_for_refresh(conn, max_age_days=0, max_funds=2)

    rows = dict(conn.execute("SELECT isin, kiid_status FROM fund_kiid_metadata").fetchall())
    assert n == 2, f"expected exactly 2 rows marked (max_funds=2), got {n}"
    assert rows["T1"] == "FORCE_REFRESH", "oldest dated OK row must be selected"
    assert rows["T3"] == "FORCE_REFRESH", "NULL kiid_downloaded_at must sort as oldest (NULLS FIRST)"
    assert rows["T2"] == "OK", "newer-dated row must be left untouched (max_funds cap)"
    assert rows["T5"] == "OK", "newer-dated row must be left untouched (max_funds cap)"
    assert rows["T4"] == "FORCE_REFRESH", "already-FORCE_REFRESH row must be unaffected (not re-selected)"
