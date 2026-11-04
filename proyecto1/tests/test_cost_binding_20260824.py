# proyecto1/tests/test_cost_binding_20260824.py
# -*- coding: utf-8 -*-
"""
Regresiones de la auditoría de distribución de atributos de coste (2026-08-24).

Cubre tres defectos detectados por los bloques 2 y 5-6 de la auditoría:

  FIX-OC-BIND             el gasto corriente ligado a una fila que no es la de
                          gestión (operación/salida, o el propio ACI).
  FIX-SCHEDULE-PCT-GATE   Total_Costs_Pct derivado de un importe en otra divisa
                          o sangrado, publicado como si fuese un porcentaje real.
  FIX-SCHEDULE-ANNUAL-ANCHOR  un total derivado por posición sobrescribía un
                          impacto anual respaldado por el ancla de etiqueta.
  FIX-SCHEDULE-IS-RHP-1Y  Is_RHP=1 puesto sobre la fila de 1 año, que lleva el
                          ACI_1Y y no el ACI_RHP.

R-7: se prueban funciones del extractor directamente; no importa pipeline.py ni
core.io. Todos los casos son sintéticos — sin acceso a disco ni a BD.
"""

import os
import sys

import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR = os.path.normpath(os.path.join(_TESTS_DIR, '..', 'core'))
if _CORE_DIR not in sys.path:
    sys.path.insert(0, _CORE_DIR)

from priips_cost_extractor import (          # noqa: E402
    _build_schedule_rows,
    _pct_is_plausible,
    _resolve_oc_binding,
)


# ===========================================================================
# FIX-OC-BIND — a qué fila quedó ligado el gasto corriente
# ===========================================================================

def test_oc_bound_to_transaction_row_is_detected():
    """LU0823401905: OC 0,08 % == operación, gestión 2,71 %, ACI_1Y 5,79 %."""
    assert _resolve_oc_binding(0.0008, 0.0271, 0.0361, 0.0579) == \
        'componente menor que la gestión'


def test_oc_bound_to_aci_rhp_is_detected():
    """Comportamiento previo (FIX-OC-ACI-REPAIR) preservado."""
    assert _resolve_oc_binding(0.018, 0.0063, 0.018, 0.038) == 'ACI'


def test_oc_bound_to_aci_1y_is_detected():
    """Extensión nueva: el ACI de 1 año también delata la mala ligadura."""
    assert _resolve_oc_binding(0.014, 0.009, 0.009_5, 0.014) == 'ACI'


def test_correct_oc_is_never_touched():
    """Contraste sano: 2.207 fondos tienen OC == gestión. No deben moverse."""
    assert _resolve_oc_binding(0.0045, 0.0045, 0.006, 0.0060) is None
    assert _resolve_oc_binding(0.005, 0.005, 0.0234, 0.0234) is None


def test_oc_slightly_above_mgmt_is_not_touched():
    """El OCF puede superar a la gestión (incluye administración/depositaría)."""
    assert _resolve_oc_binding(0.0075, 0.0063, 0.018, 0.038) is None


def test_no_repair_without_mgmt():
    """Sin comisión de gestión no hay destino de reparación."""
    assert _resolve_oc_binding(0.0008, None, 0.0361, 0.0579) is None


def test_no_repair_when_mgmt_is_not_corroborated():
    """Si el ACI es MENOR que la gestión, la sospechosa es la gestión."""
    assert _resolve_oc_binding(0.0008, 0.0271, 0.0100, 0.0120) is None


def test_no_repair_without_any_aci():
    """Sin ACI que respalde la gestión no se toca nada (conservador)."""
    assert _resolve_oc_binding(0.0008, 0.0271, None, None) is None


# ===========================================================================
# FIX-SCHEDULE-PCT-GATE — el porcentaje total derivado debe ser plausible
# ===========================================================================

def test_pct_plausible_accepts_normal_cumulative_cost():
    # 15,77 % acumulado a 5 años = 3,15 % anual implícito → plausible
    assert _pct_is_plausible(15.77, 5.0) is True


def test_pct_plausible_accepts_one_year_normal():
    assert _pct_is_plausible(6.96, 1.0) is True


def test_pct_plausible_rejects_foreign_currency_amount():
    # LU0115096736: importe en JPY sobre base 2.000.000 leído como EUR/10.000
    assert _pct_is_plausible(1532.0, 1.0) is False
    assert _pct_is_plausible(5334.62, 5.0) is False


def test_pct_plausible_rejects_scenario_bleed():
    # ES0175404013: 149,5 % a 5 años = 29,9 % anual implícito → por encima del techo
    assert _pct_is_plausible(149.5, 5.0) is False


def test_pct_plausible_is_conservative_on_none():
    assert _pct_is_plausible(None, 5.0) is False
    assert _pct_is_plausible(10.0, None) is False
    assert _pct_is_plausible(-1.0, 5.0) is False


def test_schedule_omits_implausible_total_but_keeps_annual():
    """El anual sigue publicándose aunque el total se descarte (P#10)."""
    rows = [
        {'horizon_years': 1.0, 'total_cost_eur': 153200.0, 'aci_pct': 0.077},
        {'horizon_years': 5.0, 'total_cost_eur': 533462.0, 'aci_pct': 0.038},
    ]
    out = _build_schedule_rows(rows, 5.0, 'TEST_JPY')
    by_h = {r['Horizon_Years']: r for r in out}
    assert 'Total_Costs_Pct' not in by_h[1.0]
    assert 'Total_Costs_Pct' not in by_h[5.0]
    assert by_h[1.0]['Annual_Impact_Pct'] == 7.7
    assert by_h[5.0]['Annual_Impact_Pct'] == 3.8
    # el importe crudo se conserva: no se pierde ningún dato de coste
    assert by_h[1.0]['Total_Costs_EUR'] == 153200.0


def test_schedule_keeps_plausible_total():
    """Control: una fila sana conserva su Total_Costs_Pct intacto."""
    rows = [
        {'horizon_years': 1.0, 'total_cost_eur': 696.0, 'aci_pct': 0.07},
        {'horizon_years': 5.0, 'total_cost_eur': 1577.0, 'aci_pct': 0.03},
    ]
    out = _build_schedule_rows(rows, 5.0, 'TEST_OK')
    by_h = {r['Horizon_Years']: r for r in out}
    assert by_h[1.0]['Total_Costs_Pct'] == 6.96
    assert by_h[5.0]['Total_Costs_Pct'] == 15.77


# ===========================================================================
# FIX-SCHEDULE-PCT-BASE — la base de inversión se lee del KID
# ===========================================================================

def test_schedule_uses_declared_investment_base():
    """LU2867168812: base 100.000 → 3.308 EUR son 3,308 %, no 33,08 %."""
    text = 'Ejemplo de inversión: 100.000 EUR'
    rows = [{'horizon_years': 8.0, 'total_cost_eur': 3308.0, 'aci_pct': 0.0415}]
    out = _build_schedule_rows(rows, 8.0, 'TEST_BASE', text)
    assert out[0]['Total_Costs_Pct'] == 3.308


def test_schedule_falls_back_to_standard_base_without_declaration():
    """Sin declaración de base se conserva el comportamiento previo (10.000)."""
    rows = [{'horizon_years': 1.0, 'total_cost_eur': 696.0, 'aci_pct': 0.07}]
    out = _build_schedule_rows(rows, 1.0, 'TEST_NOBASE', 'texto sin base')
    assert out[0]['Total_Costs_Pct'] == 6.96


# ===========================================================================
# FIX-SCHEDULE-ANNUAL-ANCHOR — la evidencia gana a la magnitud
# ===========================================================================

def test_label_anchored_annual_survives_a_wrong_total():
    """LU0247697476: ancla 7,80 % frente a total derivado 3,40 % → gana el ancla."""
    rows = [{'horizon_years': 1.0, 'total_cost_eur': 340.0, 'aci_pct': 0.078}]
    out = _build_schedule_rows(rows, 5.0, 'TEST_ANCHOR', None, 7.8)
    assert out[0]['Annual_Impact_Pct'] == 7.8
    assert 'Total_Costs_Pct' not in out[0]


def test_without_anchor_the_previous_arbitration_still_applies():
    """Sin ancla, un anual mayor que el total sigue corrigiéndose al derivado."""
    rows = [{'horizon_years': 1.0, 'total_cost_eur': 340.0, 'aci_pct': 0.078}]
    out = _build_schedule_rows(rows, 5.0, 'TEST_NOANCHOR')
    assert out[0]['Annual_Impact_Pct'] == 3.4


def test_kid_rounding_slack_leaves_both_figures_intact():
    """FR0010135103: el KID publica 612 EUR (6,12 %) y 6,21 % anual.

    Esa holgura es del documento — 1.835 de las 2.030 filas de 1 año del corpus
    caen por debajo de 0,5pp. Ninguno de los dos valores debe tocarse.
    """
    rows = [{'horizon_years': 1.0, 'total_cost_eur': 612.0, 'aci_pct': 0.0621}]
    out = _build_schedule_rows(rows, 3.0, 'TEST_SLACK', None, 6.21)
    assert out[0]['Total_Costs_Pct'] == 6.12
    assert out[0]['Annual_Impact_Pct'] == 6.21


def test_kid_rounding_slack_intact_without_anchor():
    """La misma holgura tampoco debe disparar la regla de magnitud."""
    rows = [{'horizon_years': 1.0, 'total_cost_eur': 205.0, 'aci_pct': 0.021}]
    out = _build_schedule_rows(rows, 5.0, 'TEST_SLACK2')
    assert out[0]['Total_Costs_Pct'] == 2.05
    assert out[0]['Annual_Impact_Pct'] == 2.1


def test_anchor_does_not_fire_when_annual_and_total_agree():
    """Control: si concuerdan, el total se publica igualmente."""
    rows = [{'horizon_years': 1.0, 'total_cost_eur': 780.0, 'aci_pct': 0.078}]
    out = _build_schedule_rows(rows, 5.0, 'TEST_AGREE', None, 7.8)
    assert out[0]['Annual_Impact_Pct'] == 7.8
    assert out[0]['Total_Costs_Pct'] == 7.8


# ===========================================================================
# FIX-SCHEDULE-ANNUAL-GT-TOTAL — no actuar con un total no creíble
# ===========================================================================

def test_annual_survives_when_total_is_absurdly_small():
    """Un total de 5 EUR sobre 10.000 no puede arbitrar sobre el anual."""
    rows = [{'horizon_years': 5.0, 'total_cost_eur': 5.0, 'aci_pct': 0.0361}]
    out = _build_schedule_rows(rows, 5.0, 'TEST_TINY')
    assert out[0]['Annual_Impact_Pct'] == 3.61


def test_annual_at_long_horizon_not_discarded_by_implausible_total():
    """Antes se DESCARTABA el anual correcto a horizontes > 1 año."""
    rows = [{'horizon_years': 5.0, 'total_cost_eur': 533462.0, 'aci_pct': 0.038}]
    out = _build_schedule_rows(rows, 5.0, 'TEST_LONG')
    assert out[0]['Annual_Impact_Pct'] == 3.8


# ===========================================================================
# FIX-SCHEDULE-ANNUAL-BLEED — el mismo dato, el mismo techo en TODOS sus destinos
#
# La incidencia anual llega a tres sitios: fund_master.ACI_1Y, fund_master.ACI_RHP
# y fund_cost_schedule.Annual_Impact_Pct. Los dos primeros pasaban por el ancla de
# etiqueta con techo _ACI_ANCHOR_MAX_PCT; el tercero se escribía con el valor
# POSICIONAL, sin techo. Resultado: 43 filas activas con la rentabilidad del
# escenario de tensión (signo comido) publicada como coste — el mismo defecto
# corregido el 2026-08-23 en fund_master y no en fund_cost_schedule.
#
#   LU1006075656  fila 1a = 48,63 %  frente a ACI_1Y publicado 1,9 %
#   ES0175404013  fila 1a = 77,24 %  frente a ACI_1Y publicado 1,0 %
# ===========================================================================

def test_scenario_bleed_replaced_by_label_anchored_aci():
    """LU1006075656: 48,63 % es la rentabilidad del escenario, no un coste."""
    rows = [{'horizon_years': 1.0, 'total_cost_eur': 5140.0, 'aci_pct': 0.4863}]
    out = _build_schedule_rows(rows, 5.0, 'TEST_BLEED', None, 1.9)
    assert out[0]['Annual_Impact_Pct'] == 1.9


def test_scenario_bleed_omitted_when_no_anchor_available():
    """Sin ancla de etiqueta se omite la clave (P#10), nunca el escenario."""
    rows = [{'horizon_years': 1.0, 'total_cost_eur': 14950.0, 'aci_pct': 0.7724}]
    out = _build_schedule_rows(rows, 5.0, 'TEST_BLEED_NOANCHOR')
    assert 'Annual_Impact_Pct' not in out[0]


def test_bleed_guard_uses_the_same_ceiling_as_the_master_aci():
    """El techo es el que gobierna el dato en su otro destino, no uno nuevo.

    El importe en EUR se elige COHERENTE con el anual (1.600 sobre base 10.000 =
    16 %), para que la comprobación mida sólo la guarda de sangrado y no dispare
    de rebote FIX-SCHEDULE-ANNUAL-GT-TOTAL.
    """
    from priips_cost_extractor import _ACI_ANCHOR_MAX_PCT
    just_under = (_ACI_ANCHOR_MAX_PCT - 0.5) / 100.0
    just_over = (_ACI_ANCHOR_MAX_PCT + 0.5) / 100.0
    kept = _build_schedule_rows(
        [{'horizon_years': 1.0, 'total_cost_eur': 1600.0, 'aci_pct': just_under}],
        5.0, 'TEST_UNDER')
    assert kept[0]['Annual_Impact_Pct'] == pytest.approx(_ACI_ANCHOR_MAX_PCT - 0.5)
    dropped = _build_schedule_rows(
        [{'horizon_years': 1.0, 'total_cost_eur': 1600.0, 'aci_pct': just_over}],
        5.0, 'TEST_OVER')
    assert 'Annual_Impact_Pct' not in dropped[0]


def test_plausible_annual_is_untouched_by_the_bleed_guard():
    """Control: los costes anuales normales no se tocan."""
    for aci, expected in ((0.019, 1.9), (0.0514, 5.14), (0.001, 0.1)):
        out = _build_schedule_rows(
            [{'horizon_years': 1.0, 'total_cost_eur': 500.0, 'aci_pct': aci}],
            5.0, 'TEST_OK', None, 99.0)
        assert out[0]['Annual_Impact_Pct'] == pytest.approx(expected)


def test_rhp_row_bleed_without_anchor_is_omitted_not_published():
    """A horizonte RHP no hay ancla de 1 año: se omite, no se publica el escenario."""
    rows = [{'horizon_years': 5.0, 'total_cost_eur': 5140.0, 'aci_pct': 0.4863}]
    out = _build_schedule_rows(rows, 5.0, 'TEST_BLEED_RHP', None, 1.9)
    assert 'Annual_Impact_Pct' not in out[0]
