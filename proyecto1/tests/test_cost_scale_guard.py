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
