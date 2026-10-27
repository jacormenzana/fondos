-- Backlog v2 Phase A, step A-1 (ADDITIVE): the transition kernel (FND-0331; doc/especificacion/PROPUESTA_BACKLOG_V2.md section 6.2 and the
-- owner decisions of 2026-10-26: D1 non-superuser owner and pinned search_path, D2 additive step apart from the revoke, D3 writes centralised
-- in controlled routines, D5 explicit actor, D6 sp_open_ticket and sp_transition are FUNCTIONS).
-- Requires 00_schema.sql, 20_links.sql, 25_actor_alias.sql. Applied by the OWNER through scripts/ops/migrate_backlog_v2.py --phase transitions
-- (dry run by default), which runs exactly the block between the markers below (single source, P#11). Idempotent. Staged rollback:
-- db/pg_backlog/down/30_transitions_down.sql (the migration script also restores the legacy sp_close_release).
--
-- WHAT THIS STEP DOES NOT DO: it revokes nothing. backlog_writer keeps its table privileges, so every existing writer keeps working. A trigger
-- only RECORDS (never blocks) a direct write to a governed column as a V-00 event in backlog.raw_update_event, and the routines mark their own
-- writes with the setting backlog.via_proc = 'on', attached to each routine as a function-level SET clause (so it is restored when the routine
-- returns: a raw write made AFTER a routine call in the same transaction is still recorded; nested calls are safe). Step A-2 (31_revoke_raw_update.sql) removes the direct privileges and is
-- allowed to run only after 50 consecutive non-system transitions with zero V-00 (v_shadow_stage). The setting is NOT a security boundary (any
-- session can set it): the shadow stage detects habits and accidents; the revoke of A-2 is the enforcement.
--
-- Identity is attested by the caller (an actor is a parameter): the database cannot authenticate which agent uses a shared DSN. It can, and does,
-- refuse anything that is not canonical (human:<login> | agent:<model> | system:<name>).

-- BEGIN backlog_transitions

-- 0. The non-superuser owner of the privileged routines (D1). NOLOGIN: nobody connects as it.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_owner') THEN
        CREATE ROLE backlog_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
    END IF;
END $$;

GRANT USAGE ON SCHEMA backlog TO backlog_owner;
GRANT SELECT, INSERT, UPDATE ON backlog.BACKLOG TO backlog_owner;
GRANT SELECT, INSERT ON backlog.BACKLOG_LOGS TO backlog_owner;
GRANT SELECT, INSERT ON backlog.backlog_link_event TO backlog_owner;
GRANT SELECT ON backlog.actor_alias, backlog.v_backlog_link TO backlog_owner;
GRANT USAGE ON SEQUENCE backlog.seq_backlog_fnd, backlog.backlog_logs_log_id_seq, backlog.backlog_link_event_event_id_seq TO backlog_owner;

-- 1. Helpers.
CREATE OR REPLACE FUNCTION backlog.fn_actor_ok(p_actor text) RETURNS boolean
LANGUAGE sql IMMUTABLE AS
$$ SELECT p_actor IS NOT NULL AND p_actor ~ '^(human|agent|system):[A-Za-z0-9._-]+$' $$;

-- Who may write AS this actor from THIS connection (owner decision 2026-10-27, FND-0337). human:* only through the owner-only role backlog_human (a member of it, or a
-- superuser); the generic placeholder of the history, agent:claude, is refused for new writes (an agent names its model). Human identities are NOT enforced while the role
-- does not exist yet: the owner creates it with step 32 (migrate_backlog_v2.py --phase human) and only then does the rule bind. It looks at session_user (who logged in),
-- never at current_user (inside a SECURITY DEFINER routine that is the routine's owner).
CREATE OR REPLACE FUNCTION backlog.fn_actor_authorized(p_actor text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, backlog AS
$$ SELECT CASE
       WHEN p_actor IS NULL OR NOT backlog.fn_actor_ok(p_actor) THEN false
       WHEN p_actor = 'agent:claude' THEN false
       WHEN p_actor LIKE 'human:%' THEN NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_human')
                                        OR pg_has_role(session_user, 'backlog_human', 'MEMBER')
       ELSE true END $$;

-- Legal moves between two DIFFERENT statuses (NORMAS_BACKLOG section 2). CLOSED as a source (reopening) and delivery closures are
-- checked in sp_transition because they depend on more than the pair.
CREATE OR REPLACE FUNCTION backlog.fn_transition_legal(p_from text, p_to text) RETURNS boolean
LANGUAGE sql IMMUTABLE AS
$$ SELECT CASE p_from
        WHEN 'OPEN'             THEN p_to IN ('TODO', 'IN_PROGRESS', 'DEFERRED', 'CLOSED')
        WHEN 'TODO'             THEN p_to IN ('OPEN', 'IN_PROGRESS', 'DEFERRED', 'CLOSED')
        WHEN 'IN_PROGRESS'      THEN p_to IN ('TODO', 'BLOCKED', 'READY_FOR_DEPLOY', 'DEFERRED', 'CLOSED')
        WHEN 'BLOCKED'          THEN p_to IN ('IN_PROGRESS', 'DEFERRED', 'CLOSED')
        WHEN 'READY_FOR_DEPLOY' THEN p_to IN ('IN_PROGRESS', 'CLOSED')
        WHEN 'DEFERRED'         THEN p_to IN ('OPEN', 'TODO', 'IN_PROGRESS', 'CLOSED')
        ELSE false END $$;

CREATE OR REPLACE FUNCTION backlog.trg_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '%.% is append-only: % is refused', TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP;
END;
$$ LANGUAGE plpgsql;

-- 2. Audit tables. Written only by the routines below (D3); append-only.
CREATE TABLE IF NOT EXISTS backlog.status_event (
    event_id        BIGSERIAL PRIMARY KEY,
    action_point_id TEXT NOT NULL REFERENCES backlog.BACKLOG(action_point_id) ON DELETE CASCADE,
    from_status     TEXT CHECK (from_status IS NULL OR from_status IN ('OPEN','TODO','IN_PROGRESS','READY_FOR_DEPLOY','BLOCKED','DEFERRED','CLOSED')),
    to_status       TEXT NOT NULL CHECK (to_status IN ('OPEN','TODO','IN_PROGRESS','READY_FOR_DEPLOY','BLOCKED','DEFERRED','CLOSED')),
    actor           TEXT NOT NULL CHECK (actor ~ '^(human|agent|system):[A-Za-z0-9._-]+$'),
    session_id      TEXT,
    reason          TEXT,
    forced          BOOLEAN NOT NULL DEFAULT false,
    origin          TEXT NOT NULL DEFAULT 'LIVE' CHECK (origin IN ('LIVE', 'BACKFILL')),
    detail          JSONB,
    event_ts        TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()        -- clock, not now(): events of one transaction must still be ordered
);
CREATE INDEX IF NOT EXISTS idx_status_event_ap ON backlog.status_event (action_point_id, event_id);

CREATE TABLE IF NOT EXISTS backlog.wait_event (
    wait_id           BIGSERIAL PRIMARY KEY,
    action_point_id   TEXT NOT NULL REFERENCES backlog.BACKLOG(action_point_id) ON DELETE CASCADE,
    kind              TEXT NOT NULL CHECK (kind IN ('BLOCKED', 'DEFERRED')),
    blocker_ref       TEXT REFERENCES backlog.BACKLOG(action_point_id),
    condition_text    TEXT,
    revisit_by        DATE,
    revisit_on_event  TEXT,
    revisit_basis     TEXT NOT NULL DEFAULT 'DECLARED' CHECK (revisit_basis IN ('DECLARED', 'DEFAULT_90D')),
    actor             TEXT NOT NULL CHECK (actor ~ '^(human|agent|system):[A-Za-z0-9._-]+$'),
    event_ts          TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK (kind <> 'BLOCKED'  OR blocker_ref IS NOT NULL OR condition_text IS NOT NULL),
    CHECK (kind <> 'DEFERRED' OR revisit_by IS NOT NULL OR revisit_on_event IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_wait_event_ap ON backlog.wait_event (action_point_id, wait_id);

-- V-00: a write to a governed column that did not come through the routines (recorded, never blocked).
CREATE TABLE IF NOT EXISTS backlog.raw_update_event (
    event_id        BIGSERIAL PRIMARY KEY,
    action_point_id TEXT NOT NULL,
    op              TEXT NOT NULL CHECK (op IN ('INSERT', 'UPDATE')),
    old_row         JSONB,
    new_row         JSONB,
    db_user         TEXT NOT NULL,
    app_name        TEXT,
    event_ts        TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['status_event', 'wait_event', 'raw_update_event'] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = ('backlog.' || t)::regclass AND tgname = 'append_only' AND NOT tgisinternal) THEN
            EXECUTE format('CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON backlog.%I FOR EACH ROW EXECUTE FUNCTION backlog.trg_append_only()', t);
        END IF;
    END LOOP;
END $$;

-- 3. Shadow stage: record (not block) a write to a governed column made outside the routines.
CREATE OR REPLACE FUNCTION backlog.trg_governed_writes() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, backlog AS $$
BEGIN
    IF coalesce(current_setting('backlog.via_proc', true), '') = 'on' THEN
        RETURN NEW;
    END IF;
    IF TG_OP = 'INSERT' THEN
        INSERT INTO backlog.raw_update_event (action_point_id, op, new_row, db_user, app_name)
        VALUES (NEW.action_point_id, 'INSERT',
                jsonb_build_object('status', NEW.status, 'reporter', NEW.reporter, 'assignee', NEW.assignee), session_user,
                current_setting('application_name', true));
    ELSIF (OLD.status, OLD.closure_ts, OLD.solution_type, OLD.deployment_ts, OLD.release_version, OLD.reporter, OLD.assignee,
           OLD.action_point_id, OLD.project_code, OLD.creation_ts)
          IS DISTINCT FROM
          (NEW.status, NEW.closure_ts, NEW.solution_type, NEW.deployment_ts, NEW.release_version, NEW.reporter, NEW.assignee,
           NEW.action_point_id, NEW.project_code, NEW.creation_ts) THEN
        INSERT INTO backlog.raw_update_event (action_point_id, op, old_row, new_row, db_user, app_name)
        VALUES (OLD.action_point_id, 'UPDATE',
                jsonb_build_object('status', OLD.status, 'closure_ts', OLD.closure_ts, 'solution_type', OLD.solution_type,
                                   'deployment_ts', OLD.deployment_ts, 'release_version', OLD.release_version,
                                   'reporter', OLD.reporter, 'assignee', OLD.assignee),
                jsonb_build_object('status', NEW.status, 'closure_ts', NEW.closure_ts, 'solution_type', NEW.solution_type,
                                   'deployment_ts', NEW.deployment_ts, 'release_version', NEW.release_version,
                                   'reporter', NEW.reporter, 'assignee', NEW.assignee),
                session_user, current_setting('application_name', true));
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS governed_writes ON backlog.BACKLOG;
CREATE TRIGGER governed_writes
    BEFORE INSERT OR UPDATE OF status, closure_ts, solution_type, deployment_ts, release_version, reporter, assignee,
                               action_point_id, project_code, creation_ts
    ON backlog.BACKLOG
    FOR EACH ROW EXECUTE FUNCTION backlog.trg_governed_writes();

-- 4. Open a ticket: id from the sequence, canonical reporter, session id in the norm's format, first log and first status event in ONE call.
CREATE OR REPLACE FUNCTION backlog.sp_open_ticket(
    p_title text, p_nature text, p_object_type text, p_object_name text, p_priority text,
    p_actor text, p_session_id text, p_description text, p_first_log text,
    p_schema_name text DEFAULT NULL, p_project_code text DEFAULT 'FND')
RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, backlog SET backlog.via_proc = 'on' AS $$
DECLARE
    v_id text;
BEGIN
    IF NOT backlog.fn_actor_ok(p_actor) THEN
        RAISE EXCEPTION 'sp_open_ticket: actor % is not canonical (human:<login> | agent:<model> | system:<name>)', p_actor USING ERRCODE = '22023';
    END IF;
    IF NOT backlog.fn_actor_authorized(p_actor) THEN
        RAISE EXCEPTION 'sp_open_ticket: actor % is not authorized from this connection (human identities write only through the owner-only role; an agent names its model, not agent:claude)', p_actor USING ERRCODE = '42501';
    END IF;
    IF p_session_id IS NULL OR p_session_id !~ '^[A-Z0-9]+_[0-9]{8}_(WKSH|DEV|REVB|SYNC|ANL)_[A-Za-z0-9_-]+$' THEN
        RAISE EXCEPTION 'sp_open_ticket: session id % must look like FND_20261026_DEV_TOPIC (types WKSH DEV REVB SYNC ANL)', p_session_id USING ERRCODE = '22023';
    END IF;
    IF btrim(coalesce(p_title, '')) = '' OR btrim(coalesce(p_first_log, '')) = '' THEN
        RAISE EXCEPTION 'sp_open_ticket: title and first log are required' USING ERRCODE = '22023';
    END IF;
    SELECT 'FND-' || LPAD(nextval('backlog.seq_backlog_fnd')::text, 4, '0') INTO v_id;
    INSERT INTO backlog.BACKLOG (action_point_id, title, project_code, nature, schema_name, object_type, object_name, status, priority,
                                 reporter, session_id, scenario_description)
    VALUES (v_id, p_title, p_project_code, p_nature, p_schema_name, p_object_type, p_object_name, 'OPEN', p_priority,
            p_actor, p_session_id, p_description);
    INSERT INTO backlog.BACKLOG_LOGS (action_point_id, username, comment) VALUES (v_id, p_actor, p_first_log);
    INSERT INTO backlog.status_event (action_point_id, from_status, to_status, actor, session_id, reason)
    VALUES (v_id, NULL, 'OPEN', p_actor, p_session_id, 'opened');
    RETURN v_id;
END;
$$;

-- 5. Move a ticket: the ONLY routine that changes status, closure fields, release and assignee (D3). Returns the status_event id.
--    Closing: solution_type required. CODE_FIX / DATA_FIX / WORKAROUND are DELIVERY closures: they come from READY_FOR_DEPLOY and need a release label
--    R<yyyy>.<mm>-<TAG>; the routine sets closure_ts and deployment_ts. WORKAROUND needs a follow-up ticket (p_followup) or opens an OPT one.
--    REJECTED needs the superseding ticket (p_followup); DUPLICATED needs the kept ticket (p_followup) and records the DUPLICATES link.
--    BLOCKED needs p_blocker (a ticket; the DEPENDS_ON link is guaranteed) or p_condition; DEFERRED needs p_revisit_by or p_revisit_on_event.
--    Reopening a CLOSED ticket, an illegal move and the REJECTED/closure shortcuts need p_force and a reason of 40+ characters; the use is recorded.
--    The Phase B gate (open DEPENDS_ON prerequisite, holds, review flags) is added there; this step has none.
CREATE OR REPLACE FUNCTION backlog.sp_transition(
    p_ap_id text, p_to text, p_actor text, p_session_id text, p_comment text,
    p_solution_type text DEFAULT NULL, p_release_version text DEFAULT NULL,
    p_blocker text DEFAULT NULL, p_condition text DEFAULT NULL,
    p_revisit_by date DEFAULT NULL, p_revisit_on_event text DEFAULT NULL,
    p_followup text DEFAULT NULL, p_assignee text DEFAULT NULL, p_force boolean DEFAULT false)
RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, backlog SET backlog.via_proc = 'on' AS $$
DECLARE
    t          backlog.BACKLOG%ROWTYPE;
    v_from     text;
    v_event    bigint;
    v_delivery boolean := false;
    v_forced   boolean := false;
    v_rel      text;
    v_follow   text;
    v_detail   jsonb;
    c_session  constant text := '^[A-Z0-9]+_[0-9]{8}_(WKSH|DEV|REVB|SYNC|ANL)_[A-Za-z0-9_-]+$';
    c_release  constant text := '^R[0-9]{4}[.][0-9]{2}-[A-Z0-9][A-Z0-9-]*$';
BEGIN
    IF NOT backlog.fn_actor_ok(p_actor) THEN
        RAISE EXCEPTION 'sp_transition: actor % is not canonical (human:<login> | agent:<model> | system:<name>)', p_actor USING ERRCODE = '22023';
    END IF;
    IF NOT backlog.fn_actor_authorized(p_actor) THEN
        RAISE EXCEPTION 'sp_transition: actor % is not authorized from this connection (human identities write only through the owner-only role; an agent names its model, not agent:claude)', p_actor USING ERRCODE = '42501';
    END IF;
    IF p_session_id IS NULL OR p_session_id !~ c_session THEN
        RAISE EXCEPTION 'sp_transition: session id % must look like FND_20261026_DEV_TOPIC', p_session_id USING ERRCODE = '22023';
    END IF;
    IF p_comment IS NULL OR length(btrim(p_comment)) < 10 THEN
        RAISE EXCEPTION 'sp_transition: a comment of 10+ characters is required (it is the progress log of the move)' USING ERRCODE = '22023';
    END IF;
    IF p_assignee IS NOT NULL AND NOT backlog.fn_actor_ok(p_assignee) THEN
        RAISE EXCEPTION 'sp_transition: assignee % is not canonical', p_assignee USING ERRCODE = '22023';
    END IF;
    IF p_to IS NULL OR p_to NOT IN ('OPEN','TODO','IN_PROGRESS','READY_FOR_DEPLOY','BLOCKED','DEFERRED','CLOSED') THEN
        RAISE EXCEPTION 'sp_transition: unknown status %', p_to USING ERRCODE = '22023';
    END IF;
    IF p_release_version IS NOT NULL AND p_release_version !~ c_release THEN
        RAISE EXCEPTION 'sp_transition: release label % must look like R2026.10-TAG', p_release_version USING ERRCODE = '22023';
    END IF;
    IF p_followup IS NOT NULL AND (p_followup = p_ap_id OR NOT EXISTS (SELECT 1 FROM backlog.BACKLOG WHERE action_point_id = p_followup)) THEN
        RAISE EXCEPTION 'sp_transition: follow-up ticket % does not exist or is the ticket itself', p_followup USING ERRCODE = '22023';
    END IF;

    SELECT * INTO t FROM backlog.BACKLOG WHERE action_point_id = p_ap_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'sp_transition: ticket % does not exist', p_ap_id USING ERRCODE = '22023';
    END IF;
    v_from := t.status;
    v_detail := jsonb_build_object('before', jsonb_build_object('closure_ts', t.closure_ts, 'solution_type', t.solution_type,
                                   'deployment_ts', t.deployment_ts, 'release_version', t.release_version, 'assignee', t.assignee));

    -- Same status: only an assignment, or the release label of a READY_FOR_DEPLOY ticket.
    IF p_to = v_from THEN
        IF p_assignee IS NULL AND NOT (p_to = 'READY_FOR_DEPLOY' AND p_release_version IS NOT NULL) THEN
            RAISE EXCEPTION 'sp_transition: % -> % changes nothing (give an assignee, or a release label for READY_FOR_DEPLOY)', v_from, p_to USING ERRCODE = '22023';
        END IF;
        UPDATE backlog.BACKLOG
           SET assignee = COALESCE(p_assignee, assignee),
               release_version = CASE WHEN p_to = 'READY_FOR_DEPLOY' AND p_release_version IS NOT NULL THEN p_release_version ELSE release_version END
         WHERE action_point_id = p_ap_id;
        INSERT INTO backlog.BACKLOG_LOGS (action_point_id, username, comment) VALUES (p_ap_id, p_actor, p_comment);
        INSERT INTO backlog.status_event (action_point_id, from_status, to_status, actor, session_id, reason, detail)
        VALUES (p_ap_id, v_from, p_to, p_actor, p_session_id, left(p_comment, 500),
                v_detail || jsonb_build_object('kind', 'same_status', 'assignee', p_assignee, 'release_version', p_release_version))
        RETURNING event_id INTO v_event;
        RETURN v_event;
    END IF;

    -- Legality.
    IF v_from = 'CLOSED' THEN
        IF NOT p_force THEN
            RAISE EXCEPTION 'sp_transition: % is CLOSED; reopening needs p_force and a reason', p_ap_id USING ERRCODE = '22023';
        END IF;
        IF p_to NOT IN ('OPEN', 'TODO', 'IN_PROGRESS') THEN
            RAISE EXCEPTION 'sp_transition: a CLOSED ticket reopens to OPEN, TODO or IN_PROGRESS, not %', p_to USING ERRCODE = '22023';
        END IF;
        v_forced := true;
    ELSIF NOT backlog.fn_transition_legal(v_from, p_to) THEN
        IF NOT p_force THEN
            RAISE EXCEPTION 'sp_transition: % -> % is not a legal move', v_from, p_to USING ERRCODE = '22023';
        END IF;
        v_forced := true;
    END IF;

    -- Closing.
    IF p_to = 'CLOSED' THEN
        IF p_solution_type IS NULL OR p_solution_type NOT IN ('CODE_FIX','DATA_FIX','WORKAROUND','REJECTED','DUPLICATED') THEN
            RAISE EXCEPTION 'sp_transition: closing needs solution_type CODE_FIX, DATA_FIX, WORKAROUND, REJECTED or DUPLICATED' USING ERRCODE = '22023';
        END IF;
        v_delivery := p_solution_type IN ('CODE_FIX', 'DATA_FIX', 'WORKAROUND');
        IF v_delivery THEN
            IF v_from <> 'READY_FOR_DEPLOY' THEN
                IF NOT p_force THEN
                    RAISE EXCEPTION 'sp_transition: a delivery closure (%) comes from READY_FOR_DEPLOY, not from %', p_solution_type, v_from USING ERRCODE = '22023';
                END IF;
                v_forced := true;
            END IF;
            v_rel := COALESCE(p_release_version, t.release_version);
            IF v_rel IS NULL OR v_rel !~ c_release THEN
                RAISE EXCEPTION 'sp_transition: a delivery closure needs a release label R<yyyy>.<mm>-<TAG> (got %)', v_rel USING ERRCODE = '22023';
            END IF;
            IF p_solution_type = 'WORKAROUND' THEN
                v_follow := p_followup;
                IF v_follow IS NULL THEN
                    v_follow := backlog.sp_open_ticket(left('Replace the workaround of ' || p_ap_id || ': ' || t.title, 300), 'OPT', t.object_type, t.object_name,
                                                       'LOW', p_actor, p_session_id,
                                                       'Follow-up of ' || p_ap_id || ' closed as WORKAROUND: ' || p_comment,
                                                       'Opened automatically by sp_transition when ' || p_ap_id || ' was closed as WORKAROUND.',
                                                       t.schema_name, t.project_code);
                    INSERT INTO backlog.backlog_link_event (from_id, to_id, link_type, status, evidence, username)
                    VALUES (v_follow, p_ap_id, 'SPLIT_FROM', 'CONFIRMED', 'opened by sp_transition when closing ' || p_ap_id || ' as WORKAROUND', p_actor);
                END IF;
            END IF;
        ELSIF p_solution_type = 'DUPLICATED' THEN
            IF p_followup IS NULL THEN
                RAISE EXCEPTION 'sp_transition: DUPLICATED needs the ticket that is kept (p_followup)' USING ERRCODE = '22023';
            END IF;
            v_follow := p_followup;
            IF NOT EXISTS (SELECT 1 FROM backlog.v_backlog_link WHERE from_id = p_ap_id AND to_id = p_followup
                           AND link_type = 'DUPLICATES' AND status = 'CONFIRMED') THEN
                INSERT INTO backlog.backlog_link_event (from_id, to_id, link_type, status, evidence, username)
                VALUES (p_ap_id, p_followup, 'DUPLICATES', 'CONFIRMED', 'closed as DUPLICATED through sp_transition by ' || p_actor, p_actor);
            END IF;
        ELSIF p_solution_type = 'REJECTED' THEN
            v_follow := p_followup;
            IF p_followup IS NULL THEN
                IF NOT p_force THEN
                    RAISE EXCEPTION 'sp_transition: REJECTED cites the ticket that supersedes it (p_followup), or is forced with a reason' USING ERRCODE = '22023';
                END IF;
                v_forced := true;
            END IF;
        END IF;
    ELSIF p_solution_type IS NOT NULL THEN
        RAISE EXCEPTION 'sp_transition: solution_type only applies when closing' USING ERRCODE = '22023';
    END IF;

    -- Waiting states.
    IF p_to = 'BLOCKED' THEN
        IF p_blocker IS NOT NULL THEN
            IF p_blocker = p_ap_id OR NOT EXISTS (SELECT 1 FROM backlog.BACKLOG WHERE action_point_id = p_blocker) THEN
                RAISE EXCEPTION 'sp_transition: blocker % does not exist or is the ticket itself', p_blocker USING ERRCODE = '22023';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM backlog.v_backlog_link WHERE from_id = p_ap_id AND to_id = p_blocker
                           AND link_type = 'DEPENDS_ON' AND status = 'CONFIRMED') THEN
                INSERT INTO backlog.backlog_link_event (from_id, to_id, link_type, status, evidence, username)
                VALUES (p_ap_id, p_blocker, 'DEPENDS_ON', 'CONFIRMED', 'declared as the blocker in sp_transition by ' || p_actor, p_actor);
            END IF;
        ELSIF p_condition IS NULL OR length(btrim(p_condition)) < 10 THEN
            RAISE EXCEPTION 'sp_transition: BLOCKED needs a blocker ticket (p_blocker) or a named condition (p_condition, 10+ characters)' USING ERRCODE = '22023';
        END IF;
        INSERT INTO backlog.wait_event (action_point_id, kind, blocker_ref, condition_text, actor)
        VALUES (p_ap_id, 'BLOCKED', p_blocker, p_condition, p_actor);
    ELSIF p_to = 'DEFERRED' THEN
        IF p_revisit_by IS NULL AND (p_revisit_on_event IS NULL OR length(btrim(p_revisit_on_event)) < 5) THEN
            RAISE EXCEPTION 'sp_transition: DEFERRED needs revisit_by (a date) or revisit_on_event (5+ characters)' USING ERRCODE = '22023';
        END IF;
        IF p_revisit_by IS NOT NULL AND p_revisit_by <= current_date THEN
            RAISE EXCEPTION 'sp_transition: revisit_by % must be in the future', p_revisit_by USING ERRCODE = '22023';
        END IF;
        INSERT INTO backlog.wait_event (action_point_id, kind, condition_text, revisit_by, revisit_on_event, actor)
        VALUES (p_ap_id, 'DEFERRED', p_condition, p_revisit_by, p_revisit_on_event, p_actor);
    END IF;

    IF v_forced AND length(btrim(p_comment)) < 40 THEN
        RAISE EXCEPTION 'sp_transition: a forced move needs a reason of 40+ characters in the comment' USING ERRCODE = '22023';
    END IF;

    UPDATE backlog.BACKLOG
       SET status          = p_to,
           closure_ts      = CASE WHEN p_to = 'CLOSED' THEN now() WHEN v_from = 'CLOSED' THEN NULL ELSE closure_ts END,
           solution_type   = CASE WHEN p_to = 'CLOSED' THEN p_solution_type WHEN v_from = 'CLOSED' THEN NULL ELSE solution_type END,
           deployment_ts   = CASE WHEN p_to = 'CLOSED' AND v_delivery THEN now() WHEN v_from = 'CLOSED' THEN NULL ELSE deployment_ts END,
           release_version = CASE WHEN p_to = 'CLOSED' AND v_delivery THEN v_rel
                                  WHEN p_to = 'READY_FOR_DEPLOY' AND p_release_version IS NOT NULL THEN p_release_version
                                  WHEN v_from = 'CLOSED' THEN NULL ELSE release_version END,
           assignee        = COALESCE(p_assignee, assignee)
     WHERE action_point_id = p_ap_id;
    INSERT INTO backlog.BACKLOG_LOGS (action_point_id, username, comment) VALUES (p_ap_id, p_actor, p_comment);
    INSERT INTO backlog.status_event (action_point_id, from_status, to_status, actor, session_id, reason, forced, detail)
    VALUES (p_ap_id, v_from, p_to, p_actor, p_session_id, left(p_comment, 500), v_forced,
            v_detail || jsonb_build_object('solution_type', p_solution_type, 'release_version', v_rel, 'followup', v_follow,
                                           'blocker', p_blocker, 'assignee', p_assignee))
    RETURNING event_id INTO v_event;
    RETURN v_event;
END;
$$;

-- 6. Release closure rebuilt on sp_transition (same name, signature and dry-run behaviour as db/pg_backlog/10_kpis_and_release.sql). The user name
--    is resolved through backlog.actor_alias, so the default 'SYSTEM_RELEASE' and a raw login both end up canonical. The log text is unchanged.
CREATE OR REPLACE PROCEDURE backlog.sp_close_release(
    p_release_version TEXT,
    p_username TEXT DEFAULT 'SYSTEM_RELEASE',
    p_dry_run BOOLEAN DEFAULT FALSE
)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, backlog
AS $$
DECLARE
    r       RECORD;
    n       INT := 0;
    v_actor TEXT;
BEGIN
    IF p_release_version IS NULL OR p_release_version = '' THEN
        RAISE EXCEPTION 'sp_close_release: p_release_version must not be empty';
    END IF;
    v_actor := COALESCE((SELECT a.canonical FROM backlog.actor_alias a WHERE a.raw_name = p_username), p_username);
    IF NOT backlog.fn_actor_ok(v_actor) THEN
        RAISE EXCEPTION 'sp_close_release: % has no canonical identity (add it to backlog.actor_alias or pass human:/agent:/system:)', p_username;
    END IF;

    IF p_dry_run THEN
        FOR r IN
            SELECT action_point_id, title FROM backlog.BACKLOG
            WHERE release_version = p_release_version AND status = 'READY_FOR_DEPLOY'
            ORDER BY action_point_id
        LOOP
            RAISE NOTICE 'DRY RUN would close: % — %', r.action_point_id, r.title;
            n := n + 1;
        END LOOP;
        RAISE NOTICE 'sp_close_release(%, dry_run=true): % ticket(s) WOULD be closed (nothing changed)', p_release_version, n;
        RETURN;
    END IF;

    FOR r IN
        SELECT action_point_id, session_id, solution_type FROM backlog.BACKLOG
        WHERE release_version = p_release_version AND status = 'READY_FOR_DEPLOY'
        ORDER BY action_point_id
    LOOP
        PERFORM backlog.sp_transition(
            r.action_point_id, 'CLOSED', v_actor,
            COALESCE(r.session_id, 'FND_' || to_char(now(), 'YYYYMMDD') || '_SYNC_RELEASE'),
            format('Closed by sp_close_release(%L) -- release deployed', p_release_version),
            COALESCE(r.solution_type, 'CODE_FIX'), p_release_version);
        n := n + 1;
    END LOOP;
    RAISE NOTICE 'sp_close_release(%): % ticket(s) closed', p_release_version, n;
END;
$$;

-- 7. Progress of the shadow stage toward the revoke of A-2 (the gate of the owner's decision D2): non-system status changes recorded after the last V-00.
CREATE OR REPLACE VIEW backlog.v_shadow_stage AS
SELECT (SELECT max(event_ts) FROM backlog.raw_update_event) AS last_v00_ts,
       (SELECT count(*) FROM backlog.raw_update_event)      AS v00_total,
       (SELECT count(*) FROM backlog.status_event e
         WHERE e.origin = 'LIVE' AND e.from_status IS NOT NULL AND e.from_status <> e.to_status AND e.actor NOT LIKE 'system:%'
           AND e.event_ts > COALESCE((SELECT max(r.event_ts) FROM backlog.raw_update_event r), '-infinity'::timestamptz)) AS live_non_system_since_last_v00;

-- 8. Backfill: one BACKFILL event per ticket that has none (its timestamp is the last known state, NOT a real transition time: the lean metrics
--    of Phase D exclude origin BACKFILL for cycle time).
INSERT INTO backlog.status_event (action_point_id, from_status, to_status, actor, session_id, reason, origin, event_ts)
SELECT b.action_point_id, NULL, b.status, 'system:backfill', NULL, 'BACKFILL: state of the ticket when status_event was introduced', 'BACKFILL',
       COALESCE(b.closure_ts, b.update_ts)
FROM backlog.BACKLOG b
WHERE NOT EXISTS (SELECT 1 FROM backlog.status_event e WHERE e.action_point_id = b.action_point_id);

-- 9. Ownership (D1) and privileges. The routines and the audit tables belong to the non-superuser backlog_owner; backlog_writer may only EXECUTE
--    the routines and read the audit data. Nothing is revoked from backlog_writer here (step A-2).
DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT p.oid::regprocedure AS sig, p.prokind FROM pg_proc p
             WHERE p.pronamespace = 'backlog'::regnamespace
               AND p.proname IN ('sp_open_ticket', 'sp_transition', 'sp_close_release', 'trg_governed_writes', 'fn_actor_ok', 'fn_actor_authorized', 'fn_transition_legal')
    LOOP
        EXECUTE format('ALTER %s %s OWNER TO backlog_owner', CASE r.prokind WHEN 'p' THEN 'PROCEDURE' ELSE 'FUNCTION' END, r.sig);
    END LOOP;
END $$;
ALTER TABLE backlog.status_event     OWNER TO backlog_owner;
ALTER TABLE backlog.wait_event       OWNER TO backlog_owner;
ALTER TABLE backlog.raw_update_event OWNER TO backlog_owner;

REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA backlog FROM PUBLIC;
REVOKE EXECUTE ON ALL PROCEDURES IN SCHEMA backlog FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_writer') THEN
        GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA backlog TO backlog_writer;
        GRANT EXECUTE ON ALL PROCEDURES IN SCHEMA backlog TO backlog_writer;
        GRANT SELECT ON backlog.status_event, backlog.wait_event, backlog.raw_update_event, backlog.v_shadow_stage TO backlog_writer;
    END IF;
END $$;
-- END backlog_transitions
