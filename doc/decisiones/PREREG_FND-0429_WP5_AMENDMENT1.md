# AMENDMENT 1 to PREREG - FND-0429 / WP5 (selection level as the primary test)

**STATUS: SIGNED OFF 2026-11-17** (the owner locked the value of section 0 in the working session; the other [ADOPTED] rules were proposals he did not override). Committed BEFORE any M1 or M2 point estimate of the real scores exists. Written 2026-11-17 in response to the WP5.2 result (`doc/decisiones/RESULT_FND-0429_WP5_2.md`). Tags: **[LOCKED]** a value the owner confirmed by name; **[ADOPTED]** a rule of the draft the owner did not override; **[F]** a fact read from a run; **[DERIVED]** arithmetic on stated numbers.

---
## 0. DECISION BLOCK - the one value the owner locked (2026-11-17)
| # | Parameter | Plan / prereg | Proposal | LOCK (yours) |
|---|---|---|---|---|
| 1 | **`MIN_MEANINGFUL_SELECTION_SPREAD`**: the smallest top-quintile-minus-peer-median spread (outcome units: 12-month return per unit of trailing volatility) worth detecting. The WP5.2 stop rule compares the minimum detectable spread with it. | none (the prereg locked 0.10 for the Sharpe difference only) | No value was proposed by the drafter: the choice is a policy judgement. | **0.10 [LOCKED]** (owner, 2026-11-17: power about 86 %, above the 80 % minimum; it mirrors the 0.10 of the Sharpe delta rather than the bare 0.091 floor) |

**What each candidate means [DERIVED from the measured SE 0.0366 of the real-score top-quintile mean, one-sided alpha 0.05; power = normal CDF of spread / SE minus 1.645]:**
| Candidate spread | Power to detect it | Testable under the stop rule (minimum detectable spread 0.091 <= candidate)? |
|---|---|---|
| 0.05 | about 39 % | no |
| 0.075 | about 66 % | no |
| **0.091** | **80 % (by definition)** | borderline |
| 0.10 (same number as the Sharpe delta, units differ) | about 86 % | yes |
| 0.15 | about 99 % | yes |

**Caution recorded with the lock.** The candidate decides only "testable or not"; it cannot change the CONTINUE / PIVOT rules, which stay as signed. But a threshold chosen after seeing that the minimum detectable spread is 0.091 can be influenced by it. This is disclosed in section 4; the table above is what the owner chose from, and a value below 0.091 would have kept the question untestable. With 0.10 the minimum detectable spread (0.091) is inside the threshold: the question is TESTABLE under the stop rule.

---
## 1. What the amendment changes (and nothing else)
1. **Primary test.** M2 at 12 months (selection level) is the only decisive metric, as the signed prereg already makes it for CONTINUE and PIVOT. The WP5.2 stop rule is applied to it: stop (untestable) if the minimum detectable selection spread exceeds `MIN_MEANINGFUL_SELECTION_SPREAD`. [ADOPTED]
2. **M1 (portfolio Sharpe difference)** is reported, never decisive. Its WP5.2 result stays on record: minimum detectable difference 0.233 against the locked 0.10, so it is not testable on this history; the verdict may not cite it as evidence for or against. Where the signed prereg says "if M1 is at most zero the verdict adds CONSTRUCTION-LOSS", the flag is kept as a DESCRIPTIVE note only. [ADOPTED]
3. **WP5.1 controls.** The diagnostic run of 2026-11-17 (`out/reports/wp5/20261117_210318`, same data, code and seed as any re-run would use) is adopted as the WP5.1 result, no re-run: permuted labels 3 of 1,000 PASS (rate 0.003, Wilson 95 % upper limit 0.009, rule rate <= 0.05 MET); planted alpha 497 of 500 detected at an outcome shift 0.182 (detection 0.994, target 0.80 MET). [ADOPTED]
4. **Unchanged:** alpha 0.05 one-sided, attrition 5 % with bounds 2.5 % and 10 %, `MIN_MEANINGFUL_DELTA_SHARPE` 0.10 (kept for M1 reporting), units, peer cells, the 2 pp drawdown margin, the outcome rules, the inference settings, the survivorship rule, the trial counter.

## 2. Facts the amendment rests on [F]
- WP5.2: 251 formation months (about 20.9 independent 12-month windows), 208,142 unit-months, 1,204 families; SE of the real-score top-quintile mean 0.0366 (date block 0.0366, family cluster 0.0280); minimum detectable selection spread 0.091 at 80 % power.
- WP5.1 diagnostic: the test is CONSERVATIVE (undersized): permuted-label PASS rate 0.003 against alpha 0.05, because the family-cluster bootstrap includes variation between families that random picks do not have (null SD of the estimate 0.0056, mean bootstrap SE 0.0161). The planted control (99.4 %) uses random, non-persistent picks, so it overstates the power for the real persistent scores; the 0.091 above, measured on the real-score SE, is the power statement that applies.

## 3. What stays NOT decided
The M2 estimate and the verdict (a further order after the lock and the commit of this amendment), enabling `PORTFOLIO_HYSTERESIS_ENABLED`, the vendor decision, the forward holdout (FND-0299) and the P3 refactor scope. The P3 refactor stays paused behind FND-0429.

## 4. Disclosure
This amendment is written AFTER the WP5.2 result and as a response to it; it is written BEFORE any M2 or M1 point estimate of the real scores exists (the harness is blind by construction: its real-score path calls only the standard-error routine, pinned by a test). The only post-hoc degree of freedom is the choice of `MIN_MEANINGFUL_SELECTION_SPREAD`, which the owner locked from the table in section 0. A reader who doubts that choice should know that 0.10 sits 0.009 above the measured minimum detectable spread, so the question is testable by a thin margin.

## 5. Sign-off
Locked by the owner on 2026-11-17: `MIN_MEANINGFUL_SELECTION_SPREAD` = 0.10. No override of any other proposal, which are therefore [ADOPTED].
Cleared to: commit this amendment with the diagnostic-flag change of the harness and the updated result page in one delivery (one full receipt), then run the M2 estimate and record the verdict. The P3 refactor stays paused behind FND-0429 until the verdict is recorded.
