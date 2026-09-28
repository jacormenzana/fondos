# Skill: auditStatisticalDataDistributionCostAttributes
**Description:** Statistical distribution audit of every cost attribute in `fund_master` and `fund_cost_schedule` — distributions, cross-component equality, shape moments, robust outliers, structural invariants and plausibility bounds. Detects systemic extraction failures that per-column inspection cannot surface. Produces evidence-gated root-cause fixes, never value patches.

---

## 1. Role & Constraints

**Role:** Senior Data Quality Auditor.
**Language:** English. **Tone:** Ultra-executive. **Format:** Bold headers, tables, key metrics. Zero filler.
**Non-negotiable:** P#1–P#11 and R-1..R-8 in `doc/reglas/`. Root cause only. Every correction is evidence-gated and preserved in `fund_cost_corrections` before the write — no cost datum is ever lost.

---

## 2. Scope

**`fund_master`:** `Ongoing_Charge_Recurrent`, `Entry_Fee_Pct`, `Entry_Fee_Pct_Max`, `Exit_Fee_Pct`, `Exit_Fee_Pct_Max`, `Management_Fee_Pct`, `Transaction_Cost_Pct`, `Performance_Fee_Pct`, `Performance_Fee_Basis`, `ACI_1Y`, `ACI_RHP`, `Cost_RHP_Years`.
**`fund_cost_schedule`:** `Horizon_Years`, `Is_RHP`, `Total_Costs_EUR`, `Total_Costs_Pct`, `Annual_Impact_Pct`.
**Filter:** `In_Current_Universe = 1` always. `--isin <csv>` scopes any run to a fixed ISIN list (e.g. the 40-ISIN validation sample) instead of the full universe.

---

## 3. The Seven Blocks — run ALL, in order

Blocks 1–2 are the highest-yield and must never be dropped: **the cross-component matrix (Block 2) found a 663-fund defect that appears in no distribution table.**

**Reference:** `python scripts/audit/run_statistical_audit.py --list-catalog --domain costs` prints every active rule (declarative catalog + procedural) with its block, tolerance/bound, and the retired-rules registry — no DB connection. Run it before editing this file; the tables below can drift from the catalogs (see `scripts/audit/check_audit_skill_sync.py`).

### Block 1 — Distributions (per column)
`n · null% · min · p50 · p95 · max · zero% · mode · mode%`
- **mode% > 40%** → a template or a bleed, not a fee population.
- **max implausible for the column's scale** → scale error (see Block 7).
- **zero% > 70%** → zero-inflated; excludes the column from Block 4 robust-z.

### Block 2 — Cross-component value equality *(mis-binding signature)*
Every unordered pair of the 7 percent-scale columns, counting `|a−b| < 0.005 AND a > 0`. Report any pair with n ≥ 8.
- Two different cost concepts holding an identical value is a **binding failure**, not a coincidence.
- Historic hits: `Exit == Management_Fee` 663 · `ACI_1Y == ACI_RHP` 879 · `Management_Fee == Transaction_Cost` 71.

### Block 3 — Shape & dispersion
`mean · sd · CV · skewness · kurtosis`
- **|skew| > 3 or kurtosis > 10** → pathological; a scale or contamination class is present.
- Benchmark: `Ongoing_Charge_Recurrent` at skew 12.72 / kurtosis 271.23 resolved to 0.75 / 1.07 after 8 corrections.

### Block 4 — Robust outliers
IQR fence (1.5×) and MAD robust-z (> 3.5, extremes > 5).
- **Mandatory guard:** skip any column with zero% > 70 — MAD is 0 there and every non-zero value reports as an outlier. Those counts are artifacts, not signal.

### Block 5 — Structural invariants *(arithmetic, not heuristic)*
| Invariant | Violation means |
|---|---|
| `ACI_RHP ≤ ACI_1Y` | amortisation inverted |
| `ACI_1Y ≠ ACI_RHP` when RHP>1y and entry>0 | entry cost must amortise |
| `Management_Fee ≤ ACI_RHP` | component exceeds total |
| `Transaction_Cost ≤ ACI_RHP` | component exceeds total |
| `Annual_Impact_Pct ≤ Total_Costs_Pct` (same horizon) | annual exceeds accumulated |
| `Annual_Impact = Total_Costs_Pct` at horizon 1y | equal by definition |
| `Ongoing_Charge × 100 ≠ ACI_RHP` | OC contaminated with the ACI |

### Block 6 — `fund_cost_schedule` integrity
**Implemented 2026-09-28 (`_run_cost_schedule_integrity`) — this block was pure specification until then; the five checks below now run as `SCHEDULE_*` findings.**
- `Total_Costs_EUR` vs `Total_Costs_Pct` coherence (EUR/100 on a 10 000 base) — `SCHEDULE_EUR_PCT_COHERENCE`, tolerance `KID_ROUNDING_TOLERANCE_PP`.
- `Total_Costs_EUR < 20` → misparse (thousands separator), **except** genuine sub-1-year horizons — `SCHEDULE_EUR_MISPARSE`.
- Schedule `Is_RHP=1` row vs `fund_master.ACI_RHP` agreement — `SCHEDULE_RHP_ACI_MISMATCH`. The comparand is `Annual_Impact_Pct` specifically (confirmed against `fund_writer.py`/`priips_cost_extractor.py`: `ACI_RHP` is derived from the `Is_RHP=1` row's `Annual_Impact_Pct` by construction, not `Total_Costs_Pct`), tolerance `KID_ROUNDING_TOLERANCE_PP`.
- `ACI_RHP` set with no `Is_RHP` row — `SCHEDULE_ACI_RHP_ORPHAN`; multiple `Is_RHP=1` rows — `SCHEDULE_MULTIPLE_RHP_ROWS` (no `UNIQUE` constraint enforces one-per-ISIN; `idx_cost_schedule_rhp` is a plain partial index).
- **Function #17 — group-constancy detector, previously undocumented here:** `Total_Costs_Pct`/`Total_Costs_EUR` duplicated identically across a fund's different `Horizon_Years` rows is a distinct defect class (a 447-fund/264-residual-row real finding, `AUDITORIA_ESTADISTICA.md` §2.7) — `TOTAL_COSTS_PCT_CONSTANT_ACROSS_HORIZONS` / `TOTAL_COSTS_EUR_CONSTANT_ACROSS_HORIZONS`, `HARD_INVARIANT`, `shared/statistical_audit/catalog_group_checks.py`. Detection only, no repair.

### Block 7 — Plausibility bounds & scale
| Column | Scale | Plausible |
|---|---|---|
| `Ongoing_Charge_Recurrent` | **decimal ratio** | 0.0001–0.06 |
| `Entry_Fee_Pct` / `Exit_Fee_Pct` | **decimal ratio** | 0–0.10 |
| `*_Pct_Max`, `ACI_*`, `Management_Fee_Pct` | **integer percent** | 0–25 |
| `Transaction_Cost_Pct` | integer percent | 0–5 |
- The ratio/percent split across same-named columns is the standing scale trap. Verify before comparing.
- **Dual bound layer (previously undocumented here):** the table above is the review-trigger *plausibility* range. Most columns also carry a separate, stricter DDL *hard* bound (a live `CHECK` constraint, e.g. `Management_Fee_Pct` hard-capped at 10 vs. plausibility 0–25; `ACI_1Y` hard-capped at 50 vs. plausibility 0–25) — both layers are checked independently and can each produce their own finding. `shared/statistical_audit/catalog_cost_columns.py` (`CostColumnSpec.hard_bound`/`.plausibility_bound`) is the single source for both; `--list-catalog` prints them side by side.

---

## 4. Mandatory Method Controls

Violating any of these has produced a wrong conclusion in this codebase before.

1. **Measure a fix by A/B against the same code with the fix disabled** — never DB-vs-fresh-extraction. The latter conflates the change with pre-existing drift (once produced a fabricated 1,209-fund "impact").
2. **A "lost value" count is not a loss.** P#1 COALESCE preserves absent keys. Verify by reprocessing a small mixed batch and reading the DB back.
3. **Normalise text to NFC before any regex.** 144 KIDs mix precomposed and decomposed accents; every accented pattern fails silently on the decomposed form.
4. **Decide which side is wrong before correcting.** An invariant violation does not say which operand is at fault — check plausibility of both. A guard that assumed the annual figure was wrong would have destroyed 192 correct values where the *total* was misparsed.
5. **A green test suite is not proof.** Always apply to the corpus; the suite passed while the pipeline died on a missing import.
6. **Prefer evidence over magnitude.** Bind by normative description text, never by proximity or by distance thresholds.
7. **Verify against source documents** before closing a category. Never conclude "correct NULL" from an implausible value alone.

---

## 5. Output Format

1. **Distribution table** (Block 1) — all columns, always.
2. **Cross-component equality matrix** (Block 2) — all pairs ≥ 8.
3. **Shape & dispersion table** (Block 3).
4. **Outlier table** (Block 4) with zero-inflation exclusions named.
5. **Invariant violation counts** (Blocks 5–6), before → after.
6. **Corrections applied** — count, evidence, preserved rows.
7. **Residual inventory** — open counts by class.
8. **Action items** — ranked, immediate.

---

## 6. Apply & Preserve

- Reprocess with `run_block.py --nature-first --master-db --recompute-costs --list-isin <csv>` (cache-only, no downloads — see `reference_cost_recompute_cached`).
- **Always** insert the prior value into `fund_cost_corrections` (ISIN, column, old, new, reason, evidence) **before** the write.
- Re-run Blocks 1–6 after applying and report before → after for every metric.
