# -*- coding: utf-8 -*-
"""
dla_table_inventory.py — BL-DLA-2-DIAG
Script de diagnóstico: inventario de tablas Cat. 1/2/3 en PDFs del corpus.

Propósito:
    Q-DLA2-02: En que porcentaje de KIIDs pdfplumber detecta al menos una tabla.
    Q-DLA2-03: Distribucion de Categorias 1, 2, 3 por pagina y por fondo.

Taxonomia de categorias:
    Cat. 1 -- Tabla 1D: una sola fila O una sola columna con contenido.
              Sin celdas combinadas. n_rows <= 2 OR n_cols_with_content <= 2.
              Tipico: lista de monedas, clases, politicas simples.

    Cat. 2 -- Tabla 2D: filas x columnas, posibles celdas combinadas (None en extract()).
              n_rows >= 3 AND n_cols_with_content >= 3.
              Tipico: costes, minimos, frecuencias, coberturas por clase.

    Cat. 3 -- Tabla 3D+: encabezados jerarquicos de 2+ niveles, o escenarios x
              percentiles x horizontes.
              Senal heuristica: n_rows >= 4 AND n_cols >= 4 AND keywords Cat.3
              en texto de la tabla o en el bloque inmediatamente superior.

Salida:
    dla_table_inventory.csv con columnas:
        ISIN, n_pages_processed, n_tables_detected, n_cat1, n_cat2, n_cat3,
        has_cost_table, has_scenario_table, cat_max,
        table_page_positions (JSON), processing_error

Uso:
    python dla_table_inventory.py --limit 300 --seed 42
    python dla_table_inventory.py          # todos los ISINs con KIID_URL

Requisito: ejecutar desde cualquier directorio con --project-root o desde la
           raiz del proyecto (C:/desarrollo/fondos). El script anade la raiz al
           sys.path automaticamente para encontrar shared.db e io.

Nota sobre descarga:
    El proyecto NO persiste los PDF bytes en disco ni en BD. El diagnostico
    necesita los PDF fisicos para que pdfplumber.find_tables() funcione.
    El script re-descarga cada PDF desde KIID_URL usando la infraestructura
    de io.download_pdf(), respetando los mismos timeouts y reintentos que el
    pipeline. Los bytes se mantienen en memoria y no se persisten.
    Se respeta MAX_PDF_PAGES = 3.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
import time
from io import BytesIO
from pathlib import Path

# ── Resolucion de raiz del proyecto ───────────────────────────────────────────
# El script puede estar en:  <raiz>/scripts/diag/dla_table_inventory.py
# o en:                      <raiz>/proyecto1/scripts/diag/...
# En ambos casos, subir hasta encontrar el directorio que contiene 'shared/'.

def _find_project_root(start: Path) -> Path:
    """
    Asciende desde 'start' hasta encontrar el directorio raiz del proyecto.

    Marcadores reconocidos (en orden de preferencia):
      1. Contiene shared/db.py          (marcador mas especifico del proyecto)
      2. Contiene shared/ como directorio  (cualquier estructura shared)
      3. Contiene proyecto1/ AND proyecto2/ (estructura de carpetas del proyecto)

    Fallback final: <script_dir>/../../..  (asumiendo scripts/diag/ dentro del proyecto)
    """
    candidate = start.resolve()
    for _ in range(8):
        # Marcador 1: shared/db.py
        if (candidate / "shared" / "db.py").exists():
            return candidate
        # Marcador 2: directorio shared/ existe
        if (candidate / "shared").is_dir():
            return candidate
        # Marcador 3: estructura proyecto1 + proyecto2
        if (candidate / "proyecto1").is_dir() and (candidate / "proyecto2").is_dir():
            return candidate
        if candidate.parent == candidate:
            break
        candidate = candidate.parent

    # Fallback: desde scripts/diag/ subir dos niveles da la raiz
    # Estructura esperada: <raiz>/scripts/diag/dla_table_inventory.py
    fallback = start.resolve().parent.parent
    if fallback.is_dir():
        return fallback
    return Path.cwd()


_THIS_FILE   = Path(__file__).resolve()
_PROJECT_ROOT_AUTO = _find_project_root(_THIS_FILE.parent)


def _setup_path(explicit_root: str = None) -> Path:
    root = Path(explicit_root).resolve() if explicit_root else _PROJECT_ROOT_AUTO
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return root


# ── Configuracion ──────────────────────────────────────────────────────────────

MAX_PDF_PAGES = 3        # igual que io.py
DELAY_BETWEEN_DOWNLOADS = 0.3   # segundos entre descargas (cortesia al servidor)
MAX_DOWNLOAD_ERRORS = 20  # abortar si hay demasiados errores de red seguidos

# Umbrales de clasificacion de categoria
_CAT3_MIN_ROWS = 4
_CAT3_MIN_COLS = 4
_CAT3_KEYWORDS = re.compile(
    r'escenario|scenario|stress|tensi[oó]n|favorable|desfavorable|moderado|'
    r'rendimiento\s+en\s+condiciones|performance\s+scenario',
    re.IGNORECASE,
)
_COST_KEYWORDS = re.compile(
    r'gastos|costes?|comisi[oó]n|cargo|charge|fee|ongoing|entry|exit|'
    r'coste\s+total|total\s+cost|TER\b',
    re.IGNORECASE,
)


# ── Clasificador de categoria ─────────────────────────────────────────────────

def _count_cols_with_content(row: list) -> int:
    """Numero de celdas de una fila con contenido no vacio."""
    return sum(1 for cell in row if cell is not None and str(cell).strip())


def classify_table_category(table_data: list, context_text: str = "") -> int:
    """
    Clasifica una tabla extraida por pdfplumber en Categoria 1, 2 o 3.

    Args:
        table_data:   lista de listas (output de table_obj.extract())
        context_text: texto del bloque inmediatamente anterior a la tabla

    Returns:
        1, 2 o 3
    """
    if not table_data:
        return 1

    n_rows = len(table_data)
    n_cols_max = max(
        (_count_cols_with_content(row) for row in table_data),
        default=0,
    )

    # Texto serializado para busqueda de keywords
    table_text = " ".join(
        str(cell) for row in table_data for cell in row if cell is not None
    )
    combined = table_text + " " + context_text

    # Cat. 3: escenarios x percentiles x horizontes
    if (n_rows >= _CAT3_MIN_ROWS
            and n_cols_max >= _CAT3_MIN_COLS
            and _CAT3_KEYWORDS.search(combined)):
        return 3

    # Cat. 1: tabla 1D (lista simple)
    if n_rows <= 2 or n_cols_max <= 2:
        return 1

    # Cat. 2: tabla 2D estandar
    return 2


def is_cost_table(table_data: list, context_text: str = "") -> bool:
    """True si la tabla contiene informacion de costes/comisiones."""
    table_text = " ".join(
        str(cell) for row in table_data for cell in row if cell is not None
    )
    return bool(_COST_KEYWORDS.search(table_text + " " + context_text))


def is_scenario_table(table_data: list, context_text: str = "") -> bool:
    """True si la tabla contiene escenarios de rendimiento (scope Cat. 3 / BL-DLA-3)."""
    table_text = " ".join(
        str(cell) for row in table_data for cell in row if cell is not None
    )
    return bool(_CAT3_KEYWORDS.search(table_text + " " + context_text))


# ── Procesador por ISIN ────────────────────────────────────────────────────────

def analyze_pdf(isin: str, pdf_bytes: bytes) -> dict:
    """
    Analiza un PDF y devuelve el inventario de tablas.
    Usa pdfplumber.find_tables() — no DLA-aware (el objetivo es ver la
    detectabilidad bruta de tablas, independientemente de la serializacion).
    """
    import pdfplumber  # importacion diferida: falla clara si no instalado

    result = {
        "ISIN":                 isin,
        "n_pages_processed":    0,
        "n_tables_detected":    0,
        "n_cat1":               0,
        "n_cat2":               0,
        "n_cat3":               0,
        "has_cost_table":       False,
        "has_scenario_table":   False,
        "cat_max":              0,
        "table_page_positions": "[]",
        "processing_error":     "",
    }

    table_positions: list = []

    try:
        with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
            for page_idx, page in enumerate(pdf.pages):
                if page_idx >= MAX_PDF_PAGES:
                    break

                result["n_pages_processed"] += 1

                try:
                    tables = page.find_tables()
                except Exception as find_exc:
                    table_positions.append({
                        "page": page_idx, "cat": 0,
                        "error": f"find_tables: {str(find_exc)[:80]}",
                    })
                    continue

                for tbl in tables:
                    try:
                        data = tbl.extract()
                        if not data:
                            continue

                        # Contexto inmediatamente encima de la tabla (~80 pts)
                        try:
                            bbox_top = tbl.bbox[1]
                            above = page.crop(
                                (0, max(0, bbox_top - 80), page.width, bbox_top)
                            )
                            context = above.extract_text() or ""
                        except Exception:
                            context = ""

                        cat  = classify_table_category(data, context)
                        cost = is_cost_table(data, context)
                        scen = is_scenario_table(data, context)

                        n_rows = len(data)
                        n_cols = max(
                            (_count_cols_with_content(row) for row in data),
                            default=0,
                        )

                        result["n_tables_detected"] += 1
                        if cat == 1:
                            result["n_cat1"] += 1
                        elif cat == 2:
                            result["n_cat2"] += 1
                        elif cat == 3:
                            result["n_cat3"] += 1

                        if cost:
                            result["has_cost_table"] = True
                        if scen:
                            result["has_scenario_table"] = True

                        table_positions.append({
                            "page": page_idx,
                            "cat":  cat,
                            "cost": cost,
                            "scen": scen,
                            "rows": n_rows,
                            "cols": n_cols,
                        })

                    except Exception as tbl_exc:
                        result["n_tables_detected"] += 1
                        table_positions.append({
                            "page": page_idx, "cat": 0,
                            "error": str(tbl_exc)[:80],
                        })

    except Exception as exc:
        result["processing_error"] = str(exc)[:200]
        return result

    result["cat_max"] = max(
        (p.get("cat", 0) for p in table_positions), default=0
    )
    result["table_page_positions"] = json.dumps(table_positions, ensure_ascii=False)
    return result


# ── Descarga de PDF ────────────────────────────────────────────────────────────

def download_pdf_for_isin(isin: str, kiid_url: str) -> tuple:
    """
    Descarga el PDF de kiid_url usando la infraestructura de io.download_pdf().
    Devuelve (pdf_bytes, error_str). Si falla, pdf_bytes=None.
    """
    try:
        from core.io import download_pdf
        pdf_bytes = download_pdf(kiid_url)
        return pdf_bytes, ""
    except Exception as exc:
        return None, str(exc)[:200]


# ── Entrypoint ─────────────────────────────────────────────────────────────────

def main(
    limit:        int  = None,
    seed:         int  = 42,
    project_root: str  = None,
    output_csv:   str  = None,
) -> None:
    # 1. Ajustar sys.path
    root = _setup_path(project_root)
    print(f"[DLA-TABLE-DIAG] project_root = {root}")

    # 2. Resolver ruta de salida por defecto
    if output_csv is None:
        output_csv = str(root / "proyecto1" / "db" / "dla_table_inventory.csv")
    Path(output_csv).parent.mkdir(parents=True, exist_ok=True)
    print(f"[DLA-TABLE-DIAG] output      = {output_csv}")

    # 3. Conectar a BD
    from shared.db import get_connection
    conn = get_connection()

    # 4. Recuperar ISINs con KIID_URL disponible y KIID_Status OK/CACHED
    rows = conn.execute(
        """
        SELECT ISIN, KIID_URL
        FROM   fund_kiid_metadata
        WHERE  KIID_Class  = 1
          AND  KIID_Status IN ('OK', 'CACHED')
          AND  KIID_URL    IS NOT NULL
          AND  KIID_URL    != ''
        ORDER  BY ISIN
        """
    ).fetchall()

    if not rows:
        print("[DLA-TABLE-DIAG] ERROR: no se encontraron ISINs con KIID_URL en BD.")
        sys.exit(1)

    isin_url_pairs = [(r[0], r[1]) for r in rows]

    if limit and limit < len(isin_url_pairs):
        random.seed(seed)
        isin_url_pairs = random.sample(isin_url_pairs, limit)
        print(f"[DLA-TABLE-DIAG] Muestra aleatoria: {len(isin_url_pairs)} ISINs "
              f"(seed={seed}, universo={len(rows)})")
    else:
        print(f"[DLA-TABLE-DIAG] Procesando universo completo: {len(isin_url_pairs)} ISINs")

    # 5. Procesar y escribir CSV
    fieldnames = [
        "ISIN", "n_pages_processed", "n_tables_detected",
        "n_cat1", "n_cat2", "n_cat3",
        "has_cost_table", "has_scenario_table", "cat_max",
        "table_page_positions", "processing_error",
    ]

    total          = len(isin_url_pairs)
    n_ok           = 0
    n_download_err = 0
    n_no_tables    = 0
    consec_errors  = 0

    with open(output_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for idx, (isin, kiid_url) in enumerate(isin_url_pairs):
            # Descarga
            pdf_bytes, dl_error = download_pdf_for_isin(isin, kiid_url)

            if not pdf_bytes:
                n_download_err += 1
                consec_errors  += 1
                row = {k: "" for k in fieldnames}
                row["ISIN"]             = isin
                row["processing_error"] = f"download_error: {dl_error}"
                writer.writerow(row)

                if consec_errors >= MAX_DOWNLOAD_ERRORS:
                    print(f"\n[DLA-TABLE-DIAG] ABORT: {consec_errors} errores de "
                          f"descarga consecutivos. Revisar conectividad.")
                    break
                continue

            consec_errors = 0

            # Analisis de tablas
            result = analyze_pdf(isin, pdf_bytes)
            del pdf_bytes  # liberar memoria inmediatamente

            writer.writerow(result)

            if result["processing_error"]:
                n_download_err += 1
            elif result["n_tables_detected"] == 0:
                n_no_tables += 1
            else:
                n_ok += 1

            # Progreso
            if (idx + 1) % 50 == 0 or (idx + 1) == total:
                pct = (idx + 1) / total * 100
                print(f"  {idx + 1:>4}/{total}  ({pct:4.1f}%)  "
                      f"ok={n_ok}  sin_tabla={n_no_tables}  "
                      f"err={n_download_err}")

            time.sleep(DELAY_BETWEEN_DOWNLOADS)

    # 6. Resumen
    print("\n--- RESUMEN DLA-TABLE-DIAG ---")
    print(f"ISINs procesados       : {min(idx + 1, total)}")
    print(f"Con tablas detectadas  : {n_ok}")
    print(f"Sin tablas             : {n_no_tables}")
    print(f"Errores (descarga/pdf) : {n_download_err}")
    print(f"CSV guardado en        : {output_csv}")
    print("--- FIN ---")


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="BL-DLA-2-DIAG: inventario de tablas Cat.1/2/3 en PDFs del corpus.",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Numero maximo de ISINs a procesar (muestra aleatoria). "
             "Sin este parametro procesa todo el corpus.",
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="Semilla para la muestra aleatoria (default: 42).",
    )
    parser.add_argument(
        "--project-root", type=str, default=None,
        dest="project_root",
        help="Ruta raiz del proyecto (donde esta la carpeta 'shared/'). "
             "Si se omite, se detecta automaticamente ascendiendo desde el "
             "directorio del script.",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Ruta del CSV de salida. "
             "Por defecto: <project_root>/proyecto1/db/dla_table_inventory.csv",
    )
    args = parser.parse_args()

    main(
        limit=args.limit,
        seed=args.seed,
        project_root=args.project_root,
        output_csv=args.output,
    )
