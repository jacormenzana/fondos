#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
migrate_to_fondos.py
====================
Migración de p1_output.sqlite → db/fondos.sqlite

Qué hace:
  1. Crea el directorio db/ si no existe
  2. Crea db/fondos.sqlite con el schema unificado (P1+P2+P3)
  3. Copia todos los datos de P1 (fund_master, fund_kiid_metadata,
     ingestion_log, fund_nav_monthly)
  4. Migra fund_metrics del formato placeholder P1 al formato canónico P2
     (los registros existentes se trasladan con real_flag=0, metric_version='v1')
  5. Valida la integridad del resultado
  6. NO elimina el fichero original (queda como backup)

Uso:
    Sitúate en la raíz del proyecto (donde están proyecto1/, proyecto2/, etc.)
    python migrate_to_fondos.py

    Opciones:
    --source   ruta al sqlite de origen  (default: proyecto1/p1_output.sqlite)
    --target   ruta al sqlite de destino (default: db/fondos.sqlite)
    --schema   ruta al fichero SQL       (default: db/schema_fondos.sql)
    --dry-run  solo valida, no escribe
"""

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


# ============================================================
# Helpers
# ============================================================

def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def get_conn(path: Path, read_only: bool = False) -> sqlite3.Connection:
    if read_only:
        uri = f"file:{path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    else:
        conn = sqlite3.connect(str(path))
        conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def count(conn: sqlite3.Connection, table: str) -> int:
    if not table_exists(conn, table):
        return 0
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# ============================================================
# Copia de tablas P1 sin transformación
# ============================================================

P1_TABLES = [
    "fund_master",
    "fund_kiid_metadata",
    "ingestion_log",
    "fund_nav_monthly",
]


def copy_table(src: sqlite3.Connection, dst: sqlite3.Connection, table: str) -> int:
    """Copia todos los registros de una tabla de src a dst."""
    if not table_exists(src, table):
        log(f"  AVISO: tabla '{table}' no existe en origen — se omite")
        return 0

    rows = src.execute(f"SELECT * FROM {table}").fetchall()
    if not rows:
        log(f"  '{table}': vacía en origen — nada que copiar")
        return 0

    cols = rows[0].keys()
    placeholders = ", ".join("?" * len(cols))
    col_names = ", ".join(cols)
    sql = f"INSERT OR IGNORE INTO {table} ({col_names}) VALUES ({placeholders})"

    dst.executemany(sql, [tuple(r) for r in rows])
    dst.commit()
    log(f"  '{table}': {len(rows)} registros copiados")
    return len(rows)


# ============================================================
# Migración fund_metrics (placeholder P1 → canónico P2)
# ============================================================

def migrate_fund_metrics(src: sqlite3.Connection, dst: sqlite3.Connection) -> int:
    """
    El placeholder de P1 tiene: ISIN, Metric_Name, Metric_Horizon,
    Metric_Value, Metric_Unit, Calculated_At

    El canónico de P2 tiene: isin, metric, horizon, value, real_flag,
    calculation_date, metric_version, benchmark_id, source_rows

    Mapeo:
      ISIN           → isin
      Metric_Name    → metric
      Metric_Horizon → horizon
      Metric_Value   → value
      Calculated_At  → calculation_date
      real_flag      = 0 (nominal, asumimos que el placeholder era nominal)
      metric_version = 'v1_migrated'
    """
    if not table_exists(src, "fund_metrics"):
        log("  'fund_metrics': no existe en origen — tabla P2 queda vacía")
        return 0

    rows = src.execute(
        "SELECT ISIN, Metric_Name, Metric_Horizon, Metric_Value, Calculated_At "
        "FROM fund_metrics"
    ).fetchall()

    if not rows:
        log("  'fund_metrics': placeholder vacío en origen — nada que migrar")
        return 0

    today = datetime.utcnow().date().isoformat()
    sql = """
        INSERT OR IGNORE INTO fund_metrics
            (isin, metric, horizon, value, real_flag,
             calculation_date, metric_version, benchmark_id, source_rows)
        VALUES (?, ?, ?, ?, 0, ?, 'v1_migrated', NULL, NULL)
    """
    params = [
        (
            r["ISIN"],
            r["Metric_Name"],
            r["Metric_Horizon"],
            r["Metric_Value"],
            r["Calculated_At"][:10] if r["Calculated_At"] else today,
        )
        for r in rows
    ]
    dst.executemany(sql, params)
    dst.commit()
    log(f"  'fund_metrics': {len(rows)} registros migrados (placeholder → canónico P2)")
    return len(rows)


# ============================================================
# Validación post-migración
# ============================================================

def validate(src: sqlite3.Connection, dst: sqlite3.Connection) -> bool:
    ok = True
    log("\n── Validación ──────────────────────────────────────────")

    for table in P1_TABLES:
        n_src = count(src, table)
        n_dst = count(dst, table)
        status = "✓" if n_dst >= n_src else "✗"
        log(f"  {status}  {table:30s}  origen={n_src:6d}  destino={n_dst:6d}")
        if n_dst < n_src:
            ok = False

    # fund_metrics: el destino puede tener más si ya había datos P2
    n_src_m = count(src, "fund_metrics")
    n_dst_m = count(dst, "fund_metrics")
    log(f"  ✓  {'fund_metrics (canónico P2)':30s}  migrados_origen={n_src_m}  total_destino={n_dst_m}")

    # Tablas P2 nuevas deben existir
    p2_tables = ["series_macro", "series_benchmark", "series_inflation", "p2_pipeline_log"]
    for t in p2_tables:
        exists = table_exists(dst, t)
        status = "✓" if exists else "✗"
        log(f"  {status}  {t:30s}  (tabla nueva P2)")
        if not exists:
            ok = False

    # Tablas P3
    p3_tables = ["fund_scores", "portfolio_scenarios", "portfolio_weights"]
    for t in p3_tables:
        exists = table_exists(dst, t)
        status = "✓" if exists else "✗"
        log(f"  {status}  {t:30s}  (tabla nueva P3)")
        if not exists:
            ok = False

    # Integridad referencial básica
    orphans = dst.execute("""
        SELECT COUNT(*) FROM fund_nav_monthly n
        WHERE NOT EXISTS (SELECT 1 FROM fund_master f WHERE f.ISIN = n.ISIN)
    """).fetchone()[0]
    status = "✓" if orphans == 0 else "✗"
    log(f"  {status}  NAV sin fondo en fund_master: {orphans}")
    if orphans > 0:
        ok = False

    log("────────────────────────────────────────────────────────")
    return ok


# ============================================================
# Main
# ============================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="Migración a fondos.sqlite unificado")
    parser.add_argument(
        "--source", default="proyecto1/p1_output.sqlite",
        help="SQLite de origen (default: proyecto1/p1_output.sqlite)"
    )
    parser.add_argument(
        "--target", default="db/fondos.sqlite",
        help="SQLite de destino (default: db/fondos.sqlite)"
    )
    parser.add_argument(
        "--schema", default="db/schema_fondos.sql",
        help="Fichero SQL con el schema unificado (default: db/schema_fondos.sql)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Solo valida el origen, no crea el destino"
    )
    args = parser.parse_args()

    source_path = Path(args.source)
    target_path = Path(args.target)
    schema_path = Path(args.schema)

    # ── Validaciones previas ─────────────────────────────────
    if not source_path.exists():
        log(f"ERROR: No se encuentra el origen: {source_path}")
        sys.exit(1)

    if not schema_path.exists():
        log(f"ERROR: No se encuentra el schema: {schema_path}")
        sys.exit(1)

    log(f"Origen : {source_path}")
    log(f"Destino: {target_path}")
    log(f"Schema : {schema_path}")

    if args.dry_run:
        log("\nModo DRY-RUN: solo se analiza el origen.")
        src = get_conn(source_path, read_only=True)
        for table in P1_TABLES + ["fund_metrics"]:
            log(f"  {table}: {count(src, table)} registros")
        src.close()
        return

    # ── Crear directorio destino ─────────────────────────────
    target_path.parent.mkdir(parents=True, exist_ok=True)

    # ── Backup si el destino ya existe ───────────────────────
    if target_path.exists():
        backup = target_path.with_suffix(
            f".backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.sqlite"
        )
        shutil.copy2(target_path, backup)
        log(f"Backup del destino existente: {backup}")

    # ── Crear schema en destino ──────────────────────────────
    log("\n── Creando schema ──────────────────────────────────────")
    schema_sql = schema_path.read_text(encoding="utf-8")
    dst = get_conn(target_path)
    dst.executescript(schema_sql)
    dst.commit()
    log("  Schema creado correctamente")

    # ── Conectar origen ──────────────────────────────────────
    src = get_conn(source_path, read_only=True)

    # ── Copiar tablas P1 ─────────────────────────────────────
    log("\n── Copiando tablas P1 ──────────────────────────────────")
    for table in P1_TABLES:
        copy_table(src, dst, table)

    # ── Migrar fund_metrics ──────────────────────────────────
    log("\n── Migrando fund_metrics ───────────────────────────────")
    migrate_fund_metrics(src, dst)

    # ── Validar ──────────────────────────────────────────────
    ok = validate(src, dst)

    src.close()
    dst.close()

    if ok:
        log(f"\n✓ Migración completada con éxito → {target_path}")
        log(f"  El fichero original queda intacto en: {source_path}")
        log(f"  Actualiza los config.py de P1 y P2 para apuntar a: {target_path}")
    else:
        log("\n✗ Migración completada CON ERRORES — revisar log arriba")
        sys.exit(1)


if __name__ == "__main__":
    main()
