"""§5 catalog_invariants — Block 5 structural invariants for both domains,
with the corrections from AUDITORIA_ESTADISTICA.md §2.4 applied:
SORTINO_VS_SHARPE_UP is sign-conditioned on the stored Sharpe (the unconditional
"sortino >= sharpe" is false for a negative numerator; the sign comes from the stored ratio, not from a
re-computed excess over an assumed risk-free rate -- FND-0234), its DOWN half is replaced by the bound that holds
(SORTINO_DOWNSIDE_BOUND, shared/statistical_audit/ratio_bounds.py), and the
original "vol_ann>0 when return_ann!=0" rule is replaced by
FROZEN_NAV_ZERO_VOL, which ties zero volatility to actual periodic-return
variance rather than to return_ann being nonzero (a flat nonzero periodic
return legitimately produces vol_ann==0 without a frozen NAV).
"""
from __future__ import annotations

from ..regime_taxonomy import REGIME_SUFFIX
from .invariants import InvariantRule
from .tolerances import (ACI_AMORTISATION_VISIBLE_PP, ACI_COMPONENTS_TOLERANCE_PP, ACI_ORDER_TOLERANCE_PP, FLOAT_IDENTITY_TOLERANCE, IPC_ELIGIBILITY_FLOOR, KID_ONE_YEAR_RATIO_MAX,
                         KID_ONE_YEAR_RATIO_MIN, KID_ROUNDING_TOLERANCE_PP, SHARPE_VOL_FLOOR)

P2_INVARIANTS: tuple[InvariantRule, ...] = (
    InvariantRule("MAX_DD_RANGE", "-1 <= max_dd <= 0", bound_type="HARD_INVARIANT",
                  description="Drawdown sign or scale error"),
    # Phase 1b (P3 optimization plan): machine-checks the sign convention
    # whose violation (fund_scorer.py's _INVERTED_METRICS wrongly inverting
    # max_dd's percentile rank) corrupted every P3 score. short_max_drawdown
    # had no invariant anywhere before this — unlike max_dd it is not
    # "bounded_unit" in catalog_metrics.py, so catalog_metric_bounds.py's
    # generic [0,1] rule does not (and should not) apply to it either.
    InvariantRule("SHORT_MAX_DD_RANGE", "-1 <= short_max_drawdown <= 0",
                  bound_type="HARD_INVARIANT",
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
        # FND-0234 (2026-10-06): the sign is the stored Sharpe's (vol_ann > 0, so it is the numerator's, whatever risk-free
        # rate P2 used); it used to be `return_ann - 4%`, a rate P2 does not divide by. 0 violations in 23,000 rows.
        "SORTINO_VS_SHARPE_UP", f"sortino >= sharpe - {FLOAT_IDENTITY_TOLERANCE}",
        when="sharpe > 0", bound_type="PLAUSIBILITY",
        description="Downside deviation above the standard deviation with a positive numerator (impossible: "
                    "dd <= sigma when the mean is above the MAR)",
    ),
    InvariantRule(
        # Replaces SORTINO_VS_SHARPE_DOWN (retired, catalog_retired.py): with a negative numerator dd > sigma is
        # legitimate, so "sortino <= sharpe" fired on 4,368 correct rows (+ 4,332 from the wrong sign basis). The bound
        # that does hold is dd^2 <= sigma^2 + 12 * shortfall^2 -- columns built by ratio_bounds.add_downside_deviation_columns.
        "SORTINO_DOWNSIDE_BOUND", f"dd_ann_implied <= dd_ann_max * (1 + {FLOAT_IDENTITY_TOLERANCE})",
        when="sharpe < 0 and sortino < 0", bound_type="PLAUSIBILITY",
        description="Downside deviation larger than the root-mean-square deviation about the risk-free rate allows "
                    "(downside-dev defect)",
    ),
    InvariantRule("CAPTURE_CLAMP", "abs(upside_capture) <= 5 and abs(downside_capture) <= 5",
                  bound_type="HARD_INVARIANT", description="Clamp bound breached"),
    InvariantRule("MACRO_R2_RANGE", "0 <= macro_r2 <= 1", bound_type="HARD_INVARIANT",
                  description="R² clamp breached"),
    InvariantRule(
        # FND-0234 (2026-10-06): eligible only where the row's OWN window had positive CPI change
        # (window_cpi_ann, scripts/audit/run_statistical_audit.py via timeseries.scalar_window_cpi). It used to be
        # gated on TODAY's YoY (+4.55%), so the 21 funds launched in spring 2008 -- whose crisis_2008 window ends
        # in the 2009 CPI decline (annualised -1.1% .. -0.06%) -- raised a HARD_INVARIANT ALARM for a correct
        # real > nominal. IPC_ELIGIBILITY_FLOOR is the same floor WINDOW_DEFLATION_STRICT uses below.
        "DEFLATION_ORDER", f"return_ann_real <= return_ann_nominal + {FLOAT_IDENTITY_TOLERANCE}",
        when=f"window_cpi_ann > {IPC_ELIGIBILITY_FLOOR}", bound_type="HARD_INVARIANT",
        description="Deflation inverted over a window with positive CPI change",
    ),
    InvariantRule("SHARPE_BOUND", "abs(sharpe) < 10", when=f"vol_ann >= {SHARPE_VOL_FLOOR}", bound_type="PLAUSIBILITY",
                  description="Risk-free or vol denominator defect (applies from SHARPE_VOL_FLOOR: below it a large ratio is the arithmetic of a near-zero volatility, as in a money-market fund)"),
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
    # FND-0114 detectors (2026-09-29). Evaluated on the per-window frame built by
    # timeseries.build_window_deflation_frame() (w_* columns) -- _run_invariants routes each
    # rule to whichever frame carries its columns, so these never touch the since_inception
    # wide frame. A literal "real == nominal at t=0" rule was rejected: deflate_nav() rebases
    # the deflator to 1 at the first NAV date under ANY anchor (correct or buggy), so it cannot
    # detect this failure class -- it would pass by construction. WINDOW_FISHER_IDENTITY below
    # instead cross-checks the deflator IMPLIED by the stored nominal/real return_ann pair
    # against the one the CPI series actually supports.
    InvariantRule(
        "WINDOW_NOMINAL_IDENTITY", f"w_nominal_gap <= {FLOAT_IDENTITY_TOLERANCE}",
        bound_type="PLAUSIBILITY",
        description="Stored nominal return_ann != (nav_end/nav_start)^(1/years) from today's "
                     "fund_nav_monthly -- row stale vs a rewritten NAV history (not a deflation "
                     "defect); gates the two deflation rules below so a NAV rewrite can't be "
                     "mistaken for a deflation bug",
    ),
    InvariantRule(
        "WINDOW_DEFLATION_STRICT", "w_return_real < w_return_nominal",
        when=f"w_cpi_ann > {IPC_ELIGIBILITY_FLOOR} and "
             f"w_nominal_gap <= {FLOAT_IDENTITY_TOLERANCE}",
        bound_type="HARD_INVARIANT",
        description="Real return_ann not strictly below nominal over a window with positive "
                     "CPI change -- deflator flattened over that window (FND-0114 class)",
    ),
    InvariantRule(
        "WINDOW_FISHER_IDENTITY",
        f"abs(w_deflator_implied / w_deflator_expected - 1) <= {FLOAT_IDENTITY_TOLERANCE}",
        when=f"w_nominal_gap <= {FLOAT_IDENTITY_TOLERANCE}",
        bound_type="HARD_INVARIANT",
        description="Deflator implied by stored nominal/real return_ann != ipc(end)/ipc(start) "
                     "under deflate_nav()'s merge_asof(backward)+leading-bfill contract -- wrong "
                     "IPC anchor or date alignment in the real series (FND-0114 class)",
    ),
) + tuple(
    # N_OBS_NONNEG fix (2026-09-13): the catalog cited a bare `n_obs` column
    # that never existed — the real metrics are per-regime. One rule per
    # regime, generated rather than hand-listed (P#11/DRY).
    # Phase 1g: the 7-regime set itself used to be hand-listed here too, a
    # second independent copy of the same set already duplicated between
    # regime_classifier.py (P3) and regime_returns.py (P2) — its own comment
    # above already records one hand-listing mistake. Now generated from
    # shared/regime_taxonomy.py, the single source of truth.
    InvariantRule(f"N_OBS_NONNEG_{suffix.upper()}", f"n_obs_{suffix} >= 0",
                  bound_type="HARD_INVARIANT", description="Observation count negative")
    for suffix in sorted(REGIME_SUFFIX.values())
)

COST_INVARIANTS: tuple[InvariantRule, ...] = (
    # FND-0276/0277 (2026-10-17): ACI_1Y and ACI_RHP are published with ONE decimal, so their order and equality are only decidable to
    # ACI_PUBLISHED_UNIT_PP (tolerances.py: ACI_ORDER_TOLERANCE_PP, ACI_AMORTISATION_VISIBLE_PP). The order rule tolerates one unit; the amortisation rule applies only where the entry fee
    # would move the ACI by at least one unit: Entry_Fee_Pct * (1 - 1 / Cost_RHP_Years), the fee spread over the holding period.
    # 2026-11-06 (owner decision, FND-0276): a rising pair is TOLERATED when the stored KID text itself prints exactly that pair next to an annual-impact wording
    # (ACI_Rise_Printed, set by run_statistical_audit._mark_printed_rises with shared.statistical_audit.text_evidence: independent of the extractor). The 73 funds the rule
    # flagged after the 2026-11-06 recompute all print their pair (no entry fee to amortise, costs that grow); a rising pair the document does NOT print stays an ALARM.
    InvariantRule("ACI_AMORTISATION_ORDER", f"ACI_RHP <= ACI_1Y + {ACI_ORDER_TOLERANCE_PP}", when="not ACI_Rise_Printed", bound_type="HARD_INVARIANT",
                  description="Amortisation inverted (not printed by the document)"),
    InvariantRule(
        "ACI_MUST_AMORTISE", "ACI_1Y != ACI_RHP",
        # 2026-11-13 (package B, owner order): Entry_Fee_Pct is a RATIO (0.05 == 5 %; catalog_cost_columns: kind "ratio", 1,307 of 1,315 funds equal Entry_Fee_Pct_Max / 100), the ACI and
        # the tolerance are percentage points. The rule compared the ratio with points, so a 5 % charge counted as 0.04 pp and it could never fire (0 violators in 575 funds with equal pairs
        # and a holding period over one year; corrected: see AUDITORIA_ESTADISTICA.md). The FND-0276 sweep that read the 97 old violators as "an entry fee of 0.0525 % or less" read ratios as percent.
        # An equal pair the KID itself prints is faithful (ACI_Equal_Pair_Printed, run_statistical_audit._mark_equal_pairs_printed): the same text evidence as ACI_AMORTISATION_ORDER. Corrected, the
        # rule flagged LU1139789298 / LU1251922891 / LU1770914999, whose KIDs print "1.7% 1.7%" and say the 5 % entry charge goes to the distributor and is not in the costs shown: false positives.
        when=f"Cost_RHP_Years > 1 and Entry_Fee_Pct * 100 * (1 - 1 / Cost_RHP_Years) >= {ACI_AMORTISATION_VISIBLE_PP} and not ACI_Equal_Pair_Printed",
        bound_type="HARD_INVARIANT", description="Entry cost must amortise",
    ),
    # FND-0280/0285 (2026-10-17). The components are ANNUAL percentages (management, transaction) published to 2 decimals; ACI_RHP is the cost
    # impact over the HOLDING PERIOD published to 1 decimal. Two consequences, both measured over fund_master (103 MGMT / 33 TXN violators):
    #  * Rounding: a component can exceed the total by up to half a unit of the coarser grid (50 MGMT violators within 0.10 pp):
    #    KID_ROUNDING_TOLERANCE_PP, the constant the cost pair rules below use.
    #  * Holding periods under a year (money market, short-term: "1 day to 3 months", LU0987193777): the cost over the period is a fraction of
    #    the annual charge (0.31 %/yr over 3 months = 0.08 USD, shown as "Total costs 0 / 0.0 %"), so an annual component legitimately exceeds the
    #    total. 36 of the 42 funds with a zero total and a positive component have an RHP under 1 year or unstated. The comparison only holds
    #    for Cost_RHP_Years >= 1 (an unknown RHP is not comparable either: the rule is skipped, not failed). A zero total at RHP >= 1 year with a
    #    positive component (6 funds) stays flagged: that one is a real extraction defect.
    InvariantRule("MGMT_LE_TOTAL", f"Management_Fee_Pct <= ACI_RHP + {KID_ROUNDING_TOLERANCE_PP}", when="Cost_RHP_Years >= 1",
                  bound_type="HARD_INVARIANT", description="Component exceeds total"),
    InvariantRule("TXN_LE_TOTAL", f"Transaction_Cost_Pct <= ACI_RHP + {KID_ROUNDING_TOLERANCE_PP}", when="Cost_RHP_Years >= 1",
                  bound_type="HARD_INVARIANT", description="Component exceeds total"),
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
    # FND-0278 (2026-10-17, owner decision): at horizon 1 the pair may also differ by a KID CONVENTION, not only by rounding -- the KID publishes the 1-decimal ACI and,
    # in the older template, assumes the moderate first-year return. tolerances.KID_ONE_YEAR_RATIO_MIN/MAX (0.9 to 1.25, evidence there) accept that band at horizon 1 ONLY;
    # beyond one year the relation stays strict, and a ratio outside the band (0.03 .. 118 in the live data) is still an extraction defect. Written with multiplications, not
    # a division, so a zero total never divides: Total_Costs_Pct = 0 with a positive annual impact still violates.
    InvariantRule("ANNUAL_LE_ACCUMULATED",
                  f"(Annual_Impact_Pct <= Total_Costs_Pct + {KID_ROUNDING_TOLERANCE_PP}) | "
                  f"((Horizon_Years == 1) & (Annual_Impact_Pct <= Total_Costs_Pct * {KID_ONE_YEAR_RATIO_MAX}))",
                  when="Horizon_Years >= 1", bound_type="HARD_INVARIANT",
                  # FND-0278 (2026-11-04): below one year the annual impact is a per-YEAR rate and the accumulated total covers only the holding period, so annual > total is the correct
                  # arithmetic (LU-class money fund with a 3-month RHP: annual 0.13 %, accumulated 0.03 % = 0.13 % * 0.25). The relation is defined from one year on.
                  description="Annual exceeds accumulated (from a one-year horizon)"),
    InvariantRule(
        "ANNUAL_EQUALS_TOTAL_AT_1Y",
        f"(abs(Annual_Impact_Pct - Total_Costs_Pct) < {KID_ROUNDING_TOLERANCE_PP}) | "
        f"((Annual_Impact_Pct >= Total_Costs_Pct * {KID_ONE_YEAR_RATIO_MIN}) & (Annual_Impact_Pct <= Total_Costs_Pct * {KID_ONE_YEAR_RATIO_MAX}))",
        when="Horizon_Years == 1", bound_type="HARD_INVARIANT",
        description="Equal at horizon 1y within KID rounding or the KID's own convention band",
    ),
    # FLOAT_IDENTITY_TOLERANCE audited 2026-09-13 (tolerances.py): sweep at
    # 0.0001/0.001/0.01/0.06 gave 89/89/93/190 violations — monotonic growth with no rounding-grid
    # plateau, confirming this is an identity test (OC and ACI_RHP are either the same number by
    # extraction defect or genuinely different) and 0.0001 is already the correct value, not an
    # unaudited default.
    # 2026-11-08 (owner decision, FND-0034): the ACI is printed with ONE decimal, so an OC equal to it is the publisher's rounding when ACI = OC + transaction costs and nothing else is charged
    # (LU2132880597: management 0,30 + transaction 0,04 = 0,34, printed "0,3"). The column is set by the runner from shared.cost_rounding, the definition P1 uses to accept the OC.
    InvariantRule("OC_NOT_CONTAMINATED",
                  f"abs(Ongoing_Charge_Recurrent * 100 - ACI_RHP) > {FLOAT_IDENTITY_TOLERANCE}",
                  when="not OC_ACI_Rounding_Explained",
                  bound_type="HARD_INVARIANT", description="OC contaminated with the ACI"),
    # 2026-11-13 (owner decision, package B / FND-0034): the stored ACI_RHP is checked against the cost components, because no rule saw a stored value the KID contradicts: 20 funds stored the 1-year
    # figure or a scenario percentage (6.66 for a printed 1.66, 11.88 for a printed 2.4) and one stored the management line (1.04 for a printed 1.3). The band is management + transaction cost
    # (ACI_Components_Low) up to that plus the entry and exit charges spread over the holding period (ACI_Components_High; the entry charge may or may not be in the ACI, so both ends are valid),
    # half a published unit either side. Applies where the components are known, no performance fee is charged, the holding period is a year or more (under a year the KID prints the cost of the
    # PERIOD, not an annual figure), and the KID text does NOT print the stored pair (ACI_Pair_Printed, shared.statistical_audit.text_evidence: independent of the extractor).
    # Columns set by run_statistical_audit._mark_aci_components.
    InvariantRule("ACI_RHP_VS_COMPONENTS",
                  f"ACI_RHP > ACI_Components_Low - {ACI_COMPONENTS_TOLERANCE_PP} and ACI_RHP < ACI_Components_High + {ACI_COMPONENTS_TOLERANCE_PP}",
                  when="ACI_Components_Eligible and not ACI_Pair_Printed",
                  bound_type="HARD_INVARIANT", description="ACI_RHP outside management + transaction cost + entry / exit (not printed by the document)"),
)
