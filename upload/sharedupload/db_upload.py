# shared/db.py
# -*- coding: utf-8 -*-
"""
Conexion a fondos.sqlite para todos los proyectos (P1, P2, P3).

Sustituye a proyecto1/src/db.py y proyecto2/src/db.py.
Usa sqlite3 puro — SQLAlchemy no es dependencia del proyecto.

Uso desde cualquier modulo:
    from shared.db import get_connection

Cambios v17:
  - timeout=30 en sqlite3.connect() — evita OperationalError en accesos
    concurrentes desde scripts distintos bajo WAL mode.
  - get_connection() acepta db_path opcional para tests y scripts
    que necesiten apuntar a una BD distinta de la configurada.
"""

import sqlite3
import sys
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent.parent   # c:/desarrollo/fondos
sys.path.insert(0, str(_ROOT))

from shared.config import DB_PATH


def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """
    Devuelve una conexion sqlite3 a fondos.sqlite con:
      - foreign_keys activadas
      - journal_mode WAL (escrituras concurrentes seguras)
      - timeout=30s (reintenta en caso de bloqueo concurrente)
      - row_factory = sqlite3.Row (acceso por nombre de columna)

    Parámetros:
        db_path: ruta alternativa a la BD. Si es None, usa DB_PATH
                 de shared.config. Útil en tests y scripts auxiliares.

    Lanza FileNotFoundError si la BD no existe.
    Ejecutar primero:  python -m shared.init_db
    """
    target = Path(db_path) if db_path is not None else DB_PATH

    if not target.exists():
        raise FileNotFoundError(
            f"No se encuentra la base de datos: {target}\n"
            "Ejecuta primero: python -m shared.init_db"
        )

    conn = sqlite3.connect(str(target), timeout=30)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    # Performance pragmas (safe with WAL):
    #   synchronous=NORMAL — skips per-commit WAL fsync; power-safe under WAL
    #     (a checkpoint sync still protects against corruption on crash).
    #   cache_size=-65536  — 64 MB page cache (vs ~2 MB default); reduces
    #     repeated btree traversals on the 16M-row fund_metric_timeseries.
    #   temp_store=MEMORY  — sorts/indexes for GROUP BY / subqueries stay in RAM.
    #   mmap_size          — 512 MB memory-mapped read window; speeds sequential
    #     reads on large tables without extra system calls.
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA cache_size = -65536;")
    conn.execute("PRAGMA temp_store = MEMORY;")
    conn.execute("PRAGMA mmap_size = 536870912;")
    conn.row_factory = sqlite3.Row
    # isolation_level=None: delega control de transacciones a SQLite y al
    # código explícito (with conn:). Evita que Python abra transacciones
    # implícitas que interfieren con ON CONFLICT DO UPDATE (SQLite 3.24+).
    # Sin esto, executescript() en create_schema resetea isolation_level a ''
    # y el upsert falla con "ON CONFLICT clause does not match any PK".
    conn.isolation_level = None
    return conn
