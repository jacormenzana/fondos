# proyecto1/core/priips_cost_extractor.py
# -*- coding: utf-8 -*-
"""
priips_cost_extractor.py — extractor de costes de KID PRIIPs.

BL-COST-4a (Sprint 2 S2-B): módulo nuevo.

Orquesta los módulos S2-A (cost_format_router, cost_table_parser, cost_cross_validator)
para extraer los campos de coste de un KID PRIIPs y devolver un dict listo para
upsert en fund_master + fund_cost_schedule.

NO reimplementa parsing de tablas, cross-validation ni detección de formato.
Su única lógica propia es:
  - Resolución de RHP numérico desde texto (_extract_rhp_years)
  - Conversión de escala ratio → % entero (_ratio_to_pct)
  - Ensamblado del dict de retorno
  - Construcción de _cost_schedule_rows (_build_schedule_rows)
  - Cálculo de Cost_Extraction_Quality (_assess_quality)
  - Heurística OC/ACI mismatch (_detect_oc_aci_mismatch)

Kill-switch: PRIIPS_COST_EXTRACTION_ENABLED = False (config v19.1).
  Si False → extract_priips_costs retorna {} inmediatamente.

Reglas de robustez (Principio #1, DRY #2):
  R-5: word boundary \\b en todo patrón regex nuevo.
  R-6: ventanas acotadas y lazy en todo patrón nuevo.
  R-8: AST validation tras cada escritura.
  Sin efectos secundarios en import.
  Ninguna excepción sale al caller.
"""

import re
import unicodedata
import logging
from typing import Optional, List, Dict, Any

# ---------------------------------------------------------------------------
# Dependencias S2-A
# ---------------------------------------------------------------------------
from cost_format_router   import detect_kid_format, detect_kid_currency
from cost_table_parser    import (
    parse_costs_over_time,
    parse_costs_composition,
    ACI_LABEL_ANCHOR,
    ACI_ROW_TAIL,
    ACI_FOOTNOTE_STOP,
    ACI_RETURN_PROJECTION,
    COMPOSITION_VALUE_LEADIN,
    COMPOSITION_DESC_TRANSACTION,
    COMPOSITION_DESC_MANAGEMENT,
    PERFORMANCE_FEE_NEGATION,
    SWITCHING_FEE_CONTEXT,
    ENTRY_FEE_VALUE,
    ENTRY_FEE_NEGATION,
    EXIT_FEE_NEGATION,
    parse_investment_base,
)
from cost_cross_validator import validate_pct_eur, ValidationResult

# ---------------------------------------------------------------------------
# Config con fallback aislado (mismo patrón que cost_cross_validator.py)
# Los 3 símbolos existen en config v19.1 (producción). El fallback solo actúa
# en entornos aislados (tests sin config en path).
# ---------------------------------------------------------------------------
try:
    from config import (
        PRIIPS_INVESTMENT_BASE,
        COST_CROSS_VALIDATION_TOLERANCE_PCT,
        PRIIPS_COST_EXTRACTION_ENABLED,
    )
except ImportError:
    PRIIPS_INVESTMENT_BASE: float          = 10000.0
    COST_CROSS_VALIDATION_TOLERANCE_PCT: float = 0.0005   # = config v19.1
    PRIIPS_COST_EXTRACTION_ENABLED: bool   = False         # = config v19.1

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes de módulo
# ---------------------------------------------------------------------------

# Source único para filas de schedule (P-7).
# Todas las filas de _cost_schedule_rows proceden de parse_costs_over_time.
_SCHEDULE_SOURCE = 'PRIIPS_COSTS_OVER_TIME'

# Valores permitidos en la columna Source de fund_cost_schedule (P-7).
# Refleja el CHECK del schema (3 valores), NO el tuple de config (5 valores).
# ⚠ NO usar 'PRIIPS_COMPOSITION' ni 'PRIIPS_TEXT' como Source de filas de schedule:
#   el CHECK del schema los rechaza → IntegrityError en upsert_cost_schedule.
_SCHEDULE_SOURCE_ALLOWED = ('PRIIPS_COSTS_OVER_TIME', 'UCITS_DERIVED', 'MANUAL')

# Patrón RHP numérico (R-5 word boundary, R-6 ventana lazy acotada).
# Captura el número y la unidad tras "período de mantenimiento recomendado: X años/meses"
# y la variante inglesa. Busca en una ventana de 60 chars tras la etiqueta.
_RHP_VALUE_PATTERN = re.compile(
    r'(?:per[ií]odo\s+de\s+mantenimiento\s+recomendado|recommended\s+holding\s+period)'
    r'\s*[:\-]?\s*'
    r'(\d+)\s*(a[ñn]os?|years?|mes(?:es)?|months?)\b',
    re.IGNORECASE,
)

# Umbral de discrepancia grave — espejo de cost_cross_validator._SEVERE_DISCREPANCY_THRESHOLD
_SEVERE_DISCREPANCY_THRESHOLD = 0.005   # 50bp en ratio decimal

# Umbrales heurística OC/ACI mismatch (P-3), todos en ratio decimal
_OC_ACI_NEAR_PP = 0.0010   # 0.10pp: el valor en BD "se parece" al ACI_RHP
_OC_TER_FAR_PP  = 0.0030   # 0.30pp: el valor en BD difiere del TER reconstruido

# FIX-OC-BIND (2026-08-24): tolerancia única de la guarda de ligadura del gasto
# corriente, en ratio decimal. 0.0006 = 0.06pp, por encima del redondeo que el
# propio KID publica (2 decimales en porcentaje) y muy por debajo de la menor
# separación real observada entre gasto corriente y comisión de gestión.
_OC_BIND_TOL = 0.0006

# FIX-SCHEDULE-PCT-GATE (2026-08-24): techo de impacto ANUAL implícito admisible
# para un coste total acumulado del schedule, en porcentaje entero. Espejo del
# techo que el modelo aplica a las columnas ACI_1Y / ACI_RHP.
_SCHEDULE_ANNUAL_CEILING_PCT = 25.0

# FIX-SCHEDULE-RECONCILE-TOL (2026-08-24): margen, en puntos porcentuales, dentro
# del cual "Costes totales" (derivado del importe) e "Incidencia anual" (leído de
# su celda %) se consideran CONCILIABLES y no se toca ninguno de los dos.
#
# El KID publica ambas cifras y no son idénticas ni tienen por qué serlo: en
# FR0010135103 la tabla dice literalmente "Costes totales 612 EUR" (6,12 %) e
# "Incidencia anual 6,21 %". Esa holgura es del documento, no un defecto.
#
# Distribución medida sobre las 2.030 filas de 1 año del universo activo:
#     <=0,05pp  1.608 | 0,05-0,15  192 | 0,15-0,5  35 | 0,5-1,5  13 | >1,5  182
# La separación es bimodal y el corte natural cae en 1,5pp: por debajo hay
# redondeo del KID; por encima, lecturas de otra columna o de otra divisa
# (proporciones de 0,01x o 100x). El umbral previo de 0,05pp trataba el
# redondeo ordinario como defecto y sobrescribía 227 impactos anuales correctos.
_SCHEDULE_RECONCILE_TOL_PP = 1.5


# ===========================================================================
# Funciones privadas
# ===========================================================================

def _ratio_to_pct(x: Optional[float]) -> Optional[float]:
    """
    Convierte ratio decimal a porcentaje entero.
    Ejemplo: 0.0525 → 5.25.
    None → None. (P-4)
    """
    if x is None:
        return None
    return round(x * 100.0, 4)


def _pct_is_plausible(total_pct: Optional[float], horizon_years: Optional[float]) -> bool:
    """
    ¿Es plausible un coste TOTAL acumulado de `total_pct` % a `horizon_years` años?

    Se normaliza a impacto ANUAL implícito (total / años) y se compara contra el
    mismo techo que acota las columnas ACI del modelo (25 %). Un fondo real no
    cuesta más de eso al año; por encima, el número procede de otra divisa, de
    otra base de inversión o de un sangrado de escenario. (FIX-SCHEDULE-PCT-GATE)

    Puro y acotado: sin None → False conservador. (P-5)
    """
    if total_pct is None or horizon_years is None:
        return False
    if total_pct < 0:
        return False
    return (total_pct / max(horizon_years, 1.0)) <= _SCHEDULE_ANNUAL_CEILING_PCT


def _extract_rhp_years(text: str) -> Optional[float]:
    """
    Resuelve el RHP (Período de Mantenimiento Recomendado) numérico desde texto.
    Años: devuelve el valor directamente.
    Meses: convierte a años con round(meses/12, 4).
    Retorna None si no se halla. (P-1, P-5)
    """
    m = _RHP_VALUE_PATTERN.search(text)
    if not m:
        return None
    value = float(m.group(1))
    unit  = m.group(2).lower()
    if 'mes' in unit or 'month' in unit:
        return round(value / 12.0, 4)
    return float(value)


def _pick_aci_for_horizon(
    rows: List[dict],
    target_years: Optional[float],
    want_rhp: bool,
) -> Optional[float]:
    """
    Selecciona el aci_pct (ratio decimal) de la fila que coincide con el horizonte pedido.
    Si want_rhp=True, prioriza la fila con is_rhp=True; si no existe, busca por target_years.
    Tolerancia de match de horizonte: ±0.01 años.
    Retorna None si no se encuentra.
    """
    if not rows:
        return None
    # Prioridad: fila RHP si se pide.
    # FIX-ACI-RETURN-GUARD (2026-08-19): only honour an is_rhp row that carries a
    # real aci_pct. Some layouts emit an is_rhp row with aci_pct=None (the RHP
    # column collapsed, or the RHP reference was a footnote); such a row must NOT
    # shadow the genuine RHP value reachable by the numeric-horizon match below.
    if want_rhp:
        for r in rows:
            if r.get('is_rhp') and r.get('aci_pct') is not None:
                return r.get('aci_pct')
    # Búsqueda por horizonte numérico
    if target_years is not None:
        for r in rows:
            hy = r.get('horizon_years')
            if hy is not None and hy >= 0 and abs(hy - target_years) <= 0.01:
                return r.get('aci_pct')
    return None


def _pick_eur_for_horizon(
    rows: List[dict],
    target_years: Optional[float],
    want_rhp: bool,
) -> Optional[float]:
    """
    Igual que _pick_aci_for_horizon pero devuelve total_cost_eur.
    """
    if not rows:
        return None
    if want_rhp:
        for r in rows:
            if r.get('is_rhp'):
                return r.get('total_cost_eur')
    if target_years is not None:
        for r in rows:
            hy = r.get('horizon_years')
            if hy is not None and hy >= 0 and abs(hy - target_years) <= 0.01:
                return r.get('total_cost_eur')
    return None


def _norm_existing_oc(existing_oc: Optional[float]) -> Optional[float]:
    """
    Normaliza el OC legacy a ratio decimal para comparación interna.
    Heurística de escala (espejo inverso de cost_format_signals.py:291 oc_pct):
      - Si existing_oc >= 0.5 → se asume que está en % → dividir entre 100.
      - Si existing_oc < 0.5  → se asume que ya es ratio.
    # DRY-SYNC: cost_format_signals.py:291 (oc_pct)
    """
    if existing_oc is None:
        return None
    if existing_oc >= 0.5:
        return existing_oc / 100.0
    return existing_oc


def _resolve_oc_binding(
    oc_norm: Optional[float],
    mgmt: Optional[float],
    aci_rhp_ratio: Optional[float],
    aci_1y_ratio: Optional[float],
) -> Optional[str]:
    """
    ¿A qué fila quedó ligado el gasto corriente heredado? (FIX-OC-BIND)

    Devuelve la señal que delata la mala ligadura, o None si el valor es
    coherente y no debe tocarse. Todos los argumentos en RATIO decimal.

    Dos señales, una sola guarda (P#11):
      'ACI'                           el valor heredado es el ACI, no el TER.
      'componente menor que la gestión'  el valor heredado está por debajo de la
                                      comisión de gestión que el gasto corriente
                                      CONTIENE (UCITS OCF) — típicamente la fila
                                      de operación o la de salida.

    La segunda señal exige que el ACI publicado respalde a la gestión: un
    incumplimiento del invariante no dice por sí solo qué lado está mal, y sin
    respaldo se prefiere no tocar nada (control de método #4).

    Puro y acotado; ante cualquier None → None conservador. (P-5)
    """
    if oc_norm is None or mgmt is None:
        return None
    if abs(oc_norm - mgmt) < _OC_BIND_TOL:
        return None

    if any(
        a is not None and abs(oc_norm - a) < _OC_BIND_TOL
        for a in (aci_rhp_ratio, aci_1y_ratio)
    ):
        return 'ACI'

    _corroboration = max(
        (a for a in (aci_rhp_ratio, aci_1y_ratio) if a is not None),
        default=None,
    )
    if (
        oc_norm < mgmt - _OC_BIND_TOL
        and _corroboration is not None
        and _corroboration >= mgmt - _OC_BIND_TOL
    ):
        return 'componente menor que la gestión'
    return None


def _detect_oc_aci_mismatch(
    existing_oc: Optional[float],
    oc_norm: Optional[float],
    ter_recon_ratio: Optional[float],
    aci_rhp_ratio: Optional[float],
) -> bool:
    """
    Devuelve True si el OC legacy en BD parece ser ACI (no TER):
    está cerca del ACI_RHP y lejos del TER reconstruido.
    Todos los argumentos comparables en RATIO decimal.
    Conservador: ante cualquier None → False. (P-3)
    """
    if existing_oc is None or oc_norm is None:
        return False
    if ter_recon_ratio is None or aci_rhp_ratio is None:
        return False
    near_aci = abs(oc_norm - aci_rhp_ratio) <= _OC_ACI_NEAR_PP   # se parece al ACI
    far_ter  = abs(oc_norm - ter_recon_ratio) >  _OC_TER_FAR_PP  # difiere del TER
    return near_aci and far_ter


# Ventana acotada tras la etiqueta ACI (R-6), distinta por pasada:
#   - Pasada 1 (firma "X% Y% cada año"): 2000 chars. Cubre el peor caso real
#     medido — tabla partida entre páginas con cabecera, logo, título y la
#     sección de riesgo completa interpuestos (GVC Gaesco: la fila de % queda a
#     ~1.400 chars de su etiqueta). El sufijo "cada año" identifica la fila de
#     forma positiva, así que la distancia no introduce ambigüedad.
#   - Pasada 2 (porcentajes positivos iniciales): 250 chars. No hay sufijo que
#     confirme la fila, luego solo es fiable cuando el valor sigue de inmediato a
#     la etiqueta ("Impacto del coste anual (*)\n1,0%"). Ventanas amplias en esta
#     pasada capturaban cifras de tablas vecinas ("665 EUR 6.65%").
_ACI_ANCHOR_WINDOW = 2000
_ACI_ANCHOR_WINDOW_ADJACENT = 250

# Tolerancia de no-op: por debajo de esto el valor almacenado y el del ancla son
# el mismo número y no hay nada que reescribir.
#
# NOTA (2026-08-23): aquí vivía `_ACI_CORRECTION_MIN_DELTA_PP = 3.0`, un umbral
# de MAGNITUD que decidía si corregir. Se eliminó: medido sobre el corpus, dejaba
# sin corregir 74 sangrados reales (p. ej. LU0119197159 — etiqueta "4.6% 2.0%
# cada año", almacenado 4.90 = proyección) y aun así corrompía 2 fondos. La
# distancia entre dos números no dice cuál de ellos es el coste; lo dice la
# EVIDENCIA de si la etiqueta respalda el valor (_label_vouches_for).
_ACI_NOOP_TOLERANCE_PP = 0.02

# Techo de ACI plausible en % entero. Un impacto de coste anual > 15% no existe
# en un UCITS; por encima es sangrado de escenario/rentabilidad.
_ACI_ANCHOR_MAX_PCT = 15.0

# Token de porcentaje con signo capturado por separado: el signo es el
# discriminante entre coste (positivo) y rendimiento de escenario (negativo).
_ACI_PCT_TOKEN = re.compile(r'(-)?(\d{1,3}(?:[.,]\d{1,2})?)\s*%')

# Techo de plausibilidad para una fila de composición (gestión / operación).
# El CHECK del schema es 5.0 para Transaction_Cost_Pct; 25 es el techo general.
_MAX_COMPOSITION_PCT = 25.0

# Mecánica de comisión de rendimiento: si el KID la describe, la comisión existe
# aunque su % coincida con otro componente. Si NO la describe y el % duplica otra
# fila, el número pertenece a esa otra fila (FIX-PERF-FEE-MISBIND).
_PERF_FEE_MECHANICS = re.compile(
    r'rentabilidad\s+superior'
    r'|supera\s+la\s+del\s+(?:indicador|[íi]ndice)'
    r'|por\s+encima\s+del?\s+(?:activo|[íi]ndice|indicador)\s+de\s+referencia'
    r'|marca\s+de\s+agua|high\s*water\s*mark|hurdle'
    r'|outperform|excess\s+return|above\s+the\s+(?:benchmark|reference)'
    r'|comisi[oó]n\s+de\s+[eé]xito',
    re.IGNORECASE,
)


def _pct_token_to_float(token: str) -> float:
    """'2,74' | '2.74' → 2.74 (coma decimal europea o punto anglosajón)."""
    return float(token.replace(',', '.'))


def _recover_aci_from_label(text: str) -> tuple:
    """
    FIX-ACI-LABEL-ANCHOR — recupera (ACI_1Y, ACI_RHP) en % entero anclando en la
    ETIQUETA PRIIPs del impacto de coste anual, no en la proximidad al titular
    de la sección de costes.

    Motivo (auditoría 2026-08-23): parse_costs_over_time liga los porcentajes por
    cercanía al titular "Costes a lo largo del tiempo".  En numerosas maquetas la
    tabla de ESCENARIOS DE RENTABILIDAD queda interpuesta en el flujo de texto de
    pdfplumber, de modo que el % ligado es el rendimiento del escenario de tensión
    (siempre negativo) o la proyección de rentabilidad de la nota al pie.  Ejemplos
    verificados: ES0175404013 (-77,24% escenario vs 1,0% real), ES0179692001
    (-94,7% vs 2,5%), LU2092974778 (23,28% proyección vs 1,42% real).

    Dos maquetas cubiertas:
      A. Sangrado de tabla adyacente — las filas de escenario preceden a la de
         coste (Dunas, Capital Group, Echiquier, BNY Mellon).
      B. Tabla partida entre páginas — cabecera y fila en EUR cierran la página N,
         la fila de % abre la N+1 con mobiliario de página intercalado (GVC Gaesco).

    Orden de resolución — ADYACENCIA PRIMERO, por cada etiqueta en orden de
    documento (FIX-ACI-ANCHOR-PASSORDER, 2026-08-23):
      1. Porcentajes POSITIVOS inmediatamente tras la etiqueta (ventana corta),
         cortando en el primer % negativo (ahí arranca la tabla de escenarios) o
         en la nota al pie.
      2. Si la etiqueta no tiene valor adyacente, firma "X% Y% cada año /
         each year" en la ventana ancha — única vía para la maqueta partida
         entre páginas, donde el valor queda a ~1.400 chars de su etiqueta.

    ⚠ El orden importa. La versión previa ejecutaba la pasada 1 (firma) sobre
    TODAS las etiquetas antes de mirar la adyacencia, de modo que una firma
    lejana podía ganar a un valor pegado a su propia etiqueta. Con el sufijo
    "al año" entonces admitido, 7 fondos Polar Capital devolvían 10.00 (línea de
    comisión "10,00% al año") teniendo su ACI real adyacente. Resolver por
    etiqueta y priorizar la adyacencia elimina la clase de fallo entera.

    Devuelve (None, None) si no hay señal. Nunca lanza. (P-3)
    """
    for m in ACI_LABEL_ANCHOR.finditer(text):
        # --- 1. Valor adyacente a ESTA etiqueta -----------------------------
        adjacent = text[m.end(): m.end() + _ACI_ANCHOR_WINDOW_ADJACENT]
        stop = ACI_FOOTNOTE_STOP.search(adjacent)
        head = adjacent[:stop.start()] if stop else adjacent
        values: List[float] = []
        for pct in _ACI_PCT_TOKEN.finditer(head):
            if pct.group(1) is not None:
                break                          # % negativo → tabla de escenarios
            value = _pct_token_to_float(pct.group(2))
            if not (0 < value <= _ACI_ANCHOR_MAX_PCT):
                break
            values.append(value)
            if len(values) == 2:
                break
        if values:
            return (values[0], values[0]) if len(values) == 1 else (values[0], values[1])

        # --- 2. Firma de fila en la ventana ancha (tabla partida) -----------
        window = text[m.end(): m.end() + _ACI_ANCHOR_WINDOW]
        for tail in ACI_ROW_TAIL.finditer(window):
            if tail.group(1).startswith('-'):
                continue                       # escenario, no coste
            first = _pct_token_to_float(tail.group(1))
            if not (0 < first <= _ACI_ANCHOR_MAX_PCT):
                continue
            second = None
            if tail.group(2) is not None and not tail.group(2).startswith('-'):
                _cand = _pct_token_to_float(tail.group(2))
                if 0 < _cand <= _ACI_ANCHOR_MAX_PCT:
                    second = _cand
            # Una sola columna → el mismo valor rige 1Y y RHP (regla PRIIPS).
            return (first, first) if second is None else (first, second)

    return None, None


def _label_vouched_values(text: str) -> set:
    """
    FIX-ACI-EVIDENCE — conjunto de porcentajes que la ETIQUETA ACI respalda:
    los positivos adyacentes a cada etiqueta más ambas capturas de cada firma
    "X% Y% cada año" de la ventana ancha.

    Sirve para decidir si un ACI ya publicado procede de la tabla de costes o de
    la nota al pie: si el valor almacenado figura aquí, ES un ACI de la tabla y
    su coincidencia con la proyección es fortuita; si no figura, procede de la
    nota. Sustituye al umbral de delta, que era un proxy de magnitud y dejaba
    escapar 74 sangrados reales por debajo de 3pp.

    Nunca lanza. (P-3)
    """
    vouched = set()
    for m in ACI_LABEL_ANCHOR.finditer(text):
        adjacent = text[m.end(): m.end() + _ACI_ANCHOR_WINDOW_ADJACENT]
        stop = ACI_FOOTNOTE_STOP.search(adjacent)
        head = adjacent[:stop.start()] if stop else adjacent
        # Máximo DOS valores, igual que _recover_aci_from_label: una fila ACI de
        # PRIIPs tiene a lo sumo dos columnas (1Y y RHP). Sin este tope la
        # ventana seguía más allá de la fila y avalaba números de la nota al pie
        # —incluida la propia proyección de rentabilidad—, con lo que el valor
        # espurio quedaba "respaldado por la etiqueta" y la corrección no se
        # disparaba (LU0106831901: avalaba 14,8 además de 7,6 y 4,1).
        _taken = 0
        for pct in _ACI_PCT_TOKEN.finditer(head):
            if pct.group(1) is not None:
                break
            value = _pct_token_to_float(pct.group(2))
            if not (0 < value <= _ACI_ANCHOR_MAX_PCT):
                break
            vouched.add(round(value, 2))
            _taken += 1
            if _taken == 2:
                break

        window = text[m.end(): m.end() + _ACI_ANCHOR_WINDOW]
        for tail in ACI_ROW_TAIL.finditer(window):
            for group in (tail.group(1), tail.group(2)):
                if group and not group.startswith('-'):
                    value = _pct_token_to_float(group)
                    if 0 < value <= _ACI_ANCHOR_MAX_PCT:
                        vouched.add(round(value, 2))
    return vouched


def _recover_composition_from_description(text: str) -> tuple:
    """
    FIX-COMPOSITION-BY-DESCRIPTION — devuelve (gestión %, operación %) ligando
    cada valor por su DESCRIPCIÓN normativa PRIIPs, no por la etiqueta de fila
    que lo precede en el flujo de texto.

    En las maquetas de columna partida el % de operación queda bajo la etiqueta
    de gestión y el valor de gestión aparece huérfano mucho más abajo; ambos
    comparten la entradilla "X% del valor de su inversión al año", de modo que
    solo la descripción los distingue:
      · "…costes en que incurrimos al comprar y vender…"  → operación
      · "…estimación basada en los costes reales del último año" → gestión

    Ventana acotada de 260 chars tras el valor (R-6). Se queda con la PRIMERA
    aparición de cada tipo. Devuelve (None, None) si no hay evidencia. No lanza.
    """
    mgmt: Optional[float] = None
    tran: Optional[float] = None
    for m in COMPOSITION_VALUE_LEADIN.finditer(text):
        window = text[m.end(): m.end() + 260]
        try:
            value = _pct_token_to_float(m.group(1))
        except ValueError:
            continue
        if not (0 < value <= _MAX_COMPOSITION_PCT):
            continue
        if COMPOSITION_DESC_TRANSACTION.search(window):
            if tran is None:
                tran = value
        elif COMPOSITION_DESC_MANAGEMENT.search(window):
            if mgmt is None:
                mgmt = value
    return mgmt, tran


def _label_vouches_for(text: str, aci_pct: Optional[float]) -> bool:
    """True si la etiqueta ACI respalda `aci_pct` (% entero). None → False."""
    if aci_pct is None:
        return False
    return any(abs(v - aci_pct) < 0.02 for v in _label_vouched_values(text))


def _matches_return_projection(text: str, aci_pct: Optional[float]) -> bool:
    """
    True si `aci_pct` (% entero) coincide con la proyección de rentabilidad de la
    nota al pie ("…será del 13,10% antes de deducir los costes").  Ese número es
    un RENDIMIENTO, no un coste: la coincidencia prueba que el valor extraído
    procede de la nota y es espurio (raíz ACT-06 RC-1).  Conservador: None → False.
    """
    if aci_pct is None:
        return False
    for m in ACI_RETURN_PROJECTION.finditer(text):
        # El patrón tiene una rama por redacción; solo una captura por match.
        for token in m.groups():
            if not token:
                continue
            try:
                if abs(_pct_token_to_float(token) - aci_pct) < 0.02:
                    return True
            except ValueError:                 # token no numérico → ignorar
                continue
    return False


def _build_schedule_rows(
    rows: List[dict],
    rhp_years: Optional[float],
    isin: str,
    text: Optional[str] = None,
    anchor_1y: Optional[float] = None,
) -> List[dict]:
    """
    Construye _cost_schedule_rows a partir de parse_costs_over_time, resolviendo:
      - RHP a valor numérico (P-1, Decisión B).
      - Fusión de colisiones de PK (mismo Horizon_Years) marcando Is_RHP=1.
      - Conversión de escala: aci_pct ratio → Annual_Impact_Pct % entero (P-4).
      - Validación defensiva de Source (P-7).

    `text` (opcional) permite resolver la base de inversión real del KID en vez
    de asumir la estándar de 10.000 (FIX-SCHEDULE-PCT-BASE).
    `anchor_1y` (opcional, % entero) es el ACI de 1 año recuperado del ancla de
    ETIQUETA; cuando existe, es evidencia y gana al total derivado por posición
    (FIX-SCHEDULE-ANNUAL-ANCHOR).
    """
    # FIX-SCHEDULE-PCT-BASE (2026-08-24): la base de ejemplo NO siempre es 10.000.
    # parse_investment_base() ya resuelve este dato y su propia docstring advierte
    # que "los llamadores NO deben recurrir a una base fija"; esta función era
    # justamente ese llamador. Se reutiliza en vez de duplicar lógica (P#11 / R-1).
    # Cuando el KID no declara base, se conserva la estándar PRIIPs: es el
    # comportamiento previo y cubre 168 de los 214 fondos auditados.
    _base = parse_investment_base(text) if text else None
    if _base is None:
        _base = PRIIPS_INVESTMENT_BASE

    by_horizon: Dict[float, dict] = {}

    for r in rows:
        hy  = r.get('horizon_years')
        rhp = bool(r.get('is_rhp'))

        # Resolver RHP a su valor numérico (P-1, Decisión B)
        if rhp or hy == -1.0:
            if rhp_years is None:
                _log.info(
                    "[BL-COST-4a] %s: fila RHP sin Cost_RHP_Years resuelto; fila descartada",
                    isin,
                )
                continue
            hy = rhp_years

        # Filtrar horizontes no válidos para el CHECK de schema: 0 < hy <= 50
        if hy is None or not (0 < hy <= 50):
            continue

        eur = r.get('total_cost_eur')
        aci = r.get('aci_pct')   # ratio decimal

        row: dict = {
            'Horizon_Years': round(hy, 4),
            'Is_RHP':        1 if (rhp or (rhp_years is not None and abs(hy - rhp_years) <= 0.01)) else 0,
            'Source':        _SCHEDULE_SOURCE,
        }
        if eur is not None:
            row['Total_Costs_EUR'] = eur
        # FIX-SCHEDULE-ANNUAL-BLEED (2026-08-24): el MISMO dato — la incidencia
        # anual de los costes — llega a tres destinos y sólo dos estaban
        # protegidos. `fund_master.ACI_1Y/ACI_RHP` pasan por el ancla de etiqueta
        # con techo _ACI_ANCHOR_MAX_PCT, y la fila RHP hereda ese valor ya
        # saneado; pero esta fila se escribía con el `aci_pct` POSICIONAL de
        # parse_costs_over_time, sin techo alguno.
        #
        # Consecuencia medida (auditoría de distribución, 43 filas activas):
        # todas con Horizon_Years=1 e Is_RHP=0, ninguna coincidente con el ACI
        # publicado del propio fondo, y ningún fondo con ACI_1Y/ACI_RHP > 25 %.
        # Son las rentabilidades del escenario de tensión con el signo comido —
        # el mismo defecto documentado el 2026-08-23, corregido entonces en
        # `fund_master` y NO en `fund_cost_schedule`:
        #   LU1006075656  fila 1a = 48,63 %  frente a ACI_1Y publicado 1,9 %
        #   ES0175404013  fila 1a = 77,24 %  frente a ACI_1Y publicado 1,0 %
        #
        # Se aplica el MISMO techo que gobierna el dato en su otro destino. Si el
        # ancla de etiqueta existe para este horizonte, gana (evidencia sobre
        # magnitud); si no, se omite la clave — NULL = "no descubierto" (P#10),
        # nunca una rentabilidad de escenario publicada como coste.
        if aci is not None:
            _ann_pct = _ratio_to_pct(aci)
            if _ann_pct is not None and _ann_pct > _ACI_ANCHOR_MAX_PCT:
                _is_1y = abs(hy - 1.0) <= 0.01
                if _is_1y and anchor_1y is not None and anchor_1y <= _ACI_ANCHOR_MAX_PCT:
                    _log.info(
                        "[FIX-SCHEDULE-ANNUAL-BLEED] %s: horizonte %.2fa anual "
                        "%.2f%% sustituido por el ACI anclado a etiqueta %.2f%%",
                        isin, hy, _ann_pct, anchor_1y,
                    )
                    row['Annual_Impact_Pct'] = anchor_1y
                else:
                    _log.info(
                        "[FIX-SCHEDULE-ANNUAL-BLEED] %s: horizonte %.2fa anual "
                        "%.2f%% omitido (supera el techo %.1f%% del ACI; sin "
                        "ancla de etiqueta para este horizonte)",
                        isin, hy, _ann_pct, _ACI_ANCHOR_MAX_PCT,
                    )
            else:
                row['Annual_Impact_Pct'] = _ann_pct
        # Total_Costs_Pct: EUR acumulado / base en % entero (coherencia con columnas nuevas P-4)
        #
        # FIX-SCHEDULE-PCT-GATE (2026-08-24): este valor es DERIVADO, no leído del
        # KID — siempre es Total_Costs_EUR/base. Por eso la comprobación de
        # coherencia EUR↔Pct no detecta nada: es una tautología, no una validación.
        # El lado fiable es Annual_Impact_Pct, que sí está ligado a su etiqueta
        # ("Incidencia anual de los costes"): en 247 de las 290 filas absurdas
        # auditadas el anual era plausible y solo el total estaba mal.
        # Cuando el importe en EUR es de otra divisa o es un número sangrado, el
        # porcentaje derivado se dispara (LU0115096736: 1.532 % a 1 año, importe en
        # JPY sobre base 2.000.000). Publicar eso es peor que no publicarlo: se
        # omite la clave y la columna queda NULL = "no descubierto" (P#10).
        _derived_pct = None
        if eur is not None:
            _derived_pct = _ratio_to_pct(eur / _base)
            if _pct_is_plausible(_derived_pct, hy):
                row['Total_Costs_Pct'] = _derived_pct
            else:
                _log.info(
                    "[FIX-SCHEDULE-PCT-GATE] %s: horizonte %.2fa Total_Costs_Pct "
                    "%.2f%% omitido (anual implícito %.2f%% supera lo plausible; "
                    "base=%s, importe=%s)",
                    isin, hy, _derived_pct,
                    _derived_pct / max(hy, 1.0), _base, eur,
                )
                _derived_pct = None

        # FIX-SCHEDULE-ANNUAL-GT-TOTAL (2026-08-23): invariante estructural — el
        # impacto ANUAL nunca puede superar el coste TOTAL acumulado del mismo
        # horizonte; a 1 año son iguales por definición y a horizontes mayores el
        # total acumula. Cuando se incumple, el % anual es sangrado (escenario o
        # proyección) mientras el importe en EUR —y por tanto Total_Costs_Pct— es
        # correcto. Auditoría del schedule (nunca antes revisado): 394 filas,
        # p. ej. ES0179692001 a 1 año con anual 94,7 % frente a 531 EUR = 5,31 %.
        # Las reparaciones previas solo tocaban la fila Is_RHP=1; las demás
        # conservaban el valor falso.
        # A 1 año se corrige al valor derivado del EUR (equivalencia exacta); en
        # horizontes mayores no es derivable sin el detalle del KID → se descarta,
        # que es mejor que publicar un coste falso.
        # ⚠ Solo se actúa si el TOTAL es creíble. Cuando Total_Costs_EUR es
        # absurdamente pequeño (1, 3, 6 EUR sobre una base de 10.000) el lado
        # equivocado es el total, no el anual: aplicar la regla a ciegas
        # DESCARTARÍA un impacto anual correcto. Umbral: 20 EUR = 0,2 % de la
        # base, por debajo del cual ningún fondo real declara su coste total.
        #
        # FIX-SCHEDULE-PCT-GATE (2026-08-24), segunda mitad: el total solo puede
        # arbitrar sobre el anual si el propio total es creíble. Antes bastaba con
        # eur >= 20, de modo que un total derivado de una base equivocada seguía
        # mandando: en LU0247697476 el anual correcto de 1 año (7,80 %, respaldado
        # por su ancla de etiqueta) fue sobrescrito con 3,40 % derivado de un
        # importe sangrado, y a horizontes > 1 año el anual correcto se DESCARTA.
        # Un incumplimiento del invariante no dice qué lado está mal (control de
        # método #4), así que se exige además que el total haya pasado la guarda
        # de plausibilidad de arriba.
        _ann = row.get('Annual_Impact_Pct')
        _tot = row.get('Total_Costs_Pct')
        _tot_credible = eur is not None and eur >= 20.0 and _derived_pct is not None
        # FIX-SCHEDULE-ANNUAL-ANCHOR (2026-08-24): si el anual de esta fila es el
        # que respalda el ancla de ETIQUETA del KID, no puede cederle la razón a un
        # total derivado por posición aunque ese total sea plausible en abstracto.
        # Caso medido (LU0247697476): ancla de etiqueta ACI_1Y = 7,80 %, importe de
        # 1 año leído como 340 EUR → total 3,40 %, también "plausible". La regla por
        # magnitud sobrescribía 7,80 → 3,40 y destruía el único valor con respaldo
        # documental. Se prefiere la evidencia a la magnitud (control de método #6).
        _ann_is_anchored = (
            _ann is not None and anchor_1y is not None
            and abs(hy - 1.0) <= 0.01
            and abs(_ann - anchor_1y) < 0.05
        )
        if (
            _ann_is_anchored and _tot is not None and _ann is not None
            and _ann > _tot + _SCHEDULE_RECONCILE_TOL_PP
        ):
            _log.info(
                "[FIX-SCHEDULE-ANNUAL-ANCHOR] %s: horizonte 1a se conserva el anual "
                "%.2f%% (respaldado por el ancla de etiqueta) y se omite el total "
                "derivado %.2f%%",
                isin, _ann, _tot,
            )
            row.pop('Total_Costs_Pct', None)
            _tot = None

        if (
            _ann is not None and _tot is not None and _tot_credible
            and _ann > _tot + _SCHEDULE_RECONCILE_TOL_PP
        ):
            if abs(hy - 1.0) <= 0.01:
                _log.info(
                    "[FIX-SCHEDULE-ANNUAL-GT-TOTAL] %s: horizonte 1a %.2f%%→%.2f%% "
                    "(anual > total acumulado; se usa el derivado de EUR)",
                    isin, _ann, _tot,
                )
                row['Annual_Impact_Pct'] = _tot
            else:
                _log.info(
                    "[FIX-SCHEDULE-ANNUAL-GT-TOTAL] %s: horizonte %.2fa anual "
                    "%.2f%% descartado (> total acumulado %.2f%%)",
                    isin, hy, _ann, _tot,
                )
                row.pop('Annual_Impact_Pct', None)

        # Fusión de colisión de PK (P-1): mismo Horizon_Years
        key = row['Horizon_Years']
        if key in by_horizon:
            prev = by_horizon[key]
            # Marcar Is_RHP=1 si alguna de las dos filas es RHP
            prev['Is_RHP'] = max(prev['Is_RHP'], row['Is_RHP'])
            # Completar campos vacíos con los de la fila nueva (sin sobreescribir)
            for k, v in row.items():
                prev.setdefault(k, v)
        else:
            by_horizon[key] = row

    # Validación defensiva de Source (P-7): descartar filas con Source no permitido
    valid_rows = [r for r in by_horizon.values() if r['Source'] in _SCHEDULE_SOURCE_ALLOWED]
    discarded  = len(by_horizon) - len(valid_rows)
    if discarded:
        _log.warning(
            "[BL-COST-4a] %s: %d fila(s) de schedule descartadas por Source no permitido",
            isin, discarded,
        )
    return valid_rows


def _assess_quality(
    vr_rhp: ValidationResult,
    vr_1y: ValidationResult,
    aci_rhp_final: Optional[float],
    aci_1y_final: Optional[float],
    comp: dict,
    over_time: List[dict],
    schedule_source_used: Optional[str],
) -> str:
    """
    Calcula Cost_Extraction_Quality según §3 del traspaso S2-B.
    Evaluado en orden; se asigna el PRIMER valor cuyo criterio se cumple.

    Valores posibles:
        'HIGH'         — ancla tiene % y EUR con cross-validation OK (≤5bp)
        'MEDIUM_CROSS' — ancla tiene % y EUR con discrepancia leve (5–50bp)
        'MEDIUM_EUR'   — ancla solo tiene EUR (PCT_ONLY → EUR_ONLY invertido)
        'MEDIUM_PCT'   — ancla solo tiene % 
        'LOW'          — discrepancia grave / solo texto plano / datos parciales
        'NONE'         — no se extrajo ningún dato de coste
    """
    has_over_time  = bool(over_time)
    has_comp       = bool(comp)

    # Regla 1: sin datos en absoluto
    if not has_over_time and not has_comp:
        return 'NONE'

    # Ancla en 1Y: única cross-val % vs EUR semánticamente válida (ACI anual vs EUR ~anual).
    # vr_rhp compara ACI anual contra EUR ACUMULADO → discrepancia espuria a RHP>1Y (BL-COST fix).
    anchor = vr_1y if vr_1y.status != 'NONE' else vr_rhp

    # Regla 3: HIGH — ambos datos, cross-validation OK
    if anchor.status == 'OK':
        return 'HIGH'

    # Regla 4: MEDIUM_CROSS — discrepancia leve (validated_pct no es None)
    if anchor.status == 'DISCREPANCY' and anchor.validated_pct is not None:
        return 'MEDIUM_CROSS'

    # Regla 5: MEDIUM_EUR — solo EUR disponible (ancla EUR_ONLY)
    if anchor.status == 'EUR_ONLY':
        return 'MEDIUM_EUR'

    # Regla 6: MEDIUM_PCT — solo % disponible (ancla PCT_ONLY)
    if anchor.status == 'PCT_ONLY':
        return 'MEDIUM_PCT'

    # Regla 7: LOW — discrepancia grave, texto plano sin ancla, datos parciales
    # (anchor.status == 'DISCREPANCY' con validated_pct=None, o NONE en ambos
    #  pero hay algún dato de composición, o única fuente fue PLAIN_TEXT)
    if has_over_time or has_comp:
        return 'LOW'

    return 'NONE'


# ===========================================================================
# API pública
# ===========================================================================

def extract_priips_costs(
    text: str,
    isin: str,
    existing_oc:    Optional[float] = None,    # Ongoing_Charge_Recurrent actual en BD (escala BD)
    existing_entry: Optional[float] = None,    # Entry_Fee_Pct actual en BD
    existing_exit:  Optional[float] = None,    # Exit_Fee_Pct actual en BD
) -> Dict[str, Any]:
    """
    Extrae los campos de coste de un KID PRIIPs a partir del texto concatenado
    (Raw_KIID_Text + DLA2_Table_Text). Orquesta los módulos S2-A; no reimplementa parsing.

    Contrato:
      - Respeta PRIIPS_COST_EXTRACTION_ENABLED (kill-switch). Si False → retorna {}.
      - Ninguna excepción sale al caller (try/except global → dict parcial + quality 'LOW').
      - Devuelve SOLO las claves extraídas con éxito (claves ausentes = no extraído),
        EXCEPTO Cost_Extraction_Quality, KID_Format y _cost_schedule_rows, que
        siempre están presentes.
      - Escala de salida: porcentaje entero para *_Pct / ACI_* (P-4). EUR absoluto sin convertir.

    Claves posibles del dict de retorno:
      KID_Format               str            (siempre)
      KID_Currency             str | None
      Cost_Extraction_Quality  str            (siempre; 'HIGH'|'MEDIUM_CROSS'|'MEDIUM_EUR'|
                                               'MEDIUM_PCT'|'LOW'|'NONE')
      Cost_RHP_Years           float | None
      Entry_Fee_Pct_Max        float | None   (% entero)
      Exit_Fee_Pct_Max         float | None   (% entero)
      Management_Fee_Pct       float | None   (% entero)
      Transaction_Cost_Pct     float | None   (% entero)
      Performance_Fee_Pct      float | None   (% entero)
      ACI_1Y                   float | None   (% entero)
      ACI_RHP                  float | None   (% entero)
      Ongoing_Charge_Recurrent float | None   (% entero; SOLO si existing_oc is None y TER OK)
      _cost_schedule_rows      List[dict]     (siempre; puede ser [])
      _oc_aci_mismatch         bool           (solo si se detecta mezcla TER/ACI con OC existente)

    Hallazgo §4.4: cross-validation % ↔ EUR solo es válida a horizonte 1 año
    (EUR acumulado ≈ ACI × base solo cuando horizonte = 1 año). Para RHP > 1 año,
    cruzar ACI_RHP con EUR_RHP daría discrepancia espuria. Por eso:
      - El ancla de cross-validation es el horizonte 1Y (vr_1y) cuando existe.
      - ACI_RHP se toma directamente del aci_pct de la fila RHP (ratio→%), salvo
        cuando rhp_years == 1.0, donde se usa el validated_pct de vr_rhp.
      - Para RHP < 1Y (ej. FR0000989626, 3m): el EUR acumulado / base ≈ ACI
        porque el periodo es corto; la cross-validation funciona como HIGH.

    Limitación conocida de texto plano (§4.1):
      En ruta PLAIN_TEXT, parse_costs_over_time devuelve filas con total_cost_eur
      y aci_pct idénticos para todas las columnas (bug de duplicación). La 2ª
      columna no es fiable. Con DLA2 activo, este comportamiento desaparece.
    """
    # --- 0. KILL-SWITCH (primera línea ejecutable) ---
    if not PRIIPS_COST_EXTRACTION_ENABLED:
        return {}

    out: Dict[str, Any] = {}

    try:
        # FIX-UNICODE-NFC (2026-08-23): normalizar a NFC antes de cualquier regex.
        # 144 KIDs del universo activo mezclan acentos PRECOMPUESTOS (U+00F3 "ó")
        # con DESCOMPUESTOS (o + U+0301 combinante) en el mismo documento, según
        # cómo generase el PDF cada gestora. Cualquier patrón que contenga una
        # vocal acentuada —los de este módulo y los preexistentes— falla en
        # silencio sobre la forma descompuesta.
        # Impacto concreto medido: la negación de comisión de salida
        # ("No cobramos una comisión de salida") solo se encuentra tras normalizar
        # en 143 fondos; LU0346393613 guardaba la comisión de gestión como
        # comisión de salida por esta causa.
        # NFC solo recompone: no altera el contenido ni las longitudes lógicas.
        text = unicodedata.normalize('NFC', text)

        # --- A. Formato y moneda (siempre se intenta) ---
        out['KID_Format'] = detect_kid_format(text)
        currency = detect_kid_currency(text)
        if currency:
            out['KID_Currency'] = currency

        # Si no es PRIIPS_KID, este extractor no aplica
        if out['KID_Format'] != 'PRIIPS_KID':
            out['Cost_Extraction_Quality'] = 'NONE'
            out['_cost_schedule_rows']     = []
            return out

        # --- B. RHP numérico (necesario para P-1 y como campo de retorno) ---
        rhp_years = _extract_rhp_years(text)
        if rhp_years is not None:
            out['Cost_RHP_Years'] = rhp_years

        # --- C. Tabla "costes a lo largo del tiempo" ---
        over_time = parse_costs_over_time(text)
        schedule_source_used: Optional[str] = None
        if over_time:
            schedule_source_used = over_time[0].get('source')

        # ACI 1Y (None si RHP < 1 o no hay columna 1Y) — P-5
        aci_1y_ratio = _pick_aci_for_horizon(over_time, target_years=1.0, want_rhp=False)
        eur_1y       = _pick_eur_for_horizon(over_time, target_years=1.0, want_rhp=False)

        # ACI RHP (ancla de calidad) — preferir fila is_rhp; si no, fila == rhp_years
        aci_rhp_ratio = _pick_aci_for_horizon(over_time, target_years=rhp_years, want_rhp=True)
        eur_rhp       = _pick_eur_for_horizon(over_time, target_years=rhp_years, want_rhp=True)

        # Cross-validation del ancla 1Y y del RHP.
        # NOTA §4.4: solo es semánticamente correcta a 1 año. Para RHP > 1,
        # validate_pct_eur(ACI_RHP, EUR_RHP) produce discrepancia espuria.
        # Se calcula igualmente para que _assess_quality use vr_1y como ancla.
        vr_1y  = validate_pct_eur(aci_1y_ratio,  eur_1y,  base=PRIIPS_INVESTMENT_BASE)
        vr_rhp = validate_pct_eur(aci_rhp_ratio, eur_rhp, base=PRIIPS_INVESTMENT_BASE)

        # ACI 1Y: usar validated_pct de vr_1y (horizonte 1Y → cross-val válida)
        aci_1y_final = vr_1y.validated_pct if vr_1y.status != 'NONE' else None

        # ACI RHP: tomar directamente el ratio (§4.4), salvo cuando RHP == 1Y
        if rhp_years is not None and abs(rhp_years - 1.0) <= 0.01:
            # RHP = 1 año → cross-validation es legítima
            aci_rhp_final = vr_rhp.validated_pct if vr_rhp.status != 'NONE' else None
        else:
            # RHP != 1Y → tomar el aci_pct crudo (no pasar por validated_pct)
            aci_rhp_final = aci_rhp_ratio

        # FIX-ACI-LABEL-ANCHOR-CORRECT: el ancla de etiqueta manda cuando el valor
        # ligado por proximidad resulta ser la PROYECCIÓN DE RENTABILIDAD de la
        # nota al pie.  Auditoría 2026-08-23: 197 fondos publicados con ACI_RHP
        # discrepante > 5pp del ancla; verificados LU2092974778 (23,28% = "average
        # return per year is projected to be 23.28% before costs"; ACI real 1,42%)
        # e IE00BDR0R792 (17,63% proyección vs 7,14%/3,34% reales).  Muchos caen
        # por debajo del techo del guard (10–15%) y por eso se publicaban como
        # coste plausible pero falso, alimentando el scoring de P3.
        #
        # Doble condición, ambas de EVIDENCIA (no de magnitud):
        #   (a) el valor actual coincide con un número de la frase de proyección
        #       → prueba de que puede proceder de la nota al pie;
        #   (b) la etiqueta ACI NO respalda ese valor → prueba de que no procede
        #       de la tabla de costes.
        # Si la etiqueta SÍ lo respalda, la coincidencia con la proyección es
        # fortuita y el valor almacenado es correcto (16 fondos del corpus, p. ej.
        # LU0546920561 ACI 2,30 real frente a proyección 2,3): no se toca.
        _anchor_1y, _anchor_rhp = _recover_aci_from_label(text)
        _aci_anchor_corrected = False

        if (
            _anchor_rhp is not None
            and aci_rhp_final is not None
            and _matches_return_projection(text, _ratio_to_pct(aci_rhp_final))
            and not _label_vouches_for(text, _ratio_to_pct(aci_rhp_final))
            and abs(_ratio_to_pct(aci_rhp_final) - _anchor_rhp)
                > _ACI_NOOP_TOLERANCE_PP
        ):
            _log.info(
                "[FIX-ACI-LABEL-ANCHOR-CORRECT] %s: ACI_RHP %.2f%%→%.2f%% "
                "(valor previo == proyección de rentabilidad de la nota al pie)",
                isin, _ratio_to_pct(aci_rhp_final), _anchor_rhp,
            )
            aci_rhp_final = _anchor_rhp / 100.0
            _aci_anchor_corrected = True

        # ACI_1Y sufre la misma clase de error, con una segunda fuente además de
        # la proyección: la COMISIÓN DE ENTRADA. Verificado contra el documento —
        # LU0200685153 y LU0329593007 publicaban ACI_1Y=5,0 con
        # Entry_Fee_Pct_Max=5,0 ("Costes de entrada 5.00% del importe que paga")
        # cuando su fila ACI dice 7,1% y 6,4%; LU0147394679 igual con 3,0.
        # Basta por tanto la evidencia de procedencia: si la etiqueta ACI expone
        # valores y el almacenado NO está entre ellos, no salió de la fila de
        # coste. No se exige además coincidencia con la proyección, que solo
        # cubre una de las dos fuentes.
        if (
            _anchor_1y is not None
            and aci_1y_final is not None
            and not _label_vouches_for(text, _ratio_to_pct(aci_1y_final))
            and abs(_ratio_to_pct(aci_1y_final) - _anchor_1y)
                > _ACI_NOOP_TOLERANCE_PP
        ):
            _log.info(
                "[FIX-ACI-1Y-LABEL-ANCHOR] %s: ACI_1Y %.2f%%→%.2f%% "
                "(la etiqueta ACI no respalda el valor previo)",
                isin, _ratio_to_pct(aci_1y_final), _anchor_1y,
            )
            aci_1y_final = _anchor_1y / 100.0

        # FIX-ACI-RHP-COLUMN-DUP (2026-08-23): bug de duplicación de columnas.
        # En numerosas maquetas pdfplumber hace que la 2ª fila herede el aci_pct
        # de la 1ª (limitación ya documentada en el docstring de este módulo), de
        # modo que ACI_RHP acaba llevando el valor de 1 AÑO. Verificado contra el
        # documento: LU0152980495 (cabeceras "después de 1 año"/"después de 3
        # años", etiqueta "6.5% 3.1% cada año", RHP=3a) publicaba ACI_RHP=6,5
        # cuando el coste anualizado a RHP es 3,1. Igual en IE00B46MFP70,
        # IE0002460198, IE0032568887, IE00BDCRG239.
        #
        # Dos submecanismos, misma firma observable:
        #   (a) PLAIN_TEXT con dos filas que comparten aci_pct (duplicación pura) —
        #       LU0152980495: filas 1a y 3a ambas con 0.065;
        #   (b) DLA2 con UNA fila etiquetada como el horizonte RHP pero que porta
        #       los valores de 1 año — IE00B46MFP70: fila "después de 3 años" con
        #       aci_pct=0.064 y total_cost_eur=643 (el coste de UN año; a 3 años
        #       con 3,2%/año serían ~960 EUR).
        # En ambos ACI_RHP acaba conteniendo el valor de 1 AÑO, que es la
        # condición que se comprueba — no la duplicación, que solo cubre (a).
        #
        # Se corrige solo con evidencia convergente:
        #   - la etiqueta expone DOS valores distintos (hay un RHP que recuperar);
        #   - ACI_RHP coincide con el 1º de la etiqueta (el valor de 1 año);
        #   - 1º >= 2º, la dirección normal (los costes se amortizan: la comisión
        #     de entrada se reparte entre más años, así que el ACI baja con el
        #     horizonte). El 5% de casos con 1º < 2º NO son esta clase — se dejan
        #     intactos (LU2455947981: "3,9% 11,8%", donde 11,8 no es un ACI);
        #   - RHP != 1 año, pues entonces ACI_RHP == ACI_1Y es lo correcto.
        if (
            _anchor_1y is not None and _anchor_rhp is not None
            and aci_rhp_final is not None
            and abs(_anchor_1y - _anchor_rhp) >= _ACI_NOOP_TOLERANCE_PP
            and _anchor_1y >= _anchor_rhp                              # amortización
            and abs(_ratio_to_pct(aci_rhp_final) - _anchor_1y)
                < _ACI_NOOP_TOLERANCE_PP                               # porta el 1Y
            and not (rhp_years is not None and abs(rhp_years - 1.0) <= 0.01)
        ):
            _log.info(
                "[FIX-ACI-RHP-COLUMN-DUP] %s: ACI_RHP %.2f%%→%.2f%% "
                "(2ª columna había heredado el valor de 1 año)",
                isin, _ratio_to_pct(aci_rhp_final), _anchor_rhp,
            )
            aci_rhp_final = _anchor_rhp / 100.0
            _aci_anchor_corrected = True

        # FIX-ACI-PROJECTION-REJECT (2026-08-23): cuando el valor coincide con la
        # proyección de rentabilidad de la nota al pie y la etiqueta ACI NO lo
        # respalda, es una rentabilidad, no un coste — aunque no haya ancla con la
        # que sustituirlo. Antes la corrección exigía `_anchor_rhp is not None`, de
        # modo que los KID SIN fila de etiqueta ACI publicaban la proyección tal
        # cual: 42 fondos con ACI_RHP entre 7,5% y 11,9% (LU1670710075: 11,89 %
        # que el propio KID describe como "antes de deducir los costes"), valores
        # que además pasaban el techo del 15%.
        # Se rechaza: mejor NULL que un coste falso alimentando el scoring de P3
        # (mismo criterio que P0-ACI-RHP-GUARD).
        if (
            aci_rhp_final is not None
            and _anchor_rhp is None
            and _matches_return_projection(text, _ratio_to_pct(aci_rhp_final))
            and not _label_vouches_for(text, _ratio_to_pct(aci_rhp_final))
        ):
            _log.info(
                "[FIX-ACI-PROJECTION-REJECT] %s: ACI_RHP=%.2f%% rechazado "
                "(== proyección de rentabilidad; sin etiqueta ACI que lo respalde)",
                isin, _ratio_to_pct(aci_rhp_final),
            )
            aci_rhp_final = None

        # P0-ACI-GUARD: ACI values > 25% are parser bleed (scenario section
        # percentages captured instead of cost ACI). Confirmed root cause:
        # LU0256846568 (79.8%), LU1575199994 (71.4%) — CHECK constraint
        # ACI_RHP <= 25 was violated, blocking publish_fund entirely.
        # Reject at ratio level (0.25 == 25%) — better NULL than wrong value.
        _MAX_ACI_RATIO = 0.25
        if aci_1y_final is not None and aci_1y_final <= _MAX_ACI_RATIO:
            out['ACI_1Y'] = _ratio_to_pct(aci_1y_final)

        # P0-ACI-RHP-GUARD: for multi-year RHP, tighten the scenario-bleed
        # threshold from 25% to 15%.  Audit 2026-06-28: 153 funds stored
        # ACI_RHP 10–24% while the KID cost-table ACI was 1–6%.  Root cause:
        # parser captures performance-scenario return (tech funds show 15–25%
        # favorable scenarios) as ACI_RHP when the cost page is not found.
        # Secondary plausibility check: ACI_RHP / ACI_1Y > 5 is implausible
        # (annual cost drag does not vary 5× between year-1 and RHP for the
        # same fund). Both guards log and set ACI_RHP = NULL instead of bleed.
        # FIX-ACI-RHP-GUARD-DEFAULT (2026-08-23): la puerta era
        # `rhp_years > 1.0` → techo 15%, en cualquier otro caso 25%.  Pero las
        # entradas espurias nacidas de la nota al pie llegan con
        # horizon_years=-1.0 y rhp_years=None, así que caían justo en la rama
        # laxa y una proyección de rentabilidad de hasta el 25% se publicaba como
        # coste (LU2092974778: 23,28%).  Se invierte el defecto: el techo estricto
        # rige salvo que se sepa POSITIVAMENTE que el RHP es de un año, único caso
        # en que ACI_RHP == ACI_1Y y la cota laxa de esquema es pertinente.
        _rhp_is_one_year = rhp_years is not None and abs(rhp_years - 1.0) <= 0.01
        _MAX_ACI_RHP_RATIO = _MAX_ACI_RATIO if _rhp_is_one_year else 0.15
        _aci_rhp_ratio_ok = (
            aci_rhp_final is not None
            and aci_rhp_final <= _MAX_ACI_RHP_RATIO
            and not (
                rhp_years is not None and rhp_years > 1.0
                and aci_1y_final is not None and aci_1y_final > 0
                and aci_rhp_final / aci_1y_final > 5.0
            )
        )
        if _aci_rhp_ratio_ok:
            out['ACI_RHP'] = _ratio_to_pct(aci_rhp_final)
        elif aci_rhp_final is not None:
            _reason = (
                f"ratio {aci_rhp_final:.4f} > {_MAX_ACI_RHP_RATIO:.2f}"
                if aci_rhp_final > _MAX_ACI_RHP_RATIO
                else f"ACI_RHP/ACI_1Y={aci_rhp_final/aci_1y_final:.1f}>5"
            )
            _log.info(
                "[P0-ACI-RHP-GUARD] %s: ACI_RHP=%.4f rejected (%s); scenario-bleed suspected",
                isin, aci_rhp_final, _reason,
            )
        # Track guard rejection so the schedule repair below can target the
        # Is_RHP=1 row that was built from the bogus is_rhp entry.
        _guard_rejected = aci_rhp_final is not None and not _aci_rhp_ratio_ok

        # FIX-ACI-RHP-SINGLE: single-column OT fallback (mirrors harness
        # FIX-HARNESS / FIX-HARNESS-2). Audit 2026-06-30: 244 funds have
        # acirhp_regrid recovered by harness but not stored in production.
        # Root cause: when rhp_years=None, _pick_aci_for_horizon finds no
        # is_rhp entry and no target_years match → aci_rhp_ratio=None.
        # By PRIIPS regulation a single-column OT table IS the RHP column.
        # Two sub-cases:
        #   (a) aci_1y_final valid → ACI_RHP = ACI_1Y (236 funds)
        #   (b) len(over_time)==1 with non-1Y hy (e.g. 3-month) → take raw
        #       aci_pct directly (8 funds, mirrors FIX-HARNESS-2)
        if 'ACI_RHP' not in out and over_time:
            _has_longer = any(
                e.get('is_rhp') or (e.get('horizon_years') or 0) > 1.0
                for e in over_time
            )
            if not _has_longer:
                _fb_aci = aci_1y_final
                if _fb_aci is None and len(over_time) == 1:
                    _fb_aci = over_time[0].get('aci_pct')
                if _fb_aci is not None and _fb_aci <= _MAX_ACI_RATIO:
                    out['ACI_RHP'] = _ratio_to_pct(_fb_aci)
                    _log.info(
                        "[FIX-ACI-RHP-SINGLE] %s: ACI_RHP=%.4f%% from "
                        "single-column OT fallback",
                        isin, _ratio_to_pct(_fb_aci),
                    )

        # FIX-ACI-RHP-LONGEST: multi-column OT where the RHP column is not
        # directly pickable — either it carries is_rhp=False with rhp_years=None
        # (Audit 2026-06-30: 9 funds), OR the only is_rhp row was a bogus
        # footnote entry whose return-% value the P0-ACI-RHP-GUARD above just
        # rejected (FIX-ACI-RETURN-GUARD, 2026-08-19: iShares/BlackRock EN
        # "recommended holding period ... average return projected to be X%
        # before costs", and ES siblings — see IE00B3D07F16, LU1983261782).
        # In both cases ACI_RHP is still unset here; take the longest genuine
        # (non-is_rhp) horizon entry as the RHP approximation. Fires only when
        # ACI_RHP is unset (fill-only), so funds already resolved by the primary
        # pick are never perturbed. Guards mirror P0-ACI-RHP-GUARD (15% cap,
        # ACI_RHP/ACI_1Y ≤5) — a rejected is_rhp % is thus never re-admitted.
        if 'ACI_RHP' not in out and over_time:
            # >= 1.0 (not > 1.0): when the longer-horizon row has aci_pct=None
            # (e.g. Wellington layout "1.7% 1.7% cada año" merges both percentages
            # onto the 1Y label), the 1Y row is the best available approximation.
            # SINGLE already handles the true single-column case (no row > 1.0);
            # here we fire when a longer row exists but carries no ACI %.
            _candidates = [
                e for e in over_time
                if (e.get('horizon_years') or 0) >= 1.0
                and not e.get('is_rhp')
                and e.get('aci_pct') is not None
            ]
            if _candidates:
                _best = max(_candidates, key=lambda e: e.get('horizon_years', 0))
                _fb_aci2 = _best['aci_pct']
                _ratio_ok = not (
                    aci_1y_final is not None and aci_1y_final > 0
                    and _fb_aci2 / aci_1y_final > 5.0
                )
                if _fb_aci2 <= 0.15 and _ratio_ok:
                    out['ACI_RHP'] = _ratio_to_pct(_fb_aci2)
                    _log.info(
                        "[FIX-ACI-RHP-LONGEST] %s: ACI_RHP=%.4f%% from "
                        "longest non-RHP OT entry (hy=%.1f)",
                        isin, _ratio_to_pct(_fb_aci2),
                        _best.get('horizon_years', 0),
                    )

        # FIX-ACI-RHP-COLLAPSED: collapsed single-value OT row. On certain
        # issuer layouts (Neuberger Berman IE00BLLX*, LU1984*, and siblings)
        # pdfplumber merges the "Incidencia anual de los costes" row so a single
        # % survives — bound to the 1Y column — while the RHP / longer-horizon
        # column parses to aci_pct=None. SINGLE (needs no longer horizon) and
        # LONGEST (needs the longer entry to carry a %) both miss it, and the
        # target-year pick returns None. When, across the whole OT table, exactly
        # ONE horizon carries a real ACI %, that value IS the table's cost impact
        # → use it as the RHP anchor (mirrors the PRIIPS single-column rule).
        # Fill-only, capped at the general ACI ceiling; NULL > wrong.
        if 'ACI_RHP' not in out and over_time:
            _valid_acis = [
                e.get('aci_pct') for e in over_time
                if e.get('aci_pct') is not None
            ]
            if len(_valid_acis) == 1:
                _fb_aci3 = _valid_acis[0]
                if 0 < _fb_aci3 <= _MAX_ACI_RATIO:
                    out['ACI_RHP'] = _ratio_to_pct(_fb_aci3)
                    if 'ACI_1Y' not in out:
                        out['ACI_1Y'] = _ratio_to_pct(_fb_aci3)
                    _log.info(
                        "[FIX-ACI-RHP-COLLAPSED] %s: ACI_RHP=%.4f%% from "
                        "collapsed single-value OT row",
                        isin, _ratio_to_pct(_fb_aci3),
                    )

        # FIX-ACI-SINGLE-VS-LABEL (2026-08-23): FIX-ACI-RHP-SINGLE asume que una
        # tabla OT de una sola columna ES la columna RHP, y copia el valor de 1
        # año. Pero a veces la tabla NO es de una columna: es que el parse (DLA2)
        # solo capturó la fila de 1 año, mientras la etiqueta ACI sí expone las
        # dos. Resultado: ACI_1Y == ACI_RHP con RHP > 1 año y comisión de entrada
        # > 0 — imposible, porque la entrada se amortiza y el ACI baja con el
        # horizonte. 213 fondos en la auditoría; LU0210531637 publicaba 7,60 en
        # ambas cuando su etiqueta dice "7,60 % / 3,70 %".
        # Cuando la etiqueta expone dos valores distintos, manda sobre el
        # fallback: es evidencia directa frente a una suposición estructural.
        if (
            'ACI_RHP' in out
            and _anchor_1y is not None and _anchor_rhp is not None
            and abs(_anchor_1y - _anchor_rhp) >= _ACI_NOOP_TOLERANCE_PP
            and _anchor_1y >= _anchor_rhp
            and abs(out['ACI_RHP'] - _anchor_1y) < _ACI_NOOP_TOLERANCE_PP
            and not (rhp_years is not None and abs(rhp_years - 1.0) <= 0.01)
        ):
            _log.info(
                "[FIX-ACI-SINGLE-VS-LABEL] %s: ACI_RHP %.2f%%→%.2f%% "
                "(el fallback de columna única había copiado el valor de 1 año)",
                isin, out['ACI_RHP'], _anchor_rhp,
            )
            out['ACI_RHP'] = _anchor_rhp

        # FIX-ACI-LABEL-ANCHOR: último recurso de relleno. Cuando la tabla de
        # ESCENARIOS DE RENTABILIDAD se interpone en el flujo de texto entre el
        # titular de costes y la fila de coste real, parse_costs_over_time liga
        # el rendimiento del escenario de tensión (negativo) y el guard lo rechaza
        # con razón → ACI_RHP queda NULL aunque el KID SÍ publica el dato.
        # Auditoría 2026-08-23: 18 fondos del universo activo, los 18 recuperables
        # (ES0175404013 1,0% · ES0179692001 2,5% · LU0157028266 1,8% · …).
        # Solo rellena (fill-only): jamás perturba un ACI_RHP ya resuelto.
        _aci_anchor_filled = False
        if 'ACI_RHP' not in out and _anchor_rhp is not None:
            out['ACI_RHP'] = _anchor_rhp
            _aci_anchor_filled = True
            _log.info(
                "[FIX-ACI-LABEL-ANCHOR] %s: ACI_RHP=%.2f%% recuperado del ancla "
                "de etiqueta (tabla de escenarios interpuesta)",
                isin, _anchor_rhp,
            )
        if 'ACI_1Y' not in out and _anchor_1y is not None:
            out['ACI_1Y'] = _anchor_1y
            _log.info(
                "[FIX-ACI-LABEL-ANCHOR] %s: ACI_1Y=%.2f%% recuperado del ancla "
                "de etiqueta",
                isin, _anchor_1y,
            )

        # --- D. Tabla "composición de los costes" ---
        comp = parse_costs_composition(text)

        # Entry/Exit MAX: preferir *_max_pct; fallback a *_fee_pct
        # P0-FEE-GUARD: Entry/Exit > 25% is a parser error (mis-assigned value).
        # Confirmed: LU0823416762 produced Entry_Fee_Pct_Max > 25, blocking
        # publish_fund. _guarded_pct in the composition parser caps at 25% but
        # the ratio threshold is tight — add a final boundary here as backstop.
        # Schema CHECK: Entry_Fee_Pct_Max <= 25 (percent form).
        _MAX_FEE_RATIO = 0.25
        entry_max = comp.get('entry_fee_max_pct', comp.get('entry_fee_pct'))
        exit_max  = comp.get('exit_fee_max_pct',  comp.get('exit_fee_pct'))
        # FIX-ENTRY-FEE-BY-DESCRIPTION (2026-08-23): la comisión de entrada se
        # define sobre el IMPORTE QUE SE PAGA; el ACI, sobre el VALOR DE LA
        # INVERSIÓN AL AÑO. Ligando por posición el parser guarda a veces el
        # ACI_1Y como comisión de entrada — 85 fondos, verificado con la tabla de
        # LU1883314244 (entrada 4,50 %, se guardaba 6,6 % = su ACI_1Y).
        # Solo corrige ante esa firma (entrada == ACI_1Y) y con evidencia
        # descriptiva explícita; nunca pisa una entrada ya distinta del ACI.
        # FIX-FEE-NEGATION (2026-08-23): la fila de entrada/salida niega con TEXTO
        # ("No cobramos comisión de salida") y el parser liga entonces el número
        # de la fila siguiente, casi siempre la comisión de gestión.
        # Auditoría: Exit_Fee_Pct_Max == Management_Fee_Pct en 663 fondos; 512 con
        # negación explícita y, en 509 de ellos, la columna Exit_Fee_Pct (ratio)
        # ya valía 0 — confirmación cruzada interna de que la comisión es cero.
        # La negación explícita manda sobre cualquier número ligado por posición.
        if exit_max is not None and exit_max > 0 and EXIT_FEE_NEGATION.search(text):
            _log.info(
                "[FIX-FEE-NEGATION] %s: Exit_Fee_Pct_Max %.2f%%→0 "
                "(el KID declara que no se cobra comisión de salida)",
                isin, _ratio_to_pct(exit_max),
            )
            exit_max = 0.0
        if entry_max is not None and entry_max > 0 and ENTRY_FEE_NEGATION.search(text):
            _log.info(
                "[FIX-FEE-NEGATION] %s: Entry_Fee_Pct_Max %.2f%%→0 "
                "(el KID declara que no se cobra comisión de entrada)",
                isin, _ratio_to_pct(entry_max),
            )
            entry_max = 0.0

        _entry_pct = _ratio_to_pct(entry_max) if entry_max is not None else None
        if (
            _entry_pct is not None
            and aci_1y_final is not None
            and abs(_entry_pct - _ratio_to_pct(aci_1y_final)) < 0.02
        ):
            _m = ENTRY_FEE_VALUE.search(text)
            if _m:
                _tok = _m.group(1) or _m.group(2)
                try:
                    _cand = _pct_token_to_float(_tok)
                except ValueError:
                    _cand = None
                if _cand is not None and 0 <= _cand <= 25.0 and abs(_cand - _entry_pct) > 0.02:
                    _log.info(
                        "[FIX-ENTRY-FEE-BY-DESCRIPTION] %s: Entry_Fee_Pct_Max "
                        "%.2f%%→%.2f%% (el valor previo era el ACI_1Y)",
                        isin, _entry_pct, _cand,
                    )
                    entry_max = _cand / 100.0

        if entry_max is not None and entry_max <= _MAX_FEE_RATIO:
            out['Entry_Fee_Pct_Max'] = _ratio_to_pct(entry_max)
        if exit_max is not None and exit_max <= _MAX_FEE_RATIO:
            out['Exit_Fee_Pct_Max']  = _ratio_to_pct(exit_max)

        mgmt = comp.get('management_fee_pct')
        tran = comp.get('transaction_cost_pct')
        perf = comp.get('performance_fee_pct')

        # FIX-COMPOSITION-BY-DESCRIPTION (2026-08-23): en las maquetas de columna
        # partida el parser liga a "gestión" el % de OPERACIÓN, con lo que ambas
        # columnas quedan con el mismo número y la comisión de gestión real (que
        # sí está en el texto, huérfana tras "Otros datos de interés") se pierde.
        # Auditoría de distribución: 71 fondos con Management_Fee_Pct ==
        # Transaction_Cost_Pct, verificados LU0110450813 (gestión real 1,6% frente
        # a 0,3% publicado; la arbitración xBand ya decía 1,6) y LU1089088741
        # (0,50% frente a 0,09%). Un fondo con ACI_RHP de 2,6-3,0% no puede tener
        # una comisión de gestión del 0,09%.
        #
        # Solo corrige ante la firma del fallo — ambas columnas iguales, o gestión
        # ausente — y solo con evidencia descriptiva explícita. Nunca pisa una
        # extracción en la que gestión y operación ya difieren.
        _desc_mgmt, _desc_tran = _recover_composition_from_description(text)
        _collapsed = (
            mgmt is not None and tran is not None
            and abs(mgmt - tran) < 1e-9
        )
        if _desc_mgmt is not None and (_collapsed or mgmt is None):
            if mgmt is None or abs(_ratio_to_pct(mgmt) - _desc_mgmt) > 0.01:
                _log.info(
                    "[FIX-COMPOSITION-BY-DESCRIPTION] %s: Management_Fee_Pct "
                    "%s→%.2f%% (ligado por descripción normativa)",
                    isin,
                    "NULL" if mgmt is None else f"{_ratio_to_pct(mgmt):.2f}%",
                    _desc_mgmt,
                )
            mgmt = _desc_mgmt / 100.0
        if _desc_tran is not None and _collapsed:
            tran = _desc_tran / 100.0

        if mgmt is not None:
            out['Management_Fee_Pct']   = _ratio_to_pct(mgmt)
        if tran is not None:
            out['Transaction_Cost_Pct'] = _ratio_to_pct(tran)
        # FIX-PERF-FEE-NEGATION (2026-08-23): la fila de comisión de rendimiento
        # niega su existencia con TEXTO, no con un 0, y la línea contigua suele
        # anunciar una comisión de CANJE — cuyo % acaba ligado como si fuera la
        # de rendimiento. Verificado contra la tabla publicada de LU1873132101:
        # "No se aplica ninguna comisión de éxito para este producto" seguido de
        # "comisión de canje no superior al 1%" → se publicaba 1,0. El valor 1.0
        # es la moda del corpus (279 fondos), lo que delata un patrón, no un dato.
        #
        # La negación explícita es evidencia más fuerte que cualquier número
        # ligado por posición: gana siempre y fija la comisión en 0.
        # FIX-PERF-FEE-MISBIND (2026-08-23): si el % ligado como comisión de
        # rendimiento coincide con OTRO componente de coste (entrada, salida,
        # gestión u operación) y el KID no aporta ninguna mecánica de comisión de
        # rendimiento, el número pertenece a esa otra fila. Publicarlo es peor que
        # omitirlo. Se resuelve AQUÍ y no anulando en BD: una anulación en BD la
        # deshace el siguiente reproceso, porque el extractor vuelve a derivar el
        # valor en cada pasada (comprobado: los 43 anulados reaparecieron).
        if perf is not None and perf > 0:
            _perf_pct = _ratio_to_pct(perf)
            _dups = [
                _v for _v in (entry_max, exit_max, mgmt, tran)
                if _v is not None and abs(_ratio_to_pct(_v) - _perf_pct) < 0.005
            ]
            if _dups and not _PERF_FEE_MECHANICS.search(text):
                _log.info(
                    "[FIX-PERF-FEE-MISBIND] %s: Performance_Fee_Pct=%.2f%% descartado "
                    "(coincide con otro componente y el KID no describe mecánica alguna)",
                    isin, _perf_pct,
                )
                perf = None

        if perf is not None and PERFORMANCE_FEE_NEGATION.search(text):
            if perf != 0.0:
                _log.info(
                    "[FIX-PERF-FEE-NEGATION] %s: Performance_Fee_Pct %.2f%%→0 "
                    "(el KID declara que no se aplica comisión de rendimiento)",
                    isin, _ratio_to_pct(perf),
                )
            perf = 0.0

        if perf is not None:
            out['Performance_Fee_Pct']  = _ratio_to_pct(perf)

        # FIX-ACI-EQUALS-TRANSACTION (2026-08-23): en algunas maquetas el ACI_RHP
        # acaba ligado a la fila de COSTES DE OPERACIÓN, con lo que queda idéntico
        # a Transaction_Cost_Pct y muy por debajo de la propia comisión de gestión
        # —imposible, porque el ACI es el total y la gestión solo un componente.
        # Detectado por la auditoría de distribución vía "Management_Fee > ACI_RHP".
        # Verificado: LU1099740216 guardaba 0,19 (== operación) cuando su etiqueta
        # ACI dice 8,94 % / 4,83 %; LU0512093542 igual con 0,19 frente a 3,16 %.
        # Se corrige solo con la firma completa: ACI_RHP == operación, la etiqueta
        # respalda otro valor y ese valor es mayor (el ACI incluye la operación).
        if (
            'ACI_RHP' in out and tran is not None
            and abs(out['ACI_RHP'] - _ratio_to_pct(tran)) < 0.005
            and _anchor_rhp is not None
            and _anchor_rhp > out['ACI_RHP'] + 0.02
        ):
            _log.info(
                "[FIX-ACI-EQUALS-TRANSACTION] %s: ACI_RHP %.2f%%→%.2f%% "
                "(el valor previo era el coste de operación)",
                isin, out['ACI_RHP'], _anchor_rhp,
            )
            out['ACI_RHP'] = _anchor_rhp
            if _anchor_1y is not None and _anchor_1y >= _anchor_rhp:
                out['ACI_1Y'] = _anchor_1y

        # --- E. TER reconstruido y gestión de Ongoing_Charge_Recurrent (P-3) ---
        ter_recon_ratio: Optional[float] = None
        if mgmt is not None:
            ter_recon_ratio = mgmt + (tran or 0.0)

        if ter_recon_ratio is not None:
            if existing_oc is None:
                # COALESCE-compatible: rellenar hueco con TER puro.
                #
                # FIX-OC-SCALE (2026-08-23): antes escribía _ratio_to_pct(...),
                # es decir PORCENTAJE, en una columna cuya convención es RATIO
                # decimal — la que documenta kiid_parser._detect_ongoing_charge
                # ("devuelve float decimal, p. ej. 0.0075 para 0.75%") y la que
                # asume _norm_existing_oc. Resultado: los fondos rellenados por
                # esta vía quedaban 100× por encima de los heredados. Auditoría
                # de distribución 2026-08-23: 81 fondos con OC > 0,5 en los que
                # OC coincidía EXACTAMENTE con el TER en escala porcentual
                # (BE0058182792: OC 1.98, TER 1,98%). Se escribe el ratio.
                out['Ongoing_Charge_Recurrent'] = ter_recon_ratio
            else:
                oc_norm = _norm_existing_oc(existing_oc)

                # FIX-OC-ACI-REPAIR (2026-08-23): el OC heredado es el ACI.
                # Firma: OC (en ratio) coincide con ACI_RHP y difiere de la
                # comisión de gestión. En 235 fondos la gestión ya está bien en BD
                # —arbitración xBand con veredicto AGREE en los 235— pero su valor
                # solo aparece en el texto como fragmento de celda ("0.29%]"), así
                # que ninguna regla sobre prosa puede recuperarlo. El gasto
                # corriente ES el componente de gestión (definición adoptada), de
                # modo que la reparación consistente es igualarlo a mgmt.
                # El _oc_aci_mismatch existente solo MARCABA el problema desde
                # 2026-06 sin corregirlo ("listo para BL-COST-5", nunca implementado).
                # Se compara contra el ACI_RHP FINAL (out), no contra
                # aci_rhp_final: cuando el valor lo aporta un fallback
                # (SINGLE/LONGEST/COLLAPSED/ancla) aci_rhp_final sigue a None y la
                # reparación no llegaba a 48 fondos igualmente contaminados.
                #
                # FIX-OC-BIND (2026-08-24): generalización de FIX-OC-ACI-REPAIR.
                # La auditoría de distribución detectó una SEGUNDA mala ligadura del
                # gasto corriente que la regla anterior no veía: el OC heredado es un
                # componente MENOR (la fila de operación o la de salida), no el ACI.
                # Firma medida sobre el universo activo: 373 fondos con
                # OC == Transaction_Cost_Pct exacto y gestión muy superior
                # (LU0823401905: OC 0,08 % frente a gestión 2,71 %). Contraste sano:
                # 2.207 fondos con OC == gestión, la ligadura correcta.
                #
                # El invariante es aritmético, no heurístico: el gasto corriente
                # CONTIENE la comisión de gestión (UCITS OCF), luego no puede quedar
                # por debajo de ella. Se reparan ambas ligaduras con UNA sola guarda
                # (P#11 / R-1), no con dos ramas paralelas.
                #
                # ⚠ Un incumplimiento del invariante no dice qué lado está mal
                # (control de método #4): si la gestión fuese la equivocada, fijar el
                # OC a ella propagaría el error. Por eso la vía (2) exige que el ACI
                # —el total publicado— respalde a la gestión. Medición: 350 fondos
                # reparables con respaldo, 6 descartados por gestión no respaldada,
                # y 0 de los 2.207 correctos alcanzados por la guarda.
                _aci_final_ratio = (
                    out['ACI_RHP'] / 100.0 if 'ACI_RHP' in out else aci_rhp_final
                )
                _aci_1y_ratio = (
                    out['ACI_1Y'] / 100.0 if 'ACI_1Y' in out else aci_1y_final
                )
                _bind_signal = _resolve_oc_binding(
                    oc_norm, mgmt, _aci_final_ratio, _aci_1y_ratio
                )

                if _bind_signal is not None:
                    _log.info(
                        "[FIX-OC-BIND] %s: Ongoing_Charge %.4f→%.4f "
                        "(el valor heredado era %s; se fija al componente de gestión)",
                        isin, oc_norm, mgmt, _bind_signal,
                    )
                    out['Ongoing_Charge_Recurrent'] = mgmt

                if _detect_oc_aci_mismatch(existing_oc, oc_norm, ter_recon_ratio, aci_rhp_final):
                    out['_oc_aci_mismatch'] = True
                    out['_oc_aci_mismatch_ter_pct'] = _ratio_to_pct(ter_recon_ratio)
                    _log.info(
                        "[BL-COST-4a][OC-ACI] %s: BD OC parece ACI (%.4f) != TER recon (%.4f); "
                        "ter_pct=%.4f%% listo para BL-COST-5",
                        isin, oc_norm or -1.0, ter_recon_ratio, _ratio_to_pct(ter_recon_ratio),
                    )
                # Si no hay mismatch y existing_oc no es None → no se toca (COALESCE)

        # --- F. _cost_schedule_rows (P-1, P-7, P-4) ---
        out['_cost_schedule_rows'] = _build_schedule_rows(
            over_time, rhp_years, isin, text, _anchor_1y
        )

        # FIX-ACI-SCHEDULE-INJECT: when a fallback (SINGLE/LONGEST/COLLAPSED)
        # recovered ACI_RHP metadata but _build_schedule_rows produced no Is_RHP=1
        # row with Annual_Impact_Pct (because rhp_years=None discarded the is_rhp
        # row), synthesize a minimal RHP row so P2/P3 can read the cost impact.
        # Guard: only fire when ACI_RHP is set and no usable RHP row exists.
        _sched = out['_cost_schedule_rows']
        _has_rhp_pct = any(
            r.get('Is_RHP') == 1 and r.get('Annual_Impact_Pct') is not None
            for r in _sched
        )
        if not _has_rhp_pct and 'ACI_RHP' in out:
            # Derive a sensible horizon: prefer rhp_years; else the OT entry
            # whose aci_pct matches the fallback value; else the longest OT
            # horizon; else 5.0 (common default RHP).
            # Guard: exclude is_rhp rows (horizon_years=-1.0) from horizon
            # candidates — using -1.0 would fail the 0 < hy <= 50 guard
            # downstream (root cause: ES0176408013, FR0014000EB4).
            _aci_ratio = out['ACI_RHP'] / 100.0
            _syn_hy = rhp_years
            if _syn_hy is None:
                _match = [
                    e for e in over_time
                    if e.get('aci_pct') is not None
                    and abs(e['aci_pct'] - _aci_ratio) < 1e-6
                    and (e.get('horizon_years') or 0) > 0   # exclude is_rhp (-1.0)
                ]
                if _match:
                    _syn_hy = max(_match, key=lambda e: e.get('horizon_years', 0)).get(
                        'horizon_years'
                    )
            if _syn_hy is None or _syn_hy <= 0:
                # Fallback: longest positive horizon in over_time
                _pos_hy = [
                    e.get('horizon_years', 0)
                    for e in over_time
                    if (e.get('horizon_years') or 0) > 0
                ]
                _syn_hy = max(_pos_hy) if _pos_hy else 5.0
            _syn_hy = _syn_hy or 5.0
            if 0 < _syn_hy <= 50:
                # PRIMARY KEY is (ISIN, Horizon_Years) in fund_cost_schedule.
                # Appending a new row at the same Horizon_Years as an existing
                # non-RHP row causes a PK conflict that rolls back the entire
                # schedule write.  PROMOTE the existing row to Is_RHP=1 instead
                # of appending a duplicate (FIX-ACI-SCHEDULE-INJECT-PK).
                _syn_hy_r = round(_syn_hy, 6)
                _existing = next(
                    (r for r in _sched
                     if round(r.get('Horizon_Years', 0), 6) == _syn_hy_r),
                    None,
                )
                # FIX-SCHEDULE-IS-RHP-1Y (2026-08-24): la promoción no puede
                # etiquetar como RHP una fila que lleva el impacto de 1 AÑO.
                # Cuando rhp_years es None, la cadena de respaldo acaba eligiendo
                # "el horizonte positivo más largo", que en los KID de una sola
                # columna es 1,0 — justo la fila cuyo Annual_Impact_Pct es el
                # ACI_1Y. El resultado es una fila Is_RHP=1 que avala el valor
                # equivocado: medido en 47 de 56 discrepancias grandes
                # (FR0010174144: schedule 6,17 % = ACI_1Y frente a ACI_RHP 4,72 %).
                # Si no sabemos el RHP, es preferible no marcar ninguna fila: el
                # ACI_RHP sigue llegando a P2/P3 por fund_master.
                _aci_1y_pct = out.get('ACI_1Y')
                _would_mislabel = (
                    _existing is not None
                    and rhp_years is None
                    and abs(_syn_hy_r - 1.0) <= 0.01
                    and _existing.get('Annual_Impact_Pct') is not None
                    and _aci_1y_pct is not None
                    and abs(_existing['Annual_Impact_Pct'] - _aci_1y_pct) < 0.05
                    and abs(_aci_1y_pct - out['ACI_RHP']) >= 0.5
                )
                if _would_mislabel:
                    _log.info(
                        "[FIX-SCHEDULE-IS-RHP-1Y] %s: promoción a Is_RHP=1 rechazada "
                        "(la fila de 1 año lleva el ACI_1Y %.2f%%, no el ACI_RHP %.2f%%)",
                        isin, _aci_1y_pct, out['ACI_RHP'],
                    )
                elif _existing is not None:
                    _existing['Is_RHP'] = 1
                    if _existing.get('Annual_Impact_Pct') is None:
                        _existing['Annual_Impact_Pct'] = out['ACI_RHP']
                    _log.info(
                        "[FIX-ACI-SCHEDULE-INJECT] %s: promoted existing schedule row "
                        "to Is_RHP=1 (Annual_Impact_Pct=%.4f%%, Horizon_Years=%.6f)",
                        isin, _existing['Annual_Impact_Pct'], _syn_hy_r,
                    )
                else:
                    _sched.append({
                        'Horizon_Years': _syn_hy_r,
                        'Is_RHP': 1,
                        'Annual_Impact_Pct': out['ACI_RHP'],
                        'Source': 'PRIIPS_COSTS_OVER_TIME',
                    })
                    _log.info(
                        "[FIX-ACI-SCHEDULE-INJECT] %s: synthesized new RHP schedule row "
                        "(Annual_Impact_Pct=%.4f%%, Horizon_Years=%.6f)",
                        isin, out['ACI_RHP'], _syn_hy_r,
                    )

        # FIX-SCHEDULE-IS-RHP-REPAIR: when P0-ACI-RHP-GUARD rejected the is_rhp
        # row's value AND a fallback (LONGEST/SINGLE/COLLAPSED) recovered the
        # correct ACI_RHP, the schedule's Is_RHP=1 row still carries the rejected
        # bogus % (e.g. Wellington ES: return-projection 13.2% written as cost
        # Annual_Impact_Pct instead of the true 1.7%). FIX-ACI-SCHEDULE-INJECT
        # skips injection because the row already "has" a value — this repair
        # overwrites that wrong value with the fallback-recovered ACI_RHP.
        # Fires only when guard_rejected AND a fallback set ACI_RHP; fills only
        # Is_RHP=1 rows carrying a value inconsistent with the recovered ACI_RHP.
        # FIX-ACI-LABEL-ANCHOR (2026-08-23) amplía el disparador a dos casos más:
        #   - el ancla CORRIGIÓ un valor que había PASADO el guard (proyección de
        #     la nota al pie por debajo del techo);
        #   - el ancla RELLENÓ ACI_RHP sin que el guard llegara a rechazar nada
        #     (aci_rhp_final era None porque la fila RHP no era ligable, pero
        #     _build_schedule_rows sí había escrito el % de escenario en la fila).
        # En ambos la fila Is_RHP=1 conservaría el número falso sin esta reparación.
        if 'ACI_RHP' in out and (
            _guard_rejected or _aci_anchor_corrected or _aci_anchor_filled
        ):
            for _sr in out['_cost_schedule_rows']:
                if _sr.get('Is_RHP') == 1 and _sr.get('Annual_Impact_Pct') is not None:
                    _old_pct = _sr['Annual_Impact_Pct']
                    if abs(_old_pct - out['ACI_RHP']) > 0.01:
                        _sr['Annual_Impact_Pct'] = out['ACI_RHP']
                        _log.info(
                            "[FIX-SCHEDULE-IS-RHP-REPAIR] %s: Is_RHP=1 corrected "
                            "%.1f%%→%.1f%% (bogus is_rhp entry overwritten by fallback)",
                            isin, _old_pct, out['ACI_RHP'],
                        )

        # --- G. Calidad (§3) ---
        out['Cost_Extraction_Quality'] = _assess_quality(
            vr_rhp=vr_rhp,
            vr_1y=vr_1y,
            aci_rhp_final=aci_rhp_final,
            aci_1y_final=aci_1y_final,
            comp=comp,
            over_time=over_time,
            schedule_source_used=schedule_source_used,
        )

        return out

    except Exception as exc:
        _log.warning(
            "[BL-COST-4a] %s: fallo en extracción (%s); retorno parcial LOW",
            isin, exc,
        )
        out.setdefault('KID_Format', 'UNKNOWN')
        out.setdefault('Cost_Extraction_Quality', 'LOW')
        out.setdefault('_cost_schedule_rows', [])
        return out
