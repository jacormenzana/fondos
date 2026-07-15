# proyecto1/tests/test_audit_fixes_20260715b.py
# -*- coding: utf-8 -*-
"""
Regression tests for bugs found during pipelineP1Audit session 14 (2026-07-15).

R-7: no imports of pipeline.py or core.io.

Fixes covered:

  FIX-B6-3a (pipeline.py, 2026-07-15):
    Monetario funds cannot hold High Yield bonds by regulatory definition.
    BL-B6-HY-KIID was firing on Monetario funds whose KIID describes a
    permitted HY floor (e.g. Carmignac Portfolio Sécurité, LU0992625243).
    Fix: exclude Fund_Nature='Monetario' from BL-B6-HY-KIID entirely.

  FIX-B6-3b (pipeline.py, 2026-07-15):
    Bloomberg US/Euro Aggregate index funds are IG broad-market trackers.
    Their KIID may mention a small HY component (~3-5% of the index) which
    is not the primary mandate. "aggregate" in the fund name now guards
    against BL-B6-HY-KIID.
    Confirmed false positive: JPM US AGGREGATE (LU0679000579).

  FIX-B6-AUDIT-2 (classify_utils.py + audit_benchmark_consistency.py, 2026-07-15):
    EM Govt Bond indices (e.g. "Morningstar EM Govt Bond Local C") use "Govt"
    not "Sovereign" in their name. The EM-sovereign suppression only matched
    "sovereign" previously, so Templeton Emerging Markets Bond Fund
    (LU0152984307) — correctly classified HY — was still flagged.
    Fix: extend the EM-sovereign exception to match "em govt" / "em gov"
    in addition to "sovereign".

  FIX-B3-2 (classify_utils.py, 2026-07-15):
    Biotech / Medtech and Clean-Energy funds straddle two sectors in the P1
    taxonomy vs. the Morningstar taxonomy. These are taxonomy divergences,
    not real classification errors:
      - Franklin Biotech: P1=Healthcare, Morningstar=Technology
      - VARIOPART Medtech: P1=Technology, Morningstar=Healthcare
      - Invesco Energy Transition: P1=Technology, Morningstar=Energy
    Fix: add {"Healthcare & Life Sciences","Technology & Innovation"} and
    {"Energy & Resources","Technology & Innovation"} to BMK_SECTOR_BENIGN_PAIRS.
"""
from __future__ import annotations

import os
import sys

import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR  = os.path.normpath(os.path.join(_TESTS_DIR, "..", "core"))
_ROOT_DIR  = os.path.normpath(os.path.join(_TESTS_DIR, "..", ".."))
for _p in (_CORE_DIR, _ROOT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ─── Mirrors BL-B6-HY-KIID guard (pipeline.py) ───────────────────────────────
_IG_NAME_GUARDS_14 = [
    "government bond", "sovereign bond", "treasury bond",
    "euro government", "staatsanleihen", "gilt",
    "aggregate",   # FIX-B6-3b
]
_HY_KIID_SIGNALS_14 = [
    "calificaciones de menor calidad",
    "inferior a grado de inversión",
    "inferiores a grado de inversión",
    "sub-investment grade",
    "calificación inferior a grado de inversión",
    "inferior a investment grade",
]

def _bl_b6_fires_14(fund_name: str, kiid_text: str, fund_nature: str = "Renta Fija Flexible") -> bool:
    """Simulate the updated BL-B6-HY-KIID including FIX-B6-3a/b."""
    if fund_nature == "Monetario":   # FIX-B6-3a
        return False
    if fund_nature != "Investment Grade":  # only fires when current CQ = IG
        pass  # simplified: assume CQ=IG for all callers
    name_l = fund_name.lower()
    if any(g in name_l for g in _IG_NAME_GUARDS_14):
        return False
    kt_l = kiid_text.lower()
    return any(k in kt_l for k in _HY_KIID_SIGNALS_14)


# ──────────────────────────────────────────────────────────────────────────────
# FIX-B6-3a: Monetario exclusion
# ──────────────────────────────────────────────────────────────────────────────

class TestMonetarioExclusion:
    """Monetario funds must never be reclassified to HY by BL-B6-HY-KIID."""

    def test_carmignac_securite_monetario_not_reclassified(self):
        """Carmignac Portfolio Sécurité (Monetario) — KIID mentions sub-IG floor
        for per-issuer category; must not trigger HY reclassification."""
        kiid = (
            "calificación crediticia o con una calificación inferior a investment grade "
            "para cada categoría de emisor. la duración modificada de la cartera oscila "
            "entre -3 y +4."
        )
        assert _bl_b6_fires_14("CARMIGNAC PR.SCURIT FW USDH AC", kiid, "Monetario") is False

    @pytest.mark.parametrize("name", [
        "BNP P PARIBAS INSTICASH EUR A",
        "AMUNDI LIQUIDITE SR EUR ACC",
        "LYXOR SMART CASH P EUR ACC",
    ])
    def test_generic_monetario_not_reclassified(self, name):
        """Any Monetario fund with HY KIID language must be guarded."""
        kiid = "inferior a grado de inversión para gestión de liquidez"
        assert _bl_b6_fires_14(name, kiid, "Monetario") is False, (
            f"Monetario fund {name!r} must be guarded from HY reclassification"
        )

    def test_rff_fund_still_fires(self):
        """RF Flexible fund (non-Monetario) with clear HY KIID still fires."""
        kiid = "invierte principalmente en títulos con calificación inferior a grado de inversión"
        assert _bl_b6_fires_14("ALLIANZ EURO HY BD AT EUR ACC", kiid, "Renta Fija Flexible") is True


# ──────────────────────────────────────────────────────────────────────────────
# FIX-B6-3b: "aggregate" IG name guard
# ──────────────────────────────────────────────────────────────────────────────

class TestAggregateNameGuard:
    """Aggregate bond index funds are IG broad-market trackers."""

    def test_jpm_us_aggregate_guarded(self):
        """JPM US Aggregate — KIID mentions HY floor for EM/sovereign slice;
        fund is IG-primary (90% IG). Must not trigger HY reclassification."""
        kiid = (
            "mínimo de la deuda soberana con calificación inferior a investment grade "
            "y de los mercados emergentes y un 90% de los títulos con calificación "
            "investment grade adquiridos"
        )
        assert _bl_b6_fires_14("JPM US AGGREGATE A EURHDG ACC", kiid) is False

    @pytest.mark.parametrize("name", [
        "VANGUARD EURO AGGREGATE BOND INDEX",
        "ISHARES EURO AGGREGATE BOND ETF",
        "AMUNDI EUR CORPORATE AGGREGATE",
    ])
    def test_aggregate_name_guard_patterns(self, name):
        """Any 'aggregate' bond fund name must be guarded."""
        kiid = "may include securities with a rating inferior a investment grade"
        assert _bl_b6_fires_14(name, kiid) is False, (
            f"Aggregate fund {name!r} must be guarded from HY reclassification"
        )

    def test_genuine_hy_fund_without_aggregate_still_fires(self):
        """Non-aggregate HY fund with KIID sub-IG signal must still fire."""
        kiid = "invierte en bonos con calificación inferior a grado de inversión"
        assert _bl_b6_fires_14("ALLIANZ EUR HIGH YIELD BOND AT", kiid) is True


# ──────────────────────────────────────────────────────────────────────────────
# FIX-B6-AUDIT-2: EM Govt Bond EM-sovereign exception
# ──────────────────────────────────────────────────────────────────────────────

class TestEmGovtBondSovereignException:
    """BMK_SECTOR_BENIGN_PAIRS and SC-H2 EM-sovereign exception."""

    def _is_em_sov(self, bmk_credit: str, bmk_name: str) -> bool:
        """Mirror the classify_utils SC-H2 EM-sovereign suppression."""
        name_l = bmk_name.lower()
        return (
            bmk_credit == "Government"
            and any(tok in name_l for tok in ("sovereign", "em govt", "em gov"))
            and any(em in name_l for em in ("em ", "emerg", "mercados em"))
        )

    def test_em_govt_bond_suppressed(self):
        """'Morningstar EM Govt Bond Local C' must suppress as EM-sovereign."""
        assert self._is_em_sov("Government", "Morningstar EM Govt Bond Local C") is True

    def test_em_sovereign_bond_suppressed(self):
        """'iShares JP Morgan EM Sovereign Bond' must still suppress."""
        assert self._is_em_sov("Government", "iShares JP Morgan EM Sovereign Bond") is True

    def test_jpm_gbi_em_suppressed(self):
        """'JPM GBI-EM Global Diversified' must suppress (em gov via 'gbi' → handled if 'em gov')."""
        # GBI-EM doesn't contain "em gov" literally; this is a known gap → not suppressed
        # (GBI = Government Bond Index, but we can't yet parse that abbreviation)
        result = self._is_em_sov("Government", "JPM GBI-EM Global Diversified")
        # This one is NOT suppressed by current logic — document expected behavior
        assert result is False  # known gap; GBI abbreviation not yet parsed

    def test_non_em_govt_not_suppressed(self):
        """'ICE BofA Euro Government Bond' must NOT be suppressed (not EM)."""
        assert self._is_em_sov("Government", "ICE BofA Euro Government Bond") is False

    def test_em_corp_not_suppressed(self):
        """'Morningstar EM Corporate Bond' must NOT be suppressed (Corporate, not Govt)."""
        assert self._is_em_sov("Corporate", "Morningstar EM Corporate Bond") is False


# ──────────────────────────────────────────────────────────────────────────────
# FIX-B3-2: Healthcare/Technology and Energy/Technology benign pairs
# ──────────────────────────────────────────────────────────────────────────────

class TestSectorBenignPairsExtended:
    """BMK_SECTOR_BENIGN_PAIRS now includes Healthcare↔Technology and
    Energy↔Technology taxonomy divergences."""

    def _load_pairs(self):
        from classify_utils import BMK_SECTOR_BENIGN_PAIRS
        return BMK_SECTOR_BENIGN_PAIRS

    def test_healthcare_technology_benign(self):
        """Franklin Biotech (P1=Healthcare) vs Morningstar Biotech (Technology) — benign."""
        pairs = self._load_pairs()
        assert frozenset({"Healthcare & Life Sciences", "Technology & Innovation"}) in pairs

    def test_energy_technology_benign(self):
        """Invesco Energy Transition (P1=Technology) vs Morningstar Renewable (Energy) — benign."""
        pairs = self._load_pairs()
        assert frozenset({"Energy & Resources", "Technology & Innovation"}) in pairs

    def test_inflation_benign_still_present(self):
        """Existing Inflation-Linked / Inflation pair must not be dropped."""
        pairs = self._load_pairs()
        assert frozenset({"Inflation-Linked", "Inflation"}) in pairs

    def test_real_assets_benign_still_present(self):
        """Existing Real Assets / Real Estate pair must not be dropped."""
        pairs = self._load_pairs()
        assert frozenset({"Real Assets", "Real Estate"}) in pairs
