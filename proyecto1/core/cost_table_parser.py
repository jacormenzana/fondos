# proyecto1/core/cost_table_parser.py
# -*- coding: utf-8 -*-
"""
cost_table_parser.py — parser de las dos tablas PRIIPs de costes.

BL-COST-4 (Sprint 2 S2-A): módulo nuevo. Dependencia de priips_cost_extractor.py
(S2-B) y ucits_cost_extractor.py (S2-C).
BL-COST-3-FIX (Sprint 2 S2-D): espacio opcional en todos los patrones multipalabra ES.
BL-COST-PARSER-FIX-1 (Sprint 2 S2-D): ventana de fragmento acotada a la
    siguiente etiqueta de coste en _parse_composition_plain. Causa raiz:
    cuando una fila de coste no tiene valor numerico propio (ej: Costes de
    operacion vacio), el parser capturaba el porcentaje de la seccion
    siguiente (ej: 20pct performance fee asignado a transaction_cost_pct).
    Fondos confirmados afectados: LU1048657123, LU0966156399, LU0966156126.
    Misma causa raíz que cost_format_router: texto pegado sin espacios en
    PDFs de ciertos emisores (JPMorgan, HSBC). Patrones afectados:
    COSTS_OVER_TIME_HEADER, RHP_PATTERN, ACI_ROW, TOTAL_COSTS_ROW,
    COMPOSITION_HEADER, COMPOSITION_ROW_LABELS.

Parsea dos tablas del KID PRIIPs:
  1. "Costes a lo largo del tiempo" / "Costs over time"
  2. "Composición de los costes" / "Composition of costs"

Detecta automáticamente si el texto está en formato DLA2 (contiene '|||')
o en texto plano. Aplica el parser adecuado en cada caso.

Funciones exportadas:
    parse_costs_over_time(text)   -> List[dict]
    parse_costs_composition(text) -> dict

Funciones internas (prefijo _):
    _parse_costs_over_time_dla2(text)
    _parse_costs_over_time_plain(text)
    _parse_composition_dla2(text)
    _parse_composition_plain(text)
    _normalize_amount(s)
    _parse_horizon_years(label)
    _is_rhp_label(label)

Reglas de robustez:
    - Ninguna excepción sale al caller: todos los parsers capturan internamente.
    - Valores ausentes se omiten del dict (no se incluye la clave, no se pone None).
    - _normalize_amount maneja separadores europeos (punto=miles, coma=decimal).
    - Ventanas acotadas en todos los patrones de búsqueda (R-6).
"""

import re
import math
from typing import List, Optional


# ======================================================================
# Constantes y patrones compilados al nivel de módulo
# ======================================================================

# Delimitador DLA2 — serialización de tablas
DLA2_SEPARATOR = '|||'

# Importe absoluto en moneda (EUR/USD/etc. explícito o implícito)
# Grupo 1: la cantidad numérica
AMOUNT_PATTERN = re.compile(
    r'(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?)\s*(?:EUR|USD|GBP|CHF|SEK|NOK|DKK|PLN|CZK)?',
    re.IGNORECASE,
)

# Porcentaje — grupo 1: valor
PCT_PATTERN = re.compile(r'(\d+[,.]?\d*)\s*%')

# Máximo fee: "hasta X%", "up to X%", "máximo X%", "maximum X%", "at most X%"
MAX_FEE_PATTERN = re.compile(
    r'(?:hasta|up\s+to|m[aá]ximo|maximum|at\s+most)\s+(?:el\s+)?(\d+[,.]?\d*)\s*%',
    re.IGNORECASE,
)

# Horizonte en años — grupo 1 (ES) o grupo 2 (EN)
HORIZON_YEARS_PATTERN = re.compile(
    r'(\d+)\s*a[ñn]os?|(\d+)\s*years?',
    re.IGNORECASE,
)

# Horizonte en meses — grupo 1 (ES) o grupo 2 (EN)
HORIZON_MONTHS_PATTERN = re.compile(
    r'(\d+)\s*mes(?:es)?|(\d+)\s*months?',
    re.IGNORECASE,
)

# Señal de RHP (Período de Mantenimiento Recomendado)
# \s* cubre texto pegado sin espacios (BL-COST-3-FIX)
RHP_PATTERN = re.compile(
    r'per[ií]odo\s*de\s*mantenimiento\s*recomendado'
    r'|recommended\s*holding\s*period'
    r'|mantenimiento\s*recomendado'
    r'|\bRHP\b|\bPMR\b',   # FIX-P1-B: abbreviated column headers in some PDFs
    re.IGNORECASE,
)

# Encabezado de sección "Costes a lo largo del tiempo"
# \s* cubre texto pegado sin espacios (BL-COST-3-FIX)
COSTS_OVER_TIME_HEADER = re.compile(
    r'costes?\s*a\s*lo\s*largo\s*del\s*tiempo|costs?\s*over\s*time',
    re.IGNORECASE,
)

# Encabezado de sección "Composición de los costes"
# \s* cubre texto pegado sin espacios (BL-COST-3-FIX)
COMPOSITION_HEADER = re.compile(
    r'composici[oó]n\s*de\s*los\s*costes?|composition\s*of\s*costs?',
    re.IGNORECASE,
)

# Fila de costes totales en tabla "over time"
# \s* cubre texto pegado sin espacios (BL-COST-3-FIX)
# FIX-P1-I: anclado a celda completa (^...$) + variante 'Costes' a secas.
# Algunos emisores (DWS / Deutsche Wealth: ES0125756017, ES0139012001)
# etiquetan la fila de costes totales sólo como "Costes" (sin "totales"),
# en una tabla OT por lo demás idéntica. El patrón anterior
# (costes?\s*totales?) exigía "totales" y nunca casaba -> total_row=None.
# El anclado a celda completa es OBLIGATORIO: una variante laxa 'costes?'
# sin anclar haría .search() positivo en "Costes de entrada/salida/
# operación/corrientes/accesorios" y capturaría la fila equivocada
# (recordatorio: el bucle de selección usa first-match-wins en L452).
# La cola [*().:\d\s]* tolera marcadores ("*", "(*)") y dígitos pegados.
TOTAL_COSTS_ROW = re.compile(
    r'^\s*(?:costes?\s*totales?|costes?|total\s*costs?)\s*[*().:\d\s]*$',
    re.IGNORECASE,
)

# Fila de ACI (Incidencia Anual de los Costes)
# \s* cubre texto pegado sin espacios (BL-COST-3-FIX)
ACI_ROW = re.compile(
    # FIX-P1-H: "anual" made optional.
    # FIX-P1-R: add "Impacto anual/en los costes" — IE/Irish PRIIPS KIDs use
    # "Impacto" instead of "Incidencia" (confirmed IE000VOGDDG7, IE0000J01ZR0,
    # IE0005023803, ~218 corpus funds). Also adds bare "Impacto en los costes"
    # without "anual". Pattern anchored to avoid matching generic "impacto".
    r'incidencia\s*(?:anual\s*)?de\s*los\s*costes?'
    r'|impacto\s*(?:anual\s*)?en\s*los\s*costes?'
    r'|annual\s*cost\s*impact',
    re.IGNORECASE,
)

# Mapeo de etiquetas de filas de composición → clave interna
# \s* cubre texto pegado sin espacios (BL-COST-3-FIX)
COMPOSITION_ROW_LABELS = {
    r'costes?\s*de\s*entrada|entry\s*(?:charge|cost)s?':             'entry',
    r'costes?\s*de\s*salida|exit\s*(?:charge|cost)s?':               'exit',
    r'comisiones?\s*de\s*gesti[oó]n|management\s*(?:fee|cost)s?':    'management',
    r'costes?\s*de\s*operaci[oó]n|transaction\s*costs?':             'transaction',
    r'comisiones?\s*(?:en\s*funci[oó]n\s*de\s*la\s*rentabilidad'
    r'|de\s*(?:[eé]xito|rendimiento))'
    r'|performance\s*fees?':                                          'performance',
}
# Compilar patrones de etiqueta una sola vez
_COMPOSITION_PATTERNS = [
    (re.compile(pat, re.IGNORECASE), key)
    for pat, key in COMPOSITION_ROW_LABELS.items()
]

# Rangos máximos razonables por tipo de coste (% entero post-conversión).
# Valores extraídos que superen estos límites se descartan como falsos positivos.
# Principio: mejor NULL que un valor absurdo que viola el CHECK constraint.
_COMPOSITION_MAX_PCT = {
    'entry':       25.0,   # CHECK constraint fund_master <= 25
    'exit':        25.0,   # CHECK constraint fund_master <= 25
    'management':   5.0,   # CHECK constraint fund_master <= 10; 5% es límite real
    'transaction':  2.0,   # CHECK constraint fund_master <= 5; 2% es límite real
    'performance': 30.0,   # CHECK constraint fund_master <= 30
}
_COST_TYPE_SUFFIX = {
    'entry':       'fee',
    'exit':        'fee',
    'management':  'fee',
    'transaction': 'cost',
    'performance': 'fee',
}


# ======================================================================
# Funciones auxiliares internas
# ======================================================================

def _normalize_amount(s: str) -> Optional[float]:
    """
    Normaliza un string numérico a float, manejando separadores europeos.

    Heurística de desambiguación punto/coma:
    - Si hay punto Y coma:
        - "1.360,00" → punto=miles, coma=decimal → 1360.0
        - "1,360.00" → coma=miles, punto=decimal → 1360.0
    - Si solo hay punto:
        - Si el bloque tras el punto tiene exactamente 3 dígitos → miles ("1.360" → 1360)
        - Si no → decimal ("1.5" → 1.5, "153" → 153.0)
    - Si solo hay coma:
        - Si el bloque tras la coma tiene exactamente 3 dígitos → miles ("1,360" → 1360)
        - Si no → decimal ("1,5" → 1.5)

    Args:
        s: string numérico, posiblemente con separadores de miles y/o decimal.

    Returns:
        float o None si no se puede parsear.
    """
    if not s:
        return None
    s = s.strip()
    # Eliminar caracteres que no sean dígitos, punto, coma o signo
    s = re.sub(r'[^\d.,]', '', s)
    if not s:
        return None

    has_dot   = '.' in s
    has_comma = ',' in s

    try:
        if has_dot and has_comma:
            # Determinar cuál va primero para identificar el separador de miles
            last_dot   = s.rfind('.')
            last_comma = s.rfind(',')
            if last_dot > last_comma:
                # "1,360.00" — coma=miles, punto=decimal
                s = s.replace(',', '')
            else:
                # "1.360,00" — punto=miles, coma=decimal
                s = s.replace('.', '').replace(',', '.')
        elif has_dot:
            # Solo punto: ¿miles o decimal?
            after_dot = s.split('.')[-1]
            if len(after_dot) == 3:
                # "1.360" → miles
                s = s.replace('.', '')
            # else: "1.5" → decimal, dejar como está
        elif has_comma:
            # Solo coma: ¿miles o decimal?
            after_comma = s.split(',')[-1]
            if len(after_comma) == 3:
                # "1,360" → miles
                s = s.replace(',', '')
            else:
                # "1,5" → decimal
                s = s.replace(',', '.')

        return float(s)
    except (ValueError, IndexError):
        return None


def _is_rhp_label(label: str) -> bool:
    """True si la etiqueta corresponde al Período de Mantenimiento Recomendado."""
    return bool(RHP_PATTERN.search(label))


def _parse_horizon_years(label: str) -> float:
    """
    Convierte una etiqueta de horizonte temporal a años en float.

    Valores especiales:
        RHP (período de mantenimiento recomendado) → -1.0  (señal: is_rhp=True)

    Conversiones:
        "1 año" / "1 year"   → 1.0
        "5 años" / "5 years" → 5.0
        "3 meses"            → 0.25  (3/12)
        "6 meses"            → 0.5
        "1 mes"              → 0.083 (1/12, redondeado)

    Args:
        label: texto de la etiqueta de horizonte.

    Returns:
        float (años), o -1.0 si es RHP.
    """
    if _is_rhp_label(label):
        return -1.0

    m = HORIZON_YEARS_PATTERN.search(label)
    if m:
        years_str = m.group(1) or m.group(2)
        return float(years_str)

    m = HORIZON_MONTHS_PATTERN.search(label)
    if m:
        months_str = m.group(1) or m.group(2)
        months = float(months_str)
        return round(months / 12.0, 4)

    return -1.0


def _extract_pct_from_cell(cell: str) -> Optional[float]:
    """
    Extrae el primer porcentaje de una celda como ratio decimal (ej: "1,11%" → 0.0111).
    Retorna None si no hay porcentaje.
    """
    m = PCT_PATTERN.search(cell)
    if not m:
        return None
    # BL-COST-PCT-NORM-FIX: un % de coste nunca lleva separador de millares (un coste
    # es 0-100%). _normalize_amount trata un grupo de 3 dígitos tras el separador como
    # millares ("0.007"/"0,007" -> 7.0), inflado luego por _ratio_to_pct_safe a 7.0 y
    # anulado por _guarded_pct (>2). Esto destruía los costes de operación sub-0,01%
    # (0,007% / 0,009%), que son REALES (no descartables). Parseo decimal directo.
    g = m.group(1).replace(',', '.')
    if g.count('.') > 1:                      # defensivo: nunca esperado en un %
        g = g.replace('.', '', g.count('.') - 1)
    try:
        val = float(g)
    except (ValueError, TypeError):
        return None
    # Convertir de porcentaje a ratio decimal
    return round(val / 100.0, 6)


def _extract_max_pct_from_cell(cell: str) -> Optional[float]:
    """
    Extrae "hasta X%" / "up to X%" de una celda como ratio decimal.
    Retorna None si no hay patrón máximo.
    """
    m = MAX_FEE_PATTERN.search(cell)
    if not m:
        return None
    val = _normalize_amount(m.group(1))
    if val is None:
        return None
    return round(val / 100.0, 6)


def _extract_eur_from_cell(cell: str) -> Optional[float]:
    """
    Extrae el primer importe numérico de una celda como float.
    Preferencia: importe con símbolo de moneda explícito; si no, primer número >= 1.
    Retorna None si no hay número válido.
    """
    # Buscar importe con moneda explícita
    m = re.search(
        r'(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?)\s*(?:EUR|USD|GBP|CHF|€|\$)',
        cell, re.IGNORECASE,
    )
    if m:
        return _normalize_amount(m.group(1))

    # Fallback: primer número >= 1 en la celda (evita 0 como false positive)
    m = AMOUNT_PATTERN.search(cell)
    if m:
        val = _normalize_amount(m.group(1))
        if val is not None and val >= 1.0:
            return val
    return None


# ======================================================================
# Parser DLA2: Costes a lo largo del tiempo
# ======================================================================

def _split_dla2_row(row: str) -> List[str]:
    """
    Divide una fila DLA2 por el separador '|||'.
    Elimina celdas vacías de los extremos (la fila empieza y termina con |||).
    """
    cells = row.split(DLA2_SEPARATOR)
    # Eliminar primer y último elemento vacíos (artefacto del formato |||..|||)
    if cells and cells[0].strip() == '':
        cells = cells[1:]
    if cells and cells[-1].strip() == '':
        cells = cells[:-1]
    return [c.strip() for c in cells]


def _parse_costs_over_time_dla2(text: str) -> List[dict]:
    """
    Parser para formato DLA2 de la tabla "Costes a lo largo del tiempo".

    Estrategia:
    1. Extraer las líneas DLA2 (contienen '|||') del bloque de texto.
    2. Localizar la sección de costes a lo largo del tiempo.
    3. Dentro de esa sección, identificar:
       - Fila de encabezado: contiene etiquetas de horizonte.
       - Fila de costes totales: contiene "costes totales" / "total costs".
       - Fila de ACI: contiene "incidencia anual" / "annual cost impact".
    4. Para cada columna de horizonte: construir un dict de resultado.
    """
    results = []

    # Extraer solo líneas DLA2
    dla2_lines = [ln for ln in text.splitlines() if DLA2_SEPARATOR in ln]
    if not dla2_lines:
        return []

    # Localizar la línea de inicio de la sección "costes a lo largo del tiempo"
    section_start = None
    for i, ln in enumerate(dla2_lines):
        if COSTS_OVER_TIME_HEADER.search(ln):
            section_start = i
            break
    if section_start is None:
        return []

    # Tomar un bloque razonable de líneas tras el encabezado (máx. 15)
    section_lines = dla2_lines[section_start: section_start + 15]

    # FIX-P1-J: label-split fragmentation. pdfplumber can insert a stray |||
    # INSIDE the label cell, e.g. "Incidencia anual de|||los costes (*)|||...".
    # Then cells[0] == "Incidencia anual de" — a truncated fragment that neither
    # ACI_ROW nor TOTAL_COSTS_ROW can match, so the row is never identified and
    # the ACI/total values are lost. Fix: match the label against a rejoined prefix
    # of up to 3 leading cells; when the match only succeeds AFTER joining, collapse
    # the consumed label fragments into a single cell 0 so downstream extractors
    # still see the value cells at the right offsets. Clean rows (cells[0] already
    # matches) are untouched. Defined early (before header scan) so FIX-P1-O can
    # also use it.
    def _match_with_join(cells, pattern):
        """Return (matched: bool, normalized_cells). Tries cells[0], then
        cells[0..1], then cells[0..2] joined. On a join-match, fragments are
        merged into cell 0 and the remaining (value) cells preserved.
        FIX-P1-N: also tries no-separator concatenation to handle mid-word
        |||splits (e.g. 'cos' + 'tes*' must join to 'costes*', not 'cos tes*')."""
        if pattern.search(cells[0]):
            return True, cells
        for _k in (2, 3):
            if len(cells) >= _k:
                for _sep in (' ', ''):
                    _joined = _sep.join(cells[:_k]).strip()
                    if pattern.search(_joined):
                        return True, [_joined] + cells[_k:]
        return False, cells

    # Identificar fila de horizonte: la que tiene >= 1 etiqueta de año/mes/RHP
    # en alguna celda que NO sea la primera (primera celda = etiqueta de fila)
    header_row_idx = None
    header_cells: List[str] = []
    for i, ln in enumerate(section_lines):
        cells = _split_dla2_row(ln)
        if len(cells) < 2:
            continue
        # Comprobar celdas 1..N (no la 0, que es la etiqueta)
        horizon_count = sum(
            1 for c in cells[1:]
            if HORIZON_YEARS_PATTERN.search(c)
            or HORIZON_MONTHS_PATTERN.search(c)
            or _is_rhp_label(c)
        )
        if horizon_count >= 1:
            header_row_idx = i
            header_cells = cells
            break

    # FIX-P1-O: "mega-cell" header — all table content crammed into a single DLA2
    # cell. Confirmed pattern (423 sampled corpus funds, 2026-06-27): pdfplumber
    # serializes the entire page into one |||cell||| so the mega-cell ends up as
    # cells[0] (after _split_dla2_row strips leading empty). The year keyword is
    # in cells[0] — NOT in cells[1:] which the header detector checks — so
    # horizon_count=0, header_row_idx stays None, and the parser exits even though
    # structured data rows (Costes totales / Incidencia anual) ARE present later.
    # Fix: scan section_lines for the FIRST data row (ACI or total). Place the
    # synthetic header at the preceding index so the data scan (which starts at
    # header_row_idx+1) picks up BOTH the total and ACI rows. FIX-P1-M then
    # infers the RHP column from compact-value count.
    if header_row_idx is None:
        _first_data_idx = None
        for _i, _ln in enumerate(section_lines):
            _c = _split_dla2_row(_ln)
            _m1, _ = _match_with_join(_c, ACI_ROW)
            _m2, _ = _match_with_join(_c, TOTAL_COSTS_ROW)
            if _m1 or _m2:
                _first_data_idx = _i
                break
        if _first_data_idx is None:
            return []
        # Synthetic header: put it just before the first data row.
        # data scan below runs from (header_row_idx + 1), so all data rows
        # starting at _first_data_idx are covered.
        header_row_idx = max(0, _first_data_idx - 1)
        header_cells   = ['', '1 año']

    if len(header_cells) < 2:
        return []

    # FIX-P1-C: merge split OT header rows.
    # Some PDFs make pdfplumber break the OT column-header row into two rows:
    #   row 0: ["", "Si sale después del PMR", "Si sale"]
    #   row 1: ["", "[5 años]", "de 1 año"]
    # The parser takes row 0 as the header; "Si sale" has no year → -1.0 fallback
    # → is_rhp=True for the wrong column (ACI_1Y=NULL, ACI_RHP=wrong value).
    # Fix: if the immediately-following row has an empty first cell, is not a data
    # row, and at least one non-first cell adds year/month/RHP info, merge it.
    _cont_idx = header_row_idx + 1
    if _cont_idx < len(section_lines):
        _cont_cells = _split_dla2_row(section_lines[_cont_idx])
        _c0 = _cont_cells[0].strip() if _cont_cells else "x"
        _not_data = not _c0 and not (
            TOTAL_COSTS_ROW.search(_cont_cells[0]) if _cont_cells else False
        ) and not (
            ACI_ROW.search(_cont_cells[0]) if _cont_cells else False
        )
        if _not_data and len(_cont_cells) >= 2:
            _merged = list(header_cells)
            _any_ext = False
            for _ci in range(1, min(len(header_cells), len(_cont_cells))):
                _nc = _cont_cells[_ci].strip()
                if _nc and (HORIZON_YEARS_PATTERN.search(_nc) or
                            HORIZON_MONTHS_PATTERN.search(_nc) or
                            _is_rhp_label(_nc)):
                    _merged[_ci] = (_merged[_ci] + " " + _nc).strip()
                    _any_ext = True
            if _any_ext:
                header_cells = _merged

    # Horizonte labels (índice 1..N de header_cells)
    horizon_labels = header_cells[1:]

    # Buscar filas de datos en las líneas restantes de la sección
    total_row:  Optional[List[str]] = None
    aci_row:    Optional[List[str]] = None

    for ln in section_lines[header_row_idx + 1:]:
        cells = _split_dla2_row(ln)
        if not cells:
            continue
        if total_row is None:
            _m, _norm = _match_with_join(cells, TOTAL_COSTS_ROW)
            # Skip mega-cells: a legitimate label cell is short (<150 chars).
            # Mega-cells cram the entire page into cells[0] (500+ chars) and
            # contain no structured value cells — they produce empty compact
            # lists and corrupt column inference.
            if _m and len(_norm[0]) < 150:
                total_row = _norm
                continue
        if aci_row is None:
            _m, _norm = _match_with_join(cells, ACI_ROW)
            if _m and len(_norm[0]) < 150:
                aci_row = _norm
                continue

    # FIX-P1-W: mega-cell ACI extraction fallback.
    # When no structured aci_row was found (len < 150 guard rejected all), check
    # if any section line is a mega-cell (cells[0] >= 100 chars) that contains
    # the ACI label embedded in a single prose block. Extract the ACI % from the
    # 200 chars after the label position. This handles FR0007435920-type layouts
    # where pdfplumber collapses the OT table into one cell: the structured
    # total_row IS present (len < 150) but the ACI row is only in the mega-cell.
    if aci_row is None:
        for _wln in section_lines[max(0, header_row_idx):]:
            _wc = _split_dla2_row(_wln)
            if not _wc or len(_wc[0]) < 100:
                continue
            _wm = ACI_ROW.search(_wc[0])
            if _wm:
                _wfrag = _wc[0][_wm.end(): _wm.end() + 200]
                _wpct_m = re.search(r'(\d+[,.]?\d*)\s*%', _wfrag)
                if _wpct_m:
                    _wval = float(_wpct_m.group(1).replace(',', '.'))
                    if 0.0 <= _wval <= 25.0:
                        aci_row = [_wc[0][:60], f'{_wval}%']
                break

    # FIX-P1-G: compacted non-empty pairing.
    # pdfplumber's per-row column banding can insert a phantom blank cell in
    # the header row that is NOT mirrored at the same index in the data rows,
    # e.g.:
    #   header: ["1 Año", "", "5 años (PMR)"]      (blank at index 2)
    #   aci row: ["7.3%", "3.4%", ""]               (blank at index 3)
    # Strict positional data_col=col_idx+1 then maps the PMR/RHP column to the
    # blank data cell, losing the real value (which landed one position left).
    # Truncated single-horizon headers ("En caso de salida después de" with no
    # year, FR0000989626-style) hit the same shape: blank label cells that
    # collapse n_cols to 1, triggering the existing single-column RHP fallback.
    # Fix: drop blank header labels, then pair the k-th surviving label with
    # the k-th value that successfully extracts (not by raw index) — but only
    # when the extracted-value count matches the surviving-label count exactly,
    # so a genuinely missing data point (real horizon, no disclosed value)
    # still resolves to None rather than absorbing a neighbour's value.
    # FIX-P1-G: drop blank horizon labels (original logic).
    # FIX-P1-U: also drop investment-base cells ('Inversión: 10 000 €' etc.).
    # Certain DLA2 formats serialize the example-investment header as an
    # extra non-empty cell BETWEEN the label column and the actual time-horizon
    # columns. Without this guard FIX-P1-G keeps it, inflating n_cols from 2
    # to 3 and causing _aci_paired=False → wrong positional assignment.
    _BASE_CELL_RE = re.compile(
        r'(?:inversi[oó]n|invest(?:ment)?|ejemplo|example)\b'
        r'.*?(?:\d{4,}|\d{1,3}(?:[\s.,]\d{3})+)',
        re.I | re.DOTALL,
    )
    _label_idx = [
        i for i, lbl in enumerate(horizon_labels)
        if lbl.strip() and not _BASE_CELL_RE.search(lbl)
    ]
    horizon_labels = [horizon_labels[i] for i in _label_idx]
    n_cols = len(horizon_labels)

    def _compact_values(row, extractor):
        if not row:
            return []
        out = []
        cells = row[1:]
        i = 0
        while i < len(cells):
            v = extractor(cells[i])
            if v is not None:
                out.append(v)
                i += 1
            else:
                # FIX-P1-P: mid-value |||split (e.g. '1,3' + '%' in separate
                # cells). pdfplumber can break "1,3%" at the decimal-sign boundary
                # → cells carry bare number and bare "%" individually, neither of
                # which passes _extract_pct_from_cell. Try joining consecutive
                # non-empty cells (up to 3) before giving up.
                joined = cells[i]
                merged = False
                for _j in range(i + 1, min(i + 3, len(cells))):
                    joined = joined + cells[_j]
                    v2 = extractor(joined.strip())
                    if v2 is not None:
                        out.append(v2)
                        i = _j + 1
                        merged = True
                        break
                if not merged:
                    i += 1
        return out

    _total_compact = _compact_values(total_row, _extract_eur_from_cell)
    _aci_compact    = _compact_values(aci_row, _extract_pct_from_cell)
    _total_paired = len(_total_compact) == n_cols
    _aci_paired   = len(_aci_compact) == n_cols

    # FIX-P1-M: infer additional RHP columns from compact-value count.
    # Root cause (345/500 sampled corpus funds, dominant ACI_RHP-missing driver,
    # confirmed 2026-06-27): many PRIIPs OT tables embed the 1Y label inside a
    # description row ("en caso de salida después de 1 año"), which the parser
    # correctly takes as a 1-column header. But the actual serialized table has 2
    # value columns (1Y + RHP) — confirmed by _compact_values finding 2 non-empty
    # values in the ACI / total rows while n_cols == 1. By PRIIPS regulation
    # (PRIIPs KID RTS Annex VI), a 2-column OT table always shows:
    #   col 0: "if you exit after 1 year"
    #   col 1: "if you exit at the recommended holding period (RHP)"
    # When compact count exceeds the detected header count, the extra columns
    # are inferred as RHP. The 'RHP' string literal triggers _is_rhp_label()
    # downstream (matched by RHP_PATTERN's \bRHP\b branch).
    # Safety cap: PRIIPS OT tables have at most 2 columns; cap extension at 1
    # extra column to avoid runaway inference on malformed serializations.
    _n_compact = max(len(_aci_compact), len(_total_compact))
    if _n_compact > n_cols:
        _extra = min(_n_compact - n_cols, 1)
        for _ in range(_extra):
            horizon_labels.append('RHP')
        n_cols = len(horizon_labels)
        _total_paired = len(_total_compact) == n_cols
        _aci_paired   = len(_aci_compact) == n_cols

    # Construir resultados por columna
    for col_idx, horizon_label in enumerate(horizon_labels):
        data_col = col_idx + 1  # índice posicional en las filas de datos (fallback)

        horizon_years = _parse_horizon_years(horizon_label)
        # FIX-P1-D: the old fallback `or (horizon_years == -1.0)` marked ANY
        # unrecognised cell as is_rhp=True in multi-column tables, causing the 1Y
        # column to be assigned ACI_RHP and ACI_1Y to stay NULL.
        # Restrict: use the -1.0 fallback only for single-column OT tables (where
        # the sole column is almost certainly the RHP). For n_cols >= 2, only an
        # explicit RHP keyword (RHP_PATTERN, now including PMR/RHP abbreviations)
        # marks a column as is_rhp.
        is_rhp = _is_rhp_label(horizon_label) or (horizon_years == -1.0 and n_cols == 1)
        # FIX-P1-T: date-based RHP (e.g. 'Si sale después de 31/12/2028').
        # Some PRIIPS KIDs replace the N-year RHP column header with the fund's
        # maturity/redemption date (DD/MM/YYYY). _parse_horizon_years returns
        # -1.0 for unrecognised labels; the column is NOT is_rhp by FIX-P1-D
        # (n_cols>=2). Detect: hy=-1.0 AND label contains a date AND it is the
        # LAST column (the second OT column is always RHP by PRIIPS regulation).
        if not is_rhp and horizon_years == -1.0 and col_idx == n_cols - 1:
            if re.search(r'\d{1,2}/\d{1,2}/\d{4}', horizon_label):
                is_rhp = True
        if is_rhp:
            horizon_years = -1.0

        total_cost_eur: Optional[float] = None
        aci_pct: Optional[float] = None

        if _total_paired:
            total_cost_eur = _total_compact[col_idx]
        elif total_row and data_col < len(total_row):
            total_cost_eur = _extract_eur_from_cell(total_row[data_col])
        # FIX-P1-F: pdfplumber sometimes merges the value into the label cell
        # (e.g. "|||Costes totales 84 EUR|||||||||"). Fall back to cell 0.
        if total_cost_eur is None and total_row:
            total_cost_eur = _extract_eur_from_cell(total_row[0])

        if _aci_paired:
            aci_pct = _aci_compact[col_idx]
        elif aci_row and data_col < len(aci_row):
            aci_pct = _extract_pct_from_cell(aci_row[data_col])
        # FIX-P1-F: same fallback for ACI % embedded in label cell
        # (e.g. "|||Incidencia anual de los costes 0.8%|||||||||").
        if aci_pct is None and aci_row:
            aci_pct = _extract_pct_from_cell(aci_row[0])

        entry: dict = {
            'horizon_label':  horizon_label,
            'horizon_years':  horizon_years,
            'is_rhp':         is_rhp,
            'source':         'DLA2',
        }
        if total_cost_eur is not None:
            entry['total_cost_eur'] = total_cost_eur
        else:
            entry['total_cost_eur'] = None
        if aci_pct is not None:
            entry['aci_pct'] = aci_pct
        else:
            entry['aci_pct'] = None

        results.append(entry)

    return results


# ======================================================================
# Parser texto plano: Costes a lo largo del tiempo
# ======================================================================

def _parse_costs_over_time_plain(text: str) -> List[dict]:
    """
    Parser para texto plano de la tabla "Costes a lo largo del tiempo".

    Estrategia:
    - Localizar el encabezado de la sección (ventana de ±600 chars).
    - Dentro de esa ventana buscar:
        * Etiquetas de horizonte: "después de N año(s)", "after N year(s)", RHP
        * Importes EUR en líneas subsiguientes
        * Porcentajes ACI en líneas subsiguientes
    - Devolver una entrada por horizonte encontrado.
    """
    m_hdr = COSTS_OVER_TIME_HEADER.search(text)
    if not m_hdr:
        return []

    # Ventana de búsqueda: desde el encabezado hasta 1500 chars después.
    # FIX-P1-Q: extended from 800 to 1500. Measurement on 118 measurable
    # has_pct_no_aci_label funds (2026-06-27) showed median ACI-label offset
    # = 762 chars, p90 = 801 chars — just above the old 800-char limit.
    # 1500 covers all measured offsets with safety margin.
    window_start = m_hdr.start()
    window_end   = min(len(text), window_start + 1500)
    window       = text[window_start:window_end]

    results = []

    # Buscar patrones de horizonte dentro de la ventana
    # Patrón: "después de N año(s)" / "after N year(s)" / RHP / "N año(s)"
    HORIZON_CONTEXT = re.compile(
        r'(?:despu[eé]s\s+de|after|si\s+(?:sale|retira)(?:\s+\w+)?\s+despu[eé]s\s+de)?'
        r'\s*'
        r'(?:'
        r'(\d+)\s*a[ñn]os?'         # N años
        r'|(\d+)\s*years?'           # N years
        r'|(\d+)\s*mes(?:es)?'       # N meses
        r'|(\d+)\s*months?'          # N months
        r'|(' + RHP_PATTERN.pattern + r')'  # RHP
        r')',
        re.IGNORECASE,
    )

    seen_horizons: set = set()
    for m in HORIZON_CONTEXT.finditer(window):
        label = m.group(0).strip()

        horizon_years = _parse_horizon_years(label)
        is_rhp = _is_rhp_label(label) or (horizon_years == -1.0)

        # Deduplicar por (horizon_years, is_rhp) — not by raw label string.
        # FIX-P1-Q's larger window can find both '5 años' and '5 año' (singular)
        # from the same table printed twice; both parse to hy=5.0, so only the
        # FIRST occurrence (which usually has the richer fragment) is kept.
        _hy_key = (round(horizon_years, 2), is_rhp)
        if _hy_key in seen_horizons:
            continue
        seen_horizons.add(_hy_key)

        # Buscar importe EUR y ACI en un fragmento de ~200 chars tras esta mención
        frag_start = m.end()
        frag_end   = min(len(window), frag_start + 200)
        fragment   = window[frag_start:frag_end]

        total_cost_eur = _extract_eur_from_cell(fragment)
        aci_pct        = _extract_pct_from_cell(fragment)

        results.append({
            'horizon_label': label,
            'horizon_years': horizon_years,
            'is_rhp':        is_rhp,
            'total_cost_eur': total_cost_eur,
            'aci_pct':        aci_pct,
            'source':         'PLAIN_TEXT',
        })

    # FIX-P1-E: global horizon fallback.
    # Some PDFs (UBS SGIIC and ~1,453 corpus funds) place the OT column header
    # "En caso de salida después de 1 año" in the scenarios section, BEFORE
    # "Costes a lo largo del tiempo" in pdfplumber text order.  The 800-char
    # window therefore contains no HORIZON_CONTEXT match → results is empty
    # even though the OT header and ACI % are present in the window.
    # Fix: search the FULL text globally for holding-period year references,
    # then extract the ACI % from near "incidencia anual" inside the OT window.
    # FIX-P1-E extension: fire when results exist but all aci_pct are None.
    # FIX-P1-Q extended the window from 800→1500, so HORIZON_CONTEXT now finds
    # year markers that were previously out of range. For funds where the ACI
    # value appears BEFORE the year marker in the text flow (not after), the
    # 200-char post-match fragment misses the value. FIX-P1-E recovers it by
    # anchoring on the ACI label instead. Extend to fire whenever no ACI was
    # found; REPLACE any all-None results if global fallback yields aci.
    if not results or all(e.get('aci_pct') is None for e in results):
        _global_years = sorted({
            int(y)
            for y in re.findall(r'despu[eé]s\s+de\s+(\d+)\s+a[ñn]os?', text, re.I)
        })[:2]
        if _global_years:
            _anc = re.search(
                r'incidencia\s+anual|impacto\s+(?:anual\s+)?en\s+los\s+costes?',
                window, re.I
            )
            _zone = window[_anc.start():_anc.start() + 250] if _anc else window
            _aci = _extract_pct_from_cell(_zone)
            _eur = _extract_eur_from_cell(window)
            if _aci is not None:
                results = []  # replace all-None results; harmless when results was []
            for _y in _global_years:
                results.append({
                    'horizon_label':  f'despues de {_y} anos',
                    'horizon_years':  float(_y),
                    'is_rhp':         False,
                    'total_cost_eur': _eur,
                    'aci_pct':        _aci,
                    'source':         'PLAIN_GLOBAL_FALLBACK',
                })

    return results


# ======================================================================
# Parser DLA2: Composición de los costes
# ======================================================================

# FIX-P2-SWAP (2026-06-20): investment-base parser, shared single source.
# Used by the EUR-cross-check that corrects the mgmt%<->oper% column collision
# (pdfplumber bleeds the operación % into the gestión row's % cell on certain
# DWS/ES layouts; the EUR value in that row stays correct, so mgmt_eur/base*100
# reconstructs the true mgmt%). Corpus audit (2026-06-20): base parseable on
# 2996/3204 funds, 2991 == 10000, all 53 affected swap funds recover base=10000.
# Patterns validated against DWS/UBS/Groupama real KIDs. scripts\diag\
# audit_investment_base.py imports THIS function (DRY, §Y.1).
_BASE_NUM = r'(\d{1,3}(?:[ .,]\d{3})+|\d{4,6})'
_BASE_CUR = r'(?:EUR|USD|GBP|CHF|€|\$|£)'
_BASE_PATTERNS = [
    re.compile(r'se\s+invierten\s+' + _BASE_NUM + r'\s*(' + _BASE_CUR + r')?', re.I),
    re.compile(r'(?:ejemplo\s+de\s+)?inversi[oó]n(?:\s+de)?\s*:?\s*(?:' + _BASE_CUR + r'\s*)?' + _BASE_NUM, re.I),
    re.compile(r'para\s+una\s+inversi[oó]n\s+de\s*:?\s*' + _BASE_NUM, re.I),
    re.compile(r'invest(?:ment)?\s+(?:of\s+)?(?:' + _BASE_CUR + r'\s*)?' + _BASE_NUM, re.I),
]


def parse_investment_base(text: str) -> Optional[int]:
    """Return the PRIIPS example-investment base (e.g. 10000) or None.
    Bounded, pure. None when no base statement is found — callers must NOT
    fall back to a hardcoded base (5 corpus funds use 100000/1000000)."""
    if not text:
        return None
    for pat in _BASE_PATTERNS:
        m = pat.search(text)
        if m:
            digits = re.sub(r'[ .,]', '', m.group(1))
            try:
                val = int(digits)
                if 1000 <= val <= 1000000:
                    return val
            except ValueError:
                pass
    return None


def _parse_composition_dla2(text: str) -> dict:
    """
    Parser para formato DLA2 de la tabla "Composición de los costes".

    Estrategia:
    1. Localizar la sección en las líneas DLA2.
    2. Para cada línea posterior, identificar el tipo de coste por la etiqueta
       (celda 0) usando COMPOSITION_ROW_LABELS.
    3. Extraer porcentaje y EUR de las celdas 1..N.
    4. Detectar también "hasta X%" para entry/exit_fee_max_pct.
    """
    result: dict = {}

    dla2_lines = [ln for ln in text.splitlines() if DLA2_SEPARATOR in ln]
    if not dla2_lines:
        return result

    # Localizar sección composición
    section_start = None
    for i, ln in enumerate(dla2_lines):
        if COMPOSITION_HEADER.search(ln):
            section_start = i
            break
    if section_start is None:
        return result

    section_lines = dla2_lines[section_start + 1: section_start + 15]

    for ln in section_lines:
        cells = _split_dla2_row(ln)
        if not cells:
            continue
        row_label = cells[0]
        row_text  = ' '.join(cells)  # texto completo de la fila para búsqueda

        cost_type = None
        for pat, key in _COMPOSITION_PATTERNS:
            if pat.search(row_label):
                cost_type = key
                break
        if cost_type is None:
            continue

        # Porcentaje de la descripción (celda de descripción o la fila completa)
        # BL-COST-PARSER-FIX-2: guard de rango (antes esta vía DLA2 no filtraba).
        pct = _guarded_pct(_extract_pct_from_cell(row_text), cost_type)
        if pct is not None:
            result[f"{cost_type}_{_COST_TYPE_SUFFIX.get(cost_type, 'fee')}_pct"] = pct

        # Máximo porcentaje (hasta X%)
        max_pct = _guarded_pct(_extract_max_pct_from_cell(row_text), cost_type)
        if max_pct is not None:
            result[f"{cost_type}_{_COST_TYPE_SUFFIX.get(cost_type, 'fee')}_max_pct"] = max_pct

        # Importe EUR (buscar celda con moneda explícita)
        for cell in cells[1:]:
            eur = _extract_eur_from_cell(cell)
            if eur is not None:
                result[f"{cost_type}_{_COST_TYPE_SUFFIX.get(cost_type, 'fee')}_eur"] = eur
                break

    # FIX-P2-SWAP (2026-06-20): correct the mgmt%<->oper% column collision.
    # On certain DWS/ES layouts pdfplumber bleeds the operación % into the
    # gestión row's % cell, so management_fee_pct == transaction_cost_pct (both
    # show the oper value) while the gestión EUR amount stays correct.
    # Signature (ALL must hold, so clean rows are never touched):
    #   - both mgmt% and oper% present AND ~equal (the bleed tell), and
    #   - mgmt EUR present, and investment base parseable, and
    #   - EUR-implied mgmt% disagrees with the (collided) grid mgmt%.
    # Then trust the EUR-derived value: mgmt% = mgmt_eur / base * 100.
    # Never fires without a parsed base (5 corpus funds use 100k/1M bases;
    # dividing by a hardcoded 10000 would corrupt them).
    _mgmt_pct = result.get('management_fee_pct')
    _oper_pct = result.get('transaction_cost_pct')
    _mgmt_eur = result.get('management_fee_eur')
    if (_mgmt_pct is not None and _oper_pct is not None and _mgmt_eur is not None
            and math.isclose(_mgmt_pct, _oper_pct, abs_tol=0.001, rel_tol=0.01)):
        _base = parse_investment_base(text)
        if _base:
            # Parser canonical scale is RATIO (0.011 == 1.1%), confirmed:
            # _extract_pct_from_cell("0,15%") -> 0.0015. So the EUR-implied
            # value is eur/base (NOT eur/base*100).
            _implied = round(_mgmt_eur / _base, 6)
            # only override if the implied value genuinely differs from the
            # collided grid value (tolerant of rounding)
            if (_implied is not None
                    and not math.isclose(_implied, _mgmt_pct, abs_tol=0.0001, rel_tol=0.02)):
                _guarded = _guarded_pct(_implied, 'management')
                if _guarded is not None:
                    result['management_fee_pct'] = _guarded
                    result['management_fee_pct_source'] = 'EUR_DERIVED'

    return result


def _ratio_to_pct_safe(ratio: float) -> float:
    """Convierte ratio decimal a porcentaje para comparación de rangos. 0.0085 → 0.85."""
    if ratio is None:
        return 0.0
    # Si ya parece porcentaje entero (> 0.5), devolver tal cual
    return ratio * 100.0 if ratio <= 0.5 else ratio


def _guarded_pct(pct, cost_type: str):
    """Descarta (→ None) un % de coste fuera del rango persistible para su tipo.

    BL-COST-PARSER-FIX-2: guard ÚNICO (DRY) usado por AMBOS parsers de
    composición — DLA2 (_parse_composition_dla2) y texto plano
    (_parse_composition_plain). Antes solo el texto plano filtraba, y solo por
    cota superior; la vía DLA2 no filtraba nada. Al activarse la serialización
    DLA2-v2, valores fuera de dominio (un % de la sección siguiente mal asignado,
    o un transaction cost NEGATIVO por la metodología de slippage PRIIPs — real
    pero no persistible) llegaban a la BD y violaban el CHECK
    'Transaction_Cost_Pct >= 0 AND <= 5', tumbando todo el publish_fund.

    - Cota superior: _COMPOSITION_MAX_PCT[cost_type] (default 30).
    - Cota inferior: 0 — el CHECK de fund_master exige '>= 0'. Mejor NULL que un
      IntegrityError que pierde el fondo entero. (Si en el futuro se decide
      preservar costes negativos legítimos PRIIPs, relajar el CHECK a '>= -1' es
      una migración aparte; hoy NULL es la opción honesta y schema-segura.)
    """
    if pct is None:
        return None
    p = _ratio_to_pct_safe(pct)
    if p < 0 or p > _COMPOSITION_MAX_PCT.get(cost_type, 30.0):
        return None
    return pct


# ======================================================================
# Parser texto plano: Composición de los costes
# ======================================================================

def _parse_composition_plain(text: str) -> dict:
    """
    Parser para texto plano de la tabla "Composición de los costes".

    Estrategia:
    - Localizar el encabezado de la sección.
    - Dentro de una ventana de 600 chars, buscar cada tipo de coste.
    - Para cada tipo: extraer porcentaje, EUR y máximo porcentaje.
    """
    result: dict = {}

    m_hdr = COMPOSITION_HEADER.search(text)
    if not m_hdr:
        return result

    window_start = m_hdr.start()
    window_end   = min(len(text), window_start + 700)
    window       = text[window_start:window_end]

    for pat, cost_type in _COMPOSITION_PATTERNS:
        m = pat.search(window)
        if not m:
            continue

        # Fragmento desde el final de la etiqueta hasta la siguiente etiqueta
        # de coste o 150 chars — lo que llegue antes (BL-COST-PARSER-FIX-1).
        # Sin este límite, si una fila no tiene valor numérico propio, el parser
        # captura el valor de la sección siguiente (ej: 20% de performance fee
        # asignado a transaction_cost_pct cuando Costes de operación está vacío).
        frag_raw = window[m.end(): min(len(window), m.end() + 150)]

        # Acotar al inicio de la siguiente etiqueta de coste
        _next_label_pos = len(frag_raw)
        for _other_pat, _other_type in _COMPOSITION_PATTERNS:
            if _other_type == cost_type:
                continue
            _nm = _other_pat.search(frag_raw)
            if _nm and _nm.start() < _next_label_pos:
                _next_label_pos = _nm.start()
        frag = frag_raw[:_next_label_pos]

        pct     = _extract_pct_from_cell(frag)
        max_pct = _extract_max_pct_from_cell(frag)
        eur     = _extract_eur_from_cell(frag)

        # Guard de rango (BL-COST-PARSER-FIX-2): guard ÚNICO compartido con la vía
        # DLA2 (_guarded_pct). Descarta valores fuera de dominio (cota superior por
        # tipo + cota inferior 0). Evita p.ej. que un performance fee (20%) caiga en
        # transaction_cost_pct, y que un coste negativo viole el CHECK '>= 0'.
        pct     = _guarded_pct(pct, cost_type)
        max_pct = _guarded_pct(max_pct, cost_type)

        if pct is not None:
            result[f"{cost_type}_{_COST_TYPE_SUFFIX.get(cost_type, 'fee')}_pct"] = pct
        if max_pct is not None:
            result[f"{cost_type}_{_COST_TYPE_SUFFIX.get(cost_type, 'fee')}_max_pct"] = max_pct
        if eur is not None:
            result[f"{cost_type}_{_COST_TYPE_SUFFIX.get(cost_type, 'fee')}_eur"] = eur

    return result


# ======================================================================
# API pública
# ======================================================================

def parse_costs_over_time(text: str) -> List[dict]:
    """
    Parsea la tabla "Costes a lo largo del tiempo" / "Costs over time".

    Detecta automáticamente si el texto está en formato DLA2 (contiene '|||')
    o en texto plano. Aplica el parser adecuado.

    Args:
        text: texto extraído del KID (Raw_KIID_Text + DLA2_Table_Text, o solo uno).

    Returns:
        Lista de dicts, uno por horizonte temporal encontrado:
            {
                'horizon_label':  str,          # "1 año", "5 años", "período recomendado"
                'horizon_years':  float,         # 1.0, 5.0, 0.25, -1.0 si RHP
                'total_cost_eur': float | None,
                'aci_pct':        float | None,  # ratio decimal (0.0012 = 0.12%)
                'is_rhp':         bool,
                'source':         str            # 'DLA2' o 'PLAIN_TEXT'
            }
        Retorna [] si la tabla no se encuentra o no se parsea correctamente.
    """
    if not text or not text.strip():
        return []

    try:
        if DLA2_SEPARATOR in text:
            results = _parse_costs_over_time_dla2(text)
            # Si DLA2 no produce resultados, intentar con texto plano como fallback.
            # NOTA (2026-06-25): FIX-P1-L (restringir el fallback a líneas de
            # rejilla) se revirtió: arreglaba funds con tabla en grid (p.ej.
            # LU0083138064) pero regresaba ~230 funds cuya tabla real está en la
            # prosa de Raw_KIID_Text (p.ej. LU0289472085). El driver dominante de
            # ACI_RHP-missing es la NO-detección de la columna RHP en tablas
            # multi-columna (is_rhp queda False cuando la cabecera de columna no
            # repite "período de mantenimiento recomendado"), un fix aparte.
            # FIX-P1-V: also fall back when DLA2 parsed a header but extracted
            # no ACI values at all (e.g. risk-section content follows the OT
            # header in the DLA2 serialization, yielding a false-positive header
            # hit with aci_pct=None for all entries). In that case, try plain
            # text which may find ACI in the raw prose. Only replace if plain
            # text yields at least one entry with a non-None aci_pct — prevents
            # regressing funds where plain text also extracts nothing.
            if not results or all(e.get('aci_pct') is None for e in results):
                _plain = _parse_costs_over_time_plain(text)
                if _plain and any(e.get('aci_pct') is not None for e in _plain):
                    results = _plain
            return results
        else:
            return _parse_costs_over_time_plain(text)
    except Exception:
        return []


def parse_costs_composition(text: str) -> dict:
    """
    Parsea la tabla "Composición de los costes" / "Composition of costs".

    Detecta automáticamente el formato (DLA2 vs. texto plano).

    Args:
        text: texto extraído del KID.

    Returns:
        Dict con las claves presentes (claves ausentes = no extraídas):
            entry_fee_pct:        float   (ratio decimal, ej: 0.05 = 5%)
            entry_fee_eur:        float
            entry_fee_max_pct:    float   ("hasta X%")
            exit_fee_pct:         float
            exit_fee_eur:         float
            exit_fee_max_pct:     float
            management_fee_pct:   float
            management_fee_eur:   float
            transaction_cost_pct: float
            transaction_cost_eur: float
            performance_fee_pct:  float
            performance_fee_eur:  float
        Retorna {} si la tabla no se encuentra.
    """
    if not text or not text.strip():
        return {}

    try:
        if DLA2_SEPARATOR in text:
            result = _parse_composition_dla2(text)
            if not result:
                result = _parse_composition_plain(text)
            return result
        else:
            return _parse_composition_plain(text)
    except Exception:
        return {}
