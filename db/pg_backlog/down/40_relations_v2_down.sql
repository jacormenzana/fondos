-- ROLLBACK of db/pg_backlog/40_relations_v2.sql (FND-0333 / FND-0350, Phase B). Run by scripts/ops/migrate_backlog_v2.py --phase relations --down --apply (dry run without --apply).
--
-- ATOMIC and self-contained (owner order 2026-10-29): it returns Phase B to the definitions of the PREVIOUS release (master 0979d80, before the executive override of FND-0350) in ONE
-- transaction, and the delivery gate never leaves the mode it was in. It reads the gate mode first, never touches the gate engine (fn_gate_mode, fn_gate_blockers, fn_gate_record,
-- fn_raise_review_flags, sp_raise_hold, sp_release_hold, sp_ack_review, sp_annotate_wait, the cycle trigger), and ends by checking that the mode and the rows of the Phase B tables are
-- exactly what it found: if they are not, it raises and the WHOLE rollback is undone. There is no window with the gate off, and nothing to re-arm afterwards.
--   removed: sp_set_gate with five arguments (the executive override), the view v_gate_override_event, the column last_override_ts of v_gate_audit_window.
--   restored: sp_set_gate with four arguments and v_gate_audit_window, exactly as the previous release defined them, with their ownership and privileges.
--   kept, with every row: review_flag, hold_event, gate_switch (and its inert column override), gate_audit_event, the link vocabulary and the column origin. Tickets, logs, status events,
--         wait events and link events are never touched.
-- Switching the gate OFF or to AUDIT is NOT a rollback: it is a governed, recorded decision, made with backlog.sp_set_gate by the owner (a human actor on the owner-only connection).
-- When Phase B changes again, this file must be re-baselined to the definitions of the release that is then in production (a test pins the shape).

-- BEGIN backlog_relations_v2_down
DO $$
BEGIN
    IF to_regclass('backlog.gate_switch') IS NULL OR to_regprocedure('backlog.fn_gate_mode(text)') IS NULL THEN
        RAISE EXCEPTION 'rollback refused: Phase B is not applied, there is nothing to roll back' USING ERRCODE = '22023';
    END IF;
    PERFORM set_config('backlog.rb_gate_mode', backlog.fn_gate_mode('DELIVERY'), true);
    PERFORM set_config('backlog.rb_gate_rows', (SELECT count(*) || ':' || COALESCE(max(event_id), 0) FROM backlog.gate_switch)
                       || '/' || (SELECT count(*) FROM backlog.hold_event) || '/' || (SELECT count(*) FROM backlog.review_flag) || '/' || (SELECT count(*) FROM backlog.gate_audit_event), true);
END $$;

DROP FUNCTION IF EXISTS backlog.sp_set_gate(text, text, text, text, boolean);
DROP VIEW IF EXISTS backlog.v_gate_override_event;
DROP VIEW IF EXISTS backlog.v_gate_audit_window;

CREATE OR REPLACE VIEW backlog.v_gate_audit_window AS
WITH cur AS (SELECT mode, event_ts FROM backlog.gate_switch WHERE gate = 'DELIVERY' ORDER BY event_id DESC LIMIT 1)
SELECT COALESCE((SELECT mode FROM cur), 'AUDIT') AS mode,
       (SELECT event_ts FROM cur) AS since_ts,
       CASE WHEN COALESCE((SELECT mode FROM cur), 'AUDIT') = 'AUDIT' THEN extract(epoch FROM clock_timestamp() - (SELECT event_ts FROM cur)) / 3600.0 END AS hours_in_audit,
       (SELECT count(*) FROM backlog.gate_audit_event e WHERE e.event_ts >= (SELECT event_ts FROM cur)) AS evaluated,
       (SELECT count(DISTINCT e.action_point_id) FROM backlog.gate_audit_event e WHERE e.event_ts >= (SELECT event_ts FROM cur)
           AND e.event_ts > clock_timestamp() - interval '48 hours') AS tickets_48h,
       5 AS required_tickets,
       (SELECT count(*) FROM backlog.gate_audit_event e WHERE jsonb_array_length(e.blockers) > 0 AND e.event_ts >= (SELECT event_ts FROM cur)) AS violations_since,
       (SELECT count(*) FROM backlog.gate_audit_event e WHERE jsonb_array_length(e.blockers) > 0 AND e.event_ts >= (SELECT event_ts FROM cur)
           AND e.event_ts > clock_timestamp() - interval '48 hours') AS violations_48h,
       COALESCE((SELECT mode FROM cur), 'AUDIT') = 'AUDIT'
         AND COALESCE(extract(epoch FROM clock_timestamp() - (SELECT event_ts FROM cur)) / 3600.0, 0) >= 48
         AND (SELECT count(DISTINCT e.action_point_id) FROM backlog.gate_audit_event e WHERE e.event_ts >= (SELECT event_ts FROM cur)
                 AND e.event_ts > clock_timestamp() - interval '48 hours') >= 5
         AND (SELECT count(*) FROM backlog.gate_audit_event e WHERE jsonb_array_length(e.blockers) > 0 AND e.event_ts >= (SELECT event_ts FROM cur)
                 AND e.event_ts > clock_timestamp() - interval '48 hours') = 0 AS clear;

CREATE OR REPLACE FUNCTION backlog.sp_set_gate(p_gate text, p_mode text, p_actor text, p_reason text) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, backlog AS $$
DECLARE v_id bigint; w record;
BEGIN
    IF p_actor IS NULL OR p_actor NOT LIKE 'human:%' OR NOT backlog.fn_actor_ok(p_actor) OR NOT backlog.fn_actor_authorized(p_actor) THEN
        RAISE EXCEPTION 'sp_set_gate: only a human actor, through the owner-only connection, changes a gate (got %)', p_actor USING ERRCODE = '42501';
    END IF;
    IF p_mode IS NULL OR p_mode NOT IN ('OFF', 'AUDIT', 'ENFORCE') THEN
        RAISE EXCEPTION 'sp_set_gate: mode must be OFF, AUDIT or ENFORCE (got %)', p_mode USING ERRCODE = '22023';
    END IF;
    IF p_mode = 'ENFORCE' THEN
        SELECT * INTO w FROM backlog.v_gate_audit_window;
        IF NOT COALESCE(w.clear, false) THEN
            RAISE EXCEPTION 'sp_set_gate: ENFORCE needs a clear audit window (mode %, % hours in audit, gated moves on % different tickets in the last 48 hours, % violations in them; needs 48 hours, % different tickets, 0 violations)',
                w.mode, round(COALESCE(w.hours_in_audit, 0)::numeric, 1), w.tickets_48h, w.violations_48h, w.required_tickets USING ERRCODE = '22023';
        END IF;
    END IF;
    INSERT INTO backlog.gate_switch (gate, mode, actor, reason) VALUES (p_gate, p_mode, p_actor, p_reason) RETURNING event_id INTO v_id;
    RETURN v_id;
END;
$$;

ALTER VIEW backlog.v_gate_audit_window OWNER TO backlog_owner;
ALTER FUNCTION backlog.sp_set_gate(text, text, text, text) OWNER TO backlog_owner;
REVOKE EXECUTE ON FUNCTION backlog.sp_set_gate(text, text, text, text) FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_writer') THEN
        GRANT EXECUTE ON FUNCTION backlog.sp_set_gate(text, text, text, text) TO backlog_writer;
        GRANT SELECT ON backlog.v_gate_audit_window TO backlog_writer;
    END IF;
END $$;

-- gate invariant: the mode and every row of the Phase B tables are exactly what this rollback found; otherwise the whole rollback is undone
DO $$
DECLARE v_rows text;
BEGIN
    v_rows := (SELECT count(*) || ':' || COALESCE(max(event_id), 0) FROM backlog.gate_switch)
              || '/' || (SELECT count(*) FROM backlog.hold_event) || '/' || (SELECT count(*) FROM backlog.review_flag) || '/' || (SELECT count(*) FROM backlog.gate_audit_event);
    IF backlog.fn_gate_mode('DELIVERY') IS DISTINCT FROM current_setting('backlog.rb_gate_mode') THEN
        RAISE EXCEPTION 'rollback aborted: the gate mode would change from % to % (nothing was changed)', current_setting('backlog.rb_gate_mode'), backlog.fn_gate_mode('DELIVERY') USING ERRCODE = '55000';
    END IF;
    IF v_rows IS DISTINCT FROM current_setting('backlog.rb_gate_rows') THEN
        RAISE EXCEPTION 'rollback aborted: the rows of the Phase B tables would change from % to % (nothing was changed)', current_setting('backlog.rb_gate_rows'), v_rows USING ERRCODE = '55000';
    END IF;
    IF to_regprocedure('backlog.sp_set_gate(text,text,text,text)') IS NULL OR to_regprocedure('backlog.sp_set_gate(text,text,text,text,boolean)') IS NOT NULL THEN
        RAISE EXCEPTION 'rollback aborted: sp_set_gate is not in the previous release shape (nothing was changed)' USING ERRCODE = '55000';
    END IF;
    RAISE NOTICE 'Phase B is back at the previous release; the gate stays % and no row changed', current_setting('backlog.rb_gate_mode');
END $$;
-- END backlog_relations_v2_down
