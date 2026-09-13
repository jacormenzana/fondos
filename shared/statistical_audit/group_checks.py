"""Group-wise structural checks — a Block 5 extension for defects that only
show up across multiple rows of the same entity (AUDITORIA_ESTADISTICA.md
§2.7, added 2026-09-13 for the cost-schedule cross-horizon duplication
defect).

check_invariant (invariants.py) is row-wise only: it can express "this row's
value obeys X" but not "every row for this ISIN carries the SAME value,
which for Total_Costs_Pct/EUR across distinct Horizon_Years IS the defect"
(root cause already characterised in proyecto1/core/priips_cost_extractor.py
around line 900 — the PLAIN_TEXT route's parse_costs_over_time returns
identical total_cost_eur/aci_pct for every horizon column; the DLA2 route
does not). This module adds the minimal generic primitive that gap needs:
per group, flag when every non-null value is identical AND there is more
than one row to compare. Not specific to cost or to ISIN — any future
"should vary across N rows of the same entity" check reuses this (P#11),
the way check_invariant/check_bounds are reused across both domains.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class GroupConstancyRule:
    rule_id: str
    group_column: str
    value_column: str
    min_group_size: int = 2
    description: str = ""


@dataclass
class GroupConstancyResult:
    rule: GroupConstancyRule
    n_groups_checked: int
    n_violating_groups: int
    violations: pd.DataFrame  # columns: group_column, value_column (the constant value), n_rows


def check_group_constancy(df: pd.DataFrame, rule: GroupConstancyRule) -> GroupConstancyResult:
    """Flags every group of `rule.group_column` where `rule.value_column` is
    non-null for at least `rule.min_group_size` rows AND every one of those
    non-null values is identical — the cross-horizon duplication signature.

    A group with fewer than `min_group_size` non-null rows cannot exhibit
    "identical across rows" and is excluded from n_groups_checked —
    undecidable, not "passing" (same NULL-exclusion discipline check_invariant
    applies via P#1/R-4: a missing operand is not evidence either way).
    """
    if df.empty or rule.value_column not in df.columns or rule.group_column not in df.columns:
        return GroupConstancyResult(rule, 0, 0, df.iloc[0:0])

    valid = df[[rule.group_column, rule.value_column]].dropna(subset=[rule.value_column])
    if valid.empty:
        return GroupConstancyResult(rule, 0, 0, df.iloc[0:0])

    counts = valid.groupby(rule.group_column, dropna=False)[rule.value_column].agg(
        n_rows="count", n_unique="nunique", value="first",
    )
    checked = counts[counts["n_rows"] >= rule.min_group_size]
    violating = checked[checked["n_unique"] == 1]

    violations = (
        violating.reset_index()
        .rename(columns={"value": rule.value_column})[[rule.group_column, rule.value_column, "n_rows"]]
    )

    return GroupConstancyResult(
        rule=rule,
        n_groups_checked=int(len(checked)),
        n_violating_groups=int(len(violating)),
        violations=violations,
    )
