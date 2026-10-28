-- ROLLBACK of db/pg_backlog/40_relations_v2.sql (FND-0333, Phase B). Run by scripts/ops/migrate_backlog_v2.py --phase relations --down --apply
-- (dry run without --apply).
--
-- What is lost: the review_flag, hold_event, gate_switch and gate_audit_event rows (export first if they matter). Tickets, logs, status events, wait events and link events are
-- never touched. sp_transition (step A-1) keeps working: fn_gate_blockers, fn_raise_review_flags, fn_gate_mode and fn_gate_record go back to their no-op stubs (mode OFF),
-- so the delivery gate stops evaluating and recording the moment this rollback commits. The link vocabulary (IMPACTS, SUPERSEDES) and the column `origin` are kept, with a NOTICE, when any link event
-- already uses them (link events are append-only history); otherwise they are restored to the pre-Phase-B shape.

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
DROP FUNCTION IF EXISTS backlog.sp_annotate_wait(text, text, text, text, text, date, text, text);
DROP VIEW IF EXISTS backlog.v_gate_audit_window;
DROP VIEW IF EXISTS backlog.v_gate_audit_violation;
DROP VIEW IF EXISTS backlog.v_supersede_suggestion;
DROP VIEW IF EXISTS backlog.v_open_hold;
DROP VIEW IF EXISTS backlog.v_open_review_flag;
DROP TABLE IF EXISTS backlog.gate_audit_event;
DROP TABLE IF EXISTS backlog.gate_switch;
DROP TABLE IF EXISTS backlog.hold_event;
DROP TABLE IF EXISTS backlog.review_flag;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM backlog.backlog_link_event WHERE link_type IN ('IMPACTS', 'SUPERSEDES') OR origin <> 'REVIEW') THEN
        RAISE NOTICE 'link events already use IMPACTS / SUPERSEDES or an origin other than REVIEW: the vocabulary and the column origin are KEPT (append-only history)';
    ELSE
        ALTER TABLE backlog.backlog_link_event DROP CONSTRAINT IF EXISTS backlog_link_event_link_type_check;
        ALTER TABLE backlog.backlog_link_event ADD CONSTRAINT backlog_link_event_link_type_check
            CHECK (link_type IN ('DEPENDS_ON','RELEASE_WITH','OVERLAPS','DUPLICATES','SPLIT_FROM','RELATED'));
        ALTER TABLE backlog.backlog_link_event DROP CONSTRAINT IF EXISTS backlog_link_event_origin_check;
        ALTER TABLE backlog.backlog_link_event DROP COLUMN IF EXISTS origin;
    END IF;
END $$;
-- END backlog_relations_v2_down
