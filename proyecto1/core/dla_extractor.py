# -*- coding: utf-8 -*-
"""
proyecto1/core/dla_extractor.py
Document Layout Analysis (DLA) — Fase 1: serialización 2D-aware de párrafos
en layouts de dos columnas.

Versión : 1.2  (BL-DLA-1, piloto 1C — fixes NBSP y fusión con contexto)
Fecha   : 2026-05-03

CONTRATO EXTERNO:
    extract_text_dla_aware(
        pdf_bytes : bytes,
        ocr_enabled : bool = True,
        ocr_lang    : str  = "spa+eng",
        ocr_dpi     : int  = 300,
        debug       : bool = False,
    ) -> tuple[str, dict]

    Devuelve:
        kiid_text      — string compatible con la salida actual de io.py
        layout_meta    — dict de telemetría (una entrada por página)

DISEÑO:
    Detecta el layout de cada página usando una heurística de dos niveles:
      Nivel 1 (siempre): width-only.
          SINGLE_COL  — n_full >= n_narrow  (predominio de bloques anchos)
          TWO_COL     — n_narrow > n_full Y ≥ MIN_BLOCKS_PER_COLUMN en cada mitad
          MIXED       — n_narrow > n_full pero columnas desbalanceadas
          NO_TEXT     — sin bloques de texto
      Nivel 2 (solo si MIXED): gap-detection.
          Histograma X de centros de bloque; si hay banda vertical vacía
          de ancho >= GAP_MIN_RATIO * page_width → TWO_COL; si no → SINGLE_COL.

    Serialización:
        SINGLE_COL  → orden natural (y0, x0): idéntico al comportamiento actual.
        TWO_COL     → columna izquierda completa (y0, x0) luego derecha completa (y0, x0).
        NO_TEXT     → fallback OCR, idéntico al actual (Fase 4 diferida).

    Separador entre bloques: "\n" (idéntico a pdfplumber.extract_text() por defecto).
    Separador entre páginas: "\n" (idéntico a io.py:extract_text_from_pdf_bytes()).

    Esta elección garantiza que text.replace(" ", "") —capa L0-FUSED de
    srri_text.py v3— produzca una cadena fusionada equivalente a la actual,
    ya que el separador entre bloques no contiene espacios adicionales.

UMBRALES (calibrados sobre corpus Q-DLA-03, 300 fondos, sesión 02-may-2026):
    NARROW_THRESHOLD      = 0.55   (bloque estrecho: ancho < 55% de página)
    FULL_THRESHOLD        = 0.70   (bloque ancho: ancho > 70% de página)
    MIN_BLOCKS_PER_COLUMN = 3      (condición mínima para TWO_COL)
    GAP_MIN_RATIO         = 0.07   (banda vacía mínima para gap-detection)
    GAP_BIN_WIDTH         = 5      (resolución del histograma X, en puntos PDF)

MÓDULOS NO MODIFICADOS:
    kiid_parser.py, srri_text.py, srri_v4_geometric.py, classify_utils.py,
    blocks/*.py.  La interfaz downstream (kiid_text: str) es idéntica.

KILL-SWITCH:
    Controlado en io.py mediante DLA_ENABLED. Este módulo no se importa
    si DLA_ENABLED = False.

RIESGOS DOCUMENTADOS (BL_DLA_DESIGN_DECISION.md, sección 6):
    R1  — Patrones L0-FUSED (srri_text.py): mitigado por separador "\n" sin espacios extra.
    R2  — Layouts atípicos (3-col, sidebar): mitigado por fallback SINGLE_COL con log.
    R5  — Detectores con dependencia ordinal: sin dependencias ordinales identificadas
          en kiid_parser.py (secciones KIID son independientes semánticamente).
"""

from __future__ import annotations

import logging
from io import BytesIO
from typing import Optional

import re

import fitz  # PyMuPDF — ya disponible (srri_v4_geometric.py lo importa)

try:
    import pytesseract
    _HAS_TESSERACT = True
except Exception:
    _HAS_TESSERACT = False

# ── Constantes ────────────────────────────────────────────────────────────────

# Heurística Nivel 1: width-only
# Calibradas sobre corpus Q-DLA-03 (300 fondos, sesión 02-may-2026).
# Deben mantenerse sincronizadas con dla_layout_inventory.py.
NARROW_THRESHOLD      = 0.55   # bloque estrecho si ancho < 55% ancho_página
FULL_THRESHOLD        = 0.70   # bloque ancho   si ancho > 70% ancho_página
MIN_BLOCKS_PER_COLUMN = 3      # mínimo de bloques por mitad para declarar TWO_COL

# Heurística Nivel 2: gap-detection (solo páginas MIXED)
GAP_MIN_RATIO = 0.07   # banda vacía debe ser ≥ 7% del ancho de página
GAP_BIN_WIDTH = 5      # ancho del bin del histograma en puntos PDF (≈ 1.76 mm)

MAX_PDF_PAGES = 3      # idéntico a io.py:MAX_PDF_PAGES

# Constantes de clasificación
_LABEL_SINGLE  = "SINGLE_COL"
_LABEL_TWO     = "TWO_COL"
_LABEL_MIXED   = "MIXED"
_LABEL_NO_TEXT = "NO_TEXT"

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# NORMALIZACIÓN DE TEXTO DE BLOQUE
# ═══════════════════════════════════════════════════════════════════════════════

def _normalize_block_text(text: str) -> str:
    """
    Normaliza el texto de un bloque fitz antes de serializar.

    Resuelve dos patologías documentadas en el piloto BL-DLA-1 Sub-fase 1C:

    BUG-1 — Fragmentación de líneas (IE00BZ4D7085, LU1458464713):
        fitz codifica algunas líneas como palabras individuales separadas por LF
        dentro de un mismo bloque:
            "El \\nFondo \\npuede \\ninvertir \\nen \\nderivados \\nfinancieros \\n"
        Causa raíz: el PDF tiene kerning especial o fue generado con herramientas
        que insertan LF explícito después de cada token de texto.
        Fix: líneas de ≤3 palabras sin puntuación final se fusionan con la
        siguiente línea usando un espacio, hasta encontrar una línea larga
        o con puntuación final que indica salto semántico real.

    BUG-2 — Non-breaking spaces NBSP \\xa0 (LU1084165304 Fidelity):
        fitz preserva los NBSP del PDF que inflaman el tamaño del texto (+7.8%)
        y rompen los patrones regex que esperan espacios normales.
        Causa raíz: el PDF Fidelity codifica los separadores como " \\xa0"
        (espacio ASCII seguido de NBSP). La versión anterior usaba
        text.replace("\\xa0", " "), que convertía " \\xa0" en "  " (doble espacio)
        — el tamaño no bajaba y aparecían 1.026 dobles espacios.
        Fix correcto: re.sub(r"[ \\xa0]+", " ", text) colapsa cualquier secuencia
        de espacios normales y/o NBSP en un único espacio ASCII, eliminando los
        dobles espacios residuales.
        Con este fix: LU1084165304 pasa de 14.505 a ~13.281 chars (-1.3% vs NoDLA).

    BUG-1 revisado — Fusión con contexto (IE00BZ4D7085, LU1458464713):
        La versión anterior no fusionaba líneas cortas con puntuación final
        ("Canadá.") aunque la SIGUIENTE línea también fuera corta (indicando
        que la fragmentación continuaba). Resultado: "Canadá.\\nEl Fondo puede
        invertir en derivados" en lugar de "Canadá. El Fondo puede invertir...".
        Causa raíz: ends_punct bloqueaba la fusión incondicionalmente.
        Fix: una línea corta (≤3 palabras) con puntuación final SÍ se fusiona
        si la línea siguiente también es corta (≤3 palabras) — es fragmentación
        continuada. Solo se trata como fin de párrafo real si la siguiente línea
        es larga (>3 palabras) o está vacía.

    Garantías:
        - El separador entre bloques (\\n) se preserva.
        - Los LF semánticos reales se preservan: línea corta con punct seguida
          de línea larga = fin de frase real (no se fusiona).
        - No se introducen espacios dobles.
    """
    # FIX BUG-2: colapsar secuencias de espacio ASCII + NBSP en un único espacio.
    # re.sub(r"[ \xa0]+", " ") maneja: " \xa0" → " ", "\xa0\xa0" → " ", "\xa0" → " ".
    text = re.sub(r'[ \xa0]+', ' ', text)

    # Strip del LF final del bloque
    text = text.rstrip('\n')

    # FIX BUG-1 revisado: fusión con contexto de línea siguiente
    lines = text.split('\n')
    result: list = []
    i = 0
    _PUNCT_END = ('.', ',', ':', ';', '!', '?', '-', '–', ')')
    _SHORT = 3   # umbral de línea corta (palabras)

    while i < len(lines):
        line    = lines[i]
        stripped = line.strip()
        words    = stripped.split()
        n_words  = len(words)
        ends_p   = stripped.endswith(_PUNCT_END)

        # Línea siguiente (para decidir si ends_punct es fin real o fragmentación)
        next_stripped = lines[i + 1].strip() if i + 1 < len(lines) else ''
        next_n_words  = len(next_stripped.split())
        next_is_short = 0 < next_n_words <= _SHORT

        # Condición de fusión:
        #   (a) línea corta SIN punct final → siempre fragmentación
        #   (b) línea corta CON punct final + siguiente también corta → fragmentación
        #       continuada (e.g. "Canadá." seguido de "El")
        #   (c) línea corta CON punct final + siguiente larga → fin de frase real
        is_fragment = (
            n_words > 0
            and n_words <= _SHORT
            and i < len(lines) - 1
            and (not ends_p or (ends_p and next_is_short))
        )

        if is_fragment:
            merged = stripped
            i += 1
            while i < len(lines):
                nxt          = lines[i].strip()
                nxt_words    = nxt.split()
                nxt_n        = len(nxt_words)
                nxt_ends_p   = nxt.endswith(_PUNCT_END)
                nxt2_stripped = lines[i + 1].strip() if i + 1 < len(lines) else ''
                nxt2_n        = len(nxt2_stripped.split())
                nxt2_is_short = 0 < nxt2_n <= _SHORT

                if nxt_n > 0:
                    merged = merged + ' ' + nxt
                    i += 1
                    # Parar si llegamos a línea larga (fin de fragmentación)
                    if nxt_n > _SHORT:
                        break
                    # Parar si línea corta con punct Y siguiente es larga
                    # (fin semántico dentro de la fusión)
                    if nxt_ends_p and not nxt2_is_short:
                        break
                else:
                    i += 1
                    break
            result.append(merged)
        else:
            result.append(line)
            i += 1

    return '\n'.join(result)


# ═══════════════════════════════════════════════════════════════════════════════
# NIVEL 1 — Clasificación por anchura de bloques
# ═══════════════════════════════════════════════════════════════════════════════

def _get_text_blocks(page: fitz.Page) -> list:
    """
    Retorna bloques de texto no vacíos de la página.
    Formato de cada bloque: (x0, y0, x1, y1, text, block_no, block_type).
    Solo block_type == 0 (texto).
    """
    return [
        b for b in page.get_text("blocks")
        if b[6] == 0 and b[4].strip()
    ]


def classify_page_layout_level1(page: fitz.Page) -> str:
    """
    Nivel 1: heurística width-only.
    Clasifica en SINGLE_COL, TWO_COL, MIXED o NO_TEXT.

    Reglas (orden de evaluación):
      1. Sin bloques de texto → NO_TEXT.
      2. n_full >= n_narrow   → SINGLE_COL  (predominio de bloques anchos).
      3. n_narrow > n_full Y ≥ MIN_BLOCKS_PER_COLUMN en cada mitad → TWO_COL.
      4. Resto                → MIXED.

    Esta función es equivalente a classify_page_layout() de dla_layout_inventory.py
    y debe mantenerse sincronizada con ella para coherencia diagnóstica.
    """
    page_w = page.rect.width
    blocks = _get_text_blocks(page)

    if not blocks:
        return _LABEL_NO_TEXT

    widths  = [b[2] - b[0] for b in blocks]
    n_narrow = sum(1 for w in widths if w < page_w * NARROW_THRESHOLD)
    n_full   = sum(1 for w in widths if w > page_w * FULL_THRESHOLD)

    if n_full >= n_narrow:
        return _LABEL_SINGLE

    # n_narrow > n_full: comprobar si hay bloques suficientes en ambas mitades.
    mid_x = page_w / 2
    n_left  = sum(1 for b in blocks if (b[0] + b[2]) / 2 <  mid_x)
    n_right = sum(1 for b in blocks if (b[0] + b[2]) / 2 >= mid_x)

    if n_left >= MIN_BLOCKS_PER_COLUMN and n_right >= MIN_BLOCKS_PER_COLUMN:
        return _LABEL_TWO

    return _LABEL_MIXED


# ═══════════════════════════════════════════════════════════════════════════════
# NIVEL 2 — Gap-detection (solo para MIXED)
# ═══════════════════════════════════════════════════════════════════════════════

def classify_page_layout_level2(page: fitz.Page) -> str:
    """
    Nivel 2: gap-detection por histograma X.
    Solo se invoca cuando Nivel 1 devuelve MIXED.

    Algoritmo:
      1. Construir histograma de densidad de centros X de bloques,
         con bins de GAP_BIN_WIDTH puntos.
      2. Buscar una banda vertical consecutiva de bins vacíos cuya
         anchura total sea >= GAP_MIN_RATIO * page_width.
      3. Si existe → TWO_COL (la banda vacía es el gutter de columnas).
         Si no      → SINGLE_COL (distribución heterogénea sin separación clara).

    Coste adicional: O(n_blocks) sobre los mismos bloques del Nivel 1.
    Aplica al 7,7% de páginas del corpus (MIXED en Q-DLA-03).
    """
    page_w = page.rect.width
    blocks = _get_text_blocks(page)

    if not blocks:
        return _LABEL_SINGLE

    # Bin del histograma: entero = floor(centro_x / GAP_BIN_WIDTH)
    n_bins     = int(page_w / GAP_BIN_WIDTH) + 1
    occupied   = set()
    for b in blocks:
        center_x = (b[0] + b[2]) / 2
        occupied.add(int(center_x / GAP_BIN_WIDTH))

    # Buscar la mayor banda consecutiva de bins vacíos
    max_gap_bins = 0
    current_gap  = 0
    for bin_idx in range(n_bins):
        if bin_idx not in occupied:
            current_gap += 1
            max_gap_bins = max(max_gap_bins, current_gap)
        else:
            current_gap = 0

    max_gap_pts = max_gap_bins * GAP_BIN_WIDTH
    threshold_pts = page_w * GAP_MIN_RATIO

    if max_gap_pts >= threshold_pts:
        return _LABEL_TWO
    return _LABEL_SINGLE


def classify_page_layout(page: fitz.Page) -> str:
    """
    Clasificación completa de dos niveles.
    Nivel 2 solo se invoca si Nivel 1 devuelve MIXED (≈7,7% de páginas).
    """
    level1 = classify_page_layout_level1(page)
    if level1 == _LABEL_MIXED:
        level2 = classify_page_layout_level2(page)
        return level2   # TWO_COL o SINGLE_COL — MIXED no se propaga
    return level1


# ═══════════════════════════════════════════════════════════════════════════════
# SERIALIZACIÓN POR PÁGINA
# ═══════════════════════════════════════════════════════════════════════════════

def _serialize_single_col(blocks: list) -> str:
    """
    Serialización SINGLE_COL: orden natural (y0, x0).
    Equivalente al comportamiento de pdfplumber.extract_text() por defecto.
    """
    sorted_blocks = sorted(blocks, key=lambda b: (b[1], b[0]))
    return "\n".join(_normalize_block_text(b[4]) for b in sorted_blocks)


def _serialize_two_col(blocks: list, page_w: float) -> str:
    """
    Serialización TWO_COL: bloques full-width primero (orden Y),
    luego columna izquierda completa (y0, x0), luego derecha completa (y0, x0).

    BUG-3 — Bloques full-width con centro en mid_x (LU0177592218 Schroders):
        En PDFs con layout asimétrico, algunos bloques de cabecera/pie tienen
        w > 70% de la página (e.g. 93%) pero su centro cae ligeramente por encima
        o por debajo de mid_x. Con la lógica izquierda/derecha pura, estos bloques
        se clasifican de forma aleatoria en una de las dos columnas, rompiendo
        el orden semántico (nombre del producto pasa al final del texto).
        Causa raíz: los bloques full-width no son bloques de columna — son
        elementos transversales que deben preceder a las columnas.
        Fix: extraer los bloques full-width (w > FULL_THRESHOLD) antes de la
        serialización por columnas y emitirlos primero en orden Y estricto.
        Esto garantiza que "Producto / Emerging Markets Debt Total Return" aparezca
        al inicio del texto, antes que los bloques de columna izquierda/derecha,
        restaurando la capacidad del detector de Type en kiid_parser.py.

    Separador entre secciones: "\\n" — idéntico al separador entre bloques.
    Los LF internos se normalizan vía _normalize_block_text().
    """
    mid_x = page_w / 2

    # Separar bloques full-width (transversales) de bloques de columna
    full_blocks = [b for b in blocks if (b[2] - b[0]) > page_w * FULL_THRESHOLD]
    col_blocks  = [b for b in blocks if (b[2] - b[0]) <= page_w * FULL_THRESHOLD]

    full_blocks.sort(key=lambda b: b[1])   # orden Y estricto

    left  = [b for b in col_blocks if (b[0] + b[2]) / 2 <  mid_x]
    right = [b for b in col_blocks if (b[0] + b[2]) / 2 >= mid_x]
    left.sort( key=lambda b: (b[1], b[0]))
    right.sort(key=lambda b: (b[1], b[0]))

    parts = []
    if full_blocks:
        parts.append("\n".join(_normalize_block_text(b[4]) for b in full_blocks))
    if left:
        parts.append("\n".join(_normalize_block_text(b[4]) for b in left))
    if right:
        parts.append("\n".join(_normalize_block_text(b[4]) for b in right))
    return "\n".join(parts)


def _serialize_page(page: fitz.Page, layout: str) -> str:
    """
    Serializa el texto de una página según su layout clasificado.

    SINGLE_COL / NO_TEXT_fallback → orden natural.
    TWO_COL                       → izquierda completa, luego derecha.

    NO_TEXT: devuelve cadena vacía — el fallback OCR lo gestiona la capa
    superior (extract_text_dla_aware), idéntico a io.py actual.
    """
    blocks = _get_text_blocks(page)

    if layout == _LABEL_NO_TEXT or not blocks:
        return ""   # fallback OCR manejado en la capa superior

    if layout == _LABEL_TWO:
        return _serialize_two_col(blocks, page.rect.width)

    # SINGLE_COL (o cualquier otro caso no previsto → comportamiento seguro)
    return _serialize_single_col(blocks)


# ═══════════════════════════════════════════════════════════════════════════════
# API PÚBLICA — extract_text_dla_aware
# ═══════════════════════════════════════════════════════════════════════════════

def extract_text_dla_aware(
    pdf_bytes: bytes,
    ocr_enabled: bool = True,
    ocr_lang: str = "spa+eng",
    ocr_dpi: int = 300,
    debug: bool = False,
) -> tuple:
    """
    Extrae texto de un PDF con serialización 2D-aware (Fase 1 DLA).

    Contrato de salida:
        (kiid_text: str, layout_meta: dict)

        kiid_text   — string lista para consumo por kiid_parser.py y bloques.
                      Compatible con la salida de io.py:extract_text_from_pdf_bytes().
        layout_meta — dict de telemetría:
            {
              "pages": [
                  {"page": 0, "layout": "TWO_COL",    "level": 1},
                  {"page": 1, "layout": "SINGLE_COL",  "level": 1},
                  {"page": 2, "layout": "TWO_COL",    "level": 2},  # nivel 2 aplicado
              ],
              "n_two_col":    2,
              "n_single_col": 1,
              "n_no_text":    0,
              "strategy":     "COL_REORDER",  # o "NATURAL" si todo SINGLE_COL
              "fallback":     False,
            }

    Política de fallback:
        Si fitz falla al abrir/procesar el PDF, se captura la excepción y
        se devuelve ("", {"fallback": True, "error": <msg>}). El caller
        (io.py) debe manejar este caso intentando la ruta pdfplumber original.
    """
    layout_meta: dict = {
        "pages": [],
        "n_two_col":    0,
        "n_single_col": 0,
        "n_no_text":    0,
        "strategy":     "NATURAL",
        "fallback":     False,
    }

    text_parts = []

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        layout_meta["fallback"] = True
        layout_meta["error"]    = f"fitz_open_error: {exc}"
        return "", layout_meta

    try:
        for i, page in enumerate(doc):
            if i >= MAX_PDF_PAGES:
                break

            try:
                level1 = classify_page_layout_level1(page)
                if level1 == _LABEL_MIXED:
                    final_layout = classify_page_layout_level2(page)
                    level_applied = 2
                else:
                    final_layout  = level1
                    level_applied = 1

                page_text = _serialize_page(page, final_layout)

                # Registro de telemetría por página
                layout_meta["pages"].append({
                    "page":   i,
                    "layout": final_layout,
                    "level":  level_applied,
                })

                # Contadores agregados
                if final_layout == _LABEL_TWO:
                    layout_meta["n_two_col"] += 1
                elif final_layout == _LABEL_NO_TEXT:
                    layout_meta["n_no_text"] += 1
                else:
                    layout_meta["n_single_col"] += 1

                if page_text and page_text.strip():
                    text_parts.append(page_text)
                    continue

                # ── Página sin texto extraíble → fallback OCR (idéntico a io.py) ──
                if ocr_enabled and _HAS_TESSERACT:
                    try:
                        # pdfplumber se usa solo para renderizar la imagen OCR.
                        # Abrimos aquí exclusivamente para el path OCR.
                        import pdfplumber
                        with pdfplumber.open(BytesIO(pdf_bytes)) as pdf_pp:
                            if i < len(pdf_pp.pages):
                                img = pdf_pp.pages[i].to_image(resolution=ocr_dpi).original
                                ocr_text = pytesseract.image_to_string(img, lang=ocr_lang)
                                if ocr_text and ocr_text.strip():
                                    text_parts.append(ocr_text)
                    except Exception as ocr_exc:
                        if debug:
                            logger.debug("[DLA-OCR] página %d error OCR: %s", i, ocr_exc)
                        # No propagamos el error; la página queda sin texto.

            except Exception as page_exc:
                # Error en página individual → fallback a texto plano de fitz
                if debug:
                    logger.debug("[DLA] página %d error: %s; fallback get_text()", i, page_exc)
                try:
                    raw = page.get_text()
                    if raw and raw.strip():
                        text_parts.append(raw)
                except Exception:
                    pass

    finally:
        doc.close()

    # Telemetría de estrategia global
    if layout_meta["n_two_col"] > 0:
        layout_meta["strategy"] = "COL_REORDER"

    kiid_text = "\n".join(text_parts)

    if debug:
        _log_dla_summary(layout_meta, len(kiid_text))

    return kiid_text, layout_meta


# ═══════════════════════════════════════════════════════════════════════════════
# LOGGING / TELEMETRÍA
# ═══════════════════════════════════════════════════════════════════════════════

def emit_dla_log(isin: str, layout_meta: dict) -> None:
    """
    Emite una línea de log estructurada por fondo, conforme al formato
    especificado en BL_DLA_DESIGN_DECISION.md sección 5.5:

        [DLA] LU0006277684 layout=[1col,2col,2col] strategy=COL_REORDER pages=3
        [DLA-FALLBACK] LU9999999 layout_undetermined → fallback_pdfplumber
        [DLA-OCR] LU8888888 page_2 capa_texto_vacia → OCR (sin DLA)
    """
    if layout_meta.get("fallback"):
        logger.info(
            "[DLA-FALLBACK] %s layout_undetermined -> fallback_pdfplumber error=%s",
            isin,
            layout_meta.get("error", "unknown"),
        )
        return

    sig_map = {
        _LABEL_SINGLE:  "1col",
        _LABEL_TWO:     "2col",
        _LABEL_NO_TEXT: "ocr",
    }
    sig_parts = [sig_map.get(p["layout"], "?") for p in layout_meta["pages"]]
    sig       = ",".join(sig_parts)
    n_pages   = len(layout_meta["pages"])
    strategy  = layout_meta.get("strategy", "NATURAL")

    logger.info(
        "[DLA] %s layout=[%s] strategy=%s pages=%d",
        isin, sig, strategy, n_pages,
    )

    # Log adicional para páginas OCR
    for p in layout_meta["pages"]:
        if p["layout"] == _LABEL_NO_TEXT:
            logger.info(
                "[DLA-OCR] %s page_%d capa_texto_vacia -> OCR (sin DLA)",
                isin, p["page"],
            )


def emit_dla_cycle_summary(stats: dict) -> None:
    """
    Emite el resumen de ciclo DLA en formato especificado en
    BL_DLA_DESIGN_DECISION.md sección 5.5.

    stats debe contener:
        n_processed, n_two_col, n_single_col, n_fallback
    """
    n = max(stats.get("n_processed", 1), 1)
    lines = [
        "--- RESUMEN DLA DEL CICLO ---",
        f"Fondos procesados con DLA  : {stats.get('n_processed', 0)}",
        f"Con layout 2-col detectado : {stats.get('n_two_col', 0)} ({stats.get('n_two_col', 0) / n * 100:.1f}%)",
        f"Con layout 1-col puro      : {stats.get('n_single_col', 0)} ({stats.get('n_single_col', 0) / n * 100:.1f}%)",
        f"Con fallback pdfplumber    : {stats.get('n_fallback', 0)} ({stats.get('n_fallback', 0) / n * 100:.1f}%)",
        "---",
    ]
    for line in lines:
        logger.info(line)


def _log_dla_summary(layout_meta: dict, text_len: int) -> None:
    """Debug interno — solo si debug=True."""
    pages_info = [(p["page"], p["layout"], p["level"]) for p in layout_meta["pages"]]
    logger.debug(
        "[DLA-DEBUG] pages=%s strategy=%s text_len=%d",
        pages_info, layout_meta.get("strategy"), text_len,
    )
