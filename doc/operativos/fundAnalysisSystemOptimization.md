### 1. SYSTEM OVERVIEW
* 3-layer Fund Analysis System organized as proyecto1, proyecto2, proyecto3.
* proyecto1 is foundation layer, proyecto2 is processing layer, proyecto3 is analytic/presentation layer.
* Cross-cutting functionality: centralized data ingestion, shared validation, common security controls, unified audit/logging, orchestration and deployment governance.

### 2. INTRA-LAYER ANALYSIS
#### proyecto1
* **Profile:**
  * Core System Purpose: source and normalize fund data.
  * Current Maturity Level: foundational ingestion and validation.
* **Current State:**
  * Deployed functionalities: raw data capture, schema enforcement, canonical staging.
  * Supporting modules implementing each feature: ingestion adapters, validation engine, staging repository.
* **Optimization Roadmap:**
  * Refactoring Targets: remove redundant adapters, consolidate validation rules, redesign staging layer for idempotency.
  * Target Features: develop centralized metadata registry, implement reusable transformation library, add automated error classification.

#### proyecto2
* **Profile:**
  * Core System Purpose: compute risk and performance metrics.
  * Current Maturity Level: intermediate analytics and aggregation.
* **Current State:**
  * Deployed functionalities: calculation engine, enrichment pipelines, metric aggregation.
  * Supporting modules implementing each feature: processing workflows, enrichment services, metric datastore.
* **Optimization Roadmap:**
  * Refactoring Targets: update workflow orchestration, remove batch-only execution paths, redesign metric caching.
  * Target Features: develop incremental computation, add deterministic replay, implement service-level telemetry.

#### proyecto3
* **Profile:**
  * Core System Purpose: deliver fund analysis, scoring, portfolio construction, and reporting.
  * Current Maturity Level: scoring engine active; dashboard/alerting layer planned (not deployed).
* **Current State:**
  * Deployed functionalities: regime classification (7 regimes), fund scoring (3-layer: hard filters + base score + regime multipliers), portfolio construction (3 sub-portfolios), static Excel monthly report.
  * Supporting modules: `regime_classifier.py`, `fund_scorer.py`, `portfolio_builder.py`, `backtesting.py`, `monthly_report.py`.
  * **NOT deployed (planned):** dashboards, WARN/ALARM alerting. The P2 rolling-indicators workstream (v26, 2026-07-27) is building this layer — see `doc/backlog/P2/EXECUTIVE_SUMMARY_rolling_indicators_20260727.md`.
* **BI Layer (v26 — in progress):**
  * `fund_metric_timeseries` + `fund_metric_alerts` tables now exist in SQLite and are auto-synced to PostgreSQL Docker (`fondos`, port 5433) via `shared/load_fondos_to_postgres.py` and the new `scripts/launch/P4_syncToPostgres.bat` launcher.
  * Static HTML dashboard: `proyecto2/src/reports/rolling_dashboard.py`.
  * Target: Apache Superset reading the existing Postgres — datasets to register: `fund_metric_timeseries` (long format), `fund_metric_alerts`, `fund_master` (dimension join).
  * Kill-switch: `ROLLING_STATS_ENABLED=False` (activate after first backfill run).
* **Optimization Roadmap:**
  * Refactoring Targets: incremental ETL for `fund_metric_timeseries` (currently full-replace), walk-forward backtest of rolling-percentile scoring signals.
  * Target Features: Superset dashboards (rolling-vol charts, alarm heatmap, regime timeline), P3 scoring enriched with rolling percentiles + alarm-fed regime transitions (kill-switched: `SHORT_HORIZON_SCORING_ENABLED` pattern).

### 3. INTER-LAYER ANALYSIS (Interfaces)
* **Current State:**
  * Existing integration points and data flows between layers: proyecto1 feeds normalized data to proyecto2; proyecto2 publishes metrics consumed by proyecto3.
* **Gap Analysis & Technical Debt:**
  * Identified interface bottlenecks and structural gaps: weak contract enforcement, manual handoffs, duplicated transformation logic.
* **Execution Roadmap:**
  * Required modifications to optimize upper-layer operations leveraging lower-layer capabilities: enforce strict schemas, automate handoff pipelines, expose reusable APIs from proyecto1 and proyecto2.
  * Target Outcomes: Increased reliability, execution efficiency, and maximized data exploitation ROI: reduce failure rate, accelerate processing, enable consistent analytics delivery.

