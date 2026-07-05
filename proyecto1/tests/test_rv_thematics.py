# proyecto1/tests/test_rv_thematics.py
# -*- coding: utf-8 -*-
"""
Tests for BL-RV-IN4 (2026-07-05): "thematics" name signal.

Covers:
  - renta_variable.get_universe_isins() includes Thematics fund names
    (is_candidate logic via a minimal df_master)
  - detect_nature_from_name() returns "Renta Variable" for all three
    real fund name abbreviations
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

import blocks.renta_variable as rv
from classify_utils import detect_nature_from_name


# ---------------------------------------------------------------------------
# is_candidate via get_universe_isins (no DB needed)
# ---------------------------------------------------------------------------

def _make_df(*rows):
    data = [{"ISIN": isin, "Fund_Name": name, "Management_Company": "Test"}
            for isin, name in rows]
    return pd.DataFrame(data)


@pytest.mark.parametrize("isin,name", [
    ("LU1951199022", "THEMATICS AI RBTICS HRE EUH AC"),
    ("LU1951200648", "THEMATICS AI RBTICS RE EU ACC"),
    ("LU2095320268", "THEMATICS SBSCRP ECO RE EU ACC"),
])
def test_thematics_funds_in_rv_universe(isin, name):
    """BL-RV-IN4: all three Thematics sub-funds are claimed by renta_variable."""
    df = _make_df((isin, name))
    result = rv.get_universe_isins(df)
    assert isin in result, (
        f"Expected {name!r} to be claimed by renta_variable universe "
        f"via 'thematics' include pattern (BL-RV-IN4)"
    )


def test_thematics_safety_still_in_rv_universe():
    """Regression: the original 'thematics safety' fund is still captured by bare 'thematics'."""
    df = _make_df(("LU9999999901", "THEMATICS SAFETY RE EUR ACC"))
    result = rv.get_universe_isins(df)
    assert "LU9999999901" in result


def test_non_thematics_fund_not_false_positive():
    """Bare 'thematics' must not pull in bond/cash funds named differently."""
    # A bond fund whose name happens to contain no equity token
    df = _make_df(("LU9999999902", "NATIXIS FIXED INCOME BOND FUND"))
    result = rv.get_universe_isins(df)
    assert "LU9999999902" not in result


# ---------------------------------------------------------------------------
# detect_nature_from_name
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fund_name", [
    "thematics ai rbtics hre euh ac",
    "thematics ai rbtics re eu acc",
    "thematics sbscrp eco re eu acc",
    "thematics safety re eur acc",    # pre-existing; still works with bare "thematics"
])
def test_detect_nature_from_name_thematics(fund_name):
    """detect_nature_from_name returns 'Renta Variable' for any Thematics fund name."""
    result = detect_nature_from_name(fund_name)
    assert result == "Renta Variable", (
        f"Expected 'Renta Variable' for {fund_name!r}, got {result!r}"
    )
