-- Backlog v3 W2: agent write authority (owner decisions of 2026-10-31, doc/especificacion/ACTION_PLAN_BACKLOG_V3.md; ticket FND-0357). Applied by the OWNER:
-- scripts/ops/migrate_backlog_v2.py --phase autonomy (dry run by default; ONE transaction; idempotent). Staged rollback: db/pg_backlog/down/97_autonomy_down.sql (drops the
-- two wrappers; KEEPS the role, the link events and every ticket and log already written).
--
-- Why: since the live-write order of 2026-10-29 a non-interactive process cannot open or move a ticket, so the owner became the relay for every write. Authority comes from the
-- DATABASE, not from the console: an agent writes through its own login, which can execute a short list of routines and nothing else.
--
-- What it adds:
--   1. The scoped login backlog_agent, created WITHOUT a password and WITHOUT membership of backlog_writer. The owner sets its password in his own session:
--          ALTER ROLE backlog_agent PASSWORD '<chosen by the owner>';
--      and puts the connection string in FONDOS_BACKLOG_PG_DSN_AGENT (the git-ignored .env). Until then agents behave exactly as before (console confirmation).
--   2. backlog.sp_agent_transition: the agent's ONLY way to move a ticket. It is sp_transition with the three things an agent may not do taken out: no p_force (so no illegal move,
--      no shortcut, no gate override), no reopening a CLOSED ticket, and no DELIVERY closure (CODE_FIX / DATA_FIX / WORKAROUND). The delivery closure is held back on purpose: the
--      audit-incident gate of FND-0264 reads another database (fondos), so the database cannot enforce it; delivery closures stay with scripts/ops/close_release.py (a person)
--      until Backlog v3 W3, where the autopilot carries that gate (it moves to shared/backlog_release.py). REJECTED, DUPLICATED and every other move are legal.
--   3. backlog.sp_record_link: the agent's way to record a link event. It writes PROPOSED freely; CONFIRMED needs stated evidence (40+ characters); and a pair whose latest event is
--      a PERSON's decision (CONFIRMED or REJECTED by a human:* identity) cannot be touched by an agent: a link is decided by a person, the owner can always REJECT later.
--   4. backlog.sp_append_log, created ONLY when absent (step iam, parked, defines the same routine and replaces it when applied): an agent's note on an EXISTING ticket.
--   5. The grants of backlog_agent: USAGE on the schema, SELECT on the tables and views (reads inside the same connection), and EXECUTE on exactly these routines: sp_open_ticket,
--      sp_agent_transition, sp_record_link, sp_append_log, sp_annotate_wait, sp_create_group, sp_group_member, sp_add_close_condition, sp_retire_close_condition. No table INSERT or
--      UPDATE, no gate / mode / hold / adjudication routine.
--   6. Backlog v3 W3 (FND-0358), section 5 below: the autopilot. The fact kinds MERGE (a merge on master that delivers a ticket), DEPLOY (its DDL is live) and CYCLE (a cycle ran
--      after the merge); the scoped login backlog_autopilot (no password: the owner sets it; DSN FONDOS_BACKLOG_PG_DSN_AUTOPILOT); and sp_auto_transition, the autopilot's ONLY way to
--      move a ticket: it accepts two edges (IN_PROGRESS -> READY_FOR_DEPLOY and READY_FOR_DEPLOY -> CLOSED as CODE_FIX / DATA_FIX), always as system:autopilot, never forced, and only when
--      the MERGE event it cites is recorded for that ticket (and, for a closure, the DEPLOY / CYCLE / closing-condition facts the delivery needs). The gates of Phase B still apply inside.

-- BEGIN backlog_autonomy
-- 0. The scoped login. No password (the owner sets it), no membership of backlog_writer: it holds ONLY the grants at the end of this block.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_agent') THEN
        CREATE ROLE backlog_agent LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 8;
    END IF;
    ALTER ROLE backlog_agent SET statement_timeout = '8s';
    ALTER ROLE backlog_agent SET idle_in_transaction_session_timeout = '15s';
END $$;

-- 1. A move by an agent: sp_transition without p_force, without reopening, without a delivery closure.
CREATE OR REPLACE FUNCTION backlog.sp_agent_transition(
    p_ap_id text, p_to text, p_actor text, p_session_id text, p_comment text,
    p_solution_type text DEFAULT NULL, p_release_version text DEFAULT NULL,
    p_blocker text DEFAULT NULL, p_condition text DEFAULT NULL,
    p_revisit_by date DEFAULT NULL, p_revisit_on_event text DEFAULT NULL,
    p_followup text DEFAULT NULL, p_assignee text DEFAULT NULL)
RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, backlog AS $$
DECLARE
    v_status text;
BEGIN
    IF NOT backlog.fn_actor_ok(p_actor) OR p_actor NOT LIKE 'agent:%' THEN
        RAISE EXCEPTION 'sp_agent_transition: actor % must be agent:<model> (a person moves tickets with his own role)', p_actor USING ERRCODE = '22023';
    END IF;
    IF NOT backlog.fn_actor_authorized(p_actor) THEN
        RAISE EXCEPTION 'sp_agent_transition: actor % is not authorized from this connection', p_actor USING ERRCODE = '42501';
    END IF;
    SELECT status INTO v_status FROM backlog.BACKLOG WHERE action_point_id = p_ap_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'sp_agent_transition: ticket % does not exist', p_ap_id USING ERRCODE = '22023';
    END IF;
    IF v_status = 'CLOSED' THEN
        RAISE EXCEPTION 'sp_agent_transition: % is CLOSED; reopening is a person''s decision (it needs p_force and a reason)', p_ap_id USING ERRCODE = '42501';
    END IF;
    IF p_to = 'CLOSED' AND p_solution_type IN ('CODE_FIX', 'DATA_FIX', 'WORKAROUND') THEN
        RAISE EXCEPTION 'sp_agent_transition: a delivery closure (%) is made by scripts/ops/close_release.py or, from Backlog v3 W3, by the autopilot, which carries the audit-incident gate of FND-0264', p_solution_type USING ERRCODE = '42501';
    END IF;
    RETURN backlog.sp_transition(p_ap_id => p_ap_id, p_to => p_to, p_actor => p_actor, p_session_id => p_session_id, p_comment => p_comment,
                                 p_solution_type => p_solution_type, p_release_version => p_release_version, p_blocker => p_blocker, p_condition => p_condition,
                                 p_revisit_by => p_revisit_by, p_revisit_on_event => p_revisit_on_event, p_followup => p_followup, p_assignee => p_assignee,
                                 p_force => false);
END;
$$;

-- 2. A link event by an agent. Append-only (the table's own trigger and the DEPENDS_ON cycle check of Phase B still apply).
CREATE OR REPLACE FUNCTION backlog.sp_record_link(
    p_from text, p_to text, p_type text, p_status text, p_evidence text, p_absorbs text, p_actor text, p_origin text DEFAULT NULL)
RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, backlog AS $$
DECLARE
    v_id     bigint;
    v_latest record;
BEGIN
    IF NOT backlog.fn_actor_ok(p_actor) OR p_actor NOT LIKE 'agent:%' THEN
        RAISE EXCEPTION 'sp_record_link: actor % must be agent:<model> (a person records links with his own role)', p_actor USING ERRCODE = '22023';
    END IF;
    IF NOT backlog.fn_actor_authorized(p_actor) THEN
        RAISE EXCEPTION 'sp_record_link: actor % is not authorized from this connection', p_actor USING ERRCODE = '42501';
    END IF;
    IF p_status = 'CONFIRMED' AND length(btrim(coalesce(p_evidence, ''))) < 40 THEN
        RAISE EXCEPTION 'sp_record_link: an agent confirms a link only with stated evidence (40+ characters); otherwise it records PROPOSED' USING ERRCODE = '22023';
    END IF;
    -- a pair whose latest event is a person's decision stays decided
    SELECT e.status, coalesce(a.canonical, e.username) AS who INTO v_latest
      FROM backlog.backlog_link_event e LEFT JOIN backlog.actor_alias a ON a.raw_name = e.username
     WHERE e.from_id = p_from AND e.to_id = p_to AND e.link_type = p_type
     ORDER BY e.event_id DESC LIMIT 1;
    IF FOUND AND v_latest.who LIKE 'human:%' AND v_latest.status IN ('CONFIRMED', 'REJECTED') THEN
        RAISE EXCEPTION 'sp_record_link: the pair % -> % (%) was decided by % (%): an agent does not overwrite a person''s decision', p_from, p_to, p_type, v_latest.who, v_latest.status USING ERRCODE = '42501';
    END IF;
    INSERT INTO backlog.backlog_link_event (from_id, to_id, link_type, status, evidence, absorbs, username, origin)
    VALUES (p_from, p_to, p_type, p_status, p_evidence, p_absorbs, p_actor, coalesce(p_origin, 'REVIEW'))
    RETURNING event_id INTO v_id;
    RETURN v_id;
END;
$$;

-- 3. An agent's note on an existing ticket, only when step iam has not defined it yet (the same routine; applying iam later replaces it).
DO $do$
BEGIN
    IF to_regprocedure('backlog.sp_append_log(text,text,text)') IS NULL THEN
        EXECUTE $f$
        CREATE FUNCTION backlog.sp_append_log(p_ap_id text, p_actor text, p_comment text) RETURNS bigint
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, backlog AS $body$
        DECLARE
            v_id bigint;
        BEGIN
            IF NOT backlog.fn_actor_ok(p_actor) OR p_actor LIKE 'human:%' THEN
                RAISE EXCEPTION 'sp_append_log: actor % must be agent:<model> or system:<name> (a human appends with his own role)', p_actor USING ERRCODE = '22023';
            END IF;
            IF NOT backlog.fn_actor_authorized(p_actor) THEN
                RAISE EXCEPTION 'sp_append_log: actor % is not authorized from this connection', p_actor USING ERRCODE = '42501';
            END IF;
            IF btrim(coalesce(p_comment, '')) = '' OR length(p_comment) > 20000 THEN
                RAISE EXCEPTION 'sp_append_log: the comment is required and at most 20000 characters' USING ERRCODE = '22023';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM backlog.BACKLOG WHERE action_point_id = p_ap_id) THEN
                RAISE EXCEPTION 'sp_append_log: ticket % does not exist (this path never creates one)', p_ap_id USING ERRCODE = '22023';
            END IF;
            INSERT INTO backlog.BACKLOG_LOGS (action_point_id, username, comment) VALUES (p_ap_id, p_actor, p_comment) RETURNING log_id INTO v_id;
            RETURN v_id;
        END $body$;
        $f$;
    END IF;
END $do$;

-- 4. Ownership and privileges. The wrappers belong to backlog_owner like every other routine of the schema; nobody but backlog_agent (and the owner's own roles) executes them.
DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT p.oid::regprocedure AS sig FROM pg_proc p
             WHERE p.pronamespace = 'backlog'::regnamespace AND p.proname IN ('sp_agent_transition', 'sp_record_link', 'sp_append_log')
    LOOP
        EXECUTE format('ALTER FUNCTION %s OWNER TO backlog_owner', r.sig);
        EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC', r.sig);
    END LOOP;
    -- the scoped login: a short list of routines and read access, nothing else
    GRANT USAGE ON SCHEMA backlog TO backlog_agent;
    GRANT SELECT ON ALL TABLES IN SCHEMA backlog TO backlog_agent;
    FOR r IN SELECT p.oid::regprocedure AS sig FROM pg_proc p
             WHERE p.pronamespace = 'backlog'::regnamespace
               AND p.proname IN ('sp_open_ticket', 'sp_agent_transition', 'sp_record_link', 'sp_append_log', 'sp_annotate_wait', 'sp_create_group', 'sp_group_member',
                                 'sp_add_close_condition', 'sp_retire_close_condition')
    LOOP
        EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO backlog_agent', r.sig);
    END LOOP;
END $$;

-- 5. Backlog v3 W3 (FND-0358): the autopilot.
-- 5.0 The kinds of operational fact: a merge that delivers a ticket, a DDL deploy, a cycle that ran. The constraint is widened only when it does not know MERGE yet (idempotent);
--     the rollback leaves it widened (ops_event is append-only and may hold such rows).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'backlog.ops_event'::regclass AND contype = 'c' AND pg_get_constraintdef(oid) LIKE '%''MERGE''%') THEN
        ALTER TABLE backlog.ops_event DROP CONSTRAINT IF EXISTS ops_event_kind_check;
        ALTER TABLE backlog.ops_event ADD CONSTRAINT ops_event_kind_check
            CHECK (kind IN ('COMMIT', 'RELEASE', 'PROMOTION', 'CYCLE_FLAG', 'AUDIT_INCIDENT', 'MERGE', 'DEPLOY', 'CYCLE'));
    END IF;
END $$;

-- 5.1 The scoped login of the autopilot. No password (the owner sets it), no membership of backlog_writer.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_autopilot') THEN
        CREATE ROLE backlog_autopilot LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 4;
    END IF;
    ALTER ROLE backlog_autopilot SET statement_timeout = '10s';
    ALTER ROLE backlog_autopilot SET idle_in_transaction_session_timeout = '15s';
END $$;

-- 5.2 The autopilot's one move. The evidence is a MERGE event recorded for this very ticket; a claim without it is refused. system:autopilot is fixed, p_force is never passed.
CREATE OR REPLACE FUNCTION backlog.sp_auto_transition(
    p_ap_id text, p_to text, p_evidence_kind text, p_evidence_ref text, p_session_id text, p_comment text,
    p_solution_type text DEFAULT NULL, p_release_version text DEFAULT NULL)
RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, backlog AS $$
DECLARE
    c_actor constant text := 'system:autopilot';
    v_status text;
    v_ev     backlog.ops_event%ROWTYPE;
    v_db     boolean;
BEGIN
    IF NOT backlog.fn_actor_authorized(c_actor) THEN
        RAISE EXCEPTION 'sp_auto_transition: % is not authorized from this connection', c_actor USING ERRCODE = '42501';
    END IF;
    SELECT status INTO v_status FROM backlog.BACKLOG WHERE action_point_id = p_ap_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'sp_auto_transition: ticket % does not exist', p_ap_id USING ERRCODE = '22023';
    END IF;
    IF p_evidence_kind IS DISTINCT FROM 'MERGE' THEN
        RAISE EXCEPTION 'sp_auto_transition: the evidence of a move is a MERGE event (got %)', p_evidence_kind USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_ev FROM backlog.ops_event WHERE kind = 'MERGE' AND ref = p_evidence_ref AND action_point_id = p_ap_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'sp_auto_transition: no MERGE event % is recorded for %: the move has no evidence', p_evidence_ref, p_ap_id USING ERRCODE = '42501';
    END IF;
    IF v_status = 'IN_PROGRESS' AND p_to = 'READY_FOR_DEPLOY' THEN
        IF p_solution_type IS NOT NULL OR p_release_version IS NOT NULL THEN
            RAISE EXCEPTION 'sp_auto_transition: the move to READY_FOR_DEPLOY carries neither a solution nor a release label' USING ERRCODE = '22023';
        END IF;
    ELSIF v_status = 'READY_FOR_DEPLOY' AND p_to = 'CLOSED' THEN
        IF p_solution_type IS NULL OR p_solution_type NOT IN ('CODE_FIX', 'DATA_FIX') THEN
            RAISE EXCEPTION 'sp_auto_transition: only CODE_FIX and DATA_FIX close automatically (got %); a person closes the rest', p_solution_type USING ERRCODE = '42501';
        END IF;
        IF p_release_version IS NULL THEN
            RAISE EXCEPTION 'sp_auto_transition: a delivery closure needs its release label' USING ERRCODE = '22023';
        END IF;
        v_db := EXISTS (SELECT 1 FROM jsonb_array_elements_text(COALESCE(v_ev.payload -> 'paths', '[]'::jsonb)) AS p(path) WHERE p.path LIKE 'db/%');
        IF v_db AND NOT EXISTS (SELECT 1 FROM backlog.ops_event WHERE kind = 'DEPLOY' AND action_point_id = p_ap_id) THEN
            RAISE EXCEPTION 'sp_auto_transition: the merge changes db/**; % is not live until its DDL is applied (no DEPLOY event)', p_ap_id USING ERRCODE = '42501';
        END IF;
        IF p_solution_type = 'DATA_FIX' THEN
            IF NOT EXISTS (SELECT 1 FROM backlog.ops_event WHERE kind = 'CYCLE' AND action_point_id = p_ap_id AND fact_ts >= v_ev.fact_ts) THEN
                RAISE EXCEPTION 'sp_auto_transition: DATA_FIX needs a CYCLE event after the merge' USING ERRCODE = '42501';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM backlog.v_close_condition WHERE action_point_id = p_ap_id) THEN
                RAISE EXCEPTION 'sp_auto_transition: DATA_FIX needs at least one active closing condition' USING ERRCODE = '42501';
            END IF;
        END IF;
    ELSE
        RAISE EXCEPTION 'sp_auto_transition: % -> % is not an automatic move (only IN_PROGRESS -> READY_FOR_DEPLOY and READY_FOR_DEPLOY -> CLOSED)', v_status, p_to USING ERRCODE = '42501';
    END IF;
    RETURN backlog.sp_transition(p_ap_id => p_ap_id, p_to => p_to, p_actor => c_actor, p_session_id => p_session_id, p_comment => p_comment,
                                 p_solution_type => p_solution_type, p_release_version => p_release_version, p_force => false);
END;
$$;

-- 5.3 Ownership and privileges. The autopilot may record facts, append notes, read the gate verdicts and make the move above: nothing else, and no table write.
DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT p.oid::regprocedure AS sig FROM pg_proc p WHERE p.pronamespace = 'backlog'::regnamespace AND p.proname = 'sp_auto_transition'
    LOOP
        EXECUTE format('ALTER FUNCTION %s OWNER TO backlog_owner', r.sig);
        EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC', r.sig);
    END LOOP;
    GRANT USAGE ON SCHEMA backlog TO backlog_autopilot;
    GRANT SELECT ON ALL TABLES IN SCHEMA backlog TO backlog_autopilot;
    FOR r IN SELECT p.oid::regprocedure AS sig FROM pg_proc p
             WHERE p.pronamespace = 'backlog'::regnamespace AND p.proname IN ('sp_auto_transition', 'sp_record_ops_event', 'sp_append_log', 'fn_gate_blockers')
    LOOP
        EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO backlog_autopilot', r.sig);
    END LOOP;
END $$;
-- END backlog_autonomy
