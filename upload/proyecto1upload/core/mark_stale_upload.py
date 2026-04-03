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

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: BD no encontrada: {db_path}"); sys.exit(1)

    conn = get_connection(db_path)
    n = mark_stale_for_refresh(conn, max_age_days=args.max_age, max_funds=args.max_funds)
    conn.close()

    print(f"[mark_stale] {n} fondos marcados FORCE_REFRESH "
          f"(antigüedad > {args.max_age} días, límite {args.max_funds}/ciclo)")

if __name__ == "__main__":
    main()
