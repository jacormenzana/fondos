# proyecto1/tests/test_oc_scale_writer.py
# -*- coding: utf-8 -*-
"""
FIX-OC-SCALE — la ruta de escritura no-COALESCE debe convertir la escala.

Regresión del despliegue del 2026-08-24. `correct_oc_aci_mismatch` recibía el
TER en PORCENTAJE ENTERO y lo escribía sin convertir en
`fund_master.Ongoing_Charge_Recurrent`, que está en RATIO DECIMAL. Un TER
reconstruido de 2,08 % quedaba almacenado como 2,08 — es decir, 208 %.

Por qué no lo detectó la medición A/B previa: el A/B comparaba la SALIDA del
extractor, y este defecto vive en el ESCRITOR, aguas abajo de lo medido. De ahí
que la prueba ataque directamente la función de escritura contra una BD real
en memoria, que es donde se cruza el contrato de escala.

R-7: no importa pipeline.py ni core.io; la BD es :memory:.
"""

import os
import sqlite3
import sys

import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR = os.path.normpath(os.path.join(_TESTS_DIR, '..', 'core'))
if _CORE_DIR not in sys.path:
    sys.path.insert(0, _CORE_DIR)

from sqlite_writer import correct_oc_aci_mismatch          # noqa: E402


def _conn(oc_inicial=0.001):
    """BD mínima en memoria con la única columna que importa aquí."""
    c = sqlite3.connect(':memory:')
    c.execute("""CREATE TABLE fund_master (
                     ISIN TEXT PRIMARY KEY,
                     Ongoing_Charge_Recurrent REAL,
                     Updated_At TEXT)""")
    c.execute("INSERT INTO fund_master VALUES (?,?,?)",
              ('LU3085135567', oc_inicial, '2026-08-23'))
    return c


def _oc(c, isin='LU3085135567'):
    return c.execute(
        "SELECT Ongoing_Charge_Recurrent FROM fund_master WHERE ISIN=?",
        (isin,)).fetchone()[0]


def test_ter_percent_is_stored_as_ratio():
    """LU3085135567: gestión 1,98 + operación 0,10 = 2,08 % -> 0,0208."""
    c = _conn()
    assert correct_oc_aci_mismatch(c, 'LU3085135567', 2.08) is True
    assert _oc(c) == pytest.approx(0.0208)


def test_stored_value_is_never_the_raw_percent():
    """El fallo concreto observado en producción: 2,08 almacenado = 208 %."""
    c = _conn()
    correct_oc_aci_mismatch(c, 'LU3085135567', 2.08)
    assert _oc(c) != pytest.approx(2.08)


@pytest.mark.parametrize('ter_pct', [0.70, 1.69, 1.88, 2.08, 0.05, 4.30])
def test_result_always_lands_in_ratio_domain(ter_pct):
    """Ningún TER plausible puede producir un OC fuera del dominio ratio.

    El universo activo tiene OC < 0,06 en 2.871 de 2.874 fondos; un valor por
    encima de 0,5 (50 %) es imposible para un gasto corriente.
    """
    c = _conn()
    correct_oc_aci_mismatch(c, 'LU3085135567', ter_pct)
    stored = _oc(c)
    assert 0.0 <= stored <= 0.5
    assert stored == pytest.approx(ter_pct / 100.0)


def test_none_is_a_no_op():
    """Sin TER reconstruible no se toca el valor almacenado."""
    c = _conn(oc_inicial=0.0123)
    assert correct_oc_aci_mismatch(c, 'LU3085135567', None) is False
    assert _oc(c) == pytest.approx(0.0123)


def test_unknown_isin_reports_false():
    c = _conn()
    assert correct_oc_aci_mismatch(c, 'XX0000000000', 1.5) is False
