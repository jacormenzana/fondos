# shared/init_db.py
# -*- coding: utf-8 -*-
"""
Inicialización y migración de fondos.sqlite.

Sustituye a proyecto1/src/init_db.py y proyecto2/src/init_db.py.
Punto único de entrada para la gestión del schema de la BD.

Responsabilidades:
  1. Aplicar schema_fondos.sql (idempotente via IF NOT EXISTS)
  2. Ejecutar migraciones incrementales de columnas nuevas
  3. Validar alineación schema <-> writers (P1 + P2 + P3)

Uso:
    cd c:/desarrollo/fondos
    python -m shared.init_db             # ejecución normal
    python -m shared.init_db --dry-run   # solo muestra migraciones pendientes
    python -m shared.init_db --skip-validation  # omite assert_schema_alignment

Cuando añadir una migración:
    Cada vez que se añada una columna nueva a fund_master u otra tabla,
    añadir una entrada a _MIGRATIONS con el ALTER TABLE correspondiente.
    El mecanismo try/except garantiza idempotencia: si la columna ya
    existe SQLite lanza OperationalError y se ignora silenciosamente.
    NUNCA eliminar entradas — son el historial de la BD.

Cambios v17:
  - Añadidas migraciones v16 que faltaban en la lista original:
    Market_Cap_Focus, Sector_Focus, Currency_Hedged, Investment_Universe,
    Processing_Time_Ms, Processing_Breakdown.
  - Añadidas migraciones v17:
    Investment_Focus, Credit_Quality, Fee_Known_Flag.
  - Corregida llamada a assert_schema_alignment(conn, scope="all") —
    el parámetro scope no existe; se elimina.
  - Índices adicionales para columnas de uso frecuente en P3.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

# -- Path setup -------------------------------------------------------
_ROOT = Path(__file__).resolve().parent.parent   # c:/desarrollo/fondos
sys.path.insert(0, str(_ROOT))

from shared.config import DB_PATH
from shared.schema_checks import assert_schema_alignment


# =====================================================================
# Migraciones incrementales
# =====================================================================
# Formato: (tabla, identificador_único, SQL)
#   - El identificador_único es el nombre de columna/índice.
#     Se usa para logging, no para control de ejecución.
#   - El control de idempotencia es por excepción (SQLite lanza
#     OperationalError si la columna ya existe).
# Orden: cronológico de introducción en el proyecto.

_MIGRATIONS: list[tuple[str, str, str]] = [

    # ------------------------------------------------------------------
    # P1 v2 — Datos extraídos de KIIDs
    # ------------------------------------------------------------------
    (
        "fund_master", "Ongoing_Charge",
        "ALTER TABLE fund_master ADD COLUMN Ongoing_Charge REAL",
        # TER anual como ratio decimal (0.0075 = 0.75%)
    ),
    (
        "fund_master", "fund_family_id",
        "ALTER TABLE fund_master ADD COLUMN fund_family_id TEXT",
        # FAM_000001..N — agrupa clases del mismo vehículo
    ),
    (
        "fund_master", "idx_fm_family",
        "CREATE INDEX IF NOT EXISTS idx_fm_family ON fund_master (fund_family_id)",
    ),

    # ------------------------------------------------------------------
    # P1 v3 — Canónico v2: nuevos atributos de clasificación
    # ------------------------------------------------------------------
    (
        "fund_master", "Strategy",
        "ALTER TABLE fund_master ADD COLUMN Strategy TEXT",
    ),
    (
        "fund_master", "Is_ESG",
        "ALTER TABLE fund_master ADD COLUMN Is_ESG INTEGER DEFAULT 0",
    ),
    (
        "fund_master", "Benchmark_Type",
        "ALTER TABLE fund_master ADD COLUMN Benchmark_Type TEXT",
    ),
    (
        "fund_master", "idx_fm_strategy",
        "CREATE INDEX IF NOT EXISTS idx_fm_strategy ON fund_master (Strategy)",
    ),
    (
        "fund_master", "idx_fm_esg",
        "CREATE INDEX IF NOT EXISTS idx_fm_esg ON fund_master (Is_ESG)",
    ),

    # ------------------------------------------------------------------
    # P1 v16 — Atributos characterizer v3 + telemetría
    # ------------------------------------------------------------------
    (
        "fund_master", "Market_Cap_Focus",
        "ALTER TABLE fund_master ADD COLUMN Market_Cap_Focus TEXT",
        # Large Cap | Mid Cap | Small Cap | SMID Cap | Multi Cap
    ),
    (
        "fund_master", "Sector_Focus",
        "ALTER TABLE fund_master ADD COLUMN Sector_Focus TEXT",
        # Sector económico GICS-ES para fondos temáticos
    ),
    (
        "fund_master", "Currency_Hedged",
        "ALTER TABLE fund_master ADD COLUMN Currency_Hedged TEXT",
        # Yes | No — señal de cobertura desde nombre de clase
    ),
    (
        "fund_master", "Investment_Universe",
        "ALTER TABLE fund_master ADD COLUMN Investment_Universe TEXT",
        # Global | Regional | Country | Liquidity (v17: Sector/Thematic → Investment_Focus)
    ),
    (
        "fund_master", "Accumulation_Policy",
        "ALTER TABLE fund_master ADD COLUMN Accumulation_Policy TEXT",
        # Accumulation | Distribution
    ),
    (
        "fund_kiid_metadata", "Processing_Time_Ms",
        "ALTER TABLE fund_kiid_metadata ADD COLUMN Processing_Time_Ms REAL",
        # Tiempo de proceso en ms (telemetría de pipeline)
    ),
    (
        "fund_kiid_metadata", "Processing_Breakdown",
        "ALTER TABLE fund_kiid_metadata ADD COLUMN Processing_Breakdown TEXT",
        # JSON con desglose por fase: kiid_fetch, kiid_parse, classify
    ),

    # ------------------------------------------------------------------
    # P1 v17 — Nuevos atributos + corrección modelo de exposición
    # ------------------------------------------------------------------
    (
        "fund_master", "Investment_Focus",
        "ALTER TABLE fund_master ADD COLUMN Investment_Focus TEXT",
        # Broad | Sector | Thematic
        # Dimensión de exposición ortogonal a Investment_Universe
    ),
    (
        "fund_master", "Credit_Quality",
        "ALTER TABLE fund_master ADD COLUMN Credit_Quality TEXT",
        # Investment Grade | High Yield | Mixed | No aplica
    ),
    (
        "fund_master", "Fee_Known_Flag",
        "ALTER TABLE fund_master ADD COLUMN Fee_Known_Flag TEXT",
        # EXTRACTED | ZERO_CONFIRMED | NOT_FOUND
        # Distingue Entry_Fee_Pct=0 confirmado de NULL por no extracción
    ),
    (
        "fund_master", "idx_fm_nature",
        "CREATE INDEX IF NOT EXISTS idx_fm_nature ON fund_master (Fund_Nature)",
    ),
    (
        "fund_master", "idx_fm_investment_focus",
        "CREATE INDEX IF NOT EXISTS idx_fm_investment_focus "
        "ON fund_master (Investment_Focus)",
    ),
    (
        "fund_master", "idx_fm_credit_quality",
        "CREATE INDEX IF NOT EXISTS idx_fm_credit_quality "
        "ON fund_master (Credit_Quality)",
    ),
]


# =====================================================================
# Función principal
# =====================================================================

def init_db(dry_run: bool = False, skip_validation: bool = False) -> None:
    """
    Aplica schema y migraciones sobre fondos.sqlite.

    dry_run:          si True, muestra qué migraciones se aplicarían
                      pero no escribe nada en la BD.
    skip_validation:  si True, omite assert_schema_alignment.
                      Útil cuando los writers aún no están actualizados.
    """
    schema_path = _ROOT / "db" / "schema_fondos.sql"
    if not schema_path.exists():
        raise FileNotFoundError(
            f"Schema canónico no encontrado: {schema_path}\n"
            "Asegúrate de que db/schema_fondos.sql existe en la raíz del proyecto."
        )

    db_path = Path(DB_PATH)
    print(f"\n{'='*60}")
    print(f"  init_db  |  BD: {db_path}")
    print(f"  Schema:  {schema_path.name}")
    print(f"  dry_run: {dry_run}")
    print(f"{'='*60}\n")

    if dry_run:
        print("  [DRY-RUN] Migraciones que se aplicarían:")
        for entry in _MIGRATIONS:
            table, col, sql_alter = entry[0], entry[1], entry[2]
            print(f"    {table}.{col}  ->  {sql_alter}")
        print(f"\n  Total: {len(_MIGRATIONS)} migraciones")
        print("  [DRY-RUN] Nada escrito en BD.")
        return

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")

    # -- 1. Aplicar schema (IF NOT EXISTS — completamente idempotente) ---
    ddl = schema_path.read_text(encoding="utf-8")
    conn.executescript(ddl)
    conn.commit()
    print(f"  Schema aplicado: {schema_path}")

    # -- 2. Migraciones incrementales ------------------------------------
    n_applied = 0
    n_skipped = 0
    for entry in _MIGRATIONS:
        table, col, sql_alter = entry[0], entry[1], entry[2]
        try:
            conn.execute(sql_alter)
            conn.commit()
            print(f"  [+] Migración aplicada:  {table}.{col}")
            n_applied += 1
        except Exception:
            n_skipped += 1   # columna/índice ya existe — OK

    print(f"\n  Migraciones: {n_applied} aplicadas, {n_skipped} ya existentes")

    # -- 3. Validación alineación schema <-> writers ---------------------
    if not skip_validation:
        try:
            assert_schema_alignment(conn)   # sin parámetro scope (v17 fix)
            print("  Schema alignment OK")
        except AssertionError as exc:
            print(f"  AVISO schema alignment:\n{exc}")
            print(
                "  (Ejecuta con --skip-validation si los writers "
                "aún no están actualizados)"
            )
    else:
        print("  Schema alignment: omitido (--skip-validation)")

    conn.close()
    print(f"\n  BD lista: {db_path}\n")


# =====================================================================
# Entry point
# =====================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Inicializa y migra fondos.sqlite"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Muestra migraciones pendientes sin escribir en BD",
    )
    parser.add_argument(
        "--skip-validation",
        action="store_true",
        help="Omite assert_schema_alignment (útil durante desarrollo)",
    )
    args = parser.parse_args()
    init_db(dry_run=args.dry_run, skip_validation=args.skip_validation)
