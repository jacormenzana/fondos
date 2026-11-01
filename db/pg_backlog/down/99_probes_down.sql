-- Rollback of Backlog v3 W4 phase `probes`: scripts/ops/migrate_backlog_v2.py --phase probes --down (dry run by default; ONE transaction).
-- Puts the stub of fn_probe_blockers back (no blocker, so the delivery gate no longer sees stale probes), drops the door and the view and takes their grants away. KEEPS backlog.probe_event
-- (append-only history of which tickets were invalidated and which probe runs passed); a later re-apply sees the same state again. It also KEEPS the kind tests_pass in the check of close_condition and
-- the default conditions the phase seeded (they are history, and a condition of a kind the check no longer accepted could not be read back); with the hook back to the stub nothing reads them.

-- BEGIN backlog_probes_down
DROP FUNCTION IF EXISTS backlog.sp_record_probe_event(text, text, boolean, text, text);
DROP VIEW IF EXISTS backlog.v_probe_status;
CREATE OR REPLACE FUNCTION backlog.fn_probe_blockers(p_ap_id text, p_to text, p_delivery boolean) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, backlog AS $f$ SELECT '[]'::jsonb $f$;
ALTER FUNCTION backlog.fn_probe_blockers(text, text, boolean) OWNER TO backlog_owner;
REVOKE EXECUTE ON FUNCTION backlog.fn_probe_blockers(text, text, boolean) FROM PUBLIC;
-- END backlog_probes_down
