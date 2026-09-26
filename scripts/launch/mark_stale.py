#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/launch/mark_stale.py
Marca como FORCE_REFRESH los fondos cuyo KIID supera KIID_CACHE_DAYS días
sin re-descargarse. Se ejecuta UNA VEZ antes del pipeline principal.

Uso:
    python scripts/launch/mark_stale.py [--db ruta] [--max-age 180] [--max-funds 50]
"""
import argparse, sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "proyecto1"))

from core.sqlite_writer import get_connection
from core.io import mark_stale_for_refresh

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db",        default=str(_ROOT / "db" / "fondos.sqlite"))
    parser.add_argument("--max-age",   type=int, default=180,
                        help="Días de antigüedad para marcar FORCE_REFRESH")
    parser.add_argument("--max-funds", type=int, default=50,
                        help="Máximo de fondos a marcar por ejecución (anti-avalancha)")
    args = parser.parse_args()

    # No pre-check of the SQLite path: --db only matters when the resolved backend is SQLite, and
    # get_connection() raises FileNotFoundError itself in that case. With FONDOS_DB_BACKEND=postgres
    # the (retired) SQLite file is irrelevant, so requiring it to exist would abort PASO 2 of
    # P1_P2_Complete.bat the day the file is archived, before touching Postgres at all.
    try:
        conn = get_connection(Path(args.db))
    except FileNotFoundError as e:
        print(f"ERROR: {e}"); sys.exit(1)
    n = mark_stale_for_refresh(conn, max_age_days=args.max_age, max_funds=args.max_funds)
    conn.close()

    print(f"[mark_stale] {n} fondos marcados FORCE_REFRESH "
          f"(antigüedad > {args.max_age} días, límite {args.max_funds}/ciclo)")

if __name__ == "__main__":
    sys.path.insert(0, str(_ROOT))
    from shared.backlog_client import install_excepthook
    install_excepthook(object_name="mark_stale.py")         # unhandled failure -> backlog ticket
    main()
