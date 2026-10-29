-- ROLLBACK of db/pg_backlog/50_iam.sql (FND-0354). fn_actor_bound goes back to its no-op stub (it is referenced by fn_actor_authorized, so it is replaced, not dropped); the new routines,
-- the binding history and the three roles go. DESTRUCTIVE for the binding history (backlog.actor_login_event); nothing in tickets, logs or events is touched.
-- Run by scripts/ops/migrate_backlog_v2.py --phase iam --down --apply.

-- BEGIN backlog_iam_down
CREATE OR REPLACE FUNCTION backlog.fn_actor_bound(p_actor text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, backlog AS $$ SELECT true $$;
DROP FUNCTION IF EXISTS backlog.sp_report_incident(text, text, text, text, text, text, interval, text, text, text, text);
DROP FUNCTION IF EXISTS backlog.sp_append_log(text, text, text);
DROP FUNCTION IF EXISTS backlog.sp_bind_login(text, text, text, text, boolean, text, text);
DROP FUNCTION IF EXISTS backlog.sp_bind_login(text, text, text, text, boolean, text);
DROP VIEW IF EXISTS backlog.v_binding_audit;
DROP VIEW IF EXISTS backlog.v_actor_login;
DROP TABLE IF EXISTS backlog.actor_login_event;
DO $$
DECLARE r text;
BEGIN
    FOREACH r IN ARRAY ARRAY['backlog_logger', 'backlog_incident', 'backlog_reader'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
            EXECUTE format('DROP OWNED BY %I', r);
            EXECUTE format('DROP ROLE %I', r);
        END IF;
    END LOOP;
END $$;
-- END backlog_iam_down
