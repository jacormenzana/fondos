# Two-pillar data-control & governance: action plan (2026-10-11)

Status: APPROVED 2026-10-11 (owner decisions D1–D4, D6, D8 recorded in §5; D5 scheduled; D7 proposed, shadow first). G2 quick win shipped (§4, commit `d06af3e`). Backlog: epic FND-0259, phases FND-0260..0265.
Baseline verified against `master` @ `bbc113c` and live `gestion.backlog` on 2026-10-11. Review history: §7.

---

## 1. Executive summary

- **This is not a greenfield build.** FND-0251 already ships most of the two-pillar model at **promotion** time:
  - one shared defect predicate (`gating.is_blocking`);
  - defect class x portfolio scope;
  - hard FX and cash gates with no override;
  - residuals that carry a ticket (AP) and an expiry;
  - the read-only governance KPIs in `governance_kpis.py`.
- **Main gap #1: there is no INCOMPLETE state.** The audit's exit code is 0 or 1. Skipped blocks are only printed (`run.skipped`) and never persisted. A missing dependency degrades the run to a pass.
- **Main gap #2: the gate trusts any run as evidence.** The promotion gate takes the *latest* run by `audit_finding.detected_at`. That pick has three flaws:
  - it does not check whether the run was full-universe or a `_sample` run;
  - it does not check whether the run was complete;
  - a run that wrote no findings is invisible to it.

  Persistence also commits per table, so a crashed run can leave a partial run that looks like a real one.
- **Main gap #3: Pillar 1 is partial.**
  - Code integrity is recorded but never gated (`_git_state` is information only).
  - The tolerance on Σw (1e-3) is looser than the 4-decimal construction contract.
  - No configuration hash exists.
  - Execution evidence (telemetry) is best-effort, so it cannot serve as evidence.
- **Main gap #4: exceptions and remediation are weak.**
  - Renewing a residual overwrites its history.
  - There is no separate approver and no revocation.
  - No finding is linked to an incident.
  - A ticket can be closed without a re-audit.
  - The cost domain never feeds the promotion gate (`AUDIT_DOMAIN = "p2_metrics"`).
- **Recommended approach:** six phases, each behind a kill-switch that ships OFF. The DDL is applied by the owner. The audit runner stays read-only and only detects. A new, thin orchestration module consumes both pillars.

---

## 2. Baseline vs target matrix

| # | Control | Pillar | Exists? | Where (evidence) | Gap | Phase |
|---|---|---|---|---|---|---|
| T1 | Code integrity (HEAD == tip, clean tree) | P1 | Recorded only | `p3_promote_scenario.py:42` (`_git_state`, "information, never a gate"); `cycle_run.git_commit` | Not a gate. No deployment policy defines "tip". Not checked by the launcher preflight | 01, 06 |
| T2 | Version identification (code + config) | P1 | Partial | `CALC_VERSION`, gold `algorithm_version`/`batch_id`, `catalog_version.compute_catalog_version()` | No hash of the `shared/config.py` flags and limits. Version is not bound to the promotion record | 04 |
| T3 | Non-EUR exposure ≤ 30% | P1 | **Yes** | `shared/fx_exposure.py`; `scenario_lifecycle.evaluate_promotion` (GATE_FX, no override); built in by `portfolio_engine.select_within_exposure` | None (keep). Catalogue it only | 01 |
| T4 | Cash ≤ 10% | P1 | **Yes** | `evaluate_promotion` GATE_CASH (`MAX_PORTFOLIO_CASH_WEIGHT`) | None (keep) | 01 |
| T5 | Σw = 1.0000 + precision policy | P1 | Partial | Builder uses largest remainder at 1e-4 (`portfolio_engine.py:68`); gate tolerance is 1e-3 (`scenario_lifecycle.py:202`) | The gate is looser than the contract. The policy is not written down | 01, 06 |
| T6 | Execution evidence | P1 | Partial | `ingestion_log` rows for promoted / blocked / override; `cycle_*` telemetry is failure-isolated (`cycle_telemetry.py:1`) | No mandatory, verifiable record of which controls ran and with what result | 04 |
| D1 | Distributions (MAD-Z / IQR) never block | P2 | **Yes** | `gating.py:29` (defect sets); FND-0175; `outliers.py` | None. Formalise in the catalogue | 03 |
| D2 | Methodological invariants block | P2 | **Yes** | HARD_INVARIANT / BLOCK2 / TIMESERIES_DUPLICATE (`gating.is_blocking`); `catalog_invariants.py` | FND-0234 false positives still open | 03 |
| D3 | Temporal integrity | P2 | Partial | BLOCK6 timeseries integrity; FND-0173 (`TIMESERIES_GAP` never investigated) | Gap triage. Freshness is checked in P3 (`data_freshness.py`), not in the audit | 03 |
| D4 | Algorithm drift / uniformity | P2 | Partial | P3 freshness check on `CALC_VERSION` uniformity; RCA-E tagging (`_apply_rca_e`); recompute gate (`--verify-recompute`) | Not an audit rule with a status of its own | 03 |
| D5 | Scalar vs timeseries per metric family | P2 | Partial | `SCALAR_EQUALS_TIMESERIES` (`tolerances.py:35`: "NOT resolved") | No tolerance per family, relative or justified | 03 |
| D6 | Lineage | P2 | Partial | `batch_id` on gold; `TIMESERIES_NULL_PROVENANCE`, `BETA_ORPHAN_BATCH` | Findings do not carry the batch / CALC_VERSION of the data they judged | 04 |
| D7 | Exceptions | P2 | Partial | `audit_accepted_finding` (AP, reason, accepted_by, expiry ≤ 90 days); renewal is an UPSERT (`persistence.py:189`) | No approver separate from the accepter, no revocation, no history | 04 |
| G1 | Audit completeness (INCOMPLETE) | Orch. | **No** | `run.skipped` is printed only; RC 0/1 (`run_statistical_audit.py:2018`); a missing residuals table is "none applied" (`:1787`) | Required: a mandatory-control contract, persisted status, RC for INCOMPLETE | 02 |
| G2 | Evidence-grade run selection | Orch. | **No** | `gating.py:131` takes the latest `detected_at` | Ignores scope (sample vs full) and completeness. A zero-finding run is invisible. Persistence is not atomic (`persistence.py` commits per emit) | 02 |
| G3 | Cost domain at promotion | Orch. | **No** | `p3_promote_scenario.py:39` | `cost_attributes` defects on scenario funds never block | 06 |
| G4 | Closed-loop remediation | Orch. | Partial | `--state-snapshot` / `--verify-recompute`; violator drift (`audit_finding_isin`); `attention_lines` warns when an AP is CLOSED but its residual is still active | No finding↔incident link. Closing a ticket needs no re-audit evidence | 05 |
| G5 | Code-deploy vs promotion separation | Orch. | Implicit | Promotion = `p3_promote_scenario.py`. "Deployment" = running the launchers from the working tree | No deployment operation exists to gate | 01 |

---

## 3. Phases

### 01: Framework and critical gaps

| Field | Content |
|---|---|
| Objective | One control catalogue with owner, acceptance criterion and consequence for each control in both pillars. |
| Baseline | Defect predicate `gating.py:29-41`; GATE_* codes in `scenario_lifecycle.py`; audit block model in `AUDITORIA_ESTADISTICA.md`; `--list-catalog`. |
| Gap | No single register. "Deployment" is undefined. The Σw precision policy is unwritten. Pillar-1 code integrity is not gated. |
| Actions | • `doc/reglas/GOBIERNO_CONTROLES.md`: catalogue + decision matrix (pillar x state x operation)<br>• Control-id scheme: `T-*` (technical), `D-*` (data), `G-*` (orchestration); map every GATE_* code and every audit rule class onto it<br>• States PASS / PASS_WITH_WARNINGS / FAIL / INCOMPLETE, with precedence (INCOMPLETE > FAIL for authorisation: neither authorises)<br>• Precision policy: weights in 4 dp, Σ = 1.0000 exactly after largest remainder, gate tolerance ≤ 5e-5<br>• Define the two operations: CODE_DEPLOY (a launcher runs production code) and PORTFOLIO_PROMOTION<br>• Index the new doc in `AGENTS.md` and cross-link `NORMAS_BACKLOG.md` |
| Deliverable | Control catalogue + dual-pillar decision matrix (doc). Machine-readable mirror `shared/governance/controls.py` (data only). |
| Exit criterion | Every control has an id, pillar, owner, criterion, consequence and operation. A test asserts that every GATE_* code and every `is_blocking` class maps to a catalogue id. |
| Tests | Positive: full mapping. Negative: an unmapped GATE code fails the test. |
| Backlog | New ticket (epic). Affects FND-0251 (READY_FOR_DEPLOY: close it first or absorb its open tail). |
| Risks / owner decisions | Resolved: D1, D2, D3 (§5). |
| Kill-switch / migration | None (doc + constants). |

### 02: Completeness contract and contract tests

| Field | Content |
|---|---|
| Objective | No PASS while essential evidence is missing. |
| Baseline | `run.skipped` (`run_statistical_audit.py:283…`); `_apply_accepted_residuals` degrades silently; `clear_run` + per-table commits. |
| Gap | No mandatory-rule list, no INCOMPLETE status, no run header, non-atomic persistence, no scope recorded. |
| Actions | • `catalog_mandatory.py`: per domain, the rules that must be EVALUATED (not skipped)<br>• Classify every `skipped` reason as LEGIT_EMPTY (an eligible population of 0, proven by a count) or MISSING (table/column/source absent, query error)<br>• New `control.audit_run` header: run_id, domain, scope (`FULL`/`SAMPLE`/`ISIN_LIST`), n_universe, status, catalog_version, git sha, config hash, started/ended, skipped JSON<br>• Persist the header plus all rows in ONE transaction; header written last = commit marker<br>• RC: 0 PASS / PASS_WITH_WARNINGS, 1 FAIL, **2 INCOMPLETE**<br>• Contract tests: catalogue ↔ procedural rules ↔ PG schema (columns each rule reads exist in `db/pg/*.sql`) |
| Deliverable | Completeness contract + `audit_run` + integration suite (hermetic PG). |
| Exit criterion | Each scenario returns its expected state: missing source table, renamed column, an omitted mandatory rule, an empty legitimate population, a query error, a crash mid-persist (no header → run does not exist). |
| Tests | One positive and one negative case per scenario above, in `run_pg_tests.py`. |
| Backlog | New ticket. Affects FND-0234 (rule set), FND-0251 (gate reader). |
| Risks / owner decisions | D4 run scope for the gate (FULL only?). Launcher RC handling of 2 (`P1_P2_Complete.bat` audit steps are diagnostics: they must not change the cycle RC). |
| Kill-switch / migration | `AUDIT_COMPLETENESS_CONTRACT_ENABLED` (OFF); `scripts/ops/migrate_audit_run.py` (owner). |

### 03: Consolidate quality rules and classification

| Field | Content |
|---|---|
| Objective | Separate real defects, legitimate methodological differences and statistical anomalies. |
| Baseline | `tolerances.py` (named, swept constants); `catalog_pairs.py`; `RETIRED_RULES`; FND-0175 outlier policy; FND-0234 false positives. |
| Gap | `SCALAR_EQUALS_TIMESERIES` has one absolute tolerance (unresolved). Criticality is implicit in the rule class. Algorithm drift is not a rule. FND-0173 gaps never triaged. |
| Actions | • Group SCALAR_EQUALS_TIMESERIES by metric family (the 9 FND-0236 families); measure the divergence distribution and its root cause per family; set a relative or absolute tolerance with a documented justification<br>• Add `criticality` (CRITICAL / MAJOR / MINOR) + `expected_evidence` to every catalogue entry; `is_blocking` derives from criticality (keep the current three classes as CRITICAL)<br>• New rule `ALGORITHM_VERSION_UNIFORMITY` (share of active funds on the current CALC_VERSION / family version)<br>• Close the FND-0234 false positives; triage FND-0173 gaps into defect vs legitimate<br>• Version the catalogue (`catalog_version` already hashes the files → record it in `audit_run`) |
| Deliverable | Versioned rules catalogue: criticality, tolerance + justification, tests, expected evidence. |
| Exit criterion | Every CRITICAL rule has reproducible positive and negative cases and one stated consequence. No tolerance lacks a justification line. |
| Tests | Positive and negative cases per CRITICAL rule; per-family tolerance tests on synthetic series. |
| Backlog | Absorbs FND-0234 (IN_PROGRESS) remainder, FND-0173. New ticket for the per-family tolerance. |
| Risks / owner decisions | D5 which families get a relative tolerance. Risk: re-classifying flips gate outcomes, so run a shadow diff before switching. |
| Kill-switch / migration | `AUDIT_CRITICALITY_MODEL_ENABLED` (OFF; shadow mode prints the old vs new blocking set). |

### 04: Traceability and exception governance

| Field | Content |
|---|---|
| Objective | Rebuild any run and any risk-acceptance decision without relying on session notes. |
| Baseline | `catalog_version`; `cycle_run` (git_commit, calc_version); `audit_accepted_finding` (UPSERT, `persistence.py:189`); promotion note in `ingestion_log`. |
| Gap | No config hash. Findings carry no data lineage. Exceptions have no approver, no revocation and no history. |
| Actions | • Execution manifest = `audit_run` row + JSON: git sha + clean flag, catalog_version, `config_hash` (the `*_ENABLED` flags + mandate limits from `shared/config.py`), CALC_VERSION, universe size, data cut-off (max NAV date, max calculation_date), coverage<br>• Findings: add `data_calc_version` / `batch_id` where the source row has them<br>• Exceptions: append-only `control.audit_exception_event` (GRANT / RENEW / REVOKE / EXPIRE) with `requested_by`, `approved_by` (≠ requester), reason, AP, expiry. `audit_accepted_finding` becomes a view of the active state<br>• Revocation causes: AP closed, violation resolved, expiry, owner revoke<br>• A read failure on the exception store → INCOMPLETE (never "no residuals") at the gate<br>• Promotion record carries the manifest ids of both pillars |
| Deliverable | Execution manifest, exception policy (in `GOBIERNO_CONTROLES.md`), auditable decision log. |
| Exit criterion | Given a promotion id, a query returns the code, config, catalogue, audit runs, exceptions and approvers that authorised it. |
| Tests | Rebuild test; approver == requester refused; revoked residual stops covering on the next gate evaluation; unreadable store → INCOMPLETE. |
| Backlog | New ticket. Affects FND-0251 (`accept_audit_residual.py`), FND-0239 (telemetry manifest overlap). |
| Risks / owner decisions | Resolved: D6, two-step GRANT → APPROVE with no time pad (§5). |
| Kill-switch / migration | `AUDIT_EXCEPTION_EVENTS_ENABLED` (OFF); `migrate_audit_exception_events.py` (owner; backfills the current rows as GRANT). |

### 05: Closed-loop remediation

| Field | Content |
|---|---|
| Objective | A defect closes only on re-audit evidence. |
| Baseline | `--state-snapshot` / `--verify-recompute`; violator drift; `backlog_client.report_incident()`; `attention_lines` (AP closed + residual active). |
| Gap | No finding ↔ incident record. Closure needs no evidence. |
| Actions | • `control.audit_incident` (finding key = domain + rule_id + group_key + ISIN set; AP id; root cause; corrective action; affected data version; state)<br>• Open: a CRITICAL finding with no incident → `report_incident()` (existing client; no-op without DSN)<br>• Recompute only through the owning pipeline (`run_pipeline.py --isin … --force`); the auditor stays read-only<br>• Certification = verify-recompute OK (input hash changed) + a re-audit run (scope ⊇ affected ISINs, status ≠ INCOMPLETE) with 0 violators<br>• Closure gate in the backlog tooling: refuse to close a linked AP without a certifying run_id; persistence or INCOMPLETE re-audit → stays open |
| Deliverable | Incident workflow + post-fix certification procedure. |
| Exit criterion | No CRITICAL defect is closed by a recompute alone (test: recompute without re-audit → close refused). |
| Tests | Fixed → closes; still violating → open; re-audit INCOMPLETE → open; hash unchanged → open. |
| Backlog | New ticket. Affects the `NORMAS_BACKLOG.md` closure rule, FND-0251 RCA-E. |
| Risks / owner decisions | D7 auto-open on the first CRITICAL occurrence or only on recurrence (noise vs coverage): decided on one shadow cycle that logs the incidents it would open, without writing to the backlog. |
| Kill-switch / migration | `AUDIT_INCIDENT_LOOP_ENABLED` (OFF); `migrate_audit_incident.py` (owner). |

### 06: Integrate the pillars with pipelines and promotion

| Field | Content |
|---|---|
| Objective | Controls become effective operating conditions. |
| Baseline | `evaluate_promotion` (all GATE_*); `fund_scorer` eligibility; launcher preflight (`p1p2_state.py preflight`). |
| Gap | No code-integrity gate. Cost domain not gated. Gate reads unvalidated runs. Σ tolerance loose. |
| Actions | • New `shared/governance/orchestrator.py`: `authorise(operation, …) → Verdict(pillar1, pillar2, state, blockers)`; pure + one loader; `p3_promote_scenario.py` calls it (no logic duplicated)<br>• Pillar 1 at PORTFOLIO_PROMOTION: code integrity per D1, Σ ≤ 5e-5, FX, cash, manifest present: hard, **no override**<br>• Pillar 1 at CODE_DEPLOY (D3): launcher preflight records the code state in the manifest and WARNS on dirty tracked tree / HEAD ≠ tip (never changes the RC)<br>• Pillar 2 at promotion: latest COMPLETE, FULL-scope run per domain (**p2 + costs**) newer than the DRAFT; INCOMPLETE → block; existing data override (`--accept-alarms` + AP) remains, Pillar 2 only<br>• Ingestion-time checks only for rules evaluable on one fund (bounds, hard invariants on costs) → DQ flags, never blocking ingest<br>• End-to-end matrix: success, P1 fail, non-blocking anomaly, CRITICAL defect, INCOMPLETE audit, sample-run-as-evidence, crash-mid-persist |
| Deliverable | Two-pillar orchestration + evidence-based promotion gate. |
| Exit criterion | No promotion passes with a P1 failure, an open CRITICAL defect on scope, or an INCOMPLETE / sample / partial audit. Each case is covered by a test. |
| Tests | The matrix above (hermetic PG, 40-ISIN sample). |
| Backlog | New ticket; DEPENDS_ON 02 and 04. Absorbs FND-0251's open tail. FND-0254 (PIT parity) stays separate. |
| Risks / owner decisions | Resolved: D3 (launchers warn; only promotion blocks on code state) and D8 (replay + one shadow cycle, then block). Risk: the promotion will refuse while local commits are unpushed: push before promoting. |
| Kill-switch / migration | `GOVERNANCE_ORCHESTRATOR_ENABLED`, `CODE_INTEGRITY_GATE_ENABLED` (both OFF; shadow mode prints both verdicts). |

---

## 4. Critical path

1. 01 catalogue + D1–D3 decisions (blocks everything).
2. 02 completeness + `audit_run` (closes the largest hole: sample / partial / INCOMPLETE runs passing as evidence).
3. 04 manifest + exception events (needed by 06's evidence check). Can run in parallel with 03.
4. 03 criticality model (shadow first, then flip).
5. 06 orchestrator + gates (shadow one monthly cycle, then flip).
6. 05 incident loop (needs 02 + 04; does not block promotion hardening).

Quick win (inside 02), **shipped 2026-10-11**, two-sided, no DDL:
- `gating.load_scope` skips run ids ending in `gating.SAMPLE_RUN_SUFFIX`.
- `run_statistical_audit._resolve_run_id` suffixes every `--isin`-scoped run, explicit `--run-id` included. Launcher ids (no `--isin`) are unchanged.

It closes G2's worst case: a sample can no longer pass for promotion evidence. Completeness and atomicity remain phase 02.

---

## 5. Owner decisions

| # | Decision | Resolution | Status |
|---|---|---|---|
| D1 | Meaning of "tip" for code integrity | `HEAD == origin/master` + clean tracked tree; untracked files ignored | **Approved** 2026-10-11 |
| D2 | Σw tolerance at promotion | 5e-5 (half a unit of the 4th decimal); builder unchanged | **Approved** 2026-10-11 |
| D3 | Where code integrity BLOCKS | **Blocks at PORTFOLIO_PROMOTION only.** Cycle launchers (`P1_P2_Complete`, `P1_P2_P3`, `P2_P3_complete`, `P3_buildPortfolio`) record the code state in the run manifest and WARN; diagnostics are out of scope | **Approved** 2026-10-11 (changed from "launchers block") |
| D4 | Valid audit evidence | FULL scope + status ≠ INCOMPLETE + newer than the DRAFT. A FAIL run IS valid evidence (it is what blocks); accepting only PASS runs would let the gate skip a FAIL and fall back to an older PASS. Sample runs never count | **Approved** 2026-10-11 (wording corrected) |
| D5 | Relative tolerances | Only for families whose divergence is proportional to level | **Scheduled**: decided at phase 03's exit, on the per-family divergence measured during phase 03 |
| D6 | Approver separation with a single owner | Two steps: GRANT (structured reason + AP), then a separate explicit APPROVE event; both logged; **no time pad** | **Approved** 2026-10-11 (changed from "≥ 24 h apart") |
| D7 | Incident auto-open | First occurrence of a CRITICAL finding on an active fund. Shadow first: one cycle logging the incidents it WOULD open (no backlog write), reviewed before enabling | **DECIDED 2026-10-17 by the owner: FIRST occurrence** (replay of 27 runs: p2 36 vs 35, costs 11 vs 11 incidents; first detects one run earlier). Auto-open still behind `AUDIT_INCIDENT_LOOP_ENABLED` |
| D8 | Cost domain at promotion | Retrospective replay of the persisted `cost_attributes` runs (what would have blocked), then one shadow cycle, then block | **Approved** 2026-10-11 (+ replay) |

---

## 6. Out of scope

- Changing the mandate limits (30% FX / 10% cash): a reviewed config commit, as today.
- The auditor writing business data, applying fixes, approving exceptions or promoting.
- PIT backtest parity (FND-0254).
- BI / Superset dashboards for the new tables (P4 can follow on the read-only roles).
- Statistical anomalies as blockers (kept as review items, FND-0175).

---

## 7. Review history (2026-10-11)

Four external assessments, each verified against the code. What changed the plan:

| Point | Verdict | Effect |
|---|---|---|
| D4 "COMPLETE" as the evidence state | Corrected | COMPLETE is not a state. Evidence = status ≠ INCOMPLETE; a FAIL run counts (§5 D4) |
| Quick win "exclude `_sample` ids" alone | Incomplete | The runner trusted an explicit `--run-id` under `--isin`. The guard is now two-sided (§4) |
| Suffixing explicit ids breaks callers | Refuted | Every launcher passes `--run-id` without `--isin` (`P1_P2_Complete.bat` audit calls); `accept_audit_residual.py` never persists. Pinned by test |
| Header-last ≠ atomicity | Already covered | Phase 02 persists header + rows in ONE transaction; the header is the marker, not the mechanism |
| Code-integrity friction on launchers | Accepted | D3: launchers warn, promotion blocks |
| 24 h cooling-off adds latency | Accepted | D6: two steps, no time pad |
| Measure cost-domain impact sooner | Accepted | D8: retrospective replay before the shadow cycle |
| D5 / D7 left open | Scheduled | D5 at phase 03's exit (needs measurement); D7 at the start of phase 05 |
| D7 without a stop gate (5th assessment) | Accepted | D7 is decided on a shadow cycle that logs would-be incidents (§5, phase 05) |
| Plan doc only local | Accepted | The `*_2026[0-9]*` ignore rule (non-canonical dated files) hid it: moved to `doc/backlog/GOBIERNO_DOS_PILARES_PLAN.md`, tracked. Not `doc/reglas/` (normative docs only; the phase 01 output `GOBIERNO_CONTROLES.md` goes there) |
