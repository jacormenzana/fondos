# -*- coding: utf-8 -*-
"""
normalize_db_casing_v20.py — barrido idempotente de casing/valor sobre fund_master.

Motivo (root-cause, §C-2 CACHED/COALESCE): `_normalize_record` solo canoniza el
record ENTRANTE; en fondos CACHED el valor entrante es None y COALESCE preserva el
valor STALE de la BD (casing v19 UPPER) sin pasar por el normalizador. Resultado:
casing mixto en columnas categóricas (Hedging_Policy 'UNHEDGED' vs 'Unhedged', etc.).

Este script aplica, sobre TODA la tabla, la MISMA canonicalización que
classify_utils.normalize_casing (config.DOMAIN_VALUES = fuente única) + los remaps
de valor legacy (config.LEGACY_VALUE_REMAP, p.ej. 'PARTIAL'→'Partially Hedged').

Propiedades:
  - DRY: deriva todo de config; no hardcodea vocabularios.
  - Idempotente: re-ejecutar no cambia filas ya canónicas.
  - Solo columnas TITLE con dominio cerrado (no toca flags UPPER_SNAKE/CODE/NUM).
  - Case-insensitive (casefold + colapso de espacios/guiones), igual que el runtime.
  - Reporta filas afectadas por columna; no inventa valores (no-match → intacto).

Uso (Windows, env des):
    python -X utf8 -m proyecto1.core.normalize_db_casing_v20 --db C:\\desarrollo\\fondos\\db\\fondos.sqlite
    python -X utf8 -m proyecto1.core.normalize_db_casing_v20 --db ... --dry-run
"""
import argparse
import re
import sqlite3
import sys

try:
    from shared import config as _cfg
except ImportError:
    import config as _cfg  # type: ignore

try:
    from shared.db import is_postgres_connection, executemany, table_columns
except ModuleNotFoundError:
    # shared no está aún en sys.path — añadirlo explícitamente (mismo patrón que sqlite_writer.py).
    import sys as _sys
    from pathlib import Path as _Path
    _shared_root = _Path(__file__).resolve().parents[2]
    if str(_shared_root) not in _sys.path:
        _sys.path.insert(0, str(_shared_root))
    from shared.db import is_postgres_connection, executemany, table_columns


def _casefold_key(s: str) -> str:
    return re.sub(r"[\s_]+", " ", s.strip().casefold())


def _build_plan():
    """{column: {casefold_key|legacy_value: canonical}} para columnas TITLE cerradas."""
    dv = getattr(_cfg, "DOMAIN_VALUES", {})
    casing = getattr(_cfg, "ATTRIBUTE_CASING", {})
    legacy = getattr(_cfg, "LEGACY_VALUE_REMAP", {})
    plan = {}
    for col, vals in dv.items():
        if casing.get(col) != "TITLE":
            continue
        lookup = {_casefold_key(v): v for v in vals}
        plan[col] = {"casing": lookup, "legacy": legacy.get(col, {})}
    return plan


def _canonical(col_plan, value):
    if value is None:
        return None
    rm = col_plan["legacy"]
    if value in rm:
        value = rm[value]
    return col_plan["casing"].get(_casefold_key(value), value)


def run(db_path: str, dry_run: bool = False, *, conn=None) -> dict:
    """conn: injected connection (Postgres or SQLite) — used by tests and any future dialect-
    aware caller. When None (the CLI entry point's default), behavior is unchanged: opens its own
    SQLite connection against db_path, exactly as before this port (Postgres migration Phase 5c,
    2026-09-20)."""
    plan = _build_plan()
    own_conn = conn is None
    if own_conn:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
    pg = is_postgres_connection(conn)
    ph = "%s" if pg else "?"
    # Postgres folds unquoted-created column names to lowercase (table_columns() docstring) —
    # config.DOMAIN_VALUES keys mirror the original SQLite mixed-case names, so the membership
    # check below must fold case on the Postgres branch only.
    existing = table_columns(conn, "fund_master")
    existing_cmp = {e.lower() for e in existing} if pg else existing
    report = {}
    try:
        for col, col_plan in plan.items():
            if (col.lower() if pg else col) not in existing_cmp:
                continue
            rows = conn.execute(
                f"SELECT ISIN, {col} AS v FROM fund_master "
                f"WHERE {col} IS NOT NULL"
            ).fetchall()
            # Positional unpacking, not name-based row["..."] access — this must work against a
            # bare psycopg3 connection (tuple rows), which is what the pg_conn/pg_conn_module_schema
            # test fixtures hand back, not only against get_connection(backend="postgres")'s
            # sqlite-compat row factory.
            updates = [
                (canon, isin)
                for isin, v in rows
                if (canon := _canonical(col_plan, v)) != v
            ]
            report[col] = len(updates)
            if updates and not dry_run:
                executemany(
                    conn, f"UPDATE fund_master SET {col}={ph} WHERE ISIN={ph}", updates
                )
        if not dry_run:
            conn.commit()
    finally:
        if own_conn:
            conn.close()
    return report


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    rep = run(args.db, args.dry_run)
    mode = "DRY-RUN (sin escribir)" if args.dry_run else "APLICADO"
    print(f"[normalize_db_casing_v20] {mode} sobre {args.db}")
    total = 0
    for col, n in sorted(rep.items()):
        if n:
            print(f"  {col:24} filas corregidas: {n}")
            total += n
    print(f"  TOTAL corregidas: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
