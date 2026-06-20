# -*- coding: utf-8 -*-
"""
scripts/diag/dla2_dual_strategy_compare.py  v2.8  -- BL-DLA-2-DUALCMP
=============================================================
Compara DOS estrategias de extracción de OC sobre cada KIID, para decidir con
datos la lógica de arbitraje (NO asume prioridad fija — se demostró que tanto
"ruled-first" como "bands-X-first" regresionan casos).

ESTRATEGIAS:
  A) RULED   — pdfplumber find_tables con vertical/horizontal "lines". Extrae
               celdas de tablas con bordes (familia DWS y similares). Cada fila
               de tabla = un componente; consolida OC = gestión + operación.
  B) BANDS-X — reconstrucción por bandas X / bloques (reutiliza el prototipo
               dla2_xband_prototype.extract_from_pdf). Para tablas SIN bordes
               (narrativas: HSBC, Morgan Stanley, etc.).

SALIDA (por fondo): bands_x_oc, ruled_oc, ambos completos?, acuerdo/conflicto,
y la clasificación de arbitraje. Permite construir la regla de arbitraje desde
datos y medir regresión antes de integrar nada en producción.

NO integra, NO modifica el serializador. Solo mide.

v1.5 (2026-05-30, BL-DLA-2): el extractor RULED recupera el OC por BLOQUE
    cuando find_tables colapsa las etiquetas multi-línea de componente (layout
    IE/PIMCO: col0 solo conserva cabeceras de grupo, valores en col1 con col0
    vacío). Antes caía a "direct:X" capturando solo el primer valor (gestión) y
    perdiendo operación. Verificado IE0005300136: direct:0.85 -> 0.85+0.10=0.95
    (AGREE con bands-X). Sin regresión en layouts con etiquetas en col0
    (LU0329630130 sigue 1.6+0.01 por el camino normal). Objetivo: convertir los
    105 conflictos IE "direct:" a AGREE, dejando CONFLICT como señal limpia de
    layouts no soportados. Afecta a los 113 casos "direct:" del corpus; los no
    validados (2 Patrón B, 8 no-IE) deben revisarse tras el re-run.

v1.6 (2026-05-30, BL-DLA-2): fallback TEXTO-based cuando find_tables degrada la
    tabla. Dos layouts: (a) tabla TRUNCADA que descarta la fila de operación
    -> ruled daba 1 solo componente (Nordea LU0173776047: 1.61, real
    1.61+0.25=1.86); (b) SIN tabla por cabecera de bloque partida en columnas +
    valores en brackets [0,84%] (Findlay Park IE0002458671: BOTH_FAIL, real
    0.84+0.11=0.95). El fallback se ancla en las ETIQUETAS de componente (no en
    la cabecera de bloque, que es lo que falla) y solo PREVALECE si recupera 2
    componentes; nunca degrada un resultado de tabla ni inventa "operación"
    desde prosa (requiere etiqueta "costes de operación" + % adyacente).
    Validado 4 PDFs (Nordea/Findlay/PIMCO/Vontobel) sin regresión. Objetivo:
    reducir el cluster ~47 "ruled pierde operación" del residual de 56.

USO:
    python dla2_dual_strategy_compare.py --pdf-dir <carpeta> --db <sqlite>
    python dla2_dual_strategy_compare.py --pdf <uno.pdf> --debug

SALIDAS: dla2_dual_strategy_compare.csv + .log
v2.1 (2026-06-03, BL-DLA-2 OC dual-extraction batch): (A) fallback texto-
    based tolera texto narrativo concatenado (HSBC: extract_text() sin espacios)
    cambiando los separadores flexibles en _R_TXT_GEST/_R_TXT_OPER/_BOUND -> recupera el bucket
    MULTI HSBC (LU0164852419 y ~29 hermanos LU01648..) a AGREE. (C) etiqueta de
    operacion ampliada a "transaccion" + variantes FR. (B) is_ocdir excluye filas
    que casen entrada/salida, evitando direct:5.0 cuando find_tables degrada la
    tabla y cuela la cabecera de grupo (GAMCO). Validado 11 anchors + 4 targets.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import re
import sys
import os
import time
import unicodedata
from pathlib import Path
from collections import Counter

try:
    import pdfplumber
except ImportError:
    print("[FATAL] pdfplumber no disponible.")
    sys.exit(2)


# ──────────────────────────────────────────────────────────────────────────────
# Reutilizar el prototipo bands-X como módulo (DRY: no se duplica su lógica)
# ──────────────────────────────────────────────────────────────────────────────

def _load_xband(project_root: Path):
    """Importa dla2_xband_prototype desde el mismo directorio o scripts/diag."""
    here = Path(__file__).resolve().parent
    for cand in (here / "dla2_xband_prototype.py",
                 project_root / "scripts" / "diag" / "dla2_xband_prototype.py"):
        if cand.exists():
            spec = importlib.util.spec_from_file_location("dla2_xband_prototype", cand)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    print("[FATAL] No se encuentra dla2_xband_prototype.py (necesario, DRY).")
    sys.exit(2)


# ──────────────────────────────────────────────────────────────────────────────
# Estrategia A: ruled-table (find_tables con bordes)
# ──────────────────────────────────────────────────────────────────────────────

_PCT = re.compile(r'(\d{1,3}[,\.]\d{1,4})\s*%|\b(0)\s*[%％]')
_RULED_SETTINGS = {"vertical_strategy": "lines", "horizontal_strategy": "lines"}

_R_GESTION = re.compile(r'gesti[oó]n|management', re.I)
_R_OPER    = re.compile(r'operaci[oó]n|transacci[oó]n|transaction', re.I)
_R_OCDIR   = re.compile(r'corrientes?|ongoing', re.I)
_R_ENTRY   = re.compile(r'de\s*entrada|entry', re.I)
_R_EXIT    = re.compile(r'de\s*salida|exit', re.I)
_R_PERF    = re.compile(r'rendimiento|[eé]xito|performance', re.I)
_R_HEADER  = re.compile(r'composici[oó]n|gesti[oó]n|corrientes|composition|ongoing\s*costs?', re.I)


def _pct_from(text: str):
    m = _PCT.search(text or "")
    if not m:
        return None
    if m.group(1):
        return float(m.group(1).replace(",", "."))
    return 0.0


# v1.5 (BL-DLA-2 layout label-collapse, IE/PIMCO): find_tables a veces colapsa
# las etiquetas multi-línea de componente; col0 solo conserva las CABECERAS de
# grupo ("Costes corrientes detraídos cada año", "...únicos...", "...accesorios
# ...") y los valores caen en col1 como filas con col0 vacío. La clasificación
# por etiqueta (is_gest/is_oper) no encuentra nada y el código caía a
# got_oc_direct, que solo capturaba el PRIMER valor del bloque ("direct:0.85")
# perdiendo el componente de operación. Recuperación BASADA EN BLOQUE.
_R_OC_BLOCK_START = re.compile(r'corrientes?.*detra|ongoing\s+costs?\s+taken', re.I)
_R_GROUP_HDR = re.compile(
    r'corrientes?.*detra|[uú]nicos|accesorios'
    r'|ongoing\s+costs?\s+taken|one[- ]?off\s+costs?|incidental\s+costs?', re.I)


def _recover_oc_block(table) -> list:
    """Suma los % de las filas del bloque 'Costes corrientes ... detraídos'
    cuando las etiquetas de componente se han colapsado (col0 vacío en las
    filas de valor). Devuelve [floats] o [].

    Solo se invoca cuando la clasificación por etiqueta NO halló componentes,
    de modo que NO afecta a los layouts con etiquetas en col0 (DWS, Janus...).
    Aborta (devuelve []) si encuentra una etiqueta de componente real en col0,
    señal de que ese layout SÍ tiene etiquetas y debe seguir el camino normal.
    """
    start = None
    for i, r in enumerate(table):
        lab = (r[0] or "").replace("\n", " ") if r else ""
        if _R_OC_BLOCK_START.search(lab):
            start = i
            break
    if start is None:
        return []
    vals = []
    for r in table[start + 1:]:
        if not r:
            continue
        lab = (r[0] or "").replace("\n", " ")
        if lab.strip():
            # etiqueta de componente real -> este layout no es de col0-colapsado
            if _R_GESTION.search(lab) or _R_OPER.search(lab):
                return []
            # siguiente cabecera de grupo -> cierra el bloque OC
            if _R_GROUP_HDR.search(lab) and not _R_OC_BLOCK_START.search(lab):
                break
        desc = " ".join(c for c in r[1:] if c)
        p = _pct_from(desc)
        if p is not None:
            vals.append(p)
    return vals


# v1.6 (BL-DLA-2 table-degraded layouts): cuando find_tables trunca la tabla
# (Nordea LU0173776047: descarta la fila de operación 0,25%) o no devuelve
# tabla alguna (Findlay Park IE0002458671: layout [0,84%] con cabecera de bloque
# partida en columnas -> "corrientes detraídos" no aparece como frase), el texto
# bruto SÍ contiene ambos componentes. Fallback texto-based ANCLADO EN LAS
# ETIQUETAS DE COMPONENTE (gestión / operación), sin depender de la cabecera de
# bloque (que es justo lo que falla en Findlay). El valor puede ir en la misma
# línea que la etiqueta o en una adyacente (wrap). Validado: LU0173776047 1.86,
# IE0002458671 0.95, IE0005300136 0.95, LU0329630130 1.61.
# v2.1 (BL-DLA-2 narrative space-strip + transacción/FR): dos defectos de cobertura
#   en el fallback texto-based, detectados sobre PDFs reales:
#   (A) extract_text() de algunos PRIIPs narrativos (HSBC) CONCATENA palabras sin
#       espacio ("Comisionesdegestiónyotroscostes"); los \s+ NO casaban y text_vals
#       quedaba vacío -> ruled None -> ONLY_BANDS_X espurio. Fix: \s+ -> \s* (misma
#       razón que el header de bands-X). \s* sigue casando el caso con espacios.
#   (C) la línea de operación puede rotularse "Costes de transacción" (GAMCO) y/o su
#       descripción ir en FRANCÉS ("...de la valeur de votre investissement..."); el
#       patrón solo cubría "operación". Añadir transacción + variantes FR. NO inventa:
#       sigue anclado a la etiqueta + % adyacente.
# v2.2 (BL-DLA-2 BNPP/AXA + perf-fee prose, corpus run): dos defectos nuevos:
#   (E) la etiqueta de gestión BNPP/AXA es "Gastos de gestión, y otros gastos
#       administrativos..." (gastos, no comisiones; coma tras gestión). _R_TXT_GEST
#       no casaba -> text_vals solo cogía operación -> ruled 1 componente
#       (FR0012903276: tabla daba 1.24, faltaba +0.11). Añadir 'gastos de gestión'.
#   (F) al ampliar a "transacción" (C), _R_TXT_OPER pasó a casar la PROSA del
#       objetivo ("...previsiones de costes de transacción.") ANTES de la fila de
#       coste real; esa línea no lleva %, y el nearest-value capturaba un % de
#       escenario (BlackRock LU0278718100: operación=22.9 -> OC 24.7). Fix: anclar
#       las etiquetas de operación a INICIO de etiqueta de coste (^\s*(?:costes?|
#       coûts?|frais)\s*de\s*...), de modo que la mención en prosa no case. GAMCO
#       ("Costes de transacción 0,08%", line-leading) sigue casando.
_R_TXT_GEST = re.compile(
    r'^\s*comisiones?\s*de\s*gesti[oó]n|^\s*gastos?\s*de\s*gesti[oó]n'
    r'|^\s*gesti[oó]n\s*[,\s]*y\s*otros\s*(?:costes|gastos)'
    r'|^\s*frais\s*de\s*gestion'
    r'|^\s*management\s*fees?', re.I)
_R_TXT_OPER = re.compile(
    r'^\s*(?:costes?|gastos?)\s*de\s*(?:operaci[oó]n|transacci[oó]n)'
    r'|^\s*co[uû]ts?\s*de\s*(?:transaction|n[ée]gociation)'
    r'|^\s*frais\s*de\s*transaction'
    r'|^\s*transaction\s*costs?', re.I)
_R_TXT_PCT = re.compile(r'(\d{1,3}[.,]\d{1,4})\s*%')


def _oc_from_lines(lines: list):
    """Núcleo de extracción gestión/operación a partir de una lista de líneas de
    texto, sea de pdfplumber (capa de texto) o de OCR (Tesseract). Devuelve
    (gest, oper) floats|None. v2.7 (BL-DLA-2): extraído de _recover_oc_text sin
    cambio de comportamiento, para que la vía OCR reutilice EXACTAMENTE la misma
    lógica nearest-value y se mantengan en lockstep."""
    # Índices de las líneas que contienen CUALQUIER etiqueta de componente o
    # cabecera de grupo: fronteras que acotan la búsqueda de valor (un % más allá
    # de la siguiente etiqueta pertenece a otro componente).
    # v2.1: \s+ -> \s* (texto narrativo concatenado, HSBC) + transacción / rentabilidad
    # como sinónimos de operación / perf-fee, para que las fronteras se detecten igual.
    _BOUND = re.compile(
        r'comisiones?\s*de\s*gesti[oó]n|gesti[oó]n\s*y\s*otros'
        r'|(?:costes?|gastos?)\s*de\s*(?:operaci[oó]n|transacci[oó]n)'
        r'|comisiones?\s*de\s*(?:rendimiento|[eé]xito)'
        r'|costes?\s*accesorios|costes?\s*[uú]nicos'
        r'|management\s*fees?|transaction\s*costs?'
        r'|ongoing\s*costs?|incidental\s*costs?', re.I)
    bound_idx = [i for i, ln in enumerate(lines) if _BOUND.search(ln)]

    def find_val(label_re):
        for k, ln in enumerate(lines):
            if label_re.search(ln):
                m = _R_TXT_PCT.search(ln)
                if m:
                    return float(m.group(1).replace(",", "."))
                # frontera: siguiente línea-etiqueta tras k (no invadir el
                # componente siguiente). hi = primera etiqueta > k.
                hi = next((b for b in bound_idx if b > k), len(lines))
                lo = max((b for b in bound_idx if b < k), default=-1)
                # nearest-value: % más cercano a k en (lo, hi). v2.4 (BL-DLA-2
                # narrative-impact gestión, LU0143551892 + cluster 0.0+X): el valor
                # del componente va en SU PROPIA frase, DESPUÉS de la etiqueta
                # ("...Actualmente 1.69%"). El nearest bidireccional empataba en
                # distancia con un 0,00% de entrada/salida situado ANTES de la
                # etiqueta (mismo d) y lo elegía por orden de iteración -> gestión=0.0.
                # Desempate: a IGUAL distancia, preferir FORWARD (kk>k). No altera el
                # caso sin empate -> wrap value-above-label (-1) y ES (+3) intactos.
                best = None  # (dist, 0=fwd / 1=bwd, value)
                for kk in range(lo + 1, hi):
                    if kk == k:
                        continue
                    m2 = _R_TXT_PCT.search(lines[kk])
                    if m2:
                        cand = (abs(kk - k), 0 if kk > k else 1)
                        if best is None or cand < (best[0], best[1]):
                            best = (cand[0], cand[1],
                                    float(m2.group(1).replace(",", ".")))
                if best is not None:
                    return best[2]
        return None

    gest = find_val(_R_TXT_GEST)
    oper = find_val(_R_TXT_OPER)
    return (gest, oper)


def _recover_oc_text(pdf, max_pages: int = 3):
    """Recupera (gestión, operación) del texto bruto de pdfplumber cuando la
    tabla está degradada. v2.7: delega el parseo en _oc_from_lines (compartido
    con la vía OCR). NO inventa: solo extrae el % de la etiqueta o de una línea
    adyacente (valor en wrap)."""
    lines = []
    for p in pdf.pages[:max_pages]:
        # v2.3 (BL-DLA-2 NFD accents): paridad con bands-X. Normalizar a NFC
        # antes de partir líneas (acentos descompuestos rompen [oó]).
        txt = unicodedata.normalize("NFC", p.extract_text() or "")
        lines.extend(txt.split("\n"))
    return _oc_from_lines(lines)


def extract_ruled_from_pdf(pdf, max_pages: int = 3) -> dict:
    """
    Ruled-table extraction over an ALREADY-OPEN pdfplumber.PDF.

    Memory-safe redesign (v1.1):
      - Accepts an open PDF so we don't reopen per strategy (one open per fund).
      - Limits scanning to first `max_pages` (cost table is always pp. 2-3 in
        PRIIPs KIIDs). Same constraint as bands-X. Cuts memory on long KIIDs.
      - Returns as soon as a usable cost table is found.

    v1.3 — Orphan-value rescue (Candriam et al.):
        En algunos layouts pdfplumber parte una fila lógica en DOS filas
        físicas: una con el valor ("0,02%") en col1 y col0 vacío, y la
        siguiente con la etiqueta ("Costes de operación") en col0 y col1 vacío.
        Sin rescate, la fila etiqueta-only se descarta por "sin valor" y el
        componente de operación se pierde (era el bug Pattern C).
        Solución: cuando una fila etiqueta tiene col1/col3 vacíos, mirar la
        fila inmediatamente anterior por un valor huérfano.
    """
    _best_table = None  # mejor resultado de tabla con 1 componente (v1.6)
    for page in pdf.pages[:max_pages]:
        try:
            tables = page.extract_tables(table_settings=_RULED_SETTINGS)
        except Exception:
            continue
        for t in tables:
            flat = " ".join(c or "" for r in t for c in r).lower()
            if not _R_HEADER.search(flat):
                continue
            oc_components = []
            breakdown = []
            got_oc_direct = None
            # Pre-compute which rows are label-matching, so the rescue can stop
            # before invading another component's territory.
            def _row_matches_any_label(r):
                if not r or not r[0]:
                    return False
                lab = r[0].replace("\n", " ")
                return bool(
                    _R_GESTION.search(lab) or _R_OPER.search(lab)
                    or (_R_OCDIR.search(lab) and not _R_GESTION.search(lab))
                )
            label_rows = [_row_matches_any_label(r) for r in t]

            # v1.7 (BL-DLA-2 perf-fee leak, LU1893894342): el rescue forward de
            # operación cruzaba la cabecera "Costes accesorios" y capturaba la
            # comisión de rendimiento (20,0%) -> o20.0. La barrera label_rows no
            # frena ahí porque "accesorios" no es etiqueta de componente. Añadir
            # barrera de CABECERA DE GRUPO: el rescue (en cualquier dirección) se
            # detiene al topar una fila cuya col0 sea cabecera de grupo.
            def _row_is_group_hdr(r):
                if not r or not r[0]:
                    return False
                return bool(_R_GROUP_HDR.search(r[0].replace("\n", " ")))
            group_rows = [_row_is_group_hdr(r) for r in t]

            for idx, row in enumerate(t):
                if not row or not row[0]:
                    continue
                label = row[0].replace("\n", " ")
                desc = " ".join(c for c in row[1:] if c)
                is_gest = bool(_R_GESTION.search(label) and not _R_OCDIR.search(label))
                is_oper = bool(_R_OPER.search(label))
                # v2.1 (BL-DLA-2 entry-cost leak, GAMCO LU0687943661/944396):
                #   find_tables degrada la tabla y concatena en una sola fila la
                #   cabecera "Costes únicos..." con las filas de entrada/salida; el
                #   texto resultante contiene a la vez "corrientes" (cabecera de grupo
                #   colada) y "de entrada", de modo que _R_OCDIR casaba y la fila se
                #   tomaba como OC directo con el 5,00% de ENTRADA -> direct:5.0. Excluir
                #   del OC-directo cualquier fila que case entrada/salida: ese % nunca
                #   es OC. (No afecta a layouts limpios: ahí la fila "corrientes" no
                #   contiene "de entrada".)
                is_ocdir = bool(_R_OCDIR.search(label) and not _R_GESTION.search(label)
                                and not _R_ENTRY.search(label)
                                and not _R_EXIT.search(label))
                if not (is_gest or is_oper or is_ocdir):
                    continue
                p = _pct_from(desc)
                # Orphan-value rescue (BIDIRECTIONAL): pdfplumber splits a
                # logical row into a label-row with empty value column plus
                # an adjacent value-row with empty label column. The value can
                # be either BEFORE the label (Candriam) or AFTER the label
                # (Polar Capital / Irish layouts where the label wraps onto
                # 3 physical rows and the value sits in the middle row).
                # Look both forward then backward, stopping at the next/prev
                # label-matching row to avoid stealing another component's
                # value. Forward first because in wrap-heavy layouts the
                # value is more reliably below the first label fragment.
                if p is None:
                    # Forward up to 3 rows
                    for j in range(idx + 1, min(idx + 4, len(t))):
                        if label_rows[j] or group_rows[j]:
                            break
                        nxt = t[j]
                        if nxt:
                            nxt_desc = " ".join(c for c in nxt[1:] if c)
                            p2 = _pct_from(nxt_desc)
                            if p2 is not None:
                                p = p2
                                break
                    # Backward up to 2 rows if forward didn't find one
                    if p is None:
                        for j in range(idx - 1, max(idx - 3, -1), -1):
                            if label_rows[j] or group_rows[j]:
                                break
                            prv = t[j]
                            if prv:
                                prv_desc = " ".join(c for c in prv[1:] if c)
                                p2 = _pct_from(prv_desc)
                                if p2 is not None:
                                    p = p2
                                    break
                if p is None:
                    continue
                if is_gest:
                    oc_components.append(p); breakdown.append(f"g{p}")
                elif is_oper:
                    oc_components.append(p); breakdown.append(f"o{p}")
                elif is_ocdir:
                    got_oc_direct = p
            if oc_components:
                _cand = {"oc": round(sum(oc_components), 4),
                         "breakdown": "+".join(str(c) for c in oc_components),
                         "n_components": len(oc_components),
                         "source": "ruled"}
                if len(oc_components) >= 2:
                    return _cand  # tabla completa: resultado fiable
                # 1 solo componente: posible fila de operación descartada por
                # find_tables (Nordea). Guardar como candidato y seguir; el
                # fallback texto se intentará tras el bucle.
                if _best_table is None:
                    _best_table = _cand
                continue
            # v1.5: recuperación por bloque para el layout col0-colapsado (IE/
            # PIMCO). Solo si la clasificación por etiqueta no halló nada.
            block_vals = _recover_oc_block(t)
            if block_vals:
                _cand = {"oc": round(sum(block_vals), 4),
                         "breakdown": "+".join(str(v) for v in block_vals),
                         "n_components": len(block_vals),
                         "source": "ruled_block"}
                if len(block_vals) >= 2:
                    return _cand
                if _best_table is None:
                    _best_table = _cand
                continue
            if got_oc_direct is not None and _best_table is None:
                _best_table = {"oc": round(got_oc_direct, 4),
                               "breakdown": f"direct:{got_oc_direct}",
                               "n_components": 1, "source": "ruled"}
    # ── Fin del bucle de tablas ──────────────────────────────────────────────
    # v1.6: si la tabla solo dio 1 componente (o nada), intentar el fallback
    # texto-based. Se PREFIERE el texto solo si recupera 2 componentes (señal de
    # que la tabla había descartado la operación). Si el texto da <=1, se
    # conserva el resultado de tabla (no degradar lo ya logrado).
    gest_v, oper_v = _recover_oc_text(pdf, max_pages)
    text_vals = [v for v in (gest_v, oper_v) if v is not None]
    if len(text_vals) >= 2:
        return {"oc": round(sum(text_vals), 4),
                "breakdown": "+".join(str(v) for v in text_vals),
                "n_components": len(text_vals),
                "source": "ruled_text"}
    # v2.5 (BL-DLA-2 single-component OC, UBS LU0167295749): cuando la operación
    # es genuinamente 0/ausente (sin %), el texto recupera SOLO gestión. Antes se
    # descartaba (<2) y el fondo caía a {} -> ONLY_BANDS_X. Usar ese único
    # componente como candidato si la tabla no aportó nada mejor.
    # v2.6: SOLO si el componente único es GESTIÓN. operación-sola (gestión
    # ausente) NO es un OC válido -> es un fallo de parseo (p.ej. layout bracket
    # [0,84%] de Findlay, donde se captura solo operación 0.11 y se pierde
    # gestión 0.84). En ese caso devolver {} (fallo honesto), no un OC erróneo.
    if gest_v is not None and oper_v is None and _best_table is None:
        _best_table = {"oc": round(gest_v, 4),
                       "breakdown": str(gest_v),
                       "n_components": 1, "source": "ruled_text"}
    if _best_table is not None:
        return _best_table
    return {}


# ──────────────────────────────────────────────────────────────────────────────
# Arbitraje (clasificación, NO decisión final — eso se decide tras ver datos)
# ──────────────────────────────────────────────────────────────────────────────

_TOL = 0.011  # puntos-%: ~1 bp. Absorbe redondeo a 2 decimales (p.ej. 0.01 vs
              # 0.009 en un componente) sin enmascarar desacuerdos reales (>=0.02
              # pp). v1.7: antes 0.0010 (0.1 bp) generaba CONFLICTs espurios por
              # redondeo de componentes (bands-X redondea, ruled no).


def _plausible(oc):
    return oc is not None and 0.0001 <= oc <= 0.06  # 0.01%–6% en ratio... ojo: aquí oc va en %


def classify(bx_oc, rl_oc):
    """
    bx_oc / rl_oc en PUNTOS % (p.ej. 1.73 = 1,73%). None si no extraído.
    Devuelve etiqueta de arbitraje.
    """
    bx = bx_oc is not None
    rl = rl_oc is not None
    if not bx and not rl:
        return "BOTH_FAIL"
    if bx and not rl:
        return "ONLY_BANDS_X"
    if rl and not bx:
        return "ONLY_RULED"
    # ambos extrajeron
    if abs(bx_oc - rl_oc) <= _TOL:
        return "AGREE"
    return "CONFLICT"


# ──────────────────────────────────────────────────────────────────────────────
# main
# ──────────────────────────────────────────────────────────────────────────────

def main(pdf_dir=None, pdf=None, db_path=None, csv_path=None, log_path=None,
         project_root=None, debug=False, quiet=False):

    root = Path(project_root).resolve() if project_root else Path.cwd()
    xband = _load_xband(root)

    base = Path.cwd()
    csv_file = Path(csv_path) if csv_path else base / "dla2_dual_strategy_compare.csv"
    log_file = Path(log_path) if log_path else base / "dla2_dual_strategy_compare.log"

    if pdf:
        pdfs = [Path(pdf)]
    elif pdf_dir:
        pdfs = sorted(Path(pdf_dir).glob("*.pdf"))
    else:
        print("[FATAL] indica --pdf-dir o --pdf.")
        sys.exit(2)

    # BD opcional para comparar con OC actual.
    db = {}
    if db_path:
        import sqlite3
        try:
            conn = sqlite3.connect(db_path)
            isins = [p.stem for p in pdfs]
            for i in range(0, len(isins), 400):
                chunk = isins[i:i + 400]
                ph = ",".join("?" * len(chunk))
                for r in conn.execute(
                    f"SELECT ISIN, Ongoing_Charge_Recurrent, Cost_Extraction_Quality "
                    f"FROM fund_master WHERE ISIN IN ({ph})", chunk):
                    db[r[0]] = {"oc": r[1], "quality": r[2]}
        except Exception as e:
            print(f"[WARN] BD no cargada: {e}")

    buf = []
    def w(s=""):
        print(s); buf.append(s)

    w("BL-DLA-2-DUALCMP — Comparación de estrategias de extracción de OC")
    w(f"PDFs: {len(pdfs)}")
    w("=" * 70)

    fields = ["ISIN", "bands_x_oc", "bands_x_breakdown", "ruled_oc",
              "ruled_breakdown", "ruled_ncomp", "arbitration",
              "db_oc", "db_oc_pct", "bx_vs_db", "ruled_vs_db",
              "ocr_oc", "ocr_breakdown", "ocr_conf", "ocr_eur_check"]
    rows_out = []
    arb_counter = Counter()

    # OCR fallback (import DIFERIDO: dla2_ocr_fallback importa _oc_from_lines de
    # ESTE módulo; importar arriba crearía un ciclo. Aquí el módulo ya está
    # totalmente definido). Robusto a `-m`, script suelto e import. Solo se
    # invoca en BOTH_FAIL sin capa de texto.
    try:
        from .dla2_ocr_fallback import extract_ocr_from_pdf, pdf_needs_ocr
    except ImportError:
        _hdir = os.path.dirname(os.path.abspath(__file__))
        if _hdir not in sys.path:
            sys.path.insert(0, _hdir)
        from dla2_ocr_fallback import extract_ocr_from_pdf, pdf_needs_ocr

    # Single open per fund: both extractors share the same pdfplumber.PDF.
    # Bands-X (xband.extract_cat2a_xband) accepts a page; ruled accepts the
    # open PDF. Halves cache allocations vs the previous double-open design.
    import gc
    t_start = time.time()

    for idx, p in enumerate(pdfs):
        isin = p.stem
        bx_res, rl_res = {}, {}
        ocr_needed = False
        try:
            # Single open per fund: bands-X y ruled comparten el mismo
            # pdfplumber.PDF (memoria/velocidad). bands-X usa
            # extract_from_open_pdf -> incluye el merge cross-page v2.9 (tablas
            # partidas por salto de página, p.ej. LU2357626170 gestión pág2 +
            # operación pág3) SIN reabrir el PDF.
            with pdfplumber.open(str(p)) as pdf_obj:
                try:
                    bx_res = xband.extract_from_open_pdf(pdf_obj, debug=False) or {}
                except Exception:
                    bx_res = {}
                try:
                    rl_res = extract_ruled_from_pdf(pdf_obj, max_pages=3)
                except Exception:
                    rl_res = {}
                # ¿sin capa de texto? -> candidato OCR (se evalúa con el PDF abierto)
                ocr_needed = pdf_needs_ocr(pdf_obj)
        except Exception:
            pass

        bx_oc_str = bx_res.get("Costes corrientes", "")
        bx_oc = None
        m = _PCT.search(bx_oc_str) if bx_oc_str else None
        if m:
            bx_oc = float(m.group(1).replace(",", ".")) if m.group(1) else 0.0

        rl_oc = rl_res.get("oc")  # en puntos %

        arb = classify(bx_oc, rl_oc)
        # Vía OCR: solo si ambos extractores fallaron Y el PDF no tiene texto.
        ocr_res = {}
        if arb == "BOTH_FAIL" and ocr_needed:
            ocr_res = extract_ocr_from_pdf(str(p)) or {}
            if ocr_res.get("oc") is not None:
                arb = "OCR_RECOVERED"
        arb_counter[arb] += 1

        # comparación con BD (BD OC está en ratio; pasar a % para comparar)
        dbinfo = db.get(isin, {})
        db_oc = dbinfo.get("oc")
        db_oc_pct = (db_oc * 100) if isinstance(db_oc, (int, float)) else None

        def cmp(x):
            if x is None or db_oc_pct is None:
                return ""
            return "OK" if abs(x - db_oc_pct) <= 0.10 else f"DIFF"

        rows_out.append({
            "ISIN": isin,
            "bands_x_oc": bx_oc if bx_oc is not None else "",
            "bands_x_breakdown": bx_res.get("_OC_breakdown", ""),
            "ruled_oc": rl_oc if rl_oc is not None else "",
            "ruled_breakdown": rl_res.get("breakdown", ""),
            "ruled_ncomp": rl_res.get("n_components", ""),
            "arbitration": arb,
            "db_oc": db_oc if db_oc is not None else "",
            "db_oc_pct": round(db_oc_pct, 4) if db_oc_pct is not None else "",
            "bx_vs_db": cmp(bx_oc),
            "ruled_vs_db": cmp(rl_oc),
            "ocr_oc": ocr_res.get("oc", ""),
            "ocr_breakdown": ocr_res.get("breakdown", ""),
            "ocr_conf": ocr_res.get("confidence", ""),
            "ocr_eur_check": ocr_res.get("oc_eur_check", ""),
        })
        # Progreso por fondo: línea única reescrita en sitio (\r). Solo consola,
        # no se vuelca al log para no inflarlo. Suprimible con --quiet.
        if not quiet and not debug and len(pdfs) > 30:
            sys.stdout.write(
                f"\r[{idx+1:>5}/{len(pdfs)}] {isin}  arb={arb:<13} "
                f"bx={('%.2f%%' % bx_oc) if bx_oc is not None else '-':<8} "
                f"ruled={('%.2f%%' % rl_oc) if rl_oc is not None else '-':<8} "
            )
            sys.stdout.flush()
        elif debug or len(pdfs) <= 30:
            w(f"{isin}: bx={bx_oc} ruled={rl_oc} [{arb}] "
              f"db={db_oc_pct if db_oc_pct is not None else '-'}")

        # Cada 50 fondos: gc.collect + resumen completo con tasa y ETA, al log.
        # Sin gc el GC de Python tarda en liberar cachés de pdfplumber/pdfminer
        # y la memoria crece monotonamente sobre miles de PDFs.
        if (idx + 1) % 50 == 0:
            gc.collect()
            if len(pdfs) > 30:
                elapsed = time.time() - t_start
                rate = (idx + 1) / elapsed if elapsed > 0 else 0
                remaining = (len(pdfs) - (idx + 1)) / rate if rate > 0 else 0
                eta_min = int(remaining // 60)
                eta_sec = int(remaining % 60)
                # snapshot acumulado de arbitraje
                snap = "  ".join(f"{k}={v}" for k, v in arb_counter.most_common())
                if not quiet and not debug:
                    sys.stdout.write("\r" + " " * 100 + "\r")  # limpiar la línea live
                w(f"[{idx+1:>5}/{len(pdfs)}]  rate={rate:.1f} pdf/s  "
                  f"ETA={eta_min:02d}m{eta_sec:02d}s  |  {snap}")

    # Limpiar la línea de progreso live antes del resumen.
    if not quiet and not debug and len(pdfs) > 30:
        sys.stdout.write("\r" + " " * 100 + "\r")
        sys.stdout.flush()

    # CSV
    csv_file.parent.mkdir(parents=True, exist_ok=True)
    with csv_file.open("w", encoding="utf-8", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=fields)
        wr.writeheader()
        for r in rows_out:
            wr.writerow(r)

    # Resumen de arbitraje
    n = len(rows_out)
    w("\n" + "=" * 70)
    w("RESUMEN DE ARBITRAJE")
    for k, c in arb_counter.most_common():
        w(f"  {k:14}: {c:5}  ({100*c/n:.1f}%)")
    w("")
    # Métricas clave de regresión/ganancia
    only_bx = arb_counter.get("ONLY_BANDS_X", 0)
    only_rl = arb_counter.get("ONLY_RULED", 0)
    conflict = arb_counter.get("CONFLICT", 0)
    agree = arb_counter.get("AGREE", 0)
    w(f"  Solo bands-X recupera:  {only_bx}  (ruled-first los PERDERÍA -> regresión)")
    w(f"  Solo ruled recupera:    {only_rl}  (bands-X-first los perdería)")
    w(f"  Conflicto de valor:     {conflict}  (requieren regla de arbitraje / revisión)")
    w(f"  Acuerdo:                {agree}  (cualquier orden funciona)")
    w(f"\n  CSV: {csv_file}")

    # comparación con BD si disponible
    if db:
        bx_ok = sum(1 for r in rows_out if r["bx_vs_db"] == "OK")
        rl_ok = sum(1 for r in rows_out if r["ruled_vs_db"] == "OK")
        w(f"\n  Coincidencia con BD: bands-X={bx_ok}  ruled={rl_ok}")

    log_file.write_text("\n".join(buf), encoding="utf-8")
    print(f"\n[LOG] {log_file}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Comparación dual de estrategias OC (BL-DLA-2-DUALCMP).")
    ap.add_argument("--pdf-dir", type=str, default=None)
    ap.add_argument("--pdf", type=str, default=None)
    ap.add_argument("--db", type=str, default=None, dest="db_path")
    ap.add_argument("--csv", type=str, default=None, dest="csv_path")
    ap.add_argument("--log", type=str, default=None, dest="log_path")
    ap.add_argument("--project-root", type=str, default=None, dest="project_root")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--quiet", action="store_true",
                    help="Suprime la línea de progreso por fondo; mantiene resumen cada 50.")
    a = ap.parse_args()
    main(pdf_dir=a.pdf_dir, pdf=a.pdf, db_path=a.db_path, csv_path=a.csv_path,
         log_path=a.log_path, project_root=a.project_root, debug=a.debug,
         quiet=a.quiet)
