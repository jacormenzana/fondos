# proyecto1/tests/test_fund_family_normalization_fnd0244.py
# -*- coding: utf-8 -*-
"""FND-0244: the family key (fund_family_builder._normalize_name). R-7: pure, no DB.

Measured on the 3,762 catalogue names (cut at ~30 characters): 725 funds (19%) ended in "AC" (a truncated "Acc") and, because it was not a
suffix, blocked every suffix before it, each forming a single-fund family; the hedge marker is abbreviated ("HDG", "EURH", "GBPH") and the
Capital Group class codes end in H ("BH", "ZH", "BGDH"). With the extended vocabulary: families 3,228 -> 2,881 (347 merged), singletons
2,849 -> 2,318, 0 existing families split, and the acronym false merge ("NEUBERGER S D E M D A" -> "neuberger") is prevented.
"""
import os
import sys

import pytest

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from proyecto1.core.fund_family_builder import (  # noqa: E402
    _KNOWN_HETEROGENEOUS_STEMS, _is_known_heterogeneous, _normalize_name,
)


# ---------------------------------------------------------------- the funds of FND-0237 / FND-0244
@pytest.mark.parametrize("names, stem", [
    (("CAPITAL NEW PERSPECTIVE ZH ACC", "CAPITAL NEW PERSPECTIVE BH ACC", "CAPITAL NEW PERSPECTIVE Z ACC",
      "CAPITAL NEW PERSPECTIVE B ACC"), "capital new perspective"),
    (("CAPITAL G.NW PERSP PH EURH ACC", "CAPITAL G.NW PERSP P EUR AC", "CAPITAL G. NEW PERSP PDH EUR".replace("NEW", "NW")), "capital g nw persp"),
    (("CGF NW PERSP BDH EURH INC",), "cgf nw persp"),
    (("CAPITAL NEW PERSPECT B GBPH AC", "CAPITAL NEW PERSPECT B ACC USD"), "capital new perspect"),
    (("CGF INVES COMP OF AMERICA B AC",), "cgf inves comp of america"),
    (("ARCUS JAPAN A EURHDG ACC", "ARCUS JAPAN A EUR ACC"), "arcus japan"),
])
def test_share_classes_of_one_fund_share_the_stem(names, stem):
    assert {_normalize_name(n) for n in names} == {stem}


def test_a_truncated_acc_no_longer_blocks_the_suffixes_before_it():
    assert _normalize_name("ABC GLOBAL EQUITY EUR AC") == _normalize_name("ABC GLOBAL EQUITY USD ACC") == "abc global equity"
    assert _normalize_name("ABC GLOBAL BOND EUR IN") == _normalize_name("ABC GLOBAL BOND EUR INC") == "abc global bond"


@pytest.mark.parametrize("name", ["FOO BAR EUR HDG", "FOO BAR EURHDG", "FOO BAR USD HEDGED", "FOO BAR GBPH", "FOO BAR CHF H", "FOO BAR USDHDG"])
def test_every_hedge_spelling_is_stripped_for_every_currency(name):
    assert _normalize_name(name) == "foo bar"


def test_manager_class_codes_with_measured_support_are_suffixes():
    for code in ("lc", "ld", "fc", "fd", "nc", "sc", "tfc", "tfd"):
        assert _normalize_name(f"DWS SOME FUND {code.upper()}") == "dws some fund", code


# ---------------------------------------------------------------- what must NOT change
@pytest.mark.parametrize("name, stem", [
    ("TEMPLETON ASIAN GROWTH F N ACC", "templeton asian growth"),            # two spaced letters are a class code (Franklin)
    ("TEMPLETON ASIAN GROWTH A ACC", "templeton asian growth"),
    ("JPM EUROPE DYNAMIC T A EUR INC", "jpm europe dynamic"),
    ("SISF CHINA A C USD ACC", "sisf china"),
    ("SISF CHINA A A1 USD ACC", "sisf china"),
    ("BGF WORLD TECHNOLOGY F.E2 EUR", "bgf world technology"),
    ("JANUS H HOR GL SM F A2 EUR ACC", "janus h hor gl sm"),
    ("BLACKROCK ESG F I S D4 EUR INC", "blackrock esg f i s"),               # D4 is the class, "F I S" the acronym
    ("BLACKROCK ESG F I S S4 EUR INC", "blackrock esg f i s"),
])
def test_two_spaced_letters_and_letter_digit_codes_are_still_class_codes(name, stem):
    assert _normalize_name(name) == stem


def test_the_acronym_guard_keeps_two_different_neuberger_funds_apart():
    sdemda = {_normalize_name(n) for n in ("NEUBERGER S D E M D A EURHDG I", "NEUBERGER S D E M D A EURHDG A", "NEUBERGER S D E M D A USD ACC")}
    ngcfi = _normalize_name("NEUBERGER N G C F I EURHDG ACC")
    assert sdemda == {"neuberger s d e m d a"} and ngcfi == "neuberger n g c f i" and ngcfi not in sdemda


def test_real_words_at_the_end_of_a_name_are_not_stripped():
    for name, stem in (("PICTET ASIA FUND", "pictet asia fund"), ("AXA WORLD BOND", "axa world bond"), ("MFS PRUDENT WEALTH", "mfs prudent wealth"),
                       ("ROBECO CHINA EQUITIES", "robeco china equities"), ("CANDRIAM SUSTAINABLE EURO BONDS", "candriam sustainable euro bonds")):
        assert _normalize_name(name) == stem


# ---------------------------------------------------------------- the suppression list is keyed by stable stems
def test_known_heterogeneous_families_are_identified_by_stem_not_by_the_reassigned_id():
    assert _KNOWN_HETEROGENEOUS_STEMS == {"allianz best styles at"}
    assert _is_known_heterogeneous(["ALLIANZ BEST STYLES AT USD ACC", "ALLIANZ BEST STYLES AT EUR ACC"])
    assert not _is_known_heterogeneous(["MUZINICH SHOR DUR HY H EURH IN", "MUZINICH SHOR DUR HY R USDH AC"])
    assert not _is_known_heterogeneous([]) and not _is_known_heterogeneous([""])
