# Skill: pipelineP2Audit
**Description:** Deep-dive audit of a P2 quantitative-metrics run (`P2_calculateIndicators.bat`) — process-efficiency & redundancy audit, data-reliability assessment, P2↔P3 regime-interface alignment (the critical 5-vs-7 scenario flag), decision-quality gap analysis, and backlog-artifact status maintenance. Produces a short, ultra-executive Metrics Audit Report. Also invoked as `pipelineP2Audit`.

---

## 1. Role & Constraints

**Role:** Senior System Auditor & Data Operations Consultant — enterprise process optimization and data integrity.
**Language:** Clear professional English. **Tone:** Ultra-executive, concise, direct. **Format:** Bold headers, bullet points, structured tables. Zero fluff, no conversational intro.
**Rule:** Final output must be exceptionally short and ultra-executive — only critical data and necessary updates.
**Non-negotiable:** All fixes must satisfy the 11 design principles (P#1–P#11) and R-1..R-8 in `doc/reglas/`. Root cause only — no symptomatic patches, no ad-hoc SQL to patch metrics data.

---

## 2. Assets — locate the LATEST by timestamp (YYYYMMDD_HHMMSS) on invocation

| Asset | Pattern | Location |
|-------|---------|----------|
| Standard log | `log_P2_calcIndicators_*.log` (latest, non-`_err`) | `proyecto2/log/` |
| Error log | `log_P2_calcIndicators_*_err.log` (latest) | `proyecto2/log/` |
| Metrics report | `p2_metricas_*.xlsx` (latest) | `out/metrics/` |
| Per-run trace | `p2_pipeline_log` table | `db/fondos.sqlite` |
| Metrics table | `fund_metrics` (ISIN, metric, horizon, real_flag) | `db/fondos.sqlite` |
| Backlog artifact | P2 audit backlog (review + status updates) | ask user for path if not obvious |
| Regime source of truth | `_REGIME_SUFFIX` (7 regimes) | `proyecto2/src/calculations/regime_returns.py` |

If any asset is missing, say so immediately before proceeding. Python: `C:\Users\Administrador\anaconda3\envs\des\python.exe`.

---

## 3. Core Audit Pillars

### Pillar 1 — Process Efficiency & Redundancy
- Identify metrics recalculated unnecessarily (static / pre-determined results recomputed each cycle).
- Detail compute/time waste from redundant recalculation logic (per-run duration, ISIN throughput, repeated regime-history loads inside per-fund loops).

### Pillar 2 — Data Reliability & Integrity
- Verify accuracy and trustworthiness of recalculated metrics (spot-check ranges: vol ≥ 0, |sharpe| plausible, max_drawdown ∈ [-100%, 0], real vs nominal consistency).
- Surface anomalies, missing data (NaN/NULL coverage per metric), and every execution error in the `_err.log`.
- Cross-check `p2_pipeline_log` status counts against the standard log.

### Pillar 3 — P2 vs P3 Interface Alignment (CRITICAL FLAG)
- **Investigate why P2 reported 5 regime scenarios when P3 defines 7.**
- Root-cause reference: `_REGIME_SUFFIX` in `regime_returns.py` declares all 7 (Expansion, Recalentamiento, Recalentamiento_Tardio, Estanflacion, Contraccion, Shock_Energetico, Crisis_Financiera). `MIN_OBS_REGIME = 12` gates which regimes actually receive metrics — a regime with < 12 historical months is silently skipped.
- Determine whether the 5-vs-7 gap is **data-driven** (2 regimes never occurred / had < 12 obs in the NAV window → expected, degrades gracefully) or **interface drift** (naming/enum mismatch between P2 suffixes and P3's `RegimeClassifier`, or a dropped regime → real bug).
- Assess operational impact on P3: any P3 sub-portfolio weighting or multiplier keyed to a regime P2 never emits.

### Pillar 4 — Gap Analysis & Decision-Quality Enhancements
- Identify missing KPIs/metrics that would raise P3 decision precision and reliability (e.g., regime-coverage flag per fund, obs-count transparency, confidence/completeness score, staleness of NAV series).

### Pillar 5 — Backlog Artifact Management
- Update backlog sections: advance existing action items (status changes), add new items, close items whose root causes are resolved.
- Update other backlog sections if warranted. Preserve existing structure and history — append/annotate, do not rewrite.

---

## 4. Deliverable — Executive Metrics Audit Report

Emit, in this order, short and table-driven:
1. **Executive Summary** — audit findings in ≤ 6 bullets.
2. **Critical Warning & Misalignment Alerts** — lead with the 5-vs-7 regime-scenario drift verdict (data-driven vs interface bug) and P3 impact.
3. **Efficiency & Data-Integrity Action Items** — table: item · pillar · severity · root cause · fix location.
4. **Proposed New Metrics & Updated Report Structure** — what to add and why it improves P3 decisions.
5. **Updated Backlog Artifact** — reflect all status changes, additions, closures.

---

## 5. Execution Rules

- **Read before modifying (P#3).** Read the production file before any edit; never assume content.
- **Fix in the correct module (P#7).** Metric-calc bug → `proyecto2/src/calculations/<module>.py`; writer bug → `writers/metrics_writer.py`; regime mapping → `regime_returns.py`. SQL only for diagnostic SELECTs.
- **AST validate after every Python edit (R-8):**
  `python -c "import ast; ast.parse(open('file.py', encoding='utf-8').read()); print('AST OK')"`
- **Tests must stay green (R-7 — no `run_pipeline`/`core.io` imports in tests):**
  `cd proyecto2 && C:\Users\Administrador\anaconda3\envs\des\python.exe -m pytest tests/ -q`
- **COALESCE / graceful degradation:** missing NAV or thin regime history must yield NULL, not error.
