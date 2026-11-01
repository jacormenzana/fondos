-- Backlog v3 W4 (FND-0359): the probe status of a ticket is INVALIDATED when something it may depend on changes, and the delivery closure is blocked until its acceptance probes are re-run and pass AFTER
-- that. Phase `probes`: scripts/ops/migrate_backlog_v2.py --phase probes (dry run by default; ONE transaction; idempotent). Rollback: db/pg_backlog/down/99_probes_down.sql (restores the no-blocker stub
-- of fn_probe_blockers, drops the door and the view; KEEPS backlog.probe_event, which is history).
--
-- Why a gate and not a note: an [impact] note informs, and information nobody has to act on is not a control. The invalidation reuses the delivery gate that already exists (fn_gate_blockers, called by
-- sp_transition for READY_FOR_DEPLOY and for CODE_FIX / DATA_FIX / WORKAROUND closures) so it has the same modes (OFF / AUDIT / ENFORCE), the same override (p_force with a reason of 40+ characters, which
-- an agent can never use), the same audit trail (gate_audit_event) and the same reading by the autopilot, which sees the blocker as a reason to WAIT instead of trying and being refused.
--   1. backlog.probe_event (append-only): kind INVALIDATED (the autopilot wrote an [impact] note to a ticket that is IN_PROGRESS or READY_FOR_DEPLOY; ref = the merge key) or RUN (the autopilot re-ran
--      the ticket's closing conditions; passed = every one held, a manual condition is not a pass).
--   2. backlog.sp_record_probe_event: the one door, system:* actors only, idempotent (an INVALIDATED fact per ticket and merge is written once; a RUN that repeats the previous RUN with nothing
--      invalidated in between is not written again). An INVALIDATED fact for a ticket in any other status is not written: it stays an informational note.
--   3. backlog.v_probe_status: per ticket with an invalidation, STALE (it has closing conditions and no passing RUN after the latest INVALIDATED), FRESH (the latest RUN passed and came after it) or
--      UNPROBED (it has no closing condition at all; the gate blocks such a ticket anyway, see 4). "After" is the order of the event ids, which cannot tie, and the times are kept for the reader.
--   4. backlog.fn_probe_blockers replaces the stub of 40_relations_v2.sql. On a delivery closure (p_to = CLOSED, p_delivery) it returns NO_PROBES for a ticket with no closing condition at all (a missing
--      test is a failure: whoever opens or works the ticket must define a probe) and STALE_PROBES for a ticket whose conditions an impact note invalidated; nothing otherwise.
-- Needs the relations phase (the stub and the gate) and the sync phase (close_condition). Nothing here changes a transition by itself.

-- BEGIN backlog_probes
CREATE TABLE IF NOT EXISTS backlog.probe_event (
    event_id         BIGSERIAL PRIMARY KEY,
    kind             TEXT NOT NULL CHECK (kind IN ('INVALIDATED', 'RUN')),
    action_point_id  TEXT NOT NULL REFERENCES backlog.BACKLOG(action_point_id) ON DELETE CASCADE,
    passed           BOOLEAN,
    ref              TEXT NOT NULL CHECK (length(btrim(ref)) > 0 AND length(ref) <= 400),
    actor            TEXT NOT NULL CHECK (actor ~ '^system:[A-Za-z0-9._-]+$'),
    event_ts         TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK ((kind = 'RUN' AND passed IS NOT NULL) OR (kind = 'INVALIDATED' AND passed IS NULL))
);
CREATE INDEX IF NOT EXISTS idx_probe_event_ap ON backlog.probe_event (action_point_id, event_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_probe_event_invalidated ON backlog.probe_event (action_point_id, ref) WHERE kind = 'INVALIDATED';

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = 'backlog.probe_event'::regclass AND tgname = 'append_only' AND NOT tgisinternal) THEN
        CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON backlog.probe_event FOR EACH ROW EXECUTE FUNCTION backlog.trg_append_only();
    END IF;
END $$;

CREATE OR REPLACE VIEW backlog.v_probe_status AS
WITH inv AS (SELECT action_point_id, max(event_id) AS last_inv_id, max(event_ts) AS last_invalidated FROM backlog.probe_event WHERE kind = 'INVALIDATED' GROUP BY action_point_id),
     run AS (SELECT DISTINCT ON (action_point_id) action_point_id, event_id AS run_id, passed, event_ts AS run_ts FROM backlog.probe_event WHERE kind = 'RUN' ORDER BY action_point_id, event_id DESC),
     cond AS (SELECT action_point_id, count(*) AS conditions FROM backlog.v_close_condition GROUP BY action_point_id)
SELECT i.action_point_id, i.last_invalidated, r.passed AS last_run_passed, r.run_ts AS last_run_ts, COALESCE(c.conditions, 0) AS conditions,
       CASE WHEN COALESCE(c.conditions, 0) = 0 THEN 'UNPROBED'
            WHEN r.run_id IS NOT NULL AND r.passed AND r.run_id > i.last_inv_id THEN 'FRESH'
            ELSE 'STALE' END AS state
  FROM inv i
  LEFT JOIN run r ON r.action_point_id = i.action_point_id
  LEFT JOIN cond c ON c.action_point_id = i.action_point_id;

-- The one door. Returns the event id, or NULL when nothing is written (an invalidation of a ticket that is not IN_PROGRESS or READY_FOR_DEPLOY, one already recorded for the merge, a RUN that repeats the last one).
CREATE OR REPLACE FUNCTION backlog.sp_record_probe_event(p_ap_id text, p_kind text, p_passed boolean, p_ref text, p_actor text) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, backlog AS $$
DECLARE
    v_status text;
    v_last   backlog.probe_event%ROWTYPE;
    v_id     bigint;
BEGIN
    IF p_actor IS NULL OR NOT backlog.fn_actor_ok(p_actor) OR NOT backlog.fn_actor_authorized(p_actor) THEN
        RAISE EXCEPTION 'sp_record_probe_event: actor % is not canonical or not authorized from this connection', p_actor USING ERRCODE = '42501';
    END IF;
    IF p_actor NOT LIKE 'system:%' THEN
        RAISE EXCEPTION 'sp_record_probe_event: only a system actor records probe facts (got %): a person or an agent does not decide that a probe passed', p_actor USING ERRCODE = '42501';
    END IF;
    IF p_kind NOT IN ('INVALIDATED', 'RUN') THEN
        RAISE EXCEPTION 'sp_record_probe_event: kind must be INVALIDATED or RUN (got %)', p_kind USING ERRCODE = '22023';
    END IF;
    IF (p_kind = 'RUN') <> (p_passed IS NOT NULL) THEN
        RAISE EXCEPTION 'sp_record_probe_event: a RUN says whether it passed, an INVALIDATED does not' USING ERRCODE = '22023';
    END IF;
    SELECT status INTO v_status FROM backlog.BACKLOG WHERE action_point_id = p_ap_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'sp_record_probe_event: ticket % does not exist', p_ap_id USING ERRCODE = '22023';
    END IF;
    IF p_kind = 'INVALIDATED' THEN
        IF v_status NOT IN ('IN_PROGRESS', 'READY_FOR_DEPLOY') THEN
            RETURN NULL;
        END IF;
        INSERT INTO backlog.probe_event (kind, action_point_id, passed, ref, actor) VALUES ('INVALIDATED', p_ap_id, NULL, btrim(p_ref), p_actor)
        ON CONFLICT (action_point_id, ref) WHERE kind = 'INVALIDATED' DO NOTHING
        RETURNING event_id INTO v_id;
        RETURN v_id;
    END IF;
    SELECT * INTO v_last FROM backlog.probe_event WHERE action_point_id = p_ap_id ORDER BY event_id DESC LIMIT 1;
    IF FOUND AND v_last.kind = 'RUN' AND v_last.passed = p_passed THEN
        RETURN NULL;
    END IF;
    INSERT INTO backlog.probe_event (kind, action_point_id, passed, ref, actor) VALUES ('RUN', p_ap_id, p_passed, btrim(p_ref), p_actor) RETURNING event_id INTO v_id;
    RETURN v_id;
END;
$$;

-- The real hook of the delivery gate (the stub lives in 40_relations_v2.sql and is replaced here). Two blockers, both only on a delivery closure (p_to = CLOSED with p_delivery, i.e. CODE_FIX / DATA_FIX /
-- WORKAROUND; a REJECTED or DUPLICATED ticket was not delivered):
--   NO_PROBES    the ticket has no closing condition at all. A missing test is a failure, not a pass: a delivery closes only against an acceptance probe, so whoever opens or works the ticket (a person or
--                an agent) must define one (backlog_client.add_close_condition) before the ticket can reach CLOSED. Owner decision 2026-11-01.
--   STALE_PROBES it has conditions, an [impact] note invalidated them, and no passing run came after.
CREATE OR REPLACE FUNCTION backlog.fn_probe_blockers(p_ap_id text, p_to text, p_delivery boolean) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, backlog AS $$
    SELECT COALESCE((SELECT jsonb_agg(b ORDER BY ord) FROM (
        SELECT 1 AS ord, jsonb_build_object('kind', 'NO_PROBES', 'hint', 'a delivery closes only against an acceptance probe: define one with backlog_client.add_close_condition(ticket, kind, args, reason)') AS b
         WHERE p_delivery AND p_to = 'CLOSED' AND NOT EXISTS (SELECT 1 FROM backlog.v_close_condition c WHERE c.action_point_id = p_ap_id)
        UNION ALL
        SELECT 2, jsonb_build_object('kind', 'STALE_PROBES', 'since', s.last_invalidated, 'conditions', s.conditions)
          FROM backlog.v_probe_status s
         WHERE s.action_point_id = p_ap_id AND s.state = 'STALE' AND p_delivery AND p_to = 'CLOSED') q), '[]'::jsonb) $$;

-- A new kind of closing condition: tests_pass (CI green: every check run of the change that delivers the ticket passed, and every check the branch requires has run; scripts/audit/rfd_matrix_probe.py,
-- shared/backlog_ci.py). It takes no argument (the delivering merge is the autopilot's fact) or the sha of a commit. Widen the check of close_condition.probe_kind (the list of the sync phase).
DO $$
DECLARE c record;
BEGIN
    FOR c IN SELECT conname FROM pg_constraint
              WHERE conrelid = 'backlog.close_condition'::regclass AND contype = 'c' AND pg_get_constraintdef(oid) LIKE '%probe_kind%' AND pg_get_constraintdef(oid) NOT LIKE '%tests_pass%'
    LOOP
        EXECUTE format('ALTER TABLE backlog.close_condition DROP CONSTRAINT %I', c.conname);
        ALTER TABLE backlog.close_condition ADD CONSTRAINT close_condition_probe_kind_check
            CHECK (probe_kind IN ('pushed', 'flag', 'calc_share', 'metric_absent', 'absent_in', 'absent_re_in', 'ols_ran', 'ticket_closed', 'task_ok', 'manual', 'tests_pass'));
    END LOOP;
END $$;

-- The migration of the work in flight (owner directive 2026-11-01): the gate above blocks a delivery with no closing condition, and no ticket had one, so the same transaction that deploys the gate gives
-- every ticket that is IN_PROGRESS or READY_FOR_DEPLOY the default condition tests_pass. A ticket that has ever had a condition of its own, active or retired, is left alone; and it runs ONCE: after the first
-- seeding (the rows of actor system:migration are the mark) a re-apply seeds nothing, so a ticket that starts later defines its own probe. The default is replaced by a more specific probe by retiring it
-- (backlog_client.retire_close_condition) and adding the better one.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM backlog.close_condition WHERE actor = 'system:migration' AND reason LIKE 'default closing condition (owner directive 2026-11-01)%') THEN
        INSERT INTO backlog.close_condition (op, action_point_id, probe_kind, args, reason, actor)
        SELECT 'ADD', b.action_point_id, 'tests_pass', '[]'::jsonb,
               'default closing condition (owner directive 2026-11-01): the check runs of the change that delivers this ticket are all green; replace it with a more specific probe when there is one', 'system:migration'
          FROM backlog.BACKLOG b
         WHERE b.status IN ('IN_PROGRESS', 'READY_FOR_DEPLOY')
           AND NOT EXISTS (SELECT 1 FROM backlog.close_condition c WHERE c.action_point_id = b.action_point_id);
    END IF;
END $$;

-- Ownership and privileges: the objects belong to backlog_owner; the autopilot records, everyone who reads the backlog may read.
DO $$
DECLARE r record;
BEGIN
    ALTER TABLE backlog.probe_event OWNER TO backlog_owner;
    ALTER VIEW backlog.v_probe_status OWNER TO backlog_owner;
    FOR r IN SELECT p.oid::regprocedure AS sig, p.proname FROM pg_proc p WHERE p.pronamespace = 'backlog'::regnamespace AND p.proname IN ('sp_record_probe_event', 'fn_probe_blockers')
    LOOP
        EXECUTE format('ALTER FUNCTION %s OWNER TO backlog_owner', r.sig);
        EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC', r.sig);
        IF r.proname = 'sp_record_probe_event' THEN
            EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO backlog_autopilot', r.sig);
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_writer') THEN
                EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO backlog_writer', r.sig);
            END IF;
        END IF;
    END LOOP;
    GRANT SELECT ON backlog.probe_event, backlog.v_probe_status TO backlog_autopilot, backlog_agent;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_writer') THEN
        GRANT SELECT ON backlog.probe_event, backlog.v_probe_status TO backlog_writer;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_reader') THEN
        GRANT SELECT ON backlog.probe_event, backlog.v_probe_status TO backlog_reader;
    END IF;
END $$;
-- END backlog_probes
