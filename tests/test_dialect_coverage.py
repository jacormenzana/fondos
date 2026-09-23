"""
tests/test_dialect_coverage.py — static guards against the failure classes that cost the Postgres
migration the most rework: production code that was never dialect-ported, found only after the
fact by grep.

What each rule is for (all three were real, all three were found late):

  1. `?` placeholders in a function with no dialect reference. Stage 9's first end-to-end rehearsal
     found proyecto2/src/calculations/*.py (every fund failed with "the query has 0 placeholders
     but 2 parameters were passed") and two functions in core/io.py, whose swallowed error turned
     every KIID cache lookup into a silent miss.
  2. Raw `sqlite3.connect()` in production modules. Such a connection ignores `--backend` and
     FONDOS_DB_BACKEND entirely. shared/export_tables.py was one (an entire planned stage that was
     silently dropped), and fund_family_builder.py's CLI block is run by the canonical launcher
     P1_discoverAllFunds.bat after every P1 cycle — after cutover it would have kept rebuilding
     families in the retired SQLite with exit code 0 while the rest of P1 wrote to Postgres.
  3. Stale allowlist entries.

Granularity matters, and the first draft of this file got it wrong: a FILE-level rule ("mentions
is_postgres_connection somewhere") did NOT flag core/io.py before its fix, because the file already
contained one ported function, mark_stale_for_refresh — while two others in it were unported. So
rule 1 works per function via `ast`. Mutation-tested against the pre-fix versions of these files
(see the migration plan's Stage 9 notes) rather than trusted because it passes today.

Exemptions are explicit, each with a reason, so they are visible in review. Adding one is a
decision, not a habit.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SCAN_DIRS = ("proyecto1", "proyecto2", "proyecto3", "shared")

# Rule 1 — "file" or "file::function". SQLite-only by design.
_QMARK_EXEMPT = {
    "proyecto1/migrate_v20_benchmark_role.py":  "one-shot SQLite data migration",
    "shared/migrate_schema_v27.py":             "one-shot SQLite schema migration (mirrors the Postgres DDL into SQLite)",
    "shared/schema_checks.py::check_schema_v24": "legacy SQLite-only diagnostic; no live caller (migration Stage 2)",
    "shared/schema_checks.py::check_schema_v26": "legacy SQLite-only diagnostic; no live caller (migration Stage 2)",
}

# Rule 2 — files allowed to open SQLite directly.
_CONNECT_EXEMPT = {
    "shared/db.py":                                "the implementation of get_connection itself",
    "shared/init_db.py":                           "creates the SQLite schema; Postgres schema comes from db/pg/*.sql",
    "shared/load_fondos_to_postgres.py":           "legacy SQLite→Postgres BI mirror (P4); SQLite is its source by definition",
    "proyecto1/migrate_v20_benchmark_role.py":     "one-shot SQLite data migration",
    "proyecto1/core/normalize_db_casing_v20.py":   "legacy standalone CLI; run() already accepts an injected connection, and "
                                                   "the pipeline's own global normalisation covers Postgres",
    "proyecto1/tools/audit_benchmark_consistency.py": "SQLite-only read-only audit tool — MUST be ported (or pointed at an "
                                                   "export) before SQLite is retired; tracked in the backlog",
}

_SKIP_PARTS = {"tests", "__pycache__", "log", "upload"}
_NONCANONICAL = re.compile(r"(_\d{8}(_\d+)?|_prod|_last|[Bb]ack[Uu]p)\.py$")
# A bare `?` bind placeholder after a comparison / list / values context.
_QMARK = re.compile(r"(=|<|>|!=|\bIN\s*\(|\bVALUES\s*\(|,|\(|\bLIMIT|\bBETWEEN|\bAND|\bOR)\s*\?\s*(\)|,|\s|$)")
_SQL_VERB = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE|WHERE)\b", re.I)
_DIALECT_REF = re.compile(r"is_postgres_connection|\bph\b|\b_ph\w*\b|placeholder|\bpg\s*=|_PG\b")


def _production_sources():
    for d in _SCAN_DIRS:
        for p in (_ROOT / d).rglob("*.py"):
            rel = p.relative_to(_ROOT)
            if _SKIP_PARTS & set(rel.parts) or _NONCANONICAL.search(p.name) or p.name.startswith("test_"):
                continue
            yield rel.as_posix(), p


def _own_strings(fn: ast.AST):
    """String constants belonging to `fn` itself, not to functions/classes nested inside it."""
    stack = list(fn.body)
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            yield n.value
        stack.extend(ast.iter_child_nodes(n))


def find_qmark_functions(rel: str, src: str) -> list[str]:
    """Functions that execute SQL with a bare `?` placeholder and never reference the dialect
    helpers. Factored out so the rule itself can be tested against known-bad source."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    hits = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        seg = ast.get_source_segment(src, fn) or ""
        if ".execute" not in seg or _DIALECT_REF.search(seg):
            continue
        if any(_QMARK.search(s) and _SQL_VERB.search(s) for s in _own_strings(fn)):
            hits.append(f"{rel}::{fn.name}")
    return hits


def test_no_function_executes_sqlite_only_placeholders_without_dialect_branching():
    offenders = []
    for rel, p in _production_sources():
        for h in find_qmark_functions(rel, p.read_text(encoding="utf-8", errors="replace")):
            if h not in _QMARK_EXEMPT and h.split("::")[0] not in _QMARK_EXEMPT:
                offenders.append(h)
    assert not offenders, (
        "These functions execute SQL with SQLite-only `?` placeholders but never reference the "
        "dialect helpers, so they cannot run against Postgres (psycopg3 raises \"the query has 0 "
        "placeholders but N parameters were passed\", often masked by a broad except):\n  "
        + "\n  ".join(offenders)
        + "\nPort with `ph = \"%s\" if is_postgres_connection(conn) else \"?\"` (see shared/db.py), or — "
          "only if SQLite-only by design — add to _QMARK_EXEMPT with a reason."
    )


def find_raw_sqlite_connects(src: str) -> list[int]:
    """Line numbers of real `sqlite3.connect(...)` CALLS. AST-based, not a substring search, so a
    comment or docstring that merely mentions it (as the fixes for this very problem do) is ignored."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    return [n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "connect"
            and isinstance(n.func.value, ast.Name) and n.func.value.id == "sqlite3"]


def test_no_production_module_opens_sqlite_directly():
    offenders = [rel for rel, p in _production_sources()
                 if rel not in _CONNECT_EXEMPT
                 and find_raw_sqlite_connects(p.read_text(encoding="utf-8", errors="replace"))]
    assert not offenders, (
        "These modules open SQLite with a raw sqlite3.connect(), so --backend / FONDOS_DB_BACKEND "
        "cannot reach them (after cutover they would keep writing to the retired database with no "
        "error):\n  " + "\n  ".join(offenders)
        + "\nUse shared.db.get_connection(backend=...), or — only if SQLite-only by design — add to "
          "_CONNECT_EXEMPT with a reason."
    )


def test_exemptions_are_not_stale():
    """A deleted/renamed file (or function) must drop out of the allowlists, or they rot."""
    missing = [k for k in (*_QMARK_EXEMPT, *_CONNECT_EXEMPT)
               if not (_ROOT / k.split("::")[0]).exists()]
    assert not missing, f"exemptions list files that no longer exist: {missing}"


# --- the rule itself, proven against real known-bad source (the pre-fix code) -----------------

_BAD_FUNC = '''
def load_category_returns(conn, fund_nature, horizon):
    rows = conn.execute("""
        SELECT isin, value FROM fund_metrics WHERE horizon = ? AND fund_nature = ?
    """, (horizon, fund_nature)).fetchall()
    return rows
'''

_PARTIALLY_PORTED_FILE = '''
from shared.db import is_postgres_connection

def ported(conn):
    ph = "%s" if is_postgres_connection(conn) else "?"
    return conn.execute(f"SELECT 1 WHERE x = {ph}", (1,))

def forgotten(conn, isin):
    return conn.execute("SELECT * FROM fund_kiid_metadata WHERE ISIN = ?", (isin,)).fetchone()
'''


def test_rule_flags_a_function_with_bare_placeholders():
    assert find_qmark_functions("x.py", _BAD_FUNC) == ["x.py::load_category_returns"]


def test_rule_flags_the_unported_function_in_a_partially_ported_file():
    """The case a file-level rule missed (core/io.py before its fix): one function is ported, so the
    file mentions the dialect helper, but another function in it is not."""
    assert find_qmark_functions("x.py", _PARTIALLY_PORTED_FILE) == ["x.py::forgotten"]


def test_connect_rule_flags_a_real_call_but_not_a_comment_or_docstring():
    real = 'import sqlite3\ndef f(p):\n    return sqlite3.connect(str(p))\n'
    prose = 'import sqlite3\n"""Docs: never call sqlite3.connect() directly."""\n# a raw sqlite3.connect() here would ignore --backend\ndef f(conn):\n    return conn\n'
    assert find_raw_sqlite_connects(real) == [3]
    assert find_raw_sqlite_connects(prose) == []


def find_hardcoded_backend_defaults(src: str) -> list[str]:
    """Functions with a `backend` parameter whose default is a literal backend name. Such a default
    ignores FONDOS_DB_BACKEND / .env: export_metrics.export(backend="sqlite") made
    P2_calculateIndicators.bat export from the frozen SQLite file after the switch (found by the
    2026-09-23 launcher test). The default must be None so get_connection() resolves it."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    bad = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        a = fn.args
        pos = [*a.posonlyargs, *a.args]
        pairs = list(zip(pos[len(pos) - len(a.defaults):], a.defaults)) + list(zip(a.kwonlyargs, a.kw_defaults))
        for arg, default in pairs:
            if (arg.arg == "backend" and isinstance(default, ast.Constant)
                    and default.value in ("sqlite", "postgres")):
                bad.append(f"{fn.name}(backend={default.value!r}) line {fn.lineno}")
    return bad


def test_no_function_hardcodes_its_backend_default():
    offenders = [f"{rel}: {b}" for rel, p in _production_sources()
                 for b in find_hardcoded_backend_defaults(p.read_text(encoding="utf-8", errors="replace"))]
    assert not offenders, ("A literal backend default bypasses the .env switch; use backend=None:\n  "
                           + "\n  ".join(offenders))


def test_backend_default_rule_flags_a_hardcoded_default_but_not_none():
    assert find_hardcoded_backend_defaults('def export(o, *, backend: str = "sqlite"): pass')
    assert find_hardcoded_backend_defaults('def f(a, backend="postgres"): pass')
    assert not find_hardcoded_backend_defaults("def export(o, *, backend=None): pass")
    assert not find_hardcoded_backend_defaults('def g(mode="sqlite"): pass')
