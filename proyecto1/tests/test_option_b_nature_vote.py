# -*- coding: utf-8 -*-
"""
Tests for resolve_nature_vote() — OPT-B (2026-07-16)

Covers the VOTE LOGIC of resolve_nature_vote:
  - 2-of-3 majority combinations (name+kiid, name+bench, kiid+bench)
  - All-disagree → name wins
  - All-abstain → None
  - RF subtype restoration (KIID > name > resolve_rf_subtype)
  - vote_detail structure

Name signals used must match the corpus-specific patterns in NAME_SIGNALS_*
(NAME_SIGNALS are abbreviations of real fund names, not generic English terms).
Verified patterns used below: "aktien" (RV), "insticash" (Monetario),
"patrimoine" (Mixtos), "corto plazo" (RFC), "pfandbrief" (RFC).

R-7: no pipeline.py or core.io imports.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_P1 = _ROOT / "proyecto1"
for _p in [str(_P1), str(_P1 / "core"), str(_P1 / "blocks")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest
from core.classify_utils import resolve_nature_vote


def _kiid(objective_text: str) -> str:
    """Wrap objective text so it lands in the UNKNOWN extraction window (≥200 chars)."""
    return "x" * 260 + " " + objective_text


# ─── majority vote: name + KIID agree ───────────────────────────────────────

class TestNameKiidMajority:
    def test_equity_name_and_kiid(self):
        """name=RV ('aktien'), KIID=RV → Renta Variable; nature correct."""
        nature, detail = resolve_nature_vote(
            "global aktien fonds",     # 'aktien' ∈ NAME_SIGNALS_RV
            _kiid("invierte principalmente en acciones de renta variable global"),
        )
        assert nature == "Renta Variable"
        assert detail["name"] == "Renta Variable"

    def test_monetario_name_and_kiid(self):
        """name=Monetario ('insticash'), KIID=Monetario → Monetario."""
        nature, detail = resolve_nature_vote(
            "insticash euro mmf",      # 'insticash' ∈ NAME_SIGNALS_MONETARIO
            _kiid("fondo del mercado monetario de muy corto plazo VNAV"),
        )
        assert nature == "Monetario"
        assert detail["name"] == "Monetario"

    def test_mixtos_name_and_kiid(self):
        """name=Mixtos ('patrimoine'), KIID=Mixtos → Mixtos."""
        nature, detail = resolve_nature_vote(
            "carmignac patrimoine fund",  # 'patrimoine' ∈ NAME_SIGNALS_MIXTO
            _kiid("invierte en una combinacion equilibrada de acciones y bonos"),
        )
        assert nature == "Mixtos"
        assert detail["name"] == "Mixtos"

    def test_rf_corto_name_and_kiid(self):
        """name=RFC ('corto plazo'), KIID=RFC → Renta Fija Corto Plazo."""
        nature, detail = resolve_nature_vote(
            "euro bonds corto plazo fund",   # 'corto plazo' ∈ NAME_SIGNALS_RF_CORTO
            _kiid("la duracion de la cartera sera inferior a 3 anos sin restriccion"),
        )
        assert nature == "Renta Fija Corto Plazo"
        assert detail["name"] == "Renta Fija Corto Plazo"

    def test_rf_corto_pfandbrief_name(self):
        """name=RFC ('pfandbrief'), KIID with bond signals → name RFC wins."""
        nature, detail = resolve_nature_vote(
            "euro pfandbrief fund",    # 'pfandbrief' ∈ NAME_SIGNALS_RF_CORTO
            _kiid("invierte en bonos garantizados de alta calidad crediticia"),
        )
        assert nature == "Renta Fija Corto Plazo"

    def test_alternativo_name_and_kiid(self):
        """name=Alternativo, KIID=Alternativo → Alternativo."""
        # No specific corpus name pattern; test via KIID-only
        nature, detail = resolve_nature_vote(
            "",
            _kiid("estrategia de retorno absoluto con objetivo de revalorizacion "
                  "positiva independientemente del mercado benchmark cash absolute return"),
        )
        assert nature == "Alternativo"


# ─── majority vote: name + benchmark agree ──────────────────────────────────

class TestNameBenchmarkMajority:
    def test_equity_name_bench_no_kiid(self):
        """name=RV ('aktien'), bench=RV → Renta Variable via name+benchmark."""
        nature, detail = resolve_nature_vote(
            "global aktien fonds",
            "",
            benchmark_declared="MSCI Europe Index",
        )
        assert nature == "Renta Variable"
        assert detail["name"] == "Renta Variable"
        assert detail["benchmark"] == "Renta Variable"
        assert "name+benchmark" in detail["reason"]

    def test_monetario_name_bench(self):
        """name=Monetario ('insticash'), bench=Monetario → Monetario via name+benchmark."""
        nature, detail = resolve_nature_vote(
            "insticash euro",
            "",
            benchmark_declared="€STR overnight rate",
        )
        assert nature == "Monetario"
        assert "name+benchmark" in detail["reason"]

    def test_rf_name_bench_coarse(self):
        """name=RFC ('corto plazo'), bench='Renta Fija' (coarse) → RF majority."""
        nature, detail = resolve_nature_vote(
            "euro corto plazo fund",
            "",
            benchmark_declared="Bloomberg Euro Aggregate Bond Index",
        )
        # name=RFC, bench=Renta Fija (coarse) → coarsened they both = "Renta Fija"
        assert nature in ("Renta Fija Corto Plazo", "Renta Fija Flexible")


# ─── majority vote: KIID + benchmark agree ──────────────────────────────────

class TestKiidBenchmarkMajority:
    def test_equity_kiid_bench_neutral_name(self):
        """KIID=RV, bench=RV, name abstains → Renta Variable via kiid+benchmark."""
        nature, detail = resolve_nature_vote(
            "",
            _kiid("invierte principalmente en acciones de compañías europeas"),
            benchmark_declared="MSCI World Index",
        )
        assert nature == "Renta Variable"
        assert "kiid+benchmark" in detail["reason"]

    def test_rf_kiid_bench_neutral_name(self):
        """KIID=RF, bench=RF, name abstains → RF nature resolved."""
        nature, detail = resolve_nature_vote(
            "",
            _kiid("invierte en bonos de alta calidad crediticia"),
            benchmark_declared="Bloomberg Global Aggregate Bond Index",
        )
        assert nature in ("Renta Fija Corto Plazo", "Renta Fija Flexible")
        assert "kiid+benchmark" in detail["reason"]


# ─── all disagree / fallback ordering ───────────────────────────────────────

class TestFallbackOrdering:
    def test_name_wins_over_kiid_when_no_bench(self):
        """name=RV ('aktien'), KIID=Mixtos, no bench → name wins (only voter with Renta Variable)."""
        nature, detail = resolve_nature_vote(
            "global aktien fonds",
            _kiid("invierte en una combinacion equilibrada de acciones y bonos"),
            benchmark_declared=None,
        )
        # name=RV, kiid=Mixtos — they disagree, no benchmark, name takes fallback
        assert nature == "Renta Variable"
        assert "name-only" in detail["reason"]

    def test_kiid_wins_when_name_abstains(self):
        """name abstains, KIID=RV → KIID wins as only voter."""
        nature, detail = resolve_nature_vote(
            "",
            _kiid("invierte principalmente en acciones de renta variable global"),
            benchmark_declared=None,
        )
        assert nature == "Renta Variable"
        assert "kiid-only" in detail["reason"]

    def test_kiid_wins_when_name_abstains_and_bench_disagrees(self):
        """name abstains, KIID=RV, bench=Monetario → no 2-of-3 → kiid wins."""
        nature, detail = resolve_nature_vote(
            "",
            _kiid("invierte principalmente en acciones de renta variable global"),
            benchmark_declared="€STR overnight rate",
        )
        assert nature == "Renta Variable"
        assert "kiid-only" in detail["reason"]


# ─── all abstain ────────────────────────────────────────────────────────────

class TestAllAbstain:
    def test_all_abstain_returns_none(self):
        """No name, no KIID signal, no benchmark → None."""
        nature, detail = resolve_nature_vote("", "", None)
        assert nature is None
        assert detail["reason"] == "all-abstain"

    def test_neutral_name_and_generic_kiid_no_bench(self):
        """Generic name + very short KIID (no window content) → all abstain."""
        nature, _ = resolve_nature_vote("strategic fund", "short text", None)
        assert nature is None


# ─── RF subtype restoration ──────────────────────────────────────────────────

class TestRFSubtypeRestoration:
    def test_rf_subtype_from_kiid_when_majority_is_rf(self):
        """When name+kiid/kiid+bench majority = RF, KIID's specific subtype is used."""
        # KIID explicitly says RFC (≤3y), name says RFC ('corto plazo')
        nature, detail = resolve_nature_vote(
            "euro corto plazo fund",
            _kiid("la duracion de la cartera sera inferior a 3 anos"),
        )
        assert nature == "Renta Fija Corto Plazo"

    def test_rff_when_kiid_unrestricted(self):
        """Majority=RF + KIID = unrestricted duration → Renta Fija Flexible."""
        nature, detail = resolve_nature_vote(
            "",
            _kiid("invierte principalmente en valores de renta fija de cartera "
                  "diversificada con duracion flexible sin restriccion"),
            benchmark_declared="Bloomberg Global Aggregate Bond Index",
        )
        # KIID says RF (renta fija), bench says Renta Fija → kiid+bench agree
        assert nature == "Renta Fija Flexible"

    def test_rfc_from_name_when_kiid_generic(self):
        """name=RFC, KIID says RF but no specific subtype → name RFC subtype wins."""
        # 'pfandbrief' gives name→RFC; KIID says RF but no specific ≤3y signal
        nature, detail = resolve_nature_vote(
            "euro pfandbrief fund",
            _kiid("invierte en bonos garantizados europeos"),
            benchmark_declared=None,
        )
        # name=RFC, KIID=RF (no subtype) → coarse match → KIID subtype or name subtype
        assert nature == "Renta Fija Corto Plazo"  # name RFC subtype
        assert detail["name"] == "Renta Fija Corto Plazo"


# ─── vote detail structure ───────────────────────────────────────────────────

class TestVoteDetail:
    def test_detail_keys_always_present(self):
        """vote_detail always contains all required keys."""
        _, detail = resolve_nature_vote("global aktien fonds", "", None)
        for key in ("name", "kiid", "benchmark", "winner_coarse", "reason"):
            assert key in detail

    def test_detail_all_none_on_abstain(self):
        """All-abstain detail has None for name/kiid/benchmark/winner_coarse."""
        _, detail = resolve_nature_vote("", "", None)
        assert detail["name"] is None
        assert detail["kiid"] is None
        assert detail["benchmark"] is None
        assert detail["winner_coarse"] is None

    def test_detail_winner_coarse_set_on_majority(self):
        """2-of-3 majority sets winner_coarse to the coarsened nature."""
        _, detail = resolve_nature_vote(
            "global aktien fonds",
            _kiid("invierte principalmente en acciones de renta variable global"),
            benchmark_declared="MSCI World",
        )
        assert detail["winner_coarse"] is not None
        assert detail["name"] == "Renta Variable"
        assert detail["benchmark"] == "Renta Variable"

    def test_benchmark_coarse_value(self):
        """detect_nature_from_benchmark returns coarse 'Renta Fija', not RFC/RFF."""
        _, detail = resolve_nature_vote(
            "",
            "",
            benchmark_declared="Bloomberg Global Aggregate Bond Index",
        )
        assert detail["benchmark"] == "Renta Fija"
