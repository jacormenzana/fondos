"""
Tests for RFC-vs-RFF ≤3y duration boundary (RF-RFF-POLICY-2026-07-16).

Canonical rule:
    Mandated max duration ≤ 3 years → RF_Corto  (Renta Fija Corto Plazo)
    Unrestricted / duration > 3 years → RF_Flexible (Renta Fija Flexible)

Standard: ICE BofA 1-3y bucket / Morningstar Short-Term Bond category.
Duration_Profile {Ultra-Short, Short} → RF_Corto;
Duration_Profile {Intermediate, Long, Flexible} → RF_Flexible.

Runnable without pipeline.py or core.io imports (R-7).

Window note (R-6): resolve_rf_subtype() extracts a character window from the
KIID text.  For "UNKNOWN" format documents (no DDF/KIID header) the window is
[200, 4500].  Tests use _make_kiid() to prepend 260 "x" characters so that the
objective text falls inside the window — same technique used across the test
suite (see test_geography.py:_kiid()).
"""
from __future__ import annotations

import pytest
from proyecto1.core.classify_utils import resolve_rf_subtype


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_kiid(objective_text: str) -> str:
    """Prepend 260 'x' chars so objective_text falls in the UNKNOWN window [200-4500]."""
    return "x" * 260 + " " + objective_text


_NEUTRAL_NAME = ""  # empty name — avoids accidental NAME_SIGNALS_RF_CORTO hits


def _rfc(name: str, kiid: str) -> bool:
    """True if resolve_rf_subtype returns RF_Corto."""
    return resolve_rf_subtype(name, kiid) == "RF_Corto"


def _rff(name: str, kiid: str) -> bool:
    """True if resolve_rf_subtype returns RF_Flexible."""
    return resolve_rf_subtype(name, kiid) == "RF_Flexible"


# ─────────────────────────────────────────────────────────────────────────────
# A. KIID-text signals → RF_Corto (≤ 3y or explicit short-duration)
# ─────────────────────────────────────────────────────────────────────────────

class TestRFCortoKIIDSignals:
    """KIID-text based signals anchored to ≤ 3 years → RF_Corto."""

    def test_1_to_3_year_range_es(self):
        kiid = _make_kiid("El fondo invierte en bonos con vencimientos de 1 a 3 años.")
        assert _rfc(_NEUTRAL_NAME, kiid), "1 a 3 años → RF_Corto"

    def test_0_to_3_year_range_en(self):
        kiid = _make_kiid("The fund invests in bonds with maturities of 0 to 3 years.")
        assert _rfc(_NEUTRAL_NAME, kiid), "0 to 3 years → RF_Corto"

    def test_menos_de_3_anos(self):
        kiid = _make_kiid("La duración media es siempre menos de 3 años.")
        assert _rfc(_NEUTRAL_NAME, kiid), "menos de 3 años → RF_Corto"

    def test_below_3_year_en(self):
        kiid = _make_kiid("Portfolio duration is maintained below 3 years at all times.")
        assert _rfc(_NEUTRAL_NAME, kiid), "below 3 years → RF_Corto"

    def test_duracion_inferior_adjacent(self):
        """'duración inferior' is an existing signal — must be adjacent in text."""
        kiid = _make_kiid("El fondo tiene duración inferior a 2 años en todo momento.")
        assert _rfc(_NEUTRAL_NAME, kiid), "duración inferior (adjacent) → RF_Corto"

    def test_ultra_short_keyword(self):
        kiid = _make_kiid("El fondo es de renta fija ultra short, duración inferior a 1 año.")
        assert _rfc(_NEUTRAL_NAME, kiid), "ultra short → RF_Corto"

    def test_short_duration_en(self):
        kiid = _make_kiid("The fund maintains a short duration profile, typically below 2 years.")
        assert _rfc(_NEUTRAL_NAME, kiid), "short duration → RF_Corto"

    def test_baja_duracion_es(self):
        kiid = _make_kiid("El fondo mantiene una baja duración, generalmente inferior a 2 años.")
        assert _rfc(_NEUTRAL_NAME, kiid), "baja duración → RF_Corto"

    def test_duracion_no_superior_12_meses(self):
        """Existing signal (FIX-P1-RFC1): ≤ 12 months, well within ≤ 3y."""
        kiid = _make_kiid(
            "La duración media de los tipos de interés del subfondo "
            "no será superior a 12 meses en ningún momento."
        )
        assert _rfc(_NEUTRAL_NAME, kiid), "≤ 12 meses → RF_Corto"

    def test_duracion_no_superior_24_meses(self):
        """NEW (RF-RFF-POLICY-2026-07-16): 24 months = 2 years ≤ 3y → RF_Corto."""
        kiid = _make_kiid(
            "La duración modificada de la cartera no podrá ser superior "
            "a 24 meses en condiciones normales de mercado."
        )
        assert _rfc(_NEUTRAL_NAME, kiid), "≤ 24 meses → RF_Corto"

    def test_duracion_no_superior_36_meses(self):
        """NEW (RF-RFF-POLICY-2026-07-16): 36 months = 3 years → RF_Corto."""
        kiid = _make_kiid(
            "La duración media de la cartera no será superior a 36 meses."
        )
        assert _rfc(_NEUTRAL_NAME, kiid), "≤ 36 meses → RF_Corto"

    def test_duracion_no_superior_3_anos_es(self):
        """NEW (RF-RFF-POLICY-2026-07-16): explicit ≤ 3 years in Spanish ('no será superior a')."""
        kiid = _make_kiid(
            "La duración media de la cartera no será superior a 3 años "
            "en condiciones normales."
        )
        assert _rfc(_NEUTRAL_NAME, kiid), "no será superior a 3 años → RF_Corto"

    def test_inferior_a_3_anos_es(self):
        """NEW (RF-RFF-POLICY-2026-07-16): 'inferior a 3 años' (< 3y) → RF_Corto."""
        kiid = _make_kiid("La duración media es inferior a 3 años en todo momento.")
        assert _rfc(_NEUTRAL_NAME, kiid), "inferior a 3 años → RF_Corto"

    def test_inferior_a_2_anos_es(self):
        """'inferior a 2 años' → RF_Corto."""
        kiid = _make_kiid("El subfondo mantiene una duración inferior a 2 años.")
        assert _rfc(_NEUTRAL_NAME, kiid), "inferior a 2 años → RF_Corto"

    def test_hasta_3_anos_es(self):
        """NEW (RF-RFF-POLICY-2026-07-16): 'hasta 3 años' → RF_Corto."""
        kiid = _make_kiid("El subfondo invierte en bonos con una duración de hasta 3 años.")
        assert _rfc(_NEUTRAL_NAME, kiid), "hasta 3 años → RF_Corto"

    def test_up_to_3_years_en(self):
        """NEW (RF-RFF-POLICY-2026-07-16): 'up to 3 years' → RF_Corto."""
        kiid = _make_kiid("The fund invests in bonds with a duration of up to 3 years.")
        assert _rfc(_NEUTRAL_NAME, kiid), "up to 3 years → RF_Corto"

    def test_duration_not_exceeding_3_years_en(self):
        """NEW (RF-RFF-POLICY-2026-07-16): explicit NOT exceeding 3 years."""
        kiid = _make_kiid("The modified duration shall not exceed 3 years.")
        assert _rfc(_NEUTRAL_NAME, kiid), "not exceed 3 years → RF_Corto"

    def test_duration_no_more_than_2_years_en(self):
        """NEW (RF-RFF-POLICY-2026-07-16): 'no more than N years' → RF_Corto."""
        kiid = _make_kiid("The portfolio duration is no more than 2 years.")
        assert _rfc(_NEUTRAL_NAME, kiid), "no more than 2 years → RF_Corto"

    def test_short_term_bond_keyword(self):
        kiid = _make_kiid("El fondo invierte en short-term bond con vencimiento inferior a 2 años.")
        assert _rfc(_NEUTRAL_NAME, kiid), "short-term bond → RF_Corto"

    def test_target_maturity_fund_horizon(self):
        """'horizon 202x' = target maturity within ≤3y from 2026."""
        kiid = _make_kiid("El fondo horizon 2027 tiene como objetivo una fecha de vencimiento fija.")
        assert _rfc(_NEUTRAL_NAME, kiid), "horizon 202x target-maturity → RF_Corto"

    def test_corto_plazo_direct_signal(self):
        """'corto plazo' in objective context (not boilerplate) → RF_Corto."""
        kiid = _make_kiid("El fondo invierte en instrumentos de deuda a corto plazo europeos.")
        assert _rfc(_NEUTRAL_NAME, kiid), "corto plazo → RF_Corto"

    def test_1_a_3_anio_explicit(self):
        """Direct 1-3 year range (Spanish variant)."""
        kiid = _make_kiid("La cartera tiene vencimientos de 1 a 3 años típicamente.")
        assert _rfc(_NEUTRAL_NAME, kiid), "1 a 3 años → RF_Corto"

    def test_superior_a_3_anos_signal(self):
        """'(no ... superior a 3 años)' substring signal → RF_Corto."""
        kiid = _make_kiid("La duración no será superior a 3 años en ningún caso.")
        assert _rfc(_NEUTRAL_NAME, kiid), "superior a 3 años → RF_Corto"


# ─────────────────────────────────────────────────────────────────────────────
# B. NAME-based signals → RF_Corto
# ─────────────────────────────────────────────────────────────────────────────

class TestRFCortoNameSignals:
    """Fund-name based signals from NAME_SIGNALS_RF_CORTO."""

    def test_covered_bonds_pfandbrief(self):
        assert _rfc("pfandbrief fonds", ""), "pfandbrief name → RF_Corto"

    def test_floating_rate_notes_name(self):
        assert _rfc("allianz float rate notes", ""), "float rate notes name → RF_Corto"

    def test_covered_bond_name(self):
        assert _rfc("eur covered bond fund", ""), "covered bond name → RF_Corto"

    def test_dur_bond_abbreviation(self):
        """'dur bond' in abbreviated fund names (e.g. BGF EURO SHORT DUR BOND)."""
        assert _rfc("bgf euro short dur bond", ""), "dur bond abbreviation → RF_Corto"

    def test_ultrashort_name_abbreviation(self):
        assert _rfc("invesco ult sh term bd", ""), "ult sh term abbreviation → RF_Corto"


# ─────────────────────────────────────────────────────────────────────────────
# C. Signals that MUST return RF_Flexible (> 3y or unrestricted)
# ─────────────────────────────────────────────────────────────────────────────

class TestRFFlexibleSignals:
    """Signals implying duration > 3y or unrestricted → RF_Flexible."""

    def test_no_duration_restriction(self):
        """Unrestricted mandate → RF_Flexible (default)."""
        kiid = _make_kiid(
            "El fondo invierte en una amplia gama de instrumentos de renta fija "
            "sin restricciones de duración ni calidad crediticia."
        )
        assert _rff(_NEUTRAL_NAME, kiid), "unrestricted → RF_Flexible"

    def test_aggregate_bond_fund(self):
        """Aggregate bond (1-10y) → RF_Flexible."""
        kiid = _make_kiid(
            "The fund aims to replicate the Bloomberg Euro Aggregate Bond Index, "
            "which covers investment grade bonds with durations from 1 to 10 years."
        )
        assert _rff(_NEUTRAL_NAME, kiid), "aggregate 1-10y → RF_Flexible"

    def test_intermediate_duration_explicit(self):
        """Explicit 3-7y intermediate → RF_Flexible."""
        kiid = _make_kiid("The fund targets an intermediate duration profile of 3 to 7 years.")
        assert _rff(_NEUTRAL_NAME, kiid), "3-7y intermediate → RF_Flexible"

    def test_long_duration_explicit(self):
        """Long-duration fund (> 7y) → RF_Flexible."""
        kiid = _make_kiid(
            "El fondo invierte en bonos de larga duración con vencimientos superiores a 10 años."
        )
        assert _rff(_NEUTRAL_NAME, kiid), "long duration > 7y → RF_Flexible"

    def test_flexible_mandate_no_restriction(self):
        """Explicitly flexible mandate → RF_Flexible."""
        kiid = _make_kiid(
            "El fondo tiene un mandato completamente flexible sobre duración, "
            "calidad crediticia y moneda, sin restricciones de duración."
        )
        assert _rff(_NEUTRAL_NAME, kiid), "flexible mandate → RF_Flexible"

    def test_inflation_linked_bonds(self):
        """Inflation-linked bonds: always RF_Flexible (existing guard at function top)."""
        kiid = _make_kiid(
            "El fondo invierte en bonos indexados a la inflación, principalmente "
            "en renta fija ligada a la inflación denominada en euros."
        )
        assert _rff(_NEUTRAL_NAME, kiid), "inflation-linked → RF_Flexible"

    def test_high_yield_no_duration_signal(self):
        """HY bond fund without explicit short-duration signal → RF_Flexible (default)."""
        kiid = _make_kiid(
            "El fondo invierte principalmente en bonos de alto rendimiento (high yield) "
            "de empresas europeas. No existe restricción de duración."
        )
        assert _rff(_NEUTRAL_NAME, kiid), "HY unrestricted → RF_Flexible"

    def test_emerging_market_debt_no_duration(self):
        """EM debt fund without duration restriction → RF_Flexible."""
        kiid = _make_kiid(
            "The fund invests in emerging market debt across local and hard currency "
            "instruments. The portfolio has no fixed duration constraint."
        )
        assert _rff(_NEUTRAL_NAME, kiid), "EM debt unrestricted → RF_Flexible"

    def test_empty_kiid_generic_name(self):
        """Empty KIID text with non-RFC name → RF_Flexible (default)."""
        assert _rff("bond fund global alpha", ""), "empty KIID, generic name → RF_Flexible"

    def test_3_to_5_year_range_is_rff(self):
        """3-5y range: starts at boundary but extends past it → RF_Flexible."""
        kiid = _make_kiid("The fund invests in bonds with maturities of 3 to 5 years.")
        assert _rff(_NEUTRAL_NAME, kiid), "3-5y range extends past boundary → RF_Flexible"


# ─────────────────────────────────────────────────────────────────────────────
# D. False-positive guards (must NOT flip RFF→RFC)
# ─────────────────────────────────────────────────────────────────────────────

class TestFalsePositiveGuards:
    """Boilerplate phrases that must NOT trigger RF_Corto (FIX-P1-RFF1, RFC2, RFC3)."""

    def test_mmf_boilerplate_corto_plazo(self):
        """FIX-P1-RFF1: 'instrumentos del mercado monetario...a corto plazo' is MMF
        boilerplate about WHAT money-market instruments are, not the fund's own duration.
        Must remain RF_Flexible even though 'corto plazo' is present in the window."""
        kiid = _make_kiid(
            "El fondo invierte en bonos corporativos europeos. "
            "Los instrumentos del mercado monetario (es decir, títulos de deuda con "
            "vencimientos a corto plazo) se usan para gestión de liquidez. "
            "La duración de la cartera no está restringida."
        )
        assert _rff(_NEUTRAL_NAME, kiid), "MMF boilerplate 'corto plazo' must not flip to RFC"

    def test_estr_benchmark_corto_plazo(self):
        """FIX-P1-RFF1: '€STR representa el tipo de interés a corto plazo' describes
        the BENCHMARK, not the fund. Must remain RF_Flexible."""
        kiid = _make_kiid(
            "El índice de referencia del fondo es el €STR, que representa el tipo de "
            "interés a corto plazo en euros. El fondo invierte en bonos de duración "
            "media a larga sin restricciones."
        )
        assert _rff(_NEUTRAL_NAME, kiid), "€STR benchmark description must not flip to RFC"

    def test_negated_maturity_boilerplate(self):
        """FIX-P1-RFC2: 'el fondo no tiene fecha de vencimiento' is open-ended
        fund boilerplate (NOT a target-maturity signal). Must remain RF_Flexible."""
        kiid = _make_kiid(
            "El fondo no tiene fecha de vencimiento y el inversor puede reembolsar "
            "sus participaciones en cualquier día hábil."
        )
        assert _rff(_NEUTRAL_NAME, kiid), "negated maturity = open-ended, must stay RFF"

    def test_euro_short_term_rate_benchmark_name(self):
        """'euro short term rate' in the benchmark reference line must not trigger RFC."""
        kiid = _make_kiid(
            "El índice de referencia del fondo es el Euro Short Term Rate (€STR). "
            "El fondo invierte en bonos corporativos europeos de duración sin restricción."
        )
        assert _rff(_NEUTRAL_NAME, kiid), "€STR name in benchmark must not flip to RFC"


# ─────────────────────────────────────────────────────────────────────────────
# E. Boundary cases (exactly at the ≤3y / >3y frontier)
# ─────────────────────────────────────────────────────────────────────────────

class TestBoundaryCases:
    """Cases at or around the 3-year threshold."""

    def test_exactly_3_years_explicit_cap_es(self):
        """Duration capped exactly at 3 years → RF_Corto (boundary inclusive ≤3y)."""
        kiid = _make_kiid("La duración media de la cartera no será superior a 3 años.")
        assert _rfc(_NEUTRAL_NAME, kiid), "exactly 3y cap ('no superior a 3 años') → RF_Corto"

    def test_exactly_3_years_inferior_a(self):
        """'inferior a 3 años' → RF_Corto."""
        kiid = _make_kiid("La duración media de la cartera es inferior a 3 años.")
        assert _rfc(_NEUTRAL_NAME, kiid), "inferior a 3 años → RF_Corto"

    def test_1_to_3_year_range_inclusive(self):
        """1 a 3 años range is entirely ≤3y → RF_Corto."""
        kiid = _make_kiid("El subfondo invierte en bonos con vencimientos de 1 a 3 años.")
        assert _rfc(_NEUTRAL_NAME, kiid), "1-3y range → RF_Corto"

    def test_3_to_7_year_range_is_rff(self):
        """3-7y range is Intermediate → RF_Flexible."""
        kiid = _make_kiid("The fund maintains an intermediate duration of 3 to 7 years.")
        assert _rff(_NEUTRAL_NAME, kiid), "3-7y range → RF_Flexible"

    def test_4_year_duration_explicitly(self):
        """Explicit 4-year duration > 3y → RF_Flexible (no ≤3y signal fires)."""
        kiid = _make_kiid("The fund maintains a portfolio duration of approximately 4 years.")
        assert _rff(_NEUTRAL_NAME, kiid), "4y duration > 3y → RF_Flexible"

    def test_36_months_equals_3_years(self):
        """36 months = 3 years → RF_Corto (≤3y boundary inclusive)."""
        kiid = _make_kiid("La duración media de la cartera no será superior a 36 meses.")
        assert _rfc(_NEUTRAL_NAME, kiid), "36 meses = 3 años → RF_Corto"

    def test_hasta_3_anos_en_boundary(self):
        """'hasta 3 años' = up to 3 years (inclusive) → RF_Corto."""
        kiid = _make_kiid("El subfondo mantiene vencimientos de hasta 3 años.")
        assert _rfc(_NEUTRAL_NAME, kiid), "hasta 3 años → RF_Corto"
