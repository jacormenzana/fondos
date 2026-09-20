"""
pg_fixtures.py — Postgres test harness (plan §5a "Test harness stand-up").

**Read this before rewriting any `:memory:` SQLite test onto this fixture.** The plan's own
description of that work as "additive, not a redesign" is only true for tests that never exercise
dialect-specific SQL. In practice, EVERY `:memory:` test file audited so far (see the grep in the
migration plan and memory/project_pg_fixture_5a_20260918.md) calls a real production function from
`sqlite_writer.py` / `metrics_writer.py` / etc. that itself issues `?`-placeholder, SQLite-flavored
SQL (`INSERT OR IGNORE`, `INSERT OR REPLACE`). **Rewriting such a test onto this fixture without
first porting the function it calls will not pass** — it isn't a fixture problem, the SQL under
test is the wrong dialect. Port function and test together, in the same change, per the plan's
§5c discipline ("written before the site is touched, so the rewrite is red→green per site") — that
discipline generalizes to essentially the whole existing suite, not just the 3 named upsert
invariants the plan called out by name.

What THIS module provides, independent of any of that porting work:
    - `pg_conn` (function-scoped): a real Postgres connection wrapped in a SAVEPOINT that always
      rolls back on teardown. Fast (microseconds, not a schema create/drop), and this is the
      DEFAULT fixture — use it for any test whose setup is "create some tables, write some rows,
      assert, forget it happened."
    - `pg_conn_module_schema` (module-scoped): a real, isolated `CREATE SCHEMA` per test module,
      dropped at module teardown. Use this ONLY for the small subset of tests that need
      commit-visible or autocommit-required behavior that cannot run inside a savepoint that
      always rolls back — `ON CONFLICT` interaction ACROSS separate transactions,
      `CREATE INDEX CONCURRENTLY`, `REFRESH MATERIALIZED VIEW CONCURRENTLY`. Identify this subset
      explicitly; expect single-digit modules, not a large fraction of the suite (plan §5a).

Connection target:
    - CI (GitHub Actions): a `services: postgres:` container — see
      `.github/workflows/pg-tests.yml`. One shared instance per job, not one per test or worker.
    - Local dev: point `FONDOS_TEST_PG_DSN` at any local Postgres 15+ instance (a throwaway Docker
      container is fine: `docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=test postgres:17`).
    - Neither exists inside this Windows dev session as of 2026-09-18 — this module is written and
      import-checked, but has NOT been run against a live server. Treat as reviewed-but-unproven
      until it runs once in CI or on a real local Postgres.

If `FONDOS_TEST_PG_DSN` is unset, every fixture here `pytest.skip()`s rather than failing — so the
existing SQLite-only suite keeps running untouched until a real Postgres target is configured.
"""

from __future__ import annotations

import os
import uuid
from typing import Iterator

import pytest

try:
    import psycopg
except ImportError:  # pragma: no cover — psycopg3 not installed; every fixture skips cleanly
    psycopg = None  # type: ignore[assignment]

ENV_VAR = "FONDOS_TEST_PG_DSN"


def _dsn() -> str | None:
    return os.environ.get(ENV_VAR)


def _require_pg() -> str:
    dsn = _dsn()
    if psycopg is None:
        pytest.skip("psycopg3 not installed — pip install 'psycopg[binary]'")
    if not dsn:
        pytest.skip(f"{ENV_VAR} not set — point it at a Postgres 15+ instance to run PG-backed tests")
    return dsn


@pytest.fixture(scope="session")
def pg_session_conn() -> Iterator["psycopg.Connection"]:
    """One connection per test session — never used directly by test functions (see `pg_conn`
    below); this is what the per-test savepoint fixture nests inside."""
    dsn = _require_pg()
    conn = psycopg.connect(dsn, autocommit=False)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture()
def pg_conn(pg_session_conn: "psycopg.Connection") -> Iterator["psycopg.Connection"]:
    """DEFAULT fixture for PG-backed tests. Wraps the test in an explicit SAVEPOINT that is always
    rolled back at teardown — costs microseconds, not a schema create/drop, and keeps a large
    suite fast against one shared session connection (plan §5a "CI execution times"). The test is
    free to CREATE TABLE / INSERT / etc. as if it owned a fresh database; none of it survives
    teardown, whether the test passes, fails, or raises.

    Implementation note: this uses plain `SAVEPOINT`/`ROLLBACK TO SAVEPOINT` SQL rather than
    psycopg3's `conn.transaction()` context manager, because that context manager COMMITS
    (releases) the savepoint on a clean exit and only rolls back on an exception — the opposite of
    what a test fixture needs (always discard, regardless of outcome). `pg_session_conn` is opened
    with `autocommit=False`, so psycopg3 implicitly begins the enclosing transaction on the first
    statement below; no explicit `BEGIN` is needed.

    Known limitation, stated plainly rather than hidden: if the code under test itself calls
    `conn.commit()` or `conn.rollback()` (plausible for code ported from SQLite, where each test
    previously owned a full private connection), that commits/rolls back the OUTER session
    transaction too, not just this savepoint — breaking isolation between tests sharing
    `pg_session_conn`. If a ported test needs to exercise commit/rollback behavior itself, it
    belongs on `pg_conn_module_schema` instead, not here.
    """
    conn = pg_session_conn
    savepoint = f"pytest_sp_{uuid.uuid4().hex[:8]}"
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        yield conn
    finally:
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")


@pytest.fixture()
def pg_conn_module_schema(pg_session_conn: "psycopg.Connection", request: pytest.FixtureRequest) -> Iterator[str]:
    """For the small subset of tests needing commit-visible/autocommit-required behavior that
    cannot run inside a savepoint-that-always-rolls-back (§module docstring). Creates one schema
    per test MODULE (not per test — the cost is paid once per file), named uniquely so parallel
    test runs (pytest-xdist) never collide, and drops it at module teardown.

    Returns the schema name (a string) — the test is responsible for `CREATE TABLE <schema>.foo`
    or issuing `SET search_path` itself; this fixture only owns the schema's lifecycle.

    Found live 2026-09-20 (first real use of this fixture — previously "reviewed but unproven"):
    psycopg3 refuses to toggle `autocommit` while the connection is mid-transaction
    (ProgrammingError: "can't change 'autocommit' now: connection in transaction status INTRANS").
    A `pg_conn`-based test that ran earlier in the same session leaves `pg_session_conn` in exactly
    that state — `ROLLBACK TO SAVEPOINT` + `RELEASE SAVEPOINT` clears the savepoint but does not
    end the outer transaction psycopg3 opened implicitly on that connection's first statement. The
    `conn.rollback()` below closes that (by then necessarily empty) outer transaction cleanly
    before flipping to autocommit — safe regardless of whether a prior test ran, and root-causing
    the actual state-precondition this fixture needs rather than relying on test execution order."""
    conn = pg_session_conn
    schema = f"test_{request.module.__name__.rsplit('.', 1)[-1]}_{uuid.uuid4().hex[:8]}"
    conn.rollback()
    conn.autocommit = True
    try:
        conn.execute(f"CREATE SCHEMA {schema}")
        yield schema
    finally:
        # Found live 2026-09-20 (fund_family_builder.py's PG tests, first module to run
        # alongside other PG-backed test files in one session): a test typically does
        # `conn.execute(f"SET search_path = {schema}")` to use this schema unqualified — that
        # SETs it on `pg_session_conn` itself, which is SESSION-scoped and shared across every
        # test module. Left unreset, the next module's tests (even ones using the unrelated
        # `pg_conn` SAVEPOINT fixture) inherit a search_path pointing at a schema this fixture
        # just dropped, failing with "no schema has been selected to create in". Resetting here
        # closes the leak at its owning fixture rather than requiring every module-schema test
        # to remember to reset it.
        conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        conn.execute("SET search_path = DEFAULT")
        conn.autocommit = False
