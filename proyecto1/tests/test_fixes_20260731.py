# proyecto1/tests/test_fixes_20260731.py
# -*- coding: utf-8 -*-
"""
Regression tests for FIX-MMF-COMMODITY-OVERLAY-1 (2026-07-31).

Root cause:
    Synthetic (swap-based) commodity funds hold "short-term money market
    instruments" as SWAP COLLATERAL, while their stated objective is to track a
    commodity index.  The STRONG MMF marker "short-term money market" (an MMFR
    category label) matched INSIDE the holding phrase "short-term money market
    instruments" — collateral language, not the self-identification "... money
    market FUND" — so detect_nature_from_kiid returned 'Monetario' at the
    Monetario branch, before control could reach the commodity->Alternativo
    branch further down.

    Confirmed live: LU0415415636 VONTOBEL COMMODIT H EURHDG ACC (KIID_Status=OK,
    SRRI=4, benchmark = Bloomberg Commodity) was classified Monetario, while its
    sibling share class LU1683489089 VONTOBEL COMMOD HN was correctly Alternativo.

Fix:
    Add a _commodity_overlay guard to the Monetario return condition so that a
    fund declaring a commodity-index mandate falls through to the existing
    commodity->Alternativo branch (which returns Alternativo when no equity
    mandate is present).  Signals mirror that branch.  Measured blast radius:
    exactly 1 net reclassification across the full 3,726-fund universe.

R-7: no imports of pipeline.py or core.io.
"""
from __future__ import annotations

import os
import sys

import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR = os.path.normpath(os.path.join(_TESTS_DIR, "..", "core"))
for _p in (_CORE_DIR,):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from classify_utils import detect_nature_from_kiid


# Faithful excerpt of the real LU0415415636 VONTOBEL COMMODITY KIID objective.
_VONTOBEL_COMMODITY_TEXT = (
    "Objective This actively managed Sub-Fund aims to participate in the growth "
    "of the commodity markets over the medium to long term. The Sub-Fund invests "
    "in time deposits, short-term money market instruments and interest-bearing "
    "securities with a residual term to maturity of a maximum of thirty months "
    "as well as complex investment instruments such as swap transactions. The "
    "Sub-Fund will be exposed to indices from the Bloomberg Commodity Indexes "
    "series and/or their sub-indices or other commodity indices."
)


class TestCommodityOverlayNotMonetario:
    """A commodity fund that holds money-market instruments as swap collateral
    must NOT be classified Monetario."""

    def test_vontobel_commodity_not_monetario(self):
        result = detect_nature_from_kiid(_VONTOBEL_COMMODITY_TEXT)
        assert result != "Monetario", (
            "Commodity fund holding MMF instruments as swap collateral must not "
            "be read as Monetario"
        )

    def test_vontobel_commodity_is_alternativo(self):
        """Suppressing the premature Monetario return lets control reach the
        existing commodity->Alternativo branch."""
        result = detect_nature_from_kiid(_VONTOBEL_COMMODITY_TEXT)
        assert result == "Alternativo", (
            "A pure commodity-index mandate (no equity signal) must resolve to "
            f"Alternativo, got {result!r}"
        )


class TestGenuineMmfStillDetected:
    """The guard is commodity-gated, not phrase-gated: a genuine MMF whose KID
    mentions 'short-term money market instruments' (without any commodity
    signal) must still be detected as Monetario."""

    def test_genuine_short_term_mmf_still_monetario(self):
        kiid = (
            "The Fund is a short-term money market fund. The Fund invests in "
            "short-term money market instruments and deposits with credit "
            "institutions, maintaining a low weighted average maturity."
        )
        result = detect_nature_from_kiid(kiid)
        assert result == "Monetario", (
            "A genuine short-term money market fund must still be detected as "
            f"Monetario, got {result!r}"
        )

    def test_mmf_instruments_holding_without_commodity_still_monetario(self):
        """'short-term money market instruments' as the primary holding, with a
        self-identifying MMF label and no commodity language, stays Monetario."""
        kiid = (
            "El subfondo es un fondo del mercado monetario a corto plazo. "
            "Invierte principalmente en instrumentos del mercado monetario a "
            "corto plazo de alta calidad crediticia."
        )
        result = detect_nature_from_kiid(kiid)
        assert result == "Monetario"
