"""FND-0440: `Geography = Global` was a DEFAULT. A single-region EQUITY index named in the fund name or in the declared benchmark is evidence, read before any default applies.

Owner's scope (2026-11-25, narrow): equity / mixed funds only, not sector funds, never a blend or a world index, never a fund whose name says global; KIID text and a region named in the name keep
priority; a euro-currency bond index is a currency, not a region. Every input below is a real name or benchmark string seen in the census, or its minimal variant."""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from proyecto1.core import classify_utils as cu


def geo(name="FUND X A EUR ACC", bmk=None, nature="Renta Variable", theme="Core/General"):
    return cu.detect_geography_from_equity_index(name, bmk, nature, theme)


@pytest.mark.parametrize("name, bmk, want_es, want_en", [
    ("FIDELITY S&P 500 IND P EUR ACC", "dow jones indices has with fidelity and any related funds", "EEUU", "North America"),     # the index is in the NAME; the benchmark is a licensing text
    ("CULLEN N AME DIV EQ A2 USD ACC", "russell 1000valueindex", "EEUU", "North America"),
    ("DUNAS SELECCION U R EURHDG ACC", "msci usa esg", "EEUU", "North America"),
    ("BNY MELLON SMALLCAP EUR A ACC", 's&p eurozone smallcap tr index (the "benchmark")', "Europa", "Europe"),
    ("SOME FUND A EUR ACC", "stoxx europe 600 index", "Europa", "Europe"),
    ("ISHARES JAP INDX IE I EUR ACC", "msci japan index (con dividendos netos)", "Japón", "Japan"),
    ("VANG PAC EXJAP STK INX EUR ACC", "msci pacific ex japan index", "Asia", "Asia-Pacific"),
    ("SOME FUND A USD ACC", "msci china index", "China", "China"),
])
def test_one_region_equity_index_is_evidence(name, bmk, want_es, want_en):
    got = geo(name, bmk)
    assert got == want_es
    assert cu._derive_geography_en(got, name.lower()) == want_en          # the ES vocabulary flows through the same translation as every other signal


@pytest.mark.parametrize("bmk", [
    "s&p 500 + 40% bloomberg us aggregate bond)",       # blend
    "s&p 500 (36 %)",                                    # one component of a blend
    "msci world net total return index",                 # world
    "msci world ex europe",                              # world, whatever it excludes
    "msci ac world index",
    "bloomberg global aggregate ex us mbs 1-5 year",
    "msci acwi",
    "s&p 500 and stoxx europe 600",                      # two regions
    "msci japan index and msci china index",
    "dow jones indices has with fidelity and any related funds",          # licensing text, not an index
    "msci usd hedged liquidity index",                   # 'msci usd' is not 'msci us'
    "ice bofa 1-3 year euro corporate index",            # a currency, not a region
    "iboxx euro corp overall tr index",
    None,
    "",
])
def test_blends_world_indices_and_non_indices_give_nothing(bmk):
    assert geo("FUND X A EUR ACC", bmk) is None


def test_a_fund_whose_name_says_global_keeps_global_whatever_its_comparator():
    assert geo("AMBIENTA GLB ENV EQ DR EUR ACC", "stoxx europe 600 index") is None
    assert geo("XY GLOBAL EQUITY A", "s&p 500 index") is None
    assert geo("XY WORLD LEADERS A", "msci usa") is None


@pytest.mark.parametrize("name", ["AZVALOR INTERNAT R EUR ACC", "XY INTERNATIONAL EQUITY A", "XY INTERNACIONAL FI", "XY INTL VALUE A"])
def test_a_name_that_says_international_is_a_global_mandate(name):
    assert geo(name, "msci europe total return net index") is None


@pytest.mark.parametrize("bmk", ["msci europe total return net index, no representa", "msci europe index, no representa necesariamente el universo", "msci usa index (not representative of the portfolio)"])
def test_a_benchmark_declared_unrepresentative_is_not_evidence(bmk):
    assert geo("FUND X A EUR ACC", bmk) is None


def test_name_and_benchmark_that_disagree_give_nothing():
    assert geo("XY S&P 500 TRACKER", "msci japan index") is None
    assert geo("XY S&P 500 TRACKER", "msci world index") is None          # the benchmark says world: the name alone does not win
    assert geo("XY S&P 500 TRACKER", "s&p 500 net total return") == "EEUU"     # agreement


@pytest.mark.parametrize("nature, theme, expected", [
    ("Renta Variable", "Core/General", "EEUU"),
    ("Mixtos", "Core/General", "EEUU"),
    ("Renta Variable", None, "EEUU"),
    ("Renta Fija Flexible", "Core/General", None),       # bonds: not in the scope
    ("Monetario", "Core/General", None),
    ("Alternativo", "Core/General", None),
    (None, "Core/General", None),
    ("Renta Variable", "Biotechnology", None),           # sector funds: the index is a comparator, the mandate is often global
    ("Renta Variable", "Technology", None),
])
def test_scope_is_equity_and_mixed_non_sector(nature, theme, expected):
    assert geo("FUND X", "msci usa index", nature, theme) == expected


# ------------------------------------------------------------------------------------------------------------------ the wiring in pipeline.py (R-7: read as source, not imported)
PIPELINE = (Path(__file__).resolve().parent.parent / "core" / "pipeline.py").read_text(encoding="utf-8")


def test_pipeline_consults_the_index_only_when_kiid_and_name_are_silent_and_before_the_record_is_built():
    m = re.search(r"if not _geo_es_final:\s*\n(?:.*\n){0,12}?.*detect_geography_from_equity_index\(", PIPELINE)
    assert m, "the index signal must be a FALLBACK behind KIID text and name (`if not _geo_es_final`)"
    assert m.start() < PIPELINE.index('"Geography": _geo_en_final'), "it must run before the record takes the value (and long before any default fills Geography)"
    assert m.start() < PIPELINE.index("_geo_en_final = _derive_geography_en(_geo_es_final"), "the value must go through the same ES->EN translation"


def test_pipeline_reads_the_effective_benchmark_and_theme_for_cached_funds():
    block = PIPELINE[PIPELINE.index("detect_geography_from_equity_index(") - 900: PIPELINE.index("detect_geography_from_equity_index(") + 300]
    assert 'eff.get("Benchmark_Declared"' in block, "a CACHED fund has its benchmark only in the database"
    assert "_detect_theme_pipeline(" in block, "the sector test must use the theme of this pass even when the block assigned none"


# ---- the index-driven country must not leave a stale warning: the Geography/Universe check runs BEFORE SC-G1 corrects a default 'Global' universe ----
def test_a_country_with_a_default_global_universe_is_what_the_validator_warns_about():
    status, _msg, _corrected = cu.validate_geography_universe("Japan", "Global")
    assert status == "WARNING"                                        # without the branch below the fund would carry this warning although SC-G1 later stores 'Country'


def test_pipeline_marks_an_index_driven_geography_and_corrects_its_universe_like_the_name_driven_case():
    start = PIPELINE.index("detect_geography_from_equity_index(", PIPELINE.index("_geo_es_final = _geo_name if"))
    before = PIPELINE[PIPELINE.index("_geo_es_final = _geo_name if"):start]
    assert re.search(r"_geo_from_index = False\s*\n(?:\s*#.*\n)*\s*if not _geo_es_final:", before), "the flag starts False, outside the fallback"
    assert re.search(r"_geo_from_index = bool\(_geo_es_final\)", PIPELINE[start:start + 700]), "the flag is True only when the index signal really gave a value"
    branch = re.search(r'elif _geouniv_status == "WARNING" and \(_geo_name_wins or _geo_from_index\):', PIPELINE)
    assert branch, "an index-driven country takes the auto-correct branch"
    assert branch.start() < PIPELINE.index('"GEOGRAPHY_UNIVERSE_WARNING"'), "and it comes before the plain warning branch"
