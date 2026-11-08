# P2 audit run `audit_20261107T220005Z`: is "0 incidents" right?

> Revision 4 (2026-11-08). The text of revision 1 is kept verbatim below. Changes from the owner's assessment and the
> facts verified on 11-08 are in **Amendment A1**. The owner's second assessment is in **Amendment A2**. **Amendment A3**
> (self-critique and gap analysis) holds the **canonical execution plan (A3.4)**. It supersedes the sequences A1.4 and A2.4.
> The item specifications in A1/A2 still apply where A3 does not change them. Precedence: A3 > A2 > A1 > revision 1.

## Context
The owner ran `P1P2_auditStatistical.bat p2 --persist`. It produced 41,032 findings, then `audit_incident.py --plan --domain p2` said
0 incidents would open. The owner asked whether this hides incidents that should open. Sources: the log
`proyecto2/log/log_AUDIT_p2_20261107_230004.log`, `shared/statistical_audit/criticality.py`, `incidents.py`, `catalog_invariants.py`,
and read-only aggregate queries on `control.audit_finding` / `audit_run`. Nothing was written.

## 1. Executive summary
- **The zero is correct under the current rules.** No finding is a defect class: 0 HARD_INVARIANT, 0 BLOCK2 identity and
  0 TIMESERIES_DUPLICATE findings.
- **It does not mean the data is clean.** One fund carries an almost certainly corrupt value. About 20 more show extreme deviations
  that would be data defects in any reasonable model.
- **No rule can escalate them.** Outliers, shape and concentration findings are information by design. Size, recurrence and how many
  metrics one fund breaks are never considered.
- **Verdict:** the system under-detects incidents. That is a gap in the model, not a malfunction.

## 2. Key findings
| Signal | Value | Level today |
|---|---|---|
| Outliers (BLOCK4) | 40,871 of 41,032 findings (99.6%): 33,640 WARN + 7,231 ALARM on 1,744 ISINs | MINOR |
| Shape / concentration (BLOCK3/1) | 82 / 14 findings | MINOR |
| Sortino above 10 | 19 rows, 11 ISINs | MAJOR (review list) |
| Hard invariants, identities, duplicates | 0 | CRITICAL would open |
| Detector history | WINDOW_FISHER_IDENTITY fired in 22 headed runs (10-12 to 11-04). It was correctly excluded as accepted residual FND-0250 (6 ISINs, until 2027-01-09). It has been absent since 11-06, cause not verified. | — |
| Coverage loss | 60 ISINs left out of the cross-sectional checks (data up to 1,713 days old). 9 checks skipped. 7,840 findings suppressed as "already alerted". | not signalled |

The detector works: it has fired before. This run simply had no defect-class finding.

## 3. Potential missed incidents
1. **DE000DWS3LN1** (equity fund, in the universe). Its 1-year momentum is 22.64, i.e. +2,264%, about 411 robust deviations from the
   median. It has 58 alarms across 10 metrics: volatility, Sortino, SRRI volatility and the z-score categories. That points to a
   corrupt NAV series (scale or unit break) feeding P3 scoring. **This should be an incident.**
2. **21 ISINs with deviations of 50 or more** (58 alarms). Examples: LU0232465467 (367), ES0125756025 (219), LU1663960000 (199),
   LU2330520862 (168). At that size an "outlier" is almost always a data defect. **These are incident candidates.**
3. **63 ISINs with 20 or more alarms each** (1,710 alarms). 35 are money-market funds, where near-zero volatility inflates ratios:
   structural, not a defect. **The other 28 need review.**
4. **60 ISINs out of audit coverage**, with data up to about 4.7 years old. Their metrics are served without these checks. Not
   verified: whether the 180-day NAV quarantine removes them from the universe.

## 4. Threshold and rule assessment
- **The defect rules are well calibrated.** The CRITICAL set (impossible values, broken identities) is sound: keep it.
- **The outlier ALARM threshold is too permissive to act on.** A deviation of about 5 already counts as an ALARM, giving 7,231 alarms
  on 60% of the universe. The baseline mixes all fund types, so money-market structure drowns the real signals.
- **The Sortino bound** (absolute value below 10) is a sensible MAJOR. But a MAJOR has no escalation path, whatever it does in later
  runs.
- **Blind spots:** 9 skipped checks, from zero spread or too small a sample. In the freshness metric, 97.9% of funds share one value,
  so both outlier tests were skipped there. Its kurtosis of 1,638 still shows a stale tail that nothing flags.
- **Suppressed alerts:** 7,840 findings are suppressed because `fund_metric_alerts` already shows them. Not verified: whether those
  alerts ever reach the backlog. If they don't, they vanish from both channels.

## 5. Design or configuration weaknesses
1. Severity depends on the finding's class only, never on how big it is. A deviation of 411 ranks the same as one of 5.
2. Nothing adds up the alarms per fund. A fund broken across 10 or more metrics is never seen as one problem.
3. MAJOR and MINOR findings never escalate, even when they repeat run after run.
4. The outlier baselines are not split by fund type. The result is 41,000 findings with 0 actionable.
5. Lost coverage (left-out ISINs, skipped checks, suppressed findings) is not itself a risk signal.
6. No automated P2 caller runs `--open` or `--certify`, unlike the cost recompute flow. `[CARGA MANUAL]`
7. The older identity findings (SHARPE_EQUALS_SORTINO, CAPTURE_UP_EQUALS_DOWN, SCALAR_EQUALS_TIMESERIES_*) exist only in runs with no
   run header, so they could never be planned.

## 6. Recommended improvements
- **New escalation: suspected data corruption.** A deviation of 50 or more (to be calibrated on past runs), or a plausibility bound
  such as 1-year momentum above 500% for a non-leveraged fund, raises the finding to CRITICAL or opens an incident. The incident is
  keyed by rule and fund.
- **New category: fund data integrity.** One incident per fund that has alarms in 8 or more distinct metrics, money-market funds
  excluded.
- **New category: audit coverage gap.** It fires when left-out ISINs, maximum staleness, skipped checks or suppressed findings pass a
  threshold.
- **MAJOR escalation on recurrence.** The same MAJOR key in 3 or more consecutive FULL runs opens an incident. The `recurrence`
  policy already exists.
- **Split outlier baselines by fund type.** This also cuts noise.
- **New KPIs for each run:** actionable ratio (incidents ÷ findings), extreme-deviation count, ALARMs per 1,000 funds, audit coverage %.
- **Shadow first.** New rules write only to the `--plan` shadow log until their precision is measured. That is how decision D7 and the
  writer proof work.

## 7. Priority actions
- **High**
  - H1: find the root cause for DE000DWS3LN1 and the 21 ISINs with deviations of 50 or more (NAV series / P2 input).
  - H2: build the suspected-corruption escalation in shadow mode.
  - H3: check whether `fund_metric_alerts` reaches the backlog.
- **Medium**
  - M1: fund-data-integrity rule.
  - M2: coverage-gap signal (60 left out, 9 skipped).
  - M3: confirm why the FND-0250 residual is gone (fixed, or the funds left the universe through the quarantine?).
  - M4: automated P2 open/certify caller.
- **Low**
  - L1: split outlier baselines by fund type.
  - L2: MAJOR recurrence escalation.
  - L3: KPI panel.

## 8. Final verdict
"0 incident(s) would open" is **correct under the current rules but not credible as a statement of data health**. At least 1 near-certain
defect (DE000DWS3LN1) and about 20 likely ones cannot become incidents by design. **The system under-detects incidents.**

## Next step after approval (no code change in this step)
1. Run `scripts/audit/backlog_links.py --new "<text>"` for H1, H2, H3 and M1-M4, to avoid duplicates. Check FND-0250 and the recent
   FND-0385..0389.
2. Open the tickets that are missing with `shared.backlog_client.open_ticket()` (`FONDOS_ACTOR=agent:claude-opus-5-5`). Each gets a
   closing condition (`add_close_condition`).
3. Don't build anything yet. Each ticket is started separately, after its dependency scan.

## Verification
- Run again the read-only queries behind the figures above (counts by block, class and severity; deviations of 50 or more; alarms
  per ISIN), on run `audit_20261107T220005Z`.
- After each ticket is opened, `backlog_links.py --for FND-nnnn` shows its relations.

---

# Amendment A1 (2026-11-08): owner assessment + facts verified today

## A1.1 Facts verified on 11-08 (read-only: code and git, no DB query)
| # | Fact | Source | Effect on the plan |
|---|---|---|---|
| F1 | FND-0383 shipped **after** the audit run: `validate_nav_stale_flat` (12 identical monthly NAVs, then a move above 40%) quarantines the fund (`nav_sources.data_status=INVALID_NAV`, metrics cleared). The same commit removes stale macro betas when the OLS has no fit. The audit run was 11-07 23:00; the commit is 11-08 09:40. | commit `4ccb9d9`, `proyecto2/AGENTS.md` | Some of the 22 extreme ISINs may already be explained and quarantined on the next P2 run. LU0232465467 (#2, deviation 367) is the "stuck betas" fund of FND-0383. H1 must re-measure first and investigate only what is left. |
| F2 | A new probe kind `audit_isins_clear [domain, isin...]` passes when the latest finished audit of the domain has no finding on those funds. | commit `972b0b1` (FND-0384) | The H1 tickets close on their own (D-0): closing condition = `audit_isins_clear p2_metrics <isins>`. Use it only if its DDL is live; otherwise `tests_pass`. |
| F3 | P3 selection reads only hard defects. The build excludes stale NAV, sparse NAV, accepted residuals and WRONG_DOC (`FUND_DATA_ELIGIBILITY_ENABLED`, `fund_scorer.load_eligibility_inputs`). Promotion blocks a hard defect on a scenario fund (`D-DATA-SCENARIO`, predicate in `shared/statistical_audit/gating.py`). `OUTLIER_MAD_Z` is listed and **never blocks**. | `shared/governance/controls.py:155-188`, `proyecto3/AGENTS.md` | **A fund with a corrupt NAV like DE000DWS3LN1 can be scored, selected and promoted today.** Detection alone does not protect selection. New action N2. |
| F4 | `fund_metric_alerts` has **no route to the backlog**. Its readers are the cycle report and the promotion gate `D-ALARM-WATCH` (only `DD_CAT_P03` / `VOL_CAT_P97`, only funds already in PRODUCTION, only "no weight increase"). The only P2 `report_incident` is the quarantine count (FND-0164). The audit suppresses by key (isin, metric) against those alerts (`shared/statistical_audit/reconcile.py`). | grep of `proyecto2/src`, `shared/` | H3 is answered in part: a suppressed finding on any other rule, or on a fund outside PRODUCTION, reaches **no gate and no backlog**. It disappears from both channels. |
| F5 | Scenario-level coverage is already enforced: `D-COVERAGE` blocks promotion when a scenario fund was left out of the audit. | `controls.py:161` | M2 is universe hygiene, not a selection risk: it stays in Phase 2 as a run-header KPI with a threshold, not one incident per run. |
| F6 | The NAV quarantine of the universe (`reconcile_universe_membership`, owner directive) is dated **11-04**, the same day WINDOW_FISHER_IDENTITY last fired. | root `AGENTS.md` | M3 has a likely explanation: the 6 FND-0250 ISINs left the universe. Not verified until the query in M3 runs. |

## A1.2 Disposition of each assessment point
| Assessment point | Plan item | Disposition | Change |
|---|---|---|---|
| 1. Investigate extreme outliers (immediate) | H1 | **Kept, modified** | Step 0: check exposure (N1). Step 1: after the next FULL P2 run on code ≥ `4ccb9d9`, re-run the audit and count again the ISINs with deviations of 50 or more. Quarantined ones are explained by FND-0383. Step 2: find the root cause only for the rest. Each confirmed defect class gets a **generic** guard in `validators.validate_nav` (P#2, P#7, like FND-0383), never a value patch. Closing condition per F2. |
| 2. Audit the alert pipeline (immediate) | H3 | **Kept, rescoped** | F4 answers the question. No new backlog writer fed by alerts: it would need a writer proof (FND-0370) and would duplicate H2/M1. Fix instead: suppression only removes duplicate display. A suppressed finding still counts for escalation (H2) and for the per-fund count (M1). Test: a suppressed finding with a deviation of 50 or more still escalates. |
| 3. Magnitude escalation, shadow first | H2 | **Kept, extended** | Two tiers. (a) Plausibility bound per `Fund_Nature` (for example `momentum_1y` > 5.0 outside `Renta Variable`, with a wider equity bound). It uses no leverage flag unless a generic attribute for leverage exists (P#5): to check at start. (b) Robust deviation ≥ T, with T calibrated on the persisted runs. The precision is measured against the H1 outcomes as labels. Kill switch `AUDIT_CORRUPTION_ESCALATION_ENABLED` (OFF → SHADOW → ON), listed in `NORMAS_IMPLEMENTACION.md` §8. Once ON, the finding feeds the selection (N2). |
| 4. Fund-level integrity (Phase 1) | M1 | **Promoted to Phase 1, modified** | Count distinct metrics **including suppressed findings** (F4). Money-market funds are excluded by `Fund_Nature='Monetario'` (generic). Start at 8 metrics, calibrated in shadow. One incident per (rule, ISIN). |
| 5. FND-0250 anomaly (Phase 2) | M3 | **Kept** | First test the F6 hypothesis: for the 6 ISINs, `In_Current_Universe`, `nav_sources.data_status` and last NAV date. If they left by quarantine, the residual stays accepted until 2027-01-09; it is not "fixed". Record the evidence in the FND-0250 log. |
| 6. Coverage gap alarms (Phase 2) | M2 | **Kept, rescoped** (F5) | Run-header KPIs (left-out count, maximum staleness, skipped checks, suppressed count) with thresholds in `shared/config.py`. An incident opens only when a threshold is breached in 2 consecutive FULL runs. The freshness-metric tail (kurtosis 1,638) gets a plausibility bound on the age in days, because the outlier tests are skipped there. |
| 7. Split baselines + P2 caller (Phase 3) | L1, M4 | **Split into two** | **M4a → Phase 1:** the P2 cycle launcher runs `audit_incident.py --plan --domain p2` after each FULL audit and keeps the shadow log. Without it the shadow precision of H2/M1 is never measured: it is their prerequisite (D-0, removes the `[CARGA MANUAL]`). **M4b → Phase 3:** `--open` / `--certify` only after H2/M1 reach ON. L1 stays in Phase 3. It is measured as a fall in ALARMs per 1,000 funds, with the extreme-deviation recall unchanged. |
| 8. Recurrence escalation (Phase 3) | L2 | **Kept** | Uses the existing `recurrence` policy. Key = rule + ISIN + metric, 3 consecutive FULL runs. Shadow first, like H2. |

## A1.3 New action points (not in the assessment)
- **N1 — Exposure check (immediate, read-only, part of H1).** Are any of the 22 extreme ISINs, or the 28 non-money-market funds with
  20 or more alarms, in the PRODUCTION scenario or the newest DRAFT (`portfolio_weights`)? A hit is reported to the owner the same day:
  the D-ALARM-WATCH gate does not cover it (F3, F4).
- **N2 — Selection link (with H2/M1, the main reliability gain for fund selection).** When the switch is ON, a "suspected corruption" or
  "fund integrity" finding becomes a reason of ineligibility at build time: `exclusion_reason` `data_suspect: <rule> (<metric>, dev <x>)`
  in `fund_scorer.load_eligibility_inputs` / `check_hard_filters`, the same path as accepted residuals (FND-0251). The builder never
  selects the fund. The promotion predicate in `gating.py` treats it as a hard defect for `D-DATA-SCENARIO` (with the existing reasoned
  override `--accept-alarms ... --ap`). Add a control row `D-DATA-SUSPECT` to `shared/governance/controls.py` and to
  `GOBIERNO_CONTROLES.md`. Before ON: a read-only diff of the eligible set with the switch ON and OFF, built like
  `scripts/audit/eligibility_switch_diff.py`. PIT rule: the backtest does **not** apply it (today's finding would be look-ahead, as with
  residuals); the run manifest says so.
- **N3 — Automatic promotion from shadow to enforce.** H2, M1 and L2 move SHADOW → ON on their own record, as FND-0385 does. Each needs
  n ≥ 3 FULL runs, precision ≥ 0.8 against the H1 labels, and 0 misses among the confirmed defects. The rule computes this from the
  M4a shadow log. Nobody approves a step (D-0).
- **N4 — KPI panel (L3) folded into the run header.** Actionable ratio, extreme-deviation count, ALARMs per 1,000 funds, coverage %
  and shadow precision per new rule. All are written to `control.audit_run` so N3 and M2 read one place. It is not a separate dashboard.

## A1.4 Revised sequence
| Phase | Items | Gate to the next phase |
|---|---|---|
| 0 — now, read-only | N1, H1 step 0-1 (after the next FULL P2 run), M3 hypothesis query | Exposure known; residue of extreme ISINs counted |
| 1 | H1 step 2 (root causes → `validate_nav` guards), H2 SHADOW, M1 SHADOW, H3 fix of suppression, M4a, N4 | ≥ 3 shadow FULL runs logged |
| 2 | N3 promotion → ON, N2 selection link, M2, M3 record | Eligible-set diff reviewed in the commit, gates green |
| 3 | M4b `--open/--certify`, L1 baselines, L2 recurrence | — |

## A1.5 Tickets (unchanged procedure, extended scope)
- Before opening: `backlog_links.py --new "<text>"` for H1, H2+N2, H3, M1, M2, M3, M4a/b, L1, L2, N3. Check overlap with
  **FND-0383** (shares LU0232465467 and the NAV guard), FND-0384 (`audit_isins_clear`), FND-0250, FND-0251 (eligibility), FND-0289
  (alarm watch), FND-0164 (quarantine incident), FND-0385 (shadow→enforce pattern).
- H1 may be absorbed into FND-0383 under the Scope Absorption Protocol if `backlog_links` confirms the overlap.
- Each ticket gets a closing condition when opened: H1 → `audit_isins_clear`, the others → `tests_pass`.
- Still no code in this step.

## A1.6 Verification (in addition to revision 1)
- N1: one read-only query of `portfolio_weights` joined to the ISIN list (≤ 50 rows), PRODUCTION and newest DRAFT.
- H1: on the first FULL audit after `4ccb9d9`, count ISINs with deviations of 50 or more and `INVALID_NAV` among them.
- H2/M1/N3: unit tests on synthetic findings (magnitude tiers, suppressed findings still counted, money-market exclusion). Hermetic PG
  test of the shadow log through `scripts/ops/run_pg_tests.py`. Sample runs only on the 40-ISIN sample.
- N2: tests for `check_hard_filters` with `data_suspect`, the gating predicate, and that PIT does not read it. The ON/OFF eligible-set
  diff on the 40-ISIN sample.
- After each commit: policy scanners, then `delivery_attest.py --scope full`.

---

# Amendment A2 (2026-11-08): owner's second assessment

## A2.1 Facts verified for A2 (read-only: code and docs)
| # | Fact | Source | Effect |
|---|---|---|---|
| G1 | A leverage attribute **exists**: `fund_master.Leverage_Used` (`Yes` / `No` / `Limited`). P1 extracts it from the KIID text, and the INTER-7 / SC-F4 rule (leverage with a conservative profile) already uses it. Its coverage and precision today are **unknown**: the last recorded figure (legacy doc) was about 65%. NULL means "not discovered" (P#10). | `db/pg/20_silver.sql:49`, `classify_utils.py:5894`, `MODELO_SEMANTICO.md` §SC-F4 | Directive 1 is a measurement of an existing attribute, not new engineering. A new P1 attribute is a fallback only. |
| G2 | The detector `WINDOW_FISHER_IDENTITY` already has a unit test on a known violation: the FND-0114 reproduction fixture must trip it (`test_fnd0114_reproduction_fails_fisher_identity`). | `proyecto1/tests/test_statistical_audit_timeseries.py:200-222` | The code is already proven on synthetic data. Nothing proves that the **live run** still feeds it data. Directive 3 is met with a live-path check (A2.3 item 3), not only another unit test. |
| G3 | Stale NAV already makes a fund ineligible at build time (`P3_FUND_NAV_MAX_AGE_DAYS`, F3). | `proyecto3/AGENTS.md` | The ignored freshness tail (kurtosis 1,638) is an audit blind spot, not a selection leak. Directive 4 can stay in Phase 3. |

## A2.2 Disposition of the four directives
| Directive | Item | Disposition |
|---|---|---|
| 1. Leverage prerequisite (H2, Phase 1) | H2a (new, Phase 1, before any plausibility bound) | **Accepted** (A2.3 item 1) |
| 2. Sync M4b with N3 (Phase 2) | M4b | **Accepted.** M4b moves from Phase 3 to Phase 2 and ships in the same change as N3 (A2.3 item 2). |
| 3. Validate the FND-0250 detector directly (M3) | M3 + new D1 | **Accepted, generalised** to every hard detector (A2.3 item 3) |
| 4. Concentrated/sparse data model (Phase 3) | L1 + new L4 | **Accepted** (A2.3 item 4). The interim age bound of M2 stays until L4 is ON. |

## A2.3 Changes
1. **H2a — Leverage gate before the plausibility tier (Phase 1).**
   - *Measure (read-only, ≤ 50 rows of output):* coverage of `Leverage_Used` by `Fund_Nature`. Precision on two generic cross-checks:
     (a) name signals for leverage (`2x`, `3x`, `Leveraged`, `Daily Leverage`, `Ultra`, `Short` / `Inverse`, with lookaheads per R-5);
     (b) a realised beta to its peers above 1.5 (`peer_panel.py`). Cross-check on the 40-ISIN sample and on every one of the 22 extreme
     ISINs.
   - *Gate:* the tier ships only if `Leverage_Used` covers ≥ 90% of `Renta Variable` / `Mixtos` and disagrees with (a)/(b) on ≤ 5%.
     A fund with `Leverage_Used` NULL or `Yes`/`Limited`, or with a name signal, gets **no** plausibility bound. Unknown never
     triggers.
   - *Failure path:* the plausibility tier stays OFF (`AUDIT_CORRUPTION_PLAUSIBILITY_ENABLED = False`, separate from the deviation
     tier). H2 escalates on the calibrated deviation threshold T alone, as directed. A ticket for P1 `Leverage_Used` coverage is opened
     (fix in the P1 classifier, P#7, R-2: classifier → INTER rule → FORCE_REFRESH). No new attribute is invented while the existing
     one can be fixed.
   - *Leverage-free corruption signal (added to H2, Phase 1, shadow):* a **one-month NAV jump that its peers do not share**. The fund's
     monthly return is more than k times its own trailing volatility, while the median of its `Fund_Nature` × category peers that same
     month is not. A leveraged fund moves with its index, and so do its peers; a corrupt NAV moves alone. The peer-panel data
     (FND-0255) is reused. This signal needs no leverage flag. It is the main defence against false positives, and its precision is
     measured in shadow like the other rules.
2. **M4b sync (Phase 2).** The P2 cycle launcher gets `--open --apply` and `--certify` for the rules **whose mode is ON**, read from the
   database at run time. When N3 turns a rule ON, its next FULL run opens its incidents with nobody's step. A rule in SHADOW keeps
   writing only to the `--plan` log. Prerequisites to verify when M4 starts:
   - Is `audit_incident.py` a grandfathered writer in `shared/backlog_writers.py`? If it is not, or a new `system:` identity is
     needed, `writer_proof.py` must PASS first (FND-0370).
   - The live loop switch `AUDIT_INCIDENT_LOOP_ENABLED` is already `True`.
   - The idempotency key (rule + ISIN), so a rerun opens nothing twice.
   - A cap on incidents per run, with an alert when it is reached. A flood after a flip is stopped by the cap, not by a person.
3. **D1 — Detector liveness canaries (Phase 1, generic, replaces "trust the quarantine" in M3).**
   - *Phase 0 (with M3):* read the persisted applicability of `WINDOW_FISHER_IDENTITY` per run from 11-01 to today: funds tested,
     violations, accepted. Look it up first in `control.audit_finding` / `audit_run`, else in the run logs with Grep. If the number of
     funds tested stays stable and the 6 FND-0250 ISINs are out of the universe → explained. If it fell to 0 or dropped sharply on
     11-06 → the detector lost its input: open a ticket at HIGH.
   - *Phase 1:* every FULL run evaluates each hard detector (`HARD_INVARIANT`, BLOCK2 identities, `TIMESERIES_DUPLICATE`) on its
     known-bad fixture inside the same process, through the same code path. The existing test fixtures are reused, and the canary rows
     are never persisted. The result goes into the `control.audit_run` header (`canaries_ok`, `canaries_failed`). A failed canary marks
     the run **INCOMPLETE**. Under the completeness contract (FND-0261) such a run is never promotion evidence, so a silently broken
     detector blocks promotion by itself.
   - A rule whose applicable count falls by more than 50% against the median of its last 5 FULL runs raises a coverage-gap finding
     (it extends M2).
   - Tests: a canary that does not fire → INCOMPLETE; the canaries never reach `audit_finding`.
4. **L4 — Concentrated-distribution outlier model (Phase 3, with L1).** Today, when the MAD or IQR is 0 (here 97.9% of funds share one
   value), both outlier tests are skipped. Replacement, to be designed and tested in shadow:
   - When the modal share ≥ 50%, measure each value's distance from the mode in units of the spread of the **non-modal** values
     (their IQR, or MAD).
   - Also apply a one-sided upper-quantile rule (above P99.5 and above mode + a floor in the metric's units).
   - A tail whose kurtosis exceeds a configured limit without any flagged fund raises a "tail not explained" finding.
   - A skipped test is never silent: it is recorded with its reason (M2).
   - Acceptance: on the persisted runs, the freshness-metric tail is flagged, and the false-positive rate on the money-market and
     equity baselines (L1) does not rise. Switch `AUDIT_CONCENTRATED_OUTLIER_ENABLED`, OFF → SHADOW → ON via N3.

## A2.4 Revised sequence (replaces A1.4)
| Phase | Items | Gate to the next phase |
|---|---|---|
| 0 — now, read-only | N1 exposure, H1 steps 0-1, M3 + D1 applicability history, H2a coverage/precision measurement | Exposure known; residue of extreme ISINs counted; Fisher input confirmed or ticketed; leverage gate decided |
| 1 | H1 step 2 (`validate_nav` guards), H2 deviation tier + peer-unshared-jump signal SHADOW, H2 plausibility tier SHADOW **only if** the H2a gate passes, M1 SHADOW, H3 suppression fix, D1 canaries, M4a, N4 | ≥ 3 shadow FULL runs logged; canaries green |
| 2 | N3 promotion → ON **together with** M4b (`--open/--certify` per ON rule, cap + idempotency), N2 selection link, M2 (with the D1 applicability-drop rule), M3 record | Eligible-set diff reviewed in the commit; writer proof PASS if needed |
| 3 | L1 baselines + L4 concentrated model, L2 recurrence | — |

## A2.5 Tickets (adds to A1.5)
- Before opening: `backlog_links.py --new` also for H2a, D1 and L4. Check overlap with the P1 classification tickets on `Leverage_Used`,
  FND-0255 (peer panel), FND-0261 (completeness contract) and FND-0370 (writer proof).
- The P1 `Leverage_Used` coverage ticket opens only if the H2a gate fails.

## A2.6 Verification (adds to A1.6)
- H2a: one aggregate query (coverage by `Fund_Nature`, ≤ 20 rows) and one ≤ 50-row agreement sample. Both results are recorded in the
  H2 ticket log before the gate is decided.
- Peer-unshared-jump: unit tests in which a leveraged fund moving with its peers does **not** fire, and an isolated corrupt jump does.
- D1: unit tests for a canary that fires, a canary that does not fire (→ INCOMPLETE), and canary rows never persisted. One hermetic PG
  run through `run_pg_tests.py`.
- M4b: hermetic PG test. A rule in SHADOW opens nothing, a rule in ON opens once, a rerun opens nothing, and the cap stops a flood and
  alerts.
- L4: replay on the persisted runs, read-only, before any switch.

---

# Amendment A3 (2026-11-08): self-critique, gap analysis, canonical execution plan

## A3.1 Rating
- **Revision 3 (rev 1 + A1 + A2): 6/10.** The 8.5 I gave at A2 was too high. The plan diagnoses the problem well, but it is a
  detection-first design. Its sequence has errors, its success criteria are statistically weak, and its form is hard to execute.
- **The canonical plan A3.4: about 8/10, provided the Phase 0 premises hold.** The remaining ceiling comes from things only data can
  settle: the thresholds and the label volume.

## A3.2 Gap analysis
Impact / likelihood: H = high, M = medium, L = low.

| # | Issue | Impact | Likelihood | Root cause | Corrective action |
|---|---|---|---|---|---|
| G-01 | **The selection protection depends on audit timing.** N2 reads findings from the latest audit. The P3 build may score metrics newer than that audit (`D-AUDIT-STALE` only requires the audit to be newer than the DRAFT). So a fund can be excluded for metrics already repaired, or missed because its new metrics are corrupt. | H | M | Detection and protection were designed as one pipeline (audit → findings → P3) instead of one shared rule. | **One deterministic predicate module** (`shared/data_suspect.py`) computes the suspicion signals from `fund_metrics` / `fund_nav_monthly` directly. The audit (incidents), P3 eligibility (build), the cycle report and PIT all call it. No dependency on when the audit ran (P#11). |
| G-02 | **PIT diverges from production.** A2 excludes N2 from the PIT backtest (look-ahead). The backtest would then evaluate a model that production does not run, which breaks the rule that PIT evaluates the production model. | H | H | A finding stored today was treated as the only form of the signal. | With G-01's predicate, PIT computes the signal **at each date from the data observable then**, like `shared/nav_continuity.py`. No look-ahead, same model. Only the exemption registry (G-08) stays today-only, and the manifest says so. |
| G-03 | **Calibration before denoising.** The deviation threshold T is calibrated in Phase 1 on mixed baselines. The baseline split (L1) is in Phase 3. A money-market fund inflates z-scores, so T would have to be recalibrated and N3's shadow evidence would be thrown away. | H | H | Sequence ordered by how easy each item looks, not by what depends on what. | **Move the per-`Fund_Nature` baseline (L1) to WS2, before any calibration.** The noise is the root cause of the classification "outliers never escalate". Fix it first. |
| G-04 | **The precision gate is statistically weak.** n ≥ 3 runs over the same about 22 funds gives the same labels three times, not three samples. A point precision of 0.8 on about 22 labels has a 95% interval of about 0.6–0.93. | H | H | The "n ≥ 3" habit from the token-savings rule was carried over to another kind of problem. | Count **distinct labelled (rule, ISIN) keys**: at least 30 per rule, with a Wilson lower bound ≥ 0.7. Label each key automatically from its outcome: later quarantined by `validate_nav`, NAV revised by the source, or a NAV that persists consistent with peers = genuine. A person labels only the leftovers, as a one-off event. |
| G-05 | **Recall is circular.** "0 misses among confirmed defects" only counts defects the rules already found. Nothing measures what the system misses. | H | H | No independent source of ground truth. | **Fault-injection harness (WS1).** Inject generic corruption classes into copies of real NAV series in a hermetic DB: ×100 scale break, unit shift, a year frozen then a jump, duplicated month, wrong currency step. Measure detection end to end: validator → metrics → predicate → incident → eligibility. A rule goes ON only if injected recall ≥ 0.95. The run re-executes on every change to the predicate or the validators. |
| G-06 | **Closing by quarantine counts as a fix.** `audit_isins_clear` passes when the audit finds nothing. A quarantined fund has its metrics cleared, so it produces no finding, and the H1 ticket closes without a repair. This is the same trap the owner raised about FND-0250. | M | H | The probe tests whether findings are absent, not whether the data is healthy. | H1 closing condition = `audit_isins_clear` **and** `nav_sources.data_status` valid for each ISIN. An ISIN that stays quarantined keeps the ticket open, or moves to an accepted residual with ticket + expiry. This needs a probe argument or a second probe: check the probe catalogue first, else a small extension of `rfd_matrix_probe.py`. |
| G-07 | **Too many switches, two sources of truth.** A2 adds `AUDIT_CORRUPTION_ESCALATION_ENABLED`, `…_PLAUSIBILITY_ENABLED`, `AUDIT_CONCENTRATED_OUTLIER_ENABLED`, and N3 also sets a mode per rule in the database. The config and the DB can disagree. | M | H | Each assessment point was answered with its own mechanism. | **One generic escalation engine.** Each rule is a config row: predicate, key, threshold, cap. One kill switch, `AUDIT_ESCALATION_ENGINE_ENABLED`. The mode per rule (OFF/SHADOW/ON) is a DB event, as with the autopilot. H2, M1, L2, M2 and L4 become rows, not separate code paths. |
| G-08 | **No way back from a false exclusion.** A genuine extreme performer (a gold-miners fund at +200%) flagged by N2 is excluded at build time. Build exclusion has no override, so the fund can never be selected. | M | M | The cost of a false positive was never set against the cost of a false negative. | An **exemption registry, "verified genuine" (ticket + expiry)**, using the accepted-residual machinery the other way round. It is recorded as an event and reviewed on expiry. Every exclusion is listed in the build output with its reason, so it stays visible. |
| G-09 | **The outcome on selection is never measured.** The plan counts incidents and precision but never asks the actual question: did corrupt data change which funds were selected, or their ranks? N1 is a one-off check. | H | M | The plan was framed around the audit, not around P3. | **Selection-impact KPIs** on every build, written to the scenario notes and the cycle report: selected funds touched by the predicate, how far they move in rank when their suspect metrics are neutralised, and the weight at risk. N1 becomes a standing check, not a one-off. |
| G-10 | **Unverified premises carry the plan.** Not checked: that `audit_finding` keeps per-ISIN deviation values for past runs (needed for calibration); that the `recurrence` policy exists as claimed; the writer status of `audit_incident.py`; whether peer-panel monthly returns are available; that applicability per rule is persisted (D1). | H | M | Each revision was written from code reading and memory, without confirming the premises. | **Phase 0 premise checklist with stop conditions.** A failed premise changes the plan before any code (A3.4 WS0). |
| G-11 | **Items dropped without a word.** The weakness §5.7 of revision 1 (old identity findings only in headerless runs) has no action item. The 11 ISINs with Sortino > 10 depend only on L2. | M | M | Each amendment answered the latest assessment, not the whole of the earlier text. | WS4: re-run the old identity checks under a headed FULL run, which makes them plannable. The Sortino MAJORs go through the engine's recurrence row. |
| G-12 | **Form: four layers and about 20 items.** An implementer has to merge rev 1 + A1 + A2 by precedence. About 15 tickets would add backlog overhead. | M | H | The additive-amendment rule was applied without a canonical summary. | A3.4 is the **single execution plan**. The amendments stay as history. **One epic and 5 tickets** (one per workstream), with the items as checklist entries. |
| G-13 | **The root-cause work is manual.** H1 forensics on 22 ISINs is one person reading NAV series, and repeats with every new extreme fund. `[CARGA MANUAL]` | M | H | No tool was planned. | A **generic per-ISIN NAV forensic report** (read-only script). For each ISIN: jumps against peers, frozen runs, scale ratios, source/date of the breaks, the guard that would catch it. It is generated by itself for every new key the engine flags in SHADOW/ON, so H1 becomes a review of a report. |
| G-14 | **The source is never watched.** Corruption from the vendor often hits many funds the same month. The plan works fund by fund. FND-0164 only counts quarantines per run. | M | M | Per-fund framing. | A predicate row "jump cluster": more than N funds from the same source with a jump not shared by peers in the same month → one incident per (source, month). Reuses G-01. |
| G-15 | **Effort and owner decisions are not stated.** No estimate, and no list of the points where the owner must act. | L | H | Not asked for, so not given. | A3.4 lists the owner events. The effort is given per workstream. |

## A3.3 What an optimal plan does differently (summary)
1. **Prevention before detection.** Validators at the input point plus one shared predicate. The audit becomes the second net and the
   health monitor, not the only defence.
2. **One rule, every consumer.** Audit, P3 build, promotion gate, PIT and cycle report call the same predicate. There is no timing gap
   and no PIT divergence.
3. **Denoise, then calibrate, then enforce.** Fund-type baselines first.
4. **Ground truth that does not come from the detector itself.** Fault injection for recall, outcome-derived labels with Wilson bounds
   for precision.
5. **One generic engine, modes in the DB.** Each new rule is a config row.
6. **Measure the business outcome.** Selection impact on every build.
7. **Every exclusion can be undone and is visible.** The verified-genuine exemption.

## A3.4 Canonical execution plan (supersedes A1.4 and A2.4)
| WS | Content (items absorbed) | Effort | Gate to the next step |
|---|---|---|---|
| **WS0: premises & exposure** (read-only, now) | N1 as a query of today's PRODUCTION + newest DRAFT, plus the rank of those funds. G-10 checklist: deviation magnitudes persisted per ISIN?; `recurrence` policy present?; `audit_incident.py` writer status; peer-panel monthly coverage; applicability per rule persisted? M3/D1 Fisher applicability history 11-01 → today. H2a `Leverage_Used` coverage/agreement. H1 step 1: residue of extreme ISINs on the first FULL audit after `4ccb9d9`. | ~1 session | Each premise PASS or a recorded change to the plan. Exposure reported. |
| **WS1: input integrity** | H1 (root causes → generic `validate_nav` guards), G-13 forensic report, G-05 fault-injection harness, G-06 closing condition, G-14 jump cluster (validator side). | 2–3 sessions | Injected recall of the validators measured. H1 residue explained. |
| **WS2: shared predicate + selection link** | G-01 `shared/data_suspect.py`: nature-split baselines (L1 moved here, G-03), deviation tier, peer-unshared jump, plausibility tier only if H2a passes, per-fund integrity incl. suppressed findings (M1, H3). N2 build eligibility + `D-DATA-SUSPECT` gate. G-02 PIT at each date. G-08 exemption registry. G-09 selection-impact KPIs. | 3–4 sessions | ON/OFF eligible-set diff and PIT ON/OFF on the 40-ISIN sample. All behind the kill switch, OFF. |
| **WS3: escalation engine** | G-07 generic engine (rules = rows: H2, M1, L2 recurrence incl. Sortino, G-14, M2). M4a shadow log on every FULL run. N3 promotion (≥ 30 distinct labelled keys, Wilson LB ≥ 0.7, injected recall ≥ 0.95) **together with** M4b open/certify per ON rule (cap, idempotency, writer proof if needed). | 2–3 sessions | Shadow evidence accrues by itself. Rules flip by themselves. |
| **WS4: audit self-health** | D1 canaries + applicability-drop rule (**built early, with WS1**: cheap, high value). M2 coverage KPIs. L4 concentrated model. G-11 old identity checks under a headed run. N4 run-header KPIs. | 2 sessions | Canaries green. L4 replay accepted. |

- **Order:** WS0 → (WS1 + D1) → WS2 → WS3 → rest of WS4. WS2 may start as soon as the WS0 premises pass; it does not wait for WS1
  to finish.
- **Tickets:** one epic plus 5 tickets (WS0–WS4). The items are checklist entries in each ticket's description. Run
  `backlog_links.py --new` for each, with the overlaps listed in A1.5 / A2.5. Closing conditions: WS1 per G-06, the others
  `tests_pass`.
- **Owner events (one-off, not recurring):** approve this plan. Read the WS0 exposure result (act only if a selected fund is hit).
  Review the ON/OFF eligible-set diff in the WS2 commit. Grant an exemption when a verified-genuine fund is excluded (G-08).
  Everything else runs by itself (D-0).
- **Still no code in this step.** WS0 is read-only and runs first after approval.

## A3.5 Root causes of the plan's own limitations (process)
- **Reactive revisions:** each assessment was answered by adding items, not by re-deriving the whole plan. → One canonical execution
  section per revision (this one), checked against every earlier item (G-11).
- **Detection bias:** the work began from an audit run, so the solution was shaped like an audit. → Frame plans from the consumer of
  the data (P3 selection) back to the source.
- **Premises taken from memory and code reading:** → a Phase 0 premise checklist is mandatory before thresholds or sequencing.
- **Statistical criteria borrowed from another context:** → state the unit of evidence (distinct keys, not runs) and the interval,
  not a point value.

---

# Amendment A4 (2026-11-08): owner approval of A3 with four directives

Status: **A3 is APPROVED by the owner, subject to the directives below.** Precedence: A4 > A3 > A2 > A1 > revision 1. The owner
ordered WS0 to run now, with the exposure and G-10 results reported before WS0.5 starts.

## A4.1 Disposition
| Directive | Disposition | Final form |
|---|---|---|
| 1. D1 out of WS4, hard gate of WS1 | **Accepted** | D1 (canaries + applicability-drop rule) is a WS1 deliverable. **WS2 does not start until the canaries are green on a FULL run.** WS4 = M2, L4, G-11, N4 only. |
| 2. Fault-injection infrastructure as its own WS0.5, before WS1 logic | **Accepted, with a smaller scope** | There is no separate DevOps team: the agent builds it. It **reuses** what exists instead of new provisioning: the throwaway container of `scripts/ops/run_pg_tests.py`, and the snapshot loader that `writer_proof.py` already uses for its throwaway-DB pass (FND-0370). Data: the **40-ISIN sample** (hard rule) plus the peer medians those funds need for the peer-unshared-jump signal, as a frozen fixture. The corruption classes of G-05 are generated in that DB. WS0.5 gate: the harness loads, injects each class, and runs validator → P2 metrics → predicate on the sample with no live access. It does not yet measure recall, because there is no predicate yet. |
| 3. Labelling failsafe: below 30 keys, go ON with a bootstrapped lower bound + alert | **Accepted in intent (no manual chokepoint), modified in consequence** | A bootstrap on fewer than 30 binomial labels does not narrow the uncertainty, and Wilson is already the right small-sample bound. Putting a low-precision rule ON for **selection** would exclude good funds without anyone noticing. So the failsafe is split by the cost of an error. (a) After 6 FULL shadow runs without 30 keys, the rule goes **ON for incidents** (the cost of an error is one incident), provided the Wilson lower bound on the keys it has is ≥ 0.5 **and** injected recall is ≥ 0.95. The run header and an automatic alert in the cycle report record "degraded confidence (n, LB)". (b) Its **selection** effect (N2) stays at "flag only": the fund is listed in the scenario notes with its reason and **may not gain weight** (the `D-ALARM-WATCH` pattern) until the full gate (≥ 30 keys, LB ≥ 0.7) passes by itself. No person labels anything, and nothing stalls. |
| 4. Exemption expiry guard | **Accepted** | It reuses the existing expiry-warning mechanism of accepted residuals (`AUDIT_RESIDUAL_EXPIRY_WARN_DAYS`, set to 7 for exemptions). The warning goes to the cycle report and the promotion output: the owner is the portfolio manager. At expiry with no review, the fund is in **GRACE** for exactly one P3 build: eligible, flagged `exemption_grace` in the scenario notes and the G-09 KPIs. It is excluded from the next build on. The GRACE state is derived from the dates (no stored state, nothing to forget). Tests: warned at T-7, eligible+flagged at T, excluded at T+1 build. |

## A4.2 Canonical sequence (replaces the A3.4 order line)
WS0 (read-only, now) → report to owner → **WS0.5** harness infrastructure → **WS1** input integrity + **D1 (hard gate: canaries green)**
→ **WS2** shared predicate + selection link → **WS3** engine + promotion (A4.1 #3 failsafe) + M4b → **WS4** M2, L4, G-11, N4.
Effort: WS0.5 ~2 sessions. WS1 drops to ~2 sessions because the harness moved out, plus D1 ~1 session.

## A4.3 WS0 runbook (read-only, executes after plan approval)
- Before anything: `preflight_check.py --mine` (another session's files and jobs are not mine). WSL keepalive
  (`wsl -d Ubuntu -- bash -c "sleep infinity"` in the background). Read `doc/reglas/NORMAS_SQL_TOKENS.md`. Read-only role, aggregates
  first, LIMIT ≤ 50, never a DSN printed.
- **Q-EXP (exposure):** the PRODUCTION scenario and the newest DRAFT, from `portfolio_scenarios` / `portfolio_weights`, crossed with:
  - the ISINs of run `audit_20261107T220005Z` with a robust deviation ≥ 50;
  - the non-money-market ISINs with ≥ 20 alarms;
  - for each hit: weight, sub-portfolio, rank in `fund_scores`, and whether it is eligible today.
- **G-10 premise checklist:**
  1. Does `control.audit_finding` keep a per-ISIN deviation magnitude for past FULL runs, and since which run? (`SCHEMA_REFERENCE.md`
     plus one aggregate.)
  2. Does a `recurrence` policy exist in `shared/statistical_audit/incidents.py`, and what does it do?
  3. Is `audit_incident.py` a grandfathered writer in `shared/backlog_writers.py`?
  4. Peer-panel monthly coverage for the 40-ISIN sample and the extreme ISINs.
  5. Is the applicability per rule (`n_applicable`) persisted per run, or only logged?
- **M3/D1:** the applicability history of `WINDOW_FISHER_IDENTITY` from 11-01 to today, plus the universe/NAV status of the 6
  FND-0250 ISINs.
- **H2a:** coverage of `Leverage_Used` by `Fund_Nature`, and agreement with the name signals (≤ 50-row sample).
- **H1 step 1:** if a FULL audit on code ≥ `4ccb9d9` exists, the residue of extreme ISINs and the `INVALID_NAV` among them. If none
  exists yet, report that, with the next run that will produce one.
- **Output:** one report to the owner with PASS / FAIL / CHANGE-TO-PLAN for each premise. Nothing is written to the DB, the backlog or
  the repo. The epic and its tickets are opened only after the report.

---

# Amendment A5 (2026-11-08): WS0 executed (read-only) — results and plan changes

Run: audit `audit_20261107T220005Z` (FULL, code `ee734f6`, 2,905 funds). Read-only session on the live DB, aggregates, no write. Full
ISIN sets (21 + 28, 46 distinct) in the session scratchpad `ws0_isin_sets.tsv`. Scripts `ws0_a.py` … `ws0_dump.py` in the same folder.

## A5.1 Exposure (Q-EXP / N1)
- **None of the 21 funds with |robust deviation| ≥ 50 is in PRODUCTION or the newest DRAFT.**
- **1 of the 28 non-money-market funds with ≥ 20 alarms is held: IE00008TZ2E8** (MAN DYNAMIC INC D EURHDG ACC, `Renta Fija Corto Plazo`).
  Weight 5.5% in Defensiva in **both** PRODUCTION `cartera_shock_energetico_202610_b20261025150959` and the newest DRAFT
  (`…202611_b2026110…`). Eligible, score 0.867, rank 15 of 300 in Defensiva.
  - 39 ALARMs in 11 metric groups, largest deviations 24 (sharpe_slope), 21 (return_ann_slope), 20 (sortino).
  - Its NAV is **not corrupt**: 38 monthly rows, annualised vol 3.4%, largest monthly move 3.5%, mean return +10.9% a year.
  - It looks like a peer-baseline mismatch against `Renta Fija Corto Plazo`. Whether its `Fund_Nature` is wrong is **not verified**
    (P#6: nature is never derived from performance). A sibling class, IE000WFG9PJ8, is in the extreme list (151), and a USD class exists.

## A5.2 G-10 premise checklist
| # | Premise | Result | Evidence | Change to the plan |
|---|---|---|---|---|
| 1 | Per-ISIN magnitude persisted for past runs | **PASS** (partial for IQR) | `OUTLIER_MAD_Z` keeps a **signed** `distance` in 40 runs since `audit_20260912T223525Z`. `OUTLIER_IQR` rows keep `value` only; the reference stats are in `audit_statistic`. | Calibrate T on `abs(distance)`. IQR magnitude is recomputed from `audit_statistic`, no DDL. |
| 2 | `recurrence` policy exists | **PASS, narrower than assumed** | `incidents.py` `POLICY_RECURRENCE`: CRITICAL keys only, seen in **the previous** full run (depth 2). | L2 needs a depth parameter (k runs) and a key class beyond CRITICAL. Small change to `incidents_to_open` (`previous_keys` → a list of runs). |
| 3 | `audit_incident.py` writer status | **CHANGE** | It reports through `backlog_client.report_incident` → `sp_report_incident`, under the process actor, not a `system:` identity. The writer registry does not apply. Dedup is 24 h per `object_name`; the incident table's live keys prevent repeats. There is no cap per run. | No writer proof needed for M4b as designed. **Cap per run and the idempotency test are still required.** Check at WS3 which actor the unattended launcher runs under (copy `p1_cost_recompute.py`). |
| 4 | Peer-panel monthly depth | **PASS** | In-universe funds per nature with a monthly NAV in each of the last 24 months: Renta Variable ≥ 1,601; Renta Fija Flexible ≥ 519; Mixtos ≥ 365; Corto Plazo ≥ 268; Monetario 59; Alternativo ≥ 42; Estructurado ≥ 15. 92–100% of funds have ≥ 36 rows. | The peer-unshared-jump signal is feasible by nature. `Estructurado` is thin (15): minimum peer count rule. The nature × category split is untested. |
| 5 | Applicability per rule persisted | **FAIL** | `_run_invariants` does `continue` when a rule has 0 violations: nothing is stored. `n_applicable` lives only in the evidence text of a violating finding. | D1 must **persist** `n_applicable` / `n_violating` per rule per run as `audit_statistic` rows (group_key = rule_id), no DDL. The applicability-drop rule only has history from that run on. |

## A5.3 M3 / D1: WINDOW_FISHER_IDENTITY
- Findings in every headed FULL run to `post_20261104_103643_p2` (INFO "ACCEPTED RESIDUAL (FND-0250): 6/6 ISINs; was 40/245"). None in
  `pre_20261106_211617_p2`, `post_20261106_225457_p2` and `audit_20261107T220005Z`.
- All 6 FND-0250 ISINs now have `in_current_universe = 0`, `data_status = STALE_FROZEN`, last NAV 2022-02-27/28. The audit universe fell from
  2,908 to 2,902 between those two dates.
- The check was **not skipped** in the headers of the last 3 runs (0 Fisher skips).
- **Verdict: consistent with "the only violators left the universe through the NAV quarantine" (F6), not proven.** The persisted data cannot
  show that the detector still evaluates live rows (premise 5). Liveness stays a WS1 gate (D1 canaries). The residual stays accepted until 2027-01-09.

## A5.4 H2a: `Leverage_Used`
- **The coverage gate is meaningless.** Coverage is **100%** in every nature (the legacy figure was ~65%), so the value is almost certainly
  imputed (derivation not audited in WS0). 460 funds `Yes` and 246 `Limited` out of 2,944 (24%), including a short-term bond fund (ES0125757023).
- A name-signal cross-check found **no truly leveraged fund** in the universe: the 11 hits are share-class suffixes ("X2") and ultra-short bond funds.
  The check is weak evidence, and the realised-beta cross-check was not run.
- **Gate result: FAIL.** The plausibility tier stays OFF, as A2.3 prescribes; escalation relies on the deviation threshold and the
  peer-unshared-jump signal. A low-priority ticket on `Leverage_Used` saturation/derivation is opened with WS1, not before.

## A5.5 H1 step 1 and what the extreme set looks like
- **No FULL audit exists on code ≥ `4ccb9d9`.** All 21 extreme funds are `data_status = OK` and in the universe. P2 recalculated 9 of them at 09:50 today
  and quarantined none. So FND-0383 removed none of them. They must be re-measured on the first FULL audit after the in-flight P2 run completes.
- **DE000DWS3LN1:** the +2,264% comes from **one month**: NAV 565 → 1,090 on 2026-08-31 (+92.9%), never reversed, 32 monthly rows. It is below the
  `validate_nav` 8x step and is not a 12-month freeze, so **no current guard catches this class** (a one-month level shift). Root cause of the
  jump itself (unadjusted merger/split, redenomination, vendor error) is not established.
- **Baseline pollution is visible:** 8 of the 21 are fixed income or money market (1 Monetario at 153, 4 Corto Plazo, 3 Flexible). That supports G-03.
  By nature: Renta Variable 7, Mixtos 6, Renta Fija Corto Plazo 4, Renta Fija Flexible 3, Monetario 1.
- **Suppression is already recorded:** the header `skips` of the newest run contains `ALERT_RECONCILIATION … 7840 findings already surfaced`, with 9 further skipped checks.

## A5.6 Plan changes
1. **G-05 harness corruption classes (WS0.5)** gain two: *one-month level shift of 50–150% (below the 8x step)* and *implausibly smooth NAV (vol collapse)*.
2. **WS1 validator candidate:** one-month return beyond a calibrated bound that the fund's nature peers did not share (generic, `validate_nav`; same predicate as H2's signal).
3. **WS2 predicate:** evaluate by **fund family** (FND-0246 family key), so share classes of one fund count once and a family-level pattern is visible.
4. **D1** persists applicability (premise 5); **L2** gets the depth parameter (premise 2); **M2/H3** read the suppression and the skips from the run header, no new store.
5. **G-08:** IE00008TZ2E8 is a live example of a held fund an M1-style rule would exclude on statistical grounds while its NAV is clean. The exemption path and the
   "flag, do not gain weight" mode (A4.1 #3b) are required before M1 can touch selection.
6. **WS0 follow-up for the owner, outside the plan:** review the `Fund_Nature` of the Man Dynamic Income family (3 classes, one held at 5.5%).

---

# Amendment A6 (2026-11-08): owner go on WS0.5 — tickets, build and corrections

## A6.1 Tickets (session FND_20261108_DEV_P2AUDITESCALATION)
Epic FND-0391. WS0.5 FND-0392 (closing condition `tests_pass`), WS1 FND-0393, WS2 FND-0394, WS3 FND-0395, WS4 FND-0396; side tickets FND-0397 (manual Fund_Nature review of the Man Dynamic Income family, no portfolio change),
FND-0398 (Leverage_Used saturated, LOW). Links to the epic and to the tickets named in each log were proposed (RELATED, PROPOSED) by `open_ticket`; no link was confirmed (a person decides links).

## A6.2 Corrections to A5
- **A5.3 (Fisher):** the "40/245" I quoted was my own truncation of the evidence text (`left(evidence, 70)`). The real text is "40/2466681 applicable rows violate across 6 ISINs" (11-04). With D1 now stored, the first run on the new code
  (`post_20261108_105955_p2`) shows `WINDOW_FISHER_IDENTITY` n_applicable = 2,465,360 and n_violating = 0. Net change 2,466,681 -> 2,465,360 = -1,321 rows (-0.05 %). The detector kept testing live rows; the 40 violating rows belonged to the 6 FND-0250
  funds, which left the universe. The residual stays accepted until 2027-01-09. (That the -1,321 rows are those funds' rows is NOT verified.)
- **A5.5 (H1 step 1):** a FULL audit on code containing 4ccb9d9 now exists (`post_20261108_105955_p2`, code 972b0b1, PASS_WITH_WARNINGS) but it is NOT a re-measurement: no full P2 run happened between (p2_pipeline_log has no rows after the 09:50
  single-fund backfill) and FND-0383's guard acts only inside a P2 run. Extreme set (|MAD-Z| >= 50) is unchanged, 21 of 21; INVALID_NAV is still 2. H1 step 1 waits for a full P2 run followed by an audit.
- The owner's cycle ran this audit while the WS0.5 edits were uncommitted: its header says `git_clean = False` (earlier headers True; `redireccion`, a deletion by another session, also keeps the tree dirty). Nothing in the repo gates on it.

## A6.3 WS0.5 delivered (FND-0392)
1. **Harness (A4 #2, G-05):** `shared/fault_injection.py` (9 corruption classes incl. the two A5 types: one-month level shift 50/100/150% and implausibly smooth NAV), `scripts/audit/fault_injection_run.py` (hermetic, reads only the fixture),
   `scripts/audit/fault_injection_fixture.py` (read-only build), fixture `tests/fixtures/fault_injection/` (39 sample + 1 real positive + 245 peers, 52,459 rows, 345 KiB). Deviation from A4 #2: no throwaway Postgres container is needed at this stage because the validators are
   pure; the container comes back with the predicate and the engine (WS2/WS3). First measurement: scale/unit/frozen/level-shift classes caught (recall 1.00, `level_shift_50` >= 0.95 at the 0.35 cut), the real defect flagged on its real series, 0 baseline false positives
   on 39 healthy series; known gaps reported, not hidden: duplicated month, wrong-currency step (+12%), smooth NAV (volatility x0.72 over the whole series).
2. **Validator (A5):** `validate_nav_unshared_jump` + `utils/peer_returns.py` + `db_readers.load_peer_returns`, wired in `run_pipeline.py` behind `NAV_UNSHARED_JUMP_MODE` = "OFF" (SHADOW logs `NAV_JUMP_SHADOW`, ON quarantines). Cut 0.35 (0.40 gave 0.94 recall on +50% shifts;
   0.30-0.50 flag the same single fund on 285 funds). Universe-wide false positives: WS1.
3. **Family level (A5):** `shared/family_scope.py` (`group_by_family`, `collapse_flags`, `load_family_map`) on the persisted `fund_master.fund_family_id`; a fund without a family is a family of one. The predicate that calls it is WS2.
4. **D1 store (A5):** `shared/statistical_audit/applicability.py`, `AuditRun.record_applicability`, hooked in `_run_invariants` and the `TIMESERIES_DUPLICATE` check. Verified in production by the owner's own post-audit: 24 p2 rules + 7 cost rules stored. The history reader works on live data.

## A6.4 Still open in WS0.5 / next
Delivery receipt of the WS0.5 commit (`delivery_attest.py --scope full`), then FND-0392 closes by itself on the receipt. Next: WS1 (FND-0393) — it needs a FULL P2 run on the new code first (owner-run); D1 canaries are its hard gate for WS2.

---

# Amendment A7 (2026-11-08, evening): owner order "WS1 now, start WS2, review the held fund, clear the noise, align administration"

## A7.1 WS1 first slice: what the evidence says (FND-0393)
- **Full P2 pass done** (`P2_runCycle`, 14:33 to 17:38, run `20261108_155048`, 0 errors, 3 quarantined) and its POST audit `post_20261108_173304_p2`. The extreme set (|MAD-Z| >= 50) is 21 -> 22: **the existing guards explain none of the 21** (A1 hypothesis refuted).
- **Forensics of the 22** (`scripts/audit/nav_forensics.py`, read-only, generic): **1 NAV defect** (DE000DWS3LN1, +92.9% in one month, 0 of 1,620 peers moved), **16 SMALL_SAMPLE_METRIC** (11 are `sharpe_slope|rolling_3y` on 3-7 observations where the nature's norm is 36; 5 are regime/since-inception metrics on about half the usual history),
  **2 LOW_VOLATILITY_RATIO** (incl. the money-market fund), **3 REVIEW** (LU1663960000, ES0116848013, LU0922333322: adequate samples, extreme in their own right).
- **G-03 is REFUTED.** Plan item "split baselines by Fund_Nature first (denoise before calibrating)" assumed baseline pollution. Measured: judged inside their own nature, 21 of 22 funds are as extreme or MORE (e.g. 436 against 157); only one falls (-153 to -69) and none below 20. **L1 (nature-split baselines) is demoted; the lever is the sample size.**
  Root-cause ticket **FND-0401** (P2 publishes slope/regime metrics on 3-7 observations: a minimum-observation floor is missing).
- **Validator universe census** (2,928 funds, 522,609 one-month returns): at the 0.35 cut 17 jump-months in 16 funds, 2 in the last 12 months; most older flags look like genuine single-region re-pricings (NOT verified). `NAV_UNSHARED_JUMP_MODE` = **SHADOW** (commit `2cfe877`, published). **Not ready for ON**: needs a recency window,
  the verified-genuine exemption (FND-0394) and a labelled precision (FND-0395), because it looks at the whole history and would quarantine a fund for good over a real one-off event.
- **Still open in WS1:** D1 detector canaries (hard gate before the selection effect of WS2 is switched on), G-06 closing condition for the extreme-fund tickets, the jump-cluster-by-source signal, the three REVIEW funds and LU0232465467 (four adequate-sample extreme metrics).
  **Confirming SHADOW in production** needs one P2 pass: its first `NAV_JUMP_SHADOW` rows in `p2_pipeline_log` are the proof (not yet run).

## A7.2 FND-0397: the held fund's nature (classifier fix built, live data NOT changed)
- **Review (read-only):** Man Dynamic Income family (3 classes, one held at 5.5% Defensiva). Replay of `resolve_nature_evidence` reproduces the stored nature; the KIID vote is `_RF_pending` and `resolve_rf_subtype` returns RF_Corto **because the substring "fecha de vencimient" fires on the open-ended boilerplate "el fondo no tiene UNA
  fecha de vencimiento fija"**: the negation guard (FIX-P1-RFC2) knows "no tiene fecha de vencimiento" but not the article. The KIID has no duration wording at all; `Duration_Profile = Short` and `Credit_Quality = Investment Grade` are DERIVED from the nature and name tokens (`derive_duration_profile` / `derive_credit_quality`), so they confirm nothing (ticket **FND-0402**).
- **Fix** (`classify_utils.py`): optional article in the negation guard + generic short-duration name abbreviations (`short dur`, `shor dur`, `ul sh`, `ult sh`) so genuine short-duration funds keep their only signal. Tests discriminate (the defect reproduces on the old code).
- **Census before any live change** (978 RF funds with a pending KIID vote): the fix changes 67 subtypes (44 in the universe): 66 Corto -> Flexible, 1 Flexible -> Corto; none of the five named short-duration funds flips. Examples that leave `Corto Plazo` (and Defensiva's peer group): AB American/European Income and High Yield, MFS Emerging Markets Debt, UBS convertibles and China bond, GAM Credit Opportunities, AXA Strategic Bonds.
- **NOT done, owner decision (R-2 step 3):** the live data still says Corto Plazo. Applying it = FORCE_REFRESH of the affected ISINs and a P1 nature-first pass; the held position and every scenario are unchanged. Impact on selection if the 44 funds move is NOT assessed.

## A7.3 FND-0280: the "closed ticket, accepted residual" alert was a false positive
`accepted_residuals.attention_lines` flagged ANY accepted residual whose ticket is CLOSED, including STANDING ones (owner design 2026-11-06: defects of the document, no calendar expiry, bound to the KID evidence). FND-0280 fixed 71 of 73 funds; the other 2 (GVC Gaesco Columbus, classes A and P) are a PUBLISHER_DEFECT: the KID prints a management fee of 3.27% above
its own 3.1% annual cost impact and the extracted values equal the printed ones. **Resolution: the acceptance stays, FND-0280 stays CLOSED, the alert now skips standing acceptances** (a time-limited one under a closed ticket is still reported; a standing one whose document changed still appears as CADUCADA). Live check: 0 attention lines in both domains.
Side observation: FND-0280 closed with the evidence "tests_pass: local receipt: 0 passed ... scope docs_only" (logged on FND-0388).

## A7.4 The six COVERAGE_CLIFF warnings (`time_to_recovery`, -2.1% to -4.6% n_valid)
Explained by the definition: of the 1,194 in-universe funds without a valid 1-year `time_to_recovery`, 1,192 recompute to NaN ("never recovers inside the window") on today's NAV, after a -1.4% median month in 2026-09. The exact PRE-to-POST delta of 36 funds is not individually attributable (no stored PRE state). Two funds recompute to a number (LU1663960000, LU2334589889): not explained.

## A7.5 WS2 started: `shared/data_suspect.py` (pure, switch OFF, no consumer wired)
`find_suspects(panel, nav_flags, family_of)`: tiers NAV_JUMP (the validator's flag, input), DEVIATION (an ADEQUATE-sample metric at |z| >= 50), INTEGRITY (>= N adequate metrics, money market excluded); share classes collapse to one suspect. A metric counts only when its `source_rows` is not both below its nature's p10 and below half its median.
**Live run** (1,240,360 metric rows, 2,904 funds, 485 metric keys; 2.6 s after a 6.1 s load): 6 suspects, all DEVIATION: the 3 REVIEW funds, the 2 LOW_VOLATILITY_RATIO funds and LU0232465467; **15 of the 16 small-sample funds are correctly not flagged**.
**Finding for WS3:** no fund has more than 5 adequate-sample metrics at |z| >= 50, so the INTEGRITY threshold of 8 (taken from raw alarm counts) would never fire; it must be recalibrated on adequate-sample counts. Not yet built: the loader of the NAV flags, build-time eligibility (`fund_scorer`), the `D-DATA-SUSPECT` control, the exemption registry (with the 7-day warning and one-build grace), PIT evaluation, the selection-impact KPIs.
**Gate decision:** A4 #1 said WS2 does not start before the D1 canaries are green; the owner then ordered WS2 to begin. WS2 is built behind `DATA_SUSPECT_ENABLED = False` and NOTHING reads it: its selection effect stays off until the canaries are green.

## A7.6 Administration
25 proposed RELATED links of FND-0391..0398 and 6 of FND-0401/0402 were CONFIRMED on the owner's written order (append-only events, no DEPENDS_ON added). The missing restore point of the P2 cycle is recorded in `deployments/20261108_P2_cycle_no_restore_point.md` (what was written, why it is recomputable, what is NOT verified: whether WAL archiving and the off-box dump are current).

---

# Amendment A8 (2026-11-08, night): owner order "FND-0397 NO GO, floor, three-fund review, integrity threshold"

## A8.1 FND-0397: simulated impact before any live change (owner: NO GO for FORCE_REFRESH / P1 cycle)
`scripts/audit/nature_move_impact.py` (read-only; imports the P3 builder, writes nothing; output `out/audit/P1/nature_move_impact.json`). Regime Shock_Energetico, weights Defensiva .55 / Equilibrada .35 / Dinamica .10. Fidelity: the rebuilt baseline equals PRODUCTION on 30 of 30 funds.
- 44 in-universe funds move (43 Corto -> Flexible, 1 Flexible -> Corto in-universe). Defensiva peers that did not move: max |score change| 0.0395; 18 of the top-30 unmoved funds change rank. Equilibrada gains 22 candidacies.
- Selection turnover 14.5% (6 funds). The held IE00008TZ2E8 leaves (family dedup keeps its USD sibling IE000W4DU5G7, which misses the cut). IE000MI53C66 (rank 11 -> 31) and IE00B3Q8M574 (7 -> 27) stay selected. ES0175414012 joins Defensiva; JPM GBP Liquidity LU1873128927 joins and Amundi Cash USD LU0987193777 leaves.
  Manager-cap cascade in Equilibrada: JPM US Select Equity LU0672672143 out, Amundi S&P500 Screened LU0996179429 in.
- Weighted portfolio: return_ann_real 5.41% -> 4.90%, sharpe 0.686 -> 0.552, max drawdown -9.70% -> -9.81%, srri 3.67 -> 3.615. Credit_Quality re-derived to "Mixed" for 36 moved funds.
- NOT modelled: P2 peer-dependent metrics, the P1 family consensus, further moves. **Decision stays with the owner; live data unchanged.**

## A8.2 FND-0401: minimum sample floor (built, uncommitted until the receipt of d42aed4 ended; delivered with this batch)
- Cause: the slope was published from n_obs >= 3 (hard-coded) while `pctile_self` needs 12; the regime floor (`REGIME_MIN_OBS` = 12) was enforced in code but never removed rows already stored (1,870 of the 1,891 funds with n_obs_expansion < 12 still held return_ann_expansion).
- Fix: `ROLLING_SLOPE_MIN_OBS` = 12; `delete_unproduced` after each family write (owned set = `regime_owned_metrics()`, 37 names); one-shot `scripts/ops/clean_stale_metric_rows.py` (dry run: 11,812 regime rows / 2,373 funds, of which 9,308 / 1,871 in the universe; 162 slope rows / 20 funds). `--apply` is owner-only.
- Counterfactual on the cleaned panel: extreme funds 22 -> 7, no new extremes. With `REGIME_MIN_OBS` = 24 (26,824 rows) only DE000DWS3LN1 and ES0116848013 remain. 24 is an owner policy decision, not implemented.

## A8.3 WS1: the three review funds (`scripts/audit/fund_dossier.py`, output `out/audit/P2/fund_dossier.md`)
- LU1663960000: all 6 anomalous rows are STALE_REGIME (0-5 months in the regime). LU0922333322: one anomaly, sortino_expansion on 11 months, stale. Both disappear with the floor and the cleanup. NAV healthy (LU1663960000 has only 36 rows over 8.7 years: sparse).
- ES0116848013 (Global Allocation FI I, Renta 4): the only genuine case. sortino|crisis_2022 = 37-45 on an adequate 12-month sample (5 months below the risk-free rate, downside deviation 0.0093 a month, mean excess 5.2% a month). The stored NAV implies calendar 2022 +75.8% (+16.2% in 2022-06 against peers -4.7%), then -0.7 / -6.4 / +2.3 / -11.6%.
  The validators pass. **Needs a human check against the publisher figures** (KIID has no yearly figures; annex www.renta4gestora.com/fondos-de-inversion/fondos/es0116848013).

## A8.4 WS3: integrity threshold recalibrated
`DATA_SUSPECT_MIN_FLAGGED_METRICS` 8 -> 4. Evidence: the current panel has at most 5 adequate-sample extreme metrics per fund (>= 4: 2 funds, >= 3: 2); after the cleanup the maximum is 2 (both current hits, LU1663960000 and LU0232465467, are driven by stale rows). 4 is PROVISIONAL: metric keys over-count correlated quantities, and the owner's 3 would flag the same 2 funds today.
WS2 stays OFF (`DATA_SUSPECT_ENABLED` = False, nothing reads it) until the monitors show a green light: D1 canaries, the labelled precision of FND-0395, the exemption registry.
