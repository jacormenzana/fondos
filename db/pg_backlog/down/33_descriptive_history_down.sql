-- ROLLBACK of db/pg_backlog/33_descriptive_history.sql (FND-0338): removes the trigger, the function, the view and the history table. The recorded history of descriptive
-- changes is LOST (it exists nowhere else), so export it first if it matters: SELECT * FROM backlog.field_change_event. Tickets, logs, link events and status events are not
-- touched. Run by scripts/ops/migrate_backlog_v2.py --phase history --down --apply.

-- BEGIN backlog_descriptive_history_down
DROP TRIGGER IF EXISTS field_changes ON backlog.BACKLOG;
DROP VIEW IF EXISTS backlog.v_field_change_attribution;
DROP FUNCTION IF EXISTS backlog.trg_field_changes();
DROP TABLE IF EXISTS backlog.field_change_event;
-- END backlog_descriptive_history_down
