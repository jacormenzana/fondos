# -*- coding: utf-8 -*-
"""
Tests for resolve_nature_evidence() — OPT-B3 (2026-07-16).

Architecture (calibrated against realized-volatility ground truth):
KIID-primary + guarded name-override (Monetario/RFC, where name is 100%
reliable) + coverage-fill (name, then benchmark) + realized-vol veto/correction.
Empirically: symmetric blends (sum 90.6% / argmax 91.6%) UNDERPERFORM
KIID-alone (92.7%); this architecture matches KIID's ceiling (92.6% ex-ante)
with full coverage.

KIID text is wrapped so it lands in the UNKNOWN objective window (>=200 chars).

R-7: no pipeline.py or core.io imports.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_P1 = _ROOT / "proyecto1"
for _p in [str(_P1), str(_P1 / "core"), str(_P1 / "blocks")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.classify_utils import resolve_nature_evidence, _NAME_DOMINANT_NATURES


def _kiid(objective: str) -> str:
    return "x" * 260 + " " + objective


class TestCalibrationInvariant:
    def test_name_dominant_is_monetario_and_rfc(self):
        """The guarded-override set must be exactly {Monetario, RFC} at current
        weights — the only natures where name is measurably >> KIID."""
        assert _NAME_DOMINANT_NATURES == {"Monetario", "Renta Fija Corto Plazo"}


class TestKiidPrimary:
    def test_kiid_leads_for_equity(self):
        nat, conf, tr = resolve_nature_evidence(
            "some fund", _kiid("invierte principalmente en acciones de renta variable"),
        )
        assert nat == "Renta Variable"
        assert tr["primary_source"] == "kiid"

    def test_kiid_leads_over_name_for_rff(self):
        """RFF is NOT name-dominant → KIID wins even if name also fires."""
        nat, conf, tr = resolve_nature_evidence(
            "global bond fund",  # name→RFF
            _kiid("invierte en valores de renta fija con duracion flexible sin restriccion"),
        )
        assert nat == "Renta Fija Flexible"
        assert tr["primary_source"] == "kiid"


class TestGuardedNameOverride:
    def test_name_monetario_overrides(self):
        """name=Monetario (100% reliable) overrides even a non-Monetario KIID."""
        nat, conf, tr = resolve_nature_evidence(
            "euro money market vnav",   # name→Monetario
            _kiid("invierte en valores de renta fija a corto plazo"),  # KIID leans RF
        )
        assert nat == "Monetario"
        assert tr["primary_source"] == "name"

    def test_name_rfc_overrides(self):
        nat, conf, tr = resolve_nature_evidence(
            "euro ultra short duration",  # name→RFC
            _kiid("invierte en una cartera diversificada de bonos"),
        )
        assert nat == "Renta Fija Corto Plazo"
        assert tr["primary_source"] == "name"


class TestCoverageFill:
    def test_name_fills_when_kiid_abstains(self):
        nat, conf, tr = resolve_nature_evidence(
            "global equity fund",   # name→RV
            _kiid("texto sin señal de naturaleza reconocible aqui"),
        )
        assert nat == "Renta Variable"
        assert tr["primary_source"] == "name"

    def test_benchmark_fills_when_name_and_kiid_abstain(self):
        nat, conf, tr = resolve_nature_evidence(
            "opaque fund name",
            _kiid("texto neutro"),
            benchmark_declared="Bloomberg Global Aggregate Bond Index",
        )
        assert nat == "Renta Fija Flexible"   # RF coarse → RFF default
        assert tr["primary_source"] == "benchmark"


class TestVolVetoCorrection:
    def test_impossible_monetario_kept_flagged_when_no_alt(self):
        """P#6: name+KIID both say Monetario but SRRI=5 is impossible for MMF.
        With NO other ex-ante candidate, vol does NOT fabricate a replacement —
        Monetario is kept but flagged low-confidence for review."""
        nat, conf, tr = resolve_nature_evidence(
            "euro money market fund",
            _kiid("fondo del mercado monetario"),
            srri_nav_band=5,
        )
        assert nat == "Monetario"
        assert conf < 0.5

    def test_impossible_monetario_arbitrated_when_alt_proposed(self):
        """When a benchmark proposes an RF nature, an impossible-band Monetario
        primary is arbitrated to the vol-consistent proposed candidate (RFF@4)."""
        nat, conf, tr = resolve_nature_evidence(
            "euro liquidity fund",                       # name→Monetario
            _kiid("fondo del mercado monetario"),        # KIID→Monetario
            benchmark_declared="Bloomberg Euro Aggregate Bond Index",  # →Renta Fija
            srri_nav_band=4,                             # Monetario impossible; RFF fits
        )
        assert nat == "Renta Fija Flexible"
        assert "vol-arbitrated" in tr["reason"]

    def test_vol_consistent_primary_kept(self):
        nat, conf, tr = resolve_nature_evidence(
            "euro money market vnav",
            _kiid("fondo del mercado monetario"),
            srri_nav_band=1,
        )
        assert nat == "Monetario"
        assert "vol-corrected" not in tr["reason"]

    def test_unambiguous_band6_arbitrates_to_proposed_rv(self):
        """The tech-equity KIID false-positive: KIID reads Mixtos (lists eligible
        bonds) but realized SRRI=6 is unambiguous equity. RV is ARBITRATED in
        because the NAME ('technolog') proposed it — vol never invents a nature."""
        nat, conf, tr = resolve_nature_evidence(
            "dws critical technologies",   # name→RV (a real ex-ante candidate)
            _kiid("el fondo invierte en acciones, bonos convertibles y warrants "
                  "sobre acciones; parte se invierte en valores de renta fija"),
            srri_nav_band=6,
        )
        assert nat == "Renta Variable"
        assert "vol-arbitrated" in tr["reason"]

    def test_vol_does_not_invent_unproposed_nature(self):
        """P#6: if the primary is vol-incompatible but NO ex-ante candidate fits
        the band, the primary is KEPT (low-conf flagged) — vol never fabricates."""
        # KIID→Mixtos only (name/benchmark abstain), vol=6. RV was NOT proposed
        # by any signal, so vol must NOT invent it → stays Mixtos, low confidence.
        nat, conf, tr = resolve_nature_evidence(
            "opaque fund xyz",
            _kiid("invierte en una combinacion equilibrada de acciones y bonos"),
            srri_nav_band=6,
        )
        assert nat == "Mixtos"
        assert conf < 0.5   # kept but flagged for review

    def test_middle_band_does_not_over_correct(self):
        """Band 4 is FUZZY (Mixtos/RFF/Alt overlap) → adjacent inconsistency is
        NOT corrected (avoids vol dominating / circularity)."""
        # KIID→RV, vol=4 (adjacent to RV band {5,6,7}); must NOT flip to fallback
        nat, conf, tr = resolve_nature_evidence(
            "some fund",
            _kiid("invierte principalmente en acciones de renta variable"),
            srri_nav_band=4,
        )
        assert nat == "Renta Variable"
        assert "vol-corrected" not in tr["reason"]


class TestFallbackAndAbstain:
    def test_no_vol_fallback_when_all_exante_abstain(self):
        """P#6: vol NEVER derives nature. All ex-ante abstain → None even if a
        volatility band is present (was previously a vol-fallback = P#6 breach)."""
        nat, conf, tr = resolve_nature_evidence(
            "opaque", _kiid("neutro"), benchmark_declared=None, srri_nav_band=6,
        )
        assert nat is None
        assert tr["reason"] == "all-abstain"
        assert conf == 0.0

    def test_all_abstain_returns_none(self):
        nat, conf, tr = resolve_nature_evidence("opaque", _kiid("neutro"), None, None)
        assert nat is None
        assert conf == 0.0
        assert tr["reason"] == "all-abstain"


class TestTraceAndConfidence:
    def test_trace_keys_present(self):
        _, _, tr = resolve_nature_evidence(
            "global equity", _kiid("invierte en acciones"), "MSCI World", 6)
        for k in ("name", "kiid", "benchmark", "srri_nav_band",
                  "primary", "primary_source", "winner", "confidence", "reason"):
            assert k in tr

    def test_corroboration_raises_confidence(self):
        """Full agreement (name+kiid+bench+vol) → high confidence."""
        nat, conf, tr = resolve_nature_evidence(
            "global equity growth",
            _kiid("invierte principalmente en acciones de renta variable"),
            benchmark_declared="MSCI World Index",
            srri_nav_band=6,
        )
        assert nat == "Renta Variable"
        assert conf >= 0.9
