-- =============================================================================
-- 30_gold.sql — Gold layer: calculated indicators, fully recomputable from Bronze+Silver
-- =============================================================================
-- Introspected directly from the live db/fondos.sqlite. Column renames per db/pg/rename_map.yaml.
--
-- fund_metric_timeseries carries the ONE partitioning decision in this migration (§P2
-- "Partitioning"). Everything else in Gold is deliberately unpartitioned.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- gold.fund_metrics — 1.6M rows. P3 reads ~3,000 ISINs at once. CLUSTER USING PK post-load.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gold.fund_metrics (
    value               double precision,
    calculation_date     date             NOT NULL,
    load_ts              timestamptz      DEFAULT now(),
    real_flag            smallint         NOT NULL DEFAULT 0 CHECK (real_flag IN (0,1)),
    source_rows           integer,
    isin                  varchar(12)      NOT NULL,
    metric                text             NOT NULL,
    horizon               text             NOT NULL,
    metric_version        text             NOT NULL DEFAULT 'v1',
    benchmark_id           text,
    algorithm_version      text,
    batch_id               text,

    CONSTRAINT fund_metrics_pkey PRIMARY KEY (isin, metric, horizon, real_flag, metric_version),
    CONSTRAINT fund_metrics_isin_fk FOREIGN KEY (isin) REFERENCES silver.fund_master (isin) ON DELETE CASCADE
);

-- idx_metrics_isin deliberately NOT recreated (PK-prefix redundant — v29 already dropped it in SQLite)
CREATE INDEX IF NOT EXISTS idx_metrics_date ON gold.fund_metrics (calculation_date);
-- Reshaped for the P3 scoring read over ~3,000 ISINs: INCLUDE makes it an index-only scan (§P2 index plan)
CREATE INDEX IF NOT EXISTS idx_metrics_scan ON gold.fund_metrics (metric, horizon, real_flag, isin)
  INCLUDE (value);

-- -----------------------------------------------------------------------------
-- gold.fund_metric_timeseries — 32.2M rows live (measured 2026-09-17, up from the 31.7M baseline).
-- PARTITION BY LIST (metric) — the ONE partitioning decision in this DB (§P2 "Partitioning").
--
-- Justification is NOT read performance (32M rows is comfortable single-table territory in PG) —
-- it is the metric-scoped bulk-delete/recompute cycle: TRUNCATE gold.fmts_p_sharpe instead of a
-- 6.3M-row DELETE + a vacuum scanning ~7GB of index. HASH(isin) and RANGE(date) were both
-- explicitly rejected — see the plan for the reasoning.
--
-- Metric list verified CLOSED against shared/config.py ROLLING_TIMESERIES_METRICS (5 metrics) and
-- the populated grain measured live: 5 metrics x 5 windows (rolling_1y/2y/3y/5y/10y), 481 distinct
-- dates, ~1,287,000 rows per (metric,window) pair — near-perfect partition balance, no skew.
-- The 3 SHORT_WINDOWS (rolling_1m/3m/6m) have ZERO rows today (SHORT_HORIZON_SCORING_ENABLED=False)
-- but stay in the CHECK constraint so flipping that kill-switch needs no migration.
--
-- `window` -> `window_label`: PostgreSQL reserved word (see rename_map.yaml).
-- Column order: fixed-width first (8->4->2), then varlena — saves ~250MB on this table alone.
-- NOT re-adding `load_ts` (lost in the live v29 rebuild) — 254MB for a column with no historical
-- data; batch_id already carries run provenance.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gold.fund_metric_timeseries (
    value              double precision,
    ref_value          double precision,
    date               date             NOT NULL,
    source_rows         integer,
    real_flag           smallint         NOT NULL DEFAULT 0 CHECK (real_flag IN (0,1)),
    isin                 varchar(12)      NOT NULL,
    metric               text             NOT NULL,
    window_label          text             NOT NULL
        CHECK (window_label IN ('rolling_1m','rolling_3m','rolling_6m',
                                 'rolling_1y','rolling_2y','rolling_3y','rolling_5y','rolling_10y')),
    ref_type              text,
    algorithm_version      text,
    batch_id               text,

    CONSTRAINT fmts_pkey PRIMARY KEY (isin, metric, window_label, date, real_flag)
) PARTITION BY LIST (metric);

-- NEITHER fillfactor NOR parallel_workers is set here on the partitioned PARENT — confirmed
-- against a real PostgreSQL 15.19 server (2026-09-19), not just inferred from docs: the parent
-- has no physical storage, and PG rejects `parallel_workers` on it outright
-- ("unrecognized parameter") the same way fillfactor is silently a no-op there. Both are applied
-- per-partition below, in the same DO loop as the autovacuum settings.

COMMENT ON TABLE gold.fund_metric_timeseries IS
  'Long-format rolling metric series. PARTITION BY LIST(metric) — 5 named + DEFAULT. FK to '
  'silver.fund_master added VALID post-seed, after the orphan pre-scan/quarantine split (§P3) — '
  'this table LOST its FK in a prior SQLite v29 rebuild, so the seed loader must not assume orphans '
  'are absent.';

-- DELIBERATELY NO FK HERE (unlike every other Gold/Bronze table's inline FK in this migration).
-- This table's FK to fund_master was lost in a prior SQLite v29 rebuild — the live source may
-- already contain orphans. Adding `REFERENCES silver.fund_master(isin)` inline here would either
-- fail immediately against a freshly-seeded table with orphaned rows, or (if added before COPY)
-- reject every orphaned row's insert with no visibility into which rows or why.
-- The seed loader instead: (1) pre-scans SQLite for orphans BEFORE any COPY runs, (2) routes them
-- to control.quarantine_fund_metric_timeseries_orphans in the same COPY pass, (3) adds this FK
-- VALID (not NOT VALID) as step 10 of the execution order, against already-orphan-free data:
--   ALTER TABLE gold.fund_metric_timeseries
--     ADD CONSTRAINT fmts_isin_fk FOREIGN KEY (isin) REFERENCES silver.fund_master (isin) ON DELETE CASCADE;
-- Do not run that statement against a table that hasn't been through the pre-scan/quarantine split
-- first — see plan §P3 "Execution order" steps 3/5/10 and §Verification gate check (d).

-- 5 named partitions + DEFAULT safety valve (§P2 "DEFAULT partition promotion runbook")
CREATE TABLE IF NOT EXISTS gold.fmts_p_vol_ann    PARTITION OF gold.fund_metric_timeseries FOR VALUES IN ('vol_ann');
CREATE TABLE IF NOT EXISTS gold.fmts_p_max_dd     PARTITION OF gold.fund_metric_timeseries FOR VALUES IN ('max_dd');
CREATE TABLE IF NOT EXISTS gold.fmts_p_return_ann PARTITION OF gold.fund_metric_timeseries FOR VALUES IN ('return_ann');
CREATE TABLE IF NOT EXISTS gold.fmts_p_sharpe     PARTITION OF gold.fund_metric_timeseries FOR VALUES IN ('sharpe');
CREATE TABLE IF NOT EXISTS gold.fmts_p_sortino    PARTITION OF gold.fund_metric_timeseries FOR VALUES IN ('sortino');
CREATE TABLE IF NOT EXISTS gold.fmts_p_default    PARTITION OF gold.fund_metric_timeseries DEFAULT;

COMMENT ON TABLE gold.fmts_p_default IS
  'Safety valve for a metric not yet promoted to a named partition. Should stay near-empty — '
  'checked by control.v_default_partition_metrics (monthly) AND by the CI test '
  'test_partition_covers_every_rolling_metric (every commit touching ROLLING_TIMESERIES_METRICS). '
  'See plan §P2 for the promotion runbook.';

-- idx_fmts_isin_metric_window_real_date dropped entirely: the PK's leading 3 columns already
-- serve isin=?/metric=?/window_label=? via prefix range scan + real_flag filter (~250-400 rows
-- total per prefix). This was redundant against its own PK in SQLite too (§P2 index plan).
--
-- Cross-sectional/BI index, reshaped as a LOCAL (per-partition) index with `metric` dropped
-- (partition pruning already supplies it) and INCLUDE added — the single highest-leverage index
-- change in this migration: turns the Superset cross-sectional scan and the peer-comparison scan
-- into index-only scans (no heap access). This is what kills the 2.5h self-join, together with
-- gold.mv_fmts_peer_stats (see 40_matviews.sql).
CREATE INDEX IF NOT EXISTS idx_fmts_bi ON gold.fund_metric_timeseries
  (window_label, real_flag, isin, date) INCLUDE (value, ref_value);

-- ETL watermark / run correlator (genuinely new, §P2 index plan)
CREATE INDEX IF NOT EXISTS idx_fmts_batch ON gold.fund_metric_timeseries (batch_id);

-- Extended statistics: metric/window_label/real_flag are strongly correlated within a partition
-- and PG would otherwise multiply selectivities independently and badly under-estimate.
CREATE STATISTICS IF NOT EXISTS stx_fmts (ndistinct, dependencies, mcv)
  ON metric, window_label, real_flag FROM gold.fund_metric_timeseries;
ALTER TABLE gold.fund_metric_timeseries ALTER COLUMN isin SET STATISTICS 1000;

-- Storage parameters (fillfactor, parallel_workers) and insert-triggered autovacuum
-- (§P2 Autovacuum), all applied per-partition in one pass — settings on the partitioned PARENT
-- are NOT inherited by physical partitions (unlike column defaults/constraints, which are);
-- PG doesn't just ignore this, it actively REJECTS some reloptions (parallel_workers, confirmed
-- against a live PG 15.19) on a relation with no physical storage. fillfactor=100 because these
-- rows are never UPDATEd. For autovacuum, what matters here is the visibility map (idx_fmts_bi's
-- index-only scans depend on it), hence insert_scale_factor is the load-bearing knob, not the
-- delete-triggered ones.
DO $$
DECLARE p text;
BEGIN
  FOREACH p IN ARRAY ARRAY['fmts_p_vol_ann','fmts_p_max_dd','fmts_p_return_ann',
                            'fmts_p_sharpe','fmts_p_sortino','fmts_p_default']
  LOOP
    EXECUTE format($f$
      ALTER TABLE gold.%I SET (
        fillfactor                            = 100,
        parallel_workers                      = 2,
        autovacuum_vacuum_scale_factor        = 0.02,
        autovacuum_vacuum_threshold           = 50000,
        autovacuum_vacuum_insert_scale_factor = 0.02,
        autovacuum_vacuum_insert_threshold    = 100000,
        autovacuum_analyze_scale_factor       = 0.01,
        autovacuum_vacuum_cost_limit          = 2000,
        autovacuum_vacuum_cost_delay          = 2
      )$f$, p);
  END LOOP;
END $$;

-- -----------------------------------------------------------------------------
-- gold.fund_metric_alerts — 14,674 rows, rolling-signal alerts
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gold.fund_metric_alerts (
    value              double precision,
    reference_value    double precision,
    detected_at         timestamptz DEFAULT now(),
    isin                 varchar(12) NOT NULL,
    metric               text        NOT NULL,
    window_label          text        NOT NULL
        CHECK (window_label IN ('rolling_1m','rolling_3m','rolling_6m',
                                 'rolling_1y','rolling_2y','rolling_3y','rolling_5y','rolling_10y')),
    level                 text        NOT NULL CHECK (level IN ('OK','WARN','ALARM')),
    rule_code             text        NOT NULL,
    ref_type              text,

    CONSTRAINT fund_metric_alerts_pkey PRIMARY KEY (isin, metric, window_label),
    CONSTRAINT fund_metric_alerts_isin_fk FOREIGN KEY (isin) REFERENCES silver.fund_master (isin) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_fma_isin ON gold.fund_metric_alerts (isin);
CREATE INDEX IF NOT EXISTS idx_fma_rule ON gold.fund_metric_alerts (rule_code);
CREATE INDEX IF NOT EXISTS idx_fma_level ON gold.fund_metric_alerts (level);

-- -----------------------------------------------------------------------------
-- gold.fund_scores — 5,884 rows, per-fund scores by sub-portfolio and regime.
--
-- SCHEMA EXTENDED 2026-09-19 for P3 Phase 3a (proyecto3 plan `i-am-sharing-an-piped-reddy.md`
-- §"Phase 3 — Persistence and keys"), coordinated across sessions via
-- memory/project_p3_scoring_regime_optimization_20260916.md, which flagged this table as
-- blocked on this DDL landing. SQLite's live PK is (isin, block, score_version) with regime
-- living only as free text inside `notes` (format: 'regime=<X>' or
-- 'regime=<X> | excluido: <metric>=<value> < <threshold> (<block>)', confirmed 100% consistent
-- across all 5,884 live rows, 2026-09-19). `INSERT OR REPLACE` against that PK silently destroys
-- the previous regime's scores every time the classifier reclassifies — this extension is the
-- fix, and the enabling prerequisite for ever removing the backtest's look-ahead-bias limitation
-- (see the P3 plan's risk register).
--
-- `regime` and `as_of_date` promote to real PK columns; `score_base`, `multiplier`,
-- `exclusion_reason` promote to real (non-PK) columns. Backfill for pre-migration rows (§P3 seed
-- loader, scripts/mig/pg_seed.py): `regime` and `exclusion_reason` parsed from `notes` (regex
-- below); `as_of_date` := `calculated_at` (every live row shares one calculated_at, so this is
-- exact, not an approximation, for the current data); `score_base`/`multiplier` were never
-- persisted historically and backfill as NULL — Phase 1's scorer only started emitting them
-- after this schema existed to receive them. `notes` is KEPT (not dropped) for any future
-- free-text commentary beyond what's now structured, but new writes should populate the
-- structured columns directly rather than re-encoding them into `notes`.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gold.fund_scores (
    score_total       double precision,
    score_base         double precision,   -- Phase 3a: promoted from notes; NULL pre-migration (never persisted before)
    multiplier          double precision,   -- Phase 3a: promoted from notes; NULL pre-migration (same reason)
    calculated_at         date        NOT NULL,
    as_of_date             date        NOT NULL,   -- Phase 3a: PK member; backfilled := calculated_at
    eligible                 smallint    NOT NULL DEFAULT 0 CHECK (eligible IN (0,1)),
    isin                      varchar(12) NOT NULL,
    block                      text        NOT NULL,
    score_version               text        NOT NULL DEFAULT 'v1',
    regime                       text        NOT NULL,   -- Phase 3a: PK member; backfilled by parsing notes
    score_detail                  jsonb,      -- P3 breakdown surfaced in Superset; validated-as-text then cast (§P3)
    exclusion_reason               text,      -- Phase 3a: promoted from notes ('excluido: ...' suffix), NULL if eligible
    notes                            text,

    CONSTRAINT fund_scores_pkey PRIMARY KEY (isin, block, score_version, regime, as_of_date),
    CONSTRAINT fund_scores_isin_fk FOREIGN KEY (isin) REFERENCES silver.fund_master (isin) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_scores_block ON gold.fund_scores (block, eligible);
-- Genuinely new (Phase 3a consumers — portfolio_builder.py:157-169, backtesting.py:96-101,
-- monthly_report.py:290-291, fund_scorer.py:997-1002 — all read "latest score for this
-- isin/block/regime", which this index serves as an index-only-eligible scan):
CREATE INDEX IF NOT EXISTS idx_scores_latest ON gold.fund_scores (isin, block, regime, as_of_date DESC);

-- -----------------------------------------------------------------------------
-- gold.regime_history — NEW TABLE, P3 Phase 3b. Persists what
-- proyecto3/src/regime_classifier.py::classify_historical() computes (321 rows/call today,
-- rebuilt on every call — the in-memory-caching half of 3b already shipped independent of this
-- table, see the P3 plan; this is the persistence half). Column set matches `RegimeResult`
-- exactly (proyecto3/src/regime_classifier.py:108-128) plus a version tag for the classifier
-- logic that produced the row and an optional raw-membership blob.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gold.regime_history (
    weight_defensive     double precision NOT NULL,
    weight_balanced       double precision NOT NULL,
    weight_dynamic         double precision NOT NULL,
    oil_yoy                 double precision,
    ipc_yoy_avg               double precision,
    cli_eu                     double precision,
    rate_deposit                 double precision,
    d_rate_3m                     double precision,
    spread_hy                       double precision,
    vix_yoy                           double precision,
    term_spread                        double precision,   -- exposed-not-wired (P3 Phase 1k); nullable
    date                                 date NOT NULL,
    classifier_version                    text NOT NULL,   -- free-text version tag, same convention as
                                                             -- algorithm_version/metric_version elsewhere
                                                             -- in Gold; value format is proyecto3's to define
    regime                                 text NOT NULL,
    membership_json                          jsonb,          -- optional; §P3 plan "fold into v27"

    CONSTRAINT regime_history_pkey PRIMARY KEY (date, classifier_version)
);

-- -----------------------------------------------------------------------------
-- gold.portfolio_scenarios — 1 row, built portfolios per scenario
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gold.portfolio_scenarios (
    scenario_id     text PRIMARY KEY,   -- e.g. 'defensiva_2026Q1'
    profile         text NOT NULL,      -- Defensiva / Equilibrada / Dinamica
    macro_regime    text,
    created_at      date NOT NULL,
    notes           text
);

-- -----------------------------------------------------------------------------
-- gold.portfolio_weights — 30 rows, fund weights within each scenario
-- `role` -> `position_role` (rename_map.yaml — avoids the SQL:2003 reserved word)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gold.portfolio_weights (
    weight            double precision NOT NULL CHECK (weight >= 0 AND weight <= 1),
    scenario_id        text             NOT NULL,
    isin                 varchar(12)      NOT NULL,
    block                 text             NOT NULL,
    position_role          text,
    notes                  text,

    CONSTRAINT portfolio_weights_pkey PRIMARY KEY (scenario_id, isin),
    CONSTRAINT portfolio_weights_scenario_fk FOREIGN KEY (scenario_id) REFERENCES gold.portfolio_scenarios (scenario_id),
    CONSTRAINT portfolio_weights_isin_fk FOREIGN KEY (isin) REFERENCES silver.fund_master (isin) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_pw_scenario ON gold.portfolio_weights (scenario_id);
CREATE INDEX IF NOT EXISTS idx_pw_block ON gold.portfolio_weights (block);

-- -----------------------------------------------------------------------------
-- gold.rotation_costs — 7 rows, one row per Fund_Nature
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gold.rotation_costs (
    fund_nature        text PRIMARY KEY,
    redemption_days     smallint         NOT NULL DEFAULT 3,
    exit_fee_pct         double precision NOT NULL DEFAULT 0.0,
    entry_fee_pct         double precision NOT NULL DEFAULT 0.0,
    min_holding_days      smallint         NOT NULL DEFAULT 0,
    notes                  text
);
