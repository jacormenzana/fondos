# Decision note: adoption route of the fee penalty of AC (PREREG_P3_7 section 13)

**Status: OPEN, waiting for the owner's ruling. Written 2026-11-26. Read-only work: no code, study, switch or live write. No turnover, cost or selection-difference field of the AC shadow rows was read (PREREG_P3_7 section 13).** It answers section 3 of `doc/backlog/HANDOFF_P3_SELECTION_RESUME.md`. One page, rewritten in place, never amended.

## Decision needed
Keep PREREG_P3_7 section 13, where the fee penalty is adopted only after R36 (about 2029-10), or amend it so that the penalty follows the structural route of `PKG_L` rule R7 (about 2027-05)?
**Recommendation:** amend it, but first run the read-only fee-coverage check (next steps, item 1).

## Two facts that narrow the question
1. **The band already has its own route.** Amendment 5 of `PREREG_FND-0193` lets the owner rule at the 6-row review of the hysteresis shadow (about 2027-05) on its deterministic rules 1 and 3. Arm A1 (band 0.10) passes them on the PIT history (`doc/audit/P3_7_TRIALS_REPORT.md`). Section 13 makes the band alone the candidate only if R6 rule 3 fails. So the wait to about 2029-10 applies only to what the penalty adds on top of the band. **To confirm:** `doc/especificacion/PLAN_SELECCION_CALIDAD.md` (2026-11-15) still says that enabling the band waits for the FND-0299 forward holdout. Does Amendment 5 (2026-11-21) replace that line?
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

## Precondition of option 2: fee coverage
- In `proyecto3/src/ranking_levers.py::l4_penalty` a fund whose entry or exit fee is unknown takes the MEDIAN penalty of its cell, counted and not silent.
- Entry fees are bimodal (45 % of the eligible funds charge 0, median 2.0 %, 90th percentile 5 %: `PKG_L` section 1). So a high-fee fund with a missing fee is favoured, and a zero-fee fund with a missing fee is penalised.
- In the first forward shadow row (2026-11), 570 of the 1,432 candidates have an unknown fee.
- Not checked yet: how the PIT costing and the shadow `cost_fn` charge an unknown fee. If an unknown fee is charged as zero, part of the "deterministic" cost saving is an artefact.

## Timing
Any amendment must be committed before the first review of the AC shadow, while no turnover, cost or selection-difference field has been read (R8, R9). On 2026-11-26 none has been read.

## Next steps (each on the owner's order)
1. Read-only fee-coverage check: how an unknown fee is costed in the PIT and in the shadow, why 570 candidates lack one, and whether the funds AC buys are among them. It gives the value of the coverage condition.
2. If option 2: the text of Amendment 2 of PREREG_P3_7, committed before the first review.
3. Either way: the adoption or waiting plan, including what must hold before a promotion (FND-0443 and FND-0444 stay parked until then).
