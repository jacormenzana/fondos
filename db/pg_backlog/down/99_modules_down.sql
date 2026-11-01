-- Rollback of Backlog v3 W4b phase `modules`: scripts/ops/migrate_backlog_v2.py --phase modules --down (dry run by default; ONE transaction).
-- Drops the routine and the view and takes the grants of the routine away. KEEPS backlog.ticket_module_event (append-only history of which module each ticket touched or targeted), the widened
-- group kind (MODULE groups, if any were made, stay valid) and the grants the autopilot received on the group routines (it needs them only for MODULE groups; they carry no other risk).

-- BEGIN backlog_modules_down
DROP FUNCTION IF EXISTS backlog.sp_record_module(text, text, text, text, text, text, text, boolean);
DROP VIEW IF EXISTS backlog.v_ticket_module;
-- END backlog_modules_down
