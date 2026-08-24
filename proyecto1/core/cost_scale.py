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
  3. `sqlite_writer.correct_oc_aci_mismatch` -> UPDATE posterior SIN COALESCE
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

__all__ = [
    'OC_RATIO_MAX',
    'pct_to_ratio',
    'ratio_to_pct',
    'is_plausible_oc_ratio',
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
