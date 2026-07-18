#!/usr/bin/env python
# -*- coding: utf-8 -*-
# scripts/mig/migrate_v22_to_v23.py  — FIX-UNIVERSE-RECON-1
"""
Migración de schema v22 → v23.

Aditiva, idempotente. Añade una sola columna a fund_master:

    In_Current_Universe  INTEGER NOT NULL DEFAULT 1

Esta columna es la "soft-delete" flag de pertenencia al universo de harvest
vigente. 1 = en el universo actual (db_document_catalogue MAX), 0 = huérfano
preservado por la política append-only de fund_master. Regenerada en cada
ciclo del pipeline por reconcile_universe_membership() en sqlite_writer.py.

Después del ALTER TABLE este script ejecuta un backfill INMEDIATO:
  - Primero carga el universo vigente de db_document_catalogue.
  - Llama a reconcile_universe_membership() para marcar las 796 filas
    huérfanas como 0 (frente al DEFAULT 1 que les asignaría el ALTER).
  - Si db_document_catalogue está vacío, deja el DEFAULT 1 en todas las
    filas (benign: el flag se sincronizará en el próximo ciclo completo).

Uso (desde la raíz del repo, con el entorno activado):
    python -X utf8 scripts/mig/migrate_v22_to_v23.py
    python -X utf8 scripts/mig/migrate_v22_to_v23.py --dry-run
"""
from __future__ import annotations

import argparse
import sys
import sqlite3
from pathlib import Path


# ── Ruta al repo y al entorno ─────────────────────────────────────────────────
_REPO = Path(__file__).resolve().parent.parent.parent   # c:/desarrollo/fondos
sys.path.insert(0, str(_REPO))


def _get_connection() -> sqlite3.Connection:
    """Obtiene conexión al SQLite canónico (shared.config.DB_PATH)."""
    for mod in ("shared.db",):
        try:
            m = __import__(mod, fromlist=["get_connection"])
            if hasattr(m, "get_connection"):
                return m.get_connection()
        except Exception:
            pass
    from shared.config import DB_PATH
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def migrate(conn: sqlite3.Connection, dry_run: bool = False) -> dict:
    """
    Añade In_Current_Universe a fund_master. Idempotente.

    Returns:
        {'added': bool, 'backfill': {'in_universe': int, 'orphans': int} | None}
    """
    existing = _columns(conn, "fund_master")
    col = "In_Current_Universe"

    added = False
    if col in existing:
        print(f"[SKIP] fund_master.{col} ya existe — no se necesita ALTER TABLE.")
    else:
        stmt = f"ALTER TABLE fund_master ADD COLUMN {col} INTEGER NOT NULL DEFAULT 1"
        if dry_run:
            print(f"[DRY] {stmt}")
        else:
            conn.execute(stmt)
            conn.commit()
            print(f"[OK] ALTER TABLE fund_master ADD COLUMN {col} INTEGER NOT NULL DEFAULT 1")
        added = True

    # ── Backfill inmediato: marcar huérfanos como 0 ──────────────────────────
    backfill = None
    if not dry_run:
        try:
            rows = conn.execute(
                """
                SELECT DISTINCT isin FROM db_document_catalogue
                WHERE isin IS NOT NULL AND isin != ''
                  AND harvest_ts = (SELECT MAX(harvest_ts) FROM db_document_catalogue)
                """
            ).fetchall()
            current_isins = [r[0] for r in rows]
        except Exception as exc:
            print(f"[WARN] No se pudo cargar db_document_catalogue: {exc}")
            print("[WARN] Backfill omitido; el flag se sincronizará en el próximo ciclo.")
            current_isins = []

        if current_isins:
            # Importar la función canónica para mantener DRY
            sys.path.insert(0, str(_REPO / "proyecto1"))
            from core.sqlite_writer import reconcile_universe_membership
            in_u, orphans = reconcile_universe_membership(conn, current_isins)
            conn.commit()
            backfill = {"in_universe": in_u, "orphans": orphans}
        else:
            print("[INFO] Backfill omitido (universo vacío); DEFAULT=1 en todas las filas.")

    return {"added": added, "backfill": backfill}


def main():
    ap = argparse.ArgumentParser(description="Migración v22 → v23 (In_Current_Universe)")
    ap.add_argument("--dry-run", action="store_true", help="solo plan, no escribe")
    args = ap.parse_args()

    conn = _get_connection()
    print("== Migración v22 → v23 ==")
    result = migrate(conn, dry_run=args.dry_run)
    print(f"[RESULT] added={result['added']}, backfill={result['backfill']}")

    if not args.dry_run:
        # Schema check rápido
        cols = {r[1] for r in conn.execute("PRAGMA table_info(fund_master)").fetchall()}
        if "In_Current_Universe" in cols:
            n_total = conn.execute("SELECT COUNT(*) FROM fund_master").fetchone()[0]
            n_in    = conn.execute("SELECT COUNT(*) FROM fund_master WHERE In_Current_Universe=1").fetchone()[0]
            n_out   = conn.execute("SELECT COUNT(*) FROM fund_master WHERE In_Current_Universe=0").fetchone()[0]
            print(f"[CHECK] fund_master rows: {n_total} total, {n_in} in-universe, {n_out} orphans")
        else:
            print("[ERROR] In_Current_Universe NO está presente en fund_master")

    conn.close()
    print("== Fin ==")


if __name__ == "__main__":
    sys.exit(main())
