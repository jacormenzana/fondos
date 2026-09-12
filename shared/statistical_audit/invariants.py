"""Functions #9-#10 (AUDITORIA_ESTADISTICA.md §4): check_invariant, check_bounds.

check_invariant only flags a row when every operand it references is
present. A row missing an operand is not "violating" the invariant — it is
undecidable, and P#1/R-4 ("NULL is designed, not lost") applies here exactly
as it does to the underlying calculation: excluding it from n_applicable
(rather than defaulting a missing comparison to "violated") is required for
correctness, not an optional refinement.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

import pandas as pd

BoundType = Literal["HARD_INVARIANT", "PLAUSIBILITY"]

_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_DSL_KEYWORDS = frozenset({"and", "or", "not", "abs"})


def expression_identifiers(expression: str) -> set[str]:
    """Every identifier-like token in a pandas-eval expression, minus the
    small set of keywords/functions the catalogs use (and/or/not/abs) —
    those are operators, never column names. Public: the audit runner uses
    this (unfiltered by any particular frame's columns) to decide whether a
    rule is even applicable to a given population before running it.
    """
    return set(_IDENTIFIER_RE.findall(expression)) - _DSL_KEYWORDS


def referenced_columns(expression: str, columns) -> list[str]:
    """Identifier tokens in `expression` that name an actual column in
    `columns`. Public: the audit runner also uses this to route each
    catalog invariant to whichever population frame (e.g. fund_master vs
    fund_cost_schedule) actually carries the columns it references.
    """
    tokens = expression_identifiers(expression)
    return [c for c in columns if c in tokens]


@dataclass
class InvariantRule:
    rule_id: str
    expression: str
    when: str | None = None
    bound_type: BoundType = "HARD_INVARIANT"
    description: str = ""


@dataclass
class InvariantResult:
    rule: InvariantRule
    n_applicable: int
    n_violations: int
    violations: pd.DataFrame


def check_invariant(df: pd.DataFrame, rule: InvariantRule) -> InvariantResult:
    if df.empty:
        return InvariantResult(rule, 0, 0, df.copy())

    applicable = df.eval(rule.when) if rule.when else pd.Series(True, index=df.index)
    if rule.when:
        when_cols = referenced_columns(rule.when, df.columns)
        if when_cols:
            applicable &= df[when_cols].notna().all(axis=1)

    expr_cols = referenced_columns(rule.expression, df.columns)
    if expr_cols:
        applicable &= df[expr_cols].notna().all(axis=1)

    satisfied = df.eval(rule.expression)
    violation = applicable & ~satisfied.fillna(False)

    return InvariantResult(
        rule=rule,
        n_applicable=int(applicable.sum()),
        n_violations=int(violation.sum()),
        violations=df.loc[violation],
    )


@dataclass
class BoundRule:
    metric: str
    min_value: float | None
    max_value: float | None
    bound_type: BoundType
    crisis_carveout: bool = True


@dataclass
class BoundResult:
    rule: BoundRule
    n_breaches: int
    breaches: pd.DataFrame
    n_carved_out: int
    carved_out: pd.DataFrame


def check_bounds(
    df: pd.DataFrame,
    value_column: str,
    rule: BoundRule,
    horizon_column: str | None = None,
) -> BoundResult:
    value = df[value_column]
    breach = pd.Series(False, index=df.index)
    if rule.min_value is not None:
        breach |= value < rule.min_value
    if rule.max_value is not None:
        breach |= value > rule.max_value
    breach &= value.notna()

    carved_out_mask = pd.Series(False, index=df.index)
    if rule.crisis_carveout and horizon_column is not None and horizon_column in df.columns:
        carved_out_mask = breach & df[horizon_column].astype(str).str.startswith("crisis_")
        breach &= ~carved_out_mask

    return BoundResult(
        rule=rule,
        n_breaches=int(breach.sum()),
        breaches=df.loc[breach],
        n_carved_out=int(carved_out_mask.sum()),
        carved_out=df.loc[carved_out_mask],
    )
