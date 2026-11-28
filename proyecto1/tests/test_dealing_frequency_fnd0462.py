# proyecto1/tests/test_dealing_frequency_fnd0462.py
# -*- coding: utf-8 -*-
"""FND-0462 (1): Liquidity_Profile is the DEALING FREQUENCY of the fund's units, read from the KIID wording.

The old detector measured settlement days and emitted T0..T10+ into a column redefined as dealing frequency (201 funds stored 'T5').
The cases below are phrasings measured on the stored KIID texts on 2026-11-28 (shortened, no fund named: P#5).

R-7: no imports of pipeline.py or core.io.
"""
from __future__ import annotations

import os
import sys

import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR = os.path.normpath(os.path.join(_TESTS_DIR, "..", "core"))
if _CORE_DIR not in sys.path:
    sys.path.insert(0, _CORE_DIR)

import kiid_parser  # noqa: E402
from kiid_parser import _detect_dealing_frequency as detect  # noqa: E402


@pytest.mark.parametrize("text, expected", [
    ("El participe podrá suscribir y reembolsar sus participaciones con una frecuencia diaria.", "Daily"),
    ("Las acciones del Subfondo se pueden reembolsar previa solicitud, y se negocian normalmente a diario.", "Daily"),
    ("Puede comprar y vender acciones cualquier día hábil.", "Daily"),
    ("En general, las participaciones pueden reembolsarse todos los días de valoración.", "Daily"),
    ("You may redeem your shares on any business day.", "Daily"),
    ("Shares can be redeemed daily.", "Daily"),
    # a month-end date offered on top of the weekly frequency is not a second frequency
    ("Puede comprar y vender acciones semanalmente, todos los miércoles (o el siguiente día hábil si el miércoles no lo fuera), "
     "y el último día hábil de cada mes.", "Weekly"),
    ("Shares may be redeemed weekly and on the last business day of each month.", "Weekly"),
    ("Las participaciones se pueden reembolsar quincenalmente.", "Bi-Weekly"),
    ("You can buy and sell shares once a month.", "Monthly"),
    # wording about something else is not a dealing frequency
    ("El reembolso se abonará en un plazo de 5 días hábiles.", None),
    ("El fondo reparte dividendos mensualmente a sus participaciones.", None),
    ("Los bonos indexados a la inflación aplican desfases para su reembolso basado en una tasa interpolada diaria.", None),
    ("Al menos el 15% de sus activos serán activos con vencimiento semanal.", None),
    ("Puede reembolsar su inversión previa solicitud.", None),
    # two different frequencies in dealing sentences: do not guess
    ("Las acciones se pueden reembolsar a diario. Las participaciones se pueden suscribir semanalmente.", None),
    ("", None),
    (None, None),
])
def test_dealing_frequency(text, expected):
    assert detect(text) == expected


def test_asset_maturity_wording_does_not_override_the_dealing_sentence():
    text = ("El partícipe podrá suscribir y reembolsar sus participaciones con una frecuencia diaria. "
            "Al menos el 15% de sus activos serán activos con vencimiento semanal.")
    assert detect(text) == "Daily"


def test_the_switch_ships_off_and_selects_the_detector(monkeypatch):
    from shared import config as cfg
    assert cfg.KIID_DEALING_FREQUENCY_ENABLED is False
    text = "Las órdenes de reembolso se liquidan en 5 días hábiles. Puede comprar y vender acciones cualquier día hábil."
    assert kiid_parser._liquidity_profile(text) == "T5"            # OFF: the stored behaviour is unchanged
    monkeypatch.setattr(cfg, "KIID_DEALING_FREQUENCY_ENABLED", True)
    assert kiid_parser._liquidity_profile(text) == "Daily"
