-- =============================================================================
-- 10_bronze.sql — Bronze layer: raw/append-only source data
-- =============================================================================
-- Introspected directly from the live db/fondos.sqlite (not from the stale db/schema_fondos.sql —
-- see plan §P2 "Source of truth"). Column renames per db/pg/rename_map.yaml.
--
-- Load-time note (§P3): these CREATE TABLE statements are the POST-seed shape (full PK/FK/CHECK/
-- index). The seed loader creates a NOT-NULL-only version first, COPYs, then applies constraints
-- and indexes per the execution order in §P3 — this file is the target end-state, not the literal
-- load-time DDL.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- bronze.fund_nav_daily — 13.9M rows live, short-horizon source. Highest-value CLUSTER target.
-- -----------------------------------------------------------------------------
-- Column order: fixed-width first (descending alignment), then varlena — see plan §P2 "Type mapping".
CREATE TABLE IF NOT EXISTS bronze.fund_nav_daily (
    nav           double precision NOT NULL,
    ingested_at   timestamptz      NOT NULL DEFAULT now(),
    date          date             NOT NULL,
    is_estimated  smallint         DEFAULT 0
                                    CHECK (is_estimated IS NULL OR is_estimated IN (0,1)),
    isin          varchar(12)      NOT NULL,
    nav_currency  varchar(3),
    nav_type      text             DEFAULT 'TOTAL_RETURN_IDX',
    data_source   text,

    CONSTRAINT fund_nav_daily_pkey PRIMARY KEY (isin, date)
    -- FK to silver.fund_master(isin) intentionally NOT declared here — silver.fund_master doesn't
    -- exist yet at this point in file-execution order (this file runs BEFORE 20_silver.sql). Added
    -- as a deferred ALTER TABLE at the end of 20_silver.sql, same pattern as fund_master's own FK
    -- to fund_families. The SQLite source for this table DOES have this FK (and has had
    -- foreign_keys=ON the whole time), so this is not the "may contain real orphans" case that
    -- gold.fund_metric_timeseries's FK is — it's added directly (VALID), not deferred to
    -- post-seed like that one.
) WITH (fillfactor = 100);   -- never UPDATEd — reserving space would inflate the heap for nothing

COMMENT ON TABLE bronze.fund_nav_daily IS
  'Daily NAV series (short-horizon source). CLUSTER USING PK after seed load — highest-value '
  'clustering target in the DB (~90x fewer page touches on the per-ISIN read).';

CREATE INDEX IF NOT EXISTS idx_nav_daily_date ON bronze.fund_nav_daily (date);
-- idx_nav_daily_isin dropped: strict prefix of PK (isin, date) — pure redundancy (§P2 index plan)
CREATE INDEX IF NOT EXISTS idx_nav_daily_ingested_at ON bronze.fund_nav_daily
  USING brin (ingested_at) WITH (pages_per_range = 128);
  -- "what did the last load touch" over 13.9M rows for ~50KB, not a 400MB btree.
  -- Stays correlated because NAV rows are appended and CLUSTER by (isin,date) largely preserves
  -- ingest order within a fund (§P2 Clustering & fillfactor — BRIN section).

-- -----------------------------------------------------------------------------
-- bronze.fund_nav_monthly — 675K rows, monthly NAV series (source: Morningstar)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bronze.fund_nav_monthly (
    nav           double precision NOT NULL,
    ingested_at   timestamptz      NOT NULL DEFAULT now(),
    date          date             NOT NULL,
    is_estimated  smallint         DEFAULT 0
                                    CHECK (is_estimated IS NULL OR is_estimated IN (0,1)),
    isin          varchar(12)      NOT NULL,
    nav_currency  varchar(3),
    nav_type      text             DEFAULT 'NAV',
    data_source   text,

    CONSTRAINT fund_nav_monthly_pkey PRIMARY KEY (isin, date)
    -- Same deferred-FK note as fund_nav_daily above — see 20_silver.sql's tail.
) WITH (fillfactor = 100);

CREATE INDEX IF NOT EXISTS idx_nav_monthly_date ON bronze.fund_nav_monthly (date);

-- -----------------------------------------------------------------------------
-- bronze.series_macro — 12K rows, all macro time series
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bronze.series_macro (
    value       double precision,
    load_ts     timestamptz NOT NULL DEFAULT now(),
    date        date        NOT NULL,
    indicator   text        NOT NULL,   -- normalized code; ~30-entry catalogue, see NORMAS_IMPLEMENTACION.md
    geography   text        NOT NULL,   -- ES / EU / US / JP / CN / GLOBAL
    unit        text,                   -- ratio / index / pct / usd_bn
    source      text,                   -- BCE / EUROSTAT / INE / FRED / IMF

    CONSTRAINT series_macro_pkey PRIMARY KEY (date, indicator, geography)
);

CREATE INDEX IF NOT EXISTS idx_macro_date ON bronze.series_macro (date);
CREATE INDEX IF NOT EXISTS idx_macro_indicator ON bronze.series_macro (indicator, geography);

-- -----------------------------------------------------------------------------
-- bronze.series_benchmark — 0 rows live, index-level benchmark series
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bronze.series_benchmark (
    value         double precision NOT NULL,
    load_ts       timestamptz      NOT NULL DEFAULT now(),
    date          date             NOT NULL,
    benchmark_id  text             NOT NULL,
    value_type    text             DEFAULT 'CLOSE',   -- CLOSE / TR
    currency      text,
    source        text,

    CONSTRAINT series_benchmark_pkey PRIMARY KEY (benchmark_id, date)
);

CREATE INDEX IF NOT EXISTS idx_bench_date ON bronze.series_benchmark (date);
CREATE INDEX IF NOT EXISTS idx_bench_id ON bronze.series_benchmark (benchmark_id);

-- -----------------------------------------------------------------------------
-- bronze.series_inflation — 949 rows, IPC index
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bronze.series_inflation (
    ipc_index   double precision NOT NULL,
    load_ts     timestamptz      NOT NULL DEFAULT now(),
    date        date             NOT NULL,
    geography   text             NOT NULL DEFAULT 'ES',
    source      text,

    CONSTRAINT series_inflation_pkey PRIMARY KEY (date, geography)
);

-- -----------------------------------------------------------------------------
-- bronze.fund_kiid_metadata — 3,726 rows, ~58M chars raw_kiid_text + ~8.9M chars dla2_table_text
-- (100% TOASTed). Cost-arbitration columns (v20) carry the dual bands-X/ruled arbitration model.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bronze.fund_kiid_metadata (
    isin                        varchar(12) NOT NULL,
    kiid_class                  smallint    NOT NULL DEFAULT 1,
    kiid_url                    text,
    kiid_pdf_hash                text,
    kiid_status                  text        DEFAULT 'CACHED',
    language                    text,
    raw_kiid_text                text,       -- Bronze raw text; never deleted, never transformed in place
    kiid_published_date          date,
    kiid_downloaded_at           timestamptz,
    srri                         smallint,
    srri_visual                  smallint,
    srri_textual                 smallint,
    srri_validation_status       text,
    processing_time_ms           integer,    -- known bug: stores seconds, not ms (AGENTS.md "Known bugs")
    processing_breakdown         text,       -- NOT JSON despite the name: pipe-delimited timing
                                              -- string, e.g. 'kiid_fetch:0ms|kiid_parse:72ms|classify:2ms'.
                                              -- Confirmed 0/3,726 live values parse as JSON (verified
                                              -- 2026-09-18 against the full non-null population, not a
                                              -- sample) — the plan's §P2 type-mapping table listed this
                                              -- as a jsonb candidate alongside fund_scores.score_detail;
                                              -- that was wrong for this column specifically and is
                                              -- corrected here. score_detail IS genuine JSON and stays jsonb.
    dla2_table_text              text,
    cost_mgmt_bandsx             double precision,
    cost_mgmt_ruled              double precision,
    cost_mgmt_arbitration        text
        CHECK (cost_mgmt_arbitration IS NULL OR cost_mgmt_arbitration IN
               ('AGREE','OCR_RECOVERED','BOTH_FAIL','ONLY_BANDS_X','ONLY_RULED','CONFLICT')),
    cost_oper_bandsx             double precision,
    cost_oper_ruled              double precision,
    cost_oper_arbitration        text
        CHECK (cost_oper_arbitration IS NULL OR cost_oper_arbitration IN
               ('AGREE','OCR_RECOVERED','BOTH_FAIL','ONLY_BANDS_X','ONLY_RULED','CONFLICT')),
    cost_aci_rhp_bandsx          double precision,
    cost_aci_rhp_ruled           double precision,
    cost_aci_rhp_arbitration     text
        CHECK (cost_aci_rhp_arbitration IS NULL OR cost_aci_rhp_arbitration IN
               ('AGREE','OCR_RECOVERED','BOTH_FAIL','ONLY_BANDS_X','ONLY_RULED','CONFLICT')),
    cost_aci_1y_bandsx           double precision,
    cost_aci_1y_ruled            double precision,
    cost_aci_1y_arbitration      text
        CHECK (cost_aci_1y_arbitration IS NULL OR cost_aci_1y_arbitration IN
               ('AGREE','OCR_RECOVERED','BOTH_FAIL','ONLY_BANDS_X','ONLY_RULED','CONFLICT')),

    CONSTRAINT fund_kiid_metadata_pkey PRIMARY KEY (isin, kiid_class)
);

CREATE INDEX IF NOT EXISTS idx_km_status ON bronze.fund_kiid_metadata (kiid_status);
CREATE INDEX IF NOT EXISTS idx_km_srri_val ON bronze.fund_kiid_metadata (srri_validation_status);
CREATE INDEX IF NOT EXISTS idx_km_visual ON bronze.fund_kiid_metadata (srri_visual);
-- Full-text (tsvector/spanish + GIN) on raw_kiid_text deferred until actually requested (§P2 index plan).

-- -----------------------------------------------------------------------------
-- bronze.db_document_catalogue — 33.8K rows, latest Deutsche Bank XML harvest snapshot
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bronze.db_document_catalogue (
    harvest_ts     text NOT NULL,
    gestora_label  text NOT NULL,
    gestora_value  text,
    cod_db         text NOT NULL,
    fund_name      text,
    isin           varchar(12),
    link_label     text NOT NULL,
    href           text NOT NULL,
    cod_doc        text,
    cod_sus        text,
    cod_cont       text,
    idioma         text,

    CONSTRAINT db_document_catalogue_pkey PRIMARY KEY (harvest_ts, cod_db, href)
);

CREATE INDEX IF NOT EXISTS ix_cat_isin ON bronze.db_document_catalogue (isin);
CREATE INDEX IF NOT EXISTS ix_cat_codsus ON bronze.db_document_catalogue (cod_sus);
