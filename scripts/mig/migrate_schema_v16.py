#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/migrate_schema_v16.py
==============================================
Migración idempotente de esquema a v16.

Columnas nuevas:
  fund_master:
    Market_Cap_Focus    TEXT  -- Large | Mid | Small | Multi
    Sector_Focus        TEXT  -- sector temático granular
    Currency_Hedged     TEXT  -- HEDGED | UNHEDGED | MULTI
    Investment_Universe TEXT  -- Global | Regional | Country | Sector | Thematic | Liquidity

  fund_kiid_metadata:
    Processing_Time_Ms   INTEGER  -- tiempo total proceso fondo en ms
    Processing_Breakdown TEXT     -- desglose por fase: "kiid_fetch:120ms|kiid_parse:3400ms|classify:45ms"

Uso:
    python scripts/migrate_schema_v16.py --dry-run
    python scripts/migrate_schema_v16.py
    python scripts/migrate_schema_v16.py --db c:\\ruta\\fondos.sqlite
"""

import sqlite3
import sys
import argparse
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

MIGRATIONS = [
    # (tabla, columna, tipo, descripción)
    ("fund_master",        "Market_Cap_Focus",    "TEXT",    "Large | Mid | Small | Multi"),
    ("fund_master",        "Sector_Focus",         "TEXT",    "Sector temático granular"),
    ("fund_master",        "Currency_Hedged",      "TEXT",    "HEDGED | UNHEDGED | MULTI"),
    ("fund_master",        "Investment_Universe",  "TEXT",    "Global | Regional | Country | Sector | Thematic | Liquidity"),
    ("fund_kiid_metadata", "Processing_Time_Ms",  "INTEGER", "Tiempo total proceso fondo en milisegundos"),
    ("fund_kiid_metadata", "Processing_Breakdown","TEXT",    "Desglose por fase: kiid_fetch|kiid_parse|classify"),
]


def migrate(db_path: Path, dry_run: bool = False) -> None:
    print(f"BD:   {db_path}")
    print(f"Modo: {'DRY-RUN (sin cambios)' if dry_run else 'EJECUTAR'}\n")

    conn = sqlite3.connect(str(db_path))
    added = skipped = 0

    for table, col, typ, desc in MIGRATIONS:
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if col in existing:
            print(f"  SKIP  {table}.{col:<25s} — ya existe")
            skipped += 1
        else:
            print(f"  ADD   {table}.{col:<25s} {typ:<8s}  -- {desc}")
            if not dry_run:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
                added += 1
            else:
                added += 1   # contamos en dry-run para mostrar el resumen

    if not dry_run:
        conn.commit()

    conn.close()

    print(f"\nResumen: {added} columnas añadidas, {skipped} ya existían")
    if dry_run:
        print("(DRY-RUN: no se ha modificado la BD)")
    else:
        print("\nVerifica con:")
        print("  SELECT name FROM pragma_table_info('fund_master')")
        print("    WHERE name IN ('Market_Cap_Focus','Sector_Focus','Currency_Hedged','Investment_Universe');")
        print("  SELECT name FROM pragma_table_info('fund_kiid_metadata')")
        print("    WHERE name IN ('Processing_Time_Ms','Processing_Breakdown');")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migración schema v16")
    parser.add_argument("--db",      default=None, help="Ruta a fondos.sqlite")
    parser.add_argument("--dry-run", action="store_true", help="Mostrar cambios sin aplicar")
    args = parser.parse_args()

    db_path = Path(args.db) if args.db else _ROOT / "db" / "fondos.sqlite"
    if not db_path.exists():
        print(f"ERROR: BD no encontrada: {db_path}")
        sys.exit(1)

    migrate(db_path, dry_run=args.dry_run)
