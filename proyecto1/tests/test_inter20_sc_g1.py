# proyecto1/tests/test_inter20_sc_g1.py
# -*- coding: utf-8 -*-
"""
Tests para INTER-20 (SC-G1): Geography -> Investment_Universe consistencia.

Regla: el ámbito geográfico declarado en Geography debe reflejarse en
Investment_Universe.
  • Geography ∈ _REGION_GEOGRAPHIES  → IU debe ser 'Regional'
  • Geography ∈ _COUNTRY_GEOGRAPHIES → IU debe ser 'Country'

Se ejecuta DESPUÉS de INTER-13 (BL-33) para interceptar el IU='Global'
que BL-33 asigna por defecto a fondos Monetario/RF Corto incluso cuando
hay señal geográfica concreta en Geography.

Contexto del bug: ~137 fondos en la BD tenían IU='Global' + Geography
específica (Europe=93, North America=32, Asia-Pacific=6, China=4, Japan=2)
por esta inconsistencia. El fix auto-corrige IU en el pipeline-level
validate_all_semantic_consistency, cuya corrected_record se merges de
vuelta a fund_master_record (pipeline.py línea 2634) y se persiste via
COALESCE.

Regla R-7: sin imports de pipeline.py ni core.io.
"""
from __future__ import annotations
import os, sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR  = os.path.normpath(os.path.join(_TESTS_DIR, "..", "core"))
if _CORE_DIR not in sys.path:
    sys.path.insert(0, _CORE_DIR)

import pytest
from classify_utils import validate_all_semantic_consistency


def _run(record: dict) -> dict:
    return validate_all_semantic_consistency(record)


def _base(**kwargs) -> dict:
    base = {"Fund_Nature": "Renta Variable", "Family": "Equity Core"}
    base.update(kwargs)
    return base


# ─── SC-G1: Region Geography + Global IU → must correct to Regional ──────────

class TestSCG1RegionCorrectedToRegional:

    @pytest.mark.parametrize("geo", [
        "Europe", "North America", "Asia-Pacific",
        "Latin America", "Eastern Europe", "Middle East & Africa",
    ])
    def test_global_iu_corrected_when_geography_is_region(self, geo):
        """IU='Global' + Region Geography → auto-corrected to 'Regional' (SC-G1)."""
        rec = _base(Geography=geo, Investment_Universe="Global")
        result = _run(rec)
        cr = result["corrected_record"]
        assert cr["Investment_Universe"] == "Regional", (
            f"Expected 'Regional' for Geography='{geo}', "
            f"got '{cr['Investment_Universe']}'"
        )

    @pytest.mark.parametrize("geo", [
        "Europe", "North America", "Asia-Pacific",
        "Latin America", "Eastern Europe", "Middle East & Africa",
    ])
    def test_correction_recorded_in_critical_errors(self, geo):
        """SC-G1 correction appears in critical_errors (auto-correctable rule)."""
        rec = _base(Geography=geo, Investment_Universe="Global")
        result = _run(rec)
        sg1_errors = [e for e in result["critical_errors"]
                      if "SC-G1" in e.get("rule", "")]
        assert len(sg1_errors) >= 1, (
            f"Expected SC-G1 in critical_errors for Geography='{geo}', "
            f"got {result['critical_errors']}"
        )

    def test_europe_global_iu_canonical_case(self):
        """Canonical bug case: European bond fund with IU='Global' (93 ISINs in DB)."""
        rec = _base(
            Fund_Nature="Renta Fija Corto Plazo",
            Family="Short-Term Fixed Income",
            Geography="Europe",
            Investment_Universe="Global",
        )
        result = _run(rec)
        assert result["corrected_record"]["Investment_Universe"] == "Regional"

    def test_north_america_global_iu(self):
        """NA funds with IU='Global' corrected to 'Regional' (32 ISINs in DB)."""
        rec = _base(Geography="North America", Investment_Universe="Global")
        result = _run(rec)
        assert result["corrected_record"]["Investment_Universe"] == "Regional"


# ─── SC-G1: Country Geography + Global IU → must correct to Country ──────────

class TestSCG1CountryCorrectedToCountry:

    @pytest.mark.parametrize("geo", ["China", "Japan", "India"])
    def test_global_iu_corrected_when_geography_is_country(self, geo):
        """IU='Global' + Country Geography → auto-corrected to 'Country' (SC-G1)."""
        rec = _base(Geography=geo, Investment_Universe="Global")
        result = _run(rec)
        assert result["corrected_record"]["Investment_Universe"] == "Country", (
            f"Expected 'Country' for Geography='{geo}', "
            f"got '{result['corrected_record']['Investment_Universe']}'"
        )

    @pytest.mark.parametrize("geo", ["China", "Japan", "India"])
    def test_country_correction_in_critical_errors(self, geo):
        rec = _base(Geography=geo, Investment_Universe="Global")
        result = _run(rec)
        sg1_errors = [e for e in result["critical_errors"]
                      if "SC-G1" in e.get("rule", "")]
        assert len(sg1_errors) >= 1


# ─── SC-G1: no correction for already-consistent combinations ─────────────────

class TestSCG1NoFalsePositives:

    @pytest.mark.parametrize("geo,iu", [
        ("Europe",        "Regional"),
        ("North America", "Regional"),
        ("Asia-Pacific",  "Regional"),
        ("China",         "Country"),
        ("Japan",         "Country"),
        ("India",         "Country"),
        ("Global",        "Global"),
        (None,            "Global"),   # no Geography → SC-G1 skips
        (None,            "Regional"), # no Geography → skips
        ("Global",        "Regional"), # INTER-10 warns; SC-G1 should not fire
    ])
    def test_correct_combinations_not_touched(self, geo, iu):
        """SC-G1 must not fire when IU already matches the geographic scope."""
        rec = _base(Geography=geo, Investment_Universe=iu)
        result = _run(rec)
        cr = result["corrected_record"]
        sg1_corrections = [e for e in result["critical_errors"]
                           if "SC-G1" in e.get("rule", "")]
        # Verify SC-G1 did not fire
        assert sg1_corrections == [], (
            f"SC-G1 fired unexpectedly for Geography='{geo}' IU='{iu}': "
            f"{sg1_corrections}"
        )
        # Verify IU is unchanged
        assert cr.get("Investment_Universe") == iu, (
            f"IU changed from '{iu}' to '{cr.get('Investment_Universe')}' "
            f"for Geography='{geo}'"
        )

    def test_no_geography_no_correction(self):
        """SC-G1 must not fire if Geography is None (no signal)."""
        rec = _base(Geography=None, Investment_Universe="Global")
        result = _run(rec)
        sg1 = [e for e in result["critical_errors"] if "SC-G1" in e.get("rule", "")]
        assert sg1 == []


# ─── SC-G1 interaction with BL-33 (Monetario/RF Corto fallback) ──────────────

class TestSCG1InteractionWithBL33:
    """BL-33 sets IU='Global' for Monetario/RF Corto regardless of Geography.
    SC-G1 (INTER-20) must override it when Geography is a specific region/country."""

    @pytest.mark.parametrize("nature", ["Monetario", "Renta Fija Corto Plazo"])
    def test_monetario_rf_corto_with_europe_geography(self, nature):
        """Root cause of 93+32 DB inconsistencies: BL-33 forces Global on
        Monetario/RF Corto even when Geography='Europe'/'North America'."""
        rec = {
            "Fund_Nature": nature,
            "Family": "Money Market" if nature == "Monetario" else "Short-Term Fixed Income",
            "Geography": "Europe",
            "Investment_Universe": None,   # block hasn't set IU yet
        }
        result = _run(rec)
        cr = result["corrected_record"]
        # BL-33 would set 'Global'; SC-G1 must then correct to 'Regional'
        assert cr["Investment_Universe"] == "Regional", (
            f"Expected 'Regional' for {nature} + Europe, "
            f"got '{cr['Investment_Universe']}'"
        )

    @pytest.mark.parametrize("nature", ["Monetario", "Renta Fija Corto Plazo"])
    def test_monetario_rf_corto_with_japan_geography(self, nature):
        """Monetario JPY fund: BL-33 forces Global, SC-G1 corrects to Country."""
        rec = {
            "Fund_Nature": nature,
            "Family": "Money Market" if nature == "Monetario" else "Short-Term Fixed Income",
            "Geography": "Japan",
            "Investment_Universe": None,
        }
        result = _run(rec)
        assert result["corrected_record"]["Investment_Universe"] == "Country"

    @pytest.mark.parametrize("nature", ["Monetario", "Renta Fija Corto Plazo"])
    def test_monetario_no_geography_stays_global(self, nature):
        """Without a Geography signal, BL-33 Global is correct; SC-G1 must not fire."""
        rec = {
            "Fund_Nature": nature,
            "Family": "Money Market" if nature == "Monetario" else "Short-Term Fixed Income",
            "Geography": None,
            "Investment_Universe": None,
        }
        result = _run(rec)
        cr = result["corrected_record"]
        sg1 = [e for e in result["critical_errors"] if "SC-G1" in e.get("rule", "")]
        assert cr["Investment_Universe"] == "Global"
        assert sg1 == [], "SC-G1 must not fire when Geography is None"


# ─── SC-G1 interaction with MIG-5 (Liquidity → Global → Regional) ────────────

class TestSCG1AfterMig5:
    """MIG-5 runs before SC-G1. A fund with IU='Liquidity' + Geo='Europe'
    should end up with IU='Regional' (MIG-5: Liquidity→Global, SC-G1: Global→Regional)."""

    def test_liquidity_plus_europe_becomes_regional(self):
        rec = _base(
            Geography="Europe",
            Investment_Universe="Liquidity",  # pre-v20 legacy value
        )
        result = _run(rec)
        assert result["corrected_record"]["Investment_Universe"] == "Regional", (
            "Expected MIG-5 (Liquidity→Global) + SC-G1 (Global→Regional) "
            f"chain, got '{result['corrected_record']['Investment_Universe']}'"
        )

    def test_liquidity_plus_no_geography_stays_global(self):
        """No Geography: MIG-5 converts to 'Global', SC-G1 does not fire."""
        rec = _base(Geography=None, Investment_Universe="Liquidity")
        result = _run(rec)
        assert result["corrected_record"]["Investment_Universe"] == "Global"
