#!/usr/bin/env python
# -*- coding: utf-8 -*-
# scripts/mig/migrate_v19_to_v20.py  (INTEGRATED_SPEC_v20_v3 §2, §6 Commit 1)
"""
Migración de schema v19(.2) → v20.

DOS partes con gates independientes:

  ── Job B (DESPLEGABLE YA) — `migrate_job_b(conn)` ───────────────────────────
     Aditivo, sin rebuild. Añade a fund_kiid_metadata las 6 columnas de
     arbitración de coste (Cost_Mgmt_* / Cost_Oper_*) y crea la vista
     v_cost_arbitration_overall. Idempotente.

  ── Job A (PENDIENTE) — `rebuild_fund_master_v20(conn)` ──────────────────────
     Rebuild de fund_master (57 → 58): −4 DELETE, +5 CREATE. Usa el idiom
     seguro (FK off, tabla _new, copy, drop, rename, recrear índices + FK).
     También corrige el hueco de rebuild-FK de migrate_v18_to_v19.
     **NO se ejecuta desde main() por defecto.** Requiere completar antes:
       (a) value-sets de las 5 columnas nuevas + 14 remaps (approved inventory),
       (b) la lógica del clasificador que las puebla,
       (c) neutralizar las referencias a columnas borradas (Subtype/
           Currency_Hedged/...) en los normalizadores de sqlite_writer.
     Hasta entonces las 5 nuevas quedan NULL (igual patrón que las 11 de v19).
     Lánzalo explícitamente con  --rebuild-fund-master  cuando (a)(b)(c) estén.

Uso:
    python -X utf8 -m scripts.mig.migrate_v19_to_v20            # Job B
    python -X utf8 scripts/mig/migrate_v19_to_v20.py --dry-run  # plan
    python -X utf8 scripts/mig/migrate_v19_to_v20.py --rebuild-fund-master
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


# ── Conexión: reutilizar shared.db si está; si no, sqlite3 sobre config.DB_PATH ──
def _get_connection() -> sqlite3.Connection:
    for mod in ("shared.db", "db", "core._db_utils"):
        try:
            m = __import__(mod, fromlist=["get_connection"])
            if hasattr(m, "get_connection"):
                return m.get_connection()
        except Exception:
            continue
    for mod in ("shared.config", "config"):
        try:
            m = __import__(mod, fromlist=["DB_PATH"])
            conn = sqlite3.connect(str(m.DB_PATH))
            conn.execute("PRAGMA journal_mode=WAL")
            return conn
        except Exception:
            continue
    raise RuntimeError("No se pudo obtener conexión (ni shared.db ni config.DB_PATH).")


def _columns(conn, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


# ── Job B — aditivo, idempotente ─────────────────────────────────────────────
_JOB_B_COLUMNS = [
    ("Cost_Mgmt_BandsX", "REAL"),
    ("Cost_Mgmt_Ruled",  "REAL"),
    ("Cost_Mgmt_Arbitration", "TEXT CHECK (Cost_Mgmt_Arbitration IS NULL OR "
        "Cost_Mgmt_Arbitration IN ('AGREE','OCR_RECOVERED','BOTH_FAIL',"
        "'ONLY_BANDS_X','ONLY_RULED','CONFLICT'))"),
    ("Cost_Oper_BandsX", "REAL"),
    ("Cost_Oper_Ruled",  "REAL"),
    ("Cost_Oper_Arbitration", "TEXT CHECK (Cost_Oper_Arbitration IS NULL OR "
        "Cost_Oper_Arbitration IN ('AGREE','OCR_RECOVERED','BOTH_FAIL',"
        "'ONLY_BANDS_X','ONLY_RULED','CONFLICT'))"),
]

_VIEW_SQL = """
CREATE VIEW v_cost_arbitration_overall AS
SELECT ISIN,
  CASE
    WHEN Cost_Mgmt_Arbitration IS NULL OR Cost_Oper_Arbitration IS NULL THEN NULL
    WHEN 'CONFLICT'      IN (Cost_Mgmt_Arbitration, Cost_Oper_Arbitration) THEN 'CONFLICT'
    WHEN 'BOTH_FAIL'     IN (Cost_Mgmt_Arbitration, Cost_Oper_Arbitration) THEN 'BOTH_FAIL'
    WHEN 'OCR_RECOVERED' IN (Cost_Mgmt_Arbitration, Cost_Oper_Arbitration) THEN 'OCR_RECOVERED'
    WHEN 'ONLY_BANDS_X'  IN (Cost_Mgmt_Arbitration, Cost_Oper_Arbitration) THEN 'ONLY_BANDS_X'
    WHEN 'ONLY_RULED'    IN (Cost_Mgmt_Arbitration, Cost_Oper_Arbitration) THEN 'ONLY_RULED'
    ELSE 'AGREE'
  END AS Cost_Arbitration_Overall
FROM fund_kiid_metadata WHERE KIID_Class = 1;
"""


def migrate_job_b(conn, dry_run: bool = False) -> dict:
    """Añade las 6 columnas de coste + la vista. Idempotente."""
    existing = _columns(conn, "fund_kiid_metadata")
    added, skipped = [], []
    for name, decl in _JOB_B_COLUMNS:
        if name in existing:
            skipped.append(name)
            continue
        stmt = f"ALTER TABLE fund_kiid_metadata ADD COLUMN {name} {decl}"
        if dry_run:
            print(f"[DRY] {stmt}")
        else:
            conn.execute(stmt)
        added.append(name)

    view_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='view' "
        "AND name='v_cost_arbitration_overall'"
    ).fetchone() is not None
    if dry_run:
        print("[DRY] CREATE VIEW v_cost_arbitration_overall (drop+create)")
    else:
        conn.execute("DROP VIEW IF EXISTS v_cost_arbitration_overall")
        conn.execute(_VIEW_SQL)
    if not dry_run:
        conn.commit()
    return {"added": added, "skipped": skipped, "view_recreated": True,
            "view_existed": view_exists}


# ── Job A — rebuild de fund_master (GUARDADO; ver docstring) ─────────────────
_FUND_MASTER_V20_DDL = """
CREATE TABLE fund_master_new (
    ISIN                    TEXT PRIMARY KEY,
    Fund_Name               TEXT NOT NULL,
    Management_Company      TEXT,
    Fund_Nature             TEXT NOT NULL,
    Profile                 TEXT,
    -- v3 §8-bis Q2: Type → Vehicle_Structure (forma jurídico-estructural del
    -- vehículo). Repropuesta: los valores antiguos NO se copian; los puebla el
    -- reprocess (approved inventory §2A.1 fila 1).
    Vehicle_Structure       TEXT,
    Family                  TEXT,
    Style_Profile           TEXT,
    Geography               TEXT,
    Theme                   TEXT,
    Exposure_Bias           TEXT,
    -- (DROP v20: Subtype)
    Heuristic_Block         TEXT NOT NULL,
    Heuristic_Core          INTEGER NOT NULL CHECK (Heuristic_Core IN (0, 1)),
    SRRI                    INTEGER,
    Fund_Currency           TEXT,
    -- (DROP v20: Portfolio_Currency)
    Hedging_Policy          TEXT,
    Replication_Method      TEXT,
    Derivatives_Usage       TEXT,
    Benchmark_Declared      TEXT,
    Inference_Trace         TEXT,
    SRRI_Quality_Flag       TEXT
        CHECK (SRRI_Quality_Flag IN ('HIGH','MEDIUM_VISUAL','MEDIUM_TEXT','LOW_CONFLICT','NONE')),
    Data_Quality_Flag       TEXT,
    Created_At              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    Updated_At              TIMESTAMP,
    Ongoing_Charge_Recurrent  REAL,
    fund_family_id          TEXT,
    Strategy                TEXT,
    -- (DROP v20: Is_ESG)
    Benchmark_Type          TEXT,
    Accumulation_Policy     TEXT,
    Entry_Fee_Pct           REAL,
    Exit_Fee_Pct            REAL,
    Sfdr_Article            INTEGER,
    -- v3 §6-bis #3: TEXT→INTEGER (años; los códigos '1Y/3Y…' no son ordenables).
    -- El valor antiguo NO se copia; lo puebla el reprocess.
    Recommended_Holding_Period INTEGER,
    Leverage_Used           TEXT,
    Liquidity_Profile       TEXT,
    Distribution_Frequency  TEXT,
    Market_Cap_Focus        TEXT,
    Sector_Focus            TEXT,
    -- (DROP v20: Currency_Hedged)
    Investment_Universe     TEXT,
    Investment_Focus        TEXT,
    Credit_Quality          TEXT,
    Fee_Known_Flag          TEXT,
    KID_Format              TEXT
        CHECK (KID_Format IN ('UCITS_KIID','PRIIPS_KID','UNKNOWN')),
    KID_Currency            TEXT,
    Cost_Extraction_Quality TEXT
        CHECK (Cost_Extraction_Quality IN (
               'HIGH','MEDIUM_CROSS','MEDIUM_EUR','MEDIUM_PCT','LOW','NONE')),
    Cost_RHP_Years          REAL CHECK (Cost_RHP_Years IS NULL OR (Cost_RHP_Years > 0 AND Cost_RHP_Years <= 50)),
    Entry_Fee_Pct_Max       REAL CHECK (Entry_Fee_Pct_Max IS NULL OR (Entry_Fee_Pct_Max >= 0 AND Entry_Fee_Pct_Max <= 25)),
    Exit_Fee_Pct_Max        REAL CHECK (Exit_Fee_Pct_Max IS NULL OR (Exit_Fee_Pct_Max >= 0 AND Exit_Fee_Pct_Max <= 25)),
    Management_Fee_Pct      REAL CHECK (Management_Fee_Pct IS NULL OR (Management_Fee_Pct >= 0 AND Management_Fee_Pct <= 10)),
    Transaction_Cost_Pct    REAL CHECK (Transaction_Cost_Pct IS NULL OR (Transaction_Cost_Pct >= 0 AND Transaction_Cost_Pct <= 5)),
    Performance_Fee_Pct     REAL CHECK (Performance_Fee_Pct IS NULL OR (Performance_Fee_Pct >= 0 AND Performance_Fee_Pct <= 30)),
    ACI_1Y                  REAL CHECK (ACI_1Y IS NULL OR (ACI_1Y >= 0 AND ACI_1Y <= 50)),
    ACI_RHP                 REAL CHECK (ACI_RHP IS NULL OR (ACI_RHP >= 0 AND ACI_RHP <= 25)),
    -- v20 CREATE (5) — TEXT nullable; valores los puebla el reprocess (approved inventory)
    Development_Status      TEXT,
    Duration_Profile        TEXT,
    MMF_Structure           TEXT,
    Alt_Strategy            TEXT,
    Payoff_Profile          TEXT,
    FOREIGN KEY (fund_family_id) REFERENCES fund_families (family_id)
);
"""

_FUND_MASTER_INDEXES_V20 = [
    "CREATE INDEX IF NOT EXISTS idx_fm_nature    ON fund_master (Fund_Nature)",
    "CREATE INDEX IF NOT EXISTS idx_fm_block     ON fund_master (Heuristic_Block, Heuristic_Core)",
    "CREATE INDEX IF NOT EXISTS idx_fm_mgmt      ON fund_master (Management_Company)",
    "CREATE INDEX IF NOT EXISTS idx_fm_family    ON fund_master (fund_family_id)",
    "CREATE INDEX IF NOT EXISTS idx_fm_strategy  ON fund_master (Strategy)",
    "CREATE INDEX IF NOT EXISTS idx_fm_company   ON fund_master (Management_Company)",
    "CREATE INDEX IF NOT EXISTS idx_fm_credit_quality ON fund_master (Credit_Quality)",
    # idx_fm_esg NO se recrea: Is_ESG borrada en v20.
]

_V20_DROP = {"Subtype", "Portfolio_Currency", "Currency_Hedged", "Is_ESG"}
_V20_NEW  = ["Development_Status", "Duration_Profile", "MMF_Structure",
             "Alt_Strategy", "Payoff_Profile"]
# v3: columnas que sobreviven estructuralmente pero cuyo DATO NO se transfiere en
# el rebuild (quedan NULL y los puebla el reprocess):
#   - Type: repropuesta y renombrada a Vehicle_Structure (vocabulario distinto).
#   - Recommended_Holding_Period: TEXT→INTEGER (los códigos no convierten).
# El nombre antiguo de la izquierda no existe en la tabla nueva o cambia de tipo.
_V20_NOT_COPIED = {"Type", "Recommended_Holding_Period"}


def rebuild_fund_master_v20(conn, dry_run: bool = False) -> dict:
    """Rebuild seguro fund_master 57→58 (idiom FK-off / _new / copy / rename).
    Soporta recuperación automática si el proceso se interrumpió a medias.
    """
    # 1. Verificar qué tablas existen actualmente en la BD
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    
    # ── MODO RECUPERACIÓN: Si la migración se quedó a medias en el run anterior ──
    if "fund_master" not in tables:
        if "fund_master_new" in tables:
            print("[RECOVERY] 'fund_master' no existe, pero 'fund_master_new' sí. "
                  "Finalizando migración interrumpida...")
            if dry_run:
                print("[DRY][RECOVERY] ALTER TABLE fund_master_new RENAME TO fund_master")
                print(f"[DRY][RECOVERY] Recrear {len(_FUND_MASTER_INDEXES_V20)} índices.")
                return {"recovered": True, "dry_run": True}
            
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("PRAGMA legacy_alter_table=ON")
            try:
                conn.execute("BEGIN")
                conn.execute("ALTER TABLE fund_master_new RENAME TO fund_master")
                for idx in _FUND_MASTER_INDEXES_V20:
                    conn.execute(idx)
                conn.execute("COMMIT")
                print("[RECOVERY] ¡Base de datos recuperada y migración completada con éxito!")
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            finally:
                conn.execute("PRAGMA legacy_alter_table=OFF")
                conn.execute("PRAGMA foreign_keys=ON")
                
            viol = conn.execute("PRAGMA foreign_key_check(fund_master)").fetchall()
            n_cols = len(_columns(conn, "fund_master"))
            return {"recovered": True, "fk_violations": len(viol), "fund_master_cols": n_cols}
        else:
            raise RuntimeError("Error fatal: No existe ni 'fund_master' ni 'fund_master_new'.")

    # ── FLUJO NORMAL: Si 'fund_master' existe desde el principio ──
    old_cols = _columns(conn, "fund_master")
    survivors = [c for c in old_cols
                 if c not in _V20_DROP and c not in _V20_NOT_COPIED]
    cols_csv = ", ".join(survivors)

    if dry_run:
        print("[DRY] PRAGMA foreign_keys=OFF; BEGIN")
        print("[DRY] CREATE TABLE fund_master_new ( ...58 cols... )")
        print(f"[DRY] INSERT INTO fund_master_new ({len(survivors)} cols) SELECT same FROM fund_master")
        print("[DRY] DROP fund_master; RENAME fund_master_new → fund_master")
        print(f"[DRY] Recrear {len(_FUND_MASTER_INDEXES_V20)} índices.")
        return {"survivors": len(survivors), "new": _V20_NEW, "dry_run": True}

    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("PRAGMA legacy_alter_table=ON") 
    
    try:
        conn.execute("BEGIN")
        conn.execute("DROP TABLE IF EXISTS fund_master_new")
        conn.executescript(_FUND_MASTER_V20_DDL)
        conn.execute(
            f"INSERT INTO fund_master_new ({cols_csv}) "
            f"SELECT {cols_csv} FROM fund_master"
        )
        conn.execute("DROP TABLE fund_master")
        conn.execute("ALTER TABLE fund_master_new RENAME TO fund_master")
        for idx in _FUND_MASTER_INDEXES_V20:
            conn.execute(idx)
        conn.execute("COMMIT")
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA legacy_alter_table=OFF")
        conn.execute("PRAGMA foreign_keys=ON")

    viol = conn.execute("PRAGMA foreign_key_check(fund_master)").fetchall()
    n_cols = len(_columns(conn, "fund_master"))
    return {"survivors": len(survivors), "new": _V20_NEW,
            "fk_violations": len(viol), "fund_master_cols": n_cols}


def main():
    ap = argparse.ArgumentParser(description="Migración v19→v20")
    ap.add_argument("--dry-run", action="store_true", help="solo plan, no escribe")
    ap.add_argument("--rebuild-fund-master", action="store_true",
                    help="ejecutar el rebuild Job A (solo tras completar value-sets+ripple)")
    args = ap.parse_args()

    conn = _get_connection()
    print("== Migración v19 → v20 ==")
    res_b = migrate_job_b(conn, dry_run=args.dry_run)
    print(f"[Job B] columnas añadidas: {res_b['added']} | ya presentes: {res_b['skipped']}")
    print(f"[Job B] vista v_cost_arbitration_overall: recreada")

    if args.rebuild_fund_master:
        print("[Job A] rebuild fund_master 57→58 ...")
        res_a = rebuild_fund_master_v20(conn, dry_run=args.dry_run)
        print(f"[Job A] {res_a}")
    else:
        print("[Job A] rebuild fund_master OMITIDO (usa --rebuild-fund-master "
              "tras completar value-sets + ripple). Las 5 nuevas quedarán NULL.")

    # Verificación rápida (no-dry)
    if not args.dry_run:
        meta_cols = _columns(conn, "fund_kiid_metadata")
        ok6 = all(c in meta_cols for c, _ in _JOB_B_COLUMNS)
        print(f"[CHECK] fund_kiid_metadata tiene las 6 columnas v20: {ok6} "
              f"(total {len(meta_cols)} columnas)")
    conn.close()
    print("== Fin ==")


if __name__ == "__main__":
    sys.exit(main())
