"""
tests/_sql_sites.py — static extraction of the SQL statements production code executes.

Shared by tests/test_sql_explain_sweep_pg.py (EXPLAIN every resolvable statement against the real
Postgres DDL; pin the unresolved ones; enforce idempotent INSERTs). Not a test module.

Why this exists: on 2026-09-26 `run_gap_analysis()` failed on Postgres with GroupingError
(non-aggregated column in a GROUP BY query, which SQLite tolerates). It sat in a diagnostic tail that
only runs after a real load, so no rehearsal ever executed it. A regex guard cannot decide whether
SQL is valid; Postgres's own parser can, without executing anything (`EXPLAIN` plans, it does not
run), and it does not care whether the code path is ever reached in a rehearsal.

Extraction is AST-based and deliberately conservative:
  * A call is an SQL sink when it is `<x>.execute(sql, ...)`, `<x>.executemany(sql, ...)`,
    `execute_fail_soft(conn, sql, ...)`, `executemany(conn, sql, ...)`, `_executemany(conn, sql,
    ...)` or `read_sql*(sql, conn)`.
  * The SQL argument is resolved through string constants, f-strings, `+` concatenation,
    `.format()`, `.strip()`, conditional expressions and simple name assignments (module or
    function scope).
  * Code that forks on the dialect (`if pg:` / `x if pg else y` / `is_postgres_connection(conn)` /
    `backend == "postgres"`) is resolved once per dialect, so the SQLite and the Postgres variants
    of one statement are never mixed: a Postgres variant uses `%s` and the Postgres column names,
    a SQLite variant uses `?` and is not sent to Postgres.
  * Placeholder / window-column fields with a known meaning are substituted (`{ph}`, `{wc}`).
    ANYTHING else that cannot be resolved makes the site UNRESOLVED: it is never counted as
    verified. The unresolved set is pinned as an exact-set baseline in the sweep test, so a new
    dynamic statement cannot slip in unseen.
"""
from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCAN_DIRS = ("proyecto1", "proyecto2", "proyecto3", "shared", "scripts/launch", "scripts/audit")
_SKIP_PARTS = {"tests", "__pycache__", "log", "logs", "upload", "prev"}
_NONCANONICAL = re.compile(r"(_\d{8}(_\d+)?|_prod|_last|[Bb]ack[Uu]p)\.py$")

# Format fields whose meaning is fixed across the codebase.
_PLACEHOLDER_FIELDS = {"ph", "_ph", "PH", "_PH", "placeholder", "_placeholder", "ph_"}
_WINDOW_FIELDS = {"wc", "_wc", "window_col", "_window_col", "window"}

# Names that mean "this connection is Postgres" in an `if` / conditional expression.
_PG_NAMES = {"pg", "_pg", "is_pg", "pg_mode", "postgres", "use_pg", "_use_pg"}

DYN = "\x00DYN\x00"          # marker for a part that cannot be resolved statically
_MAX_VARIANTS = 8
_DIALECTS = ("pg", "sqlite")

_SQL_VERB = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE|WITH|CREATE|DROP|ALTER|VALUES|SAVEPOINT|"
                       r"RELEASE|ROLLBACK|REFRESH|TRUNCATE|SET|PRAGMA|EXPLAIN|COPY|VACUUM|ANALYZE)\b",
                       re.I)
# A bare `?` bind placeholder = the SQLite dialect branch of a function.
_QMARK = re.compile(r"(=|<|>|!=|\bIN\s*\(|\bVALUES\s*\(|,|\(|\bLIMIT|\bBETWEEN|\bAND|\bOR)\s*\?\s*(\)|,|\s|$)")

# The enclosing function refers to the dialect helpers (same list tests/test_dialect_coverage.py
# uses for its `?` rule): the SQL is reached through an `is_postgres_connection()` fork.
DIALECT_REF = re.compile(r"is_postgres_connection|\bph\b|\b_ph\w*\b|placeholder|\bpg\s*=|_PG\b")

# Constructs that exist only in SQLite. On the Postgres path they are a bug; they are legitimate
# only inside a SQLite branch, i.e. in a function that forks on the dialect.
SQLITE_ONLY = re.compile(
    r"\bPRAGMA\b|\bsqlite_master\b|\bINSERT\s+OR\s+(IGNORE|REPLACE)\b|"
    r"\b(datetime|date)\s*\(\s*'now'|\bjulianday\s*\(|\bIFNULL\s*\(|\bGROUP_CONCAT\s*\(|"
    r"\bstrftime\s*\(|\bAUTOINCREMENT\b|\bIIF\s*\(", re.I)


@dataclass(frozen=True)
class SqlSite:
    file: str          # repo-relative posix path
    func: str          # enclosing function name ('<module>' at module level)
    lineno: int
    sql: str           # resolved text; unresolved parts are the DYN marker
    resolved: bool     # False when any part could not be resolved
    site_id: str       # file::func::hash(sql) — stable under line moves
    dialect_guarded: bool = False   # enclosing function references the dialect helpers
    delete_first: bool = False      # INSERT whose function also DELETEs from the same table
    dialect: str = "pg"             # 'sqlite' = the SQLite variant of a forked statement
    passes_params: bool = True      # the call passes a parameters argument

    @property
    def first_keyword(self) -> str:
        m = re.match(r"\s*(\w+)", self.sql)
        return m.group(1).upper() if m else ""

    @property
    def is_sqlite_branch(self) -> bool:
        """The SQLite side of a dialect fork: bare `?` placeholders, or the sqlite variant."""
        return self.dialect == "sqlite" or bool(_QMARK.search(self.sql))

    @property
    def has_sqlite_only_syntax(self) -> bool:
        return bool(SQLITE_ONLY.search(self.sql.replace(DYN, "")))

    @property
    def insert_table(self) -> str | None:
        m = re.match(r"\s*INSERT\s+INTO\s+([\w\.\"]+)", self.sql, re.I)
        return m.group(1).strip('"').lower() if m else None


def production_files():
    for d in SCAN_DIRS:
        base = ROOT / d
        if not base.exists():
            continue
        for p in sorted(base.rglob("*.py")):
            rel = p.relative_to(ROOT)
            if _SKIP_PARTS & set(rel.parts) or _NONCANONICAL.search(p.name) or p.name.startswith("test_"):
                continue
            yield rel.as_posix(), p


# ── dialect orientation of an `if` test ──────────────────────────────────────────────────────────

def _pg_orientation(test) -> bool | None:
    """True if `test` being truthy means 'this is Postgres', False if it means 'this is SQLite',
    None if it is not a recognisable dialect test."""
    if isinstance(test, ast.Name):
        return True if test.id in _PG_NAMES else None
    if isinstance(test, ast.Attribute):
        return True if test.attr in _PG_NAMES else None
    if isinstance(test, ast.Call):
        f = test.func
        name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
        return True if name == "is_postgres_connection" else None
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        inner = _pg_orientation(test.operand)
        return None if inner is None else (not inner)
    if (isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], (ast.Eq, ast.NotEq))
            and isinstance(test.comparators[0], ast.Constant) and test.comparators[0].value in ("postgres", "sqlite")):
        is_pg_value = test.comparators[0].value == "postgres"
        return is_pg_value if isinstance(test.ops[0], ast.Eq) else (not is_pg_value)
    return None


# ── resolution ───────────────────────────────────────────────────────────────────────────────────

def _collect_assignments(body) -> dict:
    """name -> [(value node, dialect-tag)] for simple `name = <expr>` statements in `body` and its
    control-flow blocks (not nested function/class definitions). The tag is 'pg' / 'sqlite' when
    the assignment sits in a recognisable dialect branch, else None."""
    out: dict = {}

    def walk(stmts, tag):
        for n in stmts:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
                out.setdefault(n.targets[0].id, []).append((n.value, tag))
            elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.value is not None:
                out.setdefault(n.target.id, []).append((n.value, tag))
            if isinstance(n, ast.If):
                o = _pg_orientation(n.test)
                body_tag = tag if o is None else ("pg" if o else "sqlite")
                else_tag = tag if o is None else ("sqlite" if o else "pg")
                walk(n.body, body_tag)
                walk(n.orelse, else_tag)
            else:
                for field in ("body", "orelse", "finalbody"):
                    walk(getattr(n, field, []) or [], tag)
                for h in getattr(n, "handlers", []) or []:
                    walk(h.body, tag)

    walk(body, None)
    return out


def _subst_for(name: str, dialect: str) -> str | None:
    if name in _PLACEHOLDER_FIELDS:
        return "%s" if dialect == "pg" else "?"
    if name in _WINDOW_FIELDS:
        return "window_label" if dialect == "pg" else "window"
    return None


def _resolve(node, scopes, dialect, depth=0):
    """-> list[(text, resolved_bool)] (at most _MAX_VARIANTS)."""
    if depth > 4:
        return [(DYN, False)]
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [(node.value, True)]
    if isinstance(node, ast.JoinedStr):
        variants = [("", True)]
        for part in node.values:
            if isinstance(part, ast.Constant):
                piece = [(str(part.value), True)]
            else:  # FormattedValue
                expr = part.value
                sub = _subst_for(expr.id, dialect) if isinstance(expr, ast.Name) else None
                if sub is not None:
                    piece = [(sub, True)]
                elif isinstance(expr, (ast.Name, ast.IfExp)):
                    inner = _resolve(expr, scopes, dialect, depth + 1)
                    piece = inner if inner and all(r for _, r in inner) else [(DYN, False)]
                else:
                    piece = [(DYN, False)]
            variants = [(a + b, ra and rb) for a, ra in variants for b, rb in piece][:_MAX_VARIANTS]
        return variants
    if isinstance(node, ast.Name):
        for scope in scopes:
            if node.id in scope:
                out = []
                for v, tag in scope[node.id]:
                    if tag is None or tag == dialect:
                        out.extend(_resolve(v, scopes, dialect, depth + 1))
                return out[:_MAX_VARIANTS] or [(DYN, False)]
        return [(DYN, False)]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _resolve(node.left, scopes, dialect, depth + 1)
        right = _resolve(node.right, scopes, dialect, depth + 1)
        return [(a + b, ra and rb) for a, ra in left for b, rb in right][:_MAX_VARIANTS]
    if isinstance(node, ast.IfExp):
        o = _pg_orientation(node.test)
        if o is not None:
            pick = node.body if (o == (dialect == "pg")) else node.orelse
            return _resolve(pick, scopes, dialect, depth + 1)
        return (_resolve(node.body, scopes, dialect, depth + 1)
                + _resolve(node.orelse, scopes, dialect, depth + 1))[:_MAX_VARIANTS]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id == "_sql" and len(node.args) == 2:
            # scripts/audit/run_statistical_audit.py::_sql(conn, query): substitutes the `{window}`
            # slot and turns `?` into the backend's placeholder.
            return [(t.replace("{window}", "window_label" if dialect == "pg" else "window")
                      .replace("?", "%s" if dialect == "pg" else "?"), r)
                    for t, r in _resolve(node.args[1], scopes, dialect, depth + 1)]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr == "strip" and not node.args:
            return [(t.strip(), r) for t, r in _resolve(node.func.value, scopes, dialect, depth + 1)]
        if node.func.attr == "format":
            out = []
            for text, res in _resolve(node.func.value, scopes, dialect, depth + 1):
                all_known = res

                def repl(m):
                    nonlocal all_known
                    sub = _subst_for(m.group(1), dialect)
                    if sub is not None:
                        return sub
                    all_known = False
                    return DYN
                out.append((re.sub(r"\{(\w+)\}", repl, text), all_known))
            return out[:_MAX_VARIANTS]
    return [(DYN, False)]


# ── sinks ────────────────────────────────────────────────────────────────────────────────────────

_ATTR_SINKS = {"execute": 0, "executemany": 0, "read_sql": 0, "read_sql_query": 0}
_NAME_SINKS = {"execute_fail_soft": 1, "executemany": 1, "_executemany": 1, "read_sql": 0,
               "read_sql_query": 0, "_read_table_df": 1, "build_population": 1}
# Functions that merely forward their `sql` parameter to the driver: their CALLERS are the real
# statements (and are sinks themselves, see _NAME_SINKS), so the pass-through is not a site.
_PASSTHROUGH_WRAPPERS = {"execute_fail_soft", "executemany", "_executemany", "_read_table_df",
                         "build_population"}


def _sink(call: ast.Call):
    """-> (sql arg node, passes_params) or None."""
    f = call.func
    if isinstance(f, ast.Attribute) and f.attr in _ATTR_SINKS:
        idx = _ATTR_SINKS[f.attr]
    elif isinstance(f, ast.Name) and f.id in _NAME_SINKS:
        idx = _NAME_SINKS[f.id]
    else:
        return None
    if len(call.args) <= idx:
        return None
    passes = len(call.args) > idx + 1 or any(k.arg in ("params", "parameters") for k in call.keywords)
    return call.args[idx], passes


def _iter_scopes(tree):
    """(function name, scope body, node-or-None) triples, module first."""
    yield "<module>", tree.body, None
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield n.name, n.body, n


def _calls_tagged(body):
    """(call node, dialect tag) for every call in `body`, not descending into nested function or
    class definitions. The tag is 'pg' / 'sqlite' for a call inside a recognisable dialect branch
    (`if pg:` / `if not is_postgres_connection(conn):` ...), else None."""
    def walk(stmts, tag):
        for n in stmts:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            blocks = [getattr(n, f, None) or [] for f in ("body", "orelse", "finalbody")]
            handlers = getattr(n, "handlers", None) or []
            nested = {id(x) for blk in blocks for x in blk} | {id(h) for h in handlers}
            for child in ast.iter_child_nodes(n):
                if id(child) in nested:
                    continue
                for c in ast.walk(child):
                    if isinstance(c, ast.Call):
                        yield c, tag
            if isinstance(n, ast.If):
                o = _pg_orientation(n.test)
                yield from walk(n.body, tag if o is None else ("pg" if o else "sqlite"))
                yield from walk(n.orelse, tag if o is None else ("sqlite" if o else "pg"))
            else:
                for blk in blocks:
                    yield from walk(blk, tag)
                for h in handlers:
                    yield from walk(h.body, tag)
    yield from walk(body, None)


def _norm(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


def extract_sites(rel: str, src: str) -> list[SqlSite]:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    module_scope = _collect_assignments(tree.body)
    sites: dict[str, SqlSite] = {}
    for func, body, fn_node in _iter_scopes(tree):
        scopes = (_collect_assignments(body), module_scope) if func != "<module>" else (module_scope,)
        segment = (ast.get_source_segment(src, fn_node) or "") if fn_node is not None else src
        guarded = bool(DIALECT_REF.search(segment))
        for call, call_tag in _calls_tagged(body):
            sink = _sink(call)
            if sink is None:
                continue
            arg, passes = sink
            if (func in _PASSTHROUGH_WRAPPERS and isinstance(arg, ast.Name)
                    and fn_node is not None
                    and arg.id in {a.arg for a in fn_node.args.args}):
                continue
            seen_texts: set = set()
            for dialect in (_DIALECTS if call_tag is None else (call_tag,)):
                for text, resolved in _resolve(arg, scopes, dialect):
                    if resolved is False and DYN == text:      # fully opaque expression
                        text_for_id = "<opaque:" + ast.unparse(arg)[:60] + ">"
                        shown = text_for_id
                    else:
                        if not _SQL_VERB.search(text.replace(DYN, "")):
                            continue                            # not SQL (e.g. a shell command)
                        text_for_id = _norm(text)
                        shown = text
                    if text_for_id in seen_texts:               # identical in both dialects
                        continue
                    seen_texts.add(text_for_id)
                    h = hashlib.sha1(text_for_id.encode()).hexdigest()[:8]
                    sid = f"{rel}::{func}::{h}"
                    if sid not in sites:
                        sites[sid] = SqlSite(rel, func, call.lineno, shown,
                                             resolved and DYN not in text, sid, guarded,
                                             dialect=dialect, passes_params=passes)
    # DELETE-then-INSERT is idempotent: mark INSERTs whose function also deletes from that table.
    deletes: dict = {}
    for s in sites.values():
        m = re.match(r"\s*DELETE\s+FROM\s+([\w\.\"]+)", s.sql, re.I)
        if m:
            deletes.setdefault((s.file, s.func), set()).add(m.group(1).strip('"').lower().split(".")[-1])
    out = []
    for s in sites.values():
        table = (s.insert_table or "").split(".")[-1]
        if table and table in deletes.get((s.file, s.func), ()):
            s = replace(s, delete_first=True)
        out.append(s)
    return out


def all_sites() -> list[SqlSite]:
    out: list[SqlSite] = []
    for rel, p in production_files():
        out.extend(extract_sites(rel, p.read_text(encoding="utf-8", errors="replace")))
    return out


def explain_params(sql: str, passes_params: bool):
    """The parameters to hand psycopg for `EXPLAIN <sql>` so it is parsed the way production sends
    it: `None` when production passes none (a literal `%` is then not a placeholder), a dict of NULLs
    for `%(name)s` placeholders, else a tuple of NULLs for `%s`."""
    named = re.findall(r"%\((\w+)\)s", sql)
    if named:
        return {n: None for n in named}
    if not passes_params:
        return None
    return (None,) * len(re.findall(r"(?<!%)%s", sql.replace("%%", "\x01\x01")))
