# init_db.py
# -*- coding: utf-8 -*-
"""
Inicialización de fondos.sqlite con el schema unificado (P1 + P2 + P3).

Uso:
    Desde la raíz del proyecto global (c:\\desarrollo\\fondos):

    # Inicialización normal
    python init_db.py

    # Solo verificar schema sin recrear
    python init_db.py --check-only

    # Forzar recreación completa (DESTRUYE datos existentes — usar con cuidado)
    python init_db.py --force-recreate

Notas:
    - En uso normal es idempotente: si fondos.sqlite ya existe y el schema
      está alineado, no hace nada destructivo.
    - El schema se lee desde db/schema_fondos.sql (fuente de verdad única).
    - Tras crear el schema ejecuta assert_schema_alignment() para garantizar
      coherencia entre DB y writers.
"""

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# ── Importar configuración centralizada ─────────────────────
# Añadimos la raíz al path para que funcione tanto si se lanza
# desde la raíz como desde cualquier subdirectorio.
_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "proyecto1"))  # expone proyecto1/core/

from shared.config import DB_PATH
from core.schema_checks import assert_schema_alignment

# Schema unificado (fuente de verdad)
SCHEMA_PATH = _ROOT / "db" / "schema_fondos.sql"


# ============================================================
# Helpers
# ============================================================

def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def get_conn(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.row_factory = sqlite3.Row
    return conn


# ============================================================
# Lógica principal
# ============================================================

def init_db(check_only: bool = False, force_recreate: bool = False) -> None:

    # ── Validaciones previas ─────────────────────────────────
    if not SCHEMA_PATH.exists():
        log(f"ERROR: No se encuentra el schema en: {SCHEMA_PATH}")
        log("  Asegúrate de que db/schema_fondos.sql existe en la raíz del proyecto.")
        sys.exit(1)

    # ── Modo check-only ──────────────────────────────────────
    if check_only:
        if not DB_PATH.exists():
            log(f"AVISO: La base de datos no existe todavía en: {DB_PATH}")
            sys.exit(1)
        log(f"Verificando schema de: {DB_PATH}")
        conn = get_conn(DB_PATH)
        try:
            assert_schema_alignment(conn, scope="p2")
            log("✓ Schema alineado correctamente (P1 + P2)")
        finally:
            conn.close()
        return

    # ── Recreación forzada ───────────────────────────────────
    if force_recreate and DB_PATH.exists():
        backup = DB_PATH.with_suffix(
            f".backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.sqlite"
        )
        shutil.copy2(DB_PATH, backup)
        log(f"Backup creado: {backup}")
        DB_PATH.unlink()
        log(f"Base de datos eliminada para recreación: {DB_PATH}")

    # ── Crear directorio si no existe ────────────────────────
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    # ── Crear / actualizar schema ────────────────────────────
    already_existed = DB_PATH.exists()
    ddl = SCHEMA_PATH.read_text(encoding="utf-8")

    conn = get_conn(DB_PATH)
    try:
        conn.executescript(ddl)
        conn.commit()
        action = "actualizada" if already_existed else "creada"
        log(f"Base de datos {action}: {DB_PATH}")

        # ── Verificar alineación schema ↔ writers ────────────
        assert_schema_alignment(conn, scope="p2")
        log("✓ Schema verificado y alineado correctamente (P1 + P2)")

    except AssertionError as e:
        log(f"✗ Error de alineación de schema:\n  {e}")
        conn.close()
        sys.exit(1)

    except Exception as e:
        log(f"✗ Error inesperado: {e}")
        conn.close()
        sys.exit(1)

    finally:
        conn.close()

    log("Inicialización completada.")


# ============================================================
# Entry point
# ============================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Inicializa fondos.sqlite con el schema unificado"
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Solo verifica el schema existente, no crea ni modifica nada",
    )
    parser.add_argument(
        "--force-recreate",
        action="store_true",
        help="Elimina y recrea la DB desde cero (hace backup automático)",
    )
    args = parser.parse_args()

    init_db(check_only=args.check_only, force_recreate=args.force_recreate)
