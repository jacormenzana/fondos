# proyecto1/core/dla_table_serializer.py  — v2 (BL-COST / DLA2 fix)
# -*- coding: utf-8 -*-
"""
dla_table_serializer v2 — serializa las tablas de coste del KID a formato DLA2
('|||'-delimitado) que el strong-path de cost_table_parser (_parse_*_dla2) sí
consume. Reemplaza la v1, cuya raíz de fallo (confirmada sobre PDFs reales) era:

  ROOT CAUSE 1 — formato: v1 emitía texto plano 'Etiqueta: valor' (_serialize_cat2a/2b/1)
      unido por '\\n'. El parser fuerte exige filas '|||'. → siempre caía al weak-path → LOW.
  ROOT CAUSE 2 — texto sin OCR / columnas entrelazadas: v1 leía su propio texto pdfplumber
      sin OCR (3 págs). En PDFs escaneados → vacío; en PDFs de 2 columnas, extract_text
      entrelaza las columnas y rompe las etiquetas (p.ej. DWS: "Incidencia anual de los
      4,3 % 2,3 % costes"). → la heurística no casa → tabla vacía/fragmentaria.

ESTRATEGIA v2 (validada sobre 9 KIID reales, 8/9 → HIGH-elegibles %+EUR):
  - GRID-FIRST: pdfplumber.extract_tables() preserva columnas (resuelve el entrelazado).
    Cada fila de una tabla con keywords de coste se emite como '|||celda|||celda|||'.
    Se prueban settings por defecto y vertical/horizontal='text' (tablas sin líneas).
  - TEXT-FALLBACK: para PDFs sin grid detectable, extracción por ventanas ancladas
    en etiquetas (R-6) sobre el texto YA OCR'd (Raw_KIID_Text) pasado por io.py.
  - MEJOR-FUENTE-POR-SECCIÓN: 'Costes a lo largo del tiempo' se toma íntegra de la fuente
    más completa (alinea columnas total↔ACI); 'Composición' fusiona la mejor fila por tipo.

CONTRATO (compatible con io.py):
    serialize_tables(pdf_bytes, text="", debug=False) -> (table_text: str, meta: dict)
    emit_table_log(isin, meta) -> None
  text="" → el serializador extrae su propio texto pdfplumber (modo v1). Para máxima
  cobertura, io.py debe pasar text=Raw_KIID_Text (OCR'd). table_text usa '|||' y empieza
  con cabeceras de sección '|||Composición de los costes|||' / '|||Costes a lo largo del tiempo|||'
  para que el locator de sección del parser dispare.
"""

from __future__ import annotations
import re
from io import BytesIO
from typing import Optional, List, Tuple

try:
    import pdfplumber as _pp
    _HAS_PDFPLUMBER = True
except Exception:          # pragma: no cover
    _pp = None
    _HAS_PDFPLUMBER = False

# BL-COST-OPER-FIX: extractor anclado del % de operación (Transaction_Cost_Pct).
# El % de operación vive en la celda-descripción libre; el grid la fragmenta/descarta
# -> la fila sale '|||Costes de operacion|||0 EUR|||' (sin %) -> Transaction_Cost_Pct NULL.
# Validado 10/10 emisores de texto. Import defensivo: si falta el módulo, el serializador
# degrada con elegancia (comportamiento previo, sin %).
try:
    from cost_pct_anchored import extract_transaction_cost_pct as _oper_pct_anchored
except Exception:          # pragma: no cover
    try:
        from core.cost_pct_anchored import extract_transaction_cost_pct as _oper_pct_anchored
    except Exception:
        _oper_pct_anchored = None

# ── patrones (datos; DRY-SYNC con cost_table_parser._COMPOSITION_PATTERNS) ────
_AMT = re.compile(r'(?:(\d[\d.,]*)\s*(?:EUR|USD|GBP|CHF)\b|(\d[\d.,]*)\s*€|€\s*(\d[\d.,]*))', re.I)
_PCT = re.compile(r'(\d{1,3}[.,]\d{1,3}|\d{1,3})\s*%')

_COMP: List[Tuple[str, str]] = [
    ("Costes de entrada",         r'costes?\s+de\s+entrada'),
    ("Costes de salida",          r'costes?\s+de\s+salida'),
    ("Comisiones de gestion",     r'comisiones?\s+de\s+gesti[oó]n'),
    ("Costes de operacion",       r'costes?\s+de\s+operaci[oó]n'),
    ("Comisiones de rendimiento", r'comisiones?\s+(?:de\s+(?:[eé]xito|rendimiento)|en\s+funci[oó]n\s+de\s+la\s+rentabilidad)'),
]
_ALL_LABELS = [p for _, p in _COMP]
_OT_KW   = re.compile(r'costes?\s+totales|incidencia\s+anual', re.I)
_CMP_KW  = re.compile(r'comisiones?\s+de\s+gesti|costes?\s+de\s+(?:entrada|salida|operaci)|comisiones?\s+de\s+rendimiento', re.I)
_COST_KW = re.compile(r'costes?\s+totales|incidencia\s+anual|comisiones?\s+de\s+gesti|'
                      r'costes?\s+de\s+(?:entrada|salida|operaci)|composici[oó]n\s+de\s+los\s+costes|'
                      r'comisiones?\s+de\s+rendimiento', re.I)
# FIX-P3: range pattern signals a bled performance-fee range in the oper grid cell
# (e.g. "0,10% - 20%" assigned to Costes de operación by pdfplumber column confusion).
# A legitimate oper % is a single value; a range always indicates cross-row bleed.
_RANGE_IN_CELL = re.compile(r'\d[.,]\d*\s*%\s*[-\u2013]\s*\d', re.I)

DLA2_SEPARATOR = '|||'   # DRY-SYNC: cost_table_parser.DLA2_SEPARATOR


def _amounts(s: str) -> List[str]:
    return [m.group(1) or m.group(2) or m.group(3) for m in _AMT.finditer(s or "")]

def _pcts(s: str) -> List[str]:
    return [m.group(1) for m in _PCT.finditer(s or "")]

def _clean(c: Optional[str]) -> str:
    return re.sub(r'\s+', ' ', (c or '')).strip()


# ── extracción por GRID (preserva columnas) ──────────────────────────────────
def _grid_sections(pdf) -> Tuple[List[str], List[str]]:
    cmp_rows: List[str] = []
    ot_rows:  List[str] = []
    for pg in pdf.pages[:3]:
        seen = set()
        for st in (None, {"vertical_strategy": "text", "horizontal_strategy": "text"}):
            try:
                tables = pg.extract_tables(st) if st else pg.extract_tables()
            except Exception:
                tables = []
            for tb in tables:
                rows = [[_clean(c) for c in r] for r in tb if any((c or '').strip() for c in r)]
                flat = " ".join(c for r in rows for c in r)
                if not _COST_KW.search(flat):
                    continue
                k = flat[:80]
                if k in seen:
                    continue
                seen.add(k)
                is_ot = bool(_OT_KW.search(flat)) and not _CMP_KW.search(flat)
                for r in rows:
                    line = DLA2_SEPARATOR + DLA2_SEPARATOR.join(r) + DLA2_SEPARATOR
                    lbl = r[0] if r else ""
                    if _OT_KW.search(lbl):
                        ot_rows.append(line)
                    elif _CMP_KW.search(lbl):
                        cmp_rows.append(line)
                    elif is_ot:
                        ot_rows.append(line)
    return cmp_rows, ot_rows


# ── extracción por TEXTO (ventanas ancladas, R-6) — fallback ─────────────────
def _text_sections(text: str) -> Tuple[List[str], List[str]]:
    cmp_rows: List[str] = []
    ot_rows:  List[str] = []
    if not text or not text.strip():
        return cmp_rows, ot_rows

    csec = re.search(r'composici[oó]n\s+de\s+los\s+costes', text, re.I)
    body = text[csec.start():] if csec else text
    for canon, pat in _COMP:
        m = re.search(pat, body, re.I)
        if not m:
            continue
        win = body[m.end(): m.end() + 260]
        cut = len(win)
        for p2 in _ALL_LABELS:
            mm = re.search(p2, win, re.I)
            if mm:
                cut = min(cut, mm.start())
        win = win[:cut]
        pcs = _pcts(win)
        ams = _amounts(win)
        cells = [canon]
        if pcs:
            cells.append(pcs[0] + "%")
        if ams:
            cells.append(ams[0] + " EUR")
        if len(cells) > 1:
            cmp_rows.append(DLA2_SEPARATOR + DLA2_SEPARATOR.join(cells) + DLA2_SEPARATOR)

    horizons = sorted({int(y) for y in re.findall(r'despu[eé]s\s+de\s+(\d+)\s+a[ñn]os?', text, re.I)})[:2] or [1]

    def _multi(pat: str, span: int = 160) -> Optional[str]:
        for m in re.finditer(pat, text, re.I):
            w = text[m.end(): m.end() + span]
            if _amounts(w) or _pcts(w):
                return w
        return None

    tw = _multi(r'costes?\s+totales?')
    aw = _multi(r'incidencia\s+anual\s+de\s+los\s+costes?')
    if tw or aw:
        ncol = min(max(len(_amounts(tw or '')), len(_pcts(aw or '')), 1), len(horizons)) or 1
        if len(horizons) < ncol:
            horizons = (horizons + [horizons[-1]])[:ncol]
        ot_rows.append(DLA2_SEPARATOR + "horizonte" + DLA2_SEPARATOR +
                       DLA2_SEPARATOR.join(f"despues de {y} anos" for y in horizons[:ncol]) + DLA2_SEPARATOR)
        if tw:
            a = _amounts(tw)[:ncol]
            if a:
                ot_rows.append(DLA2_SEPARATOR + "Costes totales" + DLA2_SEPARATOR +
                               DLA2_SEPARATOR.join(x + " EUR" for x in a) + DLA2_SEPARATOR)
        if aw:
            pc = _pcts(aw)[:ncol]
            if pc:
                ot_rows.append(DLA2_SEPARATOR + "Incidencia anual de los costes" + DLA2_SEPARATOR +
                               DLA2_SEPARATOR.join(x + "%" for x in pc) + DLA2_SEPARATOR)
    return cmp_rows, ot_rows


def _ot_completeness(rows: List[str]) -> int:
    blob = "\n".join(rows)
    return ((1 if re.search(r'costes?\s+totales', blob, re.I) and _amounts(blob) else 0) +
            (1 if re.search(r'incidencia\s+anual', blob, re.I) and _pcts(blob) else 0))


# ── API pública ──────────────────────────────────────────────────────────────
def serialize_tables(pdf_bytes: bytes, text: str = "", debug: bool = False) -> Tuple[str, dict]:
    """Serializa las tablas de coste a formato DLA2 '|||'.

    Args:
        pdf_bytes: binario del PDF (para extracción GRID que preserva columnas).
        text:      texto KIID ya extraído/OCR'd (Raw_KIID_Text). Recomendado: io.py
                   debe pasarlo para cubrir PDFs escaneados o de columnas entrelazadas.
        debug:     telemetría a stdout.

    Returns:
        (table_text, meta). table_text='' si no se extrajo nada.
    """
    meta = {
        "n_pages_scanned": 0,
        "tables_detected": {"composition": 0, "over_time": 0},
        "strategy": "none",
        "rows_extracted": 0,
        "fallback": False,
        "errors": [],
    }
    if not pdf_bytes and not text:
        meta["errors"].append("sin_pdf_ni_texto")
        return "", meta

    g_cmp: List[str] = []
    g_ot:  List[str] = []
    if pdf_bytes and _HAS_PDFPLUMBER:
        try:
            with _pp.open(BytesIO(pdf_bytes)) as pdf:
                meta["n_pages_scanned"] = min(len(pdf.pages), 3)
                g_cmp, g_ot = _grid_sections(pdf)
                # BL-COST-SER-FIX: preferir SIEMPRE el texto auto-extraido por
                # pdfplumber (espaciado, columnas separables). El `text` pasado por
                # io.py proviene de dla_extractor, que en PDFs de texto entrelaza las
                # columnas SIN espacios ("...costesCostes unicos...") y rompe el
                # text-path -> filas mal alineadas. Solo se usa el `text` pasado (OCR)
                # como fallback si la auto-extraccion viene vacia (PDF escaneado).
                try:
                    _self = "\n".join(p.extract_text() or "" for p in pdf.pages[:3])
                except Exception as e:
                    _self = ""
                    meta["errors"].append(f"extract_text:{e}")
                if _self and _self.strip():
                    text = _self
        except Exception as e:
            meta["errors"].append(f"pdfplumber_open:{e}")
    elif pdf_bytes and not _HAS_PDFPLUMBER:
        meta["errors"].append("pdfplumber_no_disponible")

    t_cmp, t_ot = _text_sections(text)

    # composición: mejor fila independiente por tipo de coste (pct+eur > pct > eur)
    cmp_out: List[str] = []
    for _canon, pat in _COMP:
        best = None
        best_sc = -1
        for r in g_cmp + t_cmp:
            if re.search(pat, r, re.I):
                sc = (1 if _pcts(r) else 0) + (1 if _amounts(r) else 0)
                if sc > best_sc:
                    best, best_sc = r, sc
        if best:
            cmp_out.append(best)

    # BL-COST-OPER-FIX: el grid suele descartar/fragmentar la celda-descripción de
    # operación, dejando la fila sin %. Si la fila de operación existe pero no trae %,
    # inyectamos el % extraído por anclaje semántico (incurr* + comprar/vender) sobre
    # el MISMO `text` espaciado que ya prefiere el serializador (auto-extract o, en
    # escaneados, el OCR pasado). Se emite en formato '%' idéntico al del resto de
    # tipos, por lo que recorre la misma conversión de escala del parser (sin supuestos).
    if _oper_pct_anchored is not None:
        _OPER_PAT = r'costes?\s+de\s+operaci[oó]n'
        _oper_idx = next((i for i, r in enumerate(cmp_out) if re.search(_OPER_PAT, r, re.I)), None)
        _row = cmp_out[_oper_idx] if _oper_idx is not None else None
        # FIX-P3: also override when the oper cell contains a range pattern
        # (e.g. "0,10% - 20%") — signals perf-fee bleed into the oper column.
        # A real oper % is always a single value; a "X% - Y%" range is not.
        _bled = _row is not None and bool(_RANGE_IN_CELL.search(_row))
        if _row is None or not _pcts(_row) or _bled:
            _opct = _oper_pct_anchored(text)
            if _opct is not None:
                _pct_cell = f"{_opct:g}%"
                if _row is None:
                    # operación ausente del grid: añadir fila mínima con el %
                    cmp_out.append(DLA2_SEPARATOR + "Costes de operacion" +
                                   DLA2_SEPARATOR + _pct_cell + DLA2_SEPARATOR)
                    meta.setdefault("oper_pct_injected", _pct_cell)
                else:
                    # insertar el % como celda tras la etiqueta, preservando el EUR
                    _cells = [c for c in _row.split(DLA2_SEPARATOR) if c.strip()]
                    _cells = [_cells[0], _pct_cell] + _cells[1:]
                    cmp_out[_oper_idx] = (DLA2_SEPARATOR +
                                          DLA2_SEPARATOR.join(_cells) + DLA2_SEPARATOR)
                    meta.setdefault("oper_pct_injected", _pct_cell)

    # over_time: sección íntegra de la fuente más completa (alinea columnas total↔ACI)
    # FIX-P1-A: prefer grid when equal completeness — grid has multi-column RHP data;
    # text only emits the first "después de N años" horizon (1Y). Strict '>' was
    # silently discarding the RHP column whenever both sources scored 2.
    if g_ot and _ot_completeness(g_ot) >= _ot_completeness(t_ot):
        ot_out = g_ot
        ot_src = "grid"
    else:
        ot_out = t_ot
        ot_src = "text"

    parts: List[str] = []
    if cmp_out:
        parts.append(DLA2_SEPARATOR + "Composición de los costes" + DLA2_SEPARATOR)
        parts.extend(cmp_out)
        meta["tables_detected"]["composition"] = 1
    if ot_out:
        parts.append(DLA2_SEPARATOR + "Costes a lo largo del tiempo" + DLA2_SEPARATOR)
        parts.extend(ot_out)
        meta["tables_detected"]["over_time"] = 1

    table_text = "\n".join(p for p in parts if p)
    meta["rows_extracted"] = len(cmp_out) + len(ot_out)
    if table_text:
        grid_used = bool(g_cmp or g_ot)
        meta["strategy"] = "grid" if (grid_used and ot_src == "grid") else ("grid+text" if grid_used else "text")
        meta["fallback"] = (ot_src == "text") or bool(t_cmp and not g_cmp)

    if debug:
        print(f"[DLA2v2] META: {meta}")
        print(f"[DLA2v2] OUTPUT ({len(table_text)} chars):\n{table_text}" if table_text else "[DLA2v2] (sin contenido)")

    return table_text, meta


# ── telemetría de ciclo (contrato v1 preservado) ─────────────────────────────
_cycle_stats = {"total": 0, "composition": 0, "over_time": 0, "fallback": 0, "errors": 0}

def emit_table_log(isin: str, meta: dict) -> None:
    """Acumula y registra telemetría por fondo (igual rol que v1)."""
    import logging
    _log = logging.getLogger(__name__)
    _cycle_stats["total"] += 1
    td = meta.get("tables_detected", {})
    if td.get("composition"):
        _cycle_stats["composition"] += 1
    if td.get("over_time"):
        _cycle_stats["over_time"] += 1
    if meta.get("fallback"):
        _cycle_stats["fallback"] += 1
    if meta.get("errors"):
        _cycle_stats["errors"] += 1
    _log.debug("[DLA2v2] %s: strategy=%s rows=%s tables=%s errors=%s",
               isin, meta.get("strategy"), meta.get("rows_extracted"),
               meta.get("tables_detected"), meta.get("errors"))
