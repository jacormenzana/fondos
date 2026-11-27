"""FND-0400 (the cascade subclass): a failing Postgres-backed test must not break the tests after it in the same pytest session.

Mechanism (found 2026-11-28): `pg_conn` nests each test in a SAVEPOINT of ONE shared session connection. When the code under test commits or rolls back that
connection, the savepoint is gone; a later error leaves the connection in an ABORTED transaction, the teardown's ROLLBACK TO SAVEPOINT fails, and every
following test failed at its first statement (the mutation evidence of FND-0385: 10 and 6 victims that pass alone). The fix (shared/testing/pg_fixtures.py):
`close_savepoint` rolls the whole connection back and reports the culprit test, and an autouse hygiene step heals every shared connection after each test.

Run: python scripts/gov/delivery/run_pg_tests.py -- tests/test_pg_cascade_isolation_fnd0400_pg.py
"""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from shared.testing import pg_fixtures as pf

ROOT = Path(__file__).resolve().parents[1]


def test_close_savepoint_reports_a_lost_savepoint_and_leaves_the_connection_usable(pg_session_conn):
    conn = pg_session_conn
    pf.heal_session_conn(conn)
    conn.execute("SAVEPOINT fnd0400_sp")
    conn.commit()                                                     # the code under test commits: the savepoint is gone
    with pytest.raises(Exception):
        conn.execute("SELECT 1 / 0")                                  # ... and then fails: the transaction is aborted
    with pytest.raises(RuntimeError, match="could not be rolled back"):
        pf.close_savepoint(conn, "fnd0400_sp")
    assert conn.execute("SELECT 41 + 1").fetchone()[0] == 42          # usable again


def test_heal_rolls_back_an_aborted_transaction_and_leaves_a_clean_one_alone(pg_session_conn):
    conn = pg_session_conn
    pf.heal_session_conn(conn)
    conn.execute("SELECT 1")
    assert pf.heal_session_conn(conn) is None                         # INTRANS, clean: untouched
    with pytest.raises(Exception):
        conn.execute("SELECT 1 / 0")
    assert "rolled back" in (pf.heal_session_conn(conn) or "")
    assert conn.execute("SELECT 1").fetchone()[0] == 1


_SESSION = textwrap.dedent('''
    def test_a_breaks_isolation_and_fails(pg_conn):
        pg_conn.commit()                       # production code that commits the shared connection
        pg_conn.execute("SELECT 1 / 0")

    def test_b_after_it_still_passes(pg_conn):
        assert pg_conn.execute("SELECT 1").fetchone()[0] == 1

    def test_c_direct_session_use_fails(pg_session_conn):
        pg_session_conn.execute("SELECT 1 / 0")

    def test_d_after_it_still_passes(pg_session_conn):
        assert pg_session_conn.execute("SELECT 2").fetchone()[0] == 2

    def test_e_and_through_pg_conn_too(pg_conn):
        assert pg_conn.execute("SELECT 3").fetchone()[0] == 3
''')


def test_one_failing_test_does_not_cascade_through_the_session(tmp_path):
    """The ticket's closing condition: a session in which Postgres-backed tests fail, and the next ones still pass."""
    if not os.environ.get(pf.ENV_VAR):
        pytest.skip(f"{pf.ENV_VAR} not set")
    (tmp_path / "test_session_fnd0400.py").write_text(_SESSION, encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    proc = subprocess.run([sys.executable, "-m", "pytest", "-p", "shared.testing.pg_fixtures", "-p", "no:cacheprovider", "-q", "-rA",
                           str(tmp_path / "test_session_fnd0400.py")], cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=300)
    out = proc.stdout + proc.stderr
    for passed in ("test_b_after_it_still_passes", "test_d_after_it_still_passes", "test_e_and_through_pg_conn_too"):
        assert f"PASSED test_session_fnd0400.py::{passed}" in out, out[-3000:]
    assert "FAILED test_session_fnd0400.py::test_a_breaks_isolation_and_fails" in out, out[-3000:]
    assert "FAILED test_session_fnd0400.py::test_c_direct_session_use_fails" in out, out[-3000:]
    assert "could not be rolled back" in out                          # the culprit is named at its own teardown
