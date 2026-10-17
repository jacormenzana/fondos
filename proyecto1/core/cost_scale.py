# proyecto1/core/cost_scale.py
# -*- coding: utf-8 -*-
"""
Única definición de la ESCALA de los atributos de coste (P1-19, P#11 / R-1) y ÚNICO ESCRITOR del gasto
corriente (FND-0034, 2026-10-17).

Historia. `Ongoing_Charge_Recurrent` tuvo TRES escritores independientes (auditoría de distribución 2026-08-24):

  1. `kiid_parser._detect_ongoing_charge`  -> parsed["Ongoing_Charge"] -> UPSERT con COALESCE, en CADA pase (también CACHED)
  2. los extractores de coste PRIIPs/UCITS -> _COST_FIELDS -> mismo UPSERT, solo con PDF o --recompute-costs
  3. `fund_writer.correct_oc_aci_mismatch` -> UPDATE posterior SIN COALESCE (FIX-OC-WRITE-ORDER)

y cada uno publicó defectos: porcentaje en la columna ratio (208 %), la fila contigua o el ACI copiados como gasto corriente en cada
ciclo (801 fondos con OC x 100 == ACI_RHP el 2026-08-23), el coste de operación como gasto corriente (29 fondos medidos el
2026-10-17). Las mitigaciones se apilaron durante dos meses (FIX-OC-WRITE-ORDER, FIX-OC-SCALE, FIX-OC-BIND, FIX-OC-PARSER-BIND,
FIX-OC-PARSER-GUARD, el PASO 2b del lanzador que recomputaba los fondos re-contaminados) y ninguna quitaba la causa: que había tres
escritores con tres significados y un orden implícito.

Diseño actual (FND-0034): los productores PROPONEN candidatos y NO escriben (el parser, el extractor UCITS, la comisión de gestión
ya extraída); `resolve_ongoing_charge` los arbitra UNA vez por fondo y por pase, justo antes de `publish_fund`, y el UPSERT con
COALESCE es la única escritura. Sin candidato limpio devuelve None y COALESCE conserva el valor guardado: nunca se escribe algo peor.

SIGNIFICADO (decisión del owner, 2026-10-17): gasto corriente SIN costes de transacción, como RATIO.
  PRIIPs KID .... la fila "comisiones de gestión y otros costes administrativos o de funcionamiento" == Management_Fee_Pct / 100
  UCITS KIID .... la cifra de gastos corrientes (OCF) del propio documento
  (el coste de operación vive en Transaction_Cost_Pct; el ACI en ACI_1Y / ACI_RHP: ninguno es el gasto corriente)

--------------------------------------------------------------------------------
CONVENCIÓN DE ESCALA (medida sobre el universo activo, no elegida a ojo)
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

Módulo puro y sin dependencias del pipeline (R-7).
"""

from typing import Optional, Tuple

__all__ = [
    'OC_RATIO_MAX',
    'OC_ACI_TOLERANCE_PP',
    'pct_to_ratio',
    'ratio_to_pct',
    'is_plausible_oc_ratio',
    'equals_aci',
    'resolve_ongoing_charge',
]

# Techo de último recurso para el gasto corriente EN RATIO.
# Coherente con el techo de ACI_RHP (25 % en escala porcentual): el gasto
# corriente está contenido en el ACI, luego no puede superarlo.
# Un valor por encima de esto no es un fondo caro: es un error de escala.
# Universo activo tras las correcciones del 2026-08-24: máximo real 0,052.
OC_RATIO_MAX: float = 0.25

# Un gasto corriente que coincide con el ACI (1 año o RHP) dentro de esta distancia, en PUNTOS PORCENTUALES, no es un gasto corriente:
# el ACI incluye la entrada amortizada y por construcción es mayor (el OC = ACI_RHP es la contaminación de FND-0034). Es la MISMA
# tolerancia que el invariante OC_NOT_CONTAMINATED de la auditoría (shared/statistical_audit/tolerances.FLOAT_IDENTITY_TOLERANCE;
# un test las mantiene iguales): antes la guarda del pipeline, el lanzador, el extractor y la auditoría usaban cuatro tolerancias.
OC_ACI_TOLERANCE_PP: float = 0.0001


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


def equals_aci(oc_ratio: Optional[float], aci_rhp_pct: Optional[float], aci_1y_pct: Optional[float],
               tol_pp: float = OC_ACI_TOLERANCE_PP) -> bool:
    """
    ¿Es el gasto corriente (RATIO) el ACI (PORCENTAJE ENTERO) de 1 año o del RHP? Puro; None nunca coincide.
    Predicado único de la contaminación de FND-0034 (resolutor, aviso del pipeline y, por espejo, la auditoría).
    """
    if oc_ratio is None:
        return False
    return any(a is not None and abs(oc_ratio * 100.0 - a) <= tol_pp for a in (aci_rhp_pct, aci_1y_pct))


def resolve_ongoing_charge(
    kid_format: Optional[str],
    management_fee_pct: Optional[float],
    ucits_oc_ratio: Optional[float],
    parser_oc_ratio: Optional[float],
    aci_rhp_pct: Optional[float],
    aci_1y_pct: Optional[float],
) -> Tuple[Optional[float], str]:
    """
    FND-0034: el ÚNICO valor de `Ongoing_Charge_Recurrent` que un pase de P1 puede escribir. Devuelve (ratio | None, fuente).

    Candidatos, por orden de fiabilidad según el formato del documento (`cost_format_router.detect_kid_format`):
      'PRIIPS_KID' ..... gestión -> parser          (la fila de gestión ES el gasto corriente sin operación)
      'UCITS_KIID' ..... OCF del documento -> parser -> gestión
      otro / None ...... parser -> gestión
    Un candidato se descarta si (a) está fuera de [0, OC_RATIO_MAX] (error de escala), (b) es EXACTAMENTE 0 (un cero es un parseo
    fallido mientras exista otra fuente: una comisión de gestión de 0 % legítima la lleva ya Management_Fee_Pct), o (c) coincide con
    el ACI (`equals_aci`). Sin candidato limpio -> (None, 'none'): el UPSERT con COALESCE conserva el valor guardado.

    Argumentos *_pct en PORCENTAJE ENTERO; *_ratio en RATIO. Puro y sin acceso a BD.
    """
    mgmt = pct_to_ratio(management_fee_pct)
    if kid_format == 'PRIIPS_KID':
        order = (('management_fee', mgmt), ('parser', parser_oc_ratio))
    elif kid_format == 'UCITS_KIID':
        order = (('ucits_ocf', ucits_oc_ratio), ('parser', parser_oc_ratio), ('management_fee', mgmt))
    else:
        order = (('parser', parser_oc_ratio), ('management_fee', mgmt))
    for source, value in order:
        if value is None or not (0.0 <= value <= OC_RATIO_MAX) or value == 0.0:
            continue
        if equals_aci(value, aci_rhp_pct, aci_1y_pct):
            continue
        return value, source
    return None, 'none'
