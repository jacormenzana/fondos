# Backlog ranking for FND-0425 (2026-11-15) - ONE page, rewritten in place

Read-only analysis (skill `backlog-priorizer`). Nothing was closed, moved or edited by it. Facts are marked **[F]** (read from the backlog or the database today), inferences **[I]**, hypotheses **[H]**. Owner decisions are listed in section 6.

## 1. Executive verdict
- **Risk: LOW-MEDIUM. No open item is a live reliability or data-integrity failure [F]**: the two CRITICAL audit incidents (0281, 0282) are accepted residuals until 2027-01-15, and one PRODUCTION fund carries a statistical ALARM.
- **45 open items [F]**: 34 DEFERRED (each with a dated review), 5 IN_PROGRESS, 3 OPEN (one is FND-0425 itself), 2 BLOCKED, 1 TODO. Only about 10 are live work; the rest is parked by order. The 5 closures of today (0354, 0390, 0364, 0388, 0267) are already reflected.
- **7 clusters: 1 High (a decision, not a defect), 2 Medium, 4 Low.**
- **Most consequential systemic point:** the P3 selection / risk cluster (0158, 0257, 0222, 0304, 0299, 0206, 0306) is the only one that touches fund-selection decisions and the 15 % drawdown mandate, and it is exactly what the P3 refactor would absorb.
- **Decision required: confirm "P3 refactor first".** No other group is significant enough to outrank it. Then close the 4 candidates in section 6.

## 2. Evidence coverage
| Source | Scope | Retrieved | Limitations | Completeness |
|---|---|---|---|---|
| `gestion.backlog` (tickets) | all non-CLOSED, 2026-11-15 | 45 of 45 | none | complete |
| ticket logs | last non-system log of 36 tickets | partial by design | older history not read | partial |
| confirmed links | between open items | 8 CONFIRMED, 3 PROPOSED | the 3 PROPOSED were not read | complete for CONFIRMED |
| `fund_metric_alerts` | 2026-07-31 .. 2026-11-15, aggregates only | 18,376 rows, 3,706 funds | **no ticket reference column**: alert to ticket correlation is not possible except through the ticket text; only two detection batches (2026-08-14, 2026-11-15) | complete (aggregates), no causal reading |
| receipts / cycle | this session's receipts | 4 receipts | small sample | indicative |

## 3. Prioritised clusters
| Rank | Cluster / suspected root cause | Priority | Items | Alert evidence | Cascading impact | Confidence |
|---|---|---|---|---|---|---|
| 1 | **P3 selection and risk policy still open**: hysteresis shadow (live OFF), FX budget per sub-portfolio, Safe Mode, stress scenarios, forward holdout, baseline notes, promotion-gate lineage | **High (decision)** | 0158, 0257, 0222, 0304, 0299, 0206, 0306 | 1 PRODUCTION fund with ALARM [F]; alerts are category-percentile flags, no corroboration | every one is decided inside the P3 code the refactor rewrites | High |
| 2 | **Delivery cost: full receipts for any shared/ or proyecto*/ change** (selector in SHADOW, cascading PG tests) | Medium | 0385, 0387, 0400 | none (not a data issue) | each package pays one 506-602 s receipt [F]; delays every other cluster | High |
| 3 | **Manual and un-scheduled operations (D-0)** | Medium | 0100, 0248, 0326, 0090, 0239 | none | 0078 DEPENDS_ON 0100 [F]; 0248 is `[CARGA MANUAL]` | Medium |
| 4 | **Cost and policy attribute residuals** (Package H / A tails, accepted residuals) | Low | 0384, 0410-0414, 0416-0418, 0421, 0424, 0281, 0282, 0415 | 295 ALARM rows have no value [F]; equals the 2026-08-14 batch count [I] | none recorded on PRODUCTION or DRAFT | Medium |
| 5 | **Classification semantics** (derived, not discovered; legacy P1 rules) | Low | 0397, 0398, 0402, 0033, 0035, 0324, 0325, 0185 | none | 0397: IE00008TZ2E8 held at 5.5 % in PRODUCTION, owner NO GO unchanged [F] | Medium |
| 6 | **Data provenance and coverage** | Low | 0167, 0182, 0209, 0078, 0389 | none | 0167: a bad Morningstar payload drops the fund, fail closed [F] | Low |
| 7 | **Blocked by population** | Low | 0029 | none | 5 WRONG_DOC funds; out of scope by standing rule | High |
| - | Unclustered | - | 0180, 0425 | - | 0180: empty unused table; 0425 is this task | - |

## 4. Dependency map
- **Explicit [F] (CONFIRMED links):** 0078 DEPENDS_ON 0100; 0033 OVERLAPS 0035; 0257 OVERLAPS 0158; 0325 RELATED 0257; 0239 RELATED 0167; 0239 RELATED 0222; 0402 RELATED 0397; 0402 RELATED 0398.
- **From ticket text [F]:** 0400 was found by the mutation evidence of 0385 and 0387 is its symbol-level follow-up; 0306 waits for an event (a promotion attempt with a commit between build and audit); 0281 and 0282 stay open while their residual acceptance is active.
- **Inferred [I]:** 0222 and 0304 are P3 roadmap items that depend on the shape of the refactor, not on each other.

## 5. Roadmap
| Sequence | Priority | Action | Items | Dependencies | Exit criteria | Residual risk |
|---|---|---|---|---|---|---|
| 0 | - | Owner closes the 4 candidates (section 6) | 0033, 0035, 0180, 0206 | owner | count 45 to 41 | none |
| 1 | High | Define the P3 refactor scope and absorb cluster 1 into it; keep the `shadow_after_build` call | 0158, 0257, 0222, 0304 (0299 stays a dated tracker; 0306 waits its event) | owner scope | ONE refactor ticket replaces four; shadow rows still written | scope creep |
| 2 | Medium | Earn the selector's narrower receipt (bounded change under 3 min), then close | 0385, 0387; 0400 cascade subclass stays open | a bounded change | one measured receipt, then close | full receipts stay the default until then |
| 3 | Medium | Schedule the daily NAV load and harvest steps 1-3 when the owner lifts "not now" | 0100 (then 0078) | owner | task registered, one clean run | operator cost until then |
| 4 | Low | Keep dated deferrals; review 2026-11-29 (0411, 0415, 0248) and 2026-12-15 (Package H residuals) | cluster 4, 6 | review dates | decide each on its date | none |

## 6. Decisions and unresolved questions
1. **P3 refactor first?** Recommended yes: the only cluster tied to selection and drawdown decisions, and it absorbs four tickets.
2. **Close 4 candidates (owner console, forced REJECTED; an agent cannot).** Each needs a one-line owner OK:
   - **0033 and 0035** (P1 v22 / v24 legacy items, OVERLAPS each other, revisit dates are the `DEFAULT_90D` placeholder, no recorded impact).
   - **0180** (the table is empty and unused; its own text says the `db/pg/10_bronze.sql` comment already documents it).
   - **0206** (interpretation notes with no deliverable, per its own deferral log).
3. **0078 (short-window tactical metrics): confirm or drop.** Its spec is a draft "needing the owner's confirmation" (2026-09-25) and it DEPENDS_ON 0100.
4. **Missing evidence:** an alert to ticket reference would let the next ranking correlate alerts with tickets instead of reading text.
5. **Not touched:** 0281 and 0282 (CRITICAL residuals, accepted to 2027-01-15), 0299 (a tracker to 2028-09-01).
