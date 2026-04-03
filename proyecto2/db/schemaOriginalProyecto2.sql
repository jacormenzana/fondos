-- =========================
-- PROYECTO 2 - SCHEMA
-- =========================

CREATE TABLE IF NOT EXISTS fund_master (
    isin TEXT PRIMARY KEY,
    fund_name TEXT NOT NULL,
    management_company TEXT,
    fund_category TEXT NOT NULL,
    srri INTEGER,
    benchmark TEXT,
    uses_derivatives INTEGER,
    source TEXT,
    load_ts DATE
);

CREATE TABLE IF NOT EXISTS fund_nav_series (
    isin TEXT NOT NULL,
    nav_date DATE NOT NULL,
    nav REAL NOT NULL,
    source TEXT,
    load_ts DATE,
    PRIMARY KEY (isin, nav_date)
);

CREATE TABLE IF NOT EXISTS inflation_series (
    region TEXT NOT NULL,
    ipc_date DATE NOT NULL,
    ipc_index REAL NOT NULL,
    source TEXT,
    load_ts DATE,
    PRIMARY KEY (region, ipc_date)
);

CREATE TABLE IF NOT EXISTS fund_metrics (
    isin TEXT NOT NULL,
    metric TEXT NOT NULL,
    horizon TEXT NOT NULL,
    value REAL,
    real_flag INTEGER NOT NULL,
    calculation_date DATE NOT NULL,
    metric_version TEXT NOT NULL,
    PRIMARY KEY (isin, metric, horizon, real_flag, metric_version)
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id TEXT PRIMARY KEY,
    start_ts DATE NOT NULL,
    end_ts DATE,
    metric_version TEXT NOT NULL,
    status TEXT NOT NULL
);
