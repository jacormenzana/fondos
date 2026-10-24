# proyecto1/tests/test_family_structural_heterogeneity_fnd0310.py
# -*- coding: utf-8 -*-
"""FND-0310: the word "short" in a fund name is a duration word, not a structural-heterogeneity signal. Pure: no database, no pipeline import (R-7)."""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.normpath(os.path.join(_HERE, "..", "core")), os.path.normpath(os.path.join(_HERE, ".."))):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402

from fund_family_builder import _STRUCTURAL_HETEROGENEITY_SIGNALS, _is_structural_heterogeneity, _resolve_family_nature  # noqa: E402


@pytest.mark.parametrize("names", [
    ["DWS ESG EURO BONDS SHORT FC AC", "DWS EURO BNDS SHRT TFD EUR INC"],          # the families this ticket unblocks
    ["FIDELITY EUR S-T BOND Y EUR AC", "FIDELITY F.EURO SHORT TERM E"],
    ["SISF EURO SHORT TERM BOND B", "INVESCO EURO ULTRASHORT TERM E"],
    ["NORDEA 1 NORW.SHORT-T. BOND BP", "AXA US SHORT DUR HY E USD ACC"],
])
def test_short_duration_names_are_not_structural_heterogeneity(names):
    assert _is_structural_heterogeneity(names) is False


@pytest.mark.parametrize("names", [
    ["CANDRIAM LONG SHORT CREDIT C"],                                              # the only real long/short name of today's universe
    ["SOME FUND LONG/SHORT EUR"],
    ["EVLI MARKET NEUTRAL B"],
    ["ABSOLUTE RETURN BOND FUND"],
])
def test_the_genuine_structural_signals_still_fire(names):
    assert _is_structural_heterogeneity(names) is True


def test_the_bare_short_words_are_no_longer_signals():
    assert "short " not in _STRUCTURAL_HETEROGENEITY_SIGNALS and " short" not in _STRUCTURAL_HETEROGENEITY_SIGNALS


def test_a_short_duration_family_with_a_clear_majority_is_now_arbitrated_by_rules_1_to_3():
    """DWS ESG Euro Bonds Short: five Corto Plazo classes and one Flexible. Before the change the family was skipped as structural."""
    members = [{"ISIN": f"I{i}", "Fund_Name": "DWS ESG EURO BONDS SHORT X", "Fund_Nature": "Renta Fija Corto Plazo",
                "Data_Quality_Flag": "OK", "SRRI_Quality_Flag": "HIGH", "active": True} for i in range(5)]
    members.append({"ISIN": "I5", "Fund_Name": "DWS EURO BNDS SHRT TFD EUR INC", "Fund_Nature": "Renta Fija Flexible",
                    "Data_Quality_Flag": "OK", "SRRI_Quality_Flag": "HIGH", "active": True})
    nature, fix = _resolve_family_nature(members)
    assert nature == "Renta Fija Corto Plazo" and fix == ["I5"]
