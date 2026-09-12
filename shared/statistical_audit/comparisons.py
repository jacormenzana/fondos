"""Function #8 (AUDITORIA_ESTADISTICA.md §4): compare_pairs — Block 2,
the mis-binding signature. Never a bare abs(a-b)<eps: eligibility AND
comparison(tolerance) AND min_matches, always (§3 "Igualdad cruzada").
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pandas as pd


@dataclass
class PairRule:
    rule_id: str
    tolerance: float
    min_matches: int = 8
    eligibility: Callable[[pd.DataFrame], pd.Series] | None = None
    severity: str = "WARN"
    diagnosis: str = ""


@dataclass
class PairComparisonResult:
    rule: PairRule
    n_eligible: int
    n_matches: int
    triggered: bool
    matched_rows: pd.DataFrame


def compare_pairs(
    df: pd.DataFrame,
    left_column: str,
    right_column: str,
    rule: PairRule,
) -> PairComparisonResult:
    left, right = df[left_column], df[right_column]
    eligible = left.notna() & right.notna()

    if rule.eligibility is not None:
        eligible = eligible & rule.eligibility(df).reindex(df.index, fill_value=False)

    match = eligible & ((left - right).abs() < rule.tolerance)
    n_matches = int(match.sum())

    return PairComparisonResult(
        rule=rule,
        n_eligible=int(eligible.sum()),
        n_matches=n_matches,
        triggered=n_matches >= rule.min_matches,
        matched_rows=df.loc[match],
    )
