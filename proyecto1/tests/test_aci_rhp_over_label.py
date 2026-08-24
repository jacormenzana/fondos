# proyecto1/tests/test_aci_rhp_over_label.py
# -*- coding: utf-8 -*-
"""
P1-16 / FIX-ACI-RHP-OVER-LABEL — la etiqueta del ACI manda sobre el fallback.

`FIX-ACI-SINGLE-VS-LABEL` ya declaraba el principio ("cuando la etiqueta expone
dos valores distintos, manda sobre el fallback"), pero solo actuaba cuando el
fallback había COPIADO el valor de 1 año. Si el fallback traía un número de otro
sitio, la etiqueta ni se consultaba.

Evidencia que acotó el arreglo (cohorte de 139 fondos con ACI_1Y < ACI_RHP,
clasificada por procedencia contra la etiqueta del KID):

    133 de 139  ambos valores IDÉNTICOS a los que publica el KID
                -> la inversión está en el documento de origen, no en nosotros;
                   no hay nada que corregir y NO deben tocarse
      6 de 139  ACI_RHP que la etiqueta no respalda -> único defecto real

Corolario que conviene no perder: la premisa "ACI_1Y >= ACI_RHP siempre" es
FALSA. Se sostiene cuando domina la comisión de entrada (que se amortiza), no
cuando dominan costes que crecen con el horizonte.

Estas pruebas usan el corpus real, con guarda de salto si la BD no está —
mismo patrón que test_priips_cost_extractor.py.
"""

import os
import sqlite3
import sys
import unicodedata

import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR = os.path.normpath(os.path.join(_TESTS_DIR, '..', 'core'))
if _CORE_DIR not in sys.path:
    sys.path.insert(0, _CORE_DIR)

_DB_PATH = r'C:\desarrollo\fondos\db\fondos.sqlite'
_DB_EXISTS = os.path.exists(_DB_PATH)

pytestmark = pytest.mark.skipif(not _DB_EXISTS, reason='corpus DB not available')


def _aci_rhp(isin):
    from priips_cost_extractor import extract_priips_costs
    con = sqlite3.connect(f'file:{_DB_PATH}?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    try:
        m = con.execute(
            'SELECT Ongoing_Charge_Recurrent oc FROM fund_master WHERE ISIN=?',
            (isin,)).fetchone()
        k = con.execute(
            'SELECT Raw_KIID_Text, DLA2_Table_Text FROM fund_kiid_metadata '
            'WHERE ISIN=? AND KIID_Class=1', (isin,)).fetchone()
        if m is None or k is None:
            pytest.skip(f'{isin} not in corpus')
        txt = unicodedata.normalize(
            'NFC', (k['DLA2_Table_Text'] or '') + '\n' + (k['Raw_KIID_Text'] or ''))
        return extract_priips_costs(text=txt, isin=isin,
                                    existing_oc=m['oc']).get('ACI_RHP')
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Los 3 defectos: la etiqueta no respalda el ACI_RHP almacenado
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('isin,label_rhp,stored_before', [
    ('ES0175437005', 1.1, 12.8),   # etiqueta "Impacto del coste anual 1,1% 1,1%"
    ('IE000XHDJXE4', 0.1, 3.3),    # etiqueta "Impacto anual en los costes 0.1% 0.1%"
    ('IE00BDGV0290', 1.5, 5.0),    # etiqueta "1,4% 1,5%"
])
def test_unvouched_aci_rhp_is_corrected_to_the_label(isin, label_rhp, stored_before):
    got = _aci_rhp(isin)
    assert got == pytest.approx(label_rhp, abs=0.06), (
        f'{isin}: esperaba el valor de la etiqueta {label_rhp}, obtuve {got}')
    assert got != pytest.approx(stored_before, abs=0.06), (
        f'{isin}: sigue publicando el valor sin respaldo {stored_before}')


# ---------------------------------------------------------------------------
# Los 133: el KID publica de verdad 1Y < RHP — no tocar
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('isin,expected', [
    ('LU1883316298', 1.4),    # etiqueta "1,3% 1,4%" — ambos nuestros
    ('LU1665237613', 7.21),   # etiqueta "0,51% 7,21%" — ambos nuestros
])
def test_label_backed_inversion_is_left_alone(isin, expected):
    """La regla corrige solo a la BAJA y solo contra la etiqueta.

    Si el KID publica esos dos valores, la ordenación 'anómala' es del emisor.
    Corregirla sería inventar un dato que el documento no dice.
    """
    assert _aci_rhp(isin) == pytest.approx(expected, abs=0.06)


def test_rule_never_raises_an_aci():
    """Nunca sube un ACI: solo puede acercarlo a la baja hacia su etiqueta."""
    con = sqlite3.connect(f'file:{_DB_PATH}?mode=ro', uri=True)
    try:
        before = dict(con.execute(
            'SELECT ISIN, ACI_RHP FROM fund_master WHERE ISIN IN '
            "('ES0175437005','IE000XHDJXE4','IE00BDGV0290')").fetchall())
    finally:
        con.close()
    for isin, old in before.items():
        new = _aci_rhp(isin)
        if new is not None and old is not None:
            assert new <= old + 0.06, f'{isin}: {old} -> {new} subió'
