-- Backlog v2 Phase A, step A-2 (REVOKE): enforce the single door (FND-0331; owner decisions D2 and D3 of 2026-10-26).
-- Requires step A-1 (30_transitions.sql). Applied by the OWNER through scripts/ops/migrate_backlog_v2.py --phase revoke, which REFUSES to run unless
-- backlog.v_shadow_stage shows at least 50 non-system status changes since the last V-00 (direct write) -- the owner's condition for A-2.
-- Idempotent. Staged rollback: db/pg_backlog/down/31_revoke_raw_update_down.sql.
--
-- ENUMERATED DIRECT-WRITE EXCEPTIONS (D3: listed and justified BEFORE the revoke; everything not listed here goes through a routine):
--  E1 backlog.BACKLOG_LOGS INSERT .......... append-only progress notes (append_log, close_release, humans). The log author is checked below
--                                            (trigger logs_actor): canonical, or a raw name that has an alias in backlog.actor_alias.
--  E2 backlog.backlog_link_event INSERT .... append-only, FK and CHECK enforced; Phase B moves link writes behind a routine. Same author check.
--  E3 backlog.BACKLOG UPDATE of title, nature, schema_name, object_type, object_name, priority, session_id, scenario_description:
--     descriptive and triage attributes, corrected in place by the owner's workflow; update_ts records that something changed but NOT what
--     (a known audit gap, listed in the proposal, risk 10; a field-change audit is a Phase D candidate).
--  Everything else is closed to the writer: INSERT on BACKLOG (tickets are opened with sp_open_ticket), UPDATE of status, closure_ts,
--  solution_type, deployment_ts, release_version, reporter, assignee (sp_transition), action_point_id, project_code, creation_ts, update_ts
--  (immutable by nature; update_ts is kept by its trigger), and any write to status_event, wait_event, raw_update_event (the routines).

-- BEGIN backlog_revoke_raw_update
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_writer') THEN
        RAISE NOTICE 'role backlog_writer does not exist: nothing to revoke';
    ELSE
        REVOKE INSERT, UPDATE ON backlog.BACKLOG FROM backlog_writer;
        GRANT UPDATE (title, nature, schema_name, object_type, object_name, priority, session_id, scenario_description)
            ON backlog.BACKLOG TO backlog_writer;
        REVOKE INSERT, UPDATE, DELETE ON backlog.status_event, backlog.wait_event, backlog.raw_update_event FROM backlog_writer;
    END IF;
END $$;

-- Author check for E1 and E2: a log or link event is written by a canonical identity, or by a raw name the owner has aliased.
CREATE OR REPLACE FUNCTION backlog.trg_actor_known() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, backlog AS $$
BEGIN
    IF NOT (backlog.fn_actor_ok(NEW.username) OR EXISTS (SELECT 1 FROM backlog.actor_alias a WHERE a.raw_name = NEW.username)) THEN
        RAISE EXCEPTION '% is not a known author: use human:<login> | agent:<model> | system:<name>, or have the owner add the raw name to backlog.actor_alias', NEW.username
            USING ERRCODE = '22023';
    END IF;
    -- FND-0337 (owner decision 2026-10-27): the same authorization rule as the routines, on the identity the name resolves to: human identities only through the
    -- owner-only role, and the generic agent:claude (what the raw names claude, claude-code, CLAUDE_SESSION... resolve to) refused for new writes.
    IF NOT backlog.fn_actor_authorized(COALESCE((SELECT a.canonical FROM backlog.actor_alias a WHERE a.raw_name = NEW.username), NEW.username)) THEN
        RAISE EXCEPTION '% may not write from this connection: human identities write only through the owner-only role; an agent names its model (agent:<model>), not claude or agent:claude', NEW.username
            USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END;
$$;
ALTER FUNCTION backlog.trg_actor_known() OWNER TO backlog_owner;
GRANT SELECT ON backlog.actor_alias TO backlog_owner;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_writer') THEN
        GRANT EXECUTE ON FUNCTION backlog.trg_actor_known() TO backlog_writer;
    END IF;
END $$;
DROP TRIGGER IF EXISTS logs_actor ON backlog.BACKLOG_LOGS;
CREATE TRIGGER logs_actor BEFORE INSERT ON backlog.BACKLOG_LOGS FOR EACH ROW EXECUTE FUNCTION backlog.trg_actor_known();
DROP TRIGGER IF EXISTS link_event_actor ON backlog.backlog_link_event;
CREATE TRIGGER link_event_actor BEFORE INSERT ON backlog.backlog_link_event FOR EACH ROW EXECUTE FUNCTION backlog.trg_actor_known();
-- END backlog_revoke_raw_update
