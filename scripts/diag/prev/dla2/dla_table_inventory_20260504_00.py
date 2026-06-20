# -*- coding: utf-8 -*-
"""
dla_table_inventory.py — BL-DLA-2-DIAG
Script de diagnóstico: inventario de tablas Cat. 1/2/3 en PDFs del corpus.

Propósito:
    Q-DLA2-02: ¿En qué porcentaje de KIIDs pdfplumber detecta al menos una tabla?
    Q-DLA2-03: ¿Cuál es la distribución de Categorías 1, 2, 3 por página y por fondo?

Taxonomía de categorías (BL-DLA-2-DIAG):
    Cat. 1 — Tabla 1D: una sola fila O una sola columna con contenido.
              Ninguna celda combinada. n_rows <= 2 OR n_cols <= 2.
              Atributos típicos: lista de monedas, clases, políticas simples.

    Cat. 2 — Tabla 2D: filas × columnas, posibles celdas combinadas (None en extract()).
              n_rows >= 2 AND n_cols >= 2.
              Atributos típicos: costes, mínimos, frecuencias, coberturas por clase.

    Cat. 3 — Tabla 3D+: estructura con encabezados jerárquicos de 2+ niveles,
              o tabla con ≥3 dimensiones semánticas efectivas (escenario × percentil
              × horizonte). Señal heurística: n_rows >= 4 AND n_cols >= 4
              AND presencia de tokens "escenario|scenario|stress|tensión|favorabl"
              en el texto de la tabla o en el bloque de texto inmediatamente superior.

Salida:
    dla_table_inventory.csv con columnas:
        ISIN, n_pages_processed, n_tables_detected, n_cat1, n_cat2, n_cat3,
        has_cost_table (bool), has_scenario_table (bool), cat_max,
        table_page_positions (JSON), processing_error

Uso:
    python dla_table_inventory.py --limit 300 --seed 42
    python dla_table_inventory.py  # procesa todos los ISINs con PDF en cache

Prerequisito: ejecutar sobre fondos con KIID_Status='OK' (PDF físico disponible).
"""

import csv
import json
import random
import re
import sys
from io import BytesIO
from pathlib import Path

import pdfplumber

# ── Configuración ──────────────────────────────────────────────────────────────

OUTPUT_CSV    = r"C:\desarrollo\fondos\proyecto1\db\dla_table_inventory.csv"
MAX_PDF_PAGES = 3   # igual que io.py

# Umbrales de clasificación de categoría
_CAT3_MIN_ROWS  = 4
_CAT3_MIN_COLS  = 4
_CAT3_KEYWORDS  = re.compile(
    r'escenario|scenario|stress|tensi[oó]n|favorable|desfavorable|moderado|'
    r'rendimiento\s+en\s+condiciones|performance\s+scenario',
    re.IGNORECASE
)
_COST_KEYWORDS  = re.compile(
    r'gastos|costes?|comisi[oó]n|cargo|charge|fee|ongoing|entry|exit|'
    r'coste\s+total|total\s+cost|TER\b',
    re.IGNORECASE
)


# ── Clasificador de categoría de tabla ─────────────────────────────────────────

def classify_table_category(table_data: list, context_text: str = "") -> int:
    """
    Clasifica una tabla extraída por pdfplumber en Categoría 1, 2 o 3.

    Args:
        table_data:    lista de listas (output de table_obj.extract())
        context_text:  texto del bloque inmediatamente anterior a la tabla
                       (para detectar señales Cat. 3 en el contexto)

    Returns:
        1, 2 o 3
    """
    if not table_data:
        return 1

    n_rows = len(table_data)
    # n_cols = máximo de columnas con contenido (descartando None)
    n_cols = max(
        sum(1 for cell in row if cell is not None and str(cell).strip())
        for row in table_data
    ) if n_rows > 0 else 0

    # Texto serializado de la tabla para búsqueda de keywords
    table_text = " ".join(
        str(cell) for row in table_data
        for cell in row if cell is not None
    )
    combined_text = table_text + " " + context_text

    # Cat. 3: matriz escenarios × percentiles × horizontes
    if (n_rows >= _CAT3_MIN_ROWS
            and n_cols >= _CAT3_MIN_COLS
            and _CAT3_KEYWORDS.search(combined_text)):
        return 3

    # Cat. 1: tabla 1D (lista simple)
    if n_rows <= 2 or n_cols <= 2:
        return 1

    # Cat. 2: tabla 2D estándar
    return 2


def is_cost_table(table_data: list, context_text: str = "") -> bool:
    """True si la tabla contiene información de costes/comisiones (Cat. 2 objetivo)."""
    table_text = " ".join(
        str(cell) for row in table_data
        for cell in row if cell is not None
    )
    return bool(_COST_KEYWORDS.search(table_text + " " + context_text))


def is_scenario_table(table_data: list, context_text: str = "") -> bool:
    """True si la tabla contiene escenarios de rendimiento (Cat. 3)."""
    table_text = " ".join(
        str(cell) for row in table_data
        for cell in row if cell is not None
    )
    return bool(_CAT3_KEYWORDS.search(table_text + " " + context_text))


# ── Procesador por ISIN ─────────────────────────────────────────────────────────

def analyze_isin(isin: str, pdf_bytes: bytes) -> dict:
    """
    Analiza un PDF y devuelve el inventario de tablas.

    Returns:
        dict con las columnas del CSV de salida.
    """
    result = {
        "ISIN":                isin,
        "n_pages_processed":   0,
        "n_tables_detected":   0,
        "n_cat1":              0,
        "n_cat2":              0,
        "n_cat3":              0,
        "has_cost_table":      False,
        "has_scenario_table":  False,
        "cat_max":             0,
        "table_page_positions": "[]",
        "processing_error":    "",
    }

    try:
        with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
            table_positions = []

            for page_idx, page in enumerate(pdf.pages):
                if page_idx >= MAX_PDF_PAGES:
                    break

                result["n_pages_processed"] += 1

                # Texto de la página para contexto de keywords
                page_text = page.extract_text() or ""

                tables = page.find_tables()
                for tbl in tables:
                    try:
                        data = tbl.extract()
                        if not data:
                            continue

                        # Texto inmediatamente anterior a la tabla (aprox. 300 chars)
                        bbox_top = tbl.bbox[1]
                        # Región por encima de la tabla en la misma página
                        above_region = page.crop(
                            (0, max(0, bbox_top - 80), page.width, bbox_top)
                        )
                        context = above_region.extract_text() or ""

                        cat = classify_table_category(data, context)
                        cost = is_cost_table(data, context)
                        scen = is_scenario_table(data, context)

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
                            "rows": len(data),
                            "cols": max(
                                (sum(1 for c in row if c is not None and str(c).strip())
                                 for row in data), default=0
                            ),
                        })

                    except Exception as tbl_exc:
                        # tabla individual no procesable — registrar y continuar
                        result["n_tables_detected"] += 1
                        table_positions.append({
                            "page": page_idx, "cat": 0,
                            "error": str(tbl_exc)[:80]
                        })

            result["cat_max"] = max(
                (p.get("cat", 0) for p in table_positions), default=0
            )
            result["table_page_positions"] = json.dumps(table_positions)

    except Exception as exc:
        result["processing_error"] = str(exc)[:200]

    return result


# ── Entrypoint ──────────────────────────────────────────────────────────────────

def main(limit: int = None, seed: int = 42):
    """
    Carga los ISINs con PDF disponible desde la BD, procesa y escribe CSV.
    Adaptar get_isins_with_pdf() a la función de acceso a cache del proyecto.
    """
    from shared.db import get_connection  # adaptación al proyecto

    conn = get_connection()
    rows = conn.execute(
        "SELECT ISIN FROM fund_kiid_metadata WHERE KIID_Status = 'OK'"
    ).fetchall()
    isins = [r[0] for r in rows]

    if limit:
        random.seed(seed)
        isins = random.sample(isins, min(limit, len(isins)))

    print(f"[DLA-TABLE-DIAG] Procesando {len(isins)} ISINs...")

    fieldnames = [
        "ISIN", "n_pages_processed", "n_tables_detected",
        "n_cat1", "n_cat2", "n_cat3",
        "has_cost_table", "has_scenario_table", "cat_max",
        "table_page_positions", "processing_error",
    ]

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for i, isin in enumerate(isins):
            pdf_bytes = load_pdf_from_cache(isin)   # función existente en el proyecto
            if not pdf_bytes:
                continue
            row = analyze_isin(isin, pdf_bytes)
            writer.writerow(row)
            if (i + 1) % 50 == 0:
                print(f"  {i + 1}/{len(isins)} procesados...")

    print(f"[DLA-TABLE-DIAG] Completado. CSV en: {OUTPUT_CSV}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--seed",  type=int, default=42)
    args = parser.parse_args()
    main(limit=args.limit, seed=args.seed)