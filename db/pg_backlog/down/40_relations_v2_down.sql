-- ROLLBACK of db/pg_backlog/40_relations_v2.sql (FND-0333, Phase B). Run by scripts/ops/migrate_backlog_v2.py --phase relations --down --apply (dry run without --apply).
--
-- DISABLES Phase B and KEEPS EVERY ROW (owner order 2026-10-29: the native rollback must never drop the append-only history). It removes the behavior (routines, views, the cycle trigger) and
-- puts the delivery-gate hooks of sp_transition back to their no-op stubs; it never drops a table and never touches a row:
--   kept: review_flag, hold_event, gate_switch, gate_audit_event (with their append-only triggers, ownership and privileges), the column gate_switch.override, the link vocabulary
--         (IMPACTS, SUPERSEDES) and the column backlog_link_event.origin. Tickets, logs, status events, wait events and link events are never touched.
--   removed: sp_ack_review, sp_raise_hold, sp_release_hold, sp_set_gate (both the four- and the five-argument form), sp_annotate_wait, the views over the Phase B tables, the DEPENDS_ON cycle trigger.
--   effect: sp_transition keeps working but the delivery gate stops evaluating and recording at once (fn_gate_mode answers OFF), and a DEPENDS_ON cycle is no longer refused.
-- Applying `--phase relations` again re-creates the routines and views on top of the kept tables: the gate comes back in the mode its last row says (ENFORCE stays ENFORCE).
-- Backing out ONLY an upgrade of Phase B (for example the executive override of FND-0350): run this, check out the previous code, run its `--phase relations --apply`. No row is lost on the way.
-- There is deliberately NO script that drops the Phase B tables: removing history is a decision of the owner, made by hand after exporting them.

-- BEGIN backlog_relations_v2_down
DROP TRIGGER IF EXISTS link_cycle ON backlog.backlog_link_event;
DROP FUNCTION IF EXISTS backlog.trg_link_cycle();

CREATE OR REPLACE FUNCTION backlog.fn_gate_blockers(p_ap_id text, p_to text, p_delivery boolean) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, backlog AS $f$ SELECT '[]'::jsonb $f$;
CREATE OR REPLACE FUNCTION backlog.fn_raise_review_flags(p_ap_id text, p_to text, p_actor text) RETURNS integer
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, backlog AS $f$ SELECT 0 $f$;
CREATE OR REPLACE FUNCTION backlog.fn_gate_mode(p_gate text) RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, backlog AS $f$ SELECT 'OFF'::text $f$;
CREATE OR REPLACE FUNCTION backlog.fn_gate_record(p_ap_id text, p_to text, p_delivery boolean, p_blockers jsonb, p_mode text, p_actor text, p_forced boolean) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, backlog AS $f$ BEGIN RETURN; END $f$;

DROP FUNCTION IF EXISTS backlog.sp_ack_review(bigint, text, text);
DROP FUNCTION IF EXISTS backlog.sp_raise_hold(text, text, text, text);
DROP FUNCTION IF EXISTS backlog.sp_release_hold(bigint, text, text);
DROP FUNCTION IF EXISTS backlog.sp_set_gate(text, text, text, text);
DROP FUNCTION IF EXISTS backlog.sp_set_gate(text, text, text, text, boolean);
DROP FUNCTION IF EXISTS backlog.sp_annotate_wait(text, text, text, text, text, date, text, text);
DROP VIEW IF EXISTS backlog.v_gate_override_event;
DROP VIEW IF EXISTS backlog.v_gate_audit_window;
DROP VIEW IF EXISTS backlog.v_gate_audit_violation;
DROP VIEW IF EXISTS backlog.v_supersede_suggestion;
DROP VIEW IF EXISTS backlog.v_open_hold;
DROP VIEW IF EXISTS backlog.v_open_review_flag;

DO $$
BEGIN
    RAISE NOTICE 'Phase B is disabled: the tables review_flag, hold_event, gate_switch and gate_audit_event, the link vocabulary and the column origin are KEPT with all their rows (append-only history)';
END $$;
-- END backlog_relations_v2_down
