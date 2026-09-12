# Skill: auditStatisticalDataDistributionP2Metrics
**Description:** Statistical distribution audit of every quantitative metric and indicator in `fund_metrics` and `fund_metric_timeseries` — distributions, cross-series equality, shape moments, robust outliers, structural invariants, and plausibility bounds. Detects systemic calculation and binding failures that per-metric range checks cannot surface. Produces evidence-gated root-cause fixes + forced recompute, never ad-hoc value patches.

---

## 1. Role & Constraints

**Role:** Senior Data Quality Auditor & AI Engineer.
**Language:** English. **Tone:** Ultra-executive. **Format:** Bold headers, tables, key metrics. Zero filler.
**Non-negotiable:** P#1–P#11 and R-1..R-8 in `doc/reglas/`. Root cause only. Fix the **calculation or writer module**; never write ad-hoc SQL to `fund_metrics` or `fund_metric_timeseries` (P#2/P#7). P2 metrics are deterministic functions of NAV + macro and fully reproducible — there is no corrections preservation table; remediation is root-cause fix + forced recompute.
**Complementarity boundary:** `pipelineP2Audit` / `pipelineP1P2Audit` own *operational* reliability (per-metric range spot-checks, NULL coverage, staleness >60d, provenance/cohort, coverage-delta). This skill owns *statistical shape*: moments, robust outlier fences, cross-metric equality, mode/zero-inflation, plausibility/scale. Do not duplicate those operational checks here.

---

## 2. Scope

**`fund_metrics.value`** — latest scalars. Grouping key: `(metric, horizon, real_flag, metric_version)`.
Canonical metric families (v29 code names — not the stale schema comments): risk (`max_dd`, `vol_ann`, `return_ann`, `sharpe`, `sortino`, `drawdown_duration`, `time_to_recovery`, `ret_vol_simple`), `srri_nav` / `srri_volatility`, consistency (`pct_positive_months`, `pct_negative_months`, `pct_severe_loss_months`, `worst_month`), captures (`upside_capture`, `downside_capture`, `capture_ratio`), momentum (`momentum_1y`, `momentum_3y`, `momentum_rank`), persistence (`alpha_persistence`, `alpha_persistence_n`), short-horizon (`metric_version='d1'`), currency (`fx_contribution_ann`, `fx_contribution_pct`, `fx_volatility_ann`), macro (`beta_*`, `macro_r2`, `macro_alpha`, `macro_n_obs`, `energy_sensitivity_pct`, `hy_spread_sensitivity_pct`), regime (`return_ann_{suffix}`, `vol_ann_{suffix}`, `sharpe_{suffix}`, `sortino_{suffix}`, `max_dd_{suffix}`, `n_obs_{suffix}` for 7 regime suffixes; `regime_coverage_ratio`, `crisis_stress_score_mdd`, `crisis_stress_score_ttr`).

**`fund_metric_timeseries.value`** — 5 curated rolling series: `vol_ann`, `max_dd`, `return_ann`, `sharpe`, `sortino` × windows (`rolling_1y/2y/3y/5y/10y`, `rolling_1m/3m/6m`) × `real_flag`. Grouping key: `(metric, window, real_flag)`.

**Filter:** `fund_master.In_Current_Universe = 1` (JOIN on ISIN) always.

**Time-dimension rule — mandatory, prevents i.i.d.-violation false positives.**
Cross-sectional blocks (1, 2, 3, 4, 5, 7) operate on a **single snapshot: the latest row per `(ISIN, metric, window, real_flag)` from `fund_metric_timeseries`** — `MAX(date)` per group. Aggregating skew/kurtosis/outliers across multiple historical dates conflates cross-fund dispersion with within-fund autocorrelation and volatility clustering, fabricating extreme moments — forbidden. This matches the `latest_df` contract of the existing `compute_category_snapshot` (reuse it). `fund_metrics` is already latest-scalar so the rule only binds on `fund_metric_timeseries`. **Within-fund temporal analysis (date monotonicity, source_rows coverage) is confined to Block 6.**

**Snapshot staleness & tolerance gate.**
Per-ISIN `MAX(date)` values may differ across funds (settlement lag). Report the date spread. Apply a max-spread tolerance anchored on the universe `MAX(date)` with a **calendar-day** threshold (default ~5 days — one Pandas date diff, no business-calendar or holiday logic). Funds beyond the tolerance are **held out of cross-sectional blocks and listed separately**, never silently dropped. Reuses `db_readers.count_stale_nav_funds` concept; tolerance is a review parameter.

**Snapshot query performance.**
The `MAX(date)` per-group extraction must ride the covering index `idx_fmts_isin_metric_window_real_date (isin, metric, window, real_flag, date)` — trailing `date` column makes it an index scan. Use a correlated-MAX or window-function form; never full-scan the ~16.6M-row table. Confirm with `EXPLAIN QUERY PLAN`.

**Memory envelope.**
After the snapshot filter the cross-section is one row per `(ISIN, metric, window, real_flag)` — order ~10⁵ rows. Blocks 2/3/4 run on that resident frame. Only the extraction read is chunked from `fund_metric_timeseries`. SQL does grouping/filtering; Blocks 1/3/4 moments are computed in Python/Pandas under the `des` env (see Method Control #7).

---

## 3. The Seven Blocks — run ALL, in order

Blocks 1–2 are highest-yield and must never be dropped: **the cross-series equality matrix (Block 2) is the only mechanism that surfaces systematic deflation failures and binding defects — they are invisible in per-metric distribution tables.**

### Block 1 — Distributions (per group)
`n · null% · min · p50 · p95 · max · zero% · mode · mode%`
- **null% alone is not a defect** — VIF-pruned betas, sparse regimes (`MIN_OBS_REGIME=12`, ~5/7 populate → coverage 0.714), and min-obs gaps are correctly NULL by design. Flag instead: **coverage cliff vs prior run** (>2% drop signals upstream NAV loss / calc regression — reuses `db_readers.coverage_snapshot`) and **mode% > 40%** (template value or bleed, not a valid population).
- **max implausible for the family's scale** → scale error (see Block 7).
- **zero% > 70%** → zero-inflated; exclude from Block 4 robust-z (MAD = 0 there; every non-zero value would report as an outlier — an artifact, not a signal).

### Block 2 — Cross-series equality *(mis-binding signature)*
For each pair below, count `|a − b| < ε AND both present`. Report any pair with n ≥ 8.
Two different metric concepts holding an identical value is a **calculation or binding failure**, not coincidence.

| Pair | ε | Defect if n ≥ 8 |
|---|---|---|
| `value(real_flag=0)` vs `value(real_flag=1)` — same metric, horizon/window | 0.0001 | **Deflation not applied** (IPC exists but real == nominal) |
| `sharpe` vs `sortino` — same group | 0.0001 | Sortino downside-dev collapsed to total vol |
| `upside_capture` vs `downside_capture` — same group | 0.001 | Capture denominator defect |
| `fund_metrics` latest scalar vs latest `fund_metric_timeseries` row — 5 shared metrics | 0.0001 | Snapshot/timeseries divergence (write-path inconsistency) |
| `vol_ann` vs `srri_volatility` — same ISIN, `since_inception` | 0.0001 | SRRI pipeline consumed stale vol |

### Block 3 — Shape & dispersion
`mean · sd · CV · skewness · kurtosis` per group (computed in Python — SQLite has no aggregate for these).
- **|skew| > 3 or kurtosis > 10** → pathological; a scale contamination or NAV anomaly class survived the upstream guards.
- Regime return families expect high kurtosis across regimes by design (few obs per regime); flag only non-regime groups.

### Block 4 — Robust outliers
IQR fence (1.5×) and MAD robust-z (> 3.5; extremes > 5).
- **Mandatory guard:** skip zero% > 70 groups (MAD = 0 → every non-zero is a false positive).
- **Cross-check hits against `fund_metric_alerts`** (percentile alarm engine via `ALERT_RULES`): report only outliers that the operational alarm engine did **not** already surface — these are the incremental audit findings. Reuse `compute_alerts` and `ALERT_RULES` from `shared/config.py`; do not re-implement.

### Block 5 — Structural invariants *(arithmetic, not heuristic)*
| Invariant | Violation means |
|---|---|
| `max_dd ∈ [−1, 0]` | Drawdown sign or scale error |
| `vol_ann ≥ 0`, `srri_volatility ≥ 0` | Negative dispersion — impossible |
| `srri_nav ∈ {0, 1, 2, 3, 4, 5, 6, 7}` (integer) | Bucket out of range |
| `pct_positive_months + pct_negative_months ≤ 1` | Month-share overflow |
| `vol_ann > 0` when `return_ann ≠ 0` (horizon > 1m) | Frozen/flat NAV → Sharpe/Sortino → ∞; complements `STALE_FROZEN` |
| `sortino ≥ sharpe` (same group; downside ⊆ total vol) | Downside-dev defect |
| `\|upside_capture\| ≤ 5`, `\|downside_capture\| ≤ 5`, `capture_ratio` finite | Clamp bound breached |
| `macro_r2 ∈ [0, 1]` | R² clamp breached |
| `return_ann(real_flag=1) ≤ return_ann(real_flag=0)` when IPC > 0 | Deflation inverted |
| `\|sharpe\| < 10`, `\|sortino\| < 10` | Risk-free or vol denominator defect |
| `n_obs_{suffix} ≥ 0` integer | Observation count negative |

### Block 6 — `fund_metric_timeseries` integrity *(temporal — within-fund)*
- Snapshot-vs-series agreement for the 5 shared metrics: `fund_metrics` latest scalar must equal `fund_metric_timeseries` latest `(ISIN, metric, window, real_flag)` row (flag divergence; also signals missed `_write_timeseries` call).
- `source_rows ≥ window min-obs` where value is non-NULL; NULL where min-obs unmet — consistent with `ROLLING_WINDOWS` (months) in `shared/config.py`.
- Date monotonicity and gap-free coverage per `(ISIN, metric, window, real_flag)`.
- `algorithm_version` / `batch_id` populated (first-insert provenance; `INSERT OR IGNORE` preserves them — a NULL means the row predates v26 audit columns).
- Orphan `beta_*` rows: `_replace_beta_set` does an atomic DELETE+INSERT; flag any beta row whose `(metric, isin)` combination appears with a `batch_id` not matching the fund's latest P2 run — stale betas from a superseded OLS run.

### Block 7 — Plausibility bounds & scale
| Family | Plausible range |
|---|---|
| `vol_ann`, `srri_volatility` | 0 – 5.0 (`_VOL_SANITY_CAP`; > 5 → `ANOMALOUS_VOL` sentinel) |
| `max_dd` | −1.0 – 0 |
| `return_ann` | ≈ −0.9 – 3.0 |
| `sharpe`, `sortino` | ≈ −10 – 10 |
| `srri_nav` | 0 – 7 |
| `upside_capture`, `downside_capture`, `capture_ratio` | −5 – 5 |
| `macro_r2` | 0 – 1 |
| `beta_*` | Family-specific; VIF > 10 pruned to NULL by design |
| Momentum, persistence, pct-months | 0 – 1 |

**Bounds are review triggers, never auto-clamps.** Leveraged/thematic funds and hyperinflationary macroeconomic regimes can legitimately breach `return_ann`/`|sharpe|` limits. Investigate against NAV series and macro inputs before correcting. Method Controls "decide which operand is wrong" and "verify against source" govern every correction.

**Crisis-window carve-out — config-driven, no skill maintenance.** Block-7 breaches under a `crisis_` horizon prefix (e.g. `crisis_2008`, `crisis_2011`, `crisis_2020`, `crisis_2022`) are routed to a **separate "expected under regime" bucket** and never escalated to the action list — severe declines and compressed volatility legitimately push captures/Sharpe/returns beyond static thresholds. The carve-out matches the `crisis_` **prefix**, not a hard-coded year list; future crisis windows added to `CRISIS_WINDOWS` in `shared/config.py` flow through automatically.

---

## 4. Mandatory Method Controls

Violating any of these has produced a wrong conclusion in this codebase before.

1. **Measure a fix by A/B against the same code with the fix disabled** — never DB-vs-fresh recompute. The latter conflates the change with pre-existing drift (the cost-audit sibling demonstrated a fabricated 1,209-fund "impact" this way).
2. **NULL is designed, not lost.** Graceful degradation (P#1/R-4): VIF-pruned betas, absent regimes, and min-obs gaps yield correct NULLs. Never "correct" a NULL from an implausible-value assumption alone — verify against source NAV and macro series first.
3. **Idempotency-hash gate — verify the recompute actually happened.** `fund_metric_state.input_hash` makes an un-forced re-run a no-op. Before accepting any A/B result, assert that affected ISINs' `input_hash` (or `calculated_at`) **changed** post-run. An unchanged hash means the idempotency cache bypassed processing — the "after" numbers are stale and the A/B is void.
4. **Decide which operand is wrong before correcting** an invariant violation. The violation says *that* an arithmetic relationship fails, not *which* side is at fault. Check plausibility of both operands.
5. **A green test suite is not proof.** Always apply findings to the full corpus; the suite has passed while a pipeline crashed on a missing import.
6. **Prefer evidence over magnitude.** Confirm binding defects from the calculation/writer code path, not from proximity or distance thresholds in the data.
7. **Compute moments in Python/Pandas under the `des` Conda env, not SQL.** SQLite has no aggregate for percentiles, median, skewness, or kurtosis. Extract filtered, grouped pulls into DataFrames in the repo's `des` environment (concrete local path: `C:\data\envs\des\python.exe`); compute distribution statistics there. Read `fund_metric_timeseries` chunked and pre-filtered to the latest snapshot (see Scope time-rule) — after filtering, the cross-section is ~10⁵ rows, so Blocks 2/3/4 run on a small resident frame; only extraction is chunked, keeping OOM out of reach in the `des` env.

---

## 5. Output Format

1. **Snapshot header** — universe size, date spread, funds held out by staleness tolerance.
2. **Distribution table** (Block 1) — all groups, always.
3. **Cross-series equality matrix** (Block 2) — all pairs with n ≥ 8.
4. **Shape & dispersion table** (Block 3).
5. **Outlier table** (Block 4) — with zero-inflation exclusions named and overlap with `fund_metric_alerts` removed.
6. **Invariant violation counts** (Block 5) and **timeseries integrity findings** (Block 6) — before → after.
7. **Corrections applied** — count, root-cause module, A/B delta, hash change confirmed.
8. **Residual inventory** — open counts by class (crisis-window bucket listed separately).
9. **Action items** — ranked, immediate.

---

## 6. Apply & Recompute

- Fix the **calculation or writer module** at root cause. Never write ad-hoc SQL on `fund_metrics`/`fund_metric_timeseries` (P#2/P#7).
- Reprocess affected ISINs: `python -X utf8 -m proyecto2.src.pipeline.run_pipeline --isin <csv>` (targeted) or bump `CALC_VERSION` in `run_pipeline.py` and run the full pipeline for a global recompute.
- **Confirm the recompute happened** (Method Control #3): assert `fund_metric_state.input_hash` changed for each affected ISIN before trusting the A/B result.
- No preservation table — every metric is reproducible from `fund_nav_monthly`/`fund_nav_daily` + `series_macro`.
- Re-run Blocks 1–6 after applying and report **before → after** for every metric group.
