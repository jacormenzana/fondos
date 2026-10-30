-- Rollback of Phase D (FND-0335): scripts/ops/migrate_backlog_v2.py --phase groups --down (dry run by default; ONE transaction).
-- Drops the views and the two routines. KEEPS backlog.group_tag and backlog.group_member: they are append-only history, and applying the phase again
-- resumes with every group and membership event (the RELEASE_WITH migration skips groups that already exist). No ticket, log or link is touched.

-- BEGIN backlog_groups_violations_down
DROP VIEW IF EXISTS backlog.v_impact_closure;
DROP VIEW IF EXISTS backlog.v_backlog_lean_metrics;
DROP VIEW IF EXISTS backlog.v_backlog_violations;
DROP VIEW IF EXISTS backlog.v_group_status;
DROP VIEW IF EXISTS backlog.v_group_member;
DROP FUNCTION IF EXISTS backlog.sp_group_member(text, text, text, text, text);
DROP FUNCTION IF EXISTS backlog.sp_create_group(text, text, text, text);
-- END backlog_groups_violations_down
