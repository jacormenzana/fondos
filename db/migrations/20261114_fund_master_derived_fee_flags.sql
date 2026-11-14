-- ============================================================================
-- 20261114_fund_master_derived_fee_flags.sql -- one-shot migration (P#7), Package H (FND-0408), plan R6.3 / R8
--
-- Run as fondos_owner (DDL) against the `fondos` database, in ONE transaction. NOT run by any session. Generated from shared.fee_state.fee_state_sql, the
-- expression a Postgres test proves equal to shared.fee_state.derive_fee_state on the full truth table (tests/test_fee_state_pg.py): do not edit by hand.
--
-- The entry / exit fee STATE is a function of its two numbers, so it stops being a stored column that can disagree with them: both flags become GENERATED
-- STORED columns. The old stored fee_known_flag held the OLD vocabulary (incl. EXIT_INFERRED_ZERO) and is dropped with its values (the derived value replaces it;
-- the pre-change values are in out/pkg_h/backup_fund_master_fee.csv). No view or matview depends on the column (pg_depend checked 2026-11-14).
--
-- ORDER (plan R8): apply AFTER the whole-universe pass ran with ATTRIBUTE_GROUP_WRITE_ENABLED ON (the writer then no longer writes fee_known_flag: a generated
-- column refuses an explicit value), THEN commit the post-migration change (db/pg/20_silver.sql, shared/schema_checks.py V24, bi_compat.sql, rename_map.yaml,
-- the writer spec, the hermetic tests). schema_checks fails only on a MISSING expected column, so both names stay present at every step.
-- ============================================================================
BEGIN;

ALTER TABLE silver.fund_master
    DROP COLUMN fee_known_flag,
    ADD COLUMN fee_known_flag text GENERATED ALWAYS AS (
        CASE WHEN entry_fee_pct IS NOT NULL AND entry_fee_pct >= 0 AND entry_fee_pct <> 'NaN'::float8 THEN (CASE WHEN entry_fee_pct = 0 THEN 'ZERO_CONFIRMED' ELSE 'EXTRACTED' END) WHEN entry_fee_pct_max IS NOT NULL AND entry_fee_pct_max >= 0 AND entry_fee_pct_max <> 'NaN'::float8 THEN (CASE WHEN entry_fee_pct_max = 0 THEN 'ZERO_CONFIRMED' ELSE 'ENTRY_CONDITIONAL' END) ELSE 'NOT_FOUND' END
    ) STORED,
    ADD COLUMN exit_fee_known_flag text GENERATED ALWAYS AS (
        CASE WHEN exit_fee_pct IS NOT NULL AND exit_fee_pct >= 0 AND exit_fee_pct <> 'NaN'::float8 THEN (CASE WHEN exit_fee_pct = 0 THEN 'ZERO_CONFIRMED' ELSE 'EXTRACTED' END) WHEN exit_fee_pct_max IS NOT NULL AND exit_fee_pct_max >= 0 AND exit_fee_pct_max <> 'NaN'::float8 THEN (CASE WHEN exit_fee_pct_max = 0 THEN 'ZERO_CONFIRMED' ELSE 'EXIT_CONDITIONAL' END) ELSE 'NOT_FOUND' END
    ) STORED;

COMMIT;

-- Verification (both must hold; the second must return 0 rows):
-- SELECT fee_known_flag, count(*) FROM silver.fund_master GROUP BY 1 ORDER BY 2 DESC;
-- SELECT isin FROM silver.fund_master WHERE fee_known_flag IS NULL OR exit_fee_known_flag IS NULL LIMIT 5;
