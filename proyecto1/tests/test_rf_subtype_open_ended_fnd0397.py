# proyecto1/tests/test_rf_subtype_open_ended_fnd0397.py
# -*- coding: utf-8 -*-
"""FND-0397 (2026-11-08): RF_Corto versus RF_Flexible on the open-ended-fund boilerplate and on truncated short-duration names.

R-7: only classify_utils is imported (no pipeline, no core.io, no DB); the KIID text is synthetic, built from the two sentence shapes that were measured, with no fund named in the logic (P#5).

Defect: "el fondo no tiene UNA fecha de vencimiento fija" is the open-ended-fund boilerplate, but the negation guard (FIX-P1-RFC2) only knew "no tiene fecha de vencimiento", so the bare substring
"fecha de vencimient" was read as a target-maturity signal and the fund went to RF_Corto (replay on 978 RF funds: 66 Corto -> Flexible). The fix makes the article optional; the companion rule adds the
generic abbreviations of "short duration" that the catalogue names use, so genuine short-duration funds do not lose their only RF_Corto signal.
"""
import os
import sys

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest

from proyecto1.core import classify_utils
from proyecto1.core.classify_utils import NAME_SIGNALS_RF_CORTO, NAME_SIGNALS_RF_CORTO_ABBREV, _rf_corto_name_signals, resolve_rf_subtype


@pytest.fixture
def guard_on(monkeypatch):
    """The fix ships OFF (owner NO GO 2026-11-08: RF_OPEN_ENDED_ARTICLE_GUARD_ENABLED = False); these tests exercise it with the switch ON."""
    monkeypatch.setattr(classify_utils._cfg_catalog, "RF_OPEN_ENDED_ARTICLE_GUARD_ENABLED", True, raising=False)


PAD = "Este documento le proporciona informacion fundamental sobre este fondo de inversion. " * 16      # ~1,300 characters: the objective window of the classic layout starts at 1,200


def _doc(objective: str) -> str:
    return PAD + "Objetivos Objetivo de inversion " + objective + " " + ("Politica de inversion general. " * 4)


OPEN_ENDED = "El fondo no tiene una fecha de vencimiento fija, aunque puede cerrarse en determinadas circunstancias."
BOND_FUND = "El objetivo del Fondo es proporcionar ingresos invirtiendo principalmente en bonos emitidos por empresas y Gobiernos de todo el mundo."


def test_the_open_ended_boilerplate_with_an_article_is_not_a_target_maturity_signal(guard_on):
    assert resolve_rf_subtype("generic income fund", _doc(BOND_FUND + " " + OPEN_ENDED)) == "RF_Flexible"


@pytest.mark.parametrize("sentence", [
    "El fondo no tiene fecha de vencimiento.",                       # the form the old guard already knew
    "El fondo no tiene una fecha de vencimiento fija.",             # with the article (the defect)
    "El fondo no tendra un fecha de vencimiento determinada.",
    "El fondo sin fecha de vencimiento fija.",
])
def test_every_open_ended_wording_is_negated(sentence, guard_on):
    assert resolve_rf_subtype("generic income fund", _doc(BOND_FUND + " " + sentence)) == "RF_Flexible"


def test_a_real_target_maturity_fund_is_still_short(guard_on):
    text = _doc(BOND_FUND + " El fondo vencera en la fecha de vencimiento prevista del 31 de diciembre de 2027 y sus bonos se mantienen hasta esa fecha.")
    assert resolve_rf_subtype("generic target fund", text) == "RF_Corto"


def test_an_explicit_duration_cap_is_still_short_even_beside_the_boilerplate(guard_on):
    text = _doc(BOND_FUND + " La duracion de la cartera no sera superior a 3 anos. " + OPEN_ENDED)
    assert resolve_rf_subtype("generic income fund", text) == "RF_Corto"


@pytest.mark.parametrize("name", ["axa im europe short dur hy ac", "muzinich shor dur hy h eurh ac", "ostrum cr ul sh plus n eur acc", "somebody ult sh bond fund"])
def test_truncated_short_duration_names_keep_their_signal_when_the_text_has_none(name, guard_on):
    assert resolve_rf_subtype(name, _doc(BOND_FUND + " " + OPEN_ENDED)) == "RF_Corto"


def test_a_name_without_a_short_signal_stays_flexible(guard_on):
    assert resolve_rf_subtype("dynamic income fund d eurhdg acc", _doc(BOND_FUND + " " + OPEN_ENDED)) == "RF_Flexible"


def test_the_new_name_signals_are_generic_abbreviations_not_fund_names():
    added = ["short dur", "shor dur", "ul sh", "ult sh"]
    assert NAME_SIGNALS_RF_CORTO_ABBREV == added
    assert not any(any(ch.isdigit() for ch in s) for s in added)                 # no ISIN, no share-class code


def test_the_switch_ships_off_and_the_abbreviations_join_the_list_only_when_on():
    from shared import config
    assert config.RF_OPEN_ENDED_ARTICLE_GUARD_ENABLED is False                   # owner NO GO 2026-11-08
    assert _rf_corto_name_signals(False) == NAME_SIGNALS_RF_CORTO and not any(s in NAME_SIGNALS_RF_CORTO for s in NAME_SIGNALS_RF_CORTO_ABBREV)
    assert all(s in _rf_corto_name_signals(True) for s in NAME_SIGNALS_RF_CORTO_ABBREV)


def test_with_the_switch_off_the_classification_is_the_one_before_the_ticket():
    """OFF = the old behaviour, exactly: the boilerplate with an article is still read as a target maturity (the defect the owner chose not to ship yet), and a bare abbreviation is not a name signal."""
    assert resolve_rf_subtype("generic income fund", _doc(BOND_FUND + " " + OPEN_ENDED)) == "RF_Corto"
    assert resolve_rf_subtype("generic income fund", _doc(BOND_FUND + " El fondo no tiene fecha de vencimiento.")) == "RF_Flexible"     # the older guard keeps working
    assert resolve_rf_subtype("somebody ult sh bond fund", _doc(BOND_FUND + " El fondo no tiene fecha de vencimiento.")) == "RF_Flexible"   # no name signal while OFF
