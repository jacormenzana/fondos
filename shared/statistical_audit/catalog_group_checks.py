"""§5 catalog_group_checks — Block 5 group-wise structural checks (see
group_checks.py for why these can't be expressed as InvariantRule).

Currently one defect: fund_cost_schedule.Total_Costs_Pct / Total_Costs_EUR
duplicated identically across every Horizon_Years row of the same ISIN
(AUDITORIA_ESTADISTICA.md §2.7, 264 residual finding rows / 447 funds at
the time this catalog was added, 2026-09-13). Detection only — the repair
is a separate, gated re-extraction over cached KIID text
(reference_cost_recompute_cached), not part of this catalog.
"""
from __future__ import annotations

from .group_checks import GroupConstancyRule

COST_GROUP_CHECKS: tuple[GroupConstancyRule, ...] = (
    GroupConstancyRule(
        "TOTAL_COSTS_PCT_CONSTANT_ACROSS_HORIZONS", group_column="ISIN",
        value_column="Total_Costs_Pct",
        description="Total_Costs_Pct duplicated identically across a fund's Horizon_Years rows "
                     "— PLAIN_TEXT-route extraction defect, not a rounding artifact",
    ),
    GroupConstancyRule(
        "TOTAL_COSTS_EUR_CONSTANT_ACROSS_HORIZONS", group_column="ISIN",
        value_column="Total_Costs_EUR",
        description="Total_Costs_EUR duplicated identically across a fund's Horizon_Years rows "
                     "— PLAIN_TEXT-route extraction defect, not a rounding artifact",
    ),
)
