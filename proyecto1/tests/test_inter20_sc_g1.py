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


# ─── INTER-10 / SC-G1 ordering: no redundant 'inusual' WARN (FIX-INTER10-ORDER) ──

class TestInter10OrderAfterSCG1:
    """After the 2026-07-13 reorder, INTER-10 evaluates the post-SC-G1 IU.
    Funds that SC-G1 corrects (Global→Country/Regional) must NOT trigger the
    INTER-10 'es inusual' WARN — SC-G1's Geography-Universe-SC-G1 correction
    message is the only signal. INTER-10 reverse-WARN and BL-52 must still work.

    Root case: PICTET S-T MONEY MKT JPY (LU0309035441/870) emitted 2×
    'Geography-Universe: Geography específica Japan con Universe=Global es inusual'
    in log_pipeline_20260712_234540.log even though IU was corrected to Country."""

    def test_monetario_japan_no_inusual_warn(self):
        """PICTET JPY pattern: Monetario + Japan + IU=Global → SC-G1 corrects to
        Country; INTER-10 must NOT emit the 'inusual' WARN afterwards."""
        rec = {
            "Fund_Nature": "Monetario",
            "Family": "Money Market",
            "Geography": "Japan",
            "Investment_Universe": "Global",  # BL-MON-U1 / BL-33 set this
        }
        result = _run(rec)
        cr = result["corrected_record"]
        # SC-G1 corrected it
        assert cr["Investment_Universe"] == "Country"
        # INTER-10 must NOT have emitted its old 'inusual' WARNING
        inusual_warns = [
            e for e in result["warnings"]
            if e.get("rule") == "Geography-Universe"
            and "inusual" in e.get("message", "")
        ]
        assert inusual_warns == [], (
            f"INTER-10 emitted redundant 'inusual' WARN after SC-G1 already "
            f"corrected IU to 'Country': {inusual_warns}"
        )
        # SC-G1 correction is still recorded
        sg1 = [e for e in result["critical_errors"] if "SC-G1" in e.get("rule", "")]
        assert len(sg1) >= 1

    def test_europe_global_no_inusual_warn(self):
        """RF Corto + Europe + IU=Global → SC-G1 corrects to Regional;
        INTER-10 must NOT emit 'inusual' WARN for a region geography."""
        rec = {
            "Fund_Nature": "Renta Fija Corto Plazo",
            "Family": "Short-Term Fixed Income",
            "Geography": "Europe",
            "Investment_Universe": "Global",
        }
        result = _run(rec)
        cr = result["corrected_record"]
        assert cr["Investment_Universe"] == "Regional"
        inusual_warns = [
            e for e in result["warnings"]
            if e.get("rule") == "Geography-Universe"
            and "inusual" in e.get("message", "")
        ]
        # Europe is in _REGION_GEOGRAPHIES, not _COUNTRY_GEOGRAPHIES, so
        # INTER-10 only warns on country+Global; this case is clean.
        assert inusual_warns == []

    def test_bl52_still_fires_after_sc_g1(self):
        """BL-52 (Country→Regional) in INTER-10 must still work after the reorder.
        SC-G1 does not touch this case (Country is not 'Global'), so INTER-10
        remains the sole handler."""
        rec = _base(Geography="Europe", Investment_Universe="Country")
        result = _run(rec)
        cr = result["corrected_record"]
        assert cr["Investment_Universe"] == "Regional", (
            "BL-52 (INTER-10) must still auto-correct Country→Regional when "
            f"Geography is a region; got '{cr['Investment_Universe']}'"
        )
        # Correction should appear in critical_errors with rule 'Geography-Universe'
        bl52 = [e for e in result["critical_errors"]
                if e.get("rule") == "Geography-Universe"]
        assert len(bl52) >= 1

    def test_reverse_warn_still_fires(self):
        """INTER-10 reverse WARN: Geography='Global' + IU='Country' must still
        emit a 'Geography-Universe' warning (SC-G1 never touches this direction)."""
        rec = _base(Geography="Global", Investment_Universe="Country")
        result = _run(rec)
        reverse_warns = [
            e for e in result["warnings"]
            if e.get("rule") == "Geography-Universe"
        ]
        assert len(reverse_warns) >= 1, (
            "INTER-10 reverse WARN (Geography='Global' + IU='Country') must "
            "still fire after the reorder"
        )
