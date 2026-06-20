# -*- coding: utf-8 -*-
"""
dla_table_inventory.py  v2  -- BL-DLA-2-DIAG
Inventario de tablas Cat. 1/2/3 sobre Raw_KIID_Text de la BD.

ESTRATEGIA v2 (correccion de arquitectura):
    El proyecto NO persiste PDF bytes en disco ni en BD.
    Intentar re-descargar PDFs en tiempo real es fragil (URLs caducadas,
    servidores con sesion, sin acceso a red desde entornos de desarrollo).

    La estrategia correcta es analizar Raw_KIID_Text -- el texto DLA que
    ya existe en fund_kiid_metadata -- con heuristicas textuales que detectan
    la presencia, categoria y calidad de las tablas serializadas.

    Esto es coherente con el objetivo real del diagnostico:
      "En cuantos fondos existe informacion de tabla que BL-DLA-2 podria
       explotar para poblar atributos actualmente NULL?"

    Si el texto DLA ya contiene la informacion de la tabla (serializada como
    texto plano), BL-DLA-2 necesita un parser estructurado que la extraiga.
    Si no la contiene, BL-DLA-2 necesitaria re-serializar el PDF -- lo que
    implica acceso al PDF fisico (scope diferente, mas costoso).

TAXONOMIA DE CATEGORIAS (BL-DLA-2-DIAG):
    Cat. 1 -- Tabla 1D: lista simple (monedas, clases, politicas).
              Senal: patron de lista con <= 2 columnas de datos.

    Cat. 2 -- Tabla 2D: costes, minimos, distribuciones por clase.
              Senal: >= 2 lineas con etiqueta-de-coste + valor-porcentual,
              o patron de cabecera de seccion de costes reconocible.

    Cat. 3 -- Tabla 3D+: escenarios x percentiles x horizontes.
              Senal: palabras clave de escenario + >= 2 horizontes temporales
              con valores numericos.

SALIDA:
    dla_table_inventory.csv con columnas:
        ISIN, text_len, has_cat1_signal, has_cat2_costes, has_cat2_politica,
        has_cat3_escenarios, n_pct_values, n_fee_lines,
        entry_fee_null, exit_fee_null, oc_null,
        acc_policy_null, hedging_null, ch_null,
        cat_max, processing_error

USO:
    python dla_table_inventory.py --limit 300 --seed 42
    python dla_table_inventory.py                      # todo el corpus
    python dla_table_inventory.py --project-root C:\\desarrollo\\fondos
"""

from __future__ import annotations

import argparse
import csv
import random
import re
import sys
from pathlib import Path


# == Resolucion de raiz del proyecto ==========================================

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
    # Fallback posicional: scripts/diag/ -> ../../
    fallback = start.resolve().parent.parent
    if fallback.is_dir():
        return fallback
    return Path.cwd()


_THIS_FILE         = Path(__file__).resolve()
_PROJECT_ROOT_AUTO = _find_project_root(_THIS_FILE.parent)


def _setup_path(explicit_root: str = None) -> Path:
    root = Path(explicit_root).resolve() if explicit_root else _PROJECT_ROOT_AUTO
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return root


# == Patrones de deteccion sobre texto plano ==================================

# Cat. 2 -- Tabla de costes
# Cubre texto normal y texto fusionado JPMorgan/Amundi (sin espacios, L0-FUSED)
_PAT_COSTES_HEADER = re.compile(
    r'costes?\s+de\s+entrada'
    r'|costes?\s+de\s+salida'
    r'|costes?\s+corrientes?'
    r'|composici[oó]n\s+de\s+(los\s+)?costes?'
    r'|composition\s+of\s+(the\s+)?costs?'
    r'|ongoing\s+charges?'
    r'|entry\s+(charge|fee|cost)'
    r'|exit\s+(charge|fee|cost)'
    r'|total\s+expense\s+ratio'
    r'|\bTER\b'
    r'|costesdeentrada'
    r'|costesdesalida'
    r'|costescorrientes'
    r'|ongoingcharges?'
    r'|costsofentry'
    r'|costsofexiting',
    re.IGNORECASE,
)

_PAT_FEE_LINE  = re.compile(
    r'(coste|comisi[oó]n|cargo|fee|charge|gasto|ongoing|entry|exit|'
    r'management|performance|rendimiento|[eé]xito)',
    re.IGNORECASE,
)
_PAT_PORCENTAJE = re.compile(
    r'\b\d{1,3}[,\.]\d{1,4}\s*%|\b0\s*%|\bnil\b|\bnone\b',
    re.IGNORECASE,
)

# Cat. 2 -- Politica y distribucion
_PAT_POLITICA = re.compile(
    r'(acumulaci[oó]n|distribuci[oó]n|accumulation|distribution|'
    r'cobertura\s+de\s+divisa|currency\s+hedg|hedging\s+policy|'
    r'frecuencia\s+de\s+(pago|distribuci[oó]n)|distribution\s+frequency)',
    re.IGNORECASE,
)

# Cat. 3 -- Escenarios de rendimiento
_PAT_ESCENARIO = re.compile(
    r'escenario\s+(de\s+)?(tensi[oó]n|desfavorable|moderado|favorable)'
    r'|performance\s+scenario'
    r'|stress\s+scenario'
    r'|rentabilidad\s+m[ií]nima\s+posible'
    r'|what\s+you\s+might\s+get\s+back',
    re.IGNORECASE,
)
_PAT_HORIZONTE = re.compile(r'\b([1-9]|10)\s*(a[ñn]o|year)s?\b', re.IGNORECASE)

# Cat. 1 -- Listas simples
_PAT_LISTA_CLASES = re.compile(
    r'clases?\s+de\s+(participaci[oó]n|acciones?|units?)\s*(disponibles?|ofrecidas?)?'
    r'|share\s+class(es)?\s*(available|offered)?'
    r'|clases?\s+disponibles?',
    re.IGNORECASE,
)
_PAT_LISTA_MONEDAS = re.compile(
    r'(monedas?\s+disponibles?|currencies?\s+available'
    r'|EUR|USD|GBP|CHF|JPY|NOK|SEK|CNH)\s*[,/]\s*(EUR|USD|GBP|CHF|JPY|NOK|SEK|CNH)',
    re.IGNORECASE,
)


# == Funciones auxiliares =====================================================

def count_fee_lines(text: str) -> int:
    """
    Cuenta lineas con etiqueta-fee + valor porcentual.
    Indicador de tabla Cat. 2 estructural.
    Complementa con busqueda en texto fusionado (L0-FUSED).
    """
    lines = text.split('\n')
    count = sum(
        1 for line in lines
        if _PAT_FEE_LINE.search(line) and _PAT_PORCENTAJE.search(line)
    )
    fused = text.replace(' ', '').lower()
    fused_hits = len(re.findall(
        r'(costesdeentrada|costesdesalida|costescorrientes|'
        r'ongoingcharges?|entrycost|exitcost)\d',
        fused,
    ))
    return count + fused_hits


def count_pct_values(text: str) -> int:
    return len(_PAT_PORCENTAJE.findall(text))


# == Analizador por fondo =====================================================

def analyze_text(
    isin:            str,
    raw_kiid_text:   str,
    entry_fee_null:  bool,
    exit_fee_null:   bool,
    oc_null:         bool,
    acc_policy_null: bool,
    hedging_null:    bool,
    ch_null:         bool,
) -> dict:
    result = {
        "ISIN":                isin,
        "text_len":            0,
        "has_cat1_signal":     False,
        "has_cat2_costes":     False,
        "has_cat2_politica":   False,
        "has_cat3_escenarios": False,
        "n_pct_values":        0,
        "n_fee_lines":         0,
        "entry_fee_null":      entry_fee_null,
        "exit_fee_null":       exit_fee_null,
        "oc_null":             oc_null,
        "acc_policy_null":     acc_policy_null,
        "hedging_null":        hedging_null,
        "ch_null":             ch_null,
        "cat_max":             0,
        "processing_error":    "",
    }

    if not raw_kiid_text or not raw_kiid_text.strip():
        result["processing_error"] = "empty_text"
        return result

    try:
        text = raw_kiid_text
        result["text_len"]     = len(text)
        result["n_pct_values"] = count_pct_values(text)
        result["n_fee_lines"]  = count_fee_lines(text)

        has_cat2_costes = (
            bool(_PAT_COSTES_HEADER.search(text))
            or result["n_fee_lines"] >= 2
        )
        result["has_cat2_costes"]   = has_cat2_costes
        result["has_cat2_politica"] = bool(_PAT_POLITICA.search(text))

        if _PAT_ESCENARIO.search(text):
            horizontes = set(_PAT_HORIZONTE.findall(text))
            result["has_cat3_escenarios"] = len(horizontes) >= 2

        result["has_cat1_signal"] = (
            bool(_PAT_LISTA_CLASES.search(text))
            or bool(_PAT_LISTA_MONEDAS.search(text))
        )

        if result["has_cat3_escenarios"]:
            result["cat_max"] = 3
        elif result["has_cat2_costes"] or result["has_cat2_politica"]:
            result["cat_max"] = 2
        elif result["has_cat1_signal"]:
            result["cat_max"] = 1

    except Exception as exc:
        result["processing_error"] = str(exc)[:200]

    return result


# == Entrypoint ===============================================================

def main(
    limit:        int  = None,
    seed:         int  = 42,
    project_root: str  = None,
    output_csv:   str  = None,
) -> None:

    root = _setup_path(project_root)
    print(f"[DLA-TABLE-DIAG] project_root = {root}")

    if output_csv is None:
        output_csv = str(root / "proyecto1" / "db" / "dla_table_inventory.csv")
    Path(output_csv).parent.mkdir(parents=True, exist_ok=True)
    print(f"[DLA-TABLE-DIAG] output      = {output_csv}")

    from shared.db import get_connection
    conn = get_connection()

    rows = conn.execute(
        """
        SELECT
            km.ISIN,
            km.Raw_KIID_Text,
            CASE WHEN fm.Entry_Fee_Pct       IS NULL THEN 1 ELSE 0 END,
            CASE WHEN fm.Exit_Fee_Pct        IS NULL THEN 1 ELSE 0 END,
            CASE WHEN fm.Ongoing_Charge      IS NULL THEN 1 ELSE 0 END,
            CASE WHEN fm.Accumulation_Policy IS NULL THEN 1 ELSE 0 END,
            CASE WHEN fm.Hedging_Policy      IS NULL THEN 1 ELSE 0 END,
            CASE WHEN fm.Currency_Hedged     IS NULL THEN 1 ELSE 0 END
        FROM fund_kiid_metadata km
        JOIN fund_master fm ON fm.ISIN = km.ISIN
        WHERE km.KIID_Class  = 1
          AND km.KIID_Status IN ('OK', 'CACHED')
          AND km.Raw_KIID_Text IS NOT NULL
          AND LENGTH(km.Raw_KIID_Text) > 100
        ORDER BY km.ISIN
        """
    ).fetchall()

    if not rows:
        print("[DLA-TABLE-DIAG] ERROR: no se encontraron fondos con Raw_KIID_Text en BD.")
        sys.exit(1)

    total_universo = len(rows)

    if limit and limit < total_universo:
        random.seed(seed)
        rows = random.sample(rows, limit)
        print(f"[DLA-TABLE-DIAG] Muestra aleatoria : {len(rows)} fondos "
              f"(seed={seed}, universo={total_universo})")
    else:
        print(f"[DLA-TABLE-DIAG] Universo completo : {total_universo} fondos")

    fieldnames = [
        "ISIN", "text_len",
        "has_cat1_signal", "has_cat2_costes", "has_cat2_politica",
        "has_cat3_escenarios",
        "n_pct_values", "n_fee_lines",
        "entry_fee_null", "exit_fee_null", "oc_null",
        "acc_policy_null", "hedging_null", "ch_null",
        "cat_max", "processing_error",
    ]

    total   = len(rows)
    n_cat   = [0, 0, 0, 0]   # indices 0-3
    n_err   = 0

    with open(output_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for idx, row in enumerate(rows):
            (isin, text,
             entry_null, exit_null, oc_null,
             ap_null, hp_null, ch_null) = row

            result = analyze_text(
                isin            = isin,
                raw_kiid_text   = text,
                entry_fee_null  = bool(entry_null),
                exit_fee_null   = bool(exit_null),
                oc_null         = bool(oc_null),
                acc_policy_null = bool(ap_null),
                hedging_null    = bool(hp_null),
                ch_null         = bool(ch_null),
            )
            writer.writerow(result)

            if result["processing_error"]:
                n_err += 1
            else:
                n_cat[result["cat_max"]] += 1

            if (idx + 1) % 100 == 0 or (idx + 1) == total:
                pct = (idx + 1) / total * 100
                print(f"  {idx+1:>4}/{total}  ({pct:4.1f}%)  "
                      f"cat0={n_cat[0]}  cat1={n_cat[1]}  "
                      f"cat2={n_cat[2]}  cat3={n_cat[3]}  err={n_err}")

    procesados = total - n_err
    print()
    print("--- RESUMEN DLA-TABLE-DIAG ---")
    print(f"Fondos analizados   : {total} (universo={total_universo})")
    print(f"Errores             : {n_err}")
    if procesados:
        print(f"Cat. 0 (sin senal)  : {n_cat[0]}  ({n_cat[0]/procesados*100:.1f}%)")
        print(f"Cat. 1 (lista 1D)   : {n_cat[1]}  ({n_cat[1]/procesados*100:.1f}%)")
        print(f"Cat. 2 (tabla 2D)   : {n_cat[2]}  ({n_cat[2]/procesados*100:.1f}%)")
        print(f"Cat. 3 (escenarios) : {n_cat[3]}  ({n_cat[3]/procesados*100:.1f}%)")
    print(f"CSV guardado en     : {output_csv}")
    print()
    print("Siguiente paso: ejecutar Q-DLA2-04 sobre el CSV (ver comentarios al final).")
    print("--- FIN ---")


# == CLI ======================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="BL-DLA-2-DIAG v2: inventario de tablas sobre Raw_KIID_Text.",
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--seed",  type=int, default=42)
    parser.add_argument("--project-root", type=str, default=None, dest="project_root")
    parser.add_argument("--output", type=str, default=None)
    args = parser.parse_args()
    main(
        limit=args.limit,
        seed=args.seed,
        project_root=args.project_root,
        output_csv=args.output,
    )


# =============================================================================
# QUERIES DE ANALISIS Q-DLA2-04 -- ejecutar en Python tras generar el CSV
# =============================================================================
#
# import csv, sqlite3
# conn = sqlite3.connect(r"C:\desarrollo\fondos\db\fondos.sqlite")
# conn.execute("""CREATE TEMP TABLE dla_inv (
#     ISIN TEXT, text_len INT,
#     has_cat1_signal INT, has_cat2_costes INT, has_cat2_politica INT,
#     has_cat3_escenarios INT, n_pct_values INT, n_fee_lines INT,
#     entry_fee_null INT, exit_fee_null INT, oc_null INT,
#     acc_policy_null INT, hedging_null INT, ch_null INT,
#     cat_max INT, processing_error TEXT
# )""")
# with open(r"C:\desarrollo\fondos\proyecto1\db\dla_table_inventory.csv") as f:
#     for row in csv.DictReader(f):
#         conn.execute("INSERT INTO dla_inv VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
#             [row[k] for k in [
#                 'ISIN','text_len','has_cat1_signal','has_cat2_costes',
#                 'has_cat2_politica','has_cat3_escenarios','n_pct_values',
#                 'n_fee_lines','entry_fee_null','exit_fee_null','oc_null',
#                 'acc_policy_null','hedging_null','ch_null',
#                 'cat_max','processing_error']])
#
# -- Q-DLA2-04a: ROI global
# SELECT
#     SUM(CASE WHEN has_cat2_costes=1
#              AND (entry_fee_null=1 OR exit_fee_null=1 OR oc_null=1)
#         THEN 1 ELSE 0 END)              AS roi_costes_accionable,
#     SUM(CASE WHEN has_cat2_costes=1    THEN 1 ELSE 0 END) AS total_cat2_costes,
#     SUM(CASE WHEN has_cat2_politica=1
#              AND (acc_policy_null=1 OR hedging_null=1 OR ch_null=1)
#         THEN 1 ELSE 0 END)              AS roi_politica_accionable,
#     SUM(CASE WHEN has_cat3_escenarios=1 THEN 1 ELSE 0 END) AS scope_dla3,
#     SUM(CASE WHEN cat_max=0            THEN 1 ELSE 0 END) AS sin_senal,
#     COUNT(*)                            AS total
# FROM dla_inv WHERE processing_error = '';
#
# -- Q-DLA2-04b: desglose por atributo
# SELECT
#     SUM(CASE WHEN has_cat2_costes=1 AND entry_fee_null=1 THEN 1 ELSE 0 END) AS entry_accionable,
#     SUM(CASE WHEN has_cat2_costes=1 AND exit_fee_null=1  THEN 1 ELSE 0 END) AS exit_accionable,
#     SUM(CASE WHEN has_cat2_costes=1 AND oc_null=1        THEN 1 ELSE 0 END) AS oc_accionable
# FROM dla_inv WHERE processing_error = '';
