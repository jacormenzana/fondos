# shared/cost_rounding.py
# -*- coding: utf-8 -*-
"""
Single definition of "this annual cost impact is the ongoing charge plus the transaction costs, rounded by the publisher" (FND-0034 follow-up, owner decision 2026-11-08; P#11).

A PRIIPs KID prints the annual cost impact (ACI) with ONE decimal while the management row and the transaction-cost row carry two. A fund whose only components are the management and
administrative costs (the ongoing charge, OC, without transaction costs: cost_scale.py) and the transaction costs has ACI = OC + TC, and the publisher's rounding makes the printed ACI
equal to the OC whenever TC is under half of the printed unit: LU2132880597 prints management 0,30 %, transaction costs 0,04 % (30 EUR + 4 EUR = 34 EUR on 10,000) and an annual impact of
"0,3 %", whose exact value is 0.34. An OC equal to the ACI is therefore the contamination of FND-0034 (the ACI copied into the OC) ONLY when the rounding does not explain it.

Pure, with no dependency (R-7): used by P1 (cost_scale.resolve_ongoing_charge, oc_resolution.refine_fund_aci) and by the statistical audit (OC_NOT_CONTAMINATED), so the writer guard, the
precision refinement and the audit can never disagree. All *_pct arguments are in whole percent (0.30 == 0,30 %); `oc_pct` is the OC already converted (ratio x 100).
"""
from __future__ import annotations

from typing import Optional

__all__ = ["ACI_PUBLISHED_UNIT_PP", "aci_rounding_explained", "refined_aci_pct"]

# The unit the publisher prints the annual cost impact in, in percentage points (one decimal). shared.statistical_audit.tolerances derives its ACI tolerances from this one.
ACI_PUBLISHED_UNIT_PP = 0.10
_EDGE = 1e-9                       # a gap of exactly half a unit is ambiguous (0.35 prints 0,3 or 0,4): not explained
_EQUAL_PP = 0.0001                 # the identity tolerance of OC_NOT_CONTAMINATED (tolerances.FLOAT_IDENTITY_TOLERANCE; a test keeps them equal)


def _num(x) -> Optional[float]:
    return None if x is None or x != x else float(x)


def _has(x) -> bool:
    """True when a fee component is present and not zero (None and NaN count as absent)."""
    v = _num(x)
    return v is not None and abs(v) > 1e-12


def aci_rounding_explained(oc_pct, aci_pct, transaction_cost_pct, entry_fee_pct=None, exit_fee_pct=None, performance_fee_pct=None) -> bool:
    """True when the printed `aci_pct` is OC + transaction costs rounded to the published unit: the components are known (OC, ACI and TC present), nothing else is charged (no entry, exit
    or performance fee) and |OC + TC - ACI| is under half a unit. Transaction costs of exactly 0 are explained too (ACI = OC). Unknown components explain nothing."""
    oc, aci, tc = _num(oc_pct), _num(aci_pct), _num(transaction_cost_pct)
    if oc is None or aci is None or tc is None or tc < 0:
        return False
    if _has(entry_fee_pct) or _has(exit_fee_pct) or _has(performance_fee_pct):
        return False
    return abs(oc + tc - aci) < ACI_PUBLISHED_UNIT_PP / 2 - _EDGE


def refined_aci_pct(oc_pct, aci_pct, transaction_cost_pct, entry_fee_pct=None, exit_fee_pct=None, performance_fee_pct=None) -> Optional[float]:
    """The exact two-decimal ACI (OC + TC) for a fund whose stored ACI equals its OC only because of the publisher's rounding, else None. It applies ONLY where the stored ACI equals the OC
    (the case the audit flags), the rounding explains it, and the exact value differs from the stored one (TC > 0): everything else keeps the figure the document printed."""
    oc, aci, tc = _num(oc_pct), _num(aci_pct), _num(transaction_cost_pct)
    if oc is None or aci is None or tc is None:
        return None
    if abs(oc - aci) > _EQUAL_PP:
        return None
    if not aci_rounding_explained(oc, aci, tc, entry_fee_pct, exit_fee_pct, performance_fee_pct):
        return None
    exact = round(oc + tc, 2)
    return exact if abs(exact - aci) > _EDGE else None
