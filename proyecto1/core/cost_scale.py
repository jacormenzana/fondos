# proyecto1/core/cost_scale.py
# -*- coding: utf-8 -*-
"""
Única definición de la ESCALA de los atributos de coste (P1-19, P#11 / R-1).

Motivo (auditoría de distribución 2026-08-24). `Ongoing_Charge_Recurrent` tiene
TRES escritores independientes, con un orden que es determinante y que no estaba
documentado en ninguna parte:

  1. `kiid_parser._detect_ongoing_charge`  -> parsed["Ongoing_Charge"]
                                           -> pipeline.py:1473
                                           -> UPSERT con COALESCE
  2. los extractores de coste PRIIPs/UCITS -> _COST_FIELDS -> mismo UPSERT
  3. `fund_writer.correct_oc_aci_mismatch` -> UPDATE posterior SIN COALESCE
                                              (FIX-OC-WRITE-ORDER: debe ir DESPUÉS
                                               de publish_fund o el COALESCE lo pisa)

DOS de los tres publicaron defectos el 2026-08-24:
  · `correct_oc_aci_mismatch` escribía PORCENTAJE en la columna RATIO -> 5 fondos
    con un gasto corriente del 208 % (FIX-OC-SCALE).
  · `kiid_parser` capturaba la fila contigua y pisaba valores correctos en CADA
    ciclo de P1 (FIX-OC-DDF-TERMINATOR, FIX-OC-DROP-ACI-PRIORITY-2).

La causa común no es ninguno de los tres por separado: es que la convención de
escala vivía implícita en cada uno. Este módulo la hace explícita y única.

--------------------------------------------------------------------------------
CONVENCIÓN (medida sobre el universo activo, no elegida a ojo)
--------------------------------------------------------------------------------
  Ongoing_Charge_Recurrent .... RATIO DECIMAL   0.0075 == 0,75 %
  el resto de columnas *_Pct ... PORCENTAJE ENTERO   0.75 == 0,75 %

Evidencia de que el ratio es la convención correcta para el gasto corriente:
2.233 fondos activos cumplen `Ongoing_Charge_Recurrent * 100 == Management_Fee_Pct`,
relación que sólo se sostiene si el primero es ratio y el segundo porcentaje; la
media del universo es 0,0139 frente a 1,3852 de Management_Fee_Pct.

Decisión 2026-08-24: se unifica en RATIO (no en porcentaje) porque es lo que ya
tiene la mayoría de producción y lo que leen P2/P3 — convertir la minoría es el
cambio de menor radio de impacto.
"""

from typing import Optional

# Reutiliza la guarda de ligadura y la normalización de escala ya escritas y probadas para el
# escritor 2 (FIX-OC-BIND, priips_cost_extractor.py) en vez de duplicar su heurística aquí
# (P#11 / R-1). Import defensivo, PERO priips_cost_extractor.py hace a su vez imports SIN prefijo
# de sus propios vecinos de core/ (cost_format_router, etc.) -- si quien importa cost_scale.py
# (p.ej. pipeline.py) todavía no ha añadido proyecto1/core a sys.path en su propio orden de
# imports, ese import bare falla dos niveles más abajo. Se añade aquí mismo, no se asume que el
# llamador ya lo hizo (mismo patrón que fund_writer.py / BL-COST-4c-FIX-2 en pipeline.py).
try:
    from priips_cost_extractor import _resolve_oc_binding, _norm_existing_oc
except ImportError:
    import sys as _sys
    from pathlib import Path as _Path
    _core_dir = str(_Path(__file__).resolve().parent)
    if _core_dir not in _sys.path:
        _sys.path.insert(0, _core_dir)
    from priips_cost_extractor import _resolve_oc_binding, _norm_existing_oc

__all__ = [
    'OC_RATIO_MAX',
    'pct_to_ratio',
    'ratio_to_pct',
    'is_plausible_oc_ratio',
    'guard_parser_ongoing_charge',
]

# Techo de último recurso para el gasto corriente EN RATIO.
# Coherente con el techo de ACI_RHP (25 % en escala porcentual): el gasto
# corriente está contenido en el ACI, luego no puede superarlo.
# Un valor por encima de esto no es un fondo caro: es un error de escala.
# Universo activo tras las correcciones del 2026-08-24: máximo real 0,052.
OC_RATIO_MAX: float = 0.25


def pct_to_ratio(value: Optional[float]) -> Optional[float]:
    """Porcentaje entero -> ratio decimal. 0,70 % -> 0,0070."""
    if value is None:
        return None
    return value / 100.0


def ratio_to_pct(value: Optional[float], ndigits: int = 4) -> Optional[float]:
    """Ratio decimal -> porcentaje entero. 0,0070 -> 0,70 %."""
    if value is None:
        return None
    return round(value * 100.0, ndigits)


def is_plausible_oc_ratio(value: Optional[float]) -> bool:
    """
    ¿Es `value` un gasto corriente creíble EN ESCALA RATIO?

    Conservador ante None (no se puede afirmar que un hueco sea implausible).
    Su cometido es cazar errores de ESCALA, no fondos caros: 0,052 (5,2 %) pasa,
    2,08 (208 %) no.
    """
    if value is None:
        return True
    return 0.0 <= value <= OC_RATIO_MAX


def guard_parser_ongoing_charge(
    parser_oc: Optional[float],
    existing_oc_db: Optional[float],
    management_fee_pct: Optional[float],
    aci_rhp_pct: Optional[float],
    aci_1y_pct: Optional[float],
) -> Optional[float]:
    """
    FND-0034/FND-0095 (root cause, 2026-09-27): el escritor 1 (kiid_parser._detect_ongoing_charge,
    vía parsed["Ongoing_Charge"]) corre en CADA pase de P1, incluidos los CACHED. El escritor 2 (los
    extractores PRIIPs/UCITS) trae su propia guarda de ligadura ya probada (FIX-OC-PARSER-BIND en
    priips_cost_extractor.py) que repara exactamente esta contaminación — pero ese bloque entero se
    salta en CACHED (pipeline.py: `if pdf_bytes is not None or recompute_costs`, decisión de
    rendimiento, no de datos) y nunca llega a ejecutarse. Resultado: el escritor 1 escribe sin
    oposición en cada pase, y un valor ya correcto en BD (bien ligado a la gestión) puede quedar
    pisado por una mala ligadura del regex de texto plano (ACI_RHP o coste de operación en vez del
    componente de gestión) — verificado en vivo el 2026-09-26 sobre 5 ISINs (FR0011365212,
    FR0013439478, IE00BJVNH654, IE00BJVNH761, IE00BYX5MX67).

    Guarda ligera (una SELECT dedicada, sin ejecutar el extractor completo) que se antepone a esa
    escritura: solo deja pasar el valor fresco del parser cuando no hay nada fiable que proteger
    (sin valor en BD, o sin gestión con la que arbitrar) o cuando el valor fresco SÍ está bien ligado
    a la gestión. Si el valor en BD ya está bien ligado y el del parser no lo está, se descarta
    (None) para que el COALESCE del UPSERT conserve el valor de BD en vez de contaminarlo.

    Reutiliza _resolve_oc_binding/_norm_existing_oc (priips_cost_extractor.py, ya probadas) en vez
    de duplicar su heurística (P#11 / R-1) — misma tolerancia, mismo criterio de "mala ligadura".
    Todos los argumentos *_pct/*_fee_pct en PORCENTAJE ENTERO (convención del resto del modelo);
    parser_oc/existing_oc_db en la escala en que realmente llegan (parser_oc siempre ratio, por
    contrato de kiid_parser; existing_oc_db en la escala legado que _norm_existing_oc normaliza).

    Puro y conservador ante None (P-5): sin gestión con la que arbitrar, o sin valor previo que
    proteger, se deja pasar el valor del parser sin tocar — el comportamiento actual no cambia.
    """
    if parser_oc is None or existing_oc_db is None:
        return parser_oc
    if management_fee_pct is None:
        return parser_oc
    mgmt_ratio = management_fee_pct / 100.0
    aci_rhp_ratio = None if aci_rhp_pct is None else aci_rhp_pct / 100.0
    aci_1y_ratio = None if aci_1y_pct is None else aci_1y_pct / 100.0

    existing_norm = _norm_existing_oc(existing_oc_db)
    if _resolve_oc_binding(existing_norm, mgmt_ratio, aci_rhp_ratio, aci_1y_ratio) is not None:
        return parser_oc          # el valor en BD ya estaba mal ligado: nada fiable que proteger
    if _resolve_oc_binding(parser_oc, mgmt_ratio, aci_rhp_ratio, aci_1y_ratio) is not None:
        return None               # BD bien ligado, parser no está de acuerdo -> conservar BD
    return parser_oc              # ambos de acuerdo (o el parser no diverge lo bastante)
