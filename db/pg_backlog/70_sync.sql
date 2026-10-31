-- Backlog v2, Phase C slice 1: system sync (FND-0334). Spec: doc/especificacion/PROPUESTA_BACKLOG_V2.md 6.4.8 and the codes V-RETRO / V-WORK-ON-CLOSED of
-- section 7. Applied by the OWNER: scripts/ops/migrate_backlog_v2.py --phase sync (dry run by default; ONE transaction; idempotent). Needs Phase D
-- (fn_rebuild_violations). Staged rollback: db/pg_backlog/down/70_sync_down.sql (drops the views and routines; KEEPS ops_event and close_condition).
--
-- What it adds:
--   1. backlog.ops_event: append-only facts of the system that cite a ticket (kinds COMMIT, RELEASE, PROMOTION, CYCLE_FLAG, AUDIT_INCIDENT), one row per
--      (kind, ref, ticket), written only through sp_record_ops_event (idempotent: a fact already recorded is not recorded twice). The commit events are
--      written by scripts/audit/backlog_sync.py from `git log` (source REFS = a `Refs: FND-nnnn` trailer, SUBJECT = an id in the subject line).
--   2. backlog.close_condition: the executable closing conditions of READY_FOR_DEPLOY tickets (append-only ADD / RETIRE), read by
--      scripts/audit/rfd_matrix_probe.py instead of its SPEC dictionary; v_close_condition = the active ones.
--   3. backlog.v_status_feed: every status event with its time, for the consumers in `fondos` (read since a watermark: event_id > n).
--   4. backlog.v_violations_c: V-RETRO (the first commit citing a ticket is older than the ticket) and V-WORK-ON-CLOSED (a commit citing a CLOSED ticket
--      after its closure), joined into v_backlog_violations by fn_rebuild_violations().

-- BEGIN backlog_sync
-- 1. Operational facts.
CREATE TABLE IF NOT EXISTS backlog.ops_event (
    event_id         BIGSERIAL PRIMARY KEY,
    kind             TEXT NOT NULL CHECK (kind IN ('COMMIT', 'RELEASE', 'PROMOTION', 'CYCLE_FLAG', 'AUDIT_INCIDENT')),
    ref              TEXT NOT NULL CHECK (length(btrim(ref)) > 0),
    action_point_id  TEXT NOT NULL REFERENCES backlog.BACKLOG(action_point_id) ON DELETE CASCADE,
    source           TEXT NOT NULL CHECK (source IN ('REFS', 'SUBJECT', 'OTHER')),
    payload          JSONB NOT NULL DEFAULT '{}'::jsonb,
    actor            TEXT NOT NULL CHECK (actor ~ '^(human|agent|system):[A-Za-z0-9._-]+$'),
    fact_ts          TIMESTAMPTZ NOT NULL,                                 -- when it happened (a commit: its author time)
    recorded_ts      TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT uq_ops_event_fact UNIQUE (kind, ref, action_point_id)
);
CREATE INDEX IF NOT EXISTS idx_ops_event_ap ON backlog.ops_event (action_point_id, fact_ts);

-- 2. Closing conditions.
CREATE TABLE IF NOT EXISTS backlog.close_condition (
    condition_id     BIGSERIAL PRIMARY KEY,
    op               TEXT NOT NULL CHECK (op IN ('ADD', 'RETIRE')),
    action_point_id  TEXT NOT NULL REFERENCES backlog.BACKLOG(action_point_id) ON DELETE CASCADE,
    probe_kind       TEXT NOT NULL CHECK (probe_kind IN ('pushed', 'flag', 'calc_share', 'metric_absent', 'absent_in', 'absent_re_in', 'ols_ran',
                                                         'ticket_closed', 'task_ok', 'manual')),
    args             JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(args) = 'array'),
    retires          BIGINT REFERENCES backlog.close_condition(condition_id),
    reason           TEXT NOT NULL CHECK (length(btrim(reason)) >= 10),
    actor            TEXT NOT NULL CHECK (actor ~ '^(human|agent|system):[A-Za-z0-9._-]+$'),
    event_ts         TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK ((op = 'ADD' AND retires IS NULL) OR (op = 'RETIRE' AND retires IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_close_condition_one_retire ON backlog.close_condition (retires) WHERE op = 'RETIRE';

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['ops_event', 'close_condition'] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = ('backlog.' || t)::regclass AND tgname = 'append_only' AND NOT tgisinternal) THEN
            EXECUTE format('CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON backlog.%I FOR EACH ROW EXECUTE FUNCTION backlog.trg_append_only()', t);
        END IF;
    END LOOP;
END $$;

CREATE OR REPLACE VIEW backlog.v_close_condition AS
SELECT a.condition_id, a.action_point_id, a.probe_kind, a.args, a.reason, a.actor, a.event_ts
  FROM backlog.close_condition a
 WHERE a.op = 'ADD' AND NOT EXISTS (SELECT 1 FROM backlog.close_condition r WHERE r.op = 'RETIRE' AND r.retires = a.condition_id);

-- Records one fact; returns true when it was new, false when the same (kind, ref, ticket) was already recorded.
CREATE OR REPLACE FUNCTION backlog.sp_record_ops_event(p_kind text, p_ref text, p_ap_id text, p_source text, p_payload jsonb, p_fact_ts timestamptz,
                                                       p_actor text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, backlog AS $$
DECLARE v_id bigint;
BEGIN
    IF NOT backlog.fn_actor_authorized(p_actor) THEN
        RAISE EXCEPTION 'sp_record_ops_event: actor % is not canonical or not authorized here', p_actor USING ERRCODE = '42501';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM backlog.BACKLOG WHERE action_point_id = p_ap_id) THEN
        RAISE EXCEPTION 'sp_record_ops_event: ticket % does not exist', p_ap_id USING ERRCODE = '22023';
    END IF;
    INSERT INTO backlog.ops_event (kind, ref, action_point_id, source, payload, actor, fact_ts)
    VALUES (p_kind, btrim(p_ref), p_ap_id, p_source, COALESCE(p_payload, '{}'::jsonb), p_actor, p_fact_ts)
    ON CONFLICT ON CONSTRAINT uq_ops_event_fact DO NOTHING
    RETURNING event_id INTO v_id;
    RETURN v_id IS NOT NULL;
END;
$$;

CREATE OR REPLACE FUNCTION backlog.sp_add_close_condition(p_ap_id text, p_probe_kind text, p_args jsonb, p_reason text, p_actor text) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, backlog AS $$
DECLARE v_id bigint;
BEGIN
    IF NOT backlog.fn_actor_authorized(p_actor) THEN
        RAISE EXCEPTION 'sp_add_close_condition: actor % is not canonical or not authorized here', p_actor USING ERRCODE = '42501';
    END IF;
    IF EXISTS (SELECT 1 FROM backlog.v_close_condition WHERE action_point_id = p_ap_id AND probe_kind = p_probe_kind AND args = COALESCE(p_args, '[]'::jsonb)) THEN
        RAISE EXCEPTION 'sp_add_close_condition: % already has the condition % %', p_ap_id, p_probe_kind, p_args USING ERRCODE = '23505';
    END IF;
    INSERT INTO backlog.close_condition (op, action_point_id, probe_kind, args, reason, actor)
    VALUES ('ADD', p_ap_id, p_probe_kind, COALESCE(p_args, '[]'::jsonb), btrim(p_reason), p_actor)
    RETURNING condition_id INTO v_id;
    RETURN v_id;
END;
$$;

CREATE OR REPLACE FUNCTION backlog.sp_retire_close_condition(p_condition_id bigint, p_reason text, p_actor text) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, backlog AS $$
DECLARE v_row backlog.close_condition; v_id bigint;
BEGIN
    IF NOT backlog.fn_actor_authorized(p_actor) THEN
        RAISE EXCEPTION 'sp_retire_close_condition: actor % is not canonical or not authorized here', p_actor USING ERRCODE = '42501';
    END IF;
    SELECT * INTO v_row FROM backlog.close_condition WHERE condition_id = p_condition_id AND op = 'ADD';
    IF NOT FOUND OR NOT EXISTS (SELECT 1 FROM backlog.v_close_condition WHERE condition_id = p_condition_id) THEN
        RAISE EXCEPTION 'sp_retire_close_condition: % is not an active condition', p_condition_id USING ERRCODE = '22023';
    END IF;
    INSERT INTO backlog.close_condition (op, action_point_id, probe_kind, args, retires, reason, actor)
    VALUES ('RETIRE', v_row.action_point_id, v_row.probe_kind, v_row.args, p_condition_id, btrim(p_reason), p_actor)
    RETURNING condition_id INTO v_id;
    RETURN v_id;
END;
$$;

-- 3. Status feed for the consumers in `fondos` (read since a watermark: WHERE event_id > n ORDER BY event_id).
CREATE OR REPLACE VIEW backlog.v_status_feed AS
SELECT event_id, action_point_id, from_status, to_status, actor, origin, forced, event_ts
  FROM backlog.status_event;

-- 4. The Phase C violations whose source is ops_event.
CREATE OR REPLACE VIEW backlog.v_violations_c AS
-- V-RETRO: the first commit citing a ticket is older than the ticket (the work was done before the ticket existed).
SELECT 'V-RETRO'::text AS code, 'C'::text AS phase, f.action_point_id,
       format('first commit %s (%s) is %s min older than the ticket', left(f.ref, 12), f.source,
              round(extract(epoch FROM b.creation_ts - f.fact_ts) / 60.0)) AS detail, f.fact_ts AS event_ts
  FROM (SELECT DISTINCT ON (action_point_id) action_point_id, ref, source, fact_ts
          FROM backlog.ops_event WHERE kind = 'COMMIT' ORDER BY action_point_id, fact_ts, event_id) f
  JOIN backlog.BACKLOG b ON b.action_point_id = f.action_point_id
 WHERE f.fact_ts < b.creation_ts
UNION ALL
-- V-WORK-ON-CLOSED: a commit citing a ticket that is CLOSED, made after its closure (silent reopening, or the closure was early).
SELECT 'V-WORK-ON-CLOSED', 'C', e.action_point_id,
       format('commit %s (%s) %s h after the closure', left(e.ref, 12), e.source, round(extract(epoch FROM e.fact_ts - b.closure_ts) / 3600.0, 1)),
       e.fact_ts
  FROM backlog.ops_event e
  JOIN backlog.BACKLOG b ON b.action_point_id = e.action_point_id
 WHERE e.kind = 'COMMIT' AND b.status = 'CLOSED' AND b.closure_ts IS NOT NULL AND e.fact_ts > b.closure_ts;

-- Ownership and privileges, as in the earlier phases; then the catalogue view picks up v_violations_c.
DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT p.oid::regprocedure AS sig FROM pg_proc p
             WHERE p.pronamespace = 'backlog'::regnamespace AND p.proname IN ('sp_record_ops_event', 'sp_add_close_condition', 'sp_retire_close_condition')
    LOOP
        EXECUTE format('ALTER FUNCTION %s OWNER TO backlog_owner', r.sig);
        EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC', r.sig);
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_writer') THEN
            EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO backlog_writer', r.sig);
        END IF;
    END LOOP;
END $$;
ALTER TABLE backlog.ops_event       OWNER TO backlog_owner;
ALTER TABLE backlog.close_condition OWNER TO backlog_owner;
ALTER VIEW backlog.v_close_condition OWNER TO backlog_owner;
ALTER VIEW backlog.v_status_feed     OWNER TO backlog_owner;
ALTER VIEW backlog.v_violations_c    OWNER TO backlog_owner;
SELECT backlog.fn_rebuild_violations();
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_writer') THEN
        GRANT SELECT ON backlog.ops_event, backlog.close_condition, backlog.v_close_condition, backlog.v_status_feed, backlog.v_violations_c TO backlog_writer;
    END IF;
END $$;
-- END backlog_sync
