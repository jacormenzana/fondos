"""
tests/test_sql_explain_sweep_pg.py — every SQL statement production code executes is checked by
Postgres itself, plus three static guards over the same statement inventory.

Background (2026-09-26): the first post-cutover P1_P2_Complete.bat aborted because
`run_gap_analysis()` used a non-aggregated column with GROUP BY, which SQLite tolerates and Postgres
rejects (GroupingError). The statement sat in a diagnostic tail that only runs after a real load,
so no rehearsal ever executed it. No amount of regex can decide whether SQL is valid; the Postgres
parser can, and `EXPLAIN` asks it without executing anything.

What is enforced here (statements come from tests/_sql_sites.py, an AST extraction):
  1. EXPLAIN sweep (needs FONDOS_TEST_PG_DSN, i.e. `python scripts/ops/run_pg_tests.py`): every
     statement that resolves statically and is not a SQLite branch is EXPLAINed against the real
     db/pg DDL. GroupingError, unknown function/column/table and syntax errors all fail here,
     whether or not the code path is ever reached in a rehearsal.
  2. UNRESOLVED statements (dynamic column lists etc.) cannot be EXPLAINed, so they are never
     counted as verified: the set is pinned as an exact baseline. A new dynamic statement fails
     until it is added with a reason, and a baseline entry whose statement disappeared fails too.
  3. SQLite-only syntax (PRAGMA, INSERT OR REPLACE, datetime('now'), julianday ...) must live in a
     function that forks on the dialect.
  4. Every INSERT on the Postgres path must be idempotent (ON CONFLICT ...) unless its table is an
     append-only log. P1_P2_Complete.bat's `--from N` re-runs a step in full, which is only safe if
     re-running a write cannot duplicate rows.

Allowlist protocol (applies to every dict below): each entry is `{key: reason}` and the reason must
cite a backlog ticket (`FND-0123 ...`) or start with `DESIGN:` followed by a real explanation
(>= 20 characters). Additions show up in the commit diff; the evidence for an exemption belongs in
the commit message and should be flagged for the operator's review. A stale key (its target no
longer exists) fails the suite, so the lists cannot rot.
"""
from __future__ import annotations

import os
import re
from collections import Counter

import pytest

import _sql_sites as S
from _allowlist import bad_reasons, check_reason

# ─── allowlists ({key: reason}) ──────────────────────────────────────────────────────────────────

# file -> reason. Statements in these files target another database, or are SQLite-only tools.
_FILE_EXEMPT: dict[str, str] = {
    "shared/backlog_client.py":
        "DESIGN: talks to the separate `gestion` database (backlog schema), not the fondos schema this sweep loads",
    "shared/load_fondos_to_postgres.py":
        "DESIGN: legacy SQLite->Postgres BI mirror (P4); SQLite is its source and it writes its own analytics DB",
    "shared/init_db.py":
        "DESIGN: creates the SQLite schema; the Postgres schema comes from db/pg/*.sql",
    "shared/testing/pg_fixtures.py":
        "DESIGN: test harness; issues SAVEPOINT/CREATE SCHEMA against a throwaway database",
    "shared/migrate_schema_v26.py":
        "DESIGN: one-shot SQLite schema migration, run before the Postgres cutover",
    "shared/migrate_schema_v27.py":
        "DESIGN: one-shot SQLite schema migration mirroring the Postgres DDL into SQLite",
}

R_FRAGMENT = ("DESIGN: a fragment (column list, IN-list of placeholders, expression) is assembled at runtime "
              "from code-defined names, never from input; the static remainder is still EXPLAINed with the "
              "fragment replaced by NULL (test_partially_resolved_statements_...)")
R_OPAQUE = ("DESIGN: the SQL text is built or passed in at runtime and is not a literal at this site; "
            "it cannot be extracted statically, so it relies on the runtime tests of its module")

# site_id -> reason. Statements that cannot be resolved statically (dynamic column lists, ...).
# Exact-set baseline: a NEW unresolved statement fails until it is added here.
_UNVERIFIED_BASELINE: dict[str, str] = {
    "proyecto1/core/_db_utils.py::_load_all_from_bd::7ef5c142":
        R_FRAGMENT,
    "proyecto1/core/fund_family_builder.py::correct_family_inconsistencies::a713ae01":
        R_FRAGMENT,
    "proyecto1/core/normalize_db_casing_v20.py::run::0e2c4a30":
        R_FRAGMENT,
    "proyecto1/core/normalize_db_casing_v20.py::run::c887964a":
        R_FRAGMENT,
    "proyecto1/core/pipeline.py::run_block::f3aba990":
        R_FRAGMENT,
    "proyecto1/core/sqlite_writer.py::reconcile_universe_membership::c91915df":
        R_FRAGMENT,
    "proyecto1/core/sqlite_writer.py::upsert_fund_master::df967a38":
        R_FRAGMENT,
    "proyecto1/core/sqlite_writer.py::upsert_kiid_metadata::29696e01":
        R_OPAQUE,
    "proyecto2/src/analysis/export_metrics.py::q_candidatos::044141c8":
        R_FRAGMENT,
    "proyecto2/src/analysis/export_metrics.py::q_consistencia::73b0a055":
        R_FRAGMENT,
    "proyecto2/src/analysis/export_metrics.py::q_crisis::08666780":
        R_FRAGMENT,
    "proyecto2/src/analysis/export_metrics.py::q_divisa::d93212d2":
        R_FRAGMENT,
    "proyecto2/src/analysis/export_metrics.py::q_macro_betas::dc13db71":
        R_FRAGMENT,
    "proyecto2/src/analysis/export_metrics.py::q_persistencia::96742e43":
        R_FRAGMENT,
    "proyecto2/src/analysis/export_metrics.py::q_regime_returns::3a93f429":
        R_FRAGMENT,
    "proyecto2/src/analysis/export_metrics.py::q_rentabilidad_dist::29696e01":
        R_OPAQUE,
    "proyecto2/src/analysis/export_metrics.py::q_ret_dd_ratio::bff6a855":
        R_FRAGMENT,
    "proyecto2/src/analysis/export_metrics.py::q_tendencia::8df415c8":
        R_FRAGMENT,
    "proyecto2/src/analysis/export_metrics.py::q_top_rentabilidad::13e0c807":
        R_FRAGMENT,
    "proyecto2/src/discovery/nav_discovery.py::_auto_freeze_stale_navs::4fb3d895":
        R_FRAGMENT,
    "proyecto2/src/discovery/nav_discovery.py::_splice_new_chart_batch::11f709ed":
        R_FRAGMENT,
    "proyecto2/src/readers/db_readers.py::load_fund_attributes::a1eda773":
        R_FRAGMENT,
    "proyecto2/src/reports/rolling_dashboard.py::_load_alerts::e28f50c0":
        R_FRAGMENT,
    "proyecto2/src/reports/rolling_dashboard.py::_load_scalar_metrics::7f9f6f99":
        R_FRAGMENT,
    "proyecto2/src/reports/rolling_dashboard.py::_load_snapshot::0e9e5af9":
        R_FRAGMENT,
    "proyecto3/src/monthly_report.py::_build_cartera::f0c000d9":
        R_FRAGMENT,
    "scripts/audit/run_statistical_audit.py::_periodic_return_variance::63f46a57":
        R_FRAGMENT,
    "shared/export_tables.py::export_tables::874d473f":
        R_OPAQUE,
    "shared/statistical_audit/persistence.py::emit_findings::2725fd2e":
        R_FRAGMENT,
}

# site_id -> reason. Statements EXPLAIN rejects for a reason that is not a defect.
_EXPLAIN_EXEMPT: dict[str, str] = {}

# SQLite-only syntax outside a function that references the dialect helpers. Key is a site_id, a
# `file::function`, or a whole `file`.
_SQLITE_UNGUARDED_EXEMPT: dict[str, str] = {
    "shared/db.py::get_connection":
        "DESIGN: the PRAGMAs run only in the SQLite branch; the Postgres branch returns earlier in the same function",
    "shared/db.py::_announce_backend":
        "DESIGN: PRAGMA database_list is read only when backend != 'postgres'; the postgres branch reports host/port",
    "shared/schema_checks.py":
        "DESIGN: legacy check_schema_v19..v26 chain is SQLite-only with no live caller; assert_schema_alignment "
        "goes through the dialect-aware _table_columns instead",
}

# table -> reason. Tables where INSERT without ON CONFLICT is correct: append-only logs, or rows
# replaced by a purge that runs in a different function than the INSERT.
_APPEND_ONLY_TABLES: dict[str, str] = {
    "ingestion_log":
        "DESIGN: append-only P1 event log; every event is a new row by definition",
    "p2_pipeline_log":
        "DESIGN: append-only P2 per-run traceability log; every event is a new row by definition",
    "fund_cost_corrections":
        "DESIGN: append-only audit trail of every automatic cost correction (old value -> new value)",
    "audit_finding":
        "DESIGN: rows are keyed by run_id and the runner calls persistence.clear_run(run_id) before "
        "emit_findings, so a re-run replaces the run's rows instead of duplicating them",
}

_DML = {"SELECT", "WITH", "INSERT", "UPDATE", "DELETE", "VALUES"}


# ─── inventory ───────────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def sites():
    return S.all_sites()


def _in_scope(s) -> bool:
    return s.file not in _FILE_EXEMPT


def _sqlite_exempt(s) -> bool:
    return (s.site_id in _SQLITE_UNGUARDED_EXEMPT or f"{s.file}::{s.func}" in _SQLITE_UNGUARDED_EXEMPT
            or s.file in _SQLITE_UNGUARDED_EXEMPT)


# ─── governance ──────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["_FILE_EXEMPT", "_UNVERIFIED_BASELINE", "_EXPLAIN_EXEMPT",
                                   "_SQLITE_UNGUARDED_EXEMPT", "_APPEND_ONLY_TABLES"])
def test_allowlist_reasons_cite_a_ticket_or_a_real_design_reason(name):
    bad = bad_reasons(globals()[name])
    assert not bad, (f"{name}: reason must start with an FND-#### ticket or 'DESIGN: <explanation "
                     f">= 20 chars>': {bad}")


def test_check_reason_rejects_lazy_strings():
    assert check_reason("FND-0123 dynamic column list")
    assert check_reason("DESIGN: identifiers come from ATTRIBUTE_CATALOG, never from user input")
    for lazy in ("", None, "ok", "n/a", "DESIGN: x", "later", "DESIGN:short reason"):
        assert not check_reason(lazy)


def test_allowlists_have_no_stale_entries(sites):
    ids = {s.site_id for s in sites}
    files = {s.file for s in sites} | {f for f, _ in S.production_files()}
    tables = {(s.insert_table or "").split(".")[-1] for s in sites}
    stale = (
        [f"_FILE_EXEMPT:{f}" for f in _FILE_EXEMPT if f not in files]
        + [f"_UNVERIFIED_BASELINE:{k}" for k in _UNVERIFIED_BASELINE if k not in ids]
        + [f"_EXPLAIN_EXEMPT:{k}" for k in _EXPLAIN_EXEMPT if k not in ids]
        + [f"_SQLITE_UNGUARDED_EXEMPT:{k}" for k in _SQLITE_UNGUARDED_EXEMPT
           if k not in ids and k not in files and k not in {f"{s.file}::{s.func}" for s in sites}]
        + [f"_APPEND_ONLY_TABLES:{t}" for t in _APPEND_ONLY_TABLES if t not in tables]
    )
    assert not stale, "allowlist entries whose target no longer exists (delete them):\n  " + "\n  ".join(stale)


# ─── 2. unresolved statements: exact-set baseline ────────────────────────────────────────────────

def test_unresolved_statements_match_the_pinned_baseline(sites):
    unresolved = {s.site_id: s for s in sites
                  if _in_scope(s) and not s.resolved and not s.is_sqlite_branch
                  and not s.has_sqlite_only_syntax}
    new = sorted(set(unresolved) - set(_UNVERIFIED_BASELINE))
    gone = sorted(set(_UNVERIFIED_BASELINE) - set(unresolved))
    msg = []
    if new:
        msg.append("New statements the sweep cannot resolve statically (they would escape EXPLAIN). "
                   "Prefer restructuring so the SQL is a literal or an f-string over `{ph}` only; "
                   "otherwise add to _UNVERIFIED_BASELINE with a reason:\n  "
                   + "\n  ".join(f'"{k}": "DESIGN: ...",   # {unresolved[k].sql[:60]!r}' for k in new))
    if gone:
        msg.append("Baseline entries whose statement is now resolvable or gone (delete them):\n  "
                   + "\n  ".join(gone))
    assert not msg, "\n".join(msg)


# ─── 3. SQLite-only syntax must be dialect-guarded ───────────────────────────────────────────────

def test_sqlite_only_syntax_is_confined_to_dialect_guarded_functions(sites):
    offenders = [s for s in sites
                 if _in_scope(s) and s.has_sqlite_only_syntax and not s.dialect_guarded
                 and not _sqlite_exempt(s)]
    assert not offenders, (
        "SQLite-only SQL in a function that never forks on the dialect (it would reach Postgres "
        "unchanged). Fork with is_postgres_connection(conn), or exempt with a reason:\n  "
        + "\n  ".join(f'{s.site_id}   # {s.sql[:70]!r}' for s in offenders))


# ─── 4. idempotent INSERTs ───────────────────────────────────────────────────────────────────────

def _is_idempotent_insert(s) -> bool:
    """ON CONFLICT, an INSERT ... WHERE NOT EXISTS guard, or DELETE-then-INSERT of the same table
    in the same function (a full replace: re-running it leaves the same rows)."""
    up = s.sql.upper()
    return "ON CONFLICT" in up or "NOT EXISTS" in up or s.delete_first


def test_pg_inserts_are_idempotent(sites):
    offenders = []
    for s in sites:
        if not _in_scope(s) or s.first_keyword != "INSERT" or s.is_sqlite_branch:
            continue
        if re.match(r"\s*INSERT\s+OR\s", s.sql, re.I):          # SQLite branch by construction
            continue
        if _is_idempotent_insert(s):
            continue
        table = (s.insert_table or "").split(".")[-1]
        if table and table in _APPEND_ONLY_TABLES:
            continue
        offenders.append(s)
    assert not offenders, (
        "INSERT without ON CONFLICT on the Postgres path. Re-running a pipeline step (--from N) "
        "would duplicate these rows. Add ON CONFLICT ... DO NOTHING/UPDATE, or, if the table is an "
        "append-only log, add it to _APPEND_ONLY_TABLES with a reason:\n  "
        + "\n  ".join(f"{s.site_id}   # table={s.insert_table} :: {' '.join(s.sql.split())[:70]!r}"
                      for s in offenders))


# ─── 1. the EXPLAIN sweep ────────────────────────────────────────────────────────────────────────

_SEARCH_PATH = "gold, silver, bronze, control, public"


def _explainable(s) -> bool:
    """A statement resolved to the Postgres variant. SQLite-only syntax is excluded unless the
    function forks on the dialect, in which case its Postgres variant must be clean."""
    return (_in_scope(s) and s.resolved and not s.is_sqlite_branch
            and (s.dialect_guarded or not s.has_sqlite_only_syntax)
            and s.first_keyword in _DML)


def _explain_all(conn, statements, *, definite_only: bool):
    """EXPLAIN each statement (client-side binding: parameters become NULL literals, so the server
    is never asked to infer the type of a bare `$1`). Returns (failures, inconclusive) as lists of
    (site, message). With `definite_only`, only errors that cannot be an artefact of a placeholder
    substitution count as failures; anything else (syntax errors from a substituted fragment, type
    inference) is inconclusive."""
    import psycopg
    definite = (psycopg.errors.GroupingError, psycopg.errors.UndefinedTable,
                psycopg.errors.UndefinedColumn, psycopg.errors.UndefinedFunction,
                psycopg.errors.AmbiguousColumn)
    failures, inconclusive = [], []
    conn.rollback()
    conn.execute(f"SET search_path = {_SEARCH_PATH}")
    try:
        for s, sql in statements:
            cur = psycopg.ClientCursor(conn)
            try:
                cur.execute("EXPLAIN " + sql, S.explain_params(sql, s.passes_params))
            except psycopg.Error as e:
                msg = f"{type(e).__name__}: {str(e).splitlines()[0]}"
                if isinstance(e, definite) or not definite_only and not isinstance(
                        e, psycopg.errors.IndeterminateDatatype):
                    failures.append((s, msg))
                else:
                    inconclusive.append((s, msg))
            finally:
                cur.close()
                conn.rollback()
                conn.execute(f"SET search_path = {_SEARCH_PATH}")
    finally:
        conn.rollback()
        conn.execute("SET search_path = DEFAULT")
        conn.commit()
    return failures, inconclusive


def _fmt_failures(failures) -> str:
    return "\n  ".join(f"{s.site_id}\n      {why}\n      {' '.join(s.sql.split())[:110]!r}"
                      for s, why in failures)


def test_every_resolved_statement_explains_on_postgres(sites, pg_session_conn, capsys):
    """Needs the real db/pg DDL: run through `python scripts/ops/run_pg_tests.py`."""
    pytest.importorskip("psycopg")
    todo = [(s, s.sql) for s in sites if _explainable(s) and s.site_id not in _EXPLAIN_EXEMPT]
    failures, inconclusive = _explain_all(pg_session_conn, todo, definite_only=False)
    with capsys.disabled():
        print(f"\n[explain-sweep] statements={len(sites)} explained={len(todo)} "
              f"failed={len(failures)} inconclusive={len(inconclusive)}")
    assert not failures, (
        "Postgres rejects these statements (GroupingError = non-aggregated column with GROUP BY; "
        "UndefinedFunction = SQLite-only function; UndefinedColumn = renamed column such as "
        "window -> window_label). Fix the statement in its module (P#7), or add to _EXPLAIN_EXEMPT "
        "with a reason:\n  " + _fmt_failures(failures))


def test_partially_resolved_statements_have_no_definite_error_on_postgres(sites, pg_session_conn, capsys):
    """The pinned UNRESOLVED statements are not blind: with each runtime-assembled fragment (a
    column list, an IN-list) replaced by NULL, the rest of the statement is still EXPLAINed. Only
    errors that a substitution cannot cause count (GroupingError, unknown table/column/function,
    ambiguous column); a syntax error from a substituted fragment is reported as inconclusive."""
    pytest.importorskip("psycopg")
    todo = []
    for s in sites:
        if (not _in_scope(s) or s.resolved or s.is_sqlite_branch or s.has_sqlite_only_syntax
                or s.sql.startswith("<opaque") or s.first_keyword not in _DML
                or s.site_id in _EXPLAIN_EXEMPT):
            continue
        todo.append((s, s.sql.replace(S.DYN, "NULL")))
    failures, inconclusive = _explain_all(pg_session_conn, todo, definite_only=True)
    with capsys.disabled():
        print(f"\n[explain-sweep/partial] partial={len(todo)} failed={len(failures)} "
              f"inconclusive={len(inconclusive)}")
    assert not failures, ("Postgres definitely rejects the static part of these partially "
                          "resolved statements:\n  " + _fmt_failures(failures))


# ─── the rule itself, proven against the real pre-fix statement ──────────────────────────────────

_PRE_FIX_GAP_SQL = """
    SELECT benchmark_id, benchmark_name, COUNT(*) as n
    FROM fund_benchmarks
    WHERE source = 'MORNINGSTAR' AND benchmark_id IS NOT NULL
    GROUP BY benchmark_id
    ORDER BY n DESC LIMIT 10
"""


def test_the_sweep_would_have_caught_the_2026_09_26_incident(pg_conn):
    """The statement that broke PASO 1, verbatim, run through the same EXPLAIN mechanism."""
    psycopg = pytest.importorskip("psycopg")
    pg_conn.execute("""CREATE TABLE fund_benchmarks (isin text, source text, benchmark_id text,
                                                       benchmark_name text)""")
    with pytest.raises(psycopg.errors.GroupingError):
        pg_conn.execute("EXPLAIN " + _PRE_FIX_GAP_SQL)


def test_the_extractor_finds_the_fixed_gap_statement_and_resolves_it(sites):
    hits = [s for s in sites if s.file == "proyecto1/src/loaders/benchmark_loader.py"
            and "MIN(benchmark_name)" in s.sql]
    assert hits and all(s.resolved for s in hits)
