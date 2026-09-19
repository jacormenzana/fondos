-- =============================================================================
-- 20_silver.sql — Silver layer: classified, consistency-checked records
-- =============================================================================
-- Introspected directly from the live db/fondos.sqlite. Column renames per db/pg/rename_map.yaml.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- silver.fund_master — the dimension everything joins to. 3,726 rows / ~3MB.
-- ALL secondary indexes dropped (§P2 index plan) — a seq scan on 3,726 rows is ~0.4ms and is what
-- the planner picks anyway for anything selecting >5% of the table. In SQLite these indexes served
-- a weaker join planner; in PG they are pure UPDATE tax. fillfactor=85 + no secondary indexes is
-- what makes HOT updates effective on this table, which is rewritten every P1 cycle.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS silver.fund_master (
    isin                        varchar(12) PRIMARY KEY,
    fund_name                   text NOT NULL,
    management_company          text,
    fund_nature                 text NOT NULL,
    profile                     text,
    vehicle_structure            text,
    family                      text,
    style_profile                text,
    geography                   text,
    theme                       text,
    exposure_bias                text,
    heuristic_block              text NOT NULL,
    heuristic_core                smallint NOT NULL CHECK (heuristic_core IN (0,1)),
    srri                         smallint,
    fund_currency                 varchar(3),
    hedging_policy                text,
    replication_method            text,
    derivatives_usage             text,
    benchmark_declared            text,
    inference_trace               text,       -- stays TEXT: human-read audit trace, never queried structurally
    srri_quality_flag             text
        CHECK (srri_quality_flag IN ('HIGH','MEDIUM_VISUAL','MEDIUM_TEXT','LOW_CONFLICT','NONE')),
    data_quality_flag             text,
    created_at                    timestamptz DEFAULT now(),
    updated_at                    timestamptz,
    ongoing_charge_recurrent      double precision,   -- decimal ratio (0.0075==0.75%), NOT integer pct — see below
    fund_family_id                text,
    strategy                     text,
    benchmark_type                text,
    accumulation_policy            text,
    entry_fee_pct                 double precision,
    exit_fee_pct                  double precision,
    sfdr_article                  smallint,           -- values 6/8/9 (note: config.py treats as string — app-layer only)
    recommended_holding_period    smallint,           -- years
    leverage_used                 text,
    liquidity_profile             text,
    distribution_frequency        text,
    market_cap_focus              text,
    sector_focus                  text,
    investment_universe           text,
    investment_focus              text,
    credit_quality                text,
    fee_known_flag                 text,
    kid_format                    text CHECK (kid_format IN ('UCITS_KIID','PRIIPS_KID','UNKNOWN')),
    kid_currency                   varchar(3),
    cost_extraction_quality        text
        CHECK (cost_extraction_quality IN ('HIGH','MEDIUM_CROSS','MEDIUM_EUR','MEDIUM_PCT','LOW','NONE')),
    cost_rhp_years                 double precision CHECK (cost_rhp_years IS NULL OR (cost_rhp_years > 0 AND cost_rhp_years <= 50)),
    entry_fee_pct_max              double precision CHECK (entry_fee_pct_max IS NULL OR (entry_fee_pct_max >= 0 AND entry_fee_pct_max <= 25)),
    exit_fee_pct_max               double precision CHECK (exit_fee_pct_max IS NULL OR (exit_fee_pct_max >= 0 AND exit_fee_pct_max <= 25)),
    management_fee_pct             double precision CHECK (management_fee_pct IS NULL OR (management_fee_pct >= 0 AND management_fee_pct <= 10)),
    transaction_cost_pct           double precision CHECK (transaction_cost_pct IS NULL OR (transaction_cost_pct >= 0 AND transaction_cost_pct <= 5)),
    performance_fee_pct            double precision CHECK (performance_fee_pct IS NULL OR (performance_fee_pct >= 0 AND performance_fee_pct <= 30)),
    aci_1y                        double precision CHECK (aci_1y IS NULL OR (aci_1y >= 0 AND aci_1y <= 50)),
    aci_rhp                       double precision CHECK (aci_rhp IS NULL OR (aci_rhp >= 0 AND aci_rhp <= 25)),
    development_status             text,
    duration_profile               text,
    mmf_structure                  text,
    alt_strategy                   text,
    payoff_profile                 text,
    asset_currency                 varchar(3),
    in_current_universe            smallint NOT NULL DEFAULT 1 CHECK (in_current_universe IN (0,1)),
    performance_fee_basis          text CHECK (performance_fee_basis IS NULL OR performance_fee_basis IN
                                   ('NONE','RATE_ON_OUTPERFORMANCE','PCT_OF_ASSETS','UNDETERMINED'))
    -- fund_family_id's FK to silver.fund_families is added below, AFTER that table exists —
    -- PostgreSQL requires the referenced table to exist at CREATE time, unlike SQLite's more
    -- permissive forward-reference tolerance. Declaring it inline here (as the source SQLite DDL
    -- does) would fail a from-empty `psql -f` apply of this file.
) WITH (fillfactor = 85);

COMMENT ON COLUMN silver.fund_master.ongoing_charge_recurrent IS
  'DECIMAL RATIO (0.0075 == 0.75%), while every *_pct / aci_* column on this table is INTEGER '
  'PERCENT. 81 funds were once found 100x off by conflating the two scales — do not unify them.';

-- -----------------------------------------------------------------------------
-- silver.fund_families — 3,193 rows
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS silver.fund_families (
    family_id     text PRIMARY KEY,
    family_name   text,
    fund_nature   text,
    n_funds       integer,
    updated_at    timestamptz
);

-- Deferred FKs, added idempotently — unlike CREATE TABLE/INDEX elsewhere in this DDL,
-- `ALTER TABLE ADD CONSTRAINT` has no `IF NOT EXISTS` form in PostgreSQL, so a bare version of
-- this block breaks re-applying the file a second time ("constraint already exists" —
-- confirmed against a live server 2026-09-19, on the very first re-apply attempt). Guarded via
-- pg_constraint checks instead, matching the idempotency the rest of this file already has.
--
-- fund_master's FK to fund_families: deferred to here because both tables must exist first (see
-- the comment in the fund_master CREATE TABLE above).
--
-- bronze.fund_nav_daily / fund_nav_monthly's FK to fund_master: deferred here because
-- 10_bronze.sql runs BEFORE this file, so silver.fund_master doesn't exist yet at that point.
-- Added VALID directly (not NOT VALID / deferred-to-seed-time like gold.fund_metric_timeseries's
-- FK) — the SQLite source already has and enforces this FK (foreign_keys=ON the whole time), so
-- there's no reason to expect orphans here; the seed loader's orphan pre-scan/quarantine for
-- these two tables is defensive insurance, not a response to a known problem. Found missing
-- entirely (never added to 10_bronze.sql at all) while hands-on verifying the DDL against a real
-- server, 2026-09-19 — the original SQLite schema has it, so this closes a real gap, not just an
-- ordering fix.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fund_master_family_fk') THEN
    ALTER TABLE silver.fund_master
      ADD CONSTRAINT fund_master_family_fk FOREIGN KEY (fund_family_id) REFERENCES silver.fund_families (family_id);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fund_nav_daily_isin_fk') THEN
    ALTER TABLE bronze.fund_nav_daily
      ADD CONSTRAINT fund_nav_daily_isin_fk FOREIGN KEY (isin) REFERENCES silver.fund_master (isin) ON DELETE CASCADE;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fund_nav_monthly_isin_fk') THEN
    ALTER TABLE bronze.fund_nav_monthly
      ADD CONSTRAINT fund_nav_monthly_isin_fk FOREIGN KEY (isin) REFERENCES silver.fund_master (isin) ON DELETE CASCADE;
  END IF;
END $$;

-- -----------------------------------------------------------------------------
-- silver.fund_benchmarks — 5,341 rows
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS silver.fund_benchmarks (
    isin              varchar(12) NOT NULL,
    source            text        NOT NULL,   -- KIID / MORNINGSTAR / MANUAL
    benchmark_raw     text,
    benchmark_id      text,
    benchmark_name    text,
    provider          text,
    asset_class       text,
    confidence        text,
    extracted_at      timestamptz DEFAULT now(),
    benchmark_role    text        DEFAULT 'asset_proxy',

    CONSTRAINT fund_benchmarks_pkey PRIMARY KEY (isin, source)
);

CREATE INDEX IF NOT EXISTS idx_fb_isin ON silver.fund_benchmarks (isin);
CREATE INDEX IF NOT EXISTS idx_fb_id ON silver.fund_benchmarks (benchmark_id);
CREATE INDEX IF NOT EXISTS idx_fb_provider ON silver.fund_benchmarks (provider);

-- -----------------------------------------------------------------------------
-- silver.fund_cost_schedule — 6,715 rows
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS silver.fund_cost_schedule (
    isin                varchar(12)      NOT NULL,
    horizon_years        double precision NOT NULL CHECK (horizon_years > 0 AND horizon_years <= 50),
    is_rhp               smallint         NOT NULL DEFAULT 0 CHECK (is_rhp IN (0,1)),
    total_costs_eur       double precision,
    total_costs_pct       double precision,
    annual_impact_pct     double precision,
    source                text             NOT NULL
        CHECK (source IN ('PRIIPS_COSTS_OVER_TIME', 'UCITS_DERIVED', 'MANUAL',
                           'PRIIPS_COMPOSITION', 'PRIIPS_TEXT')),
        -- 5 values per shared/config.py COST_SCHEDULE_SOURCE_VALUES — the live SQLite CHECK only
        -- had 3 (a documented drift; this DDL closes the gap rather than porting it forward)
    created_at            timestamptz      NOT NULL DEFAULT now(),
    updated_at             timestamptz      NOT NULL DEFAULT now(),

    CONSTRAINT fund_cost_schedule_pkey PRIMARY KEY (isin, horizon_years)
);

CREATE INDEX IF NOT EXISTS idx_cost_schedule_isin ON silver.fund_cost_schedule (isin);
CREATE INDEX IF NOT EXISTS idx_cost_schedule_rhp ON silver.fund_cost_schedule (isin) WHERE is_rhp = 1;
  -- partial index — PG's strength, ports verbatim from SQLite (§P2 index plan)

-- -----------------------------------------------------------------------------
-- silver.fund_cost_corrections — 6,077 rows, audit table: no cost datum is ever lost
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS silver.fund_cost_corrections (
    isin           varchar(12)      NOT NULL,
    column_name    text             NOT NULL,   -- e.g. 'aci_rhp'
    old_value      text,            -- preserved (NULL if there was none). NOT double precision:
    new_value      text,            -- this is a GENERIC audit trail for ANY column correction,
                                     -- not only numeric cost columns — confirmed against the live
                                     -- server 2026-09-19: 1/6,077 rows corrects `Performance_Fee_Basis`
                                     -- (a categorical column), holding 'RATE_ON_OUTPERFORMANCE'/'NONE'
                                     -- as literal text. SQLite itself stores this column loosely-typed
                                     -- for exactly this reason; `text` preserves that faithfully — the
                                     -- 6,076 numeric rows still cast cleanly downstream
                                     -- (`old_value::double precision`) when that's what's needed.
    reason         text             NOT NULL,   -- fix code, e.g. 'F3-PROJECTION-NO-ANCHOR'
    evidence       text,
    corrected_at   timestamptz      NOT NULL DEFAULT now(),

    CONSTRAINT fund_cost_corrections_pkey PRIMARY KEY (isin, column_name, corrected_at)
);

CREATE INDEX IF NOT EXISTS idx_fcc_isin ON silver.fund_cost_corrections (isin);
CREATE INDEX IF NOT EXISTS idx_fcc_column ON silver.fund_cost_corrections (column_name);
CREATE INDEX IF NOT EXISTS idx_fcc_reason ON silver.fund_cost_corrections (reason);

-- -----------------------------------------------------------------------------
-- silver.fund_data_quality_issues — 4,011 rows, current-state table (DELETE+re-INSERT per cycle)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS silver.fund_data_quality_issues (
    id            bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    isin          varchar(12) NOT NULL,
    check_code    text        NOT NULL,
    level         text        NOT NULL,
    message       text,
    detected_at   timestamptz
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_dqissues_isin_code ON silver.fund_data_quality_issues (isin, check_code);
CREATE INDEX IF NOT EXISTS idx_dqissues_isin ON silver.fund_data_quality_issues (isin);
-- Genuinely new (§P2 index plan): "what's wrong right now" dashboard, partial index ~5% of the table
CREATE INDEX IF NOT EXISTS idx_dqissues_open ON silver.fund_data_quality_issues (isin, check_code)
  WHERE level IN ('WARN','MISSING');

-- -----------------------------------------------------------------------------
-- silver.kiid_lifecycle — 3,728 rows, one row per lifecycle period per ISIN
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS silver.kiid_lifecycle (
    isin         text        NOT NULL,   -- NOT varchar(12) like every other isin column in this
                                          -- schema: confirmed against the live server 2026-09-19,
                                          -- 2/3,728 rows hold a corrupted value with a suffix
                                          -- appended ('IE00B45H7020_20260522',
                                          -- 'LU2598446222 - copia') — real P1-side data quality
                                          -- issue in the harvest/retirement bookkeeping (appears to
                                          -- disambiguate a PK collision by mangling isin itself
                                          -- rather than using a proper key), not something this
                                          -- migration should silently truncate or drop. Flagged as
                                          -- a P1 root-cause bug to fix upstream; this column stays
                                          -- wide enough to hold whatever P1 currently produces.
    start_date   date        NOT NULL,
    end_date     date,
    status       text        NOT NULL DEFAULT 'commercializing',
    href         text,
    retire_dir   text,

    CONSTRAINT kiid_lifecycle_pkey PRIMARY KEY (isin, start_date)
);

CREATE INDEX IF NOT EXISTS ix_lc_isin ON silver.kiid_lifecycle (isin);
CREATE INDEX IF NOT EXISTS ix_lc_status ON silver.kiid_lifecycle (status);

-- -----------------------------------------------------------------------------
-- Superset label view — plain VIEW, not a matview (3,726 rows; materializing adds a refresh
-- dependency for no gain). See plan §P4 "Views ported".
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW silver.v_fund_master_label AS
SELECT isin, fund_name, management_company, fund_nature, heuristic_block,
       geography, credit_quality, in_current_universe
FROM   silver.fund_master;

GRANT SELECT ON silver.v_fund_master_label TO superset_ro;
