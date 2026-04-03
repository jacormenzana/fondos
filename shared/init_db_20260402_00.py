# shared/init_db.py
# -*- coding: utf-8 -*-
"""
Inicializacion y migracion de fondos.sqlite.

Sustituye a proyecto1/src/init_db.py y proyecto2/src/init_db.py.
Punto unico de entrada para la gestion del schema de la BD.

Responsabilidades:
  1. Aplicar schema_fondos.sql (idempotente via IF NOT EXISTS)
  2. Ejecutar migraciones incrementales de columnas nuevas
  3. Validar alineacion schema <-> writers (P1 + P2 + P3)

Uso:
    cd c:/desarrollo/fondos
    python -m shared.init_db            # ejecucion normal
    python -m shared.init_db --dry-run  # solo muestra migraciones pendientes
    python -m shared.init_db --skip-validation  # omite assert_schema_alignment

Cuando añadir una migracion:
    Cada vez que se añada una columna nueva a fund_master u otra tabla,
    añadir una entrada a _MIGRATIONS con el ALTER TABLE correspondiente.
    El mecanismo try/except garantiza idempotencia: si la columna ya
    existe SQLite lanza OperationalError y se ignora silenciosamente.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

# -- Path setup -------------------------------------------------------
# Funciona tanto ejecutado como modulo (python -m shared.init_db)
# como directamente (python shared/init_db.py) desde la raiz del proyecto.
_ROOT = Path(__file__).resolve().parent.parent   # c:/desarrollo/fondos
sys.path.insert(0, str(_ROOT))

from shared.config import DB_PATH
from shared.schema_checks import assert_schema_alignment


# =====================================================================
# Migraciones incrementales
# =====================================================================
# Formato: (tabla, columna, SQL ALTER TABLE)
# Orden: cronologico de introduccion en el proyecto.
# Nunca eliminar entradas -- son el historial de la BD.

_MIGRATIONS: list[tuple[str, str, str]] = [

    # ------------------------------------------------------------------
    # P1 v2 — Datos extraidos de KIIDs (pipeline post-ingesta)
    # ------------------------------------------------------------------
    (
        "fund_master",
        "Ongoing_Charge",
        "ALTER TABLE fund_master ADD COLUMN Ongoing_Charge REAL",
        # TER anual como ratio decimal (0.0075 = 0.75%).
        # Extraido de la seccion PRIIPs 'Incidencia anual de los costes'.
    ),
    (
        "fund_master",
        "fund_family_id",
        "ALTER TABLE fund_master ADD COLUMN fund_family_id TEXT",
        # Agrupa clases del mismo fondo bajo un ID comun FAM_000001..N.
        # Asignado por fund_family_builder.py post-ingesta.
    ),
    (
        "fund_master",
        "idx_fm_family",
        "CREATE INDEX IF NOT EXISTS idx_fm_family ON fund_master (fund_family_id)",
    ),

    # ------------------------------------------------------------------
    # P1 v3 — Canonico v2: nuevos atributos de clasificacion
    # ------------------------------------------------------------------
    (
        "fund_master",
        "Strategy",
        "ALTER TABLE fund_master ADD COLUMN Strategy TEXT",
        # Estrategia de gestion: Activo | Pasivo | Indexado | Factor | Sistematico
        # Consolida Replication_Method (raw KIID) + Subtype en un atributo canonico.
    ),
    (
        "fund_master",
        "Is_ESG",
        "ALTER TABLE fund_master ADD COLUMN Is_ESG INTEGER DEFAULT 0",
        # 1 si el nombre del fondo contiene: ESG | Sustainable | SRI | Responsible
        #   | Green Bond | Climate | Impact
        # Separado de Theme: permite Theme='Technology' AND Is_ESG=1.
    ),
    (
        "fund_master",
        "Benchmark_Type",
        "ALTER TABLE fund_master ADD COLUMN Benchmark_Type TEXT",
        # Tipo de relacion con el benchmark:
        #   TARGET_INDEX    -> fondo replica un indice (Replication_Method=PASSIVE)
        #   REFERENCE_INDEX -> benchmark declarado como referencia
        #   NO_BENCHMARK    -> KIID declara explicitamente que no sigue ningun indice
        #   NULL            -> desconocido (Benchmark_Declared=NULL)
    ),
    (
        "fund_master",
        "idx_fm_strategy",
        "CREATE INDEX IF NOT EXISTS idx_fm_strategy ON fund_master (Strategy)",
    ),
    (
        "fund_master",
        "idx_fm_esg",
        "CREATE INDEX IF NOT EXISTS idx_fm_esg ON fund_master (Is_ESG)",
    ),

]


# =====================================================================
# Funcion principal
# =====================================================================

def init_db(dry_run: bool = False, skip_validation: bool = False) -> None:
    """
    Aplica schema y migraciones sobre fondos.sqlite.

    dry_run:          si True, muestra que migraciones se aplicarian
                      pero no escribe nada en la BD.
    skip_validation:  si True, omite assert_schema_alignment.
                      Util cuando los writers aun no estan actualizados.
    """
    schema_path = _ROOT / "db" / "schema_fondos.sql"
    if not schema_path.exists():
        raise FileNotFoundError(
            f"Schema canonico no encontrado: {schema_path}\n"
            "Asegurate de que db/schema_fondos.sql existe en la raiz del proyecto."
        )

    db_path = Path(DB_PATH)
    print(f"\n{'='*60}")
    print(f"  init_db  |  BD: {db_path}")
    print(f"  Schema:  {schema_path.name}")
    print(f"  dry_run: {dry_run}")
    print(f"{'='*60}\n")

    if dry_run:
        print("  [DRY-RUN] Migraciones que se aplicarian:")
        for table, col, sql, *_ in _MIGRATIONS:
            print(f"    {table}.{col}  ->  {sql}")
        print("\n  [DRY-RUN] Nada escrito en BD.")
        return

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")

    # -- 1. Aplicar schema (IF NOT EXISTS -- completamente idempotente) --
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
            print(f"  [+] Migracion aplicada:  {table}.{col}")
            n_applied += 1
        except Exception:
            n_skipped += 1  # columna ya existe -- OK

    print(f"\n  Migraciones: {n_applied} aplicadas, {n_skipped} ya existentes")

    # -- 3. Validacion alineacion schema <-> writers ---------------------
    if not skip_validation:
        try:
            assert_schema_alignment(conn, scope="all")
            print("  Schema alignment OK (P1 + P2 + P3)")
        except Exception as e:
            print(f"  AVISO schema alignment: {e}")
            print("  (Ejecuta con --skip-validation si los writers aun no estan actualizados)")
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
        help="Omite assert_schema_alignment (util durante desarrollo)",
    )
    args = parser.parse_args()
    init_db(dry_run=args.dry_run, skip_validation=args.skip_validation)
