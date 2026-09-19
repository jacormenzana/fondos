-- =============================================================================
-- 00_roles_schemas.sql — Namespaces, roles, grants, extensions
-- =============================================================================
-- Part of the SQLite → PostgreSQL migration (plan: role-you-are-keen-ladybug.md, §P2).
-- Run as a superuser (or the bootstrapping `fondos_owner` role itself, if pre-created by the
-- Docker entrypoint's POSTGRES_USER). Idempotent: safe to re-run.
--
-- Four real schemas (Medallion layers), not one — the deciding argument is the grant boundary:
-- Superset needs read-only on silver+gold ONLY, never bronze (58M+ chars of raw KIID text) or
-- control (operational logs). With one schema this needs per-table grants that rot the moment a
-- table is added; with schemas it is one ALTER DEFAULT PRIVILEGES that stays correct permanently.
-- Table names are globally unique across the four layers, so role-scoped search_path resolves
-- every unqualified name in the application's hundreds of string-built queries — zero rewrites.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Schemas
-- ---------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS bronze;   -- raw/append-only: NAV, macro series, raw KIID text, harvest catalogue
CREATE SCHEMA IF NOT EXISTS silver;   -- validated/normalized: fund_master and its satellite tables
CREATE SCHEMA IF NOT EXISTS gold;     -- calculated indicators: fund_metrics, timeseries, scores, portfolios
CREATE SCHEMA IF NOT EXISTS control;  -- cross-cutting pipeline state: logs, fingerprints, audit, migration bookkeeping

COMMENT ON SCHEMA bronze  IS 'Medallion Bronze: raw/append-only source data. Never transformed in place.';
COMMENT ON SCHEMA silver  IS 'Medallion Silver: classified, consistency-checked records; rebuilt each pipeline cycle.';
COMMENT ON SCHEMA gold    IS 'Medallion Gold: derived outputs, fully recomputable from Bronze+Silver.';
COMMENT ON SCHEMA control IS 'Pipeline orchestration state, audit tooling, migration bookkeeping — not domain data.';

-- Nobody creates loose objects in `public` on this database.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- Extensions
-- ---------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;   -- mandatory: the only way to know what's actually slow
CREATE EXTENSION IF NOT EXISTS pg_trgm;              -- available for similarity(); NOT indexed on fund_name (§P2 index plan)
CREATE EXTENSION IF NOT EXISTS pgstattuple;          -- bloat measurement, feeds control.v_bloat_health
CREATE EXTENSION IF NOT EXISTS pg_buffercache;       -- "is the working set actually resident?"

-- ---------------------------------------------------------------------------
-- Roles
-- ---------------------------------------------------------------------------
-- Passwords are NOT set here — inject via `ALTER ROLE ... WITH PASSWORD` from a secrets-managed
-- deploy step (Docker secret / env file outside the repo), never committed. See plan §P1
-- "Credential handling" for the same discipline applied to the pgBackRest bucket key.

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fondos_owner') THEN
    CREATE ROLE fondos_owner LOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fondos_app') THEN
    CREATE ROLE fondos_app LOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'superset_ro') THEN
    CREATE ROLE superset_ro LOGIN;
  END IF;
END
$$;

-- fondos_owner: owns all objects, runs DDL/migrations only. Not used by the pipeline at runtime.
ALTER ROLE fondos_owner SET search_path = gold, silver, bronze, control, public;

-- fondos_app: P1/P2/P3 pipelines. DML only — deliberately NO CREATE on any schema, so a stray
-- unqualified CREATE TABLE from a diagnostic script cannot silently land in `gold` (first entry
-- in the path) instead of erroring.
ALTER ROLE fondos_app SET search_path = gold, silver, bronze, control, public;
ALTER ROLE fondos_app SET timezone = 'Europe/Madrid';
ALTER ROLE fondos_app CONNECTION LIMIT 4;   -- P2/P3 are single-writer by design (§P1 concurrency isolation)

-- superset_ro: BI. Read-only, silver+gold only — never bronze (raw KIID text) or control (op logs).
ALTER ROLE superset_ro SET statement_timeout = '30s';   -- a runaway dashboard query must not starve the pipeline
ALTER ROLE superset_ro CONNECTION LIMIT 8;

GRANT USAGE ON SCHEMA bronze, silver, gold, control TO fondos_app;
GRANT USAGE ON SCHEMA silver, gold TO superset_ro;
-- deliberately NOT: superset_ro on bronze or control

GRANT CREATE ON SCHEMA bronze, silver, gold, control TO fondos_owner;

ALTER DEFAULT PRIVILEGES FOR ROLE fondos_owner IN SCHEMA bronze, silver, gold, control
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO fondos_app;
ALTER DEFAULT PRIVILEGES FOR ROLE fondos_owner IN SCHEMA bronze, silver, gold, control
  GRANT USAGE, SELECT ON SEQUENCES TO fondos_app;
ALTER DEFAULT PRIVILEGES FOR ROLE fondos_owner IN SCHEMA silver, gold
  GRANT SELECT ON TABLES TO superset_ro;

-- Apply the same grants retroactively once this script has created the tables in 10/20/30/35 —
-- run this block again (or call it from the deploy script) after those files apply, since
-- ALTER DEFAULT PRIVILEGES only governs objects created AFTER it runs, not objects that already
-- exist under a different owner at the time this file first ran.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA bronze, silver, gold, control TO fondos_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA bronze, silver, gold, control TO fondos_app;
GRANT SELECT ON ALL TABLES IN SCHEMA silver, gold TO superset_ro;

-- Single default tablespace. One physical NVMe device on this box — no custom tablespaces
-- (splitting indexes/temp onto a separate tablespace on the same device buys nothing and adds a
-- failure mode: a tablespace directory outside the volume mount is silent data loss on container
-- recreate).
