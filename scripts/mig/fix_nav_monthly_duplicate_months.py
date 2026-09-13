# scripts/mig/fix_nav_monthly_duplicate_months.py
# -*- coding: utf-8 -*-
"""
FIX-NAV-OPEN-MONTH-1 — Saneado de filas duplicadas en fund_nav_monthly.

Problema (2026-09-13, ver doc/reglas/AUDITORIA_ESTADISTICA.md §2.7):
    _write_nav_rows() usaba INSERT OR IGNORE sobre la PK real de la tabla
    (ISIN, Date), pero la clave semántica de una fila mensual es
    (ISIN, YYYY-MM). Cada ejecución de ingesta durante un mes aún abierto
    insertaba una fila con una Date distinta (el último día disponible en
    ese momento) en vez de sustituir la fila provisional del mismo mes, y
    por ser OR IGNORE (no OR REPLACE) un mes ya cerrado podía quedar
    permanentemente fijado en su primer valor provisional.

    Medido en producción (2026-09-13): 6.876 pares (ISIN, mes) con más de
    una fila (13.308 filas sobrantes), 99% concentradas en los 2 meses más
    recientes.

Corrección de código: _write_nav_rows() en
proyecto2/src/discovery/nav_discovery.py ya hace un DELETE por
(ISIN, YYYY-MM) antes de insertar — evita que el problema siga creciendo.
Este script sanea los datos YA corrompidos por el bug antes de esa
corrección: por cada (ISIN, mes) con varias filas, conserva la de fecha
más alta (la que _resample_to_monthly() siempre pretendió producir) y
elimina las demás.

El script es IDEMPOTENTE: tras aplicarlo, una segunda ejecución no
encuentra nada que corregir.

Uso (ejecutar sobre copia de seguridad primero):
    # 1. Copia de seguridad
    copy db\\fondos.sqlite db\\fondos_backup_20260913.sqlite

    # 2. Verificación en seco (comportamiento por defecto — no escribe nada)
    python scripts\\mig\\fix_nav_monthly_duplicate_months.py

    # 3. Ejecución real
    python scripts\\mig\\fix_nav_monthly_duplicate_months.py --apply

Tras aplicar, los ISINs afectados quedan con una huella de entrada (NAV)
distinta -> el fingerprint de fund_metric_state ya no coincide, así que un
ciclo P2 normal (P2_calculateIndicators.bat / run_pipeline.py) los
recalculará solo, sin necesidad de tocar CALC_VERSION.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent.parent  # c:/desarrollo/fondos
_DB_PATH = _REPO_ROOT / "db" / "fondos.sqlite"

_COUNT_QUERY = """
    SELECT ISIN, substr(Date,1,7) AS ym, COUNT(*) AS n
    FROM fund_nav_monthly
    GROUP BY ISIN, ym
    HAVING n > 1
"""

_DELETE_QUERY = """
    DELETE FROM fund_nav_monthly
    WHERE rowid NOT IN (
        SELECT rowid FROM (
            SELECT rowid,
                   ROW_NUMBER() OVER (
                       PARTITION BY ISIN, substr(Date,1,7)
                       ORDER BY Date DESC
                   ) AS rn
            FROM fund_nav_monthly
        )
        WHERE rn = 1
    )
"""


def _measure(conn: sqlite3.Connection) -> tuple[int, int]:
    """Returns (n_isin_month_pairs_with_duplicates, n_removable_rows)."""
    rows = conn.execute(_COUNT_QUERY).fetchall()
    n_pairs = len(rows)
    n_removable = sum(n - 1 for _isin, _ym, n in rows)
    return n_pairs, n_removable


def main() -> int:
    parser = argparse.ArgumentParser(
        description="FIX-NAV-OPEN-MONTH-1: elimina filas duplicadas por (ISIN, mes) en fund_nav_monthly."
    )
    parser.add_argument(
        "--db", default=str(_DB_PATH),
        help=f"Ruta a la base de datos SQLite (default: {_DB_PATH})",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Ejecuta el DELETE real. Sin este flag, solo reporta lo que se haría (dry-run).",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: BD no encontrada en {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(db_path))

    n_pairs_before, n_removable_before = _measure(conn)
    print(f"Pares (ISIN, mes) con filas duplicadas: {n_pairs_before}")
    print(f"Filas eliminables (sobrantes): {n_removable_before}")

    if n_pairs_before == 0:
        print("Nada que corregir — la BD ya está limpia.")
        conn.close()
        return 0

    if not args.apply:
        sample = conn.execute(_COUNT_QUERY + " ORDER BY n DESC LIMIT 5").fetchall()
        print("\nMuestra de los pares más afectados (ISIN, mes, n_filas):")
        for isin, ym, n in sample:
            print(f"  {isin}  {ym}  {n} filas")
        print("\n[DRY-RUN] Ningún cambio aplicado. Ejecutar con --apply para corregir.")
        conn.close()
        return 0

    conn.execute(_DELETE_QUERY)
    conn.commit()

    n_pairs_after, n_removable_after = _measure(conn)
    print(f"\n[APLICADO] Filas eliminadas: {n_removable_before - n_removable_after}")
    print(f"Pares (ISIN, mes) con duplicados restantes: {n_pairs_after}")
    if n_pairs_after == 0:
        print("OK: fund_nav_monthly saneado — un valor por (ISIN, mes).")
    else:
        print(
            f"AVISO: quedan {n_pairs_after} pares con duplicados — revisar manualmente.",
            file=sys.stderr,
        )

    print(
        "\nRecuerda ejecutar un ciclo P2 normal (P2_calculateIndicators.bat) para que "
        "los ISINs afectados recalculen sus métricas a partir del NAV corregido — el "
        "fingerprint de entrada ya cambió, no hace falta tocar CALC_VERSION."
    )

    conn.close()
    return 0 if n_pairs_after == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
