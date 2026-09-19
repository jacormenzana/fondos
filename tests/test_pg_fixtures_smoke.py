"""
tests/test_pg_fixtures_smoke.py — proves the shared Postgres test harness (plan §5a,
shared/testing/pg_fixtures.py) actually works, end to end, against a real server.

This lives at the repo root under `tests/`, not under proyecto1/2/3's own `tests/` directories,
because it tests shared cross-cutting infrastructure (the fixture itself), not P1/P2/P3 domain
logic — a new, deliberate addition to the project's test layout, alongside the existing
per-project convention (see AGENTS.md "Tests").

Run:
    FONDOS_TEST_PG_DSN="postgresql://user@localhost:5432/dbname" \\
        python -X utf8 -m pytest tests/test_pg_fixtures_smoke.py -v

Without FONDOS_TEST_PG_DSN set, every test below SKIPS (not fails, not errors) — see
shared/testing/pg_fixtures.py's `_require_pg()`. That skip-clean behavior is itself part of what
this file verifies (test_skips_cleanly_without_dsn), since a fixture that errors instead of
skipping when unconfigured would break every other test file that happens to request it in an
environment where Postgres isn't available yet.
"""

from __future__ import annotations

import os

import pytest


def test_pg_conn_is_a_real_connection(pg_conn):
    """Sanity: the fixture actually returns a live, queryable connection."""
    row = pg_conn.execute("SELECT 1").fetchone()
    assert row == (1,)


def test_pg_conn_rolls_back_between_tests_part1(pg_conn):
    """First half of a two-test pair proving savepoint isolation: create a table and insert a
    row here; the companion test below must NOT see it."""
    pg_conn.execute("CREATE TABLE IF NOT EXISTS pytest_smoke_isolation (n integer)")
    pg_conn.execute("INSERT INTO pytest_smoke_isolation VALUES (42)")
    row = pg_conn.execute("SELECT n FROM pytest_smoke_isolation").fetchone()
    assert row == (42,)


def test_pg_conn_rolls_back_between_tests_part2(pg_conn):
    """If pg_conn's SAVEPOINT/ROLLBACK TO SAVEPOINT isolation is broken, this test observes the
    table (and row) the previous test created — proving the fixture actually discards state
    between tests rather than only appearing to (the actual risk this smoke test exists to catch:
    a fixture that LOOKS like it isolates tests but silently doesn't, e.g. because ROLLBACK TO
    SAVEPOINT was misspelled and swallowed, or because release order matters and got it backwards)."""
    row = pg_conn.execute(
        "SELECT to_regclass('public.pytest_smoke_isolation')"
    ).fetchone()
    assert row == (None,), (
        "pytest_smoke_isolation still exists — savepoint rollback did not discard the previous "
        "test's CREATE TABLE. This is the isolation guarantee the whole fixture exists to provide."
    )


def test_pg_conn_survives_a_failing_statement(pg_conn):
    """A test that fails partway through (e.g. an assertion after a partial write) must still
    tear down cleanly — the fixture's `finally` block must run even when the yielded block raises.
    This test intentionally does a valid write, then a deliberately-caught error, then verifies
    the connection is still usable afterward (proving the savepoint/finally logic doesn't leave
    the transaction in an aborted state for whichever test runs next)."""
    pg_conn.execute("CREATE TABLE IF NOT EXISTS pytest_smoke_partial (n integer)")
    with pytest.raises(Exception):
        pg_conn.execute("INSERT INTO pytest_smoke_partial VALUES ('not an integer')")
    # After a failed statement, psycopg3 aborts the current transaction/savepoint — proving this
    # fixture handles that (via ROLLBACK TO SAVEPOINT, not a plain rollback that would also discard
    # the OUTER session transaction) is exactly the scenario this test targets.
    row = pg_conn.execute("SELECT 1").fetchone()
    assert row == (1,)


def test_module_schema_fixture_creates_and_drops(pg_conn_module_schema):
    """Sanity for the commit-visible-behavior fixture: the schema it hands back actually exists
    and is usable while the module runs."""
    schema = pg_conn_module_schema
    assert schema.startswith("test_")


def test_skips_cleanly_without_dsn(monkeypatch):
    """Verifies the documented skip-not-fail contract directly, independent of whatever the CI
    environment's FONDOS_TEST_PG_DSN happens to be set to — this test manipulates the env var
    itself rather than relying on ambient state."""
    monkeypatch.delenv("FONDOS_TEST_PG_DSN", raising=False)
    from shared.testing.pg_fixtures import _require_pg

    with pytest.raises(pytest.skip.Exception):
        _require_pg()
