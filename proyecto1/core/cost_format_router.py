# proyecto1/core/cost_format_router.py
# -*- coding: utf-8 -*-
"""
cost_format_router.py — clasificador de formato KID y detector de moneda base.

BL-COST-3 (Sprint 2 S2-A): módulo nuevo.
BL-COST-3-FIX (Sprint 2 S2-D): patron con espacio opcional en señales PRIIPs multipalabra ES.
    Causa raíz: PDFs JPMorgan/HSBC con texto pegado sin espacios (pdfplumber
    no inserta espacios entre palabras en ciertos layouts de dos columnas).
    Afectados confirmados: LU0070177588, LU1873127366, LU0210536867, LU0213962813.
    Fix: espacio opcional en lugar de espacio literal en señales ES multipalabra.

Propósito:
    Determina si un documento KID es PRIIPs KID o UCITS KIID a partir del
    texto extraído, y detecta la moneda base usada en la tabla de costes.
    Es la puerta de entrada del pipeline de extracción de costes: el resultado
    de detect_kid_format() decide qué extractor se invoca (priips vs. ucits).

Funciones exportadas:
    detect_kid_format(text)   -> 'PRIIPS_KID' | 'UCITS_KIID' | 'UNKNOWN'
    detect_kid_currency(text) -> código ISO-4217 en mayúsculas | None

Criterio de clasificación D-S2-1 (relajado respecto a BL-COST-1):
    - UCITS tiene prioridad: sus señales son muy específicas y raramente
      aparecen en documentos PRIIPs.
    - Para PRIIPs basta 1 señal + 1 señal Cat.3 cuando no hay señales UCITS.

Nota sobre imports / DRY:
    Los patrones de señales se duplican aquí desde cost_format_signals.py
    porque el import cruzado core/ → scripts/diag/ requeriría manipular
    sys.path o __init__.py (fuera de scope). Los patrones son datos, no
    lógica. Si se modifican en cost_format_signals.py, sincronizar también
    aquí (buscar el comentario "# DRY-SYNC: cost_format_signals.py").
"""

import re
from typing import Optional


# ======================================================================
# Patrones de señales KID
# DRY-SYNC: cost_format_signals.py — PRIIPS_SIGNALS_STRONG / UCITS_SIGNALS_STRONG
# ======================================================================

# Señales fuertes PRIIPs (secciones exclusivas del formato KID PRIIPs)
# \s* en señales multipalabra ES: cubre PDFs con texto pegado sin espacios
# (causa raíz confirmada en LU0070177588, LU1873127366, LU0210536867, LU0213962813).
# Señales EN no requieren \s*: los PDFs EN analizados no presentan este problema.
_PRIIPS_SIGNALS: list = [
    r'documento\s*de\s*datos\s*fundamentales',
    r'key information document',
    r'composici[óo]n\s*de\s*los\s*costes?',
    r'composition of costs',
    r'costes?\s*a\s*lo\s*largo\s*del\s*tiempo',
    r'costs over time',
    r'incidencia\s*anual\s*de\s*los\s*costes?',
    r'annual cost impact',
    r'escenarios?\s*de\s*rentabilidad',
    r'performance scenarios',
    r'per[ií]odo\s*de\s*mantenimiento\s*recomendado',
    r'recommended holding period',
]

# Señales fuertes UCITS (exclusivas del formato KIID UCITS clásico)
_UCITS_SIGNALS: list = [
    r'datos fundamentales para el inversor',
    r'key investor information',
    r'gastos corrientes',
    r'entry charge.{0,80}exit charge.{0,80}ongoing charge',
    r'comisi[óo]n\s+de\s+entrada.{0,200}comisi[óo]n\s+de\s+salida'
    r'.{0,200}comisi[óo]n\s+de\s+gesti[óo]n',
]

# Señales de escenarios Cat.3 PRIIPs (refuerzan clasificación PRIIPs)
_CAT3_SIGNALS: list = [
    r'escenarios? de rentabilidad',
    r'performance scenarios',
    r'escenario\s+(?:favorable|desfavorable|moderado|de\s+tensi[oó]n)',
    r'(?:favourable|unfavourable|moderate|stress)\s+scenario',
]

# Patrón principal: "10.000 EUR" / "10,000 USD" etc. en tabla de costes PRIIPs
# Cubre separadores de miles tanto con punto (ES) como con coma (EN).
_INVESTMENT_BASE_PATTERN = re.compile(
    r'10[.,]000\s*(EUR|USD|GBP|CHF|SEK|NOK|DKK|PLN|CZK)',
    re.IGNORECASE,
)

# Patrón secundario: "Inversión: 10.000 EUR" o "Investment: 10,000 USD"
# Usado como fallback cuando el patrón principal no encuentra nada.
_INVESTMENT_LABEL_PATTERN = re.compile(
    r'(?:inversi[oó]n|investment)\s*[:\-]?\s*10[.,]000\s*(EUR|USD|GBP|CHF|SEK|NOK|DKK|PLN|CZK)',
    re.IGNORECASE,
)


# ======================================================================
# Funciones de conteo internas (puras, sin efectos secundarios)
# ======================================================================

def _count_priips_signals(text: str) -> int:
    """Cuenta cuántas señales PRIIPs fuertes aparecen en el texto."""
    return sum(1 for p in _PRIIPS_SIGNALS if re.search(p, text, re.I | re.S))


def _count_ucits_signals(text: str) -> int:
    """Cuenta cuántas señales UCITS fuertes aparecen en el texto."""
    return sum(1 for p in _UCITS_SIGNALS if re.search(p, text, re.I | re.S))


def _count_cat3_signals(text: str) -> int:
    """Cuenta cuántas señales de escenarios Cat.3 aparecen en el texto."""
    return sum(1 for p in _CAT3_SIGNALS if re.search(p, text, re.I | re.S))


# ======================================================================
# API pública
# ======================================================================

def detect_kid_format(text: str) -> str:
    """
    Clasifica el formato del KID a partir del texto extraído.

    Criterio D-S2-1 (relajado respecto al criterio original BL-COST-1):
        1. UCITS tiene prioridad — sus señales son muy específicas.
           >= 2 señales UCITS  → 'UCITS_KIID'
        2. PRIIPs con señales abundantes:
           >= 3 señales PRIIPs → 'PRIIPS_KID'
        3. PRIIPs con señales escasas pero con refuerzo Cat.3:
           >= 1 señal PRIIPs + 0 señales UCITS + >= 1 señal Cat.3 → 'PRIIPS_KID'
        4. Ninguna condición cumplida → 'UNKNOWN'

    Args:
        text: texto plano extraído del KID/KIID (Raw_KIID_Text + DLA2_Table_Text).
              Puede contener separador DLA2 '|||'.

    Returns:
        'PRIIPS_KID' | 'UCITS_KIID' | 'UNKNOWN'
    """
    if not text or not text.strip():
        return 'UNKNOWN'

    priips_count = _count_priips_signals(text)
    ucits_count  = _count_ucits_signals(text)
    cat3_count   = _count_cat3_signals(text)

    # UCITS tiene prioridad (señales muy específicas)
    if ucits_count >= 2:
        return 'UCITS_KIID'

    # PRIIPs con señales abundantes
    if priips_count >= 3:
        return 'PRIIPS_KID'

    # PRIIPs con señales escasas pero reforzadas por escenarios Cat.3
    if priips_count >= 1 and ucits_count == 0 and cat3_count >= 1:
        return 'PRIIPS_KID'

    return 'UNKNOWN'


def detect_kid_currency(text: str) -> Optional[str]:
    """
    Detecta la moneda base de la tabla de costes del KID.

    Busca la base de inversión estándar PRIIPs (10.000 EUR, 10,000 USD, etc.)
    que aparece en la sección de costes del documento. Primero aplica el
    patrón directo; si no hay resultado, aplica el patrón con etiqueta
    "Inversión:" / "Investment:".

    Args:
        text: texto plano extraído del KID.

    Returns:
        Código ISO-4217 en mayúsculas ('EUR', 'USD', 'GBP', etc.)
        o None si no se detecta.
    """
    if not text:
        return None

    m = _INVESTMENT_BASE_PATTERN.search(text)
    if m:
        return m.group(1).upper()

    m = _INVESTMENT_LABEL_PATTERN.search(text)
    if m:
        return m.group(1).upper()

    return None


def get_format_signals_detail(text: str) -> dict:
    """
    Devuelve el detalle de señales para diagnóstico y logging.

    No forma parte de la interfaz pública principal, pero es útil
    para smoke tests y diagnósticos del pipeline.

    Returns:
        {
            'priips_count': int,
            'ucits_count':  int,
            'cat3_count':   int,
            'format':       str,   # resultado de detect_kid_format
            'currency':     str|None
        }
    """
    return {
        'priips_count': _count_priips_signals(text),
        'ucits_count':  _count_ucits_signals(text),
        'cat3_count':   _count_cat3_signals(text),
        'format':       detect_kid_format(text),
        'currency':     detect_kid_currency(text),
    }
