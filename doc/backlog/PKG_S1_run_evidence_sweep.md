# Package S1 - backlog burn-down on the evidence of the 11-14 / 11-15 cycles

Owner request 2026-11-15: use the two cycles of the last 24 h (P1P2P3 `20261114_010938` and `20261114_235742`, RC 0, ended 11-15 03:59) to move the DEFERRED / TODO / OPEN tickets whose status the new evidence settles; P3-rooted tickets are out of scope (0158, 0198, 0206, 0221, 0222, 0257, 0299, 0304, 0306, 0409). Not production: speed over ceremony. Plan: `C:\Users\jacor\.claude\plans\pasted-content-id-f8c6-objective-over-jiggly-honey.md` (five reviewed amendments). One record, one closing commit, one full receipt. No P1 / P2 / P3 process was run.

## 1. Result (read first)
- **Closed by event (3):** FND-0089 and FND-0021 as REJECTED (owner sign-off 2026-11-15), FND-0329 as DUPLICATED of FND-0396.
- **Closed by the autopilot on the closing commit (5):** FND-0419, 0250, 0245, 0327, 0328 (`Closes:`; each has a `tests_pass` condition and a receipt on the closing sha).
- **Opened (2):** FND-0422 (four funds whose `Fund_Nature` contradicts both benchmark sources; MEDIUM, not worked) and FND-0423 (the post-cycle probe sweep, the manual-load flag of section 6; MEDIUM, not built). Both under the session `FND_20261115_ANL_FREEZE` (a finding opened and not worked).
- **Open backlog:** 69 before; 66 now; 63 once the autopilot has closed the five and the two new tickets exist (the floor of the plan). The sweep found no further closable ticket on recorded evidence.
- **Governance:** the cycle report's "residual accepted under a CLOSED ticket" line is gone (section 4). Two collision pairs (0245 / 0250, 0250 / 0327) were artefacts of the commit gate and are decided NONE.

## 2. Evidence for each closing
| Ticket | Cluster | Evidence (read-only) | How it closed |
|---|---|---|---|
| FND-0419 | H | the InFailedSqlTransaction exists only in `out/pkg_h/sample_run.log`; absent from both cycles' P1 logs and the universe pass `log_pipeline_20261115_000258`; fixed by `a5f8236` (a DQ code per side: `_ENTRY` / `_EXIT`) | `Closes:` |
| FND-0250 | A | WINDOW_FISHER_IDENTITY and WINDOW_NOMINAL_IDENTITY last fired in `post_20261104_103643_p2`; 0 in the 12 P2 audit runs since (6 of them after the 11-12 methodology release); the 6 funds are `In_Current_Universe=0` (NAV quarantine `e48cb37`) and carry only a `SNAPSHOT_HELD_OUT` row; no P2 audit of the 12 reports the two identity rules | `Closes:`; its acceptance (b) is revoked after the closing (section 4) |
| FND-0245 | G | `[GLOBAL_NORM] rows_affected=3770` twice in `log_pipeline_20261115_000258` (NATURE_FIRST and FAMILY_REFRESH: two run modes, not a duplicate flow) | fixed `1b24cd3`: rows_affected is now the rows really changed |
| FND-0327 | C | the cycle console showed no P2 outcome (`processed=122 skipped=3621` only inside the P2 log) | fixed `b99cec8`: one line and a `p2_outcome` key in the cycle report |
| FND-0328 | G | NORM-002 12,238 raw rows (37,509 raw - 34,206 DB rows = 3,303 duplicates dropped by `ON CONFLICT`); NORM-003 AIMA 2,957, CARA 74, FMDB 5,904 | fixed `1a3d9fc`: known classes, NORM-004 watches them |
| FND-0089 | G | `ingestion_log` step BENCH_MS: 0 events ever; the 11-15 refresh printed "Placeholders: 0 (rejected_placeholder: 0)" | REJECTED, cites FND-0088 (no change needed) |
| FND-0021 | C | NAV quarantine live (180 d); 7 funds lag, the oldest 2026-09-30 | REJECTED, cites FND-0233 |
| FND-0329 | A | WINDOW_NOMINAL_IDENTITY 0, SHARPE_BOUND 0, PATHOLOGICAL_SHAPE 0, DOMINANT_VALUE_CONCENTRATION 0, SORTINO_BOUND 1 at population level in `post_20261115_003144_p2` | DUPLICATED of FND-0396 (outlier model, legacy identity checks) |

**A correction to my own first evidence.** FND-0250: I first read `control.audit_finding.isin`, which is NULL for population findings, and concluded "0 findings". The per-fund detail is `control.audit_finding_isin`: the 6 funds carry one `SNAPSHOT_HELD_OUT` row in every run, and the probe `audit_isins_clear` (any persisted row) fails for them and always will. The claim itself (the two identity rules stopped firing) holds on the rule-level evidence above, so 0250 keeps `tests_pass` as its only condition and the reason is written on the condition.

## 3. Sweep of the rest (time-boxed: one query per ticket with an identified claim; a query that failed twice would have been left)
| Ticket | Cluster | Now | Verdict |
|---|---|---|---|
| FND-0410 pair conflicts | H | entry 6, exit 1 in the universe (8 and 1 on 11-14) | unchanged in kind; moved by the live pass |
| FND-0412 SRRI status | H | the same 2 rows (FR0013296332 TEXT_ONLY with both numbers 1; LU2098886703 NOT_AVAILABLE with visual 5); mix MATCH 3,448 / VISUAL_ONLY 188 / CONFLICT 95 / NOT_AVAILABLE 22 / TEXT_ONLY 17 | unchanged |
| FND-0413 ACI_RHP vs schedule | H | 104 funds (> 0.05) | unchanged |
| FND-0414 lifecycle | H | 17 funds OK with a retired latest period (11 in the universe), 2 retired periods without `retire_scope` | unchanged |
| FND-0415 alerts without value | H | ALARM 295 of 708, WARN 432 of 1,133, OK 6,533 of 16,540 | unchanged (ruling due 11-29) |
| FND-0416 performance fee | H | NONE with a NULL pct 43, NONE with pct > 0: 1, basis NULL with pct > 0 in the universe: 5 (7 on 11-14) | unchanged in kind |
| FND-0417 distribution frequency | H | 289 universe funds | unchanged |
| FND-0398 Leverage_Used | G | No 2,237 / Yes 460 / Limited 246 | unchanged |
| FND-0326 NAV re-fetch | C | 64 NAV rows written in the last cycle; 27 funds with last NAV > 180 d in `nav_sources` | unchanged |
| FND-0180 / 0182 | G | `bronze.series_benchmark` 0 rows; no `inception_date` column | unchanged (dated 2027-02-10) |
| FND-0281 / 0282 | B | ACCEPTED to 2027-01-15; the audit shows them as INFO | unchanged (owner order 11-14) |
| the rest (tooling, governance, P2 / P3 design, owner actions) | - | no testable claim in the run evidence | `needs-detail`, not investigated in S1 |

Candidate for the next batch (not closed here): FND-0232, a docstring that overstates the FRED key (a text fix, no run needed).

## 4. Waivers
- **(a) FND-0034 line.** The plan was a standing re-grant. The evidence made it unnecessary: `OC_NOT_CONTAMINATED` has 0 findings in the 5 latest cost audits (`post_20261113_222000` to `post_20261115_001418`). The 88 rows of the time-limited grant #1 (2026-10-06, to 2027-01-04) were moot, so event #36 revoked them with cause RESOLVED (within what the owner authorized: a subset of grant + approve + revoke). The standing grant #33 (26 funds) is untouched. Read-only check afterwards: `attention_lines(cost_attributes)` returns 0 lines.
- **(b) FND-0250 acceptance** (6 funds, `WINDOW_FISHER_IDENTITY`, to 2027-01-09): revoked with cause RESOLVED only after the ticket is CLOSED (a revoke before could re-open a HARD ALARM if the rule fired again). Result is added in the follow-up docs commit.

## 5. Backlog events (agent `agent:claude-sonnet-5-5`, session `FND_20261115_SYNC_S1RUNEVIDENCE`)
Moves to IN_PROGRESS (status events 744-748) before any commit that cites them (the autopilot moves only IN_PROGRESS / READY_FOR_DEPLOY); manual conditions of 0089 and 0021 retired (81, 82) with the owner's words; terminal moves 749-751; `tests_pass` conditions 95-99; collision decisions 46 and 47 (NONE); tickets FND-0422 and FND-0423 opened. FND-0422 carries the four-signal evidence: both Janus Horizon Biotechnology funds (LU2441282899, LU2441283277; stored Renta Fija Flexible) have KIID and Morningstar benchmarks that say Equity, SRRI 5 and a realised srri_nav of 6, so ARB-1 should have moved them; the abrdn EM bond fund (LU0376989207) and Ethna-DEFENSIV (LU0279509144), stored Renta Variable, have only the Morningstar signal against them; the root cause is not determined (ARB-1 not re-arbitrating a persisted nature, the family builder carrying one nature over the four classes of FAM_001170, or the KIID vote), and it needs the old-vs-new universe replay first.

## 6. Manual-load flag (Directive D-0) and what was not done
- **[CARGA MANUAL] the evidence sweep.** An agent re-measures the open tickets against each cycle by hand. Cause: 38 of 53 tickets had no machine-checkable closing condition at S0, and the autopilot only evaluates IN_PROGRESS / READY_FOR_DEPLOY, so a DEFERRED / OPEN ticket whose cause disappeared never closes by itself. Automated alternative: a post-cycle probe sweep in the cycle report that lists the open tickets whose closing conditions all pass, plus a rule that a data ticket opened without a machine-checkable condition says why. Registered as a ticket, not built here.
- **Not done in S1 (stated, not hidden):** a full sweep of every in-scope ticket (only those with an identified claim were measured); the full drift reading of the audits (only the headline numbers: P2 audit 6,596 ALARM of which OUTLIER_MAD_Z 1,538, costs OUTLIER_MAD_Z 817); FND-0236 (HIGH, IN_PROGRESS, outside the listed statuses) is NOT closed: the run shows metrics rows on the composite version `20261112.20261019` (3,707) beside `20261112` (3,700), `PRE_V26_UNKNOWN` (190) and `20261004` (74), and a P2 pass of `processed=122 skipped=3621`, which may be the demonstration the ticket lacks; the owner decides.

## 7. After the closing commit (filled by the follow-up docs commit)
Closing sha, receipt result, `--verify`, the autopilot dry run, the waiver (b) outcome, the N1 evidence. Not filled yet.
