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
from .tolerances import FLOAT_IDENTITY_TOLERANCE, KID_ROUNDING_TOLERANCE_PP

P2_INVARIANTS: tuple[InvariantRule, ...] = (
    InvariantRule("MAX_DD_RANGE", "-1 <= max_dd <= 0", bound_type="HARD_INVARIANT",
                  description="Drawdown sign or scale error"),
    InvariantRule("VOL_ANN_NONNEG", "vol_ann >= 0", bound_type="HARD_INVARIANT",
                  description="Negative dispersion — impossible"),
    InvariantRule("SRRI_VOL_NONNEG", "srri_volatility >= 0", bound_type="HARD_INVARIANT",
                  description="Negative dispersion — impossible"),
    InvariantRule("SRRI_NAV_RANGE", "0 <= srri_nav <= 7", bound_type="HARD_INVARIANT",
                  description="Bucket out of range"),
    InvariantRule(
        "MONTH_SHARE_OVERFLOW",
        f"pct_positive_months + pct_negative_months <= 1 + {FLOAT_IDENTITY_TOLERANCE}",
        bound_type="HARD_INVARIANT", description="Month-share overflow"),
    InvariantRule(
        "SORTINO_VS_SHARPE_UP", f"sortino >= sharpe - {FLOAT_IDENTITY_TOLERANCE}",
        when="excess_return > 0", bound_type="PLAUSIBILITY",
        description="Downside-dev defect (sign-conditioned per §2.4 — not universal)",
    ),
    InvariantRule(
        "SORTINO_VS_SHARPE_DOWN", f"sortino <= sharpe + {FLOAT_IDENTITY_TOLERANCE}",
        when="excess_return < 0", bound_type="PLAUSIBILITY",
        description="Downside-dev defect (sign-conditioned per §2.4 — not universal)",
    ),
    InvariantRule("CAPTURE_CLAMP", "abs(upside_capture) <= 5 and abs(downside_capture) <= 5",
                  bound_type="HARD_INVARIANT", description="Clamp bound breached"),
    InvariantRule("MACRO_R2_RANGE", "0 <= macro_r2 <= 1", bound_type="HARD_INVARIANT",
                  description="R² clamp breached"),
    InvariantRule(
        "DEFLATION_ORDER", f"return_ann_real <= return_ann_nominal + {FLOAT_IDENTITY_TOLERANCE}",
        when="ipc_yoy > 0", bound_type="HARD_INVARIANT",
        description="Deflation inverted",
    ),
    InvariantRule("SHARPE_BOUND", "abs(sharpe) < 10", bound_type="PLAUSIBILITY",
                  description="Risk-free or vol denominator defect"),
    InvariantRule("SORTINO_BOUND", "abs(sortino) < 10", bound_type="PLAUSIBILITY",
                  description="Risk-free or vol denominator defect"),
    InvariantRule(
        # NOTE: 0.0001 here is a periodic-RETURN-VARIANCE floor (squared-return
        # units), not a float-identity slack between two same-scale values —
        # deliberately NOT aliased to FLOAT_IDENTITY_TOLERANCE (see tolerances.py
        # module docstring). Left as a literal pending its own sweep.
        "FROZEN_NAV_ZERO_VOL", "not (vol_ann == 0 and periodic_return_variance > 0.0001)",
        bound_type="HARD_INVARIANT",
        description="Frozen/flat NAV masquerading as zero volatility (corrected per §2.4 — "
                     "the original 'vol_ann>0 when return_ann!=0' rule is not a true invariant)",
    ),
) + tuple(
    # N_OBS_NONNEG fix (2026-09-13): the catalog cited a bare `n_obs` column
    # that never existed — the real metrics are per-regime, verified live:
    # n_obs_{contraccion,crisis_financiera,estanflacion,expansion,
    # recalentamiento,recalentamiento_tardio,shock_energetico}. One rule per
    # regime, generated rather than hand-listed (P#11/DRY).
    InvariantRule(f"N_OBS_NONNEG_{regime.upper()}", f"n_obs_{regime} >= 0",
                  bound_type="HARD_INVARIANT", description="Observation count negative")
    for regime in (
        "contraccion", "crisis_financiera", "estanflacion", "expansion",
        "recalentamiento", "recalentamiento_tardio", "shock_energetico",
    )
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
    # KID_ROUNDING_TOLERANCE_PP (2026-09-13, AUDITORIA_ESTADISTICA.md §2.6-2.7, named constant
    # centralized in tolerances.py 2026-09-13): investigated the live 87%/45% violation rates on
    # these two rules with tolerance 0.0001. KID Annual_Impact_Pct (Reduction in Yield) is
    # published rounded to 1 decimal place per PRIIPs convention while Total_Costs_Pct carries
    # 2-decimal computed precision — legitimate rounding alone explains 1,778/2,042 (87%) of the
    # horizon=1y cases (|diff|<0.06). The residual ~264 rows are a distinct, genuine defect
    # (Total_Costs_Pct/EUR duplicated identically across a fund's different Horizon_Years rows in
    # 447 funds — an extraction bug, not a rounding artifact) and remain correctly flagged. This
    # tolerance is grounded in the KID's own regulatory publication format, not an arbitrary
    # proximity heuristic.
    InvariantRule("ANNUAL_LE_ACCUMULATED",
                  f"Annual_Impact_Pct <= Total_Costs_Pct + {KID_ROUNDING_TOLERANCE_PP}",
                  bound_type="HARD_INVARIANT", description="Annual exceeds accumulated"),
    InvariantRule(
        "ANNUAL_EQUALS_TOTAL_AT_1Y",
        f"abs(Annual_Impact_Pct - Total_Costs_Pct) < {KID_ROUNDING_TOLERANCE_PP}",
        when="Horizon_Years == 1", bound_type="HARD_INVARIANT",
        description="Equal by definition at horizon 1y (within KID rounding)",
    ),
    # FLOAT_IDENTITY_TOLERANCE audited 2026-09-13 (tolerances.py): sweep at
    # 0.0001/0.001/0.01/0.06 gave 89/89/93/190 violations — monotonic growth with no rounding-grid
    # plateau, confirming this is an identity test (OC and ACI_RHP are either the same number by
    # extraction defect or genuinely different) and 0.0001 is already the correct value, not an
    # unaudited default.
    InvariantRule("OC_NOT_CONTAMINATED",
                  f"abs(Ongoing_Charge_Recurrent * 100 - ACI_RHP) > {FLOAT_IDENTITY_TOLERANCE}",
                  bound_type="HARD_INVARIANT", description="OC contaminated with the ACI"),
)
