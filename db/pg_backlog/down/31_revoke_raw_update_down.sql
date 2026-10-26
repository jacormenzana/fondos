-- ROLLBACK of db/pg_backlog/31_revoke_raw_update.sql (FND-0331, step A-2): gives backlog_writer back the privileges it had before the revoke
-- (table-level INSERT and UPDATE on backlog.BACKLOG) and removes the author check on logs and link events. Nothing is dropped or rewritten in the data.
-- Run by scripts/ops/migrate_backlog_v2.py --phase revoke --down --apply.

-- BEGIN backlog_revoke_raw_update_down
DROP TRIGGER IF EXISTS logs_actor ON backlog.BACKLOG_LOGS;
DROP TRIGGER IF EXISTS link_event_actor ON backlog.backlog_link_event;
DROP FUNCTION IF EXISTS backlog.trg_actor_known();
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_writer') THEN
        REVOKE UPDATE (title, nature, schema_name, object_type, object_name, priority, session_id, scenario_description)
            ON backlog.BACKLOG FROM backlog_writer;
        GRANT INSERT, UPDATE ON backlog.BACKLOG TO backlog_writer;
    END IF;
END $$;
-- END backlog_revoke_raw_update_down
