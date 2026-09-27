# proyecto1/tests/test_cost_scale_guard.py
# -*- coding: utf-8 -*-
"""
P1-19 — la escala de coste tiene UNA sola definición, y la guarda de rango
vigila también `Ongoing_Charge_Recurrent`.

Contexto (auditoría de distribución 2026-08-24). Esa columna tiene TRES
escritores independientes y era la única de su familia que `COST-RANGE-GUARD`
NO vigilaba. Por ese hueco entraron 5 fondos con un gasto corriente del 208 %
(LU3085135567: gestión 1,98 + operación 0,10 = 2,08 escrito en una columna que
está en ratio). Ninguna guarda los miró.

Estas pruebas fijan las dos mitades del remedio:
  · la conversión de escala vive en `cost_scale` y no se reescribe a mano;
  · el techo del gasto corriente está en RATIO, no en porcentaje — la trampa
    concreta que hacía inservible copiar el 25.0 de ACI_RHP.

R-7: sólo se importa `cost_scale`; sin pipeline.py, sin core.io, sin BD.
"""

import os
import sys

import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR = os.path.normpath(os.path.join(_TESTS_DIR, '..', 'core'))
if _CORE_DIR not in sys.path:
    sys.path.insert(0, _CORE_DIR)

from cost_scale import (                     # noqa: E402
    OC_RATIO_MAX,
    guard_parser_ongoing_charge,
    is_plausible_oc_ratio,
    pct_to_ratio,
    ratio_to_pct,
)


# ---------------------------------------------------------------------------
# Conversión — una sola definición, reversible
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('pct,ratio', [
    (0.70, 0.0070), (1.98, 0.0198), (0.75, 0.0075), (2.08, 0.0208), (0.0, 0.0),
])
def test_pct_to_ratio(pct, ratio):
    assert pct_to_ratio(pct) == pytest.approx(ratio)


@pytest.mark.parametrize('pct', [0.70, 1.98, 0.75, 2.08, 4.30, 0.01])
def test_round_trip_is_stable(pct):
    assert ratio_to_pct(pct_to_ratio(pct)) == pytest.approx(pct)


def test_conversions_are_conservative_on_none():
    assert pct_to_ratio(None) is None
    assert ratio_to_pct(None) is None


# ---------------------------------------------------------------------------
# El techo caza errores de ESCALA, no fondos caros
# ---------------------------------------------------------------------------

def test_ceiling_rejects_the_values_that_actually_shipped():
    """Los 5 fondos corrompidos el 2026-08-24, en su valor real almacenado."""
    for bad in (2.08, 1.88, 1.68, 1.69, 1.40):
        assert is_plausible_oc_ratio(bad) is False, f'{bad} debió rechazarse'


def test_ceiling_accepts_real_funds_including_the_expensive_ones():
    """Máximo real del universo activo tras las correcciones: 0,052 (5,2 %)."""
    for ok in (0.0, 0.0018, 0.0075, 0.0146, 0.0198, 0.052, 0.092):
        assert is_plausible_oc_ratio(ok) is True, f'{ok} no debió rechazarse'


def test_ceiling_is_conservative_on_none():
    """Un hueco no es implausible: NULL significa no descubierto (P#10)."""
    assert is_plausible_oc_ratio(None) is True


def test_ceiling_is_expressed_in_ratio_not_percent():
    """La trampa concreta de P1-19.

    `COST-RANGE-GUARD` lista sus límites en PORCENTAJE ENTERO. Copiar ahí el
    25.0 de ACI_RHP para el gasto corriente habría dejado pasar 2,08 (208 %) y
    la guarda no habría servido de nada. El techo debe estar en ratio.
    """
    assert OC_RATIO_MAX < 1.0, 'un techo >= 1.0 estaría en escala porcentual'
    assert is_plausible_oc_ratio(2.08) is False
    # y con el techo mal puesto (25.0) el valor corrupto habría pasado:
    assert 2.08 <= 25.0


def test_management_component_survives_the_guard():
    """El destino de reparación de FIX-OC-BIND debe ser siempre plausible."""
    for mgmt_pct in (0.05, 0.23, 1.46, 1.98, 2.71, 4.30):
        assert is_plausible_oc_ratio(pct_to_ratio(mgmt_pct)) is True


# ---------------------------------------------------------------------------
# guard_parser_ongoing_charge — root cause de FND-0034/FND-0095 (2026-09-27)
# ---------------------------------------------------------------------------
# El escritor 1 (kiid_parser._detect_ongoing_charge) corre en cada pase de P1,
# incluidos los CACHED, donde el bloque completo del escritor 2 (con su propia
# guarda FIX-OC-PARSER-BIND) se salta por rendimiento. Estos 5 casos son los
# ISINs reales que re-contaminaron `Ongoing_Charge_Recurrent` en vivo el
# 2026-09-26 (valores reales leídos de BD antes/después de --recompute-costs).

# (isin, parser_oc bugueado, existing_oc BD correcto, mgmt%, aci_rhp%, aci_1y%)
_REAL_RECONTAMINATION_CASES = [
    ('FR0013439478', 0.02,   0.0069, 0.69, 2.0, 4.1),
    ('FR0011365212', 0.003,  0.004,  0.40, 0.3, 0.3),
    ('IE00BJVNH654',  0.013, 0.0198, 1.80, 1.3, 1.3),
    ('IE00BJVNH761',  0.013, 0.0198, 1.80, 1.3, 1.3),
]


@pytest.mark.parametrize('isin,bad_parser_oc,good_existing_oc,mgmt_pct,aci_rhp_pct,aci_1y_pct',
                          _REAL_RECONTAMINATION_CASES)
def test_guard_protects_a_well_bound_existing_value_from_the_parsers_bad_binding(
        isin, bad_parser_oc, good_existing_oc, mgmt_pct, aci_rhp_pct, aci_1y_pct):
    """El valor en BD ya está bien ligado a la gestión; el parser lo confunde con el ACI. La
    guarda debe descartar (None) el valor del parser para que el COALESCE preserve BD."""
    result = guard_parser_ongoing_charge(
        bad_parser_oc, good_existing_oc, mgmt_pct, aci_rhp_pct, aci_1y_pct)
    assert result is None, f'{isin}: debió proteger {good_existing_oc}, dejó pasar {result}'


def test_guard_lets_a_parser_value_through_when_it_agrees_with_a_well_bound_existing_value():
    """FR0013439478 con el parser funcionando bien (0,69 %, no 2,0 %): nada que proteger."""
    assert guard_parser_ongoing_charge(0.0069, 0.0069, 0.69, 2.0, 4.1) == 0.0069


def test_guard_lets_the_parser_value_through_when_there_is_no_existing_value_to_protect():
    """Fondo nuevo o sin OC previo: el escritor 1 es la única señal disponible."""
    assert guard_parser_ongoing_charge(0.02, None, 0.69, 2.0, 4.1) == 0.02


def test_guard_is_conservative_when_the_parser_found_nothing():
    assert guard_parser_ongoing_charge(None, 0.0069, 0.69, 2.0, 4.1) is None


def test_guard_lets_the_parser_value_through_without_a_management_fee_to_arbitrate_against():
    """Sin gestión, no hay con qué decidir (P-5): no cambia el comportamiento actual."""
    assert guard_parser_ongoing_charge(0.02, 0.0069, None, 2.0, 4.1) == 0.02


def test_guard_does_not_protect_an_existing_value_that_was_already_badly_bound():
    """Si el valor en BD YA es el ACI (mala ligadura previa), no hay nada fiable que proteger:
    se deja pasar el valor fresco del parser (podría ser una oportunidad de mejora, nunca un
    empeoramiento respecto al estado actual)."""
    # existing_oc == aci_rhp (0.02 == 0.02): BD ya mal ligado
    assert guard_parser_ongoing_charge(0.0069, 0.02, 0.69, 2.0, 4.1) == 0.0069


def test_guard_accepts_the_legacy_percent_scale_for_existing_oc_db():
    """existing_oc_db puede llegar en escala legado (>= 0.5 => porcentaje); _norm_existing_oc lo
    normaliza antes de arbitrar. 0.69 (interpretado como 0,69 %) equivale a 0.0069 ratio."""
    assert guard_parser_ongoing_charge(0.02, 0.69, 0.69, 2.0, 4.1) is None
