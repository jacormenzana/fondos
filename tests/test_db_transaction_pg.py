"""
tests/test_db_transaction_pg.py — dedicated regression tests for shared.db.db_transaction(), added
during the Postgres migration's "database role switch" addendum (2026-09-20, plan §Addendum Stage
3) while porting core.pipeline.py's WRONG_DOC/FORCE_REFRESH `with conn:` blocks.

Lives at the repo root under tests/, not proyecto1/tests/, for the same reason
test_db_fail_soft_block_pg.py does — this targets shared cross-cutting infrastructure
(shared/db.py), not P1-specific domain logic.

Why this exists: `with conn:` on a psycopg3 Connection COMMITS *and then CLOSES* the connection on
a clean exit — verified live 2026-09-20, `conn.closed is True` immediately after, even on success.
sqlite3's `with conn:` only manages the transaction and never closes. Naively porting `with conn:`
unchanged inside a function called repeatedly on one long-lived pipeline connection (the normal
shape in this codebase) would close the connection after the FIRST call and break every subsequent
one in the same run — first found while porting sqlite_writer.py::publish_fund() (fixed inline
there), then centralized here as db_transaction() once pipeline.py needed the identical fix at 3
more sites (P#11/DRY). These tests prove the primitive's own contract directly, the same way
fail_soft_block()'s tests do, rather than only proving it indirectly through pipeline.py's much
larger, HTTP/PDF-dependent run_block().
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.db import db_transaction, is_postgres_connection


def test_connection_stays_open_and_usable_after_a_successful_block(pg_conn):
    """The actual bug this primitive fixes: a bare `with conn:` closes a psycopg3 connection on a
    clean exit. `with db_transaction(conn):` must not — the connection must still work for a
    second block afterward, exactly the shape pipeline.py needs (called once per fund, thousands
    of times, on one long-lived connection)."""
    pg_conn.execute("CREATE TABLE dbtxn_open_t (n integer)")

    with db_transaction(pg_conn):
        pg_conn.execute("INSERT INTO dbtxn_open_t VALUES (1)")

    assert not pg_conn.closed, "db_transaction() must never close the connection"

    # Prove it, not just assert the flag: run a second block on the same connection.
    with db_transaction(pg_conn):
        pg_conn.execute("INSERT INTO dbtxn_open_t VALUES (2)")

    rows = {r[0] for r in pg_conn.execute("SELECT n FROM dbtxn_open_t").fetchall()}
    assert rows == {1, 2}


def test_commits_on_success(pg_session_conn, pg_conn_module_schema):
    """Uses pg_conn_module_schema/pg_session_conn (not bare pg_conn) specifically to observe a
    REAL commit — pg_conn's own SAVEPOINT would otherwise mask whether db_transaction() actually
    committed or the outer fixture's rollback just never got a chance to undo it."""
    conn = pg_session_conn
    conn.execute(f"SET search_path = {pg_conn_module_schema}")
    conn.execute("CREATE TABLE dbtxn_commit_t (n integer)")
    conn.commit()

    with db_transaction(conn):
        conn.execute("INSERT INTO dbtxn_commit_t VALUES (42)")
    # No explicit conn.commit() here — db_transaction() itself must have committed.

    row = conn.execute("SELECT n FROM dbtxn_commit_t").fetchone()
    assert row == (42,)
    assert not conn.closed


def test_rolls_back_on_exception_and_connection_survives(pg_conn):
    """Mirrors sqlite3's `with conn:` exception semantics (rollback, re-raise) — the second half
    of the contract this primitive must preserve, not just the non-closing behavior."""
    pg_conn.execute("CREATE TABLE dbtxn_fail_t (n integer)")

    caught = None
    try:
        with db_transaction(pg_conn):
            pg_conn.execute("INSERT INTO dbtxn_fail_t VALUES (99)")
            raise ValueError("boom")
    except ValueError as e:
        caught = e
    assert caught is not None, "db_transaction() must re-raise, not swallow"
    assert not pg_conn.closed, "the connection must survive a rolled-back block"

    rows = pg_conn.execute("SELECT n FROM dbtxn_fail_t").fetchall()
    assert rows == [], "the failed insert must have been rolled back"

    # And the connection must still be fully usable for further statements — the actual
    # production shape (pipeline.py continues processing the next fund after a WRONG_DOC update).
    pg_conn.execute("INSERT INTO dbtxn_fail_t VALUES (100)")
    row = pg_conn.execute("SELECT n FROM dbtxn_fail_t").fetchone()
    assert row == (100,)


def test_is_a_passthrough_on_sqlite():
    """On SQLite, db_transaction(conn) must return conn itself unchanged (no psycopg-specific
    wrapping) — confirms the dialect branch, not just the Postgres side. No PG connection needed."""
    import sqlite3
    conn = sqlite3.connect(":memory:")
    assert not is_postgres_connection(conn)
    assert db_transaction(conn) is conn
    conn.close()
