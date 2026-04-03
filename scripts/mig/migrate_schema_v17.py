# -*- coding: utf-8 -*-
"""
migrate_schema_v17.py  —  Migración idempotente de esquema v16 → v17

Cambios v17:
  1. fund_master: ADD COLUMN Investment_Focus TEXT
     Dimensión de exposición ortogonal a Investment_Universe:
     Broad | Sector | Thematic
     (Investment_Universe pasa a ser puramente geográfico)

  2. fund_master: ADD COLUMN Credit_Quality TEXT
     Calidad crediticia de la cartera para fondos RF/Mixtos:
     Investment Grade | High Yield | Mixed | No aplica

  3. fund_master: ADD COLUMN Fee_Known_Flag TEXT
     Estado de extracción de la comisión de entrada:
     EXTRACTED | ZERO_CONFIRMED | NOT_FOUND

  4. BACKFILL: Investment_Focus desde Investment_Universe actual
     Los registros con Investment_Universe IN ('Sector','Thematic')
     se migran a Investment_Focus y se corrige Investment_Universe
     al valor geográfico correspondiente (desde Geography).

Uso:
    python migrate_schema_v17.py --db <ruta_db> [--dry-run]

Requiere: Python 3.8+, sqlite3 (stdlib)
"""

import argparse
import sqlite3
import sys
import textwrap
from datetime import datetime


# ─── COLUMNAS NUEVAS ──────────────────────────────────────────────────────────

NEW_COLUMNS = [
    ("Investment_Focus", "TEXT"),
    ("Credit_Quality",   "TEXT"),
    ("Fee_Known_Flag",   "TEXT"),
]

# ─── BACKFILL INVESTMENT_FOCUS desde Investment_Universe existente ─────────────
# Los valores 'Sector' y 'Thematic' actuales de Investment_Universe
# se traducen directamente a Investment_Focus.
# Luego se corrige Investment_Universe con la Geography de cada fondo.

SQL_BACKFILL_FOCUS = """
UPDATE fund_master
SET Investment_Focus = CASE
    WHEN Investment_Universe = 'Sector'   THEN 'Sector'
    WHEN Investment_Universe = 'Thematic' THEN 'Thematic'
    ELSE NULL
END
WHERE Investment_Focus IS NULL
  AND Investment_Universe IN ('Sector', 'Thematic');
"""

# Para los fondos que ya tenían IU geográfico y no tienen Theme,
# Investment_Focus = 'Broad' (mercado amplio sin restricción).
SQL_BACKFILL_BROAD = """
UPDATE fund_master
SET Investment_Focus = 'Broad'
WHERE Investment_Focus IS NULL
  AND Investment_Universe IN ('Global', 'Regional', 'Country')
  AND (Theme IS NULL OR Theme = 'Core/General')
  AND Fund_Nature NOT IN ('Monetario', 'Renta Fija Corto Plazo');
"""

# Corregir Investment_Universe: los valores Sector/Thematic pasan
# a valor geográfico derivado de Geography.
SQL_FIX_IU = """
UPDATE fund_master
SET Investment_Universe = CASE
    WHEN Geography IN ('China', 'Japón', 'India', 'Latinoamérica',
                       'Europa del Este', 'Rusia')        THEN 'Country'
    WHEN Geography IN ('Europa', 'Asia', 'Emergentes',
                       'EEUU', 'Asia-Pacífico')           THEN 'Regional'
    WHEN Geography = 'Global'                              THEN 'Global'
    WHEN Fund_Nature IN ('Monetario',
                         'Renta Fija Corto Plazo')         THEN 'Liquidity'
    ELSE NULL
END
WHERE Investment_Universe IN ('Sector', 'Thematic');
"""

# Fee_Known_Flag: retroactivo para registros con Entry_Fee_Pct ya extraído.
# Los NULL quedan sin flag (NOT_FOUND se asignará en el próximo ciclo de pipeline).
SQL_BACKFILL_FEE_FLAG = """
UPDATE fund_master
SET Fee_Known_Flag = CASE
    WHEN Entry_Fee_Pct IS NOT NULL AND Entry_Fee_Pct = 0.0 THEN 'ZERO_CONFIRMED'
    WHEN Entry_Fee_Pct IS NOT NULL                         THEN 'EXTRACTED'
    ELSE NULL
END
WHERE Fee_Known_Flag IS NULL;
"""

# Credit_Quality: retroactivo — monetarios siempre IG.
SQL_BACKFILL_CREDIT = """
UPDATE fund_master
SET Credit_Quality = 'Investment Grade'
WHERE Credit_Quality IS NULL
  AND Fund_Nature = 'Monetario';
"""

# RV: Credit_Quality = 'No aplica'
SQL_BACKFILL_CREDIT_RV = """
UPDATE fund_master
SET Credit_Quality = 'No aplica'
WHERE Credit_Quality IS NULL
  AND Fund_Nature = 'Renta Variable';
"""


# ─── HELPERS ─────────────────────────────────────────────────────────────────

def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())


def run(db_path: str, dry_run: bool = False) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")

    changes: list[str] = []

    # 1. Añadir columnas nuevas (idempotente)
    for col_name, col_type in NEW_COLUMNS:
        if column_exists(conn, "fund_master", col_name):
            print(f"  [SKIP] fund_master.{col_name} ya existe")
        else:
            sql = f"ALTER TABLE fund_master ADD COLUMN {col_name} {col_type}"
            changes.append(sql)
            if not dry_run:
                conn.execute(sql)
                print(f"  [ADD]  fund_master.{col_name} {col_type}")
            else:
                print(f"  [DRY]  {sql}")

    # 2. Backfill Investment_Focus desde IU actual
    backfills = [
        ("Backfill Investment_Focus (Sector/Thematic)",      SQL_BACKFILL_FOCUS),
        ("Backfill Investment_Focus (Broad — no temáticos)", SQL_BACKFILL_BROAD),
        ("Corregir Investment_Universe (eliminar Sector/Thematic)", SQL_FIX_IU),
        ("Backfill Fee_Known_Flag desde Entry_Fee_Pct",      SQL_BACKFILL_FEE_FLAG),
        ("Backfill Credit_Quality (Monetario → IG)",         SQL_BACKFILL_CREDIT),
        ("Backfill Credit_Quality (RV → No aplica)",         SQL_BACKFILL_CREDIT_RV),
    ]

    for label, sql in backfills:
        if dry_run:
            print(f"  [DRY]  {label}")
            print(textwrap.indent(sql.strip(), "         "))
        else:
            cur = conn.execute(sql)
            print(f"  [SQL]  {label} → {cur.rowcount} filas actualizadas")

    if not dry_run:
        conn.commit()
        print(f"\n✓ Migración v17 completada — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        # Verificación post-migración
        _verify(conn)
    else:
        print("\n[DRY-RUN] Sin cambios aplicados.")

    conn.close()


def _verify(conn: sqlite3.Connection) -> None:
    print("\n── Verificación post-migración ──────────────────────────────")

    # Columnas nuevas presentes
    for col, _ in NEW_COLUMNS:
        exists = column_exists(conn, "fund_master", col)
        status = "✓" if exists else "✗ FALTA"
        print(f"  {status}  fund_master.{col}")

    # Distribución Investment_Universe (no debe haber Sector ni Thematic)
    print("\n  Investment_Universe tras migración:")
    for row in conn.execute(
        "SELECT Investment_Universe, COUNT(*) n FROM fund_master "
        "GROUP BY Investment_Universe ORDER BY n DESC"
    ).fetchall():
        print(f"    {row[0] or 'NULL':20s} {row[1]:5d}")

    # Distribución Investment_Focus nuevo
    print("\n  Investment_Focus (nuevo):")
    for row in conn.execute(
        "SELECT Investment_Focus, COUNT(*) n FROM fund_master "
        "GROUP BY Investment_Focus ORDER BY n DESC"
    ).fetchall():
        print(f"    {row[0] or 'NULL':20s} {row[1]:5d}")

    # Fee_Known_Flag
    print("\n  Fee_Known_Flag:")
    for row in conn.execute(
        "SELECT Fee_Known_Flag, COUNT(*) n FROM fund_master "
        "GROUP BY Fee_Known_Flag ORDER BY n DESC"
    ).fetchall():
        print(f"    {row[0] or 'NULL':20s} {row[1]:5d}")

    # SQL de control: no deben quedar Sector/Thematic en Investment_Universe
    bad = conn.execute(
        "SELECT COUNT(*) FROM fund_master "
        "WHERE Investment_Universe IN ('Sector','Thematic')"
    ).fetchone()[0]
    print(f"\n  SQL control — IU Sector/Thematic restantes: {bad} "
          f"{'✓' if bad == 0 else '✗ ERROR'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migración schema v16 → v17")
    parser.add_argument("--db",      required=True, help="Ruta al fichero SQLite")
    parser.add_argument("--dry-run", action="store_true",
                        help="Mostrar cambios sin ejecutarlos")
    args = parser.parse_args()

    print(f"{'[DRY-RUN] ' if args.dry_run else ''}Migrando {args.db} → v17\n")
    try:
        run(args.db, dry_run=args.dry_run)
    except Exception as exc:
        print(f"\n✗ Error: {exc}", file=sys.stderr)
        sys.exit(1)
