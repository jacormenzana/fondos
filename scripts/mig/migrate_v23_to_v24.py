"""
scripts/mig/migrate_v23_to_v24.py
==================================
Migración de schema v23 → v24.

Cambios:
  - Crea fund_nav_monthly si no existe (era creada fuera del schema canónico).
  - Crea fund_nav_daily (NUEVA): serie diaria para métricas de horizonte corto
    (rolling_1m / rolling_3m / rolling_6m, metric_version='d1').

Idempotente: usa CREATE TABLE IF NOT EXISTS; seguro de re-ejecutar.

Uso:
    python scripts/mig/migrate_v23_to_v24.py
    python scripts/mig/migrate_v23_to_v24.py --dry-run
"""

import argparse
import sqlite3
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))
from shared.config import DB_PATH

_MIGRATIONS: list[tuple[str, str]] = [
    # (descripción, SQL idempotente)
    (
        "Crear fund_nav_monthly si no existe",
        """
        CREATE TABLE IF NOT EXISTS fund_nav_monthly (
            ISIN            TEXT    NOT NULL,
            Date            DATE    NOT NULL,
            NAV             REAL    NOT NULL,
            NAV_Currency    TEXT,
            NAV_Type        TEXT    DEFAULT 'NAV',
            Is_Estimated    INTEGER DEFAULT 0,
            Data_Source     TEXT,
            Ingested_At     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (ISIN, Date),
            FOREIGN KEY (ISIN) REFERENCES fund_master (ISIN) ON DELETE CASCADE
        )
        """,
    ),
    (
        "Índice ISIN en fund_nav_monthly",
        "CREATE INDEX IF NOT EXISTS idx_nav_monthly_isin ON fund_nav_monthly (ISIN)",
    ),
    (
        "Índice Date en fund_nav_monthly",
        "CREATE INDEX IF NOT EXISTS idx_nav_monthly_date ON fund_nav_monthly (Date)",
    ),
    (
        "Crear fund_nav_daily (NUEVA v24)",
        """
        CREATE TABLE IF NOT EXISTS fund_nav_daily (
            ISIN            TEXT    NOT NULL,
            Date            DATE    NOT NULL,
            NAV             REAL    NOT NULL,
            NAV_Currency    TEXT,
            NAV_Type        TEXT    DEFAULT 'TOTAL_RETURN_IDX',
            Is_Estimated    INTEGER DEFAULT 0,
            Data_Source     TEXT,
            Ingested_At     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (ISIN, Date),
            FOREIGN KEY (ISIN) REFERENCES fund_master (ISIN) ON DELETE CASCADE
        )
        """,
    ),
    (
        "Índice ISIN en fund_nav_daily",
        "CREATE INDEX IF NOT EXISTS idx_nav_daily_isin ON fund_nav_daily (ISIN)",
    ),
    (
        "Índice Date en fund_nav_daily",
        "CREATE INDEX IF NOT EXISTS idx_nav_daily_date ON fund_nav_daily (Date)",
    ),
]


def main(dry_run: bool = False) -> None:
    print(f"\n{'='*60}")
    print(f"  migrate_v23_to_v24 | BD: {DB_PATH}")
    print(f"  dry_run: {dry_run}")
    print(f"{'='*60}\n")

    if dry_run:
        print("  [DRY-RUN] Migraciones que se aplicarían:\n")
        for desc, sql in _MIGRATIONS:
            print(f"  • {desc}")
            print(f"    {sql.strip()[:80]}...")
        print(f"\n  Total: {len(_MIGRATIONS)} operaciones")
        return

    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")

    applied = 0
    for desc, sql in _MIGRATIONS:
        try:
            conn.execute(sql.strip())
            conn.commit()
            print(f"  OK  {desc}")
            applied += 1
        except sqlite3.OperationalError as e:
            print(f"  !  {desc} — ya existe o error: {e}")

    conn.close()
    print(f"\n  {applied}/{len(_MIGRATIONS)} operaciones aplicadas.")

    # Verificación post-migración
    print("\n  Verificando schema v24...")
    from shared.schema_checks import check_schema_v24
    conn2 = sqlite3.connect(str(DB_PATH), timeout=30)
    result = check_schema_v24(conn2)
    conn2.close()
    if result['ok']:
        print("  OK  Schema v24 validado correctamente.")
    else:
        print("  FAIL  Issues encontrados:")
        for issue in result['issues']:
            print(f"     {issue}")
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migración schema v23 → v24")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
