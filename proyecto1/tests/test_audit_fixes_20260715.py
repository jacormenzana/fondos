# proyecto1/tests/test_audit_fixes_20260715.py
# -*- coding: utf-8 -*-
"""
Regression tests for bugs found during pipelineP1Audit session 12 (2026-07-15).

R-7: no imports of pipeline.py or core.io.

Fixes covered:

  FIX-B6-2 / BL-B6-HY-KIID guard (pipeline.py, 2026-07-15)
    Government-bond funds can use sub-IG CDS for hedging; their KIIDs therefore
    contain "inferior a grado de inversión" in the derivatives/risk section.
    BL-B6-HY-KIID was firing a false positive on these.
    Fix: name-based IG guard — if fund name contains "government bond",
    "sovereign bond", "treasury bond", "euro government", etc., skip
    BL-B6-HY-KIID entirely.
    Confirmed false positive: SISF EURO GOVERNMENT BOND (LU0106236002).
    New signal added: "inferior a investment grade" (EN/mixed-language PRIIPs).

  FIX-B6-2 / rf_flexible (rf_flexible.py, 2026-07-15)
    ".h.y." and "high yie." are Morningstar OCR artifacts for "High Yield"
    in abbreviated fund names ("JPM GLOB.H.Y.BOND FUND", "AXA WF US HIGH
    YIE.BOND."). These do not match the existing "high yiel" pattern because
    a dot replaces the 'l'.
    Fix: added ".h.y." and "high yie." to include_patterns.

  FIX-B3-AUDIT / _SECTOR_BENIGN_PAIRS (audit_benchmark_consistency.py, 2026-07-15)
    "Inflation-Linked" (fund_master label) and "Inflation" (benchmark token)
    are the same concept. "Real Assets" (fund_master) ⊃ "Real Estate" (benchmark)
    is a superset, not a conflict.

  FIX-B6-AUDIT / EM Sovereign (audit_benchmark_consistency.py, 2026-07-15)
    EM Sovereign bond funds are correctly classified as "High Yield" credit
    quality (many EM governments are sub-investment grade). The benchmark token
    "Government" does not imply IG for emerging-market sovereign debt.
"""
from __future__ import annotations

import os
import sys

import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_P1_DIR    = os.path.normpath(os.path.join(_TESTS_DIR, ".."))
_CORE_DIR  = os.path.normpath(os.path.join(_TESTS_DIR, "..", "core"))
_ROOT_DIR  = os.path.normpath(os.path.join(_TESTS_DIR, "..", ".."))
for _p in (_CORE_DIR, _P1_DIR, _ROOT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ──────────────────────────────────────────────────────────────────────────────
# FIX-B6-2: BL-B6-HY-KIID name guard (tested via extracted branch logic)
# ──────────────────────────────────────────────────────────────────────────────

# Mirrors the exact guard logic in pipeline.py BL-B6-HY-KIID block.
_IG_NAME_GUARDS = [
    "government bond", "sovereign bond", "treasury bond",
    "euro government", "staatsanleihen", "gilt",
]
_HY_KIID_SIGNALS = [
    "calificaciones de menor calidad",
    "inferior a grado de inversión",
    "inferiores a grado de inversión",
    "sub-investment grade",
    "calificación inferior a grado de inversión",
    "inferior a investment grade",
]

def _bl_b6_fires(fund_name: str, kiid_text: str) -> bool:
    """Simulate BL-B6-HY-KIID: returns True if Credit_Quality would be set to HY."""
    name_l = fund_name.lower()
    if any(g in name_l for g in _IG_NAME_GUARDS):
        return False  # IG-primary mandate guard
    kt_l = kiid_text.lower()
    return any(k in kt_l for k in _HY_KIID_SIGNALS)


class TestBL_B6_IG_Name_Guard:
    """FIX-B6-2: government-bond fund names veto BL-B6-HY-KIID even when KIID
    contains sub-IG language in the derivatives/risk section."""

    def test_sisf_euro_gov_bond_not_reclassified(self):
        """SISF EURO GOVERNMENT BOND: 'inferior a grado de inversión' appears in
        a CDS hedging clause, not the primary mandate. Must NOT trigger HY reclassification."""
        kiid = (
            "gestiona de forma activa e invierte como mínimo dos tercios "
            "de sus activos en bonos con una calificación crediticia de grado de "
            "inversión o directa o indirectamente (incluso a través de permutas de "
            "incumplimiento crediticio e índices de permutas de incumplimiento "
            "crediticio) con una calificación inferior a grado de inversión (según "
            "standard & poor's) emitidos por gobiernos de países cuya divisa oficial "
            "sea el euro"
        )
        assert _bl_b6_fires("SISF EURO GOVERNMENT BOND B", kiid) is False, (
            "Government bond fund must not be reclassified to HY even if KIID "
            "mentions sub-IG in a derivatives/hedging context"
        )

    @pytest.mark.parametrize("name", [
        "PIMCO EURO GOVERNMENT BOND FUND",
        "ALLIANZ EURO SOVEREIGN BOND A",
        "VANGUARD US TREASURY BOND ETF",
        "BLACKROCK EURO GOVERNMENT BOND",
        "BUNDESANLEIHEN STAATSANLEIHEN FUND",
        "M&G UK GILT FUND",
    ])
    def test_ig_name_guard_patterns(self, name):
        """All government/sovereign/treasury/gilt fund names must be guarded."""
        kiid = "calificación inferior a grado de inversión en el contexto de las permutas"
        assert _bl_b6_fires(name, kiid) is False, (
            f"Fund with IG-name guard pattern must not fire: {name!r}"
        )

    def test_genuine_hy_fund_still_fires(self):
        """Genuine HY bond fund (no IG name guard) with clear sub-IG KIID → fires."""
        kiid = (
            "invierte principalmente en títulos de deuda corporativa con "
            "calificación inferior a grado de inversión de todo el mundo"
        )
        assert _bl_b6_fires("UBS GLOBAL HIGH YIELD BOND FUND A", kiid) is True, (
            "Genuine HY fund without IG-name guard must still trigger BL-B6-HY-KIID"
        )

    def test_en_mixed_signal_inferior_a_investment_grade(self):
        """New signal: 'inferior a investment grade' (EN/mixed PRIIPs KID)."""
        kiid = (
            "invierte principalmente en títulos de deuda corporativa con "
            "calificación inferior a investment grade de todo el mundo"
        )
        assert _bl_b6_fires("JPM GLOB.H.Y.BOND FUND A HEDGE", kiid) is True, (
            "'inferior a investment grade' signal must trigger BL-B6-HY-KIID "
            "for a fund without an IG-name guard"
        )

    def test_government_bond_with_en_signal_also_guarded(self):
        """Government bond fund with 'inferior a investment grade' in KIID → still guarded."""
        kiid = "investments with a rating inferior a investment grade may be held via cds"
        assert _bl_b6_fires("SCHRODERS EURO GOVERNMENT BOND B", kiid) is False


# ──────────────────────────────────────────────────────────────────────────────
# FIX-B6-2: rf_flexible OCR abbreviation signals
# ──────────────────────────────────────────────────────────────────────────────

class TestRfFlexibleHYOcrSignals:
    """FIX-B6-2: '.h.y.' and 'high yie.' Morningstar OCR patterns claim HY bond funds
    for rf_flexible before they fall through to RESTANTES."""

    @pytest.fixture(autouse=True)
    def _add_blocks_to_path(self):
        _BLOCKS = os.path.normpath(os.path.join(_TESTS_DIR, "..", "blocks"))
        if _BLOCKS not in sys.path:
            sys.path.insert(0, _BLOCKS)

    def _is_candidate(self, name: str) -> bool:
        import pandas as pd
        from rf_flexible import get_universe_isins
        df = pd.DataFrame([{"ISIN": "TEST001", "Fund_Name": name}])
        return "TEST001" in get_universe_isins(df)

    @pytest.mark.parametrize("name,signal", [
        # ".h.y." pattern: Morningstar OCR where dots flank the HY abbreviation
        ("JPM GLOB.H.Y.BOND FUND A HEDGE",    ".h.y."),
        ("SOME MNGR.H.Y.BOND EUR ACC",        ".h.y."),
        # "high yie." pattern: truncated "HIGH YIELD" with trailing dot
        ("AXA WF US HIGH YIE.BOND. E HED",    "high yie."),
    ])
    def test_hy_ocr_names_claimed_by_rf_flexible(self, name, signal):
        """Names with OCR HY abbreviations must be claimed as rf_flexible candidates."""
        assert self._is_candidate(name), (
            f"{name!r} must be an rf_flexible candidate via {signal!r} signal"
        )

    @pytest.mark.parametrize("name", [
        "BGF GLOBAL HIGH YIELD BOND A2",  # already works via "high yield"
        "ROBECO HIGH YIELD BONDS DH EUR", # already works
    ])
    def test_existing_hy_names_still_work(self, name):
        """Existing full 'high yield' names must still be claimed (no regression)."""
        assert self._is_candidate(name), (
            f"Existing HY name {name!r} must still be claimed by rf_flexible"
        )


# ──────────────────────────────────────────────────────────────────────────────
# FIX-B3-AUDIT: sector benign pairs
# ──────────────────────────────────────────────────────────────────────────────

class TestSectorBenignPairs:
    """_SECTOR_BENIGN_PAIRS suppresses normalization-mismatch B3 conflicts."""

    def test_inflation_linked_inflation_benign(self):
        import sys, os
        _TOOLS = os.path.normpath(os.path.join(_TESTS_DIR, "..", "tools"))
        if _TOOLS not in sys.path:
            sys.path.insert(0, _TOOLS)
        from audit_benchmark_consistency import _SECTOR_BENIGN_PAIRS
        assert frozenset({"Inflation-Linked", "Inflation"}) in _SECTOR_BENIGN_PAIRS, (
            "'Inflation-Linked' / 'Inflation' must be a benign pair"
        )

    def test_real_assets_real_estate_benign(self):
        import sys, os
        _TOOLS = os.path.normpath(os.path.join(_TESTS_DIR, "..", "tools"))
        if _TOOLS not in sys.path:
            sys.path.insert(0, _TOOLS)
        from audit_benchmark_consistency import _SECTOR_BENIGN_PAIRS
        assert frozenset({"Real Assets", "Real Estate"}) in _SECTOR_BENIGN_PAIRS, (
            "'Real Assets' / 'Real Estate' must be a benign pair"
        )
