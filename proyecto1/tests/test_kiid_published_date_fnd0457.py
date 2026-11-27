# proyecto1/tests/test_kiid_published_date_fnd0457.py
# -*- coding: utf-8 -*-
"""FND-0457: KIID_Published_Date is read from the phrase that states the document's own date.

The old extractor's ES month-name branch matched and never returned, and its EN patterns were too narrow (5 of 290 texts).
The cases below are the phrasings measured on the stored KIID texts on 2026-11-27 (shortened, no fund named: P#5).

R-7: no imports of pipeline.py or core.io.
"""
from __future__ import annotations

import os
import sys
from datetime import date

import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR = os.path.normpath(os.path.join(_TESTS_DIR, "..", "core"))
if _CORE_DIR not in sys.path:
    sys.path.insert(0, _CORE_DIR)

from kiid_parser import _extract_kiid_published_date as extract  # noqa: E402

TODAY = date(2026, 11, 27)


@pytest.mark.parametrize("text, expected", [
    # ES: the month-name branch that never returned
    ("Este documento de datos fundamentales se elaboró el 29 de abril de 2026. Finalidad", "2026-04-29"),
    ("Los presentes datos fundamentales para el inversor son exactos a 16 de febrero de 2026.", "2026-02-16"),
    ("Fecha de elaboración: 17 febrero 2026", "2026-02-17"),
    ("Fecha de elaboración del documento de datos fundamentales: 14 abril de 2026", "2026-04-14"),
    ("El presente documento tiene fecha de 12 de febrero de 2026. ¿Qué es este producto?", "2026-02-12"),
    ("Documento vigente a partir de: 19 marzo 2026", "2026-03-19"),
    # ES: numeric forms
    ("Este documento de datos fundamentales es exacto a fecha 16.04.2026. Compartimento", "2026-04-16"),
    ("Fecha de producción del KID: 19/02/2026", "2026-02-19"),
    ("Fecha de publicación: 30-04-2026", "2026-04-30"),
    ("Válido a 2026-02-18", "2026-02-18"),
    ("Este documento se publicó el 27/02/2025", "2025-02-27"),
    ("Documento publicado 26/3/2026 ¿Qué es este producto?", "2026-03-26"),
    # EN
    ("This key investor information is accurate as at 18 February 2022.", "2022-02-18"),
    ("The key investor information is accurate as at 02/04/2019.", "2019-04-02"),
    ("accurate as of February 16, 2015", "2015-02-16"),
    ("Date of production of the KID: 05/12/2023 What is this product?", "2023-12-05"),
    ("This document was published on 15/05/2026", "2026-05-15"),
    ("Contact details: www.example.com | +44 (0) 203 000 0000. Published on 27 March 2026. Key", "2026-03-27"),
])
def test_anchored_dates_are_read(text, expected):
    assert extract(text, "ES", TODAY) == expected
    assert extract(text, None, TODAY) == expected   # the phrase carries the language


@pytest.mark.parametrize("text", [
    # dates that are not the document's date
    "The fund launched on 31 October 2013. This class started to issue shares on 13 June 2014.",
    "Se trata de una estimación basada en los costes reales del ejercicio cerrado a 30.09.2025.",
    "Fund launch date: 03/12/2012. Share/unit class launch date: 03/12/2012.",
    # an anchor without a date in the same sentence
    "This information is correct as at the date of this commentary unless otherwise stated. Updated 01/02/2026",
    "Past performance is published at https://docs.example.com/pastperf. 01/02/2026",
    # no text
    "",
])
def test_unanchored_or_missing_dates_are_not_read(text):
    assert extract(text, "EN", TODAY) is None


def test_impossible_future_and_ancient_dates_are_skipped():
    assert extract("Fecha de publicación: 31/02/2026", "ES", TODAY) is None        # not a calendar date
    assert extract("Fecha de publicación: 30/12/2026", "ES", TODAY) is None        # after the run date
    assert extract("Fecha de publicación: 01/01/1999", "ES", TODAY) is None        # before 2000
    # a skipped match does not stop the search: the next anchored date wins
    assert extract("Fecha de publicación: 30/12/2026. Este documento se elaboró el 2 de marzo de 2026.", "ES", TODAY) == "2026-03-02"


def test_first_anchored_date_wins_and_default_today_is_the_run_date():
    text = "Fecha de elaboración: 01/02/2026. Los datos son exactos a 03/04/2026."
    assert extract(text, "ES", TODAY) == "2026-02-01"
    assert extract("Fecha de publicación: 01/02/2026", "ES") == "2026-02-01"   # default `today` = date.today()
    assert extract(None, "ES", TODAY) is None
