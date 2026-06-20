# -*- coding: utf-8 -*-
"""
scripts/diag/dla1_decision_diag.py  v1.3  -- BL-DLA-1-DIAG
=============================================================
Orquestador unico de toma de decision para BL-DLA-1 (Fase 1: parrafos 2-col).

ARQUITECTURA CLAVE -- Fase 4 (Q-DLA-03):
    La deteccion fisica de layout por pagina delega en dla_extractor.py
    (modulo core produccion), funcion classify_page_layout().
    Esta funcion implementa la heuristica de dos niveles:
      - Nivel 1: width-only (classify_page_layout_level1)
      - Nivel 2: gap-detection (classify_page_layout_level2, solo para MIXED)
    Calibrada en Q-DLA-03 y desplegada en BL-DLA-1 Sub-fase 1A.

    Referencia: dla_extractor.py linea 88:
    "Deben mantenerse sincronizadas con dla_layout_inventory.py".
    => dla_extractor es el modulo canonico; dla_layout_inventory es fallback.

    Fallback: si dla_extractor no esta accesible, se usa
    dla_layout_inventory.analyse_pdf_layout() (Nivel 1 solo).

CONSULTAS/SCRIPTS INTEGRADOS:
    Q-DLA-01.sql    -> Fase 1: lenguas y longitudes corpus
    Q-DLA-02.sql    -> Fase 2: patologia 2-col heuristica textual
    Q-B1-PreFase1   -> Fase 3: baseline NULL ratio atributos
    Q-B2-PreFase1   -> Fase 3: SRRI_Quality_Flag distribution
    dla_layout_inventory.py (Q-DLA-03) -> Fase 4: inventario fisico layout
    Q-DLA-06.bat    -> Fase 5a: distribucion layout_signature
    Q-DLA-07.bat    -> Fase 5b: fondos con >=2 paginas en 2-col
    [nuevo]         -> Fase 5c: muestra candidatos piloto Sub-fase 1C
    [nuevo]         -> Fase 6: decision Go/No-Go con umbrales backlog v3.7

SALIDAS:
    - Consola: secciones secuenciales con indicadores y decision final
    - CSV:  <project_root>/proyecto1/db/dla_layout_inventory.csv
    - Log:  <project_root>/proyecto1/db/dla1_decision_diag.log

USO:
  MODO BD (por defecto) -- lee CSV existente, corpus completo BD:
    python -m scripts.diag.dla1_decision_diag

  MODO RECARGA DESCARGA -- descarga PDFs, todas las fases sobre esa muestra:
    python -m scripts.diag.dla1_decision_diag --reload-pdf-sample 200
    python -m scripts.diag.dla1_decision_diag --reload-pdf-sample 3204

  MODO RECARGA LOCAL -- PDFs en disco, todas las fases sobre esos ISINs:
    python -m scripts.diag.dla1_decision_diag --reload-pdf-dir C:\\pdfs\\fondos

  OPCIONES ADICIONALES:
    python -m scripts.diag.dla1_decision_diag --project-root C:\\desarrollo\\fondos
    python -m scripts.diag.dla1_decision_diag --pilot-sample 30
    python -m scripts.diag.dla1_decision_diag --seed 123 --reload-pdf-sample 500
"""

from __future__ import annotations

import argparse
import csv
import random
import re
import sqlite3
import sys
import textwrap
import time
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Resolucion de raiz del proyecto
# ---------------------------------------------------------------------------

def _find_project_root(start):
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

def _setup_path(explicit_root=None):
    root = Path(explicit_root).resolve() if explicit_root else _PROJECT_ROOT_AUTO
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return root

# ---------------------------------------------------------------------------
# Logger dual: consola + buffer (patron identico a dla2_decision_diag.py)
# ---------------------------------------------------------------------------

class _DualLogger:
    def __init__(self):
        self._buf = StringIO()

    def _write(self, text):
        print(text)
        self._buf.write(text + "\n")

    def section(self, title):
        sep = "=" * 70
        self._write(f"\n{sep}\n  {title}\n{sep}")

    def subsection(self, title):
        self._write(f"\n  -- {title} --")

    def row(self, label, value, width=42):
        self._write(f"  {label:<{width}}: {value}")

    def table(self, headers, rows, indent=2):
        if not rows:
            self._write(" " * indent + "(sin resultados)")
            return
        widths = [len(h) for h in headers]
        for r in rows:
            for i, v in enumerate(r):
                widths[i] = max(widths[i], len(str(v) if v is not None else "NULL"))
        pad = " " * indent
        hl  = pad + "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
        sl  = pad + "-" * (len(hl) - indent)
        self._write(hl)
        self._write(sl)
        for r in rows:
            self._write(pad + "  ".join(
                (str(v) if v is not None else "NULL").ljust(widths[i])
                for i, v in enumerate(r)
            ))

    def blank(self):
        self._write("")

    def warning(self, msg):
        self._write(f"  WARNING  {msg}")

    def info(self, msg):
        self._write(f"  INFO  {msg}")

    def ok(self, msg):
        self._write(f"  OK  {msg}")

    def decision(self, label, detail, accion=""):
        box = "=" * 70
        self._write(f"\n{box}")
        self._write(f"  >>> DECISION: {label}")
        self._write(f"      {detail}")
        if accion:
            self._write(f"      Accion: {accion}")
        self._write(box)

    def flush_to_file(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self._buf.getvalue(), encoding="utf-8")
        print(f"\n[LOG] Salida guardada en: {path}")

log = _DualLogger()

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
MAX_PDF_PAGES = 3   # identico a io.py y dla_extractor.py

_PATRONES_PATOLOGIA_2COL = [
    (r'\bTipo\s+(?:tres|cinco|diez)\s+a[n~]os\b',
     "Tipo_Nanos -- heading concatenado con cuerpo columna contraria"),
    (r'\bsubfondos\s+mide\b',
     "subfondos_mide -- fragmento col izquierda + col derecha"),
    (r'\bdurante\s+un\s+per[i~]odo\s+(?:El|La|Los|Las|Es)\s+',
     "durante_periodo_El -- oracion cruzada entre columnas"),
    (r'\bproducto\s+(?:Este|Esta)\s+',
     "producto_Este -- encabezado + frase de otra columna"),
]
_COMPILED_PATOLOGIA = [
    (re.compile(pat, re.IGNORECASE), desc)
    for pat, desc in _PATRONES_PATOLOGIA_2COL
]

# ---------------------------------------------------------------------------
# Carga de modulos de clasificacion de layout
# ---------------------------------------------------------------------------

def _load_dla_extractor(root):
    """
    Carga dla_extractor.py (modulo core) via importlib.
    Preferencia sobre dla_layout_inventory porque implementa heuristica
    de dos niveles (L1 width + L2 gap-detection) desplegada en produccion.
    Retorna el modulo o None.
    """
    import importlib.util
    candidates = [
        root / "proyecto1" / "core" / "dla_extractor.py",
        _THIS_FILE.parent / "dla_extractor.py",
        root / "core" / "dla_extractor.py",
    ]
    target = next((p for p in candidates if p.is_file()), None)
    if target is None:
        return None
    spec = importlib.util.spec_from_file_location("_dla_ext_diag", target)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_dla_ext_diag"] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        log.warning(f"Error cargando dla_extractor desde {target}: {e}")
        return None
    if not hasattr(mod, "classify_page_layout"):
        log.warning(
            f"dla_extractor en {target} no tiene classify_page_layout() "
            f"(necesita v1.0+)."
        )
        return None
    log.info(f"dla_extractor cargado desde: {target}")
    return mod


def _load_dla_layout_inventory(root):
    """Fallback: carga dla_layout_inventory.py (L1 only). Retorna modulo o None."""
    import importlib.util
    candidates = [
        root / "scripts" / "diag" / "dla_layout_inventory.py",
        _THIS_FILE.parent / "dla_layout_inventory.py",
        root / "proyecto1" / "scripts" / "diag" / "dla_layout_inventory.py",
    ]
    target = next((p for p in candidates if p.is_file()), None)
    if target is None:
        return None
    spec = importlib.util.spec_from_file_location("_dla_inv_diag", target)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_dla_inv_diag"] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        log.warning(f"Error cargando dla_layout_inventory: {e}")
        return None
    log.info(f"dla_layout_inventory cargado desde: {target} (fallback L1 only)")
    return mod


def _analyse_pdf_via_extractor(pdf_bytes, dla_ext_mod):
    """
    Clasifica layout de un PDF usando classify_page_layout() de dla_extractor.
    Retorna dict con mismas columnas que dla_layout_inventory.csv.
    """
    import fitz
    counts = {"SINGLE_COL": 0, "TWO_COL": 0, "MIXED": 0, "NO_TEXT": 0}
    sig_parts = []
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        for i, page in enumerate(doc):
            if i >= MAX_PDF_PAGES:
                break
            kind = dla_ext_mod.classify_page_layout(page)
            counts[kind] = counts.get(kind, 0) + 1
            sig_parts.append(
                {"SINGLE_COL": "S", "TWO_COL": "T",
                 "MIXED": "M", "NO_TEXT": "N"}.get(kind, "?")
            )
    finally:
        doc.close()
    n = len(sig_parts)
    return {
        "n_pages_total":    n,
        "n_pages_single":   counts["SINGLE_COL"],
        "n_pages_two_col":  counts["TWO_COL"],
        "n_pages_mixed":    counts["MIXED"],
        "n_pages_no_text":  counts["NO_TEXT"],
        "has_two_col":      1 if counts["TWO_COL"] > 0 else 0,
        "layout_signature": ",".join(sig_parts),
    }

# ---------------------------------------------------------------------------
# Iteradores de PDFs
# ---------------------------------------------------------------------------

def _iter_pdfs_from_db(conn, root, sample_n, seed):
    import importlib.util as ilu
    io_candidates = [
        root / "proyecto1" / "core" / "io.py",
        root / "core" / "io.py",
        root / "io.py",
    ]
    io_path = next((p for p in io_candidates if p.is_file()), None)
    if io_path is None:
        log.warning("No se encontro core/io.py. Modo db_redownload no disponible.")
        return
    spec = ilu.spec_from_file_location("_fondos_core_io", io_path)
    core_io = ilu.module_from_spec(spec)
    sys.modules["_fondos_core_io"] = core_io
    spec.loader.exec_module(core_io)
    log.info(f"core/io cargado desde: {io_path}")

    excel_candidates = [
        root / "in" / "GestoresDeFondosv1.xlsx",
        root / "data" / "fondos" / "in" / "GestoresDeFondosv1.xlsx",
        Path(r"c:\data\fondos\in\GestoresDeFondosv1.xlsx"),
        Path(r"c:\desarrollo\fondos\in\GestoresDeFondosv1.xlsx"),
    ]
    excel_path = next((p for p in excel_candidates if p.is_file()), None)
    if excel_path is None:
        log.warning("No se encontro GestoresDeFondosv1.xlsx. Usar --inventory-pdf-dir.")
        return
    log.info(f"Excel maestro: {excel_path}")

    rows = conn.execute("""
        SELECT ISIN FROM fund_kiid_metadata
        WHERE KIID_Status IN ('OK','CACHED')
          AND Raw_KIID_Text IS NOT NULL
          AND LENGTH(Raw_KIID_Text) > 100
          AND KIID_Class = 1
        ORDER BY ISIN
    """).fetchall()
    all_isins = [r[0] for r in rows]
    if sample_n:
        random.seed(seed)
        all_isins = random.sample(all_isins, min(sample_n, len(all_isins)))
    log.info(f"ISINs a procesar: {len(all_isins)}")

    session = core_io._requests_session()
    for idx, isin in enumerate(all_isins, 1):
        try:
            links = core_io.find_kiid_links_from_excel(isin, str(excel_path))
        except Exception as e:
            print(f"[{idx}/{len(all_isins)}] {isin} ERROR_LINKS: {e}")
            continue
        if not links:
            print(f"[{idx}/{len(all_isins)}] {isin} SKIP no_links")
            continue
        for url in links:
            try:
                pdf_bytes = core_io.download_pdf(url, session)
                yield isin, pdf_bytes
                break
            except Exception:
                continue


def _iter_pdfs_local(pdf_dir):
    for pdf_file in sorted(pdf_dir.glob("*.pdf")):
        isin = pdf_file.stem
        try:
            yield isin, pdf_file.read_bytes()
        except Exception as e:
            print(f"[LOCAL] Error leyendo {pdf_file.name}: {e}")

# ---------------------------------------------------------------------------
# FASE 1: Distribucion de lenguas y longitudes  (Q-DLA-01)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Helper: cláusula WHERE/AND para filtro opcional de ISINs
# ---------------------------------------------------------------------------

def _isin_where(isin_filter, mode="AND"):
    """
    Devuelve (clausula, params) para filtrar por lista de ISINs.
    mode="AND"   -> "AND ISIN IN (...)"   para añadir tras un WHERE existente
    mode="WHERE" -> "WHERE ISIN IN (...)" para tablas sin WHERE previo
    Si isin_filter es None o vacio devuelve ('', []).
    """
    if not isin_filter:
        return "", []
    placeholders = ",".join("?" * len(isin_filter))
    return f"{mode} ISIN IN ({placeholders})", list(isin_filter)

def fase1_distribucion_corpus(conn, isin_filter=None):
    """
    Q-DLA-01. isin_filter: lista de ISINs de recarga; None = corpus completo.
    Cuando se usa modo recarga, todos los indicadores se calculan solo sobre
    los fondos cuyo PDF fue efectivamente analizado en esta sesion.
    """
    log.section("FASE 1 -- Distribucion de lenguas y longitudes del corpus  (Q-DLA-01)")
    where, params = _isin_where(isin_filter)
    scope = f"  (filtrado: {len(isin_filter)} ISINs de recarga)" if isin_filter else "  (corpus completo BD)"
    log.info(f"Ambito{scope}")

    rows = conn.execute(
        f"SELECT Language, COUNT(*) n_funds, "
        f"ROUND(AVG(LENGTH(Raw_KIID_Text))) avg_len, "
        f"MIN(LENGTH(Raw_KIID_Text)) min_len, "
        f"MAX(LENGTH(Raw_KIID_Text)) max_len "
        f"FROM fund_kiid_metadata "
        f"WHERE Raw_KIID_Text IS NOT NULL AND LENGTH(Raw_KIID_Text) > 100 {where} "
        f"GROUP BY Language ORDER BY n_funds DESC",
        params,
    ).fetchall()

    total_con_texto = conn.execute(
        f"SELECT COUNT(*) FROM fund_kiid_metadata "
        f"WHERE Raw_KIID_Text IS NOT NULL AND LENGTH(Raw_KIID_Text) > 100 {where}",
        params,
    ).fetchone()[0]
    total_sin_texto = conn.execute(
        f"SELECT COUNT(*) FROM fund_kiid_metadata "
        f"WHERE (Raw_KIID_Text IS NULL OR LENGTH(Raw_KIID_Text) <= 100) {where}",
        params,
    ).fetchone()[0]
    base_where = ("WHERE " + where[4:]) if where else ""
    total_tabla = conn.execute(
        f"SELECT COUNT(*) FROM fund_kiid_metadata {base_where}", params,
    ).fetchone()[0]

    log.subsection("Corpus total fund_kiid_metadata")
    log.row("Total registros",             total_tabla)
    log.row("Con Raw_KIID_Text valido",    f"{total_con_texto}  ({total_con_texto/max(total_tabla,1)*100:.1f}%)")
    log.row("Sin texto / muy corto",       f"{total_sin_texto}  ({total_sin_texto/max(total_tabla,1)*100:.1f}%)  <- candidatos BL-DLA-4 OCR")

    log.subsection("Distribucion por Language")
    log.table(
        ["Language", "n_funds", "avg_len", "min_len", "max_len"],
        [(r[0] if r[0] else "NULL", r[1], int(r[2]) if r[2] else 0, r[3], r[4])
         for r in rows],
    )
    null_lang = next((r for r in rows if r[0] is None), None)
    if null_lang and null_lang[1] / max(total_con_texto, 1) > 0.10:
        log.warning(
            f"Language NULL en {null_lang[1]} fondos "
            f"({null_lang[1]/total_con_texto*100:.1f}%). "
            f"Investigar KIIDs sin capa de texto."
        )
    return {"total_con_texto": total_con_texto, "total_sin_texto": total_sin_texto}

# ---------------------------------------------------------------------------
# FASE 2: Deteccion heuristica de patologia 2-col  (Q-DLA-02)
# ---------------------------------------------------------------------------

def fase2_deteccion_patologia_texto(conn, isin_filter=None):
    """
    Q-DLA-02. isin_filter: lista de ISINs de recarga; None = corpus completo.
    """
    log.section("FASE 2 -- Deteccion heuristica patologia 2-col en texto  (Q-DLA-02)")
    log.info("SQLite sin REGEXP: patrones evaluados en Python sobre Raw_KIID_Text.")
    where, params = _isin_where(isin_filter)

    total_evaluados   = 0
    total_sospechosos = 0
    hits     = {desc: 0 for _, desc in _PATRONES_PATOLOGIA_2COL}
    ejemplos = []

    cur = conn.execute(
        f"SELECT ISIN, Raw_KIID_Text FROM fund_kiid_metadata "
        f"WHERE Raw_KIID_Text IS NOT NULL AND LENGTH(Raw_KIID_Text) > 100 {where}",
        params,
    )
    while True:
        chunk = cur.fetchmany(500)
        if not chunk:
            break
        for isin, texto in chunk:
            total_evaluados += 1
            sospechoso = False
            for pat, desc in _COMPILED_PATOLOGIA:
                if pat.search(texto):
                    hits[desc] += 1
                    sospechoso = True
            if sospechoso:
                total_sospechosos += 1
                if len(ejemplos) < 5:
                    ejemplos.append(isin)

    pct = total_sospechosos / max(total_evaluados, 1) * 100

    log.subsection("Resultados por patron de patologia")
    log.table(
        ["Patron", "n_hits", "pct_corpus"],
        [(desc, n, f"{n/max(total_evaluados,1)*100:.2f}%") for desc, n in hits.items()],
    )
    log.blank()
    log.row("Total evaluados",   total_evaluados)
    log.row("Total sospechosos", f"{total_sospechosos}  ({pct:.2f}%)")
    if ejemplos:
        log.subsection("ISINs sospechosos (hasta 5 ejemplos)")
        for isin in ejemplos:
            log.row("  ISIN", isin, width=10)
    log.blank()
    if pct >= 5.0:
        log.warning(f"Patologia en {pct:.1f}% -- confirmar con inventario fisico (Fase 4).")
    else:
        log.info(f"Patologia solo en {pct:.2f}% -- senal debil; inventario fisico es determinante.")

    return {"total_evaluados": total_evaluados, "total_sospechosos": total_sospechosos,
            "pct_sospechosos": pct, "ejemplos_isin": ejemplos}

# ---------------------------------------------------------------------------
# FASE 3: Baseline de cobertura de atributos pre-DLA  (Q-B1 / Q-B2)
# ---------------------------------------------------------------------------

def fase3_baseline_cobertura(conn, isin_filter=None):
    """
    Q-B1 / Q-B2. isin_filter: lista de ISINs de recarga; None = corpus completo.
    """
    log.section("FASE 3 -- Baseline de cobertura de atributos pre-DLA  (Q-B1 / Q-B2)")
    log.info("Guardar estos valores: son referencia para C-1 (sin regresion) y C-2 (>=3 mejoras).")
    where, params = _isin_where(isin_filter)

    log.subsection("Q-B1: NULL ratio en atributos clave de fund_master")
    # fund_master no tiene WHERE base -> usar mode="WHERE"
    where_fm, params_fm = _isin_where(isin_filter, mode="WHERE")
    row = conn.execute(
        f"SELECT COUNT(*) total, "
        f"SUM(CASE WHEN Type IS NULL THEN 1 ELSE 0 END) null_type, "
        f"SUM(CASE WHEN Family IS NULL THEN 1 ELSE 0 END) null_family, "
        f"SUM(CASE WHEN Entry_Fee_Pct IS NULL THEN 1 ELSE 0 END) null_entry, "
        f"SUM(CASE WHEN Exit_Fee_Pct IS NULL THEN 1 ELSE 0 END) null_exit, "
        f"SUM(CASE WHEN Ongoing_Charge IS NULL THEN 1 ELSE 0 END) null_oc, "
        f"SUM(CASE WHEN Benchmark_Declared IS NULL THEN 1 ELSE 0 END) null_bm, "
        f"SUM(CASE WHEN SRRI IS NULL THEN 1 ELSE 0 END) null_srri "
        f"FROM fund_master {where_fm}",
        params_fm,
    ).fetchone()
    (total, nt, nf, ne, nx, no_, nb, ns) = row

    log.table(
        ["Atributo", "NULL_count", "NULL_pct", "Poblado_pct"],
        [
            ("Type",               nt, f"{nt/total*100:.1f}%", f"{(total-nt)/total*100:.1f}%"),
            ("Family",             nf, f"{nf/total*100:.1f}%", f"{(total-nf)/total*100:.1f}%"),
            ("Entry_Fee_Pct",      ne, f"{ne/total*100:.1f}%", f"{(total-ne)/total*100:.1f}%"),
            ("Exit_Fee_Pct",       nx, f"{nx/total*100:.1f}%", f"{(total-nx)/total*100:.1f}%"),
            ("Ongoing_Charge",     no_,f"{no_/total*100:.1f}%",f"{(total-no_)/total*100:.1f}%"),
            ("Benchmark_Declared", nb, f"{nb/total*100:.1f}%", f"{(total-nb)/total*100:.1f}%"),
            ("SRRI",               ns, f"{ns/total*100:.1f}%", f"{(total-ns)/total*100:.1f}%"),
        ],
    )
    log.row("Total fondos en fund_master", total)

    log.subsection("Q-B2: SRRI_Quality_Flag distribution (fund_master)")
    # SRRI_Quality_Flag vive en fund_master (escrita por pipeline.py via parsed).
    # La query original del backlog (Q-B1-PreFase1.sql) la buscaba en
    # fund_kiid_metadata con KIID_Class=1, pero en el schema v17 la columna
    # reside en fund_master. Se consulta ahi directamente.
    rows_b2 = conn.execute(
        f"SELECT SRRI_Quality_Flag, COUNT(*) n FROM fund_master {where_fm} "
        f"GROUP BY SRRI_Quality_Flag ORDER BY n DESC",
        params_fm,
    ).fetchall()
    tk1 = sum(r[1] for r in rows_b2)
    log.table(
        ["SRRI_Quality_Flag", "n", "pct"],
        [(r[0] if r[0] else "NULL", r[1], f"{r[1]/max(tk1,1)*100:.1f}%") for r in rows_b2],
    )
    log.blank()
    log.info(
        "Beneficio esperado BL-DLA-1: Type, Family, SRRI, Benchmark_Declared. "
        "Fees (Entry/Exit/OC) solo si aparecen en parrafos -- si estan en tablas, eso es BL-DLA-2."
    )
    return {"total_fondos": total, "null_type": nt, "null_family": nf,
            "null_entry": ne, "null_exit": nx, "null_oc": no_,
            "null_bm": nb, "null_srri": ns}

# ---------------------------------------------------------------------------
# FASE 4: Inventario fisico de layout  (Q-DLA-03)
# ---------------------------------------------------------------------------
#
# Logica de modos (por orden de prioridad):
#
#   1. MODO BD (por defecto, sin flags de recarga):
#      Lee el CSV dla_layout_inventory.csv existente.
#      Si no existe, avisa e informa como generarlo.
#      Todas las fases operan sobre el corpus completo de la BD: coherencia total.
#
#   2. MODO RECARGA PDF LOCAL (--reload-pdf-dir DIR):
#      Analiza PDFs en disco local. Genera/sobreescribe el CSV.
#      Todas las fases 1-3 se re-ejecutan filtrando solo los ISINs procesados.
#
#   3. MODO RECARGA PDF DESCARGA (--reload-pdf-sample N):
#      Descarga PDFs desde URLs del Excel maestro.  Genera/sobreescribe el CSV.
#      Todas las fases 1-3 se re-ejecutan filtrando solo los ISINs procesados.
#
# En modos 2 y 3, la funcion devuelve ademas isin_procesados para que main
# propague el filtro a las fases 1-3.

def fase4_inventario_layout(conn, csv_path, reload_pdf_sample,
                             reload_pdf_dir, seed, root):
    """
    Retorna dict con metricas del inventario MAS 'isin_procesados' (lista o None).
    isin_procesados != None solo en modos de recarga; se usa como filtro en F1-F3.
    """
    log.section("FASE 4 -- Inventario fisico de layout multi-columna  (Q-DLA-03)")

    # ── Modo BD (por defecto): leer CSV existente ─────────────────────────
    if reload_pdf_sample is None and reload_pdf_dir is None:
        if csv_path.exists():
            log.info(f"Modo BD: leyendo inventario desde: {csv_path}")
            result = _leer_inventario_csv(csv_path)
            result["isin_procesados"] = None  # sin filtro: corpus completo

            # Mostrar resumen del CSV leido
            n  = result["n_total"]
            n2 = result["n_two_col"]
            ne = result["n_errors"]
            log.subsection("Resumen del inventario leido")
            log.row("Fondos en el CSV",           n)
            log.row("Con >=1 pagina en 2-col",     f"{n2}  ({n2/max(n,1)*100:.1f}%)")
            log.row("Con >=2 paginas en 2-col",    f"{result['n_two_col_2p']}  ({result['n_two_col_2p']/max(n,1)*100:.1f}%)")
            log.row("Errores de procesamiento",    ne)
            log.row("Fuente CSV",                  str(csv_path))

            # Advertir si el CSV cubre una muestra del corpus
            if conn is not None:
                total_bd = conn.execute(
                    "SELECT COUNT(*) FROM fund_kiid_metadata "
                    "WHERE Raw_KIID_Text IS NOT NULL AND LENGTH(Raw_KIID_Text) > 100"
                ).fetchone()[0]
                if n < total_bd:
                    log.warning(
                        f"El inventario cubre {n} fondos de {total_bd} en BD "
                        f"({n/max(total_bd,1)*100:.1f}%). "
                        f"Los indicadores de Fases 1-3 se calcularan sobre el corpus completo BD, "
                        f"mientras que Fases 5-6 operan sobre la muestra del CSV. "
                        f"Para alinear todas las fases sobre la misma poblacion, "
                        f"ejecutar con --reload-pdf-sample {total_bd}."
                    )
            return result
        else:
            log.warning(
                f"CSV dla_layout_inventory.csv no encontrado en: {csv_path}. "
                f"Para generarlo ejecutar con --reload-pdf-sample N o --reload-pdf-dir DIR."
            )
            return {"csv_found": False, "isin_procesados": None}

    # ── Modos de recarga: generar inventario analizando PDFs ──────────────
    # Cargar clasificador: dla_extractor (L1+L2) preferido
    dla_ext = _load_dla_extractor(root)
    if dla_ext is not None:
        use_extractor    = True
        heuristica_label = "dla_extractor.classify_page_layout (L1+L2)"
        log.info("Heuristica: dla_extractor (Nivel1 width + Nivel2 gap-detection)")
    else:
        use_extractor = False
        inv_mod = _load_dla_layout_inventory(root)
        if inv_mod is None:
            log.warning(
                "Ningun modulo de clasificacion disponible. "
                "Instalar dla_extractor.py en proyecto1/core/ o scripts/diag/."
            )
            return {"csv_found": False, "isin_procesados": None}
        heuristica_label = "dla_layout_inventory (L1 only)"
        log.warning("dla_extractor no disponible -- fallback L1 only (sin gap-detection).")

    if reload_pdf_dir:
        pdf_dir = Path(reload_pdf_dir)
        if not pdf_dir.is_dir():
            log.warning(f"--reload-pdf-dir no es directorio valido: {pdf_dir}")
            return {"csv_found": False, "isin_procesados": None}
        pdf_iter     = _iter_pdfs_local(pdf_dir)
        source_label = f"local:{pdf_dir.name}"
        log.info(f"Modo recarga local: PDFs desde {pdf_dir}")
    else:
        if conn is None:
            log.warning("Sin conexion a BD no es posible modo descarga.")
            return {"csv_found": False, "isin_procesados": None}
        pdf_iter     = _iter_pdfs_from_db(conn, root, reload_pdf_sample, seed)
        source_label = "reload_download"
        log.info(f"Modo recarga descarga: muestra={reload_pdf_sample}, seed={seed}")
        log.warning(
            "NOTA: en modo recarga, las Fases 1-3 se recalculan sobre "
            "los ISINs procesados para garantizar coherencia de poblacion."
        )

    fieldnames = [
        "ISIN", "Source", "n_pages_total", "n_pages_single",
        "n_pages_two_col", "n_pages_mixed", "n_pages_no_text",
        "has_two_col", "layout_signature", "heuristica", "error",
    ]
    n_processed = n_two_col = n_errors = 0
    t_start = time.time()
    isins_procesados = []

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for isin, pdf_bytes in pdf_iter:
            n_processed += 1
            isins_procesados.append(isin)
            try:
                if use_extractor:
                    metrics = _analyse_pdf_via_extractor(pdf_bytes, dla_ext)
                else:
                    metrics = inv_mod.analyse_pdf_layout(pdf_bytes)
                row_w = {"ISIN": isin, "Source": source_label,
                         "heuristica": heuristica_label, "error": "", **metrics}
                if metrics["has_two_col"]:
                    n_two_col += 1
            except Exception as e:
                n_errors += 1
                row_w = {"ISIN": isin, "Source": source_label,
                         "n_pages_total": 0, "n_pages_single": 0,
                         "n_pages_two_col": 0, "n_pages_mixed": 0,
                         "n_pages_no_text": 0, "has_two_col": 0,
                         "layout_signature": "", "heuristica": "",
                         "error": f"{type(e).__name__}: {e}"}
            writer.writerow(row_w)
            if n_processed % 50 == 0:
                elapsed = time.time() - t_start
                print(f"[Q-DLA-03] {n_processed} procesados ({n_two_col} 2-col, "
                      f"{n_errors} err) -- {n_processed/max(elapsed,.001):.1f} PDFs/s")

    elapsed = time.time() - t_start
    log.row("PDFs procesados", n_processed)
    log.row("Con 2-col",       f"{n_two_col}  ({n_two_col/max(n_processed,1)*100:.1f}%)")
    log.row("Errores",         n_errors)
    log.row("Tiempo",          f"{elapsed:.1f}s")
    log.row("CSV generado",    str(csv_path))

    result = _leer_inventario_csv(csv_path)
    result["isin_procesados"] = isins_procesados  # propagar filtro a F1-F3
    return result


def _leer_inventario_csv(csv_path):
    registros = list(csv.DictReader(open(csv_path, newline="", encoding="utf-8")))
    n_total      = len(registros)
    n_two_col    = sum(1 for r in registros if r.get("has_two_col") == "1")
    n_errors     = sum(1 for r in registros if r.get("error", ""))
    n_no_text    = sum(1 for r in registros
                       if r.get("n_pages_no_text") and r["n_pages_no_text"] != "0")
    n_two_col_2p = sum(1 for r in registros
                       if r.get("n_pages_two_col") and int(r["n_pages_two_col"]) >= 2)
    sig_counts: dict = {}
    for r in registros:
        sig = r.get("layout_signature", "")
        if sig:
            sig_counts[sig] = sig_counts.get(sig, 0) + 1
    return {"csv_found": True, "n_total": n_total, "n_two_col": n_two_col,
            "n_errors": n_errors, "n_no_text": n_no_text,
            "n_two_col_2p": n_two_col_2p, "sig_counts": sig_counts,
            "registros": registros, "csv_path": csv_path}

# ---------------------------------------------------------------------------
# FASE 5: Analisis del inventario  (Q-DLA-06 / Q-DLA-07 / candidatos piloto)
# ---------------------------------------------------------------------------

def fase5_analisis_inventario(inv, pilot_n=25):
    log.section("FASE 5 -- Analisis del inventario de layout  (Q-DLA-06 / Q-DLA-07)")

    if not inv.get("csv_found"):
        log.warning("Inventario no disponible -- Fases 5 y 6 no ejecutables.")
        return {}

    n_total      = inv["n_total"]
    n_two_col    = inv["n_two_col"]
    n_two_col_2p = inv["n_two_col_2p"]
    sig_counts   = inv["sig_counts"]
    registros    = inv["registros"]

    log.subsection("Q-DLA-06: distribucion de layout_signature (top 20)")
    log.info("T=Two_col S=Single_col M=Mixed N=No_text. Ej: 'T,T,T' = 3 pags en 2-col.")
    top_sigs = sorted(sig_counts.items(), key=lambda x: -x[1])[:20]
    log.table(
        ["layout_signature", "n_fondos", "pct_inventario"],
        [(sig, n, f"{n/max(n_total,1)*100:.1f}%") for sig, n in top_sigs],
    )

    all_t   = sum(n for sig, n in sig_counts.items()
                  if sig and set(sig.replace(",", "")) <= {"T"})
    mixed_c = sum(n for sig, n in sig_counts.items()
                  if sig and "T" in sig and "S" in sig)
    many_m  = sum(n for sig, n in sig_counts.items()
                  if sig and sig.count("M") >= 2)

    log.blank()
    if n_two_col > 0 and all_t / max(n_two_col, 1) >= 0.60:
        log.ok(f"Patron homogeneo: {all_t} fondos con TODAS las paginas en 2-col.")
    if mixed_c > 0:
        log.info(f"{mixed_c} fondos mezclan S y T -- DLA clasifica pagina a pagina.")
    if many_m > 0:
        log.warning(f"{many_m} fondos con >=2 paginas MIXED -- gap-detection puede necesitar ajuste.")

    log.subsection("Q-DLA-07: fondos con >=2 paginas en 2-col (candidatos prioritarios)")
    log.row("Fondos con >=2 paginas en 2-col",
            f"{n_two_col_2p}  ({n_two_col_2p/max(n_total,1)*100:.1f}%)")

    log.subsection(f"Muestra de {pilot_n} ISINs candidatos para piloto Sub-fase 1C")
    cand_2p = [r["ISIN"] for r in registros
               if r.get("n_pages_two_col") and int(r["n_pages_two_col"]) >= 2
                  and not r.get("error", "")]
    cand_1p = [r["ISIN"] for r in registros
               if r.get("has_two_col") == "1"
                  and r.get("n_pages_two_col") and int(r["n_pages_two_col"]) < 2
                  and not r.get("error", "")]
    muestra = cand_2p[:pilot_n]
    if len(muestra) < pilot_n:
        muestra += cand_1p[: pilot_n - len(muestra)]
    if muestra:
        log.table(
            ["ISIN", "prioridad"],
            [(i, ">=2p 2-col" if i in cand_2p else "1p 2-col") for i in muestra],
        )
    else:
        log.warning("No se encontraron candidatos con has_two_col=1.")

    log.blank()
    log.subsection("Resumen del inventario")
    log.row("Total fondos en inventario",    n_total)
    log.row("Con >=1 pagina en 2-col",        f"{n_two_col}  ({n_two_col/max(n_total,1)*100:.1f}%)")
    log.row("Con >=2 paginas en 2-col",       f"{n_two_col_2p}  ({n_two_col_2p/max(n_total,1)*100:.1f}%)")
    log.row("Con paginas NO_TEXT (OCR)",       f"{inv['n_no_text']}  ({inv['n_no_text']/max(n_total,1)*100:.1f}%)  <- BL-DLA-4")
    log.row("Errores de procesamiento",       inv["n_errors"])

    return {"candidatos_piloto": muestra, "all_t": all_t,
            "mixed_col": mixed_c, "many_mixed": many_m}

# ---------------------------------------------------------------------------
# FASE 6: DECISION GO/NO-GO  (umbrales BL_DLA_DESIGN_DECISION 4.2)
# ---------------------------------------------------------------------------

def fase6_decision(f1, f2, f3, inv, f5):
    log.section("FASE 6 -- DECISION GO/NO-GO para BL-DLA-1")

    ok   = inv.get("csv_found", False) and inv.get("n_total", 0) > 0
    n_i  = inv.get("n_total", 0)
    n_2  = inv.get("n_two_col", 0)
    n_2p = inv.get("n_two_col_2p", 0)
    p2   = n_2 / max(n_i, 1) * 100
    p2p  = n_2p / max(n_i, 1) * 100
    p_nt = inv.get("n_no_text", 0) / max(n_i, 1) * 100
    ppr  = f2.get("pct_sospechosos", 0.0)
    tc   = f1.get("total_con_texto", 0)

    log.subsection("Indicadores clave")
    if ok:
        log.table(
            ["Indicador", "Valor", "Umbral BL_DLA_DESIGN_DECISION 4.2"],
            [
                ("Fondos >=1 pag 2-col (fisico)",
                 f"{n_2}/{n_i} ({p2:.1f}%)",
                 ">=30% GO | 15-30% PILOTO | 5-15% DIFERIR | <5% CERRAR"),
                ("Fondos >=2 pags 2-col (impacto alto)",
                 f"{n_2p} ({p2p:.1f}%)",
                 "Candidatos prioritarios Sub-fase 1C"),
                ("Patologia textual Q-DLA-02",
                 f"{f2.get('total_sospechosos','?')} ({ppr:.2f}%)",
                 "Indicador complementario"),
                ("Paginas NO_TEXT (OCR-needed)",
                 f"{inv.get('n_no_text',0)} ({p_nt:.1f}%)",
                 "Candidatos BL-DLA-4 (diferido)"),
                ("Corpus total con texto", tc, "--"),
            ],
        )
    else:
        log.warning("Inventario fisico no disponible. Solo patologia textual disponible.")

    log.blank()
    cands = f5.get("candidatos_piloto", [])

    if ok:
        if p2 >= 30:
            d  = "GO COMPLETO"
            dt = (f"Inventario fisico confirma 2-col en {p2:.1f}% del corpus muestral. "
                  f"ROI alto. Implementar BL-DLA-1 Sub-fases 1A->1B->1C->1D.")
            ac = (f"Sub-fases 1A/1B ya implementadas (dla_extractor v1.2, io.py v2 en produccion). "
                  f"Ejecutar piloto sobre {len(cands)} ISINs candidatos (Sub-fase 1C). "
                  f"Validar C-1/C-2/C-3/C-4 (BL_DLA_DESIGN_DECISION 7.1). "
                  f"Luego activar DLA_ENABLED=True progresivamente (Sub-fase 1D).")
            n4 = ""
        elif p2 >= 15:
            d  = "GO ACOTADO -- PILOTO PREVIO"
            dt = (f"2-col en {p2:.1f}% del corpus (15-30%). ROI suficiente para piloto.")
            ac = (f"Sub-fases 1A/1B ya implementadas. "
                  f"Ejecutar piloto sobre {len(cands)} ISINs candidatos (Sub-fase 1C). "
                  f"Re-evaluar tras piloto.")
            n4 = ""
        elif p2 >= 5:
            d  = "DIFERIR BL-DLA-1"
            dt = f"2-col en {p2:.1f}% del corpus (5-15%). ROI marginal."
            ac = ("Priorizar parches quirurgicos en kiid_parser.py para patrones de Q-DLA-02. "
                  "Ampliar corpus (>=500 fondos) antes de re-evaluar.")
            n4 = (f"Si pct_no_text={p_nt:.1f}% es elevado, "
                  f"considerar BL-DLA-4 (OCR) como alternativa mas urgente.")
        else:
            d  = "CERRAR BL-DLA-1 SIN IMPLEMENTACION"
            dt = f"2-col en {p2:.1f}% del corpus (<5%). Problema marginal."
            ac = "Cerrar BL-DLA. Documentar en backlog. Focalizar en BL-55, BL-51A."
            n4 = ""
    else:
        if ppr >= 5.0:
            d  = "INDETERMINADO -- EJECUTAR Q-DLA-03 OBLIGATORIO"
            dt = f"Patologia textual en {ppr:.2f}% sugiere 2-col pero necesita confirmacion fisica."
            ac = "Ejecutar: python dla1_decision_diag.py --inventory-sample 200"
        else:
            d  = "INDETERMINADO -- SENAL DEBIL -- EJECUTAR Q-DLA-03"
            dt = f"Patologia textual solo en {ppr:.2f}%. Senal insuficiente."
            ac = "Ejecutar inventario fisico Q-DLA-03 antes de decidir."
        n4 = ""

    log.decision(d, dt, ac)

    if n4:
        log.blank()
        log.subsection("Implicacion para BL-DLA-4 (OCR-aware)")
        log.row("Nota", textwrap.fill(n4, width=70))

    log.blank()
    if ok and n_i < 500:
        log.warning(f"Inventario sobre {n_i} fondos -- recomendados >=500 para mayor fiabilidad.")
    elif ok:
        log.ok(f"Inventario sobre {n_i} fondos -- muestra representativa.")

    log.blank()
    log.subsection("Baseline pre-DLA (Fase 3) -- referencia post-despliegue")
    log.info("Comparar con valores post-DLA_ENABLED=True para verificar C-1 y C-2.")
    for label, key in [
        ("Entry_Fee_Pct NULL",  "null_entry"),
        ("Exit_Fee_Pct NULL",   "null_exit"),
        ("Ongoing_Charge NULL", "null_oc"),
        ("SRRI NULL",           "null_srri"),
        ("Type NULL",           "null_type"),
        ("Family NULL",         "null_family"),
    ]:
        log.row(label, f"{f3.get(key, '?')}  <- baseline pre-DLA")

# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main(project_root=None, reload_pdf_sample=None,
         reload_pdf_dir=None, seed=42, pilot_n=25,
         csv_path_override=None, log_path_override=None):
    """
    Flujo de orquestacion:

    MODO BD (por defecto, sin --reload-pdf-*):
        F4 lee CSV existente -> F1, F2, F3 sobre corpus completo BD -> F5, F6.
        Todas las fases comparten la misma poblacion: coherencia garantizada.

    MODO RECARGA (--reload-pdf-sample N o --reload-pdf-dir DIR):
        F4 descarga/lee PDFs y genera CSV -> extrae isin_procesados ->
        F1, F2, F3 se recalculan SOLO sobre esos ISINs -> F5, F6.
        Coherencia garantizada: todos los indicadores refieren la misma submuestra.
    """
    root = _setup_path(project_root)

    csv_file = (Path(csv_path_override) if csv_path_override
                else root / "proyecto1" / "db" / "dla_layout_inventory.csv")
    log_file = (Path(log_path_override) if log_path_override
                else root / "proyecto1" / "db" / "dla1_decision_diag.log")

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log._write("=" * 70)
    log._write("  BL-DLA-1-DIAG -- Orquestador de toma de decision  v1.2")
    log._write(f"  Ejecutado:      {ts}")
    log._write(f"  project_root:   {root}")
    log._write(f"  CSV inventario: {csv_file}")
    log._write(f"  Log salida:     {log_file}")
    log._write("=" * 70)

    db_candidates = [root / "db" / "fondos.sqlite",
                     root / "proyecto1" / "db" / "fondos.sqlite"]
    db_path = next((p for p in db_candidates if p.exists()), None)
    if db_path:
        conn = sqlite3.connect(str(db_path))
        log.info(f"BD: {db_path}")
    else:
        log.warning("BD fondos.sqlite no encontrada. Fases 1-3 no ejecutables.")
        conn = None

    # ── Fase 4 primero en modo recarga; en modo BD puede ir en cualquier orden
    # pero ejecutarla antes permite propagar isin_procesados a F1-F3.
    inv = fase4_inventario_layout(
        conn, csv_file, reload_pdf_sample, reload_pdf_dir, seed, root,
    )
    isin_filter = inv.get("isin_procesados")  # None = corpus completo

    if conn:
        f1 = fase1_distribucion_corpus(conn, isin_filter)
        f2 = fase2_deteccion_patologia_texto(conn, isin_filter)
        f3 = fase3_baseline_cobertura(conn, isin_filter)
    else:
        f1 = {"total_con_texto": 0}
        f2 = {"total_sospechosos": 0, "pct_sospechosos": 0.0}
        f3 = {}

    f5 = fase5_analisis_inventario(inv, pilot_n=pilot_n)
    fase6_decision(f1=f1, f2=f2, f3=f3, inv=inv, f5=f5)

    if conn:
        conn.close()
    log.flush_to_file(log_file)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="BL-DLA-1-DIAG v1.2: orquestador unico de toma de decision.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Modos de ejecucion:

  MODO BD (por defecto) -- todas las fases sobre corpus completo de BD:
    python -m scripts.diag.dla1_decision_diag

  MODO RECARGA DESCARGA -- descarga PDFs, luego todas las fases sobre esa muestra:
    python -m scripts.diag.dla1_decision_diag --reload-pdf-sample 200
    python -m scripts.diag.dla1_decision_diag --reload-pdf-sample 200 --seed 42

  MODO RECARGA LOCAL -- PDFs en disco, luego todas las fases sobre esos ISINs:
    python -m scripts.diag.dla1_decision_diag --reload-pdf-dir C:\\pdfs\\fondos

  Opciones adicionales:
    python -m scripts.diag.dla1_decision_diag --project-root C:\\desarrollo\\fondos
    python -m scripts.diag.dla1_decision_diag --pilot-sample 30
        """,
    )
    parser.add_argument("--project-root",      default=None,
                        help="Raiz del proyecto (auto-detectada si no se indica)")
    parser.add_argument("--reload-pdf-sample", type=int, default=None, metavar="N",
                        help="Recarga: descargar PDFs de N fondos aleatorios y regenerar CSV")
    parser.add_argument("--reload-pdf-dir",    default=None, metavar="DIR",
                        help="Recarga: analizar PDFs locales en DIR y regenerar CSV")
    parser.add_argument("--seed",              type=int, default=42,
                        help="Semilla aleatoria para reproducibilidad (default: 42)")
    parser.add_argument("--pilot-sample",      type=int, default=25, metavar="N",
                        help="ISINs candidatos para piloto Sub-fase 1C (default: 25)")
    parser.add_argument("--csv-path",          default=None,
                        help="Ruta alternativa al CSV de inventario")
    parser.add_argument("--log-path",          default=None,
                        help="Ruta alternativa al log de salida")
    args = parser.parse_args()
    main(
        project_root=args.project_root,
        reload_pdf_sample=args.reload_pdf_sample,
        reload_pdf_dir=args.reload_pdf_dir,
        seed=args.seed, pilot_n=args.pilot_sample,
        csv_path_override=args.csv_path, log_path_override=args.log_path,
    )
