"""
tests/test_db_fail_soft_block_pg.py — dedicated regression tests for
shared.db.fail_soft_block(), added during Postgres migration Phase 5c (2026-09-20) while porting
core.pipeline.run_block()'s post-cycle tail (summary/sweep logic).

Lives at the repo root under tests/, not proyecto1/tests/, because it targets shared
cross-cutting infrastructure (shared/db.py), not P1-specific domain logic — same rationale as
tests/test_pg_fixtures_smoke.py.

Why this is tested at the primitive level rather than via core.pipeline.run_block() itself:
run_block() is a ~2800-line function requiring a fully populated fund_master, a live block module,
and (for non-cached funds) HTTP access — invoking it end-to-end is disproportionate for verifying
a small, mechanical tail-section edit. pipeline.py's actual usage is a thin, directly-inspectable
application of this primitive (3 call sites, each `try: with fail_soft_block(conn): ...; except
Exception as e: print(...)`); what genuinely needs live-Postgres proof is the primitive's own
contract, which is what this file verifies. This mirrors how execute_fail_soft() itself was proven
via the functions that call it, not a redundant reimplementation of pipeline.py's control flow.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.db import fail_soft_block


def test_fail_soft_block_releases_savepoint_on_success(pg_conn):
    """The common case: the block runs clean, the SAVEPOINT is released (not left dangling), and
    the block's own effects are visible afterward within the same (savepoint-wrapped-by-pg_conn)
    transaction."""
    pg_conn.execute("CREATE TABLE fsb_success_t (n integer)")
    with fail_soft_block(pg_conn):
        pg_conn.execute("INSERT INTO fsb_success_t VALUES (1)")
    row = pg_conn.execute("SELECT n FROM fsb_success_t").fetchone()
    assert row == (1,)
    # If the SAVEPOINT were left un-RELEASEd, a plain follow-up statement still works fine in
    # Postgres (dangling savepoints don't block further statements) — the real proof RELEASE ran
    # is that a *second* fail_soft_block on the same connection doesn't collide on the sp name.
    with fail_soft_block(pg_conn):
        pg_conn.execute("INSERT INTO fsb_success_t VALUES (2)")
    rows = {r[0] for r in pg_conn.execute("SELECT n FROM fsb_success_t").fetchall()}
    assert rows == {1, 2}


def test_fail_soft_block_reraises_and_does_not_poison_subsequent_statements(pg_conn):
    """The failure case pipeline.py's tail section depends on: the block's exception propagates
    (so the caller's own `except Exception as e: print(...)` still runs and sees it — this
    primitive does NOT swallow, unlike execute_fail_soft), AND — the actual point of the
    SAVEPOINT wrap — a statement run AFTER the caught exception, in the same transaction, must
    still succeed. Without the wrap, Postgres would have aborted the whole transaction and this
    follow-up statement would raise InFailedSqlTransaction."""
    pg_conn.execute("CREATE TABLE fsb_fail_t (n integer)")

    caught = None
    try:
        with fail_soft_block(pg_conn):
            pg_conn.execute("SELECT * FROM this_table_does_not_exist_fsb_test")
    except Exception as e:
        caught = e
    assert caught is not None, "fail_soft_block must re-raise, not swallow"

    # This is the statement that would fail with InFailedSqlTransaction if the transaction had
    # been left poisoned — proving the ROLLBACK TO SAVEPOINT actually ran.
    pg_conn.execute("INSERT INTO fsb_fail_t VALUES (99)")
    row = pg_conn.execute("SELECT n FROM fsb_fail_t").fetchone()
    assert row == (99,)


def test_fail_soft_block_commit_after_block_exits_succeeds(pg_session_conn, pg_conn_module_schema):
    """Confirms the documented usage rule (commit OUTSIDE the `with`, never inside) works
    end-to-end: a successful block followed by conn.commit() after it exits must not raise —
    this is the exact shape of pipeline.py's DQ-sweep tail (DELETE inside the block, commit after).
    Uses pg_conn_module_schema/pg_session_conn (not bare pg_conn) because this test's whole point
    is to call conn.commit() for real, not have it rolled back by the outer fixture."""
    conn = pg_session_conn
    schema = pg_conn_module_schema
    conn.execute(f"SET search_path = {schema}")
    conn.execute("CREATE TABLE fsb_commit_t (n integer)")

    with fail_soft_block(conn):
        conn.execute("INSERT INTO fsb_commit_t VALUES (7)")
    conn.commit()

    row = conn.execute("SELECT n FROM fsb_commit_t").fetchone()
    assert row == (7,)

    # And the connection must still be perfectly usable afterward (no leftover savepoint state).
    conn.execute("INSERT INTO fsb_commit_t VALUES (8)")
    conn.commit()
    rows = {r[0] for r in conn.execute("SELECT n FROM fsb_commit_t").fetchall()}
    assert rows == {7, 8}


def test_fail_soft_block_is_passthrough_under_autocommit(pg_session_conn, pg_conn_module_schema):
    """pg_conn_module_schema runs in autocommit mode — fail_soft_block must not attempt a
    SAVEPOINT there (it would raise NoActiveSqlTransaction, the same gap found and fixed in
    execute_fail_soft during fund_family_builder.py's own PG test). Confirms the no-op passthrough
    branch: success works, and a failure still propagates normally (nothing to protect against —
    each autocommit statement is already its own implicit transaction)."""
    conn = pg_session_conn
    schema = pg_conn_module_schema
    conn.execute(f"SET search_path = {schema}")
    conn.execute("CREATE TABLE fsb_autocommit_t (n integer)")

    with fail_soft_block(conn):
        conn.execute("INSERT INTO fsb_autocommit_t VALUES (5)")
    row = conn.execute("SELECT n FROM fsb_autocommit_t").fetchone()
    assert row == (5,)

    caught = None
    try:
        with fail_soft_block(conn):
            conn.execute("SELECT * FROM this_table_does_not_exist_fsb_autocommit_test")
    except Exception as e:
        caught = e
    assert caught is not None

    # Still usable afterward — in autocommit mode each statement is its own implicit transaction,
    # so there was never anything to poison.
    conn.execute("INSERT INTO fsb_autocommit_t VALUES (6)")
    rows = {r[0] for r in conn.execute("SELECT n FROM fsb_autocommit_t").fetchall()}
    assert rows == {5, 6}
