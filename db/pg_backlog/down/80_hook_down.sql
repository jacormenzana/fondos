-- Rollback of Phase C slice 2 (FND-0334): scripts/ops/migrate_backlog_v2.py --phase hook --down (dry run by default; ONE transaction).
-- Drops the view and the routines and rebuilds v_backlog_violations without V-HOOK-BYPASS. KEEPS backlog.gate_check and backlog.hook_bypass (append-only history) and
-- the role backlog_gate (it keeps its schema USAGE and no routine; the owner drops it when he wants) and every hold already raised (a hold is released by a person, never by a rollback): applying the phase again resumes with every recorded fact. No ticket, log or link is touched.

-- BEGIN backlog_hook_down
SELECT backlog.fn_rebuild_violations('v_violations_c2');     -- the union without this phase's view first: it depends on it
DROP VIEW IF EXISTS backlog.v_violations_c2;
DROP FUNCTION IF EXISTS backlog.sp_record_hook_bypass(text, text, text, timestamptz, text);
DROP FUNCTION IF EXISTS backlog.sp_gate_forward(text, text, text, text);
DROP FUNCTION IF EXISTS backlog.fn_gate_statuses(text[]);
DROP FUNCTION IF EXISTS backlog.sp_gate_check(text, text[], text);
-- END backlog_hook_down
