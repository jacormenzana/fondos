-- Rollback of Backlog v3 W2 (FND-0357): scripts/ops/migrate_backlog_v2.py --phase autonomy --down (dry run by default; ONE transaction).
-- Drops the two wrappers and takes every grant away from the login. KEEPS the role backlog_agent (it keeps no privilege; the owner drops it when he wants), every link
-- event, log and ticket written through it (append-only history), and backlog.sp_append_log (it may have been defined by step iam; dropping a routine other code uses is not
-- a rollback's business). Applying the phase again resumes where it was.

-- BEGIN backlog_autonomy_down
DO $$
DECLARE r record;
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_agent') THEN
        FOR r IN SELECT p.oid::regprocedure AS sig FROM pg_proc p
                 WHERE p.pronamespace = 'backlog'::regnamespace
                   AND p.proname IN ('sp_open_ticket', 'sp_agent_transition', 'sp_record_link', 'sp_append_log', 'sp_annotate_wait', 'sp_create_group', 'sp_group_member',
                                     'sp_add_close_condition', 'sp_retire_close_condition')
        LOOP
            EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM backlog_agent', r.sig);
        END LOOP;
        REVOKE SELECT ON ALL TABLES IN SCHEMA backlog FROM backlog_agent;
        REVOKE USAGE ON SCHEMA backlog FROM backlog_agent;
    END IF;
END $$;
DROP FUNCTION IF EXISTS backlog.sp_agent_transition(text, text, text, text, text, text, text, text, text, date, text, text, text);
DROP FUNCTION IF EXISTS backlog.sp_record_link(text, text, text, text, text, text, text, text);
-- END backlog_autonomy_down
