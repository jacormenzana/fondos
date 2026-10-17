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
    OC_ACI_TOLERANCE_PP,
    OC_RATIO_MAX,
    equals_aci,
    is_plausible_oc_ratio,
    pct_to_ratio,
    ratio_to_pct,
    resolve_ongoing_charge,
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
# resolve_ongoing_charge -- el UNICO escritor (FND-0034, 2026-10-17)
# ---------------------------------------------------------------------------
# Sustituye a guard_parser_ongoing_charge (FND-0034/FND-0095, 2026-09-27): el parser (escritor 1) corre en cada pase, incluidos los
# CACHED, y confundia el ACI con el gasto corriente. Los 4 casos reales de abajo son los ISINs que re-contaminaron la columna en vivo
# el 2026-09-26; ahora el parser es un CANDIDATO mas y la comision de gestion (la fila de gestion de un PRIIPs ES el gasto corriente
# sin costes de transaccion) gana a un parser que coincide con el ACI.

# (isin, parser_oc bugueado (ratio), mgmt%, aci_rhp%, aci_1y%, gasto corriente correcto (ratio))
_REAL_RECONTAMINATION_CASES = [
    ('FR0013439478', 0.02,  0.69, 2.0, 4.1, 0.0069),
    ('FR0011365212', 0.003, 0.40, 0.3, 0.3, 0.004),
    ('IE00BJVNH654', 0.013, 1.80, 1.3, 1.3, 0.018),
    ('IE00BJVNH761', 0.013, 1.80, 1.3, 1.3, 0.018),
]


@pytest.mark.parametrize('fmt', ['PRIIPS_KID', 'UCITS_KIID', 'UNKNOWN', None])
@pytest.mark.parametrize('isin,bad_parser,mgmt_pct,aci_rhp,aci_1y,expected', _REAL_RECONTAMINATION_CASES)
def test_a_parser_value_equal_to_the_aci_never_wins_over_the_management_fee(fmt, isin, bad_parser, mgmt_pct, aci_rhp, aci_1y, expected):
    value, source = resolve_ongoing_charge(fmt, mgmt_pct, None, bad_parser, aci_rhp, aci_1y)
    assert value == pytest.approx(expected), f'{isin}: dejo pasar {value} ({source})'
    assert source == 'management_fee'


def test_the_priips_management_line_is_the_ongoing_charge_even_when_the_parser_disagrees_cleanly():
    # TER (gestion + operacion) en el parser vs. la fila de gestion: el significado decidido es SIN costes de transaccion
    assert resolve_ongoing_charge('PRIIPS_KID', 0.63, None, 0.0136, 3.2, 6.4) == (pytest.approx(0.0063), 'management_fee')


def test_the_transaction_cost_is_never_the_ongoing_charge():
    # 29 fondos medidos el 2026-10-17 tenian el coste de operacion (Transaction_Cost_Pct) guardado como gasto corriente
    value, source = resolve_ongoing_charge('PRIIPS_KID', 1.61, None, 0.0010, 2.5, 6.7)
    assert value == pytest.approx(0.0161) and source == 'management_fee'


def test_ucits_prefers_the_documents_own_ocf_then_the_parser_then_the_management_fee():
    assert resolve_ongoing_charge('UCITS_KIID', 1.50, 0.0085, 0.0090, 2.0, 3.0) == (0.0085, 'ucits_ocf')
    assert resolve_ongoing_charge('UCITS_KIID', 1.50, None, 0.0090, 2.0, 3.0) == (0.0090, 'parser')
    assert resolve_ongoing_charge('UCITS_KIID', 1.50, None, None, 2.0, 3.0) == (pytest.approx(0.0150), 'management_fee')


def test_an_unknown_format_uses_the_parser_then_the_management_fee():
    assert resolve_ongoing_charge('UNKNOWN', 1.50, None, 0.0090, 2.0, 3.0) == (0.0090, 'parser')
    assert resolve_ongoing_charge(None, 1.50, None, None, 2.0, 3.0) == (pytest.approx(0.0150), 'management_fee')


def test_a_candidate_equal_to_either_aci_is_rejected_and_the_next_one_is_tried():
    assert equals_aci(0.0200, 2.0, 4.1) and equals_aci(0.0410, 2.0, 4.1) and not equals_aci(0.0069, 2.0, 4.1)
    # la gestion tambien puede ser el ACI (74 fondos medidos): entonces no hay candidato limpio
    assert resolve_ongoing_charge('PRIIPS_KID', 2.0, None, 0.0200, 2.0, 4.1) == (None, 'none')


def test_an_exact_zero_is_a_failed_parse_not_a_zero_cost():
    assert resolve_ongoing_charge('PRIIPS_KID', 0.0, None, 0.0, 1.0, 1.0) == (None, 'none')
    assert resolve_ongoing_charge('UNKNOWN', 0.8, None, 0.0, 1.5, 1.5) == (pytest.approx(0.0080), 'management_fee')


def test_a_scale_error_is_rejected_instead_of_written():
    # 2,08 en una columna ratio es un 208 %: el rango [0, OC_RATIO_MAX] lo aplica el resolutor (antes lo hacia el pipeline, solo con PDF)
    assert resolve_ongoing_charge('UNKNOWN', None, None, 2.08, 3.0, 3.0) == (None, 'none')
    assert resolve_ongoing_charge('UCITS_KIID', None, 0.85, None, 3.0, 3.0)[0] is None      # un porcentaje metido en la columna ratio


def test_no_candidate_means_none_so_coalesce_keeps_the_stored_value():
    assert resolve_ongoing_charge('PRIIPS_KID', None, None, None, None, None) == (None, 'none')


def test_the_contamination_tolerance_is_the_audits_tolerance():
    """Un solo predicado de contaminacion: el resolutor, el aviso del pipeline y el invariante OC_NOT_CONTAMINATED de la auditoria."""
    from shared.statistical_audit.tolerances import FLOAT_IDENTITY_TOLERANCE
    assert OC_ACI_TOLERANCE_PP == FLOAT_IDENTITY_TOLERANCE
