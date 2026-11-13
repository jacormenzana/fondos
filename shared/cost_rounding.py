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

__all__ = ["ACI_PUBLISHED_UNIT_PP", "ENTRY_CHARGE_SPREAD_MAX_FACTOR", "aci_components_band", "aci_rounding_explained", "refined_aci_pct"]

# The unit the publisher prints the annual cost impact in, in percentage points (one decimal). shared.statistical_audit.tolerances derives its ACI tolerances from this one.
ACI_PUBLISHED_UNIT_PP = 0.10
_EDGE = 1e-9                       # a gap of exactly half a unit is ambiguous (0.35 prints 0,3 or 0,4): not explained
# How far above the straight-line spread (entry charge / holding years) the annual impact of an entry charge may sit: the PRIIPs figure is a yield reduction, so the charge compounds with the
# assumed growth. Measured 2026-11-13 over the 1,539 funds whose KID prints their ACI pair, an entry charge of 1 % or more, a holding period of 2 years or more: (ACI - management -
# transaction cost) / (charge / years) has median 1.10, p90 1.42, p95 1.56, p98 1.82.
ENTRY_CHARGE_SPREAD_MAX_FACTOR = 1.75
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


def aci_components_band(management_pct, transaction_cost_pct, entry_ratio, entry_max_pct, exit_ratio, exit_max_pct, performance_fee_pct, rhp_years) -> Optional[tuple]:
    """(low, high) in whole percent: the annual cost impact over the holding period that the cost components allow (package B, FND-0034, 2026-11-13), or None when they cannot say.
    low = management + transaction cost; high = low + the entry and the exit charge spread over the holding period, times ENTRY_CHARGE_SPREAD_MAX_FACTOR (the charge may or may not sit in the
    printed ACI, so both ends are valid). The entry / exit charge is the larger of the stored actual (a RATIO: 0.05 == 5 %, fund_master.Entry_Fee_Pct) and the stored maximum (whole percent, *_Fee_Pct_Max); absent counts 0.
    None when management or transaction cost is unknown, a performance fee is charged (its amount is not stored), or the holding period is under one year (the KID then prints the cost of the
    PERIOD, not an annual figure). Pure."""
    mgmt, tc, years, perf = _num(management_pct), _num(transaction_cost_pct), _num(rhp_years), _num(performance_fee_pct)
    if mgmt is None or tc is None or years is None or years < 1 or _has(perf):
        return None

    def charge(ratio, max_pct) -> float:
        r, m = _num(ratio), _num(max_pct)
        return max(0.0 if r is None else r * 100.0, 0.0 if m is None else m)

    low = mgmt + tc
    return low, low + ENTRY_CHARGE_SPREAD_MAX_FACTOR * (charge(entry_ratio, entry_max_pct) + charge(exit_ratio, exit_max_pct)) / years
