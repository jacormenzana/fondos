-- =============================================================================
-- 40_matviews.sql — Views, materialized views, refresh apparatus, monthly maintenance wrapper
-- =============================================================================
-- Depends on 10/20/30/35 already applied. Introspected directly from the live db/fondos.sqlite.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- silver.v_cost_arbitration_overall — carried forward verbatim (CASE logic unchanged), but placed
-- in `silver` rather than `bronze` despite querying a bronze table. Reasoning: this is a derived
-- QC signal Superset needs (§P4 "Views ported"), and superset_ro deliberately has no USAGE on
-- `bronze` at all (00_roles_schemas.sql — bronze holds the raw KIID text, the thing being kept
-- from BI). Granting SELECT on a lone bronze view without schema USAGE would be an inert grant
-- (PG requires schema USAGE to even resolve an object within it) — placing the VIEW itself in
-- silver, where it can freely read bronze.fund_kiid_metadata (fondos_app/fondos_owner have USAGE
-- on all four schemas; only superset_ro is restricted), keeps the "never bronze to BI" boundary
-- absolute rather than carving an exception into it. Same reasoning as the plan's own flag that
-- Medallion placement here is a grant boundary, not a data-lineage purity rule (§0 flag 7).
-- SQLite's `DROP VIEW IF EXISTS` + `CREATE VIEW` becomes `CREATE OR REPLACE VIEW` in PG.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW silver.v_cost_arbitration_overall AS
SELECT isin,
  CASE
    WHEN cost_mgmt_arbitration IS NULL OR cost_oper_arbitration IS NULL THEN NULL
    WHEN 'CONFLICT'      IN (cost_mgmt_arbitration, cost_oper_arbitration) THEN 'CONFLICT'
    WHEN 'BOTH_FAIL'     IN (cost_mgmt_arbitration, cost_oper_arbitration) THEN 'BOTH_FAIL'
    WHEN 'OCR_RECOVERED' IN (cost_mgmt_arbitration, cost_oper_arbitration) THEN 'OCR_RECOVERED'
    WHEN 'ONLY_BANDS_X'  IN (cost_mgmt_arbitration, cost_oper_arbitration) THEN 'ONLY_BANDS_X'
    WHEN 'ONLY_RULED'    IN (cost_mgmt_arbitration, cost_oper_arbitration) THEN 'ONLY_RULED'
    ELSE 'AGREE'
  END AS cost_arbitration_overall
FROM bronze.fund_kiid_metadata
WHERE kiid_class = 1;

-- -----------------------------------------------------------------------------
-- gold.v_p2_patron_x_naturaleza — LIVE-DB-ONLY in SQLite: defined nowhere in the repo
-- (not in schema_fondos.sql, not in any migration script). Captured by direct introspection
-- during this migration; would have been silently lost by any schema-file-driven port
-- (see plan §P4 "Views ported").
--
-- Portability fix applied: SQLite's ROUND(x, n) accepts a floating-point x; PostgreSQL's
-- two-argument round() is defined ONLY for numeric, not double precision (AVG() over a
-- double-precision column returns double precision) — every ROUND(AVG(...), n) below needs an
-- explicit ::numeric cast or the view fails to even create.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW gold.v_p2_patron_x_naturaleza AS
SELECT fm.fund_nature,
       COUNT(DISTINCT ret.isin)                    AS fondos,
       ROUND(AVG(r2.value)::numeric, 3)             AS r2_medio,
       ROUND(AVG(reu.value)::numeric, 4)            AS beta_rate_eu,
       ROUND(AVG(rus.value)::numeric, 4)            AS beta_rate_us,
       ROUND(AVG(rjp.value)::numeric, 4)            AS beta_rate_jp,
       ROUND(AVG(m3.value)::numeric, 4)             AS beta_m3,
       ROUND(AVG(ies.value)::numeric, 4)            AS beta_ipc_es,
       ROUND(AVG(oil.value)::numeric, 4)            AS beta_oil,
       ROUND(AVG(cop.value)::numeric, 4)            AS beta_copper
FROM   gold.fund_metrics ret
JOIN   silver.fund_master fm ON fm.isin = ret.isin
LEFT JOIN gold.fund_metrics r2  ON r2.isin  = ret.isin AND r2.metric  = 'macro_r2'     AND r2.horizon  = 'since_inception' AND r2.real_flag  = 0
LEFT JOIN gold.fund_metrics reu ON reu.isin = ret.isin AND reu.metric = 'beta_rate_eu' AND reu.horizon = 'since_inception' AND reu.real_flag = 0
LEFT JOIN gold.fund_metrics rus ON rus.isin = ret.isin AND rus.metric = 'beta_rate_us' AND rus.horizon = 'since_inception' AND rus.real_flag = 0
LEFT JOIN gold.fund_metrics rjp ON rjp.isin = ret.isin AND rjp.metric = 'beta_rate_jp' AND rjp.horizon = 'since_inception' AND rjp.real_flag = 0
LEFT JOIN gold.fund_metrics m3  ON m3.isin  = ret.isin AND m3.metric  = 'beta_m3_yoy'  AND m3.horizon  = 'since_inception' AND m3.real_flag  = 0
LEFT JOIN gold.fund_metrics ies ON ies.isin = ret.isin AND ies.metric = 'beta_ipc_es'  AND ies.horizon = 'since_inception' AND ies.real_flag = 0
LEFT JOIN gold.fund_metrics oil ON oil.isin = ret.isin AND oil.metric = 'beta_oil'     AND oil.horizon = 'since_inception' AND oil.real_flag = 0
LEFT JOIN gold.fund_metrics cop ON cop.isin = ret.isin AND cop.metric = 'beta_copper'  AND cop.horizon = 'since_inception' AND cop.real_flag = 0
WHERE  ret.metric = 'return_ann' AND ret.horizon = 'since_inception' AND ret.real_flag = 0
  AND  r2.value IS NOT NULL
GROUP BY fm.fund_nature
ORDER BY fondos DESC;


-- =============================================================================
-- Materialized views (§P4). Each carries a UNIQUE index — required for
-- REFRESH MATERIALIZED VIEW CONCURRENTLY, which computes into a temp relation and applies a diff
-- instead of taking ACCESS EXCLUSIVE (which would block an open Superset dashboard).
-- =============================================================================

-- --- FLAGSHIP: kills the documented 2.5h self-join. ~240K rows. ---
CREATE MATERIALIZED VIEW IF NOT EXISTS gold.mv_fmts_peer_stats AS
SELECT t.metric, t.window_label, t.real_flag, t.date, m.heuristic_block,
       count(*)                                                    AS n_funds,
       avg(t.value)                                                AS peer_avg,
       stddev_samp(t.value)                                        AS peer_sd,
       percentile_cont(0.25) WITHIN GROUP (ORDER BY t.value)       AS peer_p25,
       percentile_cont(0.50) WITHIN GROUP (ORDER BY t.value)       AS peer_p50,
       percentile_cont(0.75) WITHIN GROUP (ORDER BY t.value)       AS peer_p75
FROM   gold.fund_metric_timeseries t
JOIN   silver.fund_master m USING (isin)
WHERE  t.value IS NOT NULL
GROUP  BY 1,2,3,4,5;

CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_fmts_peer_stats
  ON gold.mv_fmts_peer_stats (metric, window_label, real_flag, date, heuristic_block);

-- --- "Current state of every fund" — P3/dashboard landing query. ~300K rows. ---
CREATE MATERIALIZED VIEW IF NOT EXISTS gold.mv_fmts_latest AS
SELECT DISTINCT ON (isin, metric, window_label, real_flag)
       isin, metric, window_label, real_flag, date, value, ref_type, ref_value,
       algorithm_version, batch_id
FROM   gold.fund_metric_timeseries
ORDER  BY isin, metric, window_label, real_flag, date DESC;

CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_fmts_latest
  ON gold.mv_fmts_latest (isin, metric, window_label, real_flag);

-- --- Kills the documented 105s COUNT(*). ~19K rows. Reconciliation/coverage surface. ---
CREATE MATERIALIZED VIEW IF NOT EXISTS gold.mv_fund_coverage AS
SELECT isin, metric, count(*) AS n_rows, count(value) AS n_non_null,
       min(date) AS first_date, max(date) AS last_date
FROM   gold.fund_metric_timeseries
GROUP  BY 1,2;

CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_fund_coverage ON gold.mv_fund_coverage (isin, metric);

GRANT SELECT ON gold.mv_fmts_peer_stats, gold.mv_fmts_latest, gold.mv_fund_coverage TO superset_ro;
GRANT SELECT ON silver.v_cost_arbitration_overall TO superset_ro;   -- schema is silver; superset_ro has USAGE there
GRANT SELECT ON gold.v_p2_patron_x_naturaleza TO superset_ro;


-- =============================================================================
-- Refresh apparatus (§P4 "Refresh strategy" / "Pipeline hook")
-- =============================================================================
CREATE OR REPLACE FUNCTION control.refresh_gold_matviews() RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = gold, silver, control, pg_catalog AS $$
DECLARE t0 timestamptz := clock_timestamp();
BEGIN
  IF NOT pg_try_advisory_lock(hashtext('refresh_gold_matviews')) THEN
    INSERT INTO control.p2_pipeline_log (step, status, message)
      VALUES ('MV_REFRESH','SKIP','another refresh already running');
    RETURN;
  END IF;
  REFRESH MATERIALIZED VIEW CONCURRENTLY gold.mv_fmts_peer_stats;
  REFRESH MATERIALIZED VIEW CONCURRENTLY gold.mv_fmts_latest;
  REFRESH MATERIALIZED VIEW CONCURRENTLY gold.mv_fund_coverage;
  -- format() has no %f: '%.1fs' raised "unrecognized format() type specifier" and rolled back the whole refresh.
  INSERT INTO control.p2_pipeline_log (step, status, message)
    VALUES ('MV_REFRESH','OK', to_char(extract(epoch FROM clock_timestamp() - t0), 'FM990.0') || 's');
  PERFORM pg_advisory_unlock(hashtext('refresh_gold_matviews'));
EXCEPTION WHEN OTHERS THEN
  PERFORM pg_advisory_unlock(hashtext('refresh_gold_matviews'));
  INSERT INTO control.p2_pipeline_log (step, status, message)
    VALUES ('MV_REFRESH','ERROR', SQLERRM);
  RAISE;
END $$;

COMMENT ON FUNCTION control.refresh_gold_matviews() IS
  'Call once at the end of the P2 run, AFTER the last per-ISIN commit — never inside it. Escape '
  'hatch (not built): if mv_fmts_peer_stats refresh ever exceeds ~30s, convert it to a real table '
  'maintained incrementally off the finishing run''s batch_id (see plan §P4).';

-- -----------------------------------------------------------------------------
-- control.run_monthly_maintenance() — the ONE named entry point wrapping disk-headroom
-- pre-flight, cluster health, bloat (+ auto-VACUUM FULL on small tables), DEFAULT-partition
-- detection, and the hugepages check. Called once at the end of the P2 run alongside
-- refresh_gold_matviews() (§P2 "Bloat monitoring" — "One named entry point").
--
-- The disk-headroom pre-flight itself is NOT expressible in pure SQL (host `df` isn't visible to
-- SQL) — this function assumes the wrapping shell/Python step has already checked headroom via
-- control.log_disk_headroom_skip() and either proceeded or short-circuited before calling this.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION control.run_monthly_maintenance() RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
  PERFORM control.check_hugepages();
  PERFORM control.maintain_bloat();
  PERFORM control.maintain_clusters();
  -- DEFAULT-partition accumulation is detected, not auto-remediated (promotion needs a reviewed
  -- DDL change — see plan §P2 "DEFAULT partition promotion runbook"); log if non-empty.
  IF EXISTS (SELECT 1 FROM control.v_default_partition_metrics) THEN
    INSERT INTO control.p2_pipeline_log (step, status, message)
      VALUES ('DEFAULT_PARTITION_NONEMPTY', 'WARN',
              (SELECT string_agg(format('%s:%s', metric, n_rows), ', ')
               FROM control.v_default_partition_metrics));
  END IF;
END $$;

COMMENT ON FUNCTION control.run_monthly_maintenance() IS
  'Invoke after control.refresh_gold_matviews() at the end of every P2 run. Each sub-check skips '
  'immediately when nothing needs attention (the normal case) and logs to control.p2_pipeline_log '
  'either way, so intervals between actual maintenance actions are trended, not assumed.';
