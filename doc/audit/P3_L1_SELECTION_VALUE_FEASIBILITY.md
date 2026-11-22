# P3 layer 1 (selection value): feasibility note, 2026-11-22

Package L, step 3 of `doc/backlog/HANDOFF_P3_AFTER_AC_SHADOW.md`. Written BEFORE any layer-1 study is run. Read-only; no production change. Script `scripts/audit/p3/p3_l1_feasibility.py` (tests `proyecto3/tests/test_p3_l1_feasibility.py`).

## Decision needed
Layer 1 cannot be tested at the 0.5 pp materiality floor, on the history or forward. Recommended: **park layer 1 on this evidence (no study, no candidate) and go to layer 2 (P3-6 geographic caps, FND-0437)**. Alternatives are in section 4.

## 1. Question and estimand
Does the P3 ranking add gross return over a matched random pool of the same eligible funds? Estimand: the mean monthly GROSS return of the champion (T0 baseline, band 0.10, equal weights) minus the mean of the draws of the matched random pool, annualised as 12 x the monthly mean, in pp a year. Gross, because layer 1 is the value of the SELECTION; the cost of trading it is layer 0 (P3-7, done). Floor: 0.5 pp a year (owner, 2026-11-21). Test: one-sided, level 0.05, power 0.80 (the rule of P3-5A and Amendment 1 of P3-7).

## 2. What was computed (standard errors only)
Paired monthly series, 261 usable months (2005-01 ..), the four matched pools of the P3-7 run `out/reports/p3_p37_trials/20261121_p37` (20 draws each: persistent at band 0 / 0.10 / 0.20, and redraw). Stationary block bootstrap of the mean, mean block 6 months, 4,000 resamples, seed 12345 (`pit_p35a_prep.paired_se_annual_pp`, which never returns the point estimate).

| Pool | SE of the mean difference (pp a year) | SE against ONE draw (median) | MDE at 0.05 (pp a year) | MDE / floor |
|---|---|---|---|---|
| persistent, band 0 | 0.462 | 0.522 | 1.15 | 2.30 |
| persistent, band 0.10 | 0.501 | 0.534 | 1.25 | 2.49 |
| persistent, band 0.20 | 0.520 | 0.546 | 1.29 | 2.59 |
| redraw | 0.520 | 0.553 | 1.29 | 2.58 |

Worst case: SE 0.52 pp. The floor needs SE at most **0.201 pp**, that is **1,746 months (146 years)** of the same quality. Forward, at the same monthly variance (SE x sqrt(261 / T)): MDE **6.03 / 4.26 / 3.48 / 2.70 / 1.91 pp** at 12 / 24 / 36 / 60 / 120 months. Holm over 11 or 8 trials would raise the MDE further (1.5-1.8 pp on the history).

## 3. Reading
- The history can detect an effect of about 1.2-1.3 pp, 2.3-2.6 times the floor. By the rule already signed in P3-5A (MDE above the floor, so the historical test is non-inferiority and the forward shadow decides efficacy) a historical IMPROVEMENT test is not available for layer 1.
- The forward shadow cannot decide either: 2.7 pp is the MDE at five years, inside the FND-0299 horizon (2028-10, 2029-10) it is about 3.5 pp (36 months). The handoff rule applies: a study the forward shadow cannot cheaply confirm waits.
- There is no candidate to test: the P3-5A levers found nothing at this layer (L1 and L3 closed, L2 parked in FND-0437), and the one existing selection estimate (FND-0429 M2, CONTINUE, thin) has already used the data a new history test would read. Reading the same months again as a confirmation would be circular (R9).
- The figure is a lower bound on the difficulty: the sealed panel has survivors only (FND-0436), which flatters both the champion and the pool.

## 4. Options (owner's decision)
1. **Park layer 1 (recommended).** Record "untestable at 0.5 pp, no candidate"; do not spend a study. Go to layer 2 (FND-0437): its Brinson evidence (geography explains 52.8 % of the Equilibrada top gap) is a construction-layer question with a candidate and a preregistrable rule.
2. **Lower the floor** to about 1.3 pp so the history could just detect it. Not recommended: the floor would be chosen after seeing the standard errors (the same objection as in P3-5A), and a floor that high ignores effects that matter against a 2.5 pp gap to the target.
3. **Non-inferiority only** of a specific selection change (lower bound above -0.10 pp). It needs a named candidate change first; none exists.

## 5. Disclosure (R9)
Computed and printed: standard errors, counts, MDEs, months needed. Not computed: the mean or any point estimate of champion minus pool. Incidentally seen while listing the stored files: the header and the first monthly row (2005-01) of `T0_baseline/series.csv`, which carries that month's gross and net returns of the champion and the net return of the pool; one month, no aggregate, not used. The earlier readings of this layer (champion minus pool gross +0.85 pp, -0.04..+1.77; FND-0429 M2) predate this note and are not repeated here.
