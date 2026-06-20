# proyecto1/scripts/diag/cost_format_signals.py
# -*- coding: utf-8 -*-
"""
cost_format_signals.py — funciones PURAS de detección de formato KID
y patologías de falsos positivos en costes. Aisladas para R-7.4.

BL-COST-1 (Sprint 1): módulo nuevo.

NO importa de pipeline.py, core.io, proyecto1.* — solo stdlib + re.
Sin efectos secundarios: todas las funciones son puras (deterministicas
para la misma entrada).

Funciones exportadas:
    detect_kid_format(text) -> str
    detect_entry_fee_false_positive(text, entry_fee_db) -> dict
    detect_exit_fee_false_positive(text, exit_fee_db) -> dict
    detect_oc_aci_gap(text, oc_db) -> dict
"""

import re
from typing import Optional


# ======================================================================
# Patrones de detección de formato KID (BL-COST-1 fase 8)
# ======================================================================

PRIIPS_SIGNALS_STRONG = [
    # Encabezados oficiales PRIIPs
    r'documento de datos fundamentales',
    r'key information document',
    # Secciones específicas PRIIPs
    r'composici[óo]n de los costes',
    r'composition of costs',
    r'costes a lo largo del tiempo',
    r'costs over time',
    r'incidencia anual de los costes',
    r'annual cost impact',
    # Escenarios PRIIPs Cat. 3
    r'escenarios? de rentabilidad',
    r'performance scenarios',
    r'per[ií]odo de mantenimiento recomendado',
    r'recommended holding period',
]

UCITS_SIGNALS_STRONG = [
    r'datos fundamentales para el inversor',
    r'key investor information',
    r'gastos corrientes',
    r'entry charge.{0,80}exit charge.{0,80}ongoing charge',
    # Patrón multi-línea: comisión entrada + salida + gestión en mismo bloque
    r'comisi[óo]n\s+de\s+entrada.{0,200}comisi[óo]n\s+de\s+salida'
    r'.{0,200}comisi[óo]n\s+de\s+gesti[óo]n',
]

# Patrón: valor EUR/USD/€ en proximidad de palabras clave de coste.
# Ventana acotada .{0,200}? (lazy) según R-6.
EUR_VALUES_NEAR_COSTS_PATTERN = (
    r'(?:costes?\s+de\s+entrada|costes?\s+de\s+salida|'
    r'entry\s+costs?|exit\s+costs?|management\s+fees?|'
    r'comisi[óo]n\s+de\s+gesti[óo]n)'
    r'.{0,200}?'
    r'(?:\d{1,5})\s*(?:EUR|USD|€|\$)'
)


def detect_kid_format(text: str) -> str:
    """
    Score-based detection del formato regulatorio del KID.

    Lógica:
    - >=3 señales PRIIPs strong + >=1 valor EUR cerca de costes → PRIIPS_KID
    - >=2 señales UCITS strong + 0 señales PRIIPs strong → UCITS_KIID
    - Resto → UNKNOWN

    Args:
        text: texto plano extraído del KID/KIID (Raw_KIID_Text + DLA2_Table_Text).

    Returns:
        'PRIIPS_KID' | 'UCITS_KIID' | 'UNKNOWN'
    """
    if not text:
        return 'UNKNOWN'

    priips_hits = sum(
        1 for p in PRIIPS_SIGNALS_STRONG
        if re.search(p, text, re.I)
    )
    ucits_hits = sum(
        1 for p in UCITS_SIGNALS_STRONG
        if re.search(p, text, re.I)
    )
    eur_hits = len(re.findall(EUR_VALUES_NEAR_COSTS_PATTERN, text, re.I | re.S))

    if priips_hits >= 3 and eur_hits >= 1:
        return 'PRIIPS_KID'
    if ucits_hits >= 2 and priips_hits == 0:
        return 'UCITS_KIID'
    return 'UNKNOWN'


def count_kid_format_signals(text: str) -> dict:
    """
    Devuelve conteos individuales de señales para diagnóstico.

    Returns:
        {'priips_count': int, 'ucits_count': int, 'eur_near_costs_count': int}
    """
    if not text:
        return {'priips_count': 0, 'ucits_count': 0, 'eur_near_costs_count': 0}

    return {
        'priips_count': sum(
            1 for p in PRIIPS_SIGNALS_STRONG
            if re.search(p, text, re.I)
        ),
        'ucits_count': sum(
            1 for p in UCITS_SIGNALS_STRONG
            if re.search(p, text, re.I)
        ),
        'eur_near_costs_count': len(
            re.findall(EUR_VALUES_NEAR_COSTS_PATTERN, text, re.I | re.S)
        ),
    }


# ======================================================================
# Detección de falsos positivos en Entry_Fee_Pct=0 (BL-COST-1 fase 9)
# ======================================================================

ENTRY_FEE_NONZERO_SIGNALS = [
    # % explícito en descripción de entrada (valor capturado para excluir 0,00%)
    (r'(\d+[,.]\d+)\s*%\s*(?:m[áa]ximo\s+)?del\s+importe.{0,80}entrada', 'pct_inline'),
    (
        r'(\d+[,.]\d+)\s*%\s*(?:m[áa]ximo\s+)?'
        r'del\s+(?:importe|valor)\s+(?:que\s+)?(?:pagar|invertir)',
        'pct_inline',
    ),
    # Valor EUR/USD ≥10 en línea de entry costs (ventana acotada R-6)
    (r'costes?\s+de\s+entrada.{0,200}?(\d{2,5})\s*(?:EUR|USD|€|\$)', 'eur_value'),
    (r'entry\s+costs?.{0,200}?(\d{2,5})\s*(?:EUR|USD|€|\$)', 'eur_value'),
    # Frase condicional: máximo / hasta / could be up to
    (r'(?:m[áa]ximo|hasta\s+el|up\s+to)\s+(\d+[,.]?\d*)\s*%.{0,80}entrada', 'pct_max'),
]


def detect_entry_fee_false_positive(
    text: str,
    entry_fee_db: Optional[float],
) -> dict:
    """
    Para fondos con entry_fee_db == 0.0, evalúa si hay evidencia textual
    de fee no-cero (falso positivo silencioso).

    Si entry_fee_db != 0.0, retorna inmediatamente is_suspect=False (no es
    un falso positivo por definición: el campo tiene un valor real).

    Args:
        text: texto plano del KID.
        entry_fee_db: valor de Entry_Fee_Pct en BD (float o None).

    Returns:
        {
            'is_suspect': bool,
            'signal_count': int,
            'signals_matched': list[(tag, value_float)]
        }
    """
    if entry_fee_db != 0.0:
        return {'is_suspect': False, 'signal_count': 0, 'signals_matched': []}

    matched = []
    for pat, tag in ENTRY_FEE_NONZERO_SIGNALS:
        for m in re.finditer(pat, text, re.I | re.S):
            try:
                captured = m.group(1) if m.groups() else ''
                val = float(captured.replace(',', '.')) if captured else 0.0
            except (ValueError, IndexError):
                val = 0.0
            # Excluir señales con valor 0 (no son falsos positivos)
            if val == 0.0:
                continue
            matched.append((tag, val))

    return {
        'is_suspect': len(matched) > 0,
        'signal_count': len(matched),
        'signals_matched': matched,
    }


# ======================================================================
# Detección de falsos positivos en Exit_Fee_Pct=0 (BL-COST-1 fase 10)
# ======================================================================

EXIT_FEE_NONZERO_SIGNALS = [
    (r'(\d+[,.]\d+)\s*%\s*(?:m[áa]ximo\s+)?del\s+importe.{0,80}salida', 'pct_inline'),
    (r'costes?\s+de\s+salida.{0,200}?(\d{2,5})\s*(?:EUR|USD|€|\$)', 'eur_value'),
    (r'exit\s+costs?.{0,200}?(\d{2,5})\s*(?:EUR|USD|€|\$)', 'eur_value'),
]


def detect_exit_fee_false_positive(
    text: str,
    exit_fee_db: Optional[float],
) -> dict:
    """
    Análogo a detect_entry_fee_false_positive para Exit_Fee_Pct.

    Args:
        text: texto plano del KID.
        exit_fee_db: valor de Exit_Fee_Pct en BD (float o None).

    Returns:
        {
            'is_suspect': bool,
            'signal_count': int,
            'signals_matched': list[(tag, value_float)]
        }
    """
    if exit_fee_db != 0.0:
        return {'is_suspect': False, 'signal_count': 0, 'signals_matched': []}

    matched = []
    for pat, tag in EXIT_FEE_NONZERO_SIGNALS:
        for m in re.finditer(pat, text, re.I | re.S):
            try:
                captured = m.group(1) if m.groups() else ''
                val = float(captured.replace(',', '.')) if captured else 0.0
            except (ValueError, IndexError):
                val = 0.0
            if val == 0.0:
                continue
            matched.append((tag, val))

    return {
        'is_suspect': len(matched) > 0,
        'signal_count': len(matched),
        'signals_matched': matched,
    }


# ======================================================================
# Detección de sospecha OC ≠ TER real (BL-COST-1 fase 11)
# ======================================================================

# Patrones para "Annual cost impact / Incidencia anual de los costes X%"
ACI_PATTERN_ES = r'incidencia\s+anual\s+de\s+los\s+costes.{0,80}?(\d+[,.]\d+)\s*%'
ACI_PATTERN_EN = r'annual\s+cost\s+impact.{0,80}?(\d+[,.]\d+)\s*%'


def detect_oc_aci_gap(text: str, oc_db: Optional[float]) -> dict:
    """
    Detecta si el valor de Ongoing_Charge_Recurrent en BD parece ser
    realmente un "Annual Cost Impact" (ACI) que incluye amortización de
    one-offs, en lugar del TER recurrente puro.

    Heurística: si en el texto aparece "Annual cost impact: X%" (o
    equivalente ES) y X% coincide con oc_db dentro de 10 basis points
    (tolerancia 0.1), entonces oc_db probablemente está mal etiquetado
    como TER cuando es un ACI.

    Args:
        text: texto plano del KID.
        oc_db: valor de Ongoing_Charge (o Ongoing_Charge_Recurrent) en
               BD como ratio decimal (ej: 0.024 para 2.4%).
               Si oc_db es un porcentaje entero (ej: 2.4 para 2.4%),
               la comparación se hace directamente.

    Returns:
        {
            'is_suspect': bool,
            'aci_values_found': list[float],  # valores ACI detectados (como %)
            'oc_db': float | None,
            'min_gap': float | None           # mínimo gap absoluto entre oc_db y ACI
        }

    Nota sobre escala: oc_db se acepta tanto en ratio (0.024) como en
    porcentaje (2.4). La detección opera en porcentaje, por lo que si
    oc_db < 0.5 se asume que está en ratio y se multiplica por 100.
    """
    if oc_db is None:
        return {
            'is_suspect': False,
            'aci_values_found': [],
            'oc_db': None,
            'min_gap': None,
        }

    # Normalizar oc_db a porcentaje para comparar con los valores del texto
    oc_pct = oc_db * 100.0 if oc_db < 0.5 else oc_db

    aci_values: list = []
    for pat in (ACI_PATTERN_ES, ACI_PATTERN_EN):
        for m in re.finditer(pat, text, re.I | re.S):
            try:
                val = float(m.group(1).replace(',', '.'))
                aci_values.append(val)
            except (ValueError, IndexError):
                continue

    if not aci_values:
        return {
            'is_suspect': False,
            'aci_values_found': [],
            'oc_db': oc_db,
            'min_gap': None,
        }

    min_gap = min(abs(oc_pct - v) for v in aci_values)
    is_suspect = min_gap < 0.1  # tolerancia 10 basis points

    return {
        'is_suspect': is_suspect,
        'aci_values_found': aci_values,
        'oc_db': oc_db,
        'min_gap': min_gap,
    }
