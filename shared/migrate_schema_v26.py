# shared/migrate_schema_v26.py
# -*- coding: utf-8 -*-
"""
Migración idempotente a schema v26.

Cambios v26 (gobernanza — Pillar 3 audit columns):
  - fund_metrics:           + algorithm_version TEXT, + batch_id TEXT
  - fund_metric_timeseries: + algorithm_version TEXT, + batch_id TEXT
  - p2_pipeline_log:        + batch_id TEXT

Cada ALTER TABLE está protegido por un check PRAGMA table_info previo, por
lo que ejecutar este script más de una vez es seguro (idempotente).

Uso (una sola vez, contra la BD de producción):
    cd c:/desarrollo/fondos
    C:\\data\\envs\\des\\python.exe -m shared.migrate_schema_v26

Precaución:
  - Ejecutar ANTES del siguiente ciclo P1 o P2 (assert_schema_alignment lo exige).
  - Hacer una copia de seguridad de db/fondos.sqlite antes de ejecutar en producción.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Path setup — permite ejecución como módulo o script directo
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from shared.db import get_connection


_MIGRATIONS: list[tuple[str, str, str]] = [
    # (tabla, columna, definición SQL)
    ("fund_metrics",            "algorithm_version", "TEXT"),
    ("fund_metrics",            "batch_id",          "TEXT"),
    ("fund_metric_timeseries",  "algorithm_version", "TEXT"),
    ("fund_metric_timeseries",  "batch_id",          "TEXT"),
    ("p2_pipeline_log",         "batch_id",          "TEXT"),
]


def _column_exists(conn, table: str, column: str) -> bool:
    """Devuelve True si la columna ya está presente en la tabla."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def migrate(conn=None) -> dict[str, list[str]]:
    """
    Aplica las migraciones v26. Devuelve dict {tabla: [columnas_añadidas]}.

    Si conn es None abre la conexión de producción.
    """
    close = conn is None
    if conn is None:
        conn = get_connection()

    added: dict[str, list[str]] = {}
    try:
        for table, column, col_def in _MIGRATIONS:
            if _column_exists(conn, table, column):
                print(f"  SKIP  {table}.{column} — ya existe")
                continue
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
            conn.commit()
            added.setdefault(table, []).append(column)
            print(f"  ADD   {table}.{column} {col_def}")
    finally:
        if close:
            conn.close()

    return added


if __name__ == "__main__":
    print("=== Migración schema v26 ===")
    result = migrate()
    if result:
        print("\nColumnas añadidas:")
        for tbl, cols in result.items():
            print(f"  {tbl}: {cols}")
    else:
        print("\nNada que migrar — todas las columnas ya existían.")
    print("=== OK ===")
