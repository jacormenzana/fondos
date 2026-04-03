# shared/db.py
# -*- coding: utf-8 -*-
"""
Conexion a fondos.sqlite para todos los proyectos (P1, P2, P3).

Sustituye a proyecto1/src/db.py y proyecto2/src/db.py.
Usa sqlite3 puro — SQLAlchemy no es dependencia del proyecto.

Uso desde cualquier modulo:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[N]))
    from shared.db import get_connection
"""

import sqlite3
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent   # c:/desarrollo/fondos
sys.path.insert(0, str(_ROOT))

from shared.config import DB_PATH


def get_connection() -> sqlite3.Connection:
    """
    Devuelve una conexion sqlite3 a fondos.sqlite con:
      - foreign_keys activadas
      - journal_mode WAL (escrituras concurrentes seguras)
      - row_factory = sqlite3.Row (acceso por nombre de columna)

    Lanza FileNotFoundError si fondos.sqlite no existe todavia.
    Ejecutar primero:  python -m shared.init_db
    """
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"No se encuentra la base de datos: {DB_PATH}\n"
            "Ejecuta primero: python -m shared.init_db"
        )
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.row_factory = sqlite3.Row
    return conn
