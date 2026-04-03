# -*- coding: utf-8 -*-
"""
scripts/migrate_v3_columns.py
==============================
Migración de base de datos: añade las 5 columnas nuevas del Canonico v3
introducidas por fund_characterizer.py.

Uso:
    cd c:/desarrollo/fondos
    python scripts/migrate_v3_columns.py
    python scripts/migrate_v3_columns.py --dry-run

Las columnas son:
    fund_master.Market_Cap_Focus     TEXT  -- Large Cap | Mid Cap | Small Cap | SMID Cap
    fund_master.Sector_Focus         TEXT  -- Technology & Innovation | Healthcare | ...
    fund_master.Currency_Hedged      TEXT  -- Hedged | Unhedged
    fund_master.Investment_Universe  TEXT  -- Global | Regional | Country | Sector | Thematic | Liquidity
    fund_master.Accumulation_Policy  TEXT  -- ACCUMULATION | DISTRIBUTION

La migración es idempotente — si la columna ya existe, la salta sin error.
"""

import sqlite3
import sys
import argparse
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

NEW_COLUMNS = [
    (
        "Market_Cap_Focus",
        "TEXT",
        "Large Cap | Mid Cap | Small Cap | SMID Cap | NULL",
    ),
    (
        "Sector_Focus",
        "TEXT",
        "Technology & Innovation | Healthcare & Life Sciences | etc.",
    ),
    (
        "Currency_Hedged",
        "TEXT",
        "Hedged | Unhedged | NULL",
    ),
    (
        "Investment_Universe",
        "TEXT",
        "Global | Regional | Country | Sector | Thematic | Liquidity | NULL",
    ),
    (
        "Accumulation_Policy",
        "TEXT",
        "ACCUMULATION | DISTRIBUTION | NULL",
    ),
]


def get_existing_columns(conn: sqlite3.Connection, table: str) -> set:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def migrate(db_path: Path, dry_run: bool = False) -> None:
    print(f"BD: {db_path}")
    print(f"Modo: {'DRY-RUN' if dry_run else 'EJECUTAR'}\n")

    conn = sqlite3.connect(str(db_path))
    existing = get_existing_columns(conn, "fund_master")

    added = 0
    skipped = 0
    for col_name, col_type, comment in NEW_COLUMNS:
        if col_name in existing:
            print(f"  SKIP  {col_name} — ya existe")
            skipped += 1
            continue

        sql = f"ALTER TABLE fund_master ADD COLUMN {col_name} {col_type}"
        print(f"  ADD   {col_name} {col_type}  -- {comment}")
        if not dry_run:
            conn.execute(sql)
            added += 1
        else:
            added += 1  # cuenta en dry-run para el resumen

    if not dry_run:
        conn.commit()

    conn.close()

    print(f"\nResumen: {added} columnas añadidas, {skipped} ya existían")
    if dry_run:
        print("(DRY-RUN: no se escribió nada)")
    else:
        print("Migración completada — verificar con:")
        print("  SELECT Market_Cap_Focus, Sector_Focus, Currency_Hedged")
        print("  FROM fund_master LIMIT 5;")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migración BD v3 — nuevas columnas fund_characterizer")
    parser.add_argument("--db", default=None, help="Ruta a fondos.sqlite")
    parser.add_argument("--dry-run", action="store_true", help="Ver cambios sin escribir")
    args = parser.parse_args()

    db_path = Path(args.db) if args.db else _ROOT / "db" / "fondos.sqlite"
    if not db_path.exists():
        print(f"ERROR: BD no encontrada: {db_path}")
        sys.exit(1)

    migrate(db_path, dry_run=args.dry_run)
