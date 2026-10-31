-- Backlog v2, Phase D: groups, violations, lean metrics, impact closure (FND-0335). Spec: doc/especificacion/PROPUESTA_BACKLOG_V2.md section 6.5 and the
-- violation catalogue of section 7. Applied by the OWNER: scripts/ops/migrate_backlog_v2.py --phase groups (dry run by default; ONE transaction; idempotent).
-- Staged rollback: db/pg_backlog/down/60_groups_violations_down.sql (drops the views and routines; KEEPS group_tag and group_member, which are history).
-- Needs step A-1 (status_event, wait_event, raw_update_event, fn_actor_authorized, trg_append_only) and Phase B (hold_event, the link column origin).
--
-- What it adds:
--   1. backlog.group_tag (one row per group: TRAIN, THEME or DECISION) and backlog.group_member (append-only ADD / REMOVE events), written only through
--      sp_create_group and sp_group_member; v_group_member (current members) and v_group_status (READY_TO_CLOSE when every member is CLOSED).
--      A DECISION group replaces the free-text "DECISION PENDIENTE" label; the CONFIRMED RELEASE_WITH links are migrated ONCE into TRAIN groups (one per
--      connected component, named TRAIN-<lowest id>; the link type stays). A group that already exists is never touched again by a re-apply.
--   2. backlog.v_backlog_violations: one row per violation, with the codes of section 7 whose source exists in this database today (phases A, B and D:
--      view v_violations_d; a later phase adds its own v_violations_<phase> and fn_rebuild_violations() unions them).
--      The Phase C codes (V-RETRO, V-WORK-ON-CLOSED, V-HOOK-BYPASS, V-GATE-*) join the view with Phase C; V-STALE-READ lives in `fondos` (FND-0332).
--   3. backlog.v_backlog_lean_metrics: lead time from the recorded columns; cycle time and RFD wait from LIVE status events only (BACKFILL excluded).
--   4. backlog.v_impact_closure: which OPEN tickets a change in a ticket can reach, through confirmed links, up to 5 levels.

-- BEGIN backlog_groups_violations
-- 1. Groups.
CREATE TABLE IF NOT EXISTS backlog.group_tag (
    group_id     BIGSERIAL PRIMARY KEY,
    name         TEXT NOT NULL UNIQUE CHECK (name ~ '^[A-Z0-9][A-Z0-9._-]{2,63}$'),
    kind         TEXT NOT NULL CHECK (kind IN ('TRAIN', 'THEME', 'DECISION')),
    description  TEXT NOT NULL CHECK (length(btrim(description)) >= 10),
    actor        TEXT NOT NULL CHECK (actor ~ '^(human|agent|system):[A-Za-z0-9._-]+$'),
    event_ts     TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS backlog.group_member (
    member_event_id  BIGSERIAL PRIMARY KEY,
    group_id         BIGINT NOT NULL REFERENCES backlog.group_tag(group_id),
    action_point_id  TEXT NOT NULL REFERENCES backlog.BACKLOG(action_point_id) ON DELETE CASCADE,
    op               TEXT NOT NULL CHECK (op IN ('ADD', 'REMOVE')),
    reason           TEXT NOT NULL CHECK (length(btrim(reason)) >= 10),
    actor            TEXT NOT NULL CHECK (actor ~ '^(human|agent|system):[A-Za-z0-9._-]+$'),
    event_ts         TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS idx_group_member_pair ON backlog.group_member (group_id, action_point_id, member_event_id DESC);
CREATE INDEX IF NOT EXISTS idx_group_member_ap   ON backlog.group_member (action_point_id);

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['group_tag', 'group_member'] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = ('backlog.' || t)::regclass AND tgname = 'append_only' AND NOT tgisinternal) THEN
            EXECUTE format('CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON backlog.%I FOR EACH ROW EXECUTE FUNCTION backlog.trg_append_only()', t);
        END IF;
    END LOOP;
END $$;

-- Current members: the latest event of each (group, ticket) is an ADD.
CREATE OR REPLACE VIEW backlog.v_group_member AS
SELECT group_id, action_point_id, actor, event_ts
  FROM (SELECT DISTINCT ON (group_id, action_point_id) group_id, action_point_id, op, actor, event_ts
          FROM backlog.group_member
         ORDER BY group_id, action_point_id, member_event_id DESC) last
 WHERE op = 'ADD';

-- EMPTY (no member), OPEN (some member not CLOSED) or READY_TO_CLOSE (every member CLOSED: the group's purpose is met, a person decides what follows).
CREATE OR REPLACE VIEW backlog.v_group_status AS
SELECT g.group_id, g.name, g.kind, g.description,
       count(m.action_point_id)                                   AS members,
       count(m.action_point_id) FILTER (WHERE b.status = 'CLOSED') AS closed_members,
       COALESCE(array_agg(m.action_point_id ORDER BY m.action_point_id) FILTER (WHERE b.status <> 'CLOSED'), '{}') AS open_member_ids,
       CASE WHEN count(m.action_point_id) = 0 THEN 'EMPTY'
            WHEN count(m.action_point_id) FILTER (WHERE b.status <> 'CLOSED') = 0 THEN 'READY_TO_CLOSE'
            ELSE 'OPEN' END                                       AS group_status,
       g.actor, g.event_ts
  FROM backlog.group_tag g
  LEFT JOIN backlog.v_group_member m ON m.group_id = g.group_id
  LEFT JOIN backlog.BACKLOG b ON b.action_point_id = m.action_point_id
 GROUP BY g.group_id, g.name, g.kind, g.description, g.actor, g.event_ts;

CREATE OR REPLACE FUNCTION backlog.sp_create_group(p_name text, p_kind text, p_description text, p_actor text) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, backlog AS $$
DECLARE v_id bigint;
BEGIN
    IF NOT backlog.fn_actor_authorized(p_actor) THEN
        RAISE EXCEPTION 'sp_create_group: actor % is not canonical or not authorized here', p_actor USING ERRCODE = '42501';
    END IF;
    IF EXISTS (SELECT 1 FROM backlog.group_tag WHERE name = upper(btrim(p_name))) THEN
        RAISE EXCEPTION 'sp_create_group: group % already exists', upper(btrim(p_name)) USING ERRCODE = '23505';
    END IF;
    INSERT INTO backlog.group_tag (name, kind, description, actor) VALUES (upper(btrim(p_name)), p_kind, btrim(p_description), p_actor)
    RETURNING group_id INTO v_id;
    RETURN v_id;
END;
$$;

-- ADD a ticket to a group, or REMOVE it; refused when the move changes nothing (an ADD of a member, a REMOVE of a non-member).
CREATE OR REPLACE FUNCTION backlog.sp_group_member(p_group text, p_ap_id text, p_op text, p_reason text, p_actor text) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, backlog AS $$
DECLARE v_group bigint; v_member boolean; v_id bigint;
BEGIN
    IF NOT backlog.fn_actor_authorized(p_actor) THEN
        RAISE EXCEPTION 'sp_group_member: actor % is not canonical or not authorized here', p_actor USING ERRCODE = '42501';
    END IF;
    SELECT group_id INTO v_group FROM backlog.group_tag WHERE name = upper(btrim(p_group));
    IF v_group IS NULL THEN
        RAISE EXCEPTION 'sp_group_member: group % does not exist', p_group USING ERRCODE = '22023';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM backlog.BACKLOG WHERE action_point_id = p_ap_id) THEN
        RAISE EXCEPTION 'sp_group_member: ticket % does not exist', p_ap_id USING ERRCODE = '22023';
    END IF;
    v_member := EXISTS (SELECT 1 FROM backlog.v_group_member WHERE group_id = v_group AND action_point_id = p_ap_id);
    IF p_op = 'ADD' AND v_member THEN
        RAISE EXCEPTION 'sp_group_member: % is already a member of %', p_ap_id, p_group USING ERRCODE = '23505';
    ELSIF p_op = 'REMOVE' AND NOT v_member THEN
        RAISE EXCEPTION 'sp_group_member: % is not a member of %', p_ap_id, p_group USING ERRCODE = '22023';
    END IF;
    INSERT INTO backlog.group_member (group_id, action_point_id, op, reason, actor) VALUES (v_group, p_ap_id, p_op, btrim(p_reason), p_actor)
    RETURNING member_event_id INTO v_id;
    RETURN v_id;
END;
$$;

-- One-time migration of the CONFIRMED RELEASE_WITH links into TRAIN groups: one group per connected component, named after its lowest id. A component
-- whose group already exists is skipped as a whole, so a re-apply never re-adds a member the owner removed.
WITH RECURSIVE edge AS (
    SELECT from_id AS a, to_id AS b FROM backlog.v_backlog_link WHERE link_type = 'RELEASE_WITH' AND status = 'CONFIRMED'
    UNION
    SELECT to_id, from_id FROM backlog.v_backlog_link WHERE link_type = 'RELEASE_WITH' AND status = 'CONFIRMED'),
reach(node, root) AS (
    SELECT a, a FROM edge
    UNION
    SELECT e.b, r.root FROM reach r JOIN edge e ON e.a = r.node),
comp AS (SELECT node, min(root) AS root FROM reach GROUP BY node),
made AS (
    INSERT INTO backlog.group_tag (name, kind, description, actor)
    SELECT DISTINCT 'TRAIN-' || root, 'TRAIN', 'tickets released together: migrated from the confirmed RELEASE_WITH links (Phase D)', 'system:phase-d'
      FROM comp
     WHERE NOT EXISTS (SELECT 1 FROM backlog.group_tag g WHERE g.name = 'TRAIN-' || comp.root)
    RETURNING group_id, name)
INSERT INTO backlog.group_member (group_id, action_point_id, op, reason, actor)
SELECT m.group_id, c.node, 'ADD', 'member of the RELEASE_WITH component migrated in Phase D', 'system:phase-d'
  FROM made m JOIN comp c ON 'TRAIN-' || c.root = m.name;

-- 2. Violations: one row per violation. Every source below exists after steps A-1 and B; each code is the one of section 7 of the specification.
-- Each phase owns one view named v_violations_<phase> with the columns (code, phase, action_point_id, detail, event_ts); backlog.v_backlog_violations
-- is their UNION ALL, rebuilt by backlog.fn_rebuild_violations() whenever a phase adds or removes its view (so no phase copies another's SQL).
CREATE OR REPLACE VIEW backlog.v_violations_d AS
-- V-00: a direct write of a governed column outside the routines (the shadow trigger of step A-1).
SELECT 'V-00'::text AS code, 'A'::text AS phase, r.action_point_id,
       format('direct %s by %s (%s)', r.op, r.db_user, COALESCE(r.app_name, 'no application name')) AS detail, r.event_ts
  FROM backlog.raw_update_event r
UNION ALL
-- V-FORCED: a transition made with p_force (the reason is in status_event.reason).
SELECT 'V-FORCED', 'A', e.action_point_id,
       format('forced %s -> %s by %s: %s', COALESCE(e.from_status, '-'), e.to_status, e.actor, left(COALESCE(e.reason, ''), 120)), e.event_ts
  FROM backlog.status_event e
 WHERE e.forced
UNION ALL
-- V-FAST: a CODE_FIX / DATA_FIX ticket created and closed within one hour (registered after the work). Recorded columns, so the history counts too.
SELECT 'V-FAST', 'D', b.action_point_id,
       format('%s created and closed in %s min', b.solution_type, round(extract(epoch FROM b.closure_ts - b.creation_ts) / 60.0)), b.closure_ts
  FROM backlog.BACKLOG b
 WHERE b.solution_type IN ('CODE_FIX', 'DATA_FIX') AND b.closure_ts IS NOT NULL AND b.closure_ts - b.creation_ts < interval '1 hour'
UNION ALL
-- V-BLOCKED-NO-BLOCKER: BLOCKED with no BLOCKED wait event since the ticket was last in another status (the routine writes the wait event just before the
-- status event, so "since the last other status" is the window that contains it).
SELECT 'V-BLOCKED-NO-BLOCKER', 'D', b.action_point_id, 'BLOCKED with no structured blocker (wait_event)', b.update_ts
  FROM backlog.BACKLOG b
 WHERE b.status = 'BLOCKED'
   AND NOT EXISTS (SELECT 1 FROM backlog.wait_event w
                    WHERE w.action_point_id = b.action_point_id AND w.kind = 'BLOCKED'
                      AND w.event_ts > COALESCE((SELECT max(e.event_ts) FROM backlog.status_event e
                                                  WHERE e.action_point_id = b.action_point_id AND e.to_status <> 'BLOCKED'), '-infinity'::timestamptz))
UNION ALL
-- V-DEFERRED-NO-REVISIT: DEFERRED with no revisit date or revisit event in the same window.
SELECT 'V-DEFERRED-NO-REVISIT', 'D', b.action_point_id, 'DEFERRED with no revisit_by and no revisit_on_event', b.update_ts
  FROM backlog.BACKLOG b
 WHERE b.status = 'DEFERRED'
   AND NOT EXISTS (SELECT 1 FROM backlog.wait_event w
                    WHERE w.action_point_id = b.action_point_id AND w.kind = 'DEFERRED'
                      AND w.event_ts > COALESCE((SELECT max(e.event_ts) FROM backlog.status_event e
                                                  WHERE e.action_point_id = b.action_point_id AND e.to_status <> 'DEFERRED'), '-infinity'::timestamptz))
UNION ALL
-- V-REVISIT-EXPIRED: the latest revisit date of a ticket still DEFERRED has passed (listed; nothing transitions automatically).
SELECT 'V-REVISIT-EXPIRED', 'D', w.action_point_id,
       format('revisit_by %s passed (%s)', w.revisit_by, w.revisit_basis), w.revisit_by::timestamptz
  FROM (SELECT DISTINCT ON (action_point_id) action_point_id, revisit_by, revisit_basis
          FROM backlog.wait_event WHERE kind = 'DEFERRED' ORDER BY action_point_id, wait_id DESC) w
  JOIN backlog.BACKLOG b ON b.action_point_id = w.action_point_id
 WHERE b.status = 'DEFERRED' AND w.revisit_by IS NOT NULL AND w.revisit_by < current_date
UNION ALL
-- V-WORKAROUND-NO-OPT: a WORKAROUND closure with no follow-up: neither a follow-up recorded by sp_transition nor a live link to an OPT ticket.
SELECT 'V-WORKAROUND-NO-OPT', 'D', b.action_point_id, 'WORKAROUND closure with no optimisation follow-up ticket', b.closure_ts
  FROM backlog.BACKLOG b
 WHERE b.solution_type = 'WORKAROUND'
   AND NOT EXISTS (SELECT 1 FROM backlog.status_event e
                    WHERE e.action_point_id = b.action_point_id AND e.to_status = 'CLOSED' AND e.detail ->> 'followup' IS NOT NULL)
   AND NOT EXISTS (SELECT 1 FROM backlog.v_backlog_link l
                     JOIN backlog.BACKLOG o ON o.action_point_id = CASE WHEN l.from_id = b.action_point_id THEN l.to_id ELSE l.from_id END
                    WHERE (l.from_id = b.action_point_id OR l.to_id = b.action_point_id) AND l.status <> 'REJECTED' AND o.nature = 'OPT')
UNION ALL
-- V-DUP-NO-LINK: a DUPLICATED closure with no DUPLICATES link to the ticket that is kept.
SELECT 'V-DUP-NO-LINK', 'D', b.action_point_id, 'DUPLICATED closure with no DUPLICATES link', b.closure_ts
  FROM backlog.BACKLOG b
 WHERE b.solution_type = 'DUPLICATED'
   AND NOT EXISTS (SELECT 1 FROM backlog.v_backlog_link l WHERE l.from_id = b.action_point_id AND l.link_type = 'DUPLICATES' AND l.status <> 'REJECTED')
UNION ALL
-- V-HOLD-OPEN: a hold open beyond the queue alert thresholds (48 h ATENCION, 96 h ALERTA; spec 6.4.3).
SELECT 'V-HOLD-OPEN', 'D', h.action_point_id,
       format('%s: hold %s open %s h (%s)', CASE WHEN h.event_ts < clock_timestamp() - interval '96 hours' THEN 'ALERTA' ELSE 'ATENCION' END,
              h.hold_id, round(extract(epoch FROM clock_timestamp() - h.event_ts) / 3600.0), COALESCE(h.violation_code, 'no code')), h.event_ts
  FROM backlog.v_open_hold h
 WHERE h.event_ts < clock_timestamp() - interval '48 hours';

-- The catalogue view: the union of every backlog.v_violations_* present, in name order, except `p_exclude` (a rollback rebuilds the union without its
-- own view before dropping it). Run by the owner's DDL (not SECURITY DEFINER, not granted).
CREATE OR REPLACE FUNCTION backlog.fn_rebuild_violations(p_exclude text DEFAULT NULL) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, backlog AS $$
DECLARE v_sql text;
BEGIN
    SELECT string_agg(format('SELECT code, phase, action_point_id, detail, event_ts FROM backlog.%I', c.relname), ' UNION ALL ' ORDER BY c.relname)
      INTO v_sql
      FROM pg_class c
     WHERE c.relnamespace = 'backlog'::regnamespace AND c.relkind = 'v' AND c.relname LIKE 'v\_violations\_%'
       AND c.relname IS DISTINCT FROM p_exclude;
    IF v_sql IS NULL THEN
        DROP VIEW IF EXISTS backlog.v_backlog_violations;
        RETURN;
    END IF;
    EXECUTE 'CREATE OR REPLACE VIEW backlog.v_backlog_violations AS ' || v_sql;
    ALTER VIEW backlog.v_backlog_violations OWNER TO backlog_owner;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_writer') THEN
        GRANT SELECT ON backlog.v_backlog_violations TO backlog_writer;
    END IF;
END;
$$;
REVOKE EXECUTE ON FUNCTION backlog.fn_rebuild_violations(text) FROM PUBLIC;

-- 3. Lean metrics, one row per ticket, in hours. Lead time uses the recorded creation_ts / closure_ts (valid for history). Cycle time (first move to
-- IN_PROGRESS to the last closure) and RFD wait (last READY_FOR_DEPLOY to the last closure) use LIVE status events only: a BACKFILL event carries
-- the last known state, not the time of a transition, so a ticket with no LIVE events gets NULL rather than an invented number.
CREATE OR REPLACE VIEW backlog.v_backlog_lean_metrics AS
WITH live AS (SELECT action_point_id, to_status, event_ts FROM backlog.status_event WHERE origin = 'LIVE' AND from_status IS DISTINCT FROM to_status),
s AS (SELECT action_point_id, min(event_ts) AS ts FROM live WHERE to_status = 'IN_PROGRESS' GROUP BY action_point_id),
r AS (SELECT action_point_id, max(event_ts) AS ts FROM live WHERE to_status = 'READY_FOR_DEPLOY' GROUP BY action_point_id),
c AS (SELECT action_point_id, max(event_ts) AS ts FROM live WHERE to_status = 'CLOSED' GROUP BY action_point_id)
SELECT b.action_point_id, b.nature, b.priority, b.status, b.solution_type, b.creation_ts, b.closure_ts,
       CASE WHEN b.closure_ts IS NOT NULL THEN round((extract(epoch FROM b.closure_ts - b.creation_ts) / 3600.0)::numeric, 2) END AS lead_time_h,
       CASE WHEN c.ts > s.ts THEN round((extract(epoch FROM c.ts - s.ts) / 3600.0)::numeric, 2) END                                 AS cycle_time_h,
       CASE WHEN c.ts > r.ts THEN round((extract(epoch FROM c.ts - r.ts) / 3600.0)::numeric, 2) END                                 AS rfd_wait_h,
       s.ts AS first_in_progress_ts, r.ts AS last_rfd_ts, c.ts AS live_closure_ts
  FROM backlog.BACKLOG b
  LEFT JOIN s ON s.action_point_id = b.action_point_id
  LEFT JOIN r ON r.action_point_id = b.action_point_id
  LEFT JOIN c ON c.action_point_id = b.action_point_id;

-- 4. Impact closure: from each ticket, the OPEN tickets a change in it can reach through CONFIRMED links, up to 5 levels, shortest path first.
-- Directions: A IMPACTS B reaches B; A DEPENDS_ON B means a change in B reaches A; OVERLAPS and RELEASE_WITH reach both ways. RELATED, SPLIT_FROM,
-- DUPLICATES and SUPERSEDES carry no impact.
CREATE OR REPLACE VIEW backlog.v_impact_closure AS
WITH RECURSIVE edge(src, dst) AS (
    SELECT from_id, to_id FROM backlog.v_backlog_link WHERE status = 'CONFIRMED' AND link_type IN ('IMPACTS', 'OVERLAPS', 'RELEASE_WITH')
    UNION
    SELECT to_id, from_id FROM backlog.v_backlog_link WHERE status = 'CONFIRMED' AND link_type IN ('DEPENDS_ON', 'OVERLAPS', 'RELEASE_WITH')),
walk(source_id, reached_id, depth, path) AS (
    SELECT src, dst, 1, ARRAY[src, dst] FROM edge
    UNION ALL
    SELECT w.source_id, e.dst, w.depth + 1, w.path || e.dst
      FROM walk w JOIN edge e ON e.src = w.reached_id
     WHERE w.depth < 5 AND NOT e.dst = ANY (w.path))
SELECT DISTINCT ON (w.source_id, w.reached_id) w.source_id, w.reached_id, t.status AS reached_status, w.depth, array_to_string(w.path, ' > ') AS path
  FROM walk w
  JOIN backlog.BACKLOG t ON t.action_point_id = w.reached_id
 WHERE t.status <> 'CLOSED'
 ORDER BY w.source_id, w.reached_id, w.depth, array_to_string(w.path, ' > ');

-- Ownership and privileges, as in steps A-1 and B: objects belong to the non-superuser backlog_owner; backlog_writer reads and executes the routines.
DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT p.oid::regprocedure AS sig FROM pg_proc p
             WHERE p.pronamespace = 'backlog'::regnamespace AND p.proname IN ('sp_create_group', 'sp_group_member')
    LOOP
        EXECUTE format('ALTER FUNCTION %s OWNER TO backlog_owner', r.sig);
    END LOOP;
END $$;
ALTER TABLE backlog.group_tag    OWNER TO backlog_owner;
ALTER TABLE backlog.group_member OWNER TO backlog_owner;
ALTER VIEW backlog.v_group_member         OWNER TO backlog_owner;
ALTER VIEW backlog.v_group_status         OWNER TO backlog_owner;
ALTER VIEW backlog.v_violations_d         OWNER TO backlog_owner;
ALTER VIEW backlog.v_backlog_lean_metrics OWNER TO backlog_owner;
ALTER VIEW backlog.v_impact_closure       OWNER TO backlog_owner;
SELECT backlog.fn_rebuild_violations();                 -- v_backlog_violations = v_violations_d (plus any later phase's view already present)
REVOKE EXECUTE ON FUNCTION backlog.sp_create_group(text, text, text, text), backlog.sp_group_member(text, text, text, text, text) FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_writer') THEN
        GRANT EXECUTE ON FUNCTION backlog.sp_create_group(text, text, text, text), backlog.sp_group_member(text, text, text, text, text) TO backlog_writer;
        GRANT SELECT ON backlog.group_tag, backlog.group_member, backlog.v_group_member, backlog.v_group_status, backlog.v_violations_d,
                         backlog.v_backlog_lean_metrics, backlog.v_impact_closure TO backlog_writer;
    END IF;
END $$;
-- END backlog_groups_violations
