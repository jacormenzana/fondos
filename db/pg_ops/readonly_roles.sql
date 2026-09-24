-- readonly_roles.sql — least-privilege access for humans and BI tools (applied by hand, idempotent).
-- Run as the database owner against the `fondos` database:
--   scripts/ops/create_readonly_roles.sh  (also generates + sets the passwords; none are stored here)
--
--   superset_ro : BI tools (Metabase/Superset). Already created by db/pg/00_roles_schemas.sql with
--                 SELECT on silver + gold ONLY (never bronze: raw KIID text; never control: op logs),
--                 statement_timeout 30s, 8 connections. This script only gives it a password.
--   fondos_ro   : analyst/DBeaver. Read-only on ALL four schemas (bronze NAV data is what analysts
--                 browse). Read-only is enforced at the ROLE level (default_transaction_read_only),
--                 not just by grants, so an accidental UPDATE/DELETE/DROP in a SQL editor fails.

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fondos_ro') THEN
    CREATE ROLE fondos_ro LOGIN;
  END IF;
END $$;

ALTER ROLE fondos_ro SET default_transaction_read_only = on;
ALTER ROLE fondos_ro SET statement_timeout = '10min';
ALTER ROLE fondos_ro SET search_path = gold, silver, bronze, control, public;
ALTER ROLE fondos_ro CONNECTION LIMIT 5;

GRANT CONNECT ON DATABASE fondos TO fondos_ro;
GRANT USAGE ON SCHEMA bronze, silver, gold, control TO fondos_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA bronze, silver, gold, control TO fondos_ro;
-- tables created later by the owner (new partitions, new tables, matviews) are readable too
ALTER DEFAULT PRIVILEGES FOR ROLE fondos_owner IN SCHEMA bronze, silver, gold, control
  GRANT SELECT ON TABLES TO fondos_ro;
