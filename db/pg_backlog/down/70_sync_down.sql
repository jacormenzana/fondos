-- Rollback of Phase C slice 1 (FND-0334): scripts/ops/migrate_backlog_v2.py --phase sync --down (dry run by default; ONE transaction).
-- Drops the views and the routines and rebuilds v_backlog_violations without the Phase C codes. KEEPS backlog.ops_event and backlog.close_condition
-- (append-only history): applying the phase again resumes with every recorded fact and condition. No ticket, log or link is touched.

-- BEGIN backlog_sync_down
SELECT backlog.fn_rebuild_violations('v_violations_c');     -- the union without this phase's view first: it depends on it
DROP VIEW IF EXISTS backlog.v_violations_c;
DROP VIEW IF EXISTS backlog.v_status_feed;
DROP VIEW IF EXISTS backlog.v_close_condition;
DROP FUNCTION IF EXISTS backlog.sp_retire_close_condition(bigint, text, text);
DROP FUNCTION IF EXISTS backlog.sp_add_close_condition(text, text, jsonb, text, text);
DROP FUNCTION IF EXISTS backlog.sp_record_ops_event(text, text, text, text, jsonb, timestamptz, text);
-- END backlog_sync_down
