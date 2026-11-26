-- ============================================================================
-- 20261126_fund_master_geography_source.sql — one-shot migration (P#7)
--
-- FND-0441: a stored Geography says where it came from, so an evidenced 'Global' can be told from a 'Global' that was a default.
-- Additive: a nullable column with a closed vocabulary (classify_utils.GEOGRAPHY_SOURCES). NULL = no Geography value, or not derived yet.
-- Written by the P1 pipeline under the same COALESCE as Geography; filled once for the stored rows by scripts/mig/fondos/backfill_geography_source.py.
--
-- Run as the owner role through scripts/mig/fondos/migrate_fund_master_geography_source.py (dry run by default, --apply). Idempotent.
-- Apply BEFORE any P1 run with the code that writes the column (shared/schema_checks.py requires it at startup).
-- ============================================================================

ALTER TABLE silver.fund_master ADD COLUMN IF NOT EXISTS geography_source text
    CHECK (geography_source IS NULL OR geography_source IN ('kiid','name','mandate','index','default','unreproduced','wrong_doc'));
