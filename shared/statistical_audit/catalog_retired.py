"""C1 (FND-0130, 2026-09-28, audit-skill-alignment plan Wave 3) — RETIRED_RULES.

A registry of corrections already made in the declarative catalogs (catalog_pairs.py,
catalog_invariants.py) that the two skill `.md` files (auditStatisticalDataDistribution
{P2Metrics,CostAttributes}.md) still describe in their pre-correction form -- moving the
reasoning out of scattered code comments into one queryable place, and giving
scripts/audit/check_audit_skill_sync.py (C3) something concrete to check a skill row against
instead of just flagging "unknown rule."

Two shapes of entry, both keyed by a rule_id:
  - A fully removed rule (key = the id that no longer exists anywhere, e.g.
    VOL_ANN_EQUALS_SRRI_VOL): `replaced_by` is None.
  - An active rule whose current form corrects what the skill text states differently
    (key = the CURRENT active rule_id, e.g. FROZEN_NAV_ZERO_VOL): `replaced_by` is the same id,
    `reason` explains what the skill's stated version got wrong.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RetiredRule:
    reason: str
    date: str
    replaced_by: str | None


RETIRED_RULES: dict[str, RetiredRule] = {
    "VOL_ANN_EQUALS_SRRI_VOL": RetiredRule(
        reason="Investigated the live 100%-match finding and confirmed vol_ann(since_inception, "
               "nominal) and srri_volatility are mathematically identical by construction (same "
               "std(ddof=1)*sqrt(12) formula over the same NAV series), not a shared-bug "
               "coincidence. The pair could never have surfaced a defect as specified -- a "
               "catalog error, not a P2 bug. Still present in the P2 skill's Block 2 table.",
        date="2026-09-13",
        replaced_by=None,
    ),
    "FROZEN_NAV_ZERO_VOL": RetiredRule(
        reason="Replaces the original 'vol_ann>0 when return_ann!=0' invariant the P2 skill's "
               "Block 5 table still states: that rule is not a true invariant -- a flat nonzero "
               "periodic return legitimately produces vol_ann==0 without a frozen NAV. "
               "FROZEN_NAV_ZERO_VOL ties zero volatility to actual periodic-return variance "
               "instead, derived directly from fund_nav_monthly.",
        date="2026-09-15",
        replaced_by="FROZEN_NAV_ZERO_VOL",
    ),
    "SORTINO_VS_SHARPE_UP": RetiredRule(
        reason="Replaces the unconditional 'sortino >= sharpe' the P2 skill's Block 5 table still "
               "states: that form is false for a negative numerator. 2026-10-06 (FND-0234): the sign is now "
               "the stored Sharpe's (sharpe > 0), not `return_ann - RISK_FREE_RATE_ANN` -- P2 divides by the "
               "date-aligned risk-free rate (2.5% / 0.75%), not the 4% config constant, so the old sign was "
               "wrong for every row with a return between the two.",
        date="2026-09-13",
        replaced_by="SORTINO_VS_SHARPE_UP",
    ),
    "SORTINO_VS_SHARPE_DOWN": RetiredRule(
        reason="Retired 2026-10-06 (FND-0234): 8,700 live WARNs (2,441 ISINs) were false. 4,332 came from taking the "
               "sign from return_ann - 4% while P2 divides by the date-aligned risk-free rate; the other 4,368 are "
               "correct values: with a negative numerator the downside deviation (a semi-deviation about the MAR over "
               "ALL periods) legitimately exceeds sigma, so sortino > sharpe. Replaced by SORTINO_DOWNSIDE_BOUND "
               "(dd^2 <= sigma^2 + 12 * shortfall^2), which holds.",
        date="2026-10-06",
        replaced_by="SORTINO_DOWNSIDE_BOUND",
    ),
    "SHARPE_EQUALS_SORTINO": RetiredRule(
        reason="Eligibility restricted 2026-10-06 (FND-0234) to rows where BOTH ratios are clearly positive (> 0.01). The live "
               "25 matches were arithmetic or coincidence, not a defect: 17 had a numerator near 0 (both ratios ~0), 8 a "
               "negative numerator where dd/sigma legitimately crosses 1. With a positive numerator dd < sigma strictly, so "
               "equal ratios there remain a real 'downside deviation collapsed to total volatility' signature. The P2 skill's "
               "Block 2 table states the pair without any eligibility.",
        date="2026-10-06",
        replaced_by="SHARPE_EQUALS_SORTINO",
    ),
    "ANNUAL_LE_ACCUMULATED": RetiredRule(
        reason="Tolerance widened from FLOAT_IDENTITY_TOLERANCE (0.0001) to "
               "KID_ROUNDING_TOLERANCE_PP (0.06 percentage points): at 0.0001 this rule produced "
               "an 87% false-positive rate (1,778/2,042 horizon=1y rows) purely from the KID's "
               "own 1-decimal Annual_Impact_Pct rounding vs Total_Costs_Pct's 2-decimal computed "
               "precision. 0.06 eliminates the rounding noise while preserving the genuine "
               "447-fund cost-schedule duplication defect (264 residual rows). Neither skill's "
               "Method Controls mention this tolerance at all -- both imply exact equality.",
        date="2026-09-13",
        replaced_by="ANNUAL_LE_ACCUMULATED",
    ),
    "ANNUAL_EQUALS_TOTAL_AT_1Y": RetiredRule(
        reason="Same KID-rounding tolerance widening as ANNUAL_LE_ACCUMULATED -- see that entry.",
        date="2026-09-13",
        replaced_by="ANNUAL_EQUALS_TOTAL_AT_1Y",
    ),
}
