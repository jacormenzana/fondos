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
    conn.row_factory = sqlite3.Row
    return conn
