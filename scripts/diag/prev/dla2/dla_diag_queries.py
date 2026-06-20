# -*- coding: utf-8 -*-
"""
dla_diag_queries.py -- BL-DLA-2-DIAG
Importa dla_table_inventory.csv en tabla temporal y ejecuta Q-DLA2-04.

Uso:
    python dla_diag_queries.py
    python dla_diag_queries.py --csv C:\\ruta\\custom\\inventory.csv
    python dla_diag_queries.py --project-root C:\\desarrollo\\fondos
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path


# == Resolucion de raiz =======================================================

def _find_project_root(start: Path) -> Path:
    candidate = start.resolve()
    for _ in range(8):
        if (candidate / "shared" / "db.py").exists():
            return candidate
        if (candidate / "shared").is_dir():
            return candidate
        if (candidate / "proyecto1").is_dir() and (candidate / "proyecto2").is_dir():
            return candidate
        if candidate.parent == candidate:
            break
        candidate = candidate.parent
    return start.resolve().parent.parent


_THIS_FILE         = Path(__file__).resolve()
_PROJECT_ROOT_AUTO = _find_project_root(_THIS_FILE.parent)


def _setup_path(explicit_root: str = None) -> Path:
    root = Path(explicit_root).resolve() if explicit_root else _PROJECT_ROOT_AUTO
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return root


# == Importacion CSV -> tabla temporal ========================================

DDL_TEMP = """
CREATE TEMP TABLE dla_inv (
    ISIN                TEXT,
    text_len            INTEGER,
    has_cat1_signal     INTEGER,
    has_cat2_costes     INTEGER,
    has_cat2_politica   INTEGER,
    has_cat3_escenarios INTEGER,
    n_pct_values        INTEGER,
    n_fee_lines         INTEGER,
    entry_fee_null      INTEGER,
    exit_fee_null       INTEGER,
    oc_null             INTEGER,
    acc_policy_null     INTEGER,
    hedging_null        INTEGER,
    ch_null             INTEGER,
    cat_max             INTEGER,
    processing_error    TEXT
)
"""

FIELDS = [
    "ISIN", "text_len",
    "has_cat1_signal", "has_cat2_costes", "has_cat2_politica",
    "has_cat3_escenarios", "n_pct_values", "n_fee_lines",
    "entry_fee_null", "exit_fee_null", "oc_null",
    "acc_policy_null", "hedging_null", "ch_null",
    "cat_max", "processing_error",
]

INSERT_SQL = f"INSERT INTO dla_inv VALUES ({','.join(['?'] * len(FIELDS))})"

# Campos que el CSV puede serializar como 'True'/'False' o '1'/'0'
# Se normalizan a INTEGER antes del INSERT.
_BOOL_FIELDS = {
    "has_cat1_signal", "has_cat2_costes", "has_cat2_politica",
    "has_cat3_escenarios",
    "entry_fee_null", "exit_fee_null", "oc_null",
    "acc_policy_null", "hedging_null", "ch_null",
}


def _coerce(field: str, raw: str):
    """Convierte el valor raw del CSV al tipo correcto para SQLite."""
    if field in _BOOL_FIELDS:
        return 1 if raw in ("True", "1", "true", "yes") else 0
    return raw


def load_csv(conn, csv_path: Path) -> int:
    """Carga el CSV en dla_inv. Devuelve numero de filas insertadas."""
    conn.execute(DDL_TEMP)
    n = 0
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            conn.execute(INSERT_SQL, [_coerce(k, row.get(k, "")) for k in FIELDS])
            n += 1
    conn.commit()
    return n


# == Queries ==================================================================

QUERIES = [
    (
        "Q-DLA2-04a: ROI global",
        """
        SELECT
            SUM(CASE WHEN has_cat2_costes=1
                     AND (entry_fee_null=1 OR exit_fee_null=1 OR oc_null=1)
                THEN 1 ELSE 0 END)               AS roi_costes_accionable,
            SUM(CASE WHEN has_cat2_costes=1
                THEN 1 ELSE 0 END)               AS total_cat2_costes,
            SUM(CASE WHEN has_cat2_politica=1
                     AND (acc_policy_null=1 OR hedging_null=1 OR ch_null=1)
                THEN 1 ELSE 0 END)               AS roi_politica_accionable,
            SUM(CASE WHEN has_cat3_escenarios=1
                THEN 1 ELSE 0 END)               AS scope_dla3,
            SUM(CASE WHEN cat_max=0
                THEN 1 ELSE 0 END)               AS sin_senal,
            COUNT(*)                              AS total_analizados
        FROM dla_inv
        WHERE processing_error = ''
        """,
    ),
    (
        "Q-DLA2-04b: ROI desglosado por atributo de coste",
        """
        SELECT
            SUM(CASE WHEN has_cat2_costes=1 AND entry_fee_null=1
                THEN 1 ELSE 0 END)  AS entry_accionable,
            SUM(CASE WHEN has_cat2_costes=1 AND exit_fee_null=1
                THEN 1 ELSE 0 END)  AS exit_accionable,
            SUM(CASE WHEN has_cat2_costes=1 AND oc_null=1
                THEN 1 ELSE 0 END)  AS oc_accionable
        FROM dla_inv
        WHERE processing_error = ''
        """,
    ),
    (
        "Q-DLA2-04c: distribucion de categorias",
        """
        SELECT
            cat_max,
            COUNT(*)                             AS n_fondos,
            ROUND(COUNT(*) * 100.0 /
                SUM(COUNT(*)) OVER (), 1)        AS pct
        FROM dla_inv
        WHERE processing_error = ''
        GROUP BY cat_max
        ORDER BY cat_max
        """,
    ),
    (
        "Q-DLA2-04d: errores de procesamiento",
        """
        SELECT
            processing_error,
            COUNT(*) AS n
        FROM dla_inv
        WHERE processing_error != ''
        GROUP BY processing_error
        ORDER BY n DESC
        """,
    ),
]


def run_queries(conn) -> None:
    for title, sql in QUERIES:
        print(f"\n{'='*60}")
        print(f"  {title}")
        print('='*60)
        try:
            cursor = conn.execute(sql)
            cols = [d[0] for d in cursor.description]
            rows = cursor.fetchall()
            if not rows:
                print("  (sin resultados)")
                continue
            # Calcular anchos de columna
            widths = [len(c) for c in cols]
            for row in rows:
                for i, val in enumerate(row):
                    widths[i] = max(widths[i], len(str(val) if val is not None else "NULL"))
            # Cabecera
            header = "  " + "  ".join(c.ljust(widths[i]) for i, c in enumerate(cols))
            print(header)
            print("  " + "-" * (len(header) - 2))
            for row in rows:
                line = "  " + "  ".join(
                    (str(v) if v is not None else "NULL").ljust(widths[i])
                    for i, v in enumerate(row)
                )
                print(line)
        except Exception as exc:
            print(f"  ERROR: {exc}")


# == Interpretacion de resultados =============================================

def interpret(conn) -> None:
    """Imprime la decision Go/No-Go segun umbrales de BL-DLA-2-DIAG."""
    row = conn.execute(
        """
        SELECT
            SUM(CASE WHEN has_cat2_costes=1
                     AND (entry_fee_null=1 OR exit_fee_null=1 OR oc_null=1)
                THEN 1 ELSE 0 END),
            SUM(CASE WHEN has_cat2_costes=1 THEN 1 ELSE 0 END),
            COUNT(*)
        FROM dla_inv WHERE processing_error = ''
        """
    ).fetchone()

    if not row or row[2] == 0:
        print("\n[!] No hay datos suficientes para decision Go/No-Go.")
        return

    roi, cat2_total, total = row
    # Extrapolar a universo completo si es muestra
    universo = 3204
    factor = universo / total if total > 0 else 1
    roi_extrap = round(roi * factor)

    print(f"\n{'='*60}")
    print("  DECISION Go/No-Go (BL-DLA-2-DIAG)")
    print('='*60)
    print(f"  Muestra analizada         : {total} fondos")
    print(f"  roi_costes_accionable     : {roi}  (extrap. universo: ~{roi_extrap})")
    print(f"  total_cat2_costes         : {cat2_total}  ({cat2_total/total*100:.1f}% de la muestra)")
    print()

    if roi_extrap >= 150:
        decision = "GO COMPLETO"
        detalle  = "ROI alto. Implementar BL-DLA-2 Cat.1+2 completo."
    elif roi_extrap >= 80:
        decision = "GO ACOTADO"
        detalle  = "Implementar solo detector Cat.2 para Entry/Exit/OC. Diferir Cat.1."
    elif roi_extrap >= 30:
        decision = "GO CONDICIONAL"
        detalle  = "Piloto de 25 ISINs antes del despliegue. Validar estructura regular."
    else:
        decision = "NO-GO"
        detalle  = "ROI insuficiente. Priorizar BL-55 y BL-51A residual."

    print(f"  >>> DECISION: {decision}")
    print(f"      {detalle}")
    print()
    print("  NOTA: estos resultados son sobre texto pre-DLA-1 en su mayoria.")
    print("  Repetir el diagnostico tras procesar >=300 fondos con DLA-1 activo")
    print("  para obtener la cifra definitiva.")


# == Main =====================================================================

def main(csv_path: str = None, project_root: str = None) -> None:
    root = _setup_path(project_root)
    print(f"[DLA-DIAG-QUERIES] project_root = {root}")

    if csv_path is None:
        csv_path = str(root / "proyecto1" / "db" / "dla_table_inventory.csv")
    csv_path = Path(csv_path)

    if not csv_path.exists():
        print(f"[DLA-DIAG-QUERIES] ERROR: no se encuentra el CSV en {csv_path}")
        print("  Ejecuta primero: python dla_table_inventory.py --limit 300 --seed 42")
        sys.exit(1)

    print(f"[DLA-DIAG-QUERIES] CSV      = {csv_path}")

    from shared.db import get_connection
    conn = get_connection()

    n = load_csv(conn, csv_path)
    print(f"[DLA-DIAG-QUERIES] {n} filas importadas en dla_inv (tabla temporal)")

    run_queries(conn)
    interpret(conn)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="BL-DLA-2-DIAG: importa CSV y ejecuta Q-DLA2-04.",
    )
    parser.add_argument("--csv", type=str, default=None,
                        help="Ruta del CSV generado por dla_table_inventory.py")
    parser.add_argument("--project-root", type=str, default=None, dest="project_root")
    args = parser.parse_args()
    main(csv_path=args.csv, project_root=args.project_root)
