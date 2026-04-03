# -*- coding: utf-8 -*-
"""
scripts/migrate_processing_time.py
=====================================
Añade columnas de telemetría de proceso a fund_kiid_metadata.

Columnas nuevas:
    Processing_Time_Ms    INTEGER  -- tiempo total de proceso del fondo en ms
    Processing_Breakdown  TEXT     -- desglose por fase: "kiid_fetch:120ms|kiid_parse:3400ms|classify:45ms"

Uso:
    python scripts/migrate_processing_time.py --dry-run
    python scripts/migrate_processing_time.py

La migración es idempotente.
"""

import sqlite3, sys, argparse
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

NEW_COLUMNS = [
    ("Processing_Time_Ms",   "INTEGER", "Tiempo total proceso fondo en milisegundos"),
    ("Processing_Breakdown", "TEXT",    "Desglose por fase: kiid_fetch|kiid_parse|classify"),
]


def migrate(db_path: Path, dry_run: bool = False) -> None:
    print(f"BD: {db_path}")
    print(f"Modo: {'DRY-RUN' if dry_run else 'EJECUTAR'}\n")

    conn = sqlite3.connect(str(db_path))
    existing = {r[1] for r in conn.execute("PRAGMA table_info(fund_kiid_metadata)").fetchall()}

    added = skipped = 0
    for col, typ, comment in NEW_COLUMNS:
        if col in existing:
            print(f"  SKIP  {col} — ya existe")
            skipped += 1
            continue
        print(f"  ADD   {col} {typ}  -- {comment}")
        if not dry_run:
            conn.execute(f"ALTER TABLE fund_kiid_metadata ADD COLUMN {col} {typ}")
            added += 1
        else:
            added += 1

    if not dry_run:
        conn.commit()
    conn.close()

    print(f"\nResumen: {added} columnas añadidas, {skipped} ya existían")
    if dry_run:
        print("(DRY-RUN)")
    else:
        print("Verificar con:")
        print("  SELECT ISIN, Processing_Time_Ms, Processing_Breakdown")
        print("  FROM fund_kiid_metadata")
        print("  WHERE Processing_Time_Ms IS NOT NULL")
        print("  ORDER BY Processing_Time_Ms DESC LIMIT 20;")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    db_path = Path(args.db) if args.db else _ROOT / "db" / "fondos.sqlite"
    if not db_path.exists():
        print(f"ERROR: BD no encontrada: {db_path}"); sys.exit(1)

    migrate(db_path, dry_run=args.dry_run)
