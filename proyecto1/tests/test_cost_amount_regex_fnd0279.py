# proyecto1/tests/test_cost_amount_regex_fnd0279.py
# -*- coding: utf-8 -*-
"""FND-0279: the amount pattern of the plain-text costs parser took the first alternative that matched, so an UNGROUPED number after a PREFIX currency was cut at
three digits ("Costes totales EUR 712 EUR 1576" stored 157 at 5 years; KID of LU0671501129). Real KID slice below. No pipeline imports (R-7)."""
from __future__ import annotations

import os
import sys

_CORE = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)

import cost_table_parser as p  # noqa: E402


def _amounts(text):
    return [m.group(1) or m.group(2) for m in p._EUR_TOKEN.finditer(text)]


def test_prefix_currency_four_digit_amount_is_not_truncated():
    assert _amounts("Costes totales EUR 712 EUR 1576") == ["712", "1576"]


def test_prefix_currency_five_digit_amount_is_not_truncated():
    assert _amounts("EUR 1.141 EUR 10000") == ["1.141", "10000"]


def test_grouped_and_suffix_forms_unchanged():
    assert _amounts("712 EUR 1576 EUR") == ["712", "1576"]
    assert _amounts("EUR 712 EUR 1 576") == ["712", "1 576"]
    assert _amounts("EUR 712 EUR 1,141") == ["712", "1,141"]
    assert _amounts("EUR 3,5 EUR 1.5") == ["3,5", "1.5"]



def test_cell_search_does_not_start_inside_a_number():
    # LU0671501129: "1576 EUR" was read as 576 (re.search matched from the second digit)
    assert p._extract_eur_from_cell("1576 EUR") == 1576.0
    assert p._extract_eur_from_cell("712 EUR") == 712.0
    assert p._extract_eur_from_cell("1 576 EUR") == 1576.0
    assert p._extract_eur_from_cell("1.141 EUR") == 1141.0
    assert p._extract_eur_from_cell("10000 EUR") == 10000.0
