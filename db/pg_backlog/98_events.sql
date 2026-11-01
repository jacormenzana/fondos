-- Backlog v3.1, phase `events` (owner directive of 2026-11-01: remove the administrative tasks; doc/especificacion/ACTION_PLAN_BACKLOG_V3.md and PROPUESTA_BACKLOG_V2.md round 13). Applied by the OWNER the first
-- time (the deploy watcher: python scripts/ops/owner_deploy.py), by the watcher itself afterwards: scripts/ops/migrate_backlog_v2.py --phase events (ONE transaction; idempotent). Staged rollback:
-- db/pg_backlog/down/98_events_down.sql (drops the routines and the guard; KEEPS every table, the role and the history).
--
-- Principle: each transition of the state machine has an automatic trigger, and a person appears only as an EVENT (a new ticket, a priority, a reopening), never as an approver.
--   1. Reopening is a normal event. backlog.reopen_event (append-only) + backlog.sp_reopen (any actor, a cause, no p_force) + backlog.sp_rescind_reopen (the same actor, within one hour). sp_transition
--      (30_transitions.sql) no longer needs p_force to reopen and records the cause here; V-FORCED no longer counts a reopening.
--   2. Holds release by themselves: backlog.fn_auto_release_holds frees the system holds (V-COLLISION, V-HOOK-BYPASS) of a ticket once no collision pair of it is undecided. A hold raised by a person stays
--      a person's.
--   3. Agents decide collision pairs with stated evidence: backlog.sp_agent_adjudicate_overlap (the same effect as the person's routine, through backlog.fn_adjudicate_core of 90_collision.sql).
--   4. The autopilot mode is an event, not a file edit: backlog.autopilot_mode_event, backlog.fn_autopilot_mode, backlog.sp_set_autopilot_mode. A person sets any mode; the autopilot itself only moves
--      between SHADOW and ON (its promotion and demotion rules live in scripts/audit/backlog_autopilot.py).
--   5. The DEPLOY fact the autopilot waits for cannot be forged by an agent: the scoped login backlog_facts (created WITHOUT a password: the owner vault holds its string) records it through
--      backlog.sp_record_fact, and a trigger on ops_event refuses a DEPLOY row from any other non-superuser login.

-- BEGIN backlog_events
-- 1. Reopening.
CREATE TABLE IF NOT EXISTS backlog.reopen_event (
    reopen_id        BIGSERIAL PRIMARY KEY,
    action_point_id  TEXT NOT NULL REFERENCES backlog.BACKLOG(action_point_id) ON DELETE CASCADE,
    kind             TEXT NOT NULL CHECK (kind IN ('REOPEN', 'RESCIND')),
    actor            TEXT NOT NULL CHECK (actor ~ '^(human|agent|system):[A-Za-z0-9._-]+$'),
    cause            TEXT NOT NULL CHECK (cause IN ('REGRESSION', 'INCOMPLETE_FIX', 'COVERAGE_GAP', 'WRONG_ROOT_CAUSE', 'DOWNSTREAM_EFFECT', 'ENVIRONMENT', 'SCOPE_CHANGE', 'OTHER')),
    reason           TEXT NOT NULL CHECK (length(btrim(reason)) >= 10),
    prior            JSONB NOT NULL DEFAULT '{}'::jsonb,              -- the closure fields the reopening cleared (closure_ts, solution_type, deployment_ts, release_version)
    status_event_id  BIGINT,
    event_ts         TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS idx_reopen_event_ap ON backlog.reopen_event (action_point_id, reopen_id);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = 'backlog.reopen_event'::regclass AND tgname = 'append_only' AND NOT tgisinternal) THEN
        CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON backlog.reopen_event FOR EACH ROW EXECUTE FUNCTION backlog.trg_append_only();
    END IF;
END $$;

-- Backfill, once: the reopenings the history shows (the structured one of FND-0193 and the three that only the logs show). Idempotent per ticket and kind.
DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT * FROM (VALUES
        ('FND-0193', 'OTHER',             'a correction of a known limitation of the deployment timestamp (status event 351/352, owner directive 2026-10-28)'),
        ('FND-0177', 'COVERAGE_GAP',      'reopened 2026-10-03 after the owner''s --force load test: the quality gate guarded the daily write path and not the monthly one'),
        ('FND-0157', 'INCOMPLETE_FIX',    'reopened 2026-10-01: the portfolio still contained two ISINs through 2026-03-21 score rows after the closure'),
        ('FND-0034', 'DOWNSTREAM_EFFECT', 'reopened 2026-10-25 on the owner''s instruction: the P2P3 cycle report still showed 88 ISINs of cost_attributes accepted under the closed ticket')
    ) AS v(ap, cause, reason)
    LOOP
        IF EXISTS (SELECT 1 FROM backlog.BACKLOG WHERE action_point_id = r.ap)
           AND NOT EXISTS (SELECT 1 FROM backlog.reopen_event WHERE action_point_id = r.ap AND kind = 'REOPEN') THEN
            INSERT INTO backlog.reopen_event (action_point_id, kind, actor, cause, reason, event_ts)
            VALUES (r.ap, 'REOPEN', 'system:backfill', r.cause, r.reason, '2026-11-01T00:00:00+00');
        END IF;
    END LOOP;
END $$;

-- Reopens a CLOSED ticket with its cause. Any actor the connection may speak for: reopening is the safe direction (it adds work and hides nothing); only closing needs evidence.
CREATE OR REPLACE FUNCTION backlog.sp_reopen(p_ap_id text, p_cause text, p_reason text, p_actor text, p_session_id text, p_to text DEFAULT 'IN_PROGRESS') RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, backlog AS $$
DECLARE
    v_status text;
    v_event  bigint;
BEGIN
    IF p_cause IS NULL OR p_cause NOT IN ('REGRESSION', 'INCOMPLETE_FIX', 'COVERAGE_GAP', 'WRONG_ROOT_CAUSE', 'DOWNSTREAM_EFFECT', 'ENVIRONMENT', 'SCOPE_CHANGE', 'OTHER') THEN
        RAISE EXCEPTION 'sp_reopen: cause % is not one of the known causes', p_cause USING ERRCODE = '22023';
    END IF;
    SELECT status INTO v_status FROM backlog.BACKLOG WHERE action_point_id = p_ap_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'sp_reopen: ticket % does not exist', p_ap_id USING ERRCODE = '22023';
    END IF;
    IF v_status <> 'CLOSED' THEN
        RAISE EXCEPTION 'sp_reopen: % is %, not CLOSED', p_ap_id, v_status USING ERRCODE = '22023';
    END IF;
    PERFORM set_config('backlog.reopen_cause', p_cause, true);           -- read by sp_transition, which records the reopening
    v_event := backlog.sp_transition(p_ap_id => p_ap_id, p_to => p_to, p_actor => p_actor, p_session_id => p_session_id, p_comment => p_reason);
    PERFORM set_config('backlog.reopen_cause', '', true);
    RETURN v_event;
END;
$$;

-- The actor who reopened a ticket may take it back within one hour, while nothing else has happened to it: the closure fields come back from the event, the history keeps both moves.
CREATE OR REPLACE FUNCTION backlog.sp_rescind_reopen(p_ap_id text, p_actor text, p_session_id text) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, backlog SET backlog.via_proc = 'on' AS $$
DECLARE
    r        backlog.reopen_event%ROWTYPE;
    se       backlog.status_event%ROWTYPE;
    v_event  bigint;
BEGIN
    IF NOT backlog.fn_actor_ok(p_actor) OR NOT backlog.fn_actor_authorized(p_actor) THEN
        RAISE EXCEPTION 'sp_rescind_reopen: actor % is not canonical or not authorized from this connection', p_actor USING ERRCODE = '42501';
    END IF;
    IF p_session_id IS NULL OR p_session_id !~ '^[A-Z0-9]+_[0-9]{8}_(WKSH|DEV|REVB|SYNC|ANL)_[A-Za-z0-9_-]+$' THEN
        RAISE EXCEPTION 'sp_rescind_reopen: session id % must look like FND_20261101_DEV_TOPIC', p_session_id USING ERRCODE = '22023';
    END IF;
    PERFORM 1 FROM backlog.BACKLOG WHERE action_point_id = p_ap_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'sp_rescind_reopen: ticket % does not exist', p_ap_id USING ERRCODE = '22023';
    END IF;
    SELECT * INTO r FROM backlog.reopen_event WHERE action_point_id = p_ap_id ORDER BY reopen_id DESC LIMIT 1;
    IF NOT FOUND OR r.kind <> 'REOPEN' THEN
        RAISE EXCEPTION 'sp_rescind_reopen: % has no reopening to take back', p_ap_id USING ERRCODE = '22023';
    END IF;
    IF r.actor <> p_actor THEN
        RAISE EXCEPTION 'sp_rescind_reopen: only the actor who reopened (%) takes a reopening back', r.actor USING ERRCODE = '42501';
    END IF;
    IF clock_timestamp() - r.event_ts > interval '1 hour' THEN
        RAISE EXCEPTION 'sp_rescind_reopen: the one-hour window has passed' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO se FROM backlog.status_event WHERE action_point_id = p_ap_id ORDER BY event_id DESC LIMIT 1;
    IF se.event_id IS DISTINCT FROM r.status_event_id THEN
        RAISE EXCEPTION 'sp_rescind_reopen: % has moved since it was reopened', p_ap_id USING ERRCODE = '22023';
    END IF;
    UPDATE backlog.BACKLOG
       SET status = 'CLOSED',
           closure_ts      = (r.prior ->> 'closure_ts')::timestamptz,
           solution_type   = r.prior ->> 'solution_type',
           deployment_ts   = (r.prior ->> 'deployment_ts')::timestamptz,
           release_version = r.prior ->> 'release_version'
     WHERE action_point_id = p_ap_id;
    INSERT INTO backlog.BACKLOG_LOGS (action_point_id, username, comment) VALUES (p_ap_id, p_actor, 'Reopening taken back by the same actor within one hour: the ticket is CLOSED again as before.');
    INSERT INTO backlog.status_event (action_point_id, from_status, to_status, actor, session_id, reason, detail)
    VALUES (p_ap_id, se.to_status, 'CLOSED', p_actor, p_session_id, 'reopening taken back by the same actor within one hour', jsonb_build_object('kind', 'rescind', 'restored', r.prior))
    RETURNING event_id INTO v_event;
    INSERT INTO backlog.reopen_event (action_point_id, kind, actor, cause, reason, prior, status_event_id)
    VALUES (p_ap_id, 'RESCIND', p_actor, r.cause, 'the reopening of event ' || r.reopen_id || ' was taken back', r.prior, v_event);
    RETURN v_event;
END;
$$;

-- 2. Holds that release by themselves. A hold of a SYSTEM code is not an opinion of a person: it exists while a collision pair of the ticket is undecided, and goes when none is.
CREATE OR REPLACE FUNCTION backlog.fn_auto_release_holds(p_actor text) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, backlog AS $$
DECLARE
    h record;
    n integer := 0;
BEGIN
    IF p_actor IS NULL OR p_actor LIKE 'human:%' OR NOT backlog.fn_actor_ok(p_actor) OR NOT backlog.fn_actor_authorized(p_actor) THEN
        RAISE EXCEPTION 'fn_auto_release_holds: actor % must be an authorized agent:<model> or system:<name>', p_actor USING ERRCODE = '42501';
    END IF;
    FOR h IN SELECT v.hold_id, v.action_point_id, v.violation_code FROM backlog.v_open_hold v WHERE v.violation_code IN ('V-COLLISION', 'V-HOOK-BYPASS') LOOP
        IF NOT EXISTS (SELECT 1 FROM backlog.v_gate_queue q WHERE h.action_point_id IN (q.a, q.b)) THEN
            INSERT INTO backlog.hold_event (kind, action_point_id, raise_id, reason, actor)
            VALUES ('RELEASE', h.action_point_id, h.hold_id, 'released automatically: no collision pair of this ticket is undecided any more', p_actor);
            INSERT INTO backlog.BACKLOG_LOGS (action_point_id, username, comment)
            VALUES (h.action_point_id, p_actor, 'Hold ' || h.hold_id || ' (' || h.violation_code || ') released automatically: no collision pair of this ticket is undecided any more.');
            n := n + 1;
        END IF;
    END LOOP;
    RETURN n;
END;
$$;

-- 3. An agent decides a collision pair with stated evidence. Same effect as the person's routine (fn_adjudicate_core), but never a REVERT (that needs the verified sha of a revert commit) and never a
-- pair that is already decided (the core refuses it), so a person's earlier decision is never overwritten.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'backlog.gate_adjudication'::regclass AND contype = 'c' AND conname = 'gate_adjudication_actor_check'
               AND pg_get_constraintdef(oid) NOT LIKE '%agent%') THEN
        ALTER TABLE backlog.gate_adjudication DROP CONSTRAINT gate_adjudication_actor_check;
        ALTER TABLE backlog.gate_adjudication ADD CONSTRAINT gate_adjudication_actor_check CHECK (actor ~ '^(human|agent):[A-Za-z0-9._-]+$');
    END IF;
END $$;

CREATE OR REPLACE FUNCTION backlog.sp_agent_adjudicate_overlap(p_a text, p_b text, p_decision text, p_absorbs text, p_reason text, p_actor text) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, backlog AS $$
BEGIN
    IF p_actor IS NULL OR p_actor NOT LIKE 'agent:%' OR NOT backlog.fn_actor_ok(p_actor) OR NOT backlog.fn_actor_authorized(p_actor) THEN
        RAISE EXCEPTION 'sp_agent_adjudicate_overlap: the deciding identity must be an authorized agent:<model> (got %)', p_actor USING ERRCODE = '42501';
    END IF;
    IF p_decision NOT IN ('OVERLAPS', 'RELATED', 'DEPENDS_ON', 'NONE') THEN
        RAISE EXCEPTION 'sp_agent_adjudicate_overlap: an agent decides OVERLAPS, RELATED, DEPENDS_ON or NONE (got %); REVERT needs a person', p_decision USING ERRCODE = '22023';
    END IF;
    IF p_reason IS NULL OR length(btrim(p_reason)) < 40 THEN
        RAISE EXCEPTION 'sp_agent_adjudicate_overlap: an agent states its evidence in 40 or more characters' USING ERRCODE = '22023';
    END IF;
    RETURN backlog.fn_adjudicate_core(p_a, p_b, p_decision, p_absorbs, p_reason, p_actor);
END;
$$;

-- 4. The autopilot mode is an event.
CREATE TABLE IF NOT EXISTS backlog.autopilot_mode_event (
    event_id  BIGSERIAL PRIMARY KEY,
    mode      TEXT NOT NULL CHECK (mode IN ('OFF', 'SHADOW', 'ON')),
    actor     TEXT NOT NULL CHECK (actor ~ '^(human|agent|system):[A-Za-z0-9._-]+$'),
    reason    TEXT NOT NULL CHECK (length(btrim(reason)) >= 10),
    event_ts  TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = 'backlog.autopilot_mode_event'::regclass AND tgname = 'append_only' AND NOT tgisinternal) THEN
        CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON backlog.autopilot_mode_event FOR EACH ROW EXECUTE FUNCTION backlog.trg_append_only();
    END IF;
END $$;

CREATE OR REPLACE FUNCTION backlog.fn_autopilot_mode() RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, backlog AS
$$ SELECT COALESCE((SELECT mode FROM backlog.autopilot_mode_event ORDER BY event_id DESC LIMIT 1), 'SHADOW') $$;

-- A person sets any mode. The autopilot only moves between SHADOW and ON (it can never switch itself off or on from OFF): its rule is promotion after 20 uncontradicted decisions, demotion when its
-- own closures are reopened.
CREATE OR REPLACE FUNCTION backlog.sp_set_autopilot_mode(p_mode text, p_reason text, p_actor text) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, backlog AS $$
DECLARE
    v_now text := backlog.fn_autopilot_mode();
    v_id  bigint;
BEGIN
    IF NOT backlog.fn_actor_ok(p_actor) OR NOT backlog.fn_actor_authorized(p_actor) THEN
        RAISE EXCEPTION 'sp_set_autopilot_mode: actor % is not canonical or not authorized from this connection', p_actor USING ERRCODE = '42501';
    END IF;
    IF p_actor = 'system:autopilot' THEN
        IF NOT ((v_now = 'SHADOW' AND p_mode = 'ON') OR (v_now = 'ON' AND p_mode = 'SHADOW')) THEN
            RAISE EXCEPTION 'sp_set_autopilot_mode: the autopilot moves only SHADOW <-> ON (now %, asked %)', v_now, p_mode USING ERRCODE = '42501';
        END IF;
    ELSIF p_actor NOT LIKE 'human:%' THEN
        RAISE EXCEPTION 'sp_set_autopilot_mode: only a person or the autopilot sets the mode (got %)', p_actor USING ERRCODE = '42501';
    END IF;
    IF p_mode NOT IN ('OFF', 'SHADOW', 'ON') THEN
        RAISE EXCEPTION 'sp_set_autopilot_mode: mode % is not OFF, SHADOW or ON', p_mode USING ERRCODE = '22023';
    END IF;
    INSERT INTO backlog.autopilot_mode_event (mode, actor, reason) VALUES (p_mode, p_actor, p_reason) RETURNING event_id INTO v_id;
    RETURN v_id;
END;
$$;

-- 5. The DEPLOY fact. Created without a password: the owner vault holds the string of this login (FONDOS_BACKLOG_PG_DSN_FACTS).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_facts') THEN
        CREATE ROLE backlog_facts LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 4;
    END IF;
    ALTER ROLE backlog_facts CONNECTION LIMIT 4;
    ALTER ROLE backlog_facts SET statement_timeout = '10s';
    ALTER ROLE backlog_facts SET idle_in_transaction_session_timeout = '15s';
END $$;

CREATE OR REPLACE FUNCTION backlog.sp_record_fact(p_kind text, p_ref text, p_ap_id text, p_payload jsonb, p_fact_ts timestamptz) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, backlog AS $$
DECLARE v_id bigint;
BEGIN
    IF p_kind IS DISTINCT FROM 'DEPLOY' THEN
        RAISE EXCEPTION 'sp_record_fact: only a DEPLOY fact is recorded here (got %)', p_kind USING ERRCODE = '22023';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM backlog.BACKLOG WHERE action_point_id = p_ap_id) THEN
        RAISE EXCEPTION 'sp_record_fact: ticket % does not exist', p_ap_id USING ERRCODE = '22023';
    END IF;
    INSERT INTO backlog.ops_event (kind, ref, action_point_id, source, payload, actor, fact_ts)
    VALUES ('DEPLOY', btrim(p_ref), p_ap_id, 'OTHER', COALESCE(p_payload, '{}'::jsonb), 'system:deploy', p_fact_ts)
    ON CONFLICT ON CONSTRAINT uq_ops_event_fact DO NOTHING
    RETURNING event_id INTO v_id;
    RETURN v_id IS NOT NULL;
END;
$$;

-- A DEPLOY row is accepted only from the login backlog_facts (or a superuser: migrations and backfills). Not SECURITY DEFINER on purpose: session_user is the real login. While the role does not exist
-- (an older state of the database) the guard lets everything through, so applying the phases in any order never breaks the sync.
CREATE OR REPLACE FUNCTION backlog.trg_deploy_fact_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.kind = 'DEPLOY' AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_facts')
       AND NOT pg_has_role(session_user, 'backlog_facts', 'MEMBER')
       AND NOT COALESCE((SELECT rolsuper FROM pg_roles WHERE rolname = session_user), false) THEN
        RAISE EXCEPTION 'ops_event: a DEPLOY fact is recorded only through backlog.sp_record_fact by the login backlog_facts (it says the DDL is live: nobody else may claim it)' USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END;
$$;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = 'backlog.ops_event'::regclass AND tgname = 'deploy_fact_guard' AND NOT tgisinternal) THEN
        CREATE TRIGGER deploy_fact_guard BEFORE INSERT ON backlog.ops_event FOR EACH ROW EXECUTE FUNCTION backlog.trg_deploy_fact_guard();
    END IF;
END $$;

-- 6. Ownership and privileges.
DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT p.oid::regprocedure AS sig FROM pg_proc p
             WHERE p.pronamespace = 'backlog'::regnamespace
               AND p.proname IN ('sp_reopen', 'sp_rescind_reopen', 'fn_auto_release_holds', 'sp_agent_adjudicate_overlap', 'fn_autopilot_mode', 'sp_set_autopilot_mode', 'sp_record_fact', 'trg_deploy_fact_guard')
    LOOP
        EXECUTE format('ALTER FUNCTION %s OWNER TO backlog_owner', r.sig);
        EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC', r.sig);
    END LOOP;
    ALTER TABLE backlog.reopen_event OWNER TO backlog_owner;
    ALTER TABLE backlog.autopilot_mode_event OWNER TO backlog_owner;
    GRANT USAGE ON SCHEMA backlog TO backlog_facts;
    GRANT SELECT ON ALL TABLES IN SCHEMA backlog TO backlog_facts, backlog_agent, backlog_autopilot;
    GRANT EXECUTE ON FUNCTION backlog.sp_record_fact(text, text, text, jsonb, timestamptz) TO backlog_facts;
    GRANT EXECUTE ON FUNCTION backlog.sp_reopen(text, text, text, text, text, text), backlog.sp_rescind_reopen(text, text, text),
                              backlog.fn_auto_release_holds(text), backlog.fn_autopilot_mode() TO backlog_agent, backlog_autopilot;
    GRANT EXECUTE ON FUNCTION backlog.sp_agent_adjudicate_overlap(text, text, text, text, text, text) TO backlog_agent;
    GRANT EXECUTE ON FUNCTION backlog.sp_set_autopilot_mode(text, text, text) TO backlog_autopilot;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_writer') THEN
        GRANT SELECT ON backlog.reopen_event, backlog.autopilot_mode_event TO backlog_writer;
        GRANT EXECUTE ON FUNCTION backlog.sp_reopen(text, text, text, text, text, text), backlog.sp_rescind_reopen(text, text, text), backlog.fn_autopilot_mode(),
                                  backlog.sp_set_autopilot_mode(text, text, text) TO backlog_writer;
    END IF;
END $$;
-- END backlog_events
