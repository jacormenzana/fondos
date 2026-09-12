"""§5 catalog_cost_columns — the single machine-readable scale/bounds catalog
for fund_master + fund_cost_schedule cost columns.

Retires finding K (AUDITORIA_ESTADISTICA.md §7): scale knowledge was
scattered across proyecto1/core/cost_scale.py (docstring only), a
function-local dict in pipeline.py (_COST_PCT_LIMITS, ~line 2972), the DDL
CHECK constraints, and a stale row in SCHEMA_REFERENCE.md. This module is the
canonical import site going forward (P#11 / R-1) — do not duplicate its
bounds elsewhere.

hard_bound = the DDL CHECK constraint in db/schema_fondos.sql, enforced by
SQLite itself at write time.
plausibility_bound = the review-trigger range from
auditStatisticalDataDistributionCostAttributes.md Block 7 — never an
auto-clamp.
Where the two disagree (e.g. Management_Fee_Pct: DDL caps at 10, the skill's
Block 7 table says 0-25), both are kept — they answer different questions
("can this even be written" vs "is this plausible for this fee type").
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Scale = Literal["ratio", "integer_percent", "years", "eur_amount", "categorical"]
BoundType = Literal["HARD_INVARIANT", "PLAUSIBILITY"]


@dataclass(frozen=True)
class Bound:
    min_value: float | None
    max_value: float | None
    bound_type: BoundType


@dataclass(frozen=True)
class CostColumnSpec:
    column: str
    table: str
    scale: Scale
    hard_bound: Bound | None
    plausibility_bound: Bound | None


COST_COLUMNS: dict[str, CostColumnSpec] = {
    "Ongoing_Charge_Recurrent": CostColumnSpec(
        "Ongoing_Charge_Recurrent", "fund_master", "ratio",
        hard_bound=None,
        plausibility_bound=Bound(0.0001, 0.06, "PLAUSIBILITY"),
    ),
    "Entry_Fee_Pct": CostColumnSpec(
        "Entry_Fee_Pct", "fund_master", "ratio",
        hard_bound=None,
        plausibility_bound=Bound(0.0, 0.10, "PLAUSIBILITY"),
    ),
    "Exit_Fee_Pct": CostColumnSpec(
        "Exit_Fee_Pct", "fund_master", "ratio",
        hard_bound=None,
        plausibility_bound=Bound(0.0, 0.10, "PLAUSIBILITY"),
    ),
    "Entry_Fee_Pct_Max": CostColumnSpec(
        "Entry_Fee_Pct_Max", "fund_master", "integer_percent",
        hard_bound=Bound(0, 25, "HARD_INVARIANT"),
        plausibility_bound=None,
    ),
    "Exit_Fee_Pct_Max": CostColumnSpec(
        "Exit_Fee_Pct_Max", "fund_master", "integer_percent",
        hard_bound=Bound(0, 25, "HARD_INVARIANT"),
        plausibility_bound=None,
    ),
    "Management_Fee_Pct": CostColumnSpec(
        "Management_Fee_Pct", "fund_master", "integer_percent",
        hard_bound=Bound(0, 10, "HARD_INVARIANT"),
        plausibility_bound=Bound(0, 25, "PLAUSIBILITY"),
    ),
    "Transaction_Cost_Pct": CostColumnSpec(
        "Transaction_Cost_Pct", "fund_master", "integer_percent",
        hard_bound=Bound(0, 5, "HARD_INVARIANT"),
        plausibility_bound=Bound(0, 5, "PLAUSIBILITY"),
    ),
    "Performance_Fee_Pct": CostColumnSpec(
        "Performance_Fee_Pct", "fund_master", "integer_percent",
        hard_bound=Bound(0, 30, "HARD_INVARIANT"),
        plausibility_bound=None,
    ),
    "Performance_Fee_Basis": CostColumnSpec(
        "Performance_Fee_Basis", "fund_master", "categorical",
        hard_bound=None, plausibility_bound=None,
    ),
    "ACI_1Y": CostColumnSpec(
        "ACI_1Y", "fund_master", "integer_percent",
        hard_bound=Bound(0, 50, "HARD_INVARIANT"),
        plausibility_bound=Bound(0, 25, "PLAUSIBILITY"),
    ),
    "ACI_RHP": CostColumnSpec(
        "ACI_RHP", "fund_master", "integer_percent",
        hard_bound=Bound(0, 25, "HARD_INVARIANT"),
        plausibility_bound=Bound(0, 25, "PLAUSIBILITY"),
    ),
    "Cost_RHP_Years": CostColumnSpec(
        "Cost_RHP_Years", "fund_master", "years",
        hard_bound=Bound(0, 50, "HARD_INVARIANT"),
        plausibility_bound=None,
    ),
    "Horizon_Years": CostColumnSpec(
        "Horizon_Years", "fund_cost_schedule", "years",
        hard_bound=Bound(0, 50, "HARD_INVARIANT"),
        plausibility_bound=None,
    ),
    "Total_Costs_EUR": CostColumnSpec(
        "Total_Costs_EUR", "fund_cost_schedule", "eur_amount",
        hard_bound=None, plausibility_bound=None,
    ),
    "Total_Costs_Pct": CostColumnSpec(
        "Total_Costs_Pct", "fund_cost_schedule", "integer_percent",
        hard_bound=None, plausibility_bound=None,
    ),
    "Annual_Impact_Pct": CostColumnSpec(
        "Annual_Impact_Pct", "fund_cost_schedule", "integer_percent",
        hard_bound=None, plausibility_bound=None,
    ),
}
