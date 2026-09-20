# shared/db.py
# -*- coding: utf-8 -*-
"""
Conexion a fondos.sqlite (y, durante la migracion, a Postgres) para P1/P2/P3.

Sustituye a proyecto1/src/db.py y proyecto2/src/db.py.

Uso desde cualquier modulo:
    from shared.db import get_connection

Cambios v17:
  - timeout=30 en sqlite3.connect() — evita OperationalError en accesos
    concurrentes desde scripts distintos bajo WAL mode.
  - get_connection() acepta db_path opcional para tests y scripts
    que necesiten apuntar a una BD distinta de la configurada.

=== Dual-dialect capability (2026-09-18, migration plan §5b) — read before touching this file ===

`get_connection()` now accepts an optional `backend` keyword ("sqlite", the default — zero
behavior change for any existing caller — or "postgres"). This is the deliberate, scoped answer to
"how do we port the connection layer without breaking the live production pipeline": NOT a
wholesale one-way swap, NOT a parallel module tree (would violate P#11 DRY — two near-duplicate
copies of every writer coexisting for months), but a factory that can produce either connection
type, with individual functions ported IN PLACE, one at a time, each branching internally on
connection type for the parts that actually differ. See `is_postgres_connection()` below for the
canonical way to do that branch.

**Migration invariant, load-bearing:** every existing call site (`get_connection()` with no args,
or `get_connection(db_path)`) is completely unaffected — they get a SQLite connection exactly as
before. Nothing in production routes to Postgres until a whole tranche (P1, then P2, then P3) is
ported and validated, and even then only via the explicit dual-write wiring at cutover (plan §5e)
— not by anything in this file changing behavior on its own.

**Deliberately NOT built: an automatic `?` → `%s` placeholder translator.** It looks like an
obvious convenience, and was considered — rejected because `?` is not just SQLite's placeholder
character, it is also PostgreSQL's own native jsonb containment operator (`?`, `?|`, `?&`,
`jsonb_column ? 'key'`). A blind regex substitution on a ported query that ever touches
`gold.fund_scores.score_detail` or `bronze.fund_kiid_metadata.processing_breakdown` (both `jsonb`
in the target schema — see `db/pg/10_bronze.sql` / `30_gold.sql`) would silently corrupt that
operator into a placeholder. Each ported function writes its own explicit `%s`-parameterized SQL
string for the Postgres branch — more typing, no footgun.

**What IS built:** `is_postgres_connection(conn)` — the one canonical dialect check, so ported
functions don't each need their own `isinstance` import juggling.

**Row access — corrected 2026-09-20, first Phase 5b port.** The original design here used
`psycopg.rows.dict_row` and asserted "name-based access is already the codebase's dominant style."
That assertion was wrong, found by actually porting `proyecto2/src/analysis/export_metrics.py`'s
`q_estado()`/`export()` against live Postgres: its consumer `build_estado()` does
`ws.append(list(r))`, relying on `sqlite3.Row`'s iteration-yields-values-in-column-order behavior
(same as a tuple). `list(a_dict_row_result)` yields the dict's KEYS instead — column names would
have silently landed in the Excel report as if they were data, with no exception raised anywhere to
catch it. Root-caused once here rather than patched at each of the ~86 call sites (P#2/P#11):
`_SqliteCompatRow` (below) supports BOTH `row[0]` and `row["col"]`, matching `sqlite3.Row` exactly,
including iteration yielding values. This is now the `row_factory` for every `backend="postgres"`
connection — ported functions can keep whatever access style the original SQLite code used.

This whole `backend="postgres"` branch, and every per-function dialect branch it enables, is
transitional — deleted once SQLite is retired (plan §5e Stage 3), same category as the seed
loader's reverse-coercion functions kept only for the rollback runbook.
"""

import sqlite3
import sys
from pathlib import Path
from typing import Optional, Union

_ROOT = Path(__file__).resolve().parent.parent   # c:/desarrollo/fondos
sys.path.insert(0, str(_ROOT))

from shared.config import DB_PATH

try:
    import psycopg
except ImportError:  # pragma: no cover — psycopg3 optional until a caller actually asks for it
    psycopg = None


class _SqliteCompatRow:
    """A psycopg3 row that behaves like sqlite3.Row: subscriptable by int OR str, iterates values
    (not keys) in column order. See the "Row access" note in this module's docstring for why this
    exists — dict_row alone silently breaks any code relying on sqlite3.Row's positional/iteration
    behavior (e.g. `list(row)`, `for v in row`), which turned out to be common in this codebase.
    """
    __slots__ = ("_columns", "_index", "_values")

    def __init__(self, columns: list, index: dict, values: tuple):
        self._columns = columns
        self._index = index
        self._values = values

    def __getitem__(self, key):
        if isinstance(key, str):
            return self._values[self._index[key]]
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def keys(self):
        return list(self._columns)

    def __repr__(self):
        return f"<Row {dict(zip(self._columns, self._values))}>"

    def __eq__(self, other):
        # sqlite3.Row supports content equality between two distinct row objects (verified
        # directly, Phase 5c, 2026-09-20: r1 == r2 is True for two separately-fetched rows with
        # identical content) — without this, write-path code that compares rows (dedup, diff
        # checks) would silently always get False under Postgres. Only compares against another
        # _SqliteCompatRow; NotImplemented lets Python fall back sanely for anything else.
        if isinstance(other, _SqliteCompatRow):
            return self._columns == other._columns and self._values == other._values
        return NotImplemented

    def __hash__(self):
        return hash((tuple(self._columns), tuple(self._values)))


def _sqlite_compat_row_factory(cursor):
    """psycopg3 row-factory protocol: (cursor) -> (values: tuple) -> Row. Column name/index lookup
    is built once per cursor.description (i.e. once per query, not once per row) and shared by
    reference across every row in the result set — same amortized cost as sqlite3.Row's own
    implementation."""
    # cursor.description is None for statements with no result columns (SET, DDL, etc.) — psycopg3
    # still asks the row_factory to build a row maker in that case even though it's never called.
    columns = [d.name for d in cursor.description] if cursor.description else []
    index = {name: i for i, name in enumerate(columns)}

    def make_row(values):
        return _SqliteCompatRow(columns, index, values)

    return make_row

# Populated lazily (see _pg_dsn()) rather than at import time, so importing shared.db never
# requires FONDOS_PG_DSN to be set — only actually requesting backend="postgres" does.
_PG_DSN_ENV_VAR = "FONDOS_PG_DSN"


def is_postgres_connection(conn) -> bool:
    """The one canonical dialect check — use this instead of a local `isinstance` in every ported
    function. True for a connection returned by `get_connection(backend="postgres")` (or
    `pg_conn`/`pg_conn_module_schema` from `shared.testing.pg_fixtures`, which return the same
    psycopg3 connection type); False for the default SQLite connection."""
    return psycopg is not None and isinstance(conn, psycopg.Connection)


def _pg_dsn() -> str:
    import os
    dsn = os.environ.get(_PG_DSN_ENV_VAR)
    if not dsn:
        raise RuntimeError(
            f"get_connection(backend='postgres') requires {_PG_DSN_ENV_VAR} to be set "
            "(e.g. postgresql://fondos_app@localhost:5432/fondos). Password via PGPASSWORD env "
            "var or a .pgpass file — never on the command line or hardcoded."
        )
    return dsn


def get_connection(
    db_path: Optional[Path] = None, *, backend: str = "sqlite"
) -> Union[sqlite3.Connection, "psycopg.Connection"]:
    """
    Devuelve una conexion a la base de datos configurada.

    Por defecto (backend="sqlite", sin cambios de comportamiento respecto a cualquier llamada
    existente): conexion sqlite3 a fondos.sqlite con:
      - foreign_keys activadas
      - journal_mode WAL (escrituras concurrentes seguras)
      - timeout=30s (reintenta en caso de bloqueo concurrente)
      - row_factory = sqlite3.Row (acceso por nombre de columna)

    backend="postgres" (nuevo, migracion §5b — ver el docstring del modulo antes de usarlo):
    conexion psycopg3 a la base de datos apuntada por la variable de entorno FONDOS_PG_DSN, con
    row_factory compatible con sqlite3.Row (acceso por indice O por nombre de columna, igual que
    el codigo SQLite existente — ver _SqliteCompatRow). `db_path` se ignora en este modo.

    Parámetros:
        db_path: ruta alternativa a la BD SQLite. Si es None, usa DB_PATH de shared.config.
                 Solo aplica cuando backend="sqlite".
        backend: "sqlite" (por defecto) o "postgres".

    Lanza FileNotFoundError si la BD SQLite no existe (backend="sqlite").
    Lanza RuntimeError si FONDOS_PG_DSN no esta definida (backend="postgres").
    Ejecutar primero:  python -m shared.init_db
    """
    if backend == "postgres":
        if psycopg is None:
            raise RuntimeError(
                "backend='postgres' requires psycopg3 (pip install 'psycopg[binary]') — "
                "not installed in this environment."
            )
        conn = psycopg.connect(_pg_dsn(), row_factory=_sqlite_compat_row_factory)
        # db/pg/00_roles_schemas.sql sets this via ALTER ROLE for fondos_owner/fondos_app so that
        # unqualified table names (the whole point of the schema design — see the DDL's own
        # comment) resolve without query rewrites. Set it here too, defensively: found live
        # 2026-09-20 that the role actually in FONDOS_PG_DSN right now (postgres superuser, not
        # fondos_app) has the ordinary "$user", public default — an unqualified `fund_metrics`
        # query failed outright. Redundant once every caller uses fondos_app, harmless meanwhile.
        conn.execute("SET search_path = gold, silver, bronze, control, public")
        # Commit immediately: plain SET (no LOCAL) is still transaction-scoped in the sense that a
        # ROLLBACK of the transaction that issued it undoes it too, unless committed first. Found
        # live 2026-09-20: a caller that rolls back after a later failed statement (export_metrics
        # .export()'s per-sheet error recovery) silently lost the search_path along with it, so the
        # NEXT sheet's otherwise-correct query failed with "relation ... does not exist" — a bug
        # nothing downstream could plausibly have anticipated, only found by exercising the real
        # multi-statement, error-recovering call pattern end to end.
        conn.commit()
        return conn
    if backend != "sqlite":
        raise ValueError(f"Unknown backend {backend!r} — expected 'sqlite' or 'postgres'")

    target = Path(db_path) if db_path is not None else DB_PATH

    if not target.exists():
        raise FileNotFoundError(
            f"No se encuentra la base de datos: {target}\n"
            "Ejecuta primero: python -m shared.init_db"
        )

    conn = sqlite3.connect(str(target), timeout=30)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    # Performance pragmas (safe with WAL):
    #   synchronous=NORMAL — skips per-commit WAL fsync; power-safe under WAL
    #     (a checkpoint sync still protects against corruption on crash).
    #   cache_size=-65536  — 64 MB page cache (vs ~2 MB default); reduces
    #     repeated btree traversals on the 16M-row fund_metric_timeseries.
    #   temp_store=MEMORY  — sorts/indexes for GROUP BY / subqueries stay in RAM.
    #   mmap_size          — 512 MB memory-mapped read window; speeds sequential
    #     reads on large tables without extra system calls.
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA cache_size = -65536;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    conn.execute("PRAGMA mmap_size = 536870912;")
    conn.row_factory = sqlite3.Row
    # isolation_level=None: delega control de transacciones a SQLite y al
    # código explícito (with conn:). Evita que Python abra transacciones
    # implícitas que interfieren con ON CONFLICT DO UPDATE (SQLite 3.24+).
    # Sin esto, executescript() en create_schema resetea isolation_level a ''
    # y el upsert falla con "ON CONFLICT clause does not match any PK".
    conn.isolation_level = None
    return conn
