# proyecto1/migrate_v20_benchmark_role.py
# -*- coding: utf-8 -*-
"""
Phase 2 — Migración BL-BENCH-ROLE: eje benchmark_role en fund_benchmarks.

Añade la columna `benchmark_role TEXT DEFAULT 'asset_proxy'` (idempotente) y
rellena las filas existentes (ambas fuentes: KIID y MORNINGSTAR) aplicando la
MISMA regla que el runtime (core.benchmark_normalizer.benchmark_role), forzando
el flag durante el backfill — el acto de migrar es la habilitación deliberada
para los datos ya almacenados; el kill-switch sigue gobernando la escritura en
caliente del pipeline.

Idempotente: re-ejecutar recomputa los mismos valores (R-9: sin dependencia de
estado previo; comparaciones tolerantes a padding no aplican aquí porque se
reclasifica el 100% de filas con benchmark_raw no nulo).

ORDEN DE DESPLIEGUE (R-2): ejecutar ESTA migración ANTES de desplegar el
sqlite_writer.py de Phase 2 — el INSERT de _upsert_kiid_benchmark referencia
la columna; sin ella, el INSERT falla (y el except del writer lo silenciaría).

Uso (Windows, env des):
    python -X utf8 proyecto1\\migrate_v20_benchmark_role.py
    python -X utf8 proyecto1\\migrate_v20_benchmark_role.py --db C:\\ruta\\fondos.sqlite
    python -X utf8 proyecto1\\migrate_v20_benchmark_role.py --dry-run
"""

import argparse
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = r"C:\desarrollo\fondos\db\fondos.sqlite"


def _import_role_fn():
    """
    Importa benchmark_role del módulo canónico (fuente única, R-1) y fuerza el
    flag a True para el backfill. Devuelve una función raw -> rol.
    """
    # sys.path: permitir tanto 'core.benchmark_normalizer' como import directo.
    here = Path(__file__).resolve()
    for cand in (here.parents[0], here.parents[0] / "core"):
        if str(cand) not in sys.path:
            sys.path.insert(0, str(cand))
    try:
        import config as _cfg  # noqa
        _cfg.BENCHMARK_ROLE_ENABLED = True  # forzar regla en el backfill
    except Exception:
        pass
    try:
        from core.benchmark_normalizer import benchmark_role
    except Exception:
        from benchmark_normalizer import benchmark_role  # type: ignore
    return benchmark_role


def _column_exists(conn: sqlite3.Connection, table: str, col: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == col for r in rows)


def migrate(db_path: str, dry_run: bool = False) -> dict:
    role_fn = _import_role_fn()
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    stats = {"added_column": False, "rows_total": 0,
             "hurdle": 0, "asset_proxy": 0, "skipped_null_raw": 0}
    try:
        # 1. Añadir columna si no existe (idempotente).
        if not _column_exists(conn, "fund_benchmarks", "benchmark_role"):
            if not dry_run:
                conn.execute(
                    "ALTER TABLE fund_benchmarks "
                    "ADD COLUMN benchmark_role TEXT DEFAULT 'asset_proxy'"
                )
            stats["added_column"] = True

        # 2. Backfill ambas fuentes según la regla canónica.
        rows = conn.execute(
            "SELECT ISIN, source, benchmark_raw FROM fund_benchmarks"
        ).fetchall()
        stats["rows_total"] = len(rows)
        updates = []
        for isin, source, raw in rows:
            if raw is None or not str(raw).strip():
                stats["skipped_null_raw"] += 1
                role = "asset_proxy"
            else:
                role = role_fn(str(raw))
            stats["hurdle" if role == "hurdle_rate" else "asset_proxy"] += 1
            updates.append((role, isin, source))

        if not dry_run:
            conn.executemany(
                "UPDATE fund_benchmarks SET benchmark_role=? "
                "WHERE ISIN=? AND source=?",
                updates,
            )
            conn.commit()
    finally:
        conn.close()
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    s = migrate(args.db, dry_run=args.dry_run)
    tag = "[DRY-RUN] " if args.dry_run else ""
    print(f"{tag}fund_benchmarks.benchmark_role migration")
    print(f"  column added      : {s['added_column']}")
    print(f"  rows total        : {s['rows_total']}")
    print(f"  hurdle_rate       : {s['hurdle']}")
    print(f"  asset_proxy       : {s['asset_proxy']}")
    print(f"  null raw (default): {s['skipped_null_raw']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
