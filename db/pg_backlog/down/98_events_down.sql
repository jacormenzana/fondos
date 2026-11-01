-- Rollback of Backlog v3.1 phase `events`: scripts/ops/migrate_backlog_v2.py --phase events --down (dry run by default; ONE transaction).
-- Drops the guard trigger, its function and the routines of the phase and takes the grants away. KEEPS the tables (reopen_event, autopilot_mode_event: append-only history), the role backlog_facts
-- (it keeps no privilege), the widened gate_adjudication actor check and every DEPLOY fact already recorded. The behaviour of sp_transition (a reopening needs no p_force), of the V-FORCED view and of
-- sp_agent_transition belongs to the phases transitions / groups / autonomy and goes back only by re-applying an earlier version of those files.

-- BEGIN backlog_events_down
DROP TRIGGER IF EXISTS deploy_fact_guard ON backlog.ops_event;
DROP FUNCTION IF EXISTS backlog.trg_deploy_fact_guard();
DROP FUNCTION IF EXISTS backlog.sp_record_fact(text, text, text, jsonb, timestamptz);
DROP FUNCTION IF EXISTS backlog.sp_set_autopilot_mode(text, text, text);
DROP FUNCTION IF EXISTS backlog.fn_autopilot_mode();
DROP FUNCTION IF EXISTS backlog.sp_agent_adjudicate_overlap(text, text, text, text, text, text);
DROP FUNCTION IF EXISTS backlog.fn_auto_release_holds(text);
DROP FUNCTION IF EXISTS backlog.sp_rescind_reopen(text, text, text);
DROP FUNCTION IF EXISTS backlog.sp_reopen(text, text, text, text, text, text);
-- END backlog_events_down
