# proyecto1/tests/test_family_key_official_name_fnd0246.py
# -*- coding: utf-8 -*-
"""FND-0246 Phase C: `fund_family_builder.compute_family_groups`, the one definition of the family key (OFF = today's catalogue key, ON = the official
sub-fund name read from the KIID head, with the catalogue key as the fallback). Pure: no database, no pipeline import (R-7)."""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_CORE = os.path.normpath(os.path.join(_HERE, "..", "core"))
_P1 = os.path.normpath(os.path.join(_HERE, ".."))
for _p in (_CORE, _P1):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fund_family_builder import compute_family_groups  # noqa: E402


def _partition(groups):
    """The grouping as a set of frozensets of ISINs (ids and key strings are not part of the contract)."""
    return {frozenset(v) for v in groups.values()}


# (isin, catalogue fund_name, management company, official name) -- catalogue names and official names as measured on the stored texts
ABBREV = [
    ("A1", "BGF DYNAMIC HY D2 USD ACC", "BLACKROCK", "Dynamic High Income Fund"),
    ("A2", "BGF DYNAMIC HI D2 EURH ACC", "BLACKROCK", "Dynamic High Income Fund"),
    ("A3", "BGF DYNAMIC HI D6 USD INC", "BLACKROCK", "Dynamic High Income Fund"),
]
PICTET_MM = [
    ("P1", "PICTET S-T MONEY MKT USD I ACC", "PICTET", "Pictet - Short-Term Money Market USD"),
    ("P2", "PICTET S-T MONEY MKT CHF P ACC", "PICTET", "Pictet - Short-Term Money Market CHF"),
    ("P3", "PICTET S-T MONEY MKT JPY P ACC", "PICTET", "Pictet - Short-Term Money Market JPY"),
]


def test_off_is_exactly_the_catalogue_key():
    g = compute_family_groups(ABBREV, use_official=False)
    assert _partition(g) == {frozenset({"A1"}), frozenset({"A2", "A3"})}               # the abbreviation split FND-0246 exists to fix
    assert all(k[0] == "blackrock" for k in g)


def test_off_ignores_the_official_name_even_when_present():
    with_names = compute_family_groups(ABBREV, use_official=False)
    without = compute_family_groups([(i, n, c, None) for i, n, c, _o in ABBREV], use_official=False)
    assert with_names == without


def test_on_unifies_abbreviation_variants_of_one_fund():
    assert _partition(compute_family_groups(ABBREV, use_official=True)) == {frozenset({"A1", "A2", "A3"})}


def test_on_keeps_currency_sub_funds_apart_where_the_catalogue_key_merges_them():
    assert _partition(compute_family_groups(PICTET_MM, use_official=False)) == {frozenset({"P1", "P2", "P3"})}       # today's key strips the currency
    assert _partition(compute_family_groups(PICTET_MM, use_official=True)) == {frozenset({"P1"}), frozenset({"P2"}), frozenset({"P3"})}


def test_on_different_managers_with_the_same_official_name_stay_apart():
    rows = [("X1", "AAA FUND A", "MANAGER ONE", "Global Bond Fund"), ("X2", "BBB FUND A", "MANAGER TWO", "Global Bond Fund")]
    assert _partition(compute_family_groups(rows, use_official=True)) == {frozenset({"X1"}), frozenset({"X2"})}


def test_on_a_class_without_a_name_joins_when_its_stem_maps_to_one_official_group():
    rows = ABBREV[:2] + [("A4", "BGF DYNAMIC HI X2 EUR ACC", "BLACKROCK", None)]              # stem "bgf dynamic hi" -> only "Dynamic High Income Fund"
    assert _partition(compute_family_groups(rows, use_official=True)) == {frozenset({"A1", "A2", "A4"})}


def test_on_a_class_without_a_name_keeps_its_catalogue_key_when_the_stem_is_ambiguous():
    # one catalogue stem shared by two different official funds: the unnamed class is NOT guessed into either
    rows = [("B1", "DB FIXED INCOME LC", "DB", "DB Fixed Income"), ("B2", "DB FIXED INCOME SC", "DB", "DB Fixed Income Opportunities"),
            ("B3", "DB FIXED INCOME LD", "DB", None)]
    g = compute_family_groups(rows, use_official=True)
    assert _partition(g) == {frozenset({"B1"}), frozenset({"B2"}), frozenset({"B3"})}


def test_on_a_class_without_a_name_and_without_named_siblings_keeps_its_catalogue_key():
    rows = [("C1", "UBS CHINA OPPORTUNITY USD P AC", "UBS", None), ("C2", "UBS CHINA OPPORTUNITY EUR P AC", "UBS", None)]
    assert compute_family_groups(rows, use_official=True) == compute_family_groups(rows, use_official=False)


def test_on_with_no_official_names_at_all_is_identical_to_off():
    rows = [(i, n, c, None) for i, n, c, _o in ABBREV + PICTET_MM]
    assert compute_family_groups(rows, use_official=True) == compute_family_groups(rows, use_official=False)


def test_every_isin_lands_in_exactly_one_family_in_both_modes():
    rows = ABBREV + PICTET_MM + [("A4", "BGF DYNAMIC HI X2 EUR ACC", "BLACKROCK", None), ("C1", "ZZZ", None, None)]
    for use_official in (False, True):
        placed = [i for members in compute_family_groups(rows, use_official).values() for i in members]
        assert sorted(placed) == sorted(r[0] for r in rows)


def test_the_switch_ships_off():
    import shared.config as cfg
    assert cfg.OFFICIAL_NAME_FAMILY_KEY_ENABLED is False
