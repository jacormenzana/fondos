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
**Filter:** `In_Current_Universe = 1` always.

---

## 3. The Seven Blocks — run ALL, in order

Blocks 1–2 are the highest-yield and must never be dropped: **the cross-component matrix (Block 2) found a 663-fund defect that appears in no distribution table.**

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
- `Total_Costs_EUR` vs `Total_Costs_Pct` coherence (EUR/100 on a 10 000 base).
- `Total_Costs_EUR < 20` → misparse (thousands separator), **except** genuine sub-1-year horizons.
- Schedule `Is_RHP=1` row vs `fund_master.ACI_RHP` agreement.
- `ACI_RHP` set with no `Is_RHP` row; multiple `Is_RHP=1` rows.

### Block 7 — Plausibility bounds & scale
| Column | Scale | Plausible |
|---|---|---|
| `Ongoing_Charge_Recurrent` | **decimal ratio** | 0.0001–0.06 |
| `Entry_Fee_Pct` / `Exit_Fee_Pct` | **decimal ratio** | 0–0.10 |
| `*_Pct_Max`, `ACI_*`, `Management_Fee_Pct` | **integer percent** | 0–25 |
| `Transaction_Cost_Pct` | integer percent | 0–5 |
- The ratio/percent split across same-named columns is the standing scale trap. Verify before comparing.

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
