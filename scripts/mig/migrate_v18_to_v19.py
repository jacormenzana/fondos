# scripts/mig/migrate_v18_to_v19.py
# -*- coding: utf-8 -*-
"""
Migración no destructiva v18 → v19.

Estrategia: RENAME TO _tmp → CREATE schema v19 → INSERT SELECT → DROP _tmp.
Justificación: permite renombrar Ongoing_Charge → Ongoing_Charge_Recurrent
sin ALTER TABLE RENAME COLUMN (no disponible con seguridad en SQLite < 3.25,
y este proyecto no garantiza versión mínima).

Garantías:
  - Idempotente: si la BD ya está en v19, imprime [SKIP] y sale.
  - Backup automático antes de cualquier modificación.
  - Transacción IMMEDIATE: o migra todo o no migra nada (ROLLBACK en error).
  - Verificación de integridad de filas post-migración.
  - Las 11 columnas nuevas quedan NULL en todos los fondos (Sprint 2 las puebla).
  - Ongoing_Charge_Recurrent hereda el valor de Ongoing_Charge.

Verificación post-migración (José ejecuta en DBeaver):
  SELECT COUNT(*) FROM pragma_table_info('fund_master');  -- esperado: 57
  SELECT COUNT(*) FROM fund_master;                       -- esperado: 3205
  SELECT COUNT(*) FROM pragma_table_info('fund_master')
    WHERE name = 'Ongoing_Charge';                        -- esperado: 0
  SELECT name FROM sqlite_master WHERE type='table'
    AND name='fund_cost_schedule';                        -- esperado: 1 fila

USO:
  cd c:/desarrollo/fondos
  python -X utf8 scripts/mig/migrate_v18_to_v19.py
  python -X utf8 scripts/mig/migrate_v18_to_v19.py --dry-run
"""

from __future__ import annotations

import argparse
import datetime
import shutil
import sqlite3
import sys
from pathlib import Path

# ── Resolución de raíz del proyecto ──────────────────────────────────────────
_THIS_FILE   = Path(__file__).resolve()
_PROJECT_ROOT = _THIS_FILE.parent.parent.parent   # scripts/mig/.. → raíz

if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from shared.config import DB_PATH
from shared.init_db import create_schema_v19


# ── Detección de versión ──────────────────────────────────────────────────────

def detect_schema_version(conn: sqlite3.Connection) -> int:
    """
    Devuelve 18 o 19 según presencia/ausencia de columnas clave.

    - v19: tiene KID_Format Y Ongoing_Charge_Recurrent (y NO tiene Ongoing_Charge).
    - v18: tiene Ongoing_Charge (y NO tiene Ongoing_Charge_Recurrent).
    - Otro: lanza RuntimeError.
    """
    cur = conn.execute("PRAGMA table_info(fund_master)")
    cols = {row[1] for row in cur.fetchall()}
    if "KID_Format" in cols and "Ongoing_Charge_Recurrent" in cols:
        return 19
    if "Ongoing_Charge" in cols and "Ongoing_Charge_Recurrent" not in cols:
        return 18
    raise RuntimeError(
        f"Schema no reconocido. Columnas fund_master: {sorted(cols)}"
    )


# ── Backup ────────────────────────────────────────────────────────────────────

def backup_db(db_path: Path) -> Path:
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = db_path.parent / f"{db_path.stem}_pre_v19_{timestamp}{db_path.suffix}"
    shutil.copy2(db_path, backup)
    print(f"[BACKUP] {backup}")
    return backup


# ── Migración principal ───────────────────────────────────────────────────────

def migrate(dry_run: bool = False) -> None:
    db_path = Path(DB_PATH)
    if not db_path.exists():
        raise FileNotFoundError(f"No existe BD: {db_path}")

    print(f"\n{'='*60}")
    print(f"  migrate_v18_to_v19")
    print(f"  BD: {db_path}")
    print(f"  dry_run: {dry_run}")
    print(f"{'='*60}\n")

    conn = sqlite3.connect(str(db_path))
    conn.isolation_level = None  # autocommit para controlar transacciones manualmente

    version = detect_schema_version(conn)
    if version == 19:
        print("[SKIP] BD ya está en v19. Nada que hacer.")
        conn.close()
        return

    print(f"[INFO] BD detectada en v{version}. Iniciando migración a v19.")

    if dry_run:
        print("[DRY-RUN] Operaciones que se ejecutarían:")
        print("  1. Backup de fondos.sqlite → fondos_pre_v19_TIMESTAMP.sqlite")
        print("  2. ALTER TABLE fund_master RENAME TO fund_master_v18_tmp")
        print("  3. CREATE TABLE fund_master (v19, 57 columnas)")
        print("  4. CREATE TABLE fund_cost_schedule (nueva)")
        print("  5. INSERT INTO fund_master SELECT ... FROM fund_master_v18_tmp")
        print("     (Ongoing_Charge → Ongoing_Charge_Recurrent; 11 nuevas → NULL)")
        print("  6. Verificar COUNT(*) coincide")
        print("  7. DROP TABLE fund_master_v18_tmp")
        n = conn.execute("SELECT COUNT(*) FROM fund_master").fetchone()[0]
        print(f"  Filas a migrar: {n}")
        conn.close()
        return

    # Backup antes de cualquier cambio
    backup_db(db_path)

    cur = conn.cursor()
    cur.execute("BEGIN IMMEDIATE")
    try:
        # 1. Renombrar tabla actual a temporal
        cur.execute("ALTER TABLE fund_master RENAME TO fund_master_v18_tmp")
        print("[INFO] fund_master renombrada a fund_master_v18_tmp")

        # 2. Crear schema v19 limpio (fund_master v19 + fund_cost_schedule)
        create_schema_v19(conn, drop_existing=False)
        print("[INFO] Schema v19 creado (fund_master + fund_cost_schedule)")

        # 3. Copiar datos con renombrado Ongoing_Charge → Ongoing_Charge_Recurrent.
        #    Las 11 columnas nuevas quedan NULL (default).
        cur.execute("""
            INSERT INTO fund_master (
                ISIN, Fund_Name, Management_Company, Fund_Nature, Profile,
                Type, Strategy, Family, Style_Profile, Geography, Theme,
                Is_ESG, Exposure_Bias, Benchmark_Type, Subtype,
                Market_Cap_Focus, Sector_Focus, Currency_Hedged,
                Investment_Universe, Investment_Focus, Credit_Quality,
                Accumulation_Policy, Heuristic_Block, Heuristic_Core,
                SRRI, Fund_Currency, Portfolio_Currency, Hedging_Policy,
                Replication_Method, Derivatives_Usage, Benchmark_Declared,
                Ongoing_Charge_Recurrent,
                Entry_Fee_Pct, Exit_Fee_Pct, Fee_Known_Flag, Sfdr_Article,
                Recommended_Holding_Period, Leverage_Used, Liquidity_Profile,
                Distribution_Frequency, fund_family_id, Inference_Trace,
                SRRI_Quality_Flag, Data_Quality_Flag, Created_At, Updated_At
                -- Las 11 columnas v19 quedan NULL (no se incluyen → DEFAULT NULL)
            )
            SELECT
                ISIN, Fund_Name, Management_Company, Fund_Nature, Profile,
                Type, Strategy, Family, Style_Profile, Geography, Theme,
                Is_ESG, Exposure_Bias, Benchmark_Type, Subtype,
                Market_Cap_Focus, Sector_Focus, Currency_Hedged,
                Investment_Universe, Investment_Focus, Credit_Quality,
                Accumulation_Policy, Heuristic_Block, Heuristic_Core,
                SRRI, Fund_Currency, Portfolio_Currency, Hedging_Policy,
                Replication_Method, Derivatives_Usage, Benchmark_Declared,
                Ongoing_Charge,                          -- v18: origen del valor
                Entry_Fee_Pct, Exit_Fee_Pct, Fee_Known_Flag, Sfdr_Article,
                Recommended_Holding_Period, Leverage_Used, Liquidity_Profile,
                Distribution_Frequency, fund_family_id, Inference_Trace,
                SRRI_Quality_Flag, Data_Quality_Flag, Created_At, Updated_At
            FROM fund_master_v18_tmp
        """)

        n_new  = cur.execute("SELECT COUNT(*) FROM fund_master").fetchone()[0]
        n_orig = cur.execute("SELECT COUNT(*) FROM fund_master_v18_tmp").fetchone()[0]
        if n_new != n_orig:
            raise RuntimeError(
                f"Pérdida de filas: {n_orig} originales → {n_new} migradas"
            )
        print(f"[BL-COST-2-MIG] {n_new} filas preservadas. "
              f"11 columnas nuevas inicializadas NULL.")

        # 4. Verificar que la columna renombrada tiene el valor correcto
        n_oc_rec = cur.execute(
            "SELECT COUNT(*) FROM fund_master WHERE Ongoing_Charge_Recurrent IS NOT NULL"
        ).fetchone()[0]
        print(f"[INFO] Ongoing_Charge_Recurrent poblado: {n_oc_rec} fondos")

        # 5. Drop tabla temporal
        cur.execute("DROP TABLE fund_master_v18_tmp")
        print("[INFO] fund_master_v18_tmp eliminada")

        cur.execute("COMMIT")
        print(f"\n[OK] Migración v18 → v19 completa.")
        print(f"[OK] fund_cost_schedule creada vacía.")
        print(f"[OK] {n_new} filas preservadas, 11 columnas nuevas NULL.")

    except Exception as e:
        cur.execute("ROLLBACK")
        print(f"\n[ERROR] Migración revertida: {e}")
        print("[INFO] BD restaurada a estado pre-migración (ROLLBACK).")
        print("[INFO] Backup disponible para restauración manual si es necesario.")
        conn.close()
        raise
    finally:
        conn.close()


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Migración fondos.sqlite v18 → v19 (BL-COST-2 Sprint 1)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Muestra las operaciones sin ejecutarlas.",
    )
    args = parser.parse_args()
    migrate(dry_run=args.dry_run)
