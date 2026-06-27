#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
scripts/mig/migrate_v20_to_v20_1.py

Migración de schema v20 → v20.1.

Añade a fund_kiid_metadata las 6 columnas de arbitración ACI.
Idempotente y sin rebuild.
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


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


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


_JOB_COLUMNS = [
    ("Cost_ACI_RHP_BandsX", "REAL"),
    ("Cost_ACI_RHP_Ruled", "REAL"),
    ("Cost_ACI_RHP_Arbitration", "TEXT CHECK (Cost_ACI_RHP_Arbitration IS NULL OR "
        "Cost_ACI_RHP_Arbitration IN ('AGREE','OCR_RECOVERED','BOTH_FAIL',"
        "'ONLY_BANDS_X','ONLY_RULED','CONFLICT'))"),
    ("Cost_ACI_1Y_BandsX", "REAL"),
    ("Cost_ACI_1Y_Ruled", "REAL"),
    ("Cost_ACI_1Y_Arbitration", "TEXT CHECK (Cost_ACI_1Y_Arbitration IS NULL OR "
        "Cost_ACI_1Y_Arbitration IN ('AGREE','OCR_RECOVERED','BOTH_FAIL',"
        "'ONLY_BANDS_X','ONLY_RULED','CONFLICT'))"),
]


def migrate(conn: sqlite3.Connection, dry_run: bool = False) -> dict:
    existing = _columns(conn, "fund_kiid_metadata")
    added = []
    skipped = []
    for name, decl in _JOB_COLUMNS:
        if name in existing:
            skipped.append(name)
            continue
        stmt = f"ALTER TABLE fund_kiid_metadata ADD COLUMN {name} {decl}"
        if dry_run:
            print(f"[DRY] {stmt}")
        else:
            conn.execute(stmt)
        added.append(name)
    if not dry_run:
        conn.commit()
    return {
        "added": added,
        "skipped": skipped,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Migración v20 → v20.1: añade columnas ACI en fund_kiid_metadata.")
    parser.add_argument("--dry-run", action="store_true", help="Mostrar las sentencias sin ejecutarlas.")
    args = parser.parse_args()

    conn = _get_connection()
    result = migrate(conn, dry_run=args.dry_run)
    print("added:", result["added"])
    print("skipped:", result["skipped"])


if __name__ == "__main__":
    main()
