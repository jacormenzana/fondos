-- Backlog v2 identity: the owner-only role for human actors (FND-0337; owner decision of 2026-10-27, option D of
-- doc/especificacion/EVALUACION_LIMITACIONES_BACKLOG_V2.md). Requires step A-1 as re-applied with FND-0337 (backlog.fn_actor_authorized must exist).
-- Applied by the OWNER through scripts/ops/migrate_backlog_v2.py --phase human (dry run by default), which runs exactly the block between the markers below.
-- Idempotent. Staged rollback: db/pg_backlog/down/32_human_role_down.sql.
--
-- The role is created WITHOUT a password and cannot log in until the owner sets one IN HIS OWN SESSION:
--     ALTER ROLE backlog_human PASSWORD '<chosen by the owner>';        (psql, or \password backlog_human)
-- and keeps the connection string in his own .pgpass / a prompt, as FONDOS_BACKLOG_PG_DSN_HUMAN typed when needed: NEVER in the repository .env and never exported in a shell
-- that also launches agents (they would inherit it, and the control would stop meaning anything). No script and no session of this repository ever sees the password.
--
-- From the moment this role exists, backlog.fn_actor_authorized refuses actors human:* to every connection that is not backlog_human (or a member of it, or a superuser):
-- sp_open_ticket and sp_transition refuse, and after step A-2 the author triggers of logs and link events too. Assigning work to a human stays allowed.

-- BEGIN backlog_human_role
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_human') THEN
        CREATE ROLE backlog_human LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_writer') THEN
        GRANT backlog_writer TO backlog_human;          -- the same table and routine privileges as the writer, plus the right to be human
    ELSE
        RAISE NOTICE 'role backlog_writer does not exist: backlog_human has no privileges yet';
    END IF;
END $$;
-- END backlog_human_role
