-- ============================================================
-- Postgres typed DDL for v26 analytics tables
-- ============================================================
-- Purpose: pre-create these tables in Postgres with explicit column types
-- so that load_fondos_to_postgres.py's to_sql(if_exists="replace") cannot
-- infer imprecise types from an empty DataFrame (e.g. empty fund_metric_alerts
-- on a clean cycle would produce TEXT for all columns).
--
-- Run ONCE against the Docker Postgres (fondos DB, port 5433) before the
-- first P4_syncToPostgres.bat execution:
--
--   psql "postgresql://superset:superset@localhost:5433/fondos" \
--        -f db/postgres_analytics_ddl.sql
--
-- The ETL (load_fondos_to_postgres.py) will then use if_exists="replace"
-- which drops and recreates, but the explicit CREATE TABLE below provides
-- a typed template. If you want typed columns to survive re-runs, change
-- the ETL to if_exists="append" for these two tables only (P5 hardening).
-- ============================================================

-- Drop + recreate to keep this script idempotent during development.
-- CAUTION: removes existing data. After first production load, remove
-- the DROP statements and replace with ALTER TABLE ADD COLUMN IF NOT EXISTS.
DROP TABLE IF EXISTS fund_metric_timeseries;
DROP TABLE IF EXISTS fund_metric_alerts;

-- ----------------------------------------------------------
-- fund_metric_timeseries
-- Rolling metric time-series (curated 3 metrics × all windows).
-- Long format: one row per (isin, metric, window, date, real_flag).
-- ----------------------------------------------------------
CREATE TABLE fund_metric_timeseries (
    isin            VARCHAR(12)     NOT NULL,
    metric          VARCHAR(64)     NOT NULL,
    window          VARCHAR(32)     NOT NULL,
    date            DATE            NOT NULL,
    value           DOUBLE PRECISION,
    real_flag       SMALLINT        NOT NULL DEFAULT 0
                                    CHECK (real_flag IN (0, 1)),
    ref_type        VARCHAR(16),            -- NULL / 'category' / 'benchmark'
    ref_value       DOUBLE PRECISION,
    source_rows     INTEGER,
    load_ts         TIMESTAMPTZ     DEFAULT NOW(),

    PRIMARY KEY (isin, metric, window, date, real_flag)
);

CREATE INDEX IF NOT EXISTS idx_pg_fmts_isin_metric
    ON fund_metric_timeseries (isin, metric);
CREATE INDEX IF NOT EXISTS idx_pg_fmts_metric_window
    ON fund_metric_timeseries (metric, window);
CREATE INDEX IF NOT EXISTS idx_pg_fmts_date
    ON fund_metric_timeseries (date);

COMMENT ON TABLE fund_metric_timeseries IS
    'P2 v26 — Rolling metric series (roll_vol_ann / roll_max_dd / roll_return_ann). '
    'Incremental append by date; do not full-replace in production ETL runs.';

-- ----------------------------------------------------------
-- fund_metric_alerts
-- Current-state WARN/ALARM table (rebuilt each P2 cycle).
-- One row per (isin, metric, window) — latest alert level only.
-- ----------------------------------------------------------
CREATE TABLE fund_metric_alerts (
    isin            VARCHAR(12)     NOT NULL,
    metric          VARCHAR(64)     NOT NULL,
    window          VARCHAR(32)     NOT NULL,
    level           VARCHAR(8)      NOT NULL CHECK (level IN ('OK', 'WARN', 'ALARM')),
    rule_code       VARCHAR(32)     NOT NULL,
    value           DOUBLE PRECISION,
    reference_value DOUBLE PRECISION,
    ref_type        VARCHAR(16),
    detected_at     TIMESTAMPTZ     DEFAULT NOW(),

    PRIMARY KEY (isin, metric, window)
);

CREATE INDEX IF NOT EXISTS idx_pg_fma_level   ON fund_metric_alerts (level);
CREATE INDEX IF NOT EXISTS idx_pg_fma_rule    ON fund_metric_alerts (rule_code);
CREATE INDEX IF NOT EXISTS idx_pg_fma_isin    ON fund_metric_alerts (isin);

COMMENT ON TABLE fund_metric_alerts IS
    'P2 v26 — Current-state WARN/ALARM per (isin, metric, window). '
    'Rebuilt each P2 cycle (like fund_data_quality_issues in P1). '
    'Alarms compare fund value vs Fund_Nature category percentile.';
