# shared/statistical_audit/canaries.py
# -*- coding: utf-8 -*-
"""canaries.py -- detector-liveness canaries (FND-0393, plan D1: PLAN_P2_AUDIT_ESCALATION.md).

Why: a hard detector that stopped evaluating looks exactly like a clean population (it writes nothing). Each FULL audit run therefore feeds every hard detector a KNOWN-BAD fixture, inside the same
process and through the same code the run uses, and expects the detector to fire. A canary that does not fire marks the run INCOMPLETE (completeness check CANARY), so a silently broken detector
blocks promotion by itself instead of reporting "no violation".

This module is PURE (no database, no runner import): it BUILDS the fixtures, generically, from the rule catalogs themselves (no rule is named here, P#5 / P#11), and reads the result of a canary
run. The runner (scripts/audit/run_statistical_audit.py::run_canaries) pushes the fixtures through its own `_run_invariants` / `_run_group_checks` / duplicate-series helper. The fixtures never
reach audit_finding: they are evaluated on a scratch run that is discarded.

A row-invariant fixture is FOUND, not written by hand: the columns the rule's expression and `when` name are filled from a small grid of values (numbers, the string literals the rule itself
quotes, booleans) until one combination violates the rule through `check_invariant`. A rule no grid value can violate is reported as `unbuildable` (information: its liveness is not covered), never
as a failure.
"""
from __future__ import annotations

import itertools
import re
from typing import Iterable, Optional

import pandas as pd

from shared.statistical_audit.invariants import InvariantRule, check_invariant, expression_identifiers

CANARY_ISIN = "CANARY000000"
NUMBERS = (-1e6, -1.0, -0.5, 0.0, 0.5, 1.0, 2.0, 3.0, 5.0, 7.0, 8.0, 25.0, 100.0, 1e6)
MAX_GRID_ROWS = 120_000
_QUOTED = re.compile(r"""['"]([^'"]+)['"]""")


def _string_literals(rule: InvariantRule) -> tuple:
    return tuple(dict.fromkeys(_QUOTED.findall(rule.expression) + _QUOTED.findall(rule.when or "")))


def _grid(columns: list, kinds: tuple, strings: tuple) -> pd.DataFrame:
    """Every combination of one candidate per column (numbers for an 'n' column, True / False for a 'b' column, the rule's own literals plus an unrelated string for an 's' column), capped at MAX_GRID_ROWS.
    One kind per column keeps each column a single dtype, which `DataFrame.eval` needs."""
    pools = [list(NUMBERS) if k == "n" else ([True, False] if k == "b" else list(strings) + ["__other__"]) for k in kinds]
    size = 1
    for p in pools:
        size *= len(p)
    if size > MAX_GRID_ROWS:                       # keep the extremes and the thresholds first: the first values of the pools are the informative ones
        keep = max(2, int(MAX_GRID_ROWS ** (1.0 / max(1, len(pools)))))
        pools = [p[:keep] for p in pools]
    rows = list(itertools.islice(itertools.product(*pools), MAX_GRID_ROWS))
    return pd.DataFrame(rows, columns=columns)


def invariant_fixture(rule: InvariantRule) -> Optional[pd.DataFrame]:
    """A one-row frame that VIOLATES `rule` (every operand present, the `when` condition met), found through `check_invariant` itself; None when no grid value violates it."""
    needed = sorted(expression_identifiers(rule.expression) | (expression_identifiers(rule.when) if rule.when else set()))
    if not needed:
        return None
    strings = _string_literals(rule)
    for kinds in itertools.product(("n", "b", "s") if strings else ("n", "b"), repeat=len(needed)):
        try:
            grid = _grid(needed, kinds, strings)
            result = check_invariant(grid, rule)
        except Exception:                          # noqa: BLE001 -- a kind assignment the expression cannot evaluate (str vs number): try the next one
            continue
        if result.n_violations:
            row = result.violations.iloc[[0]].reset_index(drop=True)
            row["isin"] = CANARY_ISIN
            row["ISIN"] = CANARY_ISIN
            return row[[c for c in row.columns]]
    return None


def group_fixture(rule) -> pd.DataFrame:
    """`rule.min_group_size` rows of ONE group carrying the SAME non-null value: the signature the group-constancy rule flags. Pure."""
    n = max(2, int(rule.min_group_size))
    return pd.DataFrame({rule.group_column: [CANARY_ISIN] * n, rule.value_column: [1.0] * n})


def duplicate_series_rows() -> list:
    """Summary rows in the layout of the runner's series query (isin, real_flag, min_date, max_date, n_distinct_dates, n_rows, n_null_batch, n_below_min_obs) with n_rows > n_distinct_dates: the
    duplicate signature. Pure."""
    return [(CANARY_ISIN, False, "2020-01", "2020-12", 12, 13, 0, 0)]


def fired(findings: Iterable, rule_id: str) -> bool:
    """True when the scratch run's findings contain one for `rule_id`. Pure."""
    return any(f.get("rule_id") == rule_id for f in findings)


def summarize(results: dict) -> dict:
    """The manifest entry of a canary pass: {"checked": n, "fired": n, "failed": [rule ids], "unbuildable": [rule ids]}. `results` = {rule_id: "fired" | "failed" | "unbuildable"}. Pure."""
    return {"checked": sum(1 for v in results.values() if v in ("fired", "failed")), "fired": sum(1 for v in results.values() if v == "fired"),
            "failed": sorted(k for k, v in results.items() if v == "failed"), "unbuildable": sorted(k for k, v in results.items() if v == "unbuildable")}
