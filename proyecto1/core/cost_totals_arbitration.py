# proyecto1/core/cost_totals_arbitration.py
# -*- coding: utf-8 -*-
"""
Arbitraje de los TOTALES de la tabla de costes a lo largo del tiempo entre dos lecturas del mismo documento (FND-0279 / FND-0283 / FND-0284).

El texto que ve el extractor de costes es `Raw_KIID_Text + DLA2_Table_Text` (io.py, Fed_Text_For_Cost). La tabla DLA2 rescata KIDs cuyo texto plano
sale con las columnas intercaladas, pero a veces PIERDE una celda o un dígito y, al ir añadida al final, el extractor la prefiere:
  LU1734046979  texto plano "Costes totales 456 EUR 1 216 EUR"  -> tabla DLA2 "456 EUR / 216 EUR"   (se guardaba 216)
  LU1683489089  texto plano "Costes totales EUR 637 EUR 1,141"  -> la fila DLA2 solo tiene "637 EUR"  (el total a 5 años se perdía)
No vale cambiar a "solo texto plano": medido el 2026-10-17 sobre los 2.857 KIDs PRIIPs, esa lectura tiene MÁS violaciones de las invariantes del
calendario (1.294 fondos frente a 1.064) porque en muchos documentos el texto plano liga mal las columnas. Y tampoco vale cambiar la salida ENTERA
del extractor por la del texto plano: en los fondos que cambiarían, 88 pierden la comisión de gestión y 236 bajan la calidad de extracción.

Regla (la más estrecha que arregla el defecto): se toman SOLO las celdas de costes acumulados (`Total_Costs_EUR` / `Total_Costs_Pct`) de la lectura en
texto plano, horizonte a horizonte, y SOLO si el calendario resultante tiene un subconjunto PROPIO de las violaciones estructurales de la lectura actual:
nunca introduce un tipo de violación nuevo. Todo lo demás (impacto anual, gestión, ACI, calidad, moneda) sigue siendo el de la lectura actual. Los
totales viven solo en `fund_cost_schedule`; ningún escalar de `fund_master` depende de ellos.

Las invariantes estructurales son las que se cumplen sea cual sea el rendimiento del primer año que el KID asuma (por eso NO se usa
ANNUAL_EQUALS_TOTAL_AT_1Y, cuya premisa se refutó el 2026-10-17):
  EUR_DECREASING          el coste acumulado no baja al alargar el horizonte
  EUR_CONSTANT            no es igual en todos los horizontes
  LOST_TOTAL              un horizonte del calendario no se queda sin total
  ANNUAL_GT_CUMULATIVE    para horizontes de más de un año el impacto anual no supera el coste acumulado (dentro del redondeo del KID)

GUARDA DE PLAUSIBILIDAD (añadida tras medir los valores sustituidos, no solo los recuentos de violaciones): la lectura en texto plano a veces toma el IMPORTE
INVERTIDO ("Se invierten 10 000 EUR") como total: 49 de las 384 celdas habrían quedado en 10.000,0 EUR (el 100 % de la inversión) y las invariantes estructurales
no lo ven (no baja con el horizonte y supera al impacto anual). Un total sustituido tiene que ser coherente con el impacto anual DEL MISMO DOCUMENTO: la razón
total % / (impacto anual % x años) en las filas limpias del universo (1.630 filas multi-año, 2026-10-17) tiene percentil 1 = 0,31 y percentil 99 = 2,20; se acepta
[0,3 ; 2,5]. A un año la relación es total % / impacto anual %, y aquí se acepta [0,5 ; 2,0] (las diferencias legítimas de convención del KID, redondeo del ACI o
rendimiento del primer año asumido, caen en [0,95 ; 1,20]). Sin impacto anual, el total no puede pasar del 50 % de la inversión.

VERIFICACIÓN CONTRA EL DOCUMENTO (añadida al comprobar que 5 sustituciones "mejoraban" un fondo con un valor FALSO: LU1951227419 tiene "Costes totales 605 EUR 1580 EUR", la
tabla guardó 580 a 5 años y el texto plano dio 400 en todas las celdas; sustituir el 605 correcto por 400 quitaba la violación "el total baja" sin arreglar nada): un total del
texto plano solo se acepta si ese valor aparece TAL CUAL en el texto del documento junto a la etiqueta "Costes totales" (con decimales y cualquier separador de miles).
Si el valor no está en el documento no es una lectura, es un error, por mucho que deje el calendario más "ordenado".

Medido sobre el universo (2.857 KIDs PRIIPs), ver el ticket FND-0279. Puro (R-7): sin BD ni pipeline.
"""
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple

try:
    from shared.statistical_audit.tolerances import KID_ROUNDING_TOLERANCE_PP
except ImportError:                                     # shared aún no está en sys.path (mismo patrón que fund_writer.py)
    import sys as _sys
    from pathlib import Path as _Path
    _root = str(_Path(__file__).resolve().parents[2])
    if _root not in _sys.path:
        _sys.path.insert(0, _root)
    from shared.statistical_audit.tolerances import KID_ROUNDING_TOLERANCE_PP

import re

try:
    from core.priips_cost_extractor import extract_priips_costs
    from core.cost_table_parser import _TOTAL_COSTS_LABEL
except ImportError:                                     # proyecto1/core ya en sys.path
    from priips_cost_extractor import extract_priips_costs
    from cost_table_parser import _TOTAL_COSTS_LABEL

__all__ = ['structural_violations', 'plausible_total', 'total_appears_in_text', 'arbitrate_schedule_totals', 'extract_priips_costs_arbitrated']

Row = Mapping[str, Any]

# Envolvente de plausibilidad de un total sustituido (ver la cabecera del módulo): razón total % / (impacto anual % x años).
CUMULATIVE_RATIO_MIN, CUMULATIVE_RATIO_MAX = 0.3, 2.5          # horizontes de más de un año (filas limpias: p1 0,31 / p99 2,20)
ONE_YEAR_RATIO_MIN, ONE_YEAR_RATIO_MAX = 0.5, 2.0              # a un año: total % / impacto anual %
MAX_TOTAL_PCT_WITHOUT_ANNUAL = 50.0                            # sin impacto anual: nunca más de la mitad de la inversión


def plausible_total(horizon: Any, total_pct: Optional[float], annual_pct: Optional[float]) -> bool:
    """¿Es un total acumulado (en % de la inversión) coherente con el impacto anual del mismo documento? Rechaza p. ej. 10.000 EUR (el importe invertido)."""
    if total_pct is None or total_pct <= 0:
        return False
    if annual_pct is None or annual_pct <= 0 or horizon is None or horizon < 1:
        return total_pct <= MAX_TOTAL_PCT_WITHOUT_ANNUAL
    ratio = total_pct / (annual_pct * horizon)
    lo, hi = (ONE_YEAR_RATIO_MIN, ONE_YEAR_RATIO_MAX) if horizon == 1 else (CUMULATIVE_RATIO_MIN, CUMULATIVE_RATIO_MAX)
    return lo <= ratio <= hi


def structural_violations(rows: Sequence[Row]) -> FrozenSet[str]:
    """Violaciones estructurales de un calendario de costes (lista de filas con Horizon_Years / Total_Costs_EUR / Total_Costs_Pct / Annual_Impact_Pct)."""
    found = set()
    eur = [r.get('Total_Costs_EUR') for r in rows if r.get('Total_Costs_EUR') is not None]
    for r in rows:
        horizon, pct, annual = r.get('Horizon_Years'), r.get('Total_Costs_Pct'), r.get('Annual_Impact_Pct')
        if horizon is not None and horizon > 1 and annual is not None and pct is not None and annual > pct + KID_ROUNDING_TOLERANCE_PP:
            found.add('ANNUAL_GT_CUMULATIVE')
        if r.get('Total_Costs_EUR') is None and len(rows) >= 2:
            found.add('LOST_TOTAL')
    if len(eur) >= 2 and len(set(eur)) == 1:
        found.add('EUR_CONSTANT')
    if len(eur) >= 2 and any(b < a for a, b in zip(eur, eur[1:])):
        found.add('EUR_DECREASING')
    return frozenset(found)


_NUMBER_TOKEN = re.compile(r'\d{1,3}(?:[ .,\u00a0\u202f]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?')
_LABEL_WINDOW = 260


def _readings(token: str) -> List[float]:
    """Valores posibles de un número tal como está escrito: con todos los separadores como miles ('1.141' -> 1141) y con el último [.,] como decimal ('511,97' -> 511.97)."""
    plain = re.sub(r'[ .,\u00a0\u202f]', '', token)
    out = [float(plain)]
    m = re.match(r'^(.*)[.,](\d{1,2})$', token)
    if m:
        out.append(float(re.sub(r'[ .,\u00a0\u202f]', '', m.group(1)) + '.' + m.group(2)))
    return out


def total_appears_in_text(value: float, text: str) -> bool:
    """¿Aparece `value` tal cual en `text`, en los 260 caracteres siguientes a alguna etiqueta 'Costes totales'? (decimales y separadores de miles admitidos)."""
    for label in _TOTAL_COSTS_LABEL.finditer(text or ''):
        window = text[label.start(): label.start() + _LABEL_WINDOW]
        for token in _NUMBER_TOKEN.findall(window):
            if any(abs(r - value) < 0.51 for r in _readings(token)):
                return True
    return False


def _total_is_trustworthy(plain_row: Row, annual_pct: Optional[float], raw_text: Optional[str] = None) -> bool:
    """El total del texto plano es utilizable si (a) existe, (b) el importe y el porcentaje que trae SE CORRESPONDEN (porcentaje = importe / 100 sobre los 10.000 que
    asume el KID: una celda con 10000 EUR y 1,0 % es una lectura mala) y (c) el % derivado del importe es plausible frente al impacto anual."""
    eur, pct = plain_row.get('Total_Costs_EUR'), plain_row.get('Total_Costs_Pct')
    if eur is None:
        return False
    pct_from_eur = eur / 100.0
    if pct is not None and abs(pct - pct_from_eur) > 0.1:
        return False
    if raw_text is not None and not total_appears_in_text(eur, raw_text):
        return False                                                                # (d) el valor no está en el documento
    return plausible_total(plain_row.get('Horizon_Years'), pct_from_eur, annual_pct)


def arbitrate_schedule_totals(current_rows: Sequence[Row], plain_rows: Sequence[Row],
                              raw_text: Optional[str] = None) -> Tuple[List[Dict[str, Any]], List[Tuple[Any, Any, Any]]]:
    """
    (filas, celdas sustituidas). `current_rows`: calendario de la lectura actual (texto + tabla DLA2); `plain_rows`: el del texto plano.
    Devuelve las filas ACTUALES con los totales del texto plano en los horizontes donde éste tiene un total, si y solo si el resultado tiene un
    subconjunto propio de violaciones estructurales; si no, las filas actuales sin tocar. `celdas` = [(horizonte, total_EUR_antes, total_EUR_despues)].
    `raw_text`: el texto plano del documento; si se da, un total solo se acepta si su valor aparece en él junto a la etiqueta (verificación contra el documento).
    """
    current = [dict(r) for r in current_rows]
    # solo se consideran totales del texto plano COHERENTES entre sí y PLAUSIBLES frente al impacto anual de la fila actual (guarda de la cabecera)
    current_annual = {r.get('Horizon_Years'): r.get('Annual_Impact_Pct') for r in current}
    plain_by_horizon = {r.get('Horizon_Years'): r for r in plain_rows
                        if _total_is_trustworthy(r, current_annual.get(r.get('Horizon_Years'), r.get('Annual_Impact_Pct')), raw_text)}
    hybrid, replaced = [], []
    for r in current:
        p = plain_by_horizon.get(r.get('Horizon_Years'))
        new = dict(r)
        if p is not None and (p.get('Total_Costs_EUR') != r.get('Total_Costs_EUR') or p.get('Total_Costs_Pct') != r.get('Total_Costs_Pct')):
            new['Total_Costs_EUR'], new['Total_Costs_Pct'] = p.get('Total_Costs_EUR'), p.get('Total_Costs_Pct')
            replaced.append((r.get('Horizon_Years'), r.get('Total_Costs_EUR'), p.get('Total_Costs_EUR')))
        hybrid.append(new)
    if replaced and structural_violations(hybrid) < structural_violations(current):          # '<' entre conjuntos = subconjunto propio
        return hybrid, replaced
    return current, []


def extract_priips_costs_arbitrated(raw_text: str, fed_text: str, isin: str, **kw: Any) -> Tuple[Dict[str, Any], List[Tuple[Any, Any, Any]]]:
    """
    `extract_priips_costs` sobre el texto que ve hoy el extractor (`fed_text`) y, solo si la tabla DLA2 está añadida (`fed_text != raw_text`), una segunda
    lectura sobre el texto plano para arbitrar los totales. Devuelve (salida del extractor con el calendario arbitrado, celdas sustituidas).
    """
    out = extract_priips_costs(text=fed_text, isin=isin, **kw)
    if not out or not fed_text or fed_text == raw_text or not raw_text:
        return out, []
    rows = out.get('_cost_schedule_rows') or []
    if not rows:
        return out, []
    plain = extract_priips_costs(text=raw_text, isin=isin, **kw) or {}
    arbitrated, replaced = arbitrate_schedule_totals(rows, plain.get('_cost_schedule_rows') or [], raw_text)
    if replaced:
        out['_cost_schedule_rows'] = arbitrated
    return out, replaced
