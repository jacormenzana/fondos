-- ROLLBACK of db/pg_backlog/32_human_role.sql (FND-0337): removes the owner-only role. From then on backlog.fn_actor_authorized no longer binds human actors (it only
-- looks for the role), so the owner goes back to the state before this step. Nothing in tickets, logs or events is touched. Run by
-- scripts/ops/migrate_backlog_v2.py --phase human --down --apply.

-- BEGIN backlog_human_role_down
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_human') THEN
        DROP OWNED BY backlog_human;
        DROP ROLE backlog_human;
    END IF;
END $$;
-- END backlog_human_role_down
