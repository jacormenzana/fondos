# MasterBacklog Tool — Reference Documentation

**Sistema Fondos · v8 · 2026-08-13**
*Authoritative reference for agents and pipelines interacting with the backlog artifact.*

Artifact: `https://claude.ai/code/artifact/24eb50ad-1274-4149-bae6-6cda184f1a0e`

---

## 1. Architecture & Functionality Overview

**What it is:** A single-page HTML artifact that functions as the live, consolidated issue-and-decision registry for the Sistema Fondos project (P1/P2/P3/BI domains). It is the **sole authoritative source** for open work items, dependency ordering, and policy gate status.

**Core capabilities:**

| Capability | Implementation |
|---|---|
| Multi-domain backlog consolidation | 4 pipelines (P1/P2/P3/BI) unified into one view |
| Priority-ordered execution queue | Integer `Ord` field encodes dependency-cascade-optimal sequence |
| Severity taxonomy | Critical / High / Medium / Low + Open / Closed / Blocked states |
| Dependency chain visualization | Explicit `Upstream → Downstream` cross-references per item |
| Policy gate tracking | Dedicated `Decision Required` blocks for items needing domain sign-off before code |
| Run strip | Top-bar metadata band: last updated date, P0 blocker count, pending gate count, critical path |
| KPI strip | Four live counters: P0 Critical · P1 High · P2+P3 Open · Closed |
| Verdict row | Three fixed executive-verdict panels: most recent P0 resolution, structural finding, corpus-run status |
| Resolved archive | Immutable closed-item log with root-cause and commit references |

---

## 2. Section-by-Section Data Dictionary

### Section 0 — Header / Run Strip

Artifact-level metadata rendered in the fixed top-bar and dark run strip.

| Field | Type | Definition |
|---|---|---|
| Title | string | Artifact name + version + date |
| Item counts | integer × 3 | Total · Open · Closed |
| Last Updated | ISO date | Date of most recent backlog edit |
| Sources | string list | Audit commit hashes + session summaries that informed the current state |
| P0 Blockers | integer | Count of Critical-severity items in Open state |
| Policy Gates | string | Count of items with `Decision Required` block and no committed code |
| Critical Path | ordered string | Minimal ordered sequence of items that must complete to unblock BI layer |

---

### Section 1 — Executive Summary & Strategy (`#exec`)

Synthesized situational awareness for human and agent consumers. Updated on every substantive backlog change.

**Structure:**

- **Strategic Findings block** — 1–4 `finding` cards typed by severity (`ok / crit / warn / info`). Each card includes: title claim, supporting evidence (row counts, commit hashes, run IDs), and a `Cross-References` footer linking to item codes.
- **Dependency Cascade block** — Canonical ASCII dependency graph grouped into four tracks:
  1. Completed critical path (tick)
  2. Active critical path (next gate)
  3. Policy gates (blocked on decision)
  4. Independent hardening (parallelizable)
- **Action List** — Prioritized (`P0 / P1 / P2 / P3`) prose action items, one per unresolved gate or critical step. Always sorted by `Ord`.

**Invariant:** Every item in the Dependency Cascade must have a corresponding entry in the Backlog Matrix.

---

### Section 2 — Consolidated Backlog Matrix (`#matrix`)

High-density tabular registry of all items. Single source of truth for item codes, levels, and execution order.

**Schema:**

| Column | Type | Definition |
|---|---|---|
| `Code` | string | Unique identifier: `{Domain}-{Seq}` (e.g. `P1-01`, `P2-07`, `BI-01`) or `RX-{Seq}` for superseded items. Closed items carry `✓` suffix. |
| `Block / Module` | string | Primary file(s) affected. Multiple files separated by `·`. |
| `Description` | string | One-line root cause or action. Closed items include resolution summary inline. |
| `Level` | chip | `Critical` · `High` · `Medium` · `Low` · `Closed` · `Blocked` |
| `Ord` | integer | Dependency-cascade-optimal execution index (1 = highest urgency). Closed items show `—`. |

Closed rows render with strikethrough and reduced opacity.

---

### Section 3 — P1 Classification (`#p1`)

7 items covering the classification pipeline (`monetarios` → `restantes`, `classify_utils.py`, `pipeline.py`, `fund_family_builder.py`).

**Per-item structure:**

| Sub-section | Purpose |
|---|---|
| `Block` | Exact module(s). Always maps to files in `proyecto1/` or `shared/`. |
| `Root Cause` | Code-level diagnosis: what is broken and why, with specific line references where known. |
| `Fix` | Ordered remediation steps. If multi-step, prerequisites are explicit. |
| `Decision Required` | Present only on policy-gate items (P1-04, P1-07). Lists mutually exclusive options with downstream schema/INTER-rule impact. |
| `Cross-References` | Upstream dependencies + downstream consequences. |

**Current state (2026-08-01 · v7):**

| Item | Title | Level | Ord | Gate type |
|---|---|---|---|---|
| P1-01 | OPT-B-LIVE — Full corpus run with Option B | Critical | 1 | Execution (no remaining blockers) |
| P1-03 ✓ | SC-H2-ARCANO — Credit_Quality inconsistency | Closed | — | P2 data gap confirmed; closed v7 |
| P1-04 | BL-B6-EM-GOVT — EM sovereign IG threshold policy | Medium | 5 | Policy decision |
| P1-05 | WRONGDOC-DWS — DWS Multi Opp name collision in Excel | Low | 8 | Manual data fix |
| P1-06 | ACI-RHP-DIAG — Diagnostic threshold mismatch (cosmetic) | Low | 9 | Maintenance |
| P1-07 | AXA-FLEX-PROP — Fund_Nature policy for real-estate fund | Low | 10 | Policy decision |

---

### Section 4 — P2 Metrics Pipeline (`#p2`)

10 items covering `run_pipeline.py`, `rolling_stats.py`, `db_readers.py`, `regime_classifier.py`, `macro_discovery.py`, `metrics_writer.py`, and the Postgres ETL/DDL layer. Includes 2 closed items (P2-01, P2-02).

**Additional structure element:** `Root Cause Chain` — numbered chain steps showing systemic bug propagation path (used for items like P2-01).

**Current state (2026-08-01 · v7):**

| Item | Title | Level | Ord | Gate type |
|---|---|---|---|---|
| P2-01 ✓ | BUG-SIGNAL-DIAG — ISIN mismatch zero-signal bug | Closed | — | Fixed v6 (commit 8c3806a) |
| P2-02 ✓ | ROLL-CATEGORY-RERUN — CALC_VERSION bump + full run | Closed | — | Done v6 (commit 6d06a05) |
| P2-03 | REGIME-THRESHOLD — Recalentamiento structural absence | Medium | 4 | Option B accepted; document P3 fallback only |
| P2-04 | P2-PREFLIGHT — Abort when no new NAV | High | 2 | Hardening (independent) |
| P2-05 | P2-EARLY-EXIT — Skip rolling snapshot at n_recomputed==0 | High | 3 | Hardening (independent) |
| P2-06 ✓ | P2-ZERO-WARN — Superseded by P2-12 | Closed | — | Subsumed into P2-12 (v6) |
| P2-07 | P2-SPREAD-IG — Source FRED BAMLC0A0CM | Medium | 6 | Data acquisition |
| P2-08 | ROLL-POSTGRES-DDL — One-time Postgres DDL | Medium | 7 | Infrastructure (gates BI-01); now executable |
| P2-09 | ROLL-P5-ETL — Incremental append for ETL | Low | 11 | Performance (depends P2-08) |
| P2-10 | ROLL-P5-P3 — Wire rolling percentile signals into P3 Layer 3 | Low | 12 | Feature (kill-switched; earliest 2027-07) |
| P2-11 | OLS-COVERAGE-DIAG — Low-coverage OLS factors | Low | 13 | Diagnostic (independent) |
| P2-12 | P2-OBSERVABILITY — Structured run log + exit codes | Low | 14 | Hardening (subsumes P2-06) |

---

### Section 5 — P3 Scoring (`#p3`)

4 proposed new metrics derivable from existing OLS output or regime history in `fund_metrics`. All gate on P2-02 confirmed clean.

| Item | Title | Level | Ord | Formula |
|---|---|---|---|---|
| P3-01 | NEW-METRIC-COVERAGE — `regime_coverage_ratio` | Medium | 18 | Fraction of 7 regimes with ≥12 months per-fund returns |
| P3-02 | NEW-METRIC-CRISIS — `crisis_stress_score` | Medium | 19 | Drawdown+recovery over Crisis_Financiera months only |
| P3-03 | NEW-METRIC-OIL — `energy_sensitivity_pct` | Low | 20 | `beta_oil × 0.25` (+25% WTI shock scenario) |
| P3-04 | NEW-METRIC-HY — `hy_spread_sensitivity_pct` | Low | 21 | `beta_spread_hy × 3.0` (300bps HY widening scenario) |

---

### Section 6 — BI Layer (`#bi`)

3 items covering Superset datasets, monitoring metrics, and OLS completeness logging.

| Item | Title | Level | Ord | Dependency |
|---|---|---|---|---|
| BI-01 | ROLL-P4-SUPERSET — Register datasets + rolling charts | Medium | 22 | Gates on P2-08 |
| BI-02 | NEW-METRIC-FRESH — `nav_data_freshness_delta` | Low | 23 | Independent |
| BI-03 | NEW-METRIC-MACRO — `macro_factor_coverage_pct` in pipeline log | Low | 24 | Independent |

---

### Section 7 — Resolved (`#resolved`)

Immutable archive of closed and superseded items. **Do not reopen or modify.**

Protocol: §7 shows only items closed in the current version. Prior closures are erased per-version.

**v7 closures (2026-08-01):**

| Item | Resolution |
|---|---|
| P1-03 | P2-02 clean run confirms SC-H2 inconsistency on 3 ARCANO funds is a P2 data gap, not a P1 error. |
| FIX-ALTRV-TBILL-1 | "treasury bill" added to `has_cash_bench` patterns (commit 0734f56). |
| FIX-ALTRV-HEDGEFUND-1 | "fondos de inversión libre" + `_ar_in_pre_header` guard (commit 0734f56). |
| FIX-BL44-RFC-SRRI4-1 | BL-44 RFCP threshold ≥4 → ≥5; preserves EM credit at SRRI=4 (commit 0734f56). |
| ROLLING-DASH-1 | Bounded Tier 1/2 queries + Chart.js 4.4.0 vendored; 8.3× speedup; 32 R-7 tests (27b66c0+53655f0). |

**v6 closures (2026-07-31, for reference):**

| Item | Resolution |
|---|---|
| P2-01 | INNER JOIN fix in `db_readers.py` (commit 8c3806a). 68K+ category signal rows confirmed. |
| P2-02 | `CALC_VERSION="20260730"`, 3,197 ISINs, 1,006,988 metric rows, 0 errors (commit 6d06a05). |
| P1-02 | FIX-P1-RFC-TRES-1 + FIX-P1-RFF-OVERRIDE-1; 35 RFC→RFF changes (commit f0bc328). |
| FIX-MMF-COMMODITY-OVERLAY-1 | `_commodity_overlay` guard prevents synthetic commodity funds from firing MMF marker (commit 4b73d93). |
| P2-06 | Superseded by P2-12 (P2-OBSERVABILITY, broader structured spec). |
| RX-01–RX-03 | Audit P0 items superseded by DB audit 2026-07-30 (spread_hy/oil_wti fully populated; Crisis/Shock regimes correct). |

---

## 3. Operational Update Framework

### 3.1 Update Triggers

| Trigger | Required Update |
|---|---|
| **P1 pipeline run** (`P1_discoverAllFunds.bat` or `run_block.py`) | Close P1-02 if RF diff is clean. Close P1-01 if full corpus diff passes. Update Sources in run strip. |
| **P2 pipeline run** (`P2_calculateIndicators.bat`) | Update `Last Updated`, `CALC_VERSION` reference, run ID, and row counts. If P0 resolves: move to §7 Resolved with commit hash. |
| **DB audit run** | Update Verdict Row panels with confirmed counts. Update or close RX-series items. |
| **Policy decision made** | Replace `Decision Required` block with `Fix` block. Record decision rationale. Decrement Policy Gates count in run strip. |
| **Skill invocation** (`/pipelineP1Audit`, `/pipelineP2Audit`) | Add sourced commit hash to Sources. Add or update affected item blocks based on audit output. |
| **New issue discovered** | Add Matrix row + full issue block in domain section. Update KPI strip counts. |

---

### 3.2 SOP — Closing an Item

1. Mark Matrix row `resolved`: add `✓` to code, set `Level = Closed`, set `Ord = —`.
2. Add `fix-item` card in §7 Resolved with: code + title, fix summary, evidence (row counts, run ID, commit hash), date.
3. Remove item from Dependency Cascade in §1 (or mark as completed tick).
4. Decrement KPI strip counter for the item's severity tier. Increment Closed counter.
5. Update run strip: `P0 Blockers`, `Policy Gates`, `Critical Path` as needed.
6. Update top-bar `time` element to today's date and new item totals.

---

### 3.3 SOP — Adding an Item

1. Assign code: `{Domain}-{next seq}`. Domains: `P1`, `P2`, `P3`, `BI`. Use `RX-{n}` for audit-provisional items.
2. Assign `Ord`: insert after the last item it depends on; renumber downstream items if necessary.
3. Add Matrix row (Code · Block · Description · Level · Ord).
4. Add full issue block in the domain section (Root Cause + Fix + Cross-References). If policy-dependent: use `Decision Required` block instead of Fix.
5. Wire into Dependency Cascade in §1 under the correct track.
6. Update KPI strip and run strip.

---

### 3.4 SOP — Escalating or Downgrading Severity

1. Update `Level` chip in Matrix row.
2. Update item severity class (`lv-critical / lv-high / lv-medium / lv-low`) in the HTML.
3. Update KPI strip counts (subtract from old tier, add to new tier).
4. Add a note to the item's body with escalation/downgrade rationale and supporting evidence.

---

### 3.5 Immutable Rules

- **Never delete** a closed item from §7. The resolved archive is append-only.
- **Never change** closed item evidence (row counts, commit hashes) retroactively.
- **Never merge** two open items without retiring one under an `RX-{n}` code.
- **`Ord` is a global sequence** — no two open items share the same value.
- **Sources field** must cite specific audit session, commit hash, or run ID. Vague dates alone are insufficient.
- **Policy gate items** must not receive a `Fix` block or any code changes until the decision is formally recorded in the item body.
