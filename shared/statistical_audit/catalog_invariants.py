"""§5 catalog_invariants — Block 5 structural invariants for both domains,
with the two corrections from AUDITORIA_ESTADISTICA.md §2.4 applied:
SORTINO_VS_SHARPE is sign-conditioned on excess_return (the unconditional
"sortino >= sharpe" is false whenever excess return is negative), and the
original "vol_ann>0 when return_ann!=0" rule is replaced by
FROZEN_NAV_ZERO_VOL, which ties zero volatility to actual periodic-return
variance rather than to return_ann being nonzero (a flat nonzero periodic
return legitimately produces vol_ann==0 without a frozen NAV).
"""
from __future__ import annotations

from .invariants import InvariantRule

P2_INVARIANTS: tuple[InvariantRule, ...] = (
    InvariantRule("MAX_DD_RANGE", "-1 <= max_dd <= 0", bound_type="HARD_INVARIANT",
                  description="Drawdown sign or scale error"),
    InvariantRule("VOL_ANN_NONNEG", "vol_ann >= 0", bound_type="HARD_INVARIANT",
                  description="Negative dispersion — impossible"),
    InvariantRule("SRRI_VOL_NONNEG", "srri_volatility >= 0", bound_type="HARD_INVARIANT",
                  description="Negative dispersion — impossible"),
    InvariantRule("SRRI_NAV_RANGE", "0 <= srri_nav <= 7", bound_type="HARD_INVARIANT",
                  description="Bucket out of range"),
    InvariantRule("MONTH_SHARE_OVERFLOW", "pct_positive_months + pct_negative_months <= 1.0001",
                  bound_type="HARD_INVARIANT", description="Month-share overflow"),
    InvariantRule(
        "SORTINO_VS_SHARPE_UP", "sortino >= sharpe - 0.0001",
        when="excess_return > 0", bound_type="PLAUSIBILITY",
        description="Downside-dev defect (sign-conditioned per §2.4 — not universal)",
    ),
    InvariantRule(
        "SORTINO_VS_SHARPE_DOWN", "sortino <= sharpe + 0.0001",
        when="excess_return < 0", bound_type="PLAUSIBILITY",
        description="Downside-dev defect (sign-conditioned per §2.4 — not universal)",
    ),
    InvariantRule("CAPTURE_CLAMP", "abs(upside_capture) <= 5 and abs(downside_capture) <= 5",
                  bound_type="HARD_INVARIANT", description="Clamp bound breached"),
    InvariantRule("MACRO_R2_RANGE", "0 <= macro_r2 <= 1", bound_type="HARD_INVARIANT",
                  description="R² clamp breached"),
    InvariantRule(
        "DEFLATION_ORDER", "return_ann_real <= return_ann_nominal + 0.0001",
        when="ipc_yoy > 0", bound_type="HARD_INVARIANT",
        description="Deflation inverted",
    ),
    InvariantRule("SHARPE_BOUND", "abs(sharpe) < 10", bound_type="PLAUSIBILITY",
                  description="Risk-free or vol denominator defect"),
    InvariantRule("SORTINO_BOUND", "abs(sortino) < 10", bound_type="PLAUSIBILITY",
                  description="Risk-free or vol denominator defect"),
    InvariantRule("N_OBS_NONNEG", "n_obs >= 0", bound_type="HARD_INVARIANT",
                  description="Observation count negative"),
    InvariantRule(
        "FROZEN_NAV_ZERO_VOL", "not (vol_ann == 0 and periodic_return_variance > 0.0001)",
        bound_type="HARD_INVARIANT",
        description="Frozen/flat NAV masquerading as zero volatility (corrected per §2.4 — "
                     "the original 'vol_ann>0 when return_ann!=0' rule is not a true invariant)",
    ),
)

COST_INVARIANTS: tuple[InvariantRule, ...] = (
    InvariantRule("ACI_AMORTISATION_ORDER", "ACI_RHP <= ACI_1Y", bound_type="HARD_INVARIANT",
                  description="Amortisation inverted"),
    InvariantRule(
        "ACI_MUST_AMORTISE", "ACI_1Y != ACI_RHP",
        when="Cost_RHP_Years > 1 and Entry_Fee_Pct > 0", bound_type="HARD_INVARIANT",
        description="Entry cost must amortise",
    ),
    InvariantRule("MGMT_LE_TOTAL", "Management_Fee_Pct <= ACI_RHP", bound_type="HARD_INVARIANT",
                  description="Component exceeds total"),
    InvariantRule("TXN_LE_TOTAL", "Transaction_Cost_Pct <= ACI_RHP", bound_type="HARD_INVARIANT",
                  description="Component exceeds total"),
    InvariantRule("ANNUAL_LE_ACCUMULATED", "Annual_Impact_Pct <= Total_Costs_Pct",
                  bound_type="HARD_INVARIANT", description="Annual exceeds accumulated"),
    InvariantRule(
        "ANNUAL_EQUALS_TOTAL_AT_1Y", "abs(Annual_Impact_Pct - Total_Costs_Pct) < 0.0001",
        when="Horizon_Years == 1", bound_type="HARD_INVARIANT",
        description="Equal by definition at horizon 1y",
    ),
    InvariantRule("OC_NOT_CONTAMINATED", "abs(Ongoing_Charge_Recurrent * 100 - ACI_RHP) > 0.0001",
                  bound_type="HARD_INVARIANT", description="OC contaminated with the ACI"),
)
