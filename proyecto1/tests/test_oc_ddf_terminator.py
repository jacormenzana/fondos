# proyecto1/tests/test_oc_ddf_terminator.py
# -*- coding: utf-8 -*-
"""
FIX-OC-DDF-TERMINATOR — la ventana del gasto corriente no puede cruzar a otra
fila de coste.

En maquetas de columna partida la etiqueta "Comisiones de gestión y otros costes
administrativos y de funcionamiento" queda HUÉRFANA: su valor no aparece detrás.
El siguiente número del flujo pertenece a "Costes de operación". Los separadores
sin acotar (`[^\\.]{0,150}` en la prioridad 0.5, `[\\s\\S]{0,100}?` en la
prioridad 5) lo capturaban y lo publicaban como gasto corriente.

Detectado en el ensayo del 2026-08-24 sobre copia desechable (universo completo):
    LU0423950053  gestión 0,23 %  ->  devolvía 0,01 % (la operación)
    IE00BYQQ1F19  gestión 0,01 %  ->  devolvía 0,46 % (la operación)

El valor viajaba por parsed["Ongoing_Charge"] -> pipeline.py:1473 -> UPSERT con
COALESCE, pisando un valor correcto en CADA ciclo de P1 — con independencia de
--recompute-costs y de los extractores de coste.

Resultado correcto cuando la etiqueta no lleva valor: None (P#10, NULL = no
descubierto). El componente de gestión sigue publicándose por Management_Fee_Pct.

R-7: sólo se importa kiid_parser; sin pipeline.py, sin core.io, sin BD.
"""

import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_P1_DIR = os.path.normpath(os.path.join(_TESTS_DIR, '..'))
_CORE_DIR = os.path.join(_P1_DIR, 'core')
for _p in (_P1_DIR, _CORE_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.kiid_parser import (                    # noqa: E402
    _OC_DDF_MGMT_RE,
    _OC_DEL_VALOR_RE,
    _detect_ongoing_charge,
)

# Maquetas reales, transcritas de los dos KIDs afectados.
LU0423950053 = (
    "El porcentaje de gastos corrientes se basa en gastos\n"
    "históricos calculados a 30/11/2024.\n"
    "Comisiones de gestión y otros costes\n"
    "administrativos y de funcionamiento\n"
    "Costes de operación 0,01% del valor de su inversión al año. Se trata de "
    "una estimación de los costes en que incurrimos al comprar y vender.\n"
)
IE00BYQQ1F19 = (
    "- Costes de salida No cobramos ninguna comisión de salida.\n"
    "- Costes corrientes anuales Comisiones de gestión y\n"
    "otros costes administrativos u operativos\n"
    "Costes de operación 0.46% del valor de su inversión al año. Esta es una "
    "estimación de los costes incurridos al comprar y vender.\n"
)


# ---------------------------------------------------------------------------
# La ventana no cruza a la fila de operación
# ---------------------------------------------------------------------------

def test_orphan_mgmt_label_does_not_capture_operacion_es():
    assert _OC_DDF_MGMT_RE.search(LU0423950053) is None
    assert _OC_DEL_VALOR_RE.search(LU0423950053) is None


def test_orphan_mgmt_label_does_not_capture_operacion_variant():
    assert _OC_DDF_MGMT_RE.search(IE00BYQQ1F19) is None
    assert _OC_DEL_VALOR_RE.search(IE00BYQQ1F19) is None


def test_detect_returns_none_rather_than_the_next_row():
    """P#10: sin valor propio, NINGUNO — nunca el número de la fila siguiente."""
    for txt, wrong in ((LU0423950053, 0.0001), (IE00BYQQ1F19, 0.0046)):
        got = _detect_ongoing_charge(txt, 'es')
        assert got != wrong, f"capturó el coste de operación: {got}"
        assert got is None


# ---------------------------------------------------------------------------
# Controles: cuando la etiqueta SÍ lleva su valor, se sigue extrayendo
# ---------------------------------------------------------------------------

def test_label_with_own_value_still_extracted_with_article():
    m = _OC_DDF_MGMT_RE.search(
        "Comisiones de gestion y otros costes administrativos  El 0,66 %")
    assert m is not None and m.group(1) == '0,66'


def test_label_with_own_value_still_extracted_without_article():
    m = _OC_DDF_MGMT_RE.search(
        "Comisiones de gestión y otros costes administrativos y de "
        "funcionamiento 1,25%")
    assert m is not None and m.group(1) == '1,25'


def test_del_valor_branch_still_extracted():
    m = _OC_DEL_VALOR_RE.search(
        "Comisiones de gestión y otros costes administrativos 0,88% del valor "
        "de su inversión al año")
    assert m is not None and m.group(1) == '0,88'


def test_costes_corrientes_detraidos_branch_still_extracted():
    m = _OC_DEL_VALOR_RE.search(
        "Costes corrientes detraídos cada año\n"
        "Comisiones de 1,10% del valor de su inversión al año")
    assert m is not None and m.group(1) == '1,10'


def test_detect_still_returns_value_for_healthy_layout():
    assert _detect_ongoing_charge(
        "Comisiones de gestión y otros costes administrativos  El 0,66 %",
        'es') == 0.0066
