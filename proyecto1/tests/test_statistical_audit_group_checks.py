# proyecto1/tests/test_statistical_audit_group_checks.py
# -*- coding: utf-8 -*-
"""Tests unitarios de shared/statistical_audit/group_checks.py y
catalog_group_checks.py (doc/reglas/AUDITORIA_ESTADISTICA.md §2.7): la
duplicacion Total_Costs_Pct/EUR identica entre Horizon_Years de un mismo
ISIN, que check_invariant (row-wise) no puede expresar.

Cumple R-7: sin importar pipeline.py ni core.io.
"""

import os
import sys

import pandas as pd
import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.normpath(os.path.join(_TESTS_DIR, '..', '..'))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from shared.statistical_audit.catalog_group_checks import COST_GROUP_CHECKS
from shared.statistical_audit.group_checks import GroupConstancyRule, check_group_constancy


def _rule(**overrides):
    defaults = dict(rule_id="R1", group_column="ISIN", value_column="Total_Costs_Pct")
    defaults.update(overrides)
    return GroupConstancyRule(**defaults)


class TestCheckGroupConstancy:
    def test_flags_isin_with_identical_value_across_multiple_horizons(self):
        df = pd.DataFrame({
            "ISIN": ["A", "A", "A", "B", "B"],
            "Total_Costs_Pct": [1.5, 1.5, 1.5, 2.0, 3.0],
        })
        result = check_group_constancy(df, _rule())
        assert result.n_groups_checked == 2
        assert result.n_violating_groups == 1
        assert list(result.violations["ISIN"]) == ["A"]
        assert result.violations.iloc[0]["Total_Costs_Pct"] == 1.5
        assert result.violations.iloc[0]["n_rows"] == 3

    def test_single_row_isin_is_undecidable_not_flagged(self):
        # One horizon row can't demonstrate "identical across rows" — P#1/R-4:
        # excluded from n_groups_checked, not silently counted as passing.
        df = pd.DataFrame({"ISIN": ["A"], "Total_Costs_Pct": [1.5]})
        result = check_group_constancy(df, _rule())
        assert result.n_groups_checked == 0
        assert result.n_violating_groups == 0

    def test_genuinely_varying_horizons_never_flagged(self):
        df = pd.DataFrame({
            "ISIN": ["A", "A", "A"],
            "Total_Costs_Pct": [1.0, 2.0, 3.0],
        })
        result = check_group_constancy(df, _rule())
        assert result.n_groups_checked == 1
        assert result.n_violating_groups == 0

    def test_null_row_excluded_from_the_row_count_not_treated_as_a_value(self):
        # A NULL row doesn't count toward min_group_size and isn't itself
        # a "duplicate" — same NULL-exclusion discipline as check_invariant.
        # 2 non-null 1.5 rows remain for A, which IS a violation (n_rows=2,
        # not 3 — the NULL row must not inflate the count).
        df = pd.DataFrame({
            "ISIN": ["A", "A", "A"],
            "Total_Costs_Pct": [1.5, None, 1.5],
        })
        result = check_group_constancy(df, _rule())
        assert result.n_groups_checked == 1
        assert result.n_violating_groups == 1
        assert result.violations.iloc[0]["n_rows"] == 2

    def test_missing_columns_returns_empty_result_not_an_error(self):
        df = pd.DataFrame({"ISIN": ["A"], "Other_Col": [1.0]})
        result = check_group_constancy(df, _rule())
        assert result.n_groups_checked == 0
        assert result.n_violating_groups == 0

    def test_empty_frame_returns_empty_result(self):
        df = pd.DataFrame(columns=["ISIN", "Total_Costs_Pct"])
        result = check_group_constancy(df, _rule())
        assert result.n_groups_checked == 0
        assert result.n_violating_groups == 0

    def test_min_group_size_respected(self):
        df = pd.DataFrame({
            "ISIN": ["A", "A", "B", "B", "B"],
            "Total_Costs_Pct": [1.5, 1.5, 2.0, 2.0, 2.0],
        })
        result = check_group_constancy(df, _rule(min_group_size=3))
        assert result.n_groups_checked == 1  # only B has >=3 rows
        assert list(result.violations["ISIN"]) == ["B"]


class TestCostGroupChecksCatalog:
    def test_covers_both_total_costs_columns(self):
        value_columns = {rule.value_column for rule in COST_GROUP_CHECKS}
        assert value_columns == {"Total_Costs_Pct", "Total_Costs_EUR"}

    def test_every_rule_groups_by_isin_with_min_group_size_2(self):
        for rule in COST_GROUP_CHECKS:
            assert rule.group_column == "ISIN"
            assert rule.min_group_size == 2
