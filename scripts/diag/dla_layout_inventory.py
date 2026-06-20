# -*- coding: utf-8 -*-
"""
scripts/diag/dla_layout_inventory.py — Q-DLA-03 (Fase 0 BL-DLA)

Inventario físico de layout multi-columna sobre el corpus de KIIDs.
Analiza cada PDF y clasifica cada página como:
    - SINGLE_COL  : layout de una sola columna (lectura natural top-bottom)
    - TWO_COL     : layout de dos columnas (susceptible a patología DLA)
    - MIXED       : indeterminado (mezcla de bloques anchos y estrechos sin patrón claro)
    - NO_TEXT     : página sin capa de texto (solo imagen — OCR-aware necesario)

Output:
    db/dla_layout_inventory.csv
    Columnas: ISIN, Source, n_pages_total, n_pages_single, n_pages_two_col,
              n_pages_mixed, n_pages_no_text, has_two_col, layout_signature

USO DESDE cmd.exe (entorno conda "des"):

    chcp 65001
    cd c:\\desarrollo\\fondos
    python -X utf8 scripts\\diag\\dla_layout_inventory.py --mode local --pdf_dir c:\\ruta\\a\\pdfs
    python -X utf8 scripts\\diag\\dla_layout_inventory.py --mode db_redownload --max_funds 100
    python -X utf8 scripts\\diag\\dla_layout_inventory.py --mode db_redownload --random_sample 50

REQUISITOS:
    - Conda env "des" activado.
    - PyMuPDF instalado (pip install pymupdf).
    - Acceso a db\\fondos.sqlite (modos db_*).
    - Acceso a Excel maestro si modo db_redownload (idéntico al de pipeline.py).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import random
import sqlite3
import sys
import time
from io import BytesIO
from pathlib import Path
from typing import Optional, List, Tuple

import fitz  # PyMuPDF — ya disponible (lo usa srri_v4_geometric.py)

# ============================================================
# RUTAS DEL PROYECTO
# ============================================================
# Raíz operativa: C:\desarrollo\fondos\
# Estructura confirmada (tree 02-may-2026):
#   proyecto1/core/io.py    ← módulo de descarga PDF
#   proyecto1/core/...      ← demás módulos del pipeline
#   proyecto2/, proyecto3/, shared/ ← hermanos de proyecto1
#   db/fondos.sqlite        ← BD canónica (raíz, NO dentro de proyecto1)
# ============================================================
PROJECT_ROOT = Path(r"c:\desarrollo\fondos")
CORE_IO_PATH = PROJECT_ROOT / "proyecto1" / "core" / "io.py"
DB_PATH = PROJECT_ROOT / "db" / "fondos.sqlite"

# Output del inventario: en proyecto1/db por coherencia con el resto del pipeline
OUTPUT_CSV_DEFAULT = PROJECT_ROOT / "proyecto1" / "db" / "dla_layout_inventory.csv"

# Heurística calibrada en sesión empírica del 02-may-2026 sobre 5 PDFs:
#   - "narrow": bloque cuyo ancho es < 55% del ancho de página
#   - "full":   bloque cuyo ancho es > 70% del ancho de página
#   - 2-col:    n_narrow > n_full Y al menos 3 bloques en cada mitad horizontal
NARROW_THRESHOLD = 0.55
FULL_THRESHOLD = 0.70
MIN_BLOCKS_PER_COLUMN = 3
MAX_PDF_PAGES = 3  # idéntico a io.py:MAX_PDF_PAGES


# ============================================================
# DETECCIÓN DE LAYOUT (núcleo del diagnóstico)
# ============================================================

def classify_page_layout(page: fitz.Page) -> str:
    """
    Clasifica una página en SINGLE_COL, TWO_COL, MIXED, NO_TEXT.

    Reglas:
        1. Si page.get_text("blocks") devuelve 0 bloques de texto → NO_TEXT.
        2. Si n_narrow == 0 y n_full > 0 → SINGLE_COL puro.
        3. Si n_narrow > n_full y hay ≥3 bloques en cada mitad → TWO_COL.
        4. Si n_full >= n_narrow → SINGLE_COL (predominio de bloques anchos).
        5. Resto → MIXED (caso ambiguo, requiere fallback).
    """
    page_w = page.rect.width
    blocks = page.get_text("blocks")
    text_blocks = [b for b in blocks if b[6] == 0 and b[4].strip()]

    if not text_blocks:
        return "NO_TEXT"

    widths = [b[2] - b[0] for b in text_blocks]
    n_narrow = sum(1 for w in widths if w < page_w * NARROW_THRESHOLD)
    n_full = sum(1 for w in widths if w > page_w * FULL_THRESHOLD)

    n_blocks_left = sum(
        1 for b in text_blocks if (b[0] + b[2]) / 2 < page_w / 2
    )
    n_blocks_right = sum(
        1 for b in text_blocks if (b[0] + b[2]) / 2 >= page_w / 2
    )

    if n_full >= n_narrow:
        return "SINGLE_COL"

    if (
        n_narrow > n_full
        and n_blocks_left >= MIN_BLOCKS_PER_COLUMN
        and n_blocks_right >= MIN_BLOCKS_PER_COLUMN
    ):
        return "TWO_COL"

    return "MIXED"


def analyse_pdf_layout(pdf_bytes: bytes) -> dict:
    """
    Analiza hasta MAX_PDF_PAGES de un PDF y devuelve métricas.

    Returns:
        dict con n_pages_total, n_pages_single, n_pages_two_col,
        n_pages_mixed, n_pages_no_text, layout_signature.

        layout_signature es una cadena tipo "S2,T,T" que codifica el layout
        de cada página (S=Single, T=Two_col, M=Mixed, N=No_text).
    """
    counts = {
        "SINGLE_COL": 0,
        "TWO_COL": 0,
        "MIXED": 0,
        "NO_TEXT": 0,
    }
    sig_parts = []

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        n_pages_seen = 0
        for i, page in enumerate(doc):
            if i >= MAX_PDF_PAGES:
                break
            n_pages_seen += 1
            kind = classify_page_layout(page)
            counts[kind] += 1
            sig_parts.append(
                {"SINGLE_COL": "S", "TWO_COL": "T", "MIXED": "M", "NO_TEXT": "N"}[kind]
            )
    finally:
        doc.close()

    return {
        "n_pages_total": n_pages_seen,
        "n_pages_single": counts["SINGLE_COL"],
        "n_pages_two_col": counts["TWO_COL"],
        "n_pages_mixed": counts["MIXED"],
        "n_pages_no_text": counts["NO_TEXT"],
        "has_two_col": 1 if counts["TWO_COL"] > 0 else 0,
        "layout_signature": ",".join(sig_parts),
    }


# ============================================================
# FUENTES DE PDFs (3 modos)
# ============================================================

def _load_core_io():
    """
    Carga el módulo io.py del proyecto de forma segura, sin shadowing.

    Por qué no usamos `import io as core_io`:
        `io` es un módulo built-in de Python (BytesIO, StringIO, etc.). Si el
        nombre se solapa con sys.modules['io'] o con un import previo, Python
        resolverá al built-in y NO al módulo del proyecto. Eso produce
        AttributeError silencioso para funciones del proyecto.

    Estrategia:
        Carga directa por ruta absoluta usando importlib.util.spec_from_file_location.
        Registra el módulo bajo un nombre INEQUÍVOCO ('_fondos_core_io') para que
        no colisione con el built-in.

    Estructura confirmada en árbol del 02-may-2026:
        C:\\desarrollo\\fondos\\proyecto1\\core\\io.py  ← ruta canónica

    Se mantienen rutas alternativas como red de seguridad por si la estructura
    cambia o el script se ejecuta en otro entorno con la misma codebase.
    """
    import importlib.util

    candidates = [
        CORE_IO_PATH,                              # canónica: proyecto1/core/io.py
        PROJECT_ROOT / "core" / "io.py",           # legacy: estructura plana
        PROJECT_ROOT / "io.py",                    # legacy: io.py en raíz
    ]
    target = None
    for c in candidates:
        if c.is_file():
            target = c
            break

    if target is None:
        raise FileNotFoundError(
            f"No se encontró io.py del proyecto. Buscado en:\n  "
            + "\n  ".join(str(c) for c in candidates)
        )

    spec = importlib.util.spec_from_file_location("_fondos_core_io", target)
    if spec is None or spec.loader is None:
        raise ImportError(f"No se pudo crear spec para: {target}")

    module = importlib.util.module_from_spec(spec)
    sys.modules["_fondos_core_io"] = module
    spec.loader.exec_module(module)

    # Verificación explícita: las funciones requeridas DEBEN existir
    required = ("_requests_session", "find_kiid_links_from_excel", "download_pdf")
    missing = [name for name in required if not hasattr(module, name)]
    if missing:
        raise AttributeError(
            f"El módulo cargado desde {target} no tiene las funciones esperadas: "
            f"{missing}. ¿Es realmente el io.py del proyecto?"
        )

    print(f"[INFO] core/io cargado desde: {target}")
    return module


def iter_pdfs_local(pdf_dir: Path):
    """
    Modo 'local': itera PDFs desde un directorio local.
    Convención: nombre del fichero = ISIN + ".pdf" (ej. LU0006277684.pdf).
    """
    for pdf_path in sorted(pdf_dir.glob("*.pdf")):
        isin = pdf_path.stem
        try:
            with open(pdf_path, "rb") as f:
                yield isin, f.read()
        except Exception as e:
            print(f"[WARN] No se pudo leer {pdf_path.name}: {e}")
            continue


def iter_pdfs_redownload(
    excel_master_path: Optional[str],
    max_funds: Optional[int] = None,
    random_sample: Optional[int] = None,
    seed: int = 42,
):
    """
    Modo 'db_redownload': itera ISINs desde fund_kiid_metadata (KIID_Status='OK')
    y descarga el PDF vía URL del Excel maestro.

    Carga core/io.py del proyecto usando importlib.util (carga directa por path),
    evitando el shadowing del built-in `io` de Python al hacer `import io`.
    """
    core_io = _load_core_io()

    if not DB_PATH.exists():
        raise FileNotFoundError(f"BD no encontrada: {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(
            """SELECT ISIN
               FROM fund_kiid_metadata
               WHERE KIID_Status IN ('OK','CACHED')
                 AND Raw_KIID_Text IS NOT NULL
                 AND LENGTH(Raw_KIID_Text) > 100
                 AND KIID_Class = 1
               ORDER BY ISIN"""
        )
        all_isins = [r[0] for r in cur.fetchall()]
    finally:
        conn.close()

    if random_sample:
        random.seed(seed)
        all_isins = random.sample(all_isins, min(random_sample, len(all_isins)))
    if max_funds:
        all_isins = all_isins[:max_funds]

    print(f"[INFO] {len(all_isins)} ISINs a procesar")

    if not excel_master_path:
        raise ValueError("Modo db_redownload requiere --excel_master <ruta>")

    session = core_io._requests_session()  # type: ignore[attr-defined]

    for idx, isin in enumerate(all_isins, 1):
        try:
            links = core_io.find_kiid_links_from_excel(isin, excel_master_path)
        except Exception as e:
            print(f"[{idx}/{len(all_isins)}] {isin} ERROR_LINKS: {e}")
            continue

        if not links:
            print(f"[{idx}/{len(all_isins)}] {isin} SKIP no_links")
            continue

        downloaded = False
        for url in links:
            try:
                pdf_bytes = core_io.download_pdf(url, session)
                yield isin, pdf_bytes
                downloaded = True
                break
            except Exception as e:
                print(f"[{idx}/{len(all_isins)}] {isin} retry: {type(e).__name__}")
                continue

        if not downloaded:
            print(f"[{idx}/{len(all_isins)}] {isin} SKIP all_urls_failed")


# ============================================================
# MAIN
# ============================================================

def main():
    ap = argparse.ArgumentParser(
        description="Q-DLA-03: inventario físico de layout multi-columna en KIIDs"
    )
    ap.add_argument(
        "--mode",
        choices=["local", "db_redownload"],
        required=True,
        help="local: PDFs ya en disco | db_redownload: descarga desde URLs del Excel",
    )
    ap.add_argument(
        "--pdf_dir",
        type=str,
        help="(modo local) Directorio con PDFs nombrados como <ISIN>.pdf",
    )
    ap.add_argument(
        "--excel_master",
        type=str,
        default=None,
        help="(modo db_redownload) Ruta al Excel maestro con URLs",
    )
    ap.add_argument(
        "--max_funds",
        type=int,
        default=None,
        help="Limitar a primeros N ISINs (alfabéticamente)",
    )
    ap.add_argument(
        "--random_sample",
        type=int,
        default=None,
        help="Tomar muestra aleatoria de N ISINs (semilla fija para reproducibilidad)",
    )
    ap.add_argument(
        "--output",
        type=str,
        default=str(OUTPUT_CSV_DEFAULT),
        help=f"Ruta del CSV de salida (default: {OUTPUT_CSV_DEFAULT})",
    )
    args = ap.parse_args()

    if args.mode == "local":
        if not args.pdf_dir:
            ap.error("Modo local requiere --pdf_dir")
        pdf_dir = Path(args.pdf_dir)
        if not pdf_dir.is_dir():
            ap.error(f"--pdf_dir no es un directorio válido: {pdf_dir}")
        source_iter = iter_pdfs_local(pdf_dir)
        source_label = f"local:{pdf_dir.name}"
    else:  # db_redownload
        source_iter = iter_pdfs_redownload(
            excel_master_path=args.excel_master,
            max_funds=args.max_funds,
            random_sample=args.random_sample,
        )
        source_label = "db_redownload"

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "ISIN",
        "Source",
        "n_pages_total",
        "n_pages_single",
        "n_pages_two_col",
        "n_pages_mixed",
        "n_pages_no_text",
        "has_two_col",
        "layout_signature",
        "error",
    ]

    n_processed = 0
    n_two_col = 0
    n_errors = 0
    t_start = time.time()

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for isin, pdf_bytes in source_iter:
            n_processed += 1
            try:
                metrics = analyse_pdf_layout(pdf_bytes)
                row = {
                    "ISIN": isin,
                    "Source": source_label,
                    "error": "",
                    **metrics,
                }
                if metrics["has_two_col"]:
                    n_two_col += 1
            except Exception as e:
                n_errors += 1
                row = {
                    "ISIN": isin,
                    "Source": source_label,
                    "n_pages_total": 0,
                    "n_pages_single": 0,
                    "n_pages_two_col": 0,
                    "n_pages_mixed": 0,
                    "n_pages_no_text": 0,
                    "has_two_col": 0,
                    "layout_signature": "",
                    "error": f"{type(e).__name__}: {e}",
                }

            writer.writerow(row)

            if n_processed % 50 == 0:
                elapsed = time.time() - t_start
                rate = n_processed / elapsed if elapsed else 0
                print(
                    f"[PROGRESO] {n_processed} procesados "
                    f"({n_two_col} con 2-col, {n_errors} errores) "
                    f"— {rate:.1f} PDFs/s"
                )

    elapsed = time.time() - t_start

    print()
    print("=" * 70)
    print("RESUMEN Q-DLA-03")
    print("=" * 70)
    print(f"  Total procesados      : {n_processed}")
    print(f"  Con 2-col detectado   : {n_two_col} ({n_two_col / max(n_processed, 1) * 100:.1f}%)")
    print(f"  Errores               : {n_errors}")
    print(f"  Tiempo total          : {elapsed:.1f}s")
    print(f"  Rate                  : {n_processed / max(elapsed, 0.001):.1f} PDFs/s")
    print()
    print(f"  CSV salida            : {output_path}")
    print()
    print("UMBRALES BL-DLA (sección 4.2 del documento de decisión):")
    pct = n_two_col / max(n_processed, 1) * 100
    if pct >= 30:
        print(f"  ✓  {pct:.1f}% ≥ 30% → PROCEDER con Fase 1 (ROI alto)")
    elif pct >= 15:
        print(f"  ?  {pct:.1f}% ∈ [15%, 30%) → PROCEDER con piloto acotado")
    elif pct >= 5:
        print(f"  ⚠  {pct:.1f}% ∈ [5%, 15%) → DIFERIR; parchar regex específicos")
    else:
        print(f"  ✗  {pct:.1f}% < 5% → CERRAR BL-DLA sin implementación")

    print()
    print("CONSULTAS DE AGREGACIÓN POSTERIORES:")
    print()
    print("  -- Distribución por layout_signature:")
    print(f"  python -c \"import pandas as pd; df = pd.read_csv(r'{output_path}');"
          f" print(df['layout_signature'].value_counts().head(20))\"")
    print()
    print("  -- Cuántos fondos tienen >=2 páginas en 2-cols:")
    print(f"  python -c \"import pandas as pd; df = pd.read_csv(r'{output_path}');"
          f" print((df['n_pages_two_col'] >= 2).sum())\"")


if __name__ == "__main__":
    main()
