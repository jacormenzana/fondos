# proyecto1/core/ucits_cost_extractor.py
# -*- coding: utf-8 -*-
"""
ucits_cost_extractor.py — extractor de costes de KIID UCITS clásico.

BL-COST-4b (Sprint 2 S2-C): módulo nuevo.

Alcance mínimo estricto (PC-5 S2-C):
  - Extrae Ongoing_Charge_Recurrent del patrón "Gastos corrientes / Ongoing charges: X%"
  - Extrae Management_Fee_Pct y Transaction_Cost_Pct usando parse_costs_composition (DRY S2-A)
  - Genera una fila sintética de schedule (Horizon_Years=1.0, Is_RHP=1, Source='UCITS_DERIVED')
  - Calidad: HIGH (si OC extraído) | LOW (si no) | NONE (si no es UCITS_KIID)

Decisiones de diseño (§2 S2-C):
  - NO genera tabla de horizonte múltiple (característica PRIIPs, no UCITS).
  - _ratio_to_pct: importado desde priips_cost_extractor para evitar duplicación.
    Si hay dependencia circular, se redefine localmente con comentario DRY-SYNC.
  - Kill-switch: PRIIPS_COST_EXTRACTION_ENABLED (mismo flag que Sprint 2 global).
  - Sin efectos secundarios en import. Ninguna excepción sale al caller.

Reglas de robustez heredadas (S2-C §0.3):
  R-5: word boundary \\b en todo patrón regex.
  R-6: ventanas acotadas y lazy en todo patrón nuevo.
"""

import re
import logging
from typing import Optional, Dict, Any

# ---------------------------------------------------------------------------
# Dependencias S2-A (DRY — no reimplementar)
# ---------------------------------------------------------------------------
from cost_format_router import detect_kid_format, detect_kid_currency
from cost_table_parser  import parse_costs_composition

# ---------------------------------------------------------------------------
# _ratio_to_pct: importar desde priips_cost_extractor (DRY).
# Fallback local si el import falla (DRY-SYNC: priips_cost_extractor._ratio_to_pct).
# ---------------------------------------------------------------------------
try:
    from priips_cost_extractor import _ratio_to_pct
except ImportError:
    def _ratio_to_pct(x: Optional[float]) -> Optional[float]:   # DRY-SYNC: priips_cost_extractor
        """Convierte ratio decimal a porcentaje entero. 0.0085 → 0.85. None → None."""
        if x is None:
            return None
        return round(x * 100.0, 4)

# ---------------------------------------------------------------------------
# Config con fallback aislado (mismo patrón que priips_cost_extractor)
# ---------------------------------------------------------------------------
try:
    from config import PRIIPS_COST_EXTRACTION_ENABLED, PRIIPS_INVESTMENT_BASE
except ImportError:
    PRIIPS_COST_EXTRACTION_ENABLED: bool  = False
    PRIIPS_INVESTMENT_BASE: float         = 10000.0

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Patrón "Gastos corrientes / Ongoing charges: X%"
# R-5: word boundary implícito por la estructura del patrón (el número va al final).
# R-6: ventana acotada — el porcentaje sigue inmediatamente a la etiqueta.
# ---------------------------------------------------------------------------
_UCITS_OC_PATTERN = re.compile(
    r'(?:gastos\s+corrientes|ongoing\s+charges?)\s*[:\-]?\s*'
    r'(\d+[,\.]\d+)\s*%',
    re.IGNORECASE,
)


# ===========================================================================
# Función privada
# ===========================================================================

def _extract_ucits_oc(text: str) -> Optional[float]:
    """
    Extrae el porcentaje de gastos corrientes de un KIID UCITS.

    Returns:
        ratio decimal (ej: 0.0085 para 0.85%) si se encuentra; None si no.
    """
    m = _UCITS_OC_PATTERN.search(text)
    if not m:
        return None
    raw = m.group(1).replace(',', '.')
    try:
        return float(raw) / 100.0
    except ValueError:
        return None


# ===========================================================================
# API pública
# ===========================================================================

def extract_ucits_costs(
    text: str,
    isin: str,
    existing_oc: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Extrae costes de un KIID UCITS clásico.

    Alcance mínimo (PC-5 S2-C): Ongoing_Charge_Recurrent, Management_Fee_Pct,
    Transaction_Cost_Pct, fila de schedule sintética.

    Contrato:
    - Kill-switch: si PRIIPS_COST_EXTRACTION_ENABLED is False → retorna {}.
    - Si KID_Format != 'UCITS_KIID' → retorna solo {KID_Format,
      Cost_Extraction_Quality='NONE', _cost_schedule_rows=[]}.
    - Ninguna excepción sale al caller.
    - Escala de salida: porcentaje entero (mismo criterio que priips_cost_extractor).
    - Ongoing_Charge_Recurrent solo se devuelve si existing_oc is None (COALESCE-safe).

    Args:
        text:        texto del KIID (concatenado Raw + DLA2_Table_Text).
        isin:        ISIN del fondo (para logging).
        existing_oc: valor de Ongoing_Charge_Recurrent ya en BD (None si no existe).

    Returns:
        dict con claves posibles:
          KID_Format               str     (siempre)
          KID_Currency             str | None
          Cost_Extraction_Quality  str     ('HIGH'|'LOW'|'NONE')
          Ongoing_Charge_Recurrent float | None  (% entero; solo si existing_oc is None)
          Management_Fee_Pct       float | None  (% entero)
          Transaction_Cost_Pct     float | None  (% entero)
          _cost_schedule_rows      List[dict]    (siempre; 1 fila o [])
    """
    # 0. Kill-switch
    if not PRIIPS_COST_EXTRACTION_ENABLED:
        return {}

    out: Dict[str, Any] = {}

    try:
        # A. Formato
        fmt = detect_kid_format(text)
        out['KID_Format'] = fmt

        currency = detect_kid_currency(text)
        if currency:
            out['KID_Currency'] = currency

        # Si no es UCITS, salir limpiamente
        if fmt != 'UCITS_KIID':
            out['Cost_Extraction_Quality'] = 'NONE'
            out['_cost_schedule_rows'] = []
            return out

        # B. Ongoing Charges (patrón directo del texto KIID)
        oc_ratio = _extract_ucits_oc(text)

        # C. Composición desde S2-A (DRY: no reimplementar)
        try:
            comp = parse_costs_composition(text)
        except Exception as _e_comp:
            _log.debug("[BL-COST-4b] %s: parse_costs_composition falló (%s)", isin, _e_comp)
            comp = {}

        mgmt = comp.get('management_fee_pct')
        tran = comp.get('transaction_cost_pct')
        if mgmt is not None:
            out['Management_Fee_Pct'] = _ratio_to_pct(mgmt)
        if tran is not None:
            out['Transaction_Cost_Pct'] = _ratio_to_pct(tran)

        # D. Ongoing_Charge_Recurrent — solo si existing_oc is None (COALESCE-safe, P-3)
        if oc_ratio is not None and existing_oc is None:
            out['Ongoing_Charge_Recurrent'] = _ratio_to_pct(oc_ratio)

        # E. Fila de schedule sintética (1 fila a 1 año con OC como proxy)
        if oc_ratio is not None:
            out['_cost_schedule_rows'] = [{
                'Horizon_Years':    1.0,
                'Is_RHP':          1,
                'Source':          'UCITS_DERIVED',
                'Annual_Impact_Pct': _ratio_to_pct(oc_ratio),
                # Total_Costs_EUR y Total_Costs_Pct: no disponibles (UCITS no tiene EUR)
            }]
        else:
            out['_cost_schedule_rows'] = []

        # F. Calidad
        out['Cost_Extraction_Quality'] = 'HIGH' if oc_ratio is not None else 'LOW'

        _log.debug(
            "[BL-COST-4b] %s: UCITS OK — OC=%s, Mgmt=%s, Transac=%s, Quality=%s",
            isin,
            out.get('Ongoing_Charge_Recurrent'),
            out.get('Management_Fee_Pct'),
            out.get('Transaction_Cost_Pct'),
            out.get('Cost_Extraction_Quality'),
        )
        return out

    except Exception as exc:
        _log.warning("[BL-COST-4b] %s: fallo en extracción UCITS (%s)", isin, exc)
        out.setdefault('KID_Format', 'UNKNOWN')
        out.setdefault('Cost_Extraction_Quality', 'LOW')
        out.setdefault('_cost_schedule_rows', [])
        return out
