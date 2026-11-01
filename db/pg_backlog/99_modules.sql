-- Backlog v3 W4b (FND-0360): which modules a ticket changes or is about -- the facts that tickets are clustered by. Phase `modules`:
-- scripts/ops/migrate_backlog_v2.py --phase modules (dry run by default; ONE transaction; idempotent). Rollback: db/pg_backlog/down/99_modules_down.sql (drops the routine and the view; KEEPS the table, which
-- is history, and the widened group kind).
--
-- The registry is DERIVED, never maintained by hand (owner decision 2026-10-31, which overrides the rejection of the old `ticket_object` registry):
--   1. backlog.ticket_module_event: append-only. One row says "ticket X has role R on module M (optionally a symbol), seen from source S"; a retraction is a later row with retracted = true.
--        role   TARGET  the logic of this module will change to fix or upgrade (an agent declares it);
--               TOUCHED a commit that cites the ticket changed it (derived from git);
--               CITED   the ticket's text or object name mentions it (a PROPOSAL only: tools and registries are mentioned in logs all the time).
--        source DECLARED (TARGET or CITED, by an agent or a person), COMMIT (TOUCHED), TEXT / OBJECT_NAME (CITED): the last three only by a system:* actor.
--   2. backlog.v_ticket_module: the current set (the latest row of each (ticket, module, symbol, role, source), unless that row is a retraction).
--   3. backlog.sp_record_module: the only door. Idempotent (a fact already current is not written twice), and a derived fact is never written again over a retraction made by an agent or a person,
--      so a rejected proposal stays rejected.
--   4. group_tag.kind accepts 'MODULE': the clusters are groups named after the module, maintained by the autopilot through the existing sp_create_group / sp_group_member.
-- Nothing here is a gate: no transition needs a TARGET, and nobody approves a membership.

-- BEGIN backlog_modules
CREATE TABLE IF NOT EXISTS backlog.ticket_module_event (
    event_id         BIGSERIAL PRIMARY KEY,
    action_point_id  TEXT NOT NULL REFERENCES backlog.BACKLOG(action_point_id) ON DELETE CASCADE,
    module_path      TEXT NOT NULL CHECK (length(module_path) <= 300 AND module_path ~ '^[A-Za-z0-9_./-]+$' AND module_path !~ '(^/|\.\./|//)'),
    symbol           TEXT CHECK (symbol IS NULL OR symbol ~ '^[A-Za-z_][A-Za-z0-9_.]*$'),
    role             TEXT NOT NULL CHECK (role IN ('TARGET', 'TOUCHED', 'CITED')),
    source           TEXT NOT NULL CHECK (source IN ('DECLARED', 'COMMIT', 'TEXT', 'OBJECT_NAME')),
    actor            TEXT NOT NULL CHECK (actor ~ '^(human|agent|system):[A-Za-z0-9._-]+$'),
    evidence         TEXT,
    retracted        BOOLEAN NOT NULL DEFAULT false,
    event_ts         TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK ((source = 'DECLARED' AND role IN ('TARGET', 'CITED')) OR (source = 'COMMIT' AND role = 'TOUCHED') OR (source IN ('TEXT', 'OBJECT_NAME') AND role = 'CITED'))
);
CREATE INDEX IF NOT EXISTS idx_ticket_module_ap     ON backlog.ticket_module_event (action_point_id, event_id);
CREATE INDEX IF NOT EXISTS idx_ticket_module_module ON backlog.ticket_module_event (module_path, event_id);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = 'backlog.ticket_module_event'::regclass AND tgname = 'append_only' AND NOT tgisinternal) THEN
        CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON backlog.ticket_module_event FOR EACH ROW EXECUTE FUNCTION backlog.trg_append_only();
    END IF;
END $$;

CREATE OR REPLACE VIEW backlog.v_ticket_module AS
SELECT action_point_id, module_path, symbol, role, source, actor, evidence, event_ts
  FROM (SELECT DISTINCT ON (action_point_id, module_path, COALESCE(symbol, ''), role, source) *
          FROM backlog.ticket_module_event
         ORDER BY action_point_id, module_path, COALESCE(symbol, ''), role, source, event_id DESC) latest
 WHERE NOT retracted;

-- The only door. Returns the event id, or NULL when the call changes nothing (the fact is already current, a retraction of what is not current, or a derived fact over a retraction of a person).
CREATE OR REPLACE FUNCTION backlog.sp_record_module(
    p_ap_id text, p_module_path text, p_symbol text, p_role text, p_source text, p_evidence text, p_actor text, p_retracted boolean DEFAULT false) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, backlog AS $$
DECLARE
    v_last   backlog.ticket_module_event%ROWTYPE;
    v_id     bigint;
BEGIN
    IF p_actor IS NULL OR NOT backlog.fn_actor_ok(p_actor) OR NOT backlog.fn_actor_authorized(p_actor) THEN
        RAISE EXCEPTION 'sp_record_module: actor % is not canonical or not authorized from this connection', p_actor USING ERRCODE = '42501';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM backlog.BACKLOG WHERE action_point_id = p_ap_id) THEN
        RAISE EXCEPTION 'sp_record_module: ticket % does not exist', p_ap_id USING ERRCODE = '22023';
    END IF;
    IF p_source IN ('COMMIT', 'TEXT', 'OBJECT_NAME') AND p_actor NOT LIKE 'system:%' AND NOT p_retracted THEN
        RAISE EXCEPTION 'sp_record_module: a % fact is derived, so only a system:* actor records it (got %); an agent declares with source DECLARED', p_source, p_actor USING ERRCODE = '42501';
    END IF;
    IF p_source = 'DECLARED' AND p_actor LIKE 'system:%' THEN
        RAISE EXCEPTION 'sp_record_module: DECLARED is what an agent or a person says, not a system actor (got %)', p_actor USING ERRCODE = '42501';
    END IF;
    SELECT * INTO v_last FROM backlog.ticket_module_event
     WHERE action_point_id = p_ap_id AND module_path = p_module_path AND COALESCE(symbol, '') = COALESCE(p_symbol, '') AND role = p_role AND source = p_source
     ORDER BY event_id DESC LIMIT 1;
    IF FOUND THEN
        IF p_retracted = v_last.retracted THEN
            RETURN NULL;                                                                  -- already so: nothing to write
        END IF;
        IF NOT p_retracted AND v_last.retracted AND p_actor LIKE 'system:%' AND v_last.actor NOT LIKE 'system:%' THEN
            RETURN NULL;                                                                  -- a person's or an agent's rejection is never undone by the derivation
        END IF;
    ELSIF p_retracted THEN
        RETURN NULL;                                                                      -- nothing to retract
    END IF;
    INSERT INTO backlog.ticket_module_event (action_point_id, module_path, symbol, role, source, actor, evidence, retracted)
    VALUES (p_ap_id, p_module_path, NULLIF(btrim(p_symbol), ''), p_role, p_source, p_actor, NULLIF(btrim(p_evidence), ''), p_retracted)
    RETURNING event_id INTO v_id;
    RETURN v_id;
END;
$$;

-- A cluster is a group of kind MODULE (named after the module, maintained by the autopilot): widen the kind check of the groups phase.
DO $$
DECLARE c record;
BEGIN
    FOR c IN SELECT conname FROM pg_constraint
              WHERE conrelid = 'backlog.group_tag'::regclass AND contype = 'c' AND pg_get_constraintdef(oid) LIKE '%TRAIN%' AND pg_get_constraintdef(oid) NOT LIKE '%MODULE%'
    LOOP
        EXECUTE format('ALTER TABLE backlog.group_tag DROP CONSTRAINT %I', c.conname);
        ALTER TABLE backlog.group_tag ADD CONSTRAINT group_tag_kind_check CHECK (kind IN ('TRAIN', 'THEME', 'DECISION', 'MODULE'));
    END LOOP;
END $$;

-- Ownership and privileges: the routine belongs to backlog_owner and runs as it; an agent declares, the autopilot derives and keeps the clusters, the writer keeps the person's route; all read.
DO $$
DECLARE r record;
BEGIN
    ALTER TABLE backlog.ticket_module_event OWNER TO backlog_owner;
    ALTER VIEW backlog.v_ticket_module OWNER TO backlog_owner;
    FOR r IN SELECT p.oid::regprocedure AS sig FROM pg_proc p WHERE p.pronamespace = 'backlog'::regnamespace AND p.proname = 'sp_record_module'
    LOOP
        EXECUTE format('ALTER FUNCTION %s OWNER TO backlog_owner', r.sig);
        EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC', r.sig);
        EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO backlog_agent, backlog_autopilot', r.sig);
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_writer') THEN
            EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO backlog_writer', r.sig);
        END IF;
    END LOOP;
    GRANT SELECT ON backlog.ticket_module_event, backlog.v_ticket_module TO backlog_agent, backlog_autopilot;
    GRANT SELECT ON backlog.group_member, backlog.group_tag, backlog.v_group_member TO backlog_autopilot;
    GRANT EXECUTE ON FUNCTION backlog.sp_create_group(text, text, text, text), backlog.sp_group_member(text, text, text, text, text) TO backlog_autopilot;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_writer') THEN
        GRANT SELECT ON backlog.ticket_module_event, backlog.v_ticket_module TO backlog_writer;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_reader') THEN
        GRANT SELECT ON backlog.ticket_module_event, backlog.v_ticket_module TO backlog_reader;
    END IF;
END $$;
-- END backlog_modules
