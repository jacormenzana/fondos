# -*- coding: utf-8 -*-
"""
normalize_db_casing_v20.py — barrido idempotente de casing/valor sobre fund_master.

Motivo (root-cause, §C-2 CACHED/COALESCE): `_normalize_record` solo canoniza el
record ENTRANTE; en fondos CACHED el valor entrante es None y COALESCE preserva el
valor STALE de la BD (casing v19 UPPER) sin pasar por el normalizador. Resultado:
casing mixto en columnas categóricas (Hedging_Policy 'UNHEDGED' vs 'Unhedged', etc.).

Este script aplica, sobre TODA la tabla, la MISMA canonicalización que
classify_utils.normalize_casing (config.DOMAIN_VALUES = fuente única) + los remaps
de valor legacy (config.LEGACY_VALUE_REMAP, p.ej. 'PARTIAL'→'Partially Hedged').

Propiedades:
  - DRY: deriva todo de config; no hardcodea vocabularios.
  - Idempotente: re-ejecutar no cambia filas ya canónicas.
  - Solo columnas TITLE con dominio cerrado (no toca flags UPPER_SNAKE/CODE/NUM).
  - Case-insensitive (casefold + colapso de espacios/guiones), igual que el runtime.
  - Reporta filas afectadas por columna; no inventa valores (no-match → intacto).

Uso (Windows, env des):
    python -X utf8 -m proyecto1.core.normalize_db_casing_v20 --db C:\\desarrollo\\fondos\\db\\fondos.sqlite
    python -X utf8 -m proyecto1.core.normalize_db_casing_v20 --db ... --dry-run
"""
import argparse
import re
import sqlite3
import sys

try:
    from shared import config as _cfg
except ImportError:
    import config as _cfg  # type: ignore


def _casefold_key(s: str) -> str:
    return re.sub(r"[\s_]+", " ", s.strip().casefold())


def _build_plan():
    """{column: {casefold_key|legacy_value: canonical}} para columnas TITLE cerradas."""
    dv = getattr(_cfg, "DOMAIN_VALUES", {})
    casing = getattr(_cfg, "ATTRIBUTE_CASING", {})
    legacy = getattr(_cfg, "LEGACY_VALUE_REMAP", {})
    plan = {}
    for col, vals in dv.items():
        if casing.get(col) != "TITLE":
            continue
        lookup = {_casefold_key(v): v for v in vals}
        plan[col] = {"casing": lookup, "legacy": legacy.get(col, {})}
    return plan


def _canonical(col_plan, value):
    if value is None:
        return None
    rm = col_plan["legacy"]
    if value in rm:
        value = rm[value]
    return col_plan["casing"].get(_casefold_key(value), value)


def run(db_path: str, dry_run: bool = False) -> dict:
    plan = _build_plan()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    existing = {r[1] for r in conn.execute("PRAGMA table_info(fund_master)").fetchall()}
    report = {}
    try:
        for col, col_plan in plan.items():
            if col not in existing:
                continue
            rows = conn.execute(
                f"SELECT ISIN, {col} AS v FROM fund_master "
                f"WHERE {col} IS NOT NULL"
            ).fetchall()
            updates = [
                (canon, r["ISIN"])
                for r in rows
                if (canon := _canonical(col_plan, r["v"])) != r["v"]
            ]
            report[col] = len(updates)
            if updates and not dry_run:
                conn.executemany(
                    f"UPDATE fund_master SET {col}=? WHERE ISIN=?", updates
                )
        if not dry_run:
            conn.commit()
    finally:
        conn.close()
    return report


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    rep = run(args.db, args.dry_run)
    mode = "DRY-RUN (sin escribir)" if args.dry_run else "APLICADO"
    print(f"[normalize_db_casing_v20] {mode} sobre {args.db}")
    total = 0
    for col, n in sorted(rep.items()):
        if n:
            print(f"  {col:24} filas corregidas: {n}")
            total += n
    print(f"  TOTAL corregidas: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
