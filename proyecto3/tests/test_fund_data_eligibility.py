# proyecto3/tests/test_fund_data_eligibility.py
# -*- coding: utf-8 -*-
"""FND-0251 Commit B: build-time data eligibility (stale monthly NAV / active accepted residual) in the live scorer. Pure parts (R-7: no DB).
The switch ships OFF: with it OFF nothing changes, with it ON the two rules exclude, and absent inputs (point-in-time frames) never do."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import shared.config as cfg  # noqa: E402
from proyecto3.src import fund_scorer as fs  # noqa: E402

_spec = importlib.util.spec_from_file_location("eligibility_switch_diff", _ROOT / "scripts" / "audit" / "eligibility_switch_diff.py")
sw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sw)


def _row(**kw):
    base = {"max_dd": -0.05, "return_ann_real": 0.03, "Fund_Nature": "Mixtos", "Credit_Quality": "Investment Grade"}
    base.update(kw)
    return pd.Series(base)


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(cfg, "FUND_DATA_ELIGIBILITY_ENABLED", True)
    monkeypatch.setattr(cfg, "P3_FUND_NAV_MAX_AGE_DAYS", 60)


def test_the_switch_ships_off():
    import ast
    src = (_ROOT / "shared" / "config.py").read_text(encoding="utf-8")
    node = next(n for n in ast.parse(src).body if isinstance(n, ast.AnnAssign) and getattr(n.target, "id", "") == "FUND_DATA_ELIGIBILITY_ENABLED")
    assert node.value.value is False, "Commit B must ship OFF: enabling it is a separate, reviewed commit"


def test_off_changes_nothing_even_for_a_fund_that_would_be_excluded(monkeypatch):
    monkeypatch.setattr(cfg, "FUND_DATA_ELIGIBILITY_ENABLED", False)
    row = _row(nav_age_days=900.0, nav_last_date="2024-04-30", residual_reason="WINDOW_FISHER_IDENTITY (FND-0250, hasta 2027-01-09)")
    assert fs.check_hard_filters(row, "Equilibrada") is None


def test_a_stale_nav_excludes_with_the_date_and_the_limit(on):
    out = fs.check_hard_filters(_row(nav_age_days=61.0, nav_last_date="2026-08-10"), "Equilibrada")
    assert out == "nav_stale: last NAV 2026-08-10 (61 d > 60 d)"


def test_the_nav_age_limit_is_inclusive_at_the_edge(on):
    assert fs.check_hard_filters(_row(nav_age_days=60.0), "Equilibrada") is None
    assert fs.check_hard_filters(_row(nav_age_days=60.0001), "Equilibrada") is not None


def test_an_active_accepted_residual_excludes_and_names_rule_ticket_and_expiry(on):
    out = fs.check_hard_filters(_row(residual_reason="REAL_EQUALS_NOMINAL_sharpe (FND-0252, hasta 2027-01-09)"), "Dinamica")
    assert out == "accepted_residual: REAL_EQUALS_NOMINAL_sharpe (FND-0252, hasta 2027-01-09)"


@pytest.mark.parametrize("extra", [
    {},                                                                      # point-in-time frames carry neither input
    {"nav_age_days": np.nan, "nav_last_date": None, "residual_reason": None},  # a fund with no monthly NAV / no residual
    {"nav_age_days": 12.0, "residual_reason": ""},
])
def test_absent_or_empty_inputs_never_exclude_fail_open(on, extra):
    assert fs.check_hard_filters(_row(**extra), "Equilibrada") is None


def test_data_eligibility_comes_before_the_drawdown_filter(on):
    out = fs.check_hard_filters(_row(max_dd=-0.9, nav_age_days=400.0, nav_last_date="2025-09-01"), "Defensiva")
    assert out.startswith("nav_stale")                          # the drawdown filter would also have fired, but the data is the reason


def test_other_filters_still_apply_when_the_data_is_fine(on):
    out = fs.check_hard_filters(_row(max_dd=-0.9, nav_age_days=10.0), "Defensiva")
    assert out is not None and out.startswith("max_drawdown")


# ---- the comparison script ----

def _res(rows):
    return pd.DataFrame(rows, columns=["isin", "subportfolio", "eligible", "exclusion_reason", "fund_name"])


def test_diff_lists_only_the_pairs_whose_eligibility_changes():
    off = _res([("A", "Dinamica", True, None, "Fund A"), ("B", "Dinamica", True, None, "Fund B"), ("C", "Defensiva", False, "max_drawdown", "Fund C")])
    on = _res([("A", "Dinamica", False, "nav_stale: x", "Fund A"), ("B", "Dinamica", True, None, "Fund B"), ("C", "Defensiva", False, "nav_stale: y", "Fund C")])
    d = sw.diff_eligibility(off, on)
    assert d[["isin", "subportfolio", "change", "eligible_off", "eligible_on"]].values.tolist() == [["A", "Dinamica", "excluded", True, False]]
    assert d.iloc[0]["exclusion_reason_on"] == "nav_stale: x"


def test_a_sibling_taking_over_the_family_is_labelled_not_reported_as_an_exclusion():
    """fund_scorer.deduplicate_by_family keeps one class per (family, block): excluding the representative moves it to a sibling."""
    off = _res([("REP", "Dinamica", True, None, "Rep class")])
    on = _res([("SIB", "Dinamica", True, None, "Sibling class")])
    d = sw.diff_eligibility(off, on).set_index("isin")
    assert d.loc["REP", "change"] == "family_out" and d.loc["SIB", "change"] == "family_in"
    assert d.loc["REP", "fund_name"] == "Rep class" and d.loc["SIB", "fund_name"] == "Sibling class"


def test_an_empty_difference_is_an_empty_frame():
    r = _res([("A", "Dinamica", True, None, "Fund A")])
    assert sw.diff_eligibility(r, r).empty
    assert sw.diff_eligibility(_res([]), _res([])).empty


def test_score_funds_from_df_end_to_end_excludes_only_the_flagged_fund(monkeypatch):
    """The real scoring core with a tiny frame: with the switch ON the stale fund is ineligible in its block, the others are untouched."""
    idx = pd.Index(["OK1", "OK2", "OLD"], name="isin")
    df = pd.DataFrame({
        "Fund_Name": ["a", "b", "c"], "Fund_Nature": ["Mixtos"] * 3, "fund_family_id": ["f1", "f2", "f3"],
        "return_ann_real": [0.04, 0.05, 0.06], "sharpe": [1.0, 1.1, 1.2], "max_dd": [-0.05, -0.06, -0.04],
        "nav_age_days": [10.0, 20.0, 300.0], "nav_last_date": ["2026-09-30", "2026-09-20", "2025-12-31"],
        "residual_reason": [None, None, None],
    }, index=idx)
    monkeypatch.setattr(cfg, "FUND_DATA_ELIGIBILITY_ENABLED", False)
    off = fs.score_funds_from_df(df, "Expansion", verbose=False)
    monkeypatch.setattr(cfg, "FUND_DATA_ELIGIBILITY_ENABLED", True)
    on = fs.score_funds_from_df(df, "Expansion", verbose=False)
    changed = sw.diff_eligibility(off, on)
    assert set(changed["isin"]) == {"OLD"} and changed["exclusion_reason_on"].str.startswith("nav_stale").all()
