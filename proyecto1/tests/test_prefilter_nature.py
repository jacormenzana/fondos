# -*- coding: utf-8 -*-
"""
Tests for detect_nature_from_prefilter() and the six block predicates
— OPT-B2 (2026-07-16).

These centralize the block get_universe_isins() include/exclude patterns
(Set #1) into classify_utils as the reusable medium-weight name signal for
the evidence-weighted classifier. Faithfulness to each block's is_candidate()
is verified corpus-wide elsewhere; here we lock the behavioral contract:
predicate membership, first-match precedence, and residual (None) coverage.

R-7: no pipeline.py or core.io imports.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_P1 = _ROOT / "proyecto1"
for _p in [str(_P1), str(_P1 / "core"), str(_P1 / "blocks")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.classify_utils import (
    detect_nature_from_prefilter,
    _prefilter_match_monetario,
    _prefilter_match_rf_corto,
    _prefilter_match_rf_flexible,
    _prefilter_match_renta_variable,
    _prefilter_match_mixtos,
    _prefilter_match_alternativo,
)


# ─── per-predicate include / exclude behavior ────────────────────────────────

class TestMonetario:
    def test_money_market_included(self):
        assert _prefilter_match_monetario("euro money market fund") is True

    def test_inscash_abbrev(self):
        assert _prefilter_match_monetario("bnp paribas inscash eur 3m") is True

    def test_bond_excluded_even_with_treasury(self):
        # "treasury" is include, but "bond" is exclude → exclude wins
        assert _prefilter_match_monetario("us treasury bond fund") is False

    def test_income_excludes(self):
        assert _prefilter_match_monetario("cash income plus") is False


class TestRfCorto:
    def test_short_duration(self):
        assert _prefilter_match_rf_corto("euro short duration credit") is True

    def test_ultra_short(self):
        assert _prefilter_match_rf_corto("ab ultra short bond") is True

    def test_money_market_excluded(self):
        assert _prefilter_match_rf_corto("short term money market") is False

    def test_equity_excluded(self):
        assert _prefilter_match_rf_corto("short term equity income") is False


class TestRfFlexible:
    def test_high_yield(self):
        assert _prefilter_match_rf_flexible("global high yield bond") is True

    def test_hy_word_boundary(self):
        assert _prefilter_match_rf_flexible("gs global hy e acc") is True

    def test_edr_bond_alloc_before_allocation_exclude(self):
        # "allocation" is exclude, but the edr special-case returns True first
        assert _prefilter_match_rf_flexible("edr bond allocation a eur") is True

    def test_equity_excluded(self):
        assert _prefilter_match_rf_flexible("global equity high conviction") is False


class TestRentaVariable:
    def test_equity(self):
        assert _prefilter_match_renta_variable("global equity fund") is True

    def test_shares_word_boundary_blocks_ishares_bond(self):
        # bare "shares" needs \b; "ishares ... bnd" is excluded upstream anyway
        assert _prefilter_match_renta_variable("ishares global corp indx") is False

    def test_bond_excluded(self):
        assert _prefilter_match_renta_variable("global bond opportunities") is False

    def test_convertible_prefix_excluded(self):
        assert _prefilter_match_renta_variable("jpm global conver (eur)") is False


class TestMixtos:
    def test_balanced(self):
        assert _prefilter_match_mixtos("global balanced fund") is True

    def test_allocation_regex(self):
        assert _prefilter_match_mixtos("bgf global alloca f hed") is True

    def test_confirmed_non_mixtos_excluded(self):
        # "templeton growth" is in the confirmed-non-mixtos exclude regex
        assert _prefilter_match_mixtos("templeton growth euro") is False


class TestAlternativo:
    def test_market_neutral(self):
        assert _prefilter_match_alternativo("global market neutral fund") is True

    def test_pictet_fixed_income_special_case(self):
        # "fixed income" is exclude, but the pictet special-case returns True first
        assert _prefilter_match_alternativo("pictet fixed income opportunities") is True

    def test_balanced_excluded(self):
        assert _prefilter_match_alternativo("balanced absolute return") is False


# ─── first-match precedence (clarity order) ──────────────────────────────────

class TestPrecedence:
    def test_rv_beats_mixtos(self):
        """The key legacy bug fix: 'equity' (RV) + 'growth' (Mixtos) → RV wins,
        because RV is checked before Mixtos. INTER-DBLCLAIM used to patch this."""
        assert detect_nature_from_prefilter("global equity growth fund") == "Renta Variable"

    def test_alternativo_beats_rv_on_global_macro(self):
        """'global macro' (Alt) + 'global' (RV) → Alternativo, Alt checked first."""
        assert detect_nature_from_prefilter("global macro opportunities") == "Alternativo"

    def test_monetario_first(self):
        assert detect_nature_from_prefilter("euro money market vnav") == "Monetario"

    def test_mixtos_when_only_style_word(self):
        assert detect_nature_from_prefilter("global balanced allocation") == "Mixtos"


# ─── residual coverage (None) ────────────────────────────────────────────────

class TestResidual:
    def test_obscure_name_returns_none(self):
        """Names matching no block pattern → None (legacy 'restantes' residual)."""
        assert detect_nature_from_prefilter("carmignac patrimoine a eur") is None

    def test_empty_returns_none(self):
        assert detect_nature_from_prefilter("") is None

    def test_none_is_residual_not_error(self):
        assert detect_nature_from_prefilter("xyz random fund name") is None
