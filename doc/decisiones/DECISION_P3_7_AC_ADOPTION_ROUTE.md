# Decision note: adoption route of the fee penalty of AC (PREREG_P3_7 section 13)

**Status: RULED IN PRINCIPLE 2026-11-26, Amendment 2 not drafted.** Owner rulings of 2026-11-26: (a) aim for option 2, but run next step 1 first; no Amendment 2 is drafted or committed until it is verified how the PIT and the shadow cost the unknown fees and which minimum coverage threshold to require; (b) Amendment 5 of `PREREG_FND-0193` (2026-11-21) prevails over the 2026-11-15 line of `PLAN_SELECCION_CALIDAD.md`: the band 0.10 is decoupled from the FND-0299 holdout and is ruled at the 6-row review (R6) (that line now carries the pointer). **Written 2026-11-26. Read-only work: no code, study, switch or live write. No turnover, cost or selection-difference field of the AC shadow rows was read (PREREG_P3_7 section 13).** It answers section 3 of `doc/backlog/HANDOFF_P3_SELECTION_RESUME.md`. One page, rewritten in place, never amended.

## Decision needed
Keep PREREG_P3_7 section 13, where the fee penalty is adopted only after R36 (about 2029-10), or amend it so that the penalty follows the structural route of `PKG_L` rule R7 (about 2027-05)?
**Recommendation:** amend it, but first run the read-only fee-coverage check (next steps, item 1).

## Two facts that narrow the question
1. **The band already has its own route.** Amendment 5 of `PREREG_FND-0193` lets the owner rule at the 6-row review of the hysteresis shadow (about 2027-05) on its deterministic rules 1 and 3. Arm A1 (band 0.10) passes them on the PIT history (`doc/audit/P3_7_TRIALS_REPORT.md`). Section 13 makes the band alone the candidate only if R6 rule 3 fails. So the wait to about 2029-10 applies only to what the penalty adds on top of the band. **Confirmed by the owner 2026-11-26:** Amendment 5 replaces the 2026-11-15 line of `doc/especificacion/PLAN_SELECCION_CALIDAD.md` that made the band wait for the FND-0299 forward holdout.
2. **Waiting does not test the penalty.** R24 and R36 compare AC with the live arm L only. That gap is mostly band plus fee arithmetic: +2.96 pp a year in-sample, against a minimum detectable effect (MDE) of 2.38 pp at 36 months. AC cannot be separated from the band alone within the horizon: MDE 1.72 pp at 36 months against a gain of about 1.2 pp (section 13, feasibility table). Three years of waiting therefore never measure the penalty's selection effect.

## What is at stake (PIT history: survivor funds only, today's fees; an upper bound)
| Arm | Net | Gross | Turnover / month | Max drawdown at stated fees |
|---|---|---|---|---|
| L (live, baseline) | 0.08 % | 4.69 % | 32.2 % | -26.4 % |
| A1 (band 0.10) | 1.92 % | 4.15 % | 14.8 % | -15.7 % |
| AC (band 0.10 + penalty, H 0.58) | 3.12 % | 4.12 % | 13.4 % | -13.4 % |

The penalty adds about 1.2 pp a year net over the band and costs about 0.03 pp of gross return. Amending brings adoption about 2.4 years forward: about 3 pp of net return in total at most, because a KIID entry fee is a maximum.

## Options
**Option 1: keep section 13.** Adoption needs R6 (about 2027-05) and R36 (about 2029-10). The band can still go live earlier through Amendment 5. Cost: the penalty's increment is forgone for about 2.4 years, for a test that cannot isolate the penalty (fact 2).

**Option 2: amend section 13 (recommended).** The penalty is adopted on all of:
- the R6 cost rules 1-3;
- the PIT evidence across the cost grid (already met: at a flat 25 bp every arm nets about the same, 3.55-3.71 %);
- a fee-coverage condition, defined from item 1 of the next steps;
- the owner's order.

R12 stays an alarm. R24 and R36 stay as checks after adoption: a failure reverts. The revert trigger of Amendment 5 applies: 12 consecutive builds; forward net return more than 0.50 pp a year below L, or drawdown more than 2.0 pp deeper, or a realised turnover reduction below half the PIT one. This protects against the failure R36 can detect (a forward gross-return loss of about 3 pp) about two years sooner.

**Counter-argument (it stands).** The penalty changes which funds are bought, so its effect on return is not arithmetic. The in-sample gross cost of 0.03 pp is a floor, because the panel holds no dead funds and flatters the arms that hold funds longer (FND-0436). Neither option measures that effect within the horizon. Option 2 accepts the risk now, covered by a revert trigger. Option 1 accepts it later, with no better test.

## Precondition of option 2: fee coverage (step 1 DONE 2026-11-26, read-only; no shadow comparison field read)
**Coverage.** "570 of 1,432" mixes two bases. The shadow counts unknown fees over the whole scored frame (2,901 funds) and candidates over (fund, sub-portfolio) rows: 1,432 rows, 942 distinct funds. Both counts were reproduced exactly, read-only, on the live metrics (regime Shock_Energetico). Of the 942 candidate funds:
- 202 (21.4 %) lack a point value on at least one side;
- 180 of those are CONDITIONAL: the document states a ceiling (entry ceiling p10 / p50 / p90 = 1.6 / 4.0 / 5.0 %);
- only **22 (2.3 %) have no figure at all** (NOT_FOUND).

Known candidates: entry 0 for 50.7 %, median entry 0, p90 5 %; exit 0 for 96.5 %.

**How each path treats an unknown fee.** The three paths do not agree:

| Path | Unknown fee becomes | Effect |
|---|---|---|
| Ranking penalty (live, shadow and PIT; `l4_penalty` inside each nature cell of the scored frame) | median (entry + exit) of the known fees of the cell: 0 % Monetario, 1 % RF corto plazo, 3 % RF flexible, 4 % RV / Mixtos / Estructurado, 4.5 % Alternativo | Close to the declared ceilings: 79 of 143 entry-conditional candidates have a ceiling above the fill, mean gap +0.47 pp. Unknown-fee funds are NOT favoured; against the known candidates (median 0) they are penalised |
| PIT cost of P3-7 (`fee_cost_series`, all arms and pools) | one global fill: entry 3 %, exit 0 (median of the ever-eligible funds) | The PIT weights are not stored, so the exact effect needs a rebuild (an order). Estimate at the pool's 21 % unknown share and a 1 pp gap to the median ceiling: costs understated by about 0.34 pp a year for AC, 0.37 for A1 and 0.8 for L. That understates the gain against L and is neutral against A1 (about 0.03 pp) |
| Stored `rotation_cost_estimate` of both shadows (`portfolio_builder.estimate_rotation_cost`) | entry → `rotation_costs` (0 for every nature), so 0; exit → nature row or 0.5 %; plus a 0.1 % spread; paired rotations only | Optimistic. Section 13 already excludes it: R6 recomputes the cost from the weights |
| R6 reader (not built) | **not specified by section 13** | The gap Amendment 2 must close |

**Threshold (assistant's proposal, derived, NOT the owner's decision).** AC buys about 13.4 % of capital a month, about 1.6 times capital a year. A share u of bought notional costed at 0 while it really pays 5 % biases the cost by u × 8.0 pp a year. Keeping that bias under the 0.50 pp materiality of R6 rules 2 and 3 needs u ≤ 6.2 %. Proposal:
- at least **95 % of AC's bought notional** in the review window has a point value or a declared ceiling on both sides, so the bias stays at most 0.40 pp;
- below that, R6 is NOT INFORMATIVE and carried to the next review, never PASS.

Today the pool is at 97.7 %.

**What Amendment 2 would need (not drafted, by order):**
1. The R6 cost fill: the point value; else the declared ceiling (D1: the KIID maximum is what he pays); else (NOT_FOUND) the cell's p90.
2. R6 rules 1-3 must PASS under that fill, with an optimistic run (NOT_FOUND at 0) reported next to it.
3. The same fill applies to L, B and AC.
4. The coverage condition above.
5. The ranking penalty's own fill stays as it is: it defines the AC arm, and changing it would break the shadow stream.

## Timing
Any amendment must be committed before the first review of the AC shadow, while no turnover, cost or selection-difference field has been read (R8, R9). On 2026-11-26 none has been read.

## Next steps (each on the owner's order)
1. DONE: the read-only fee-coverage check above. Open: the owner sets the threshold (95 % proposed) and the fill rule. Optional, with an order: rebuild the PIT weights of L, A1 and AC to measure the unknown share AC actually bought and re-cost them under the proposed fill (a run).
2. Option 2, after the owner's values: the text of Amendment 2 of PREREG_P3_7, committed before the first review.
3. Either way: the adoption or waiting plan, including what must hold before a promotion (FND-0443 and FND-0444 stay parked until then).
