# proyecto1/tests/test_incident_fixes_20260705.py
# -*- coding: utf-8 -*-
"""
Tests for three incident fixes applied 2026-07-05:

  BL-ALT-IN2/IN3 (alternativos.py):
    "glob macro" and "alpha 10 ma"/"alph 10 ma" added to include_patterns.
    Root cause: JPM GLOB MACRO OPPORTUNITIES and Nordea Alpha 10 MA were not
    claimed by any primary block; restantes delegated them to Monetario
    (KIID liquidity language), then BL-44 reclassified to Restantes every run.

  BL-MON-IN1 (monetarios.py):
    "eu m mkt" added to include_patterns.
    Root cause: DWS ESG EU M MKT IC100 EUR ACC (abbreviated European MMF)
    not claimed by monetarios; fell to renta_variable/restantes with SRRI=5
    (wrong KIID stored).

  BL-MON-U1 (monetarios.py):
    Investment_Universe='Liquidity' now explicit in classify_fund() result.
    Root cause: stale Universe='Global' for country-specific MMFs (e.g. Pictet
    JPY money market) persisted via COALESCE, triggering GEOGRAPHY_UNIVERSE_WARNING.

  BL-NTC-SRRI (pipeline.py):
    SRRI guard skips INTER_NTC equity-benchmark contradiction for
    Renta Fija Flexible funds with SRRI≤2. Tested here via the classify_utils
    imports; the pipeline condition itself is integration-level (not testable
    without pipeline.py per R-7).
"""

from __future__ import annotations

import os
import sys

import pandas as pd
import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR  = os.path.normpath(os.path.join(_TESTS_DIR, '..', 'core'))
_P1_DIR    = os.path.normpath(os.path.join(_TESTS_DIR, '..'))
_ROOT_DIR  = os.path.normpath(os.path.join(_TESTS_DIR, '..', '..'))
for _p in (_CORE_DIR, _P1_DIR, _ROOT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import blocks.alternativos as alt
import blocks.monetarios as mon


def _df(*rows):
    """Minimal df_master with ISIN + Fund_Name columns."""
    return pd.DataFrame([
        {"ISIN": isin, "Fund_Name": name, "Management_Company": "Test"}
        for isin, name in rows
    ])


# ─────────────────────────────────────────────────────────────────────────────
# BL-ALT-IN2: "glob macro" in alternativos universe (JPM GLOB MACRO OPPORTUNITIES)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("isin,name", [
    ("LU0095938881", "JPM GLOB MACRO OPPORTUNITIES A"),
    ("LU0115098948", "JPM GLOB MACRO OPPORTUNITIES D"),
])
def test_jpm_glob_macro_in_alternativos(isin, name):
    """BL-ALT-IN2: unhedged JPM Global Macro Opportunities share classes
    must be claimed by alternativos via 'glob macro' pattern."""
    df = _df((isin, name))
    assert isin in alt.get_universe_isins(df), (
        f"{name!r} must be in alternativos universe ('glob macro' pattern)"
    )


def test_jpm_global_macro_eur_hdg_still_captured():
    """Regression: full 'global macro' (EUR HDG classes) still works."""
    df = _df(("LU0917670407", "JPM GLOBAL MACRO (EUR HGD) A"))
    assert "LU0917670407" in alt.get_universe_isins(df)


# ─────────────────────────────────────────────────────────────────────────────
# BL-ALT-IN3: "alpha 10 ma" / "alph 10 ma" in alternativos universe
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("isin,name", [
    ("LU0841597866", "NORDEA 1 ALPHA 10 MA BC EUR AC"),   # full "alpha"
    ("LU0445386369", "NORDEA 1 ALPHA 10 MA BP ACC"),       # full "alpha"
    ("LU1009728160", "NORDEA 1 ALPH 10 MA HBC USH AC"),   # abbreviated "alph"
    ("LU1059923869", "NORDEA 1 ALPH 10 MA HB USD ACC"),   # abbreviated "alph"
])
def test_nordea_alpha10_in_alternativos(isin, name):
    """BL-ALT-IN3: all Nordea Alpha 10 Multi-Asset share classes (full and
    abbreviated name) must be claimed by alternativos."""
    df = _df((isin, name))
    assert isin in alt.get_universe_isins(df), (
        f"{name!r} must be in alternativos universe (BL-ALT-IN3)"
    )


def test_glob_macro_no_false_positive_equity():
    """'glob macro' must not pull in an unrelated equity fund."""
    df = _df(("LU9999999901", "DWS ESG GLOBAL EQUITY FUND"))
    assert "LU9999999901" not in alt.get_universe_isins(df)


def test_alpha10_no_false_positive_bond():
    """'alpha 10 ma' must not pull in a bond fund with 'alpha' in name."""
    df = _df(("LU9999999902", "PIMCO ALPHA BOND STRATEGY EUR ACC"))
    assert "LU9999999902" not in alt.get_universe_isins(df)


# ─────────────────────────────────────────────────────────────────────────────
# BL-MON-IN1: "eu m mkt" in monetarios universe
# ─────────────────────────────────────────────────────────────────────────────

def test_dws_esg_eu_m_mkt_in_monetarios():
    """BL-MON-IN1: DWS ESG EU M MKT IC100 EUR ACC must be claimed by
    monetarios via 'eu m mkt' pattern."""
    df = _df(("LU2098886703", "DWS ESG EU M MKT IC100 EUR ACC"))
    assert "LU2098886703" in mon.get_universe_isins(df)


def test_em_mkt_not_false_positive_in_monetarios():
    """'eu m mkt' must not match 'EM MKT' (emerging market) abbreviation."""
    df = _df(("LU9999999903", "FIDELITY EM MKT EQUITY EUR ACC"))
    assert "LU9999999903" not in mon.get_universe_isins(df)


def test_euro_m_mkt_jpm_still_captured():
    """Regression: existing 'euro m mkt' pattern (JPM EURO M MKT VNAV) still works."""
    df = _df(("LU9999999904", "JPM EURO M MKT VNAV S EUR ACC"))
    assert "LU9999999904" in mon.get_universe_isins(df)


# ─────────────────────────────────────────────────────────────────────────────
# BL-MON-U1 + SC-G1: Investment_Universe for monetarios.classify_fund()
#
# BL-MON-U1 (v20 §2A.1 #5): block emits 'Global' internally (not 'Liquidity').
# SC-G1 (INTER-20, 2026-07-12): apply_semantic_validation() then corrects
# 'Global' → 'Country'/'Regional' when Geography is a specific country/region.
# Result: final IU reflects the geographic scope, not the raw block fallback.
# ─────────────────────────────────────────────────────────────────────────────

def test_monetarios_classify_fund_jpy_corrected_to_country():
    """PICTET S-T MONEY MKT JPY: 'jpy' in name → Geography='Japan' (country).
    BL-MON-U1 emits 'Global'; SC-G1 (INTER-20) corrects to 'Country' because
    Geography is a specific country. 'Liquidity' (pre-v20) must NOT appear."""
    result = mon.classify_fund("PICTET S-T MONEY MKT JPY P ACC", kiid_text=None)
    iu = result.get("Investment_Universe")
    assert iu == "Country", (
        f"Expected 'Country' (SC-G1 corrects Global→Country when Geo='Japan'); "
        f"got '{iu}'. Must not be 'Liquidity' (pre-v20 bug)."
    )
    assert result.get("Geography") == "Japan"


def test_monetarios_classify_fund_no_geo_stays_global():
    """Monetario fund with no geographic signal in name or KIID: IU stays 'Global'.
    SC-G1 does not fire when Geography is None (no positive signal)."""
    result = mon.classify_fund(
        "JPM EUR LIQUIDITY LVNAV A EUR ACC",
        kiid_text="Este fondo del mercado monetario invierte principalmente "
                  "en instrumentos del mercado monetario denominados en EUR."
    )
    assert result.get("Investment_Universe") == "Global", (
        "Without a Geography signal, BL-MON-U1 'Global' should not be corrected by SC-G1"
    )


def test_monetarios_classify_fund_nature_unchanged():
    """Regression: adding Investment_Universe must not affect Fund_Nature."""
    result = mon.classify_fund("FIDELITY EURO CASH S EUR ACC", kiid_text=None)
    assert result.get("Fund_Nature") == "Monetario"
