-- Backlog v2, Phase C slice 3: the collision rule, adjudication and the gate modes (FND-0334). Spec: doc/especificacion/PROPUESTA_BACKLOG_V2.md 6.4.2 to 6.4.5.
-- Applied by the OWNER: scripts/ops/migrate_backlog_v2.py --phase collision (dry run by default; ONE transaction; idempotent). Needs slice 2 (the phase `hook`: gate_check, the
-- backlog_gate login) and Phase B (holds, links). Staged rollback: db/pg_backlog/down/90_collision_down.sql (drops the views and the routines; KEEPS the tables).
--
-- The role backlog_adjudicator is NOT created here: the owner creates it and keeps its DSN outside the repository .env (spec 6.4.3: every agent session can read the writer DSN, so an
-- "actor is not the committer" check inside software would be spoofable). Until it exists nobody can adjudicate, which is the safe state; the grants below are made when it does.
--
-- What it adds:
--   1. backlog.gate_state: append-only mode events of the collision gate (OFF / AUDIT / BLOCK and the rule tier T0 / T1). The effective mode is the LOWER of the ceiling in
--      shared/config.py (BACKLOG_COLLISION_GATE_MODE, passed as a parameter) and the latest event; the tier is the latest event's, T0 when there is none. Written only through
--      sp_set_collision_mode: a person (human actor) may set anything, but BLOCK only after 14 days of AUDIT and a share of commits introducing a new undecided pair of 10 % or
--      less; system:calibrator may only retire the gate (OFF) or tighten T0 -> T1 and never lowers BLOCK to AUDIT, never loosens.
--   2. backlog.gate_collision: one row per hook run that evaluated the rule (mode AUDIT or BLOCK): the nonce, the tickets, how many NEW undecided pairs it introduced. The share of
--      commits that introduce a pair is read from it.
--   3. backlog.gate_adjudication: append-only record of each decision on a pair (OVERLAPS / RELATED / DEPENDS_ON / NONE / REVERT), written only by sp_adjudicate_overlap.
--   4. fn_collision_pairs: the rule (T0: a code file whose full path is cited in the text or logs of another IN_PROGRESS ticket; files cited by 5 or more open tickets are ignored; pairs
--      with a CONFIRMED link or already adjudicated are skipped; T1: at least 2 shared files). sp_gate_collision records the pairs it finds as PROPOSED OVERLAPS link events with origin
--      GATE (in canonical order: the smaller id first), and in BLOCK raises a hold on each committing ticket that has an undecided pair.
--   5. sp_adjudicate_overlap: checks session_user (the role backlog_adjudicator, or a member), writes the CONFIRMED / REJECTED events, a log on both tickets and releases the holds of
--      the tickets left with no undecided pair.
--   6. v_gate_queue: the undecided pairs with their age (48 h ATENCION, 96 h ALERTA), and fn_gate_undecided for the pre-push hook.

-- BEGIN backlog_collision
-- 1. Mode events.
CREATE TABLE IF NOT EXISTS backlog.gate_state (
    event_id  BIGSERIAL PRIMARY KEY,
    mode      TEXT NOT NULL CHECK (mode IN ('OFF', 'AUDIT', 'BLOCK')),
    tier      TEXT NOT NULL DEFAULT 'T0' CHECK (tier IN ('T0', 'T1')),
    actor     TEXT NOT NULL CHECK (actor ~ '^(human|system):[A-Za-z0-9._-]+$'),
    reason    TEXT NOT NULL CHECK (length(btrim(reason)) >= 10),
    evidence  JSONB NOT NULL DEFAULT '{}'::jsonb,
    event_ts  TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

-- 2. One row per evaluation of the rule at commit time.
CREATE TABLE IF NOT EXISTS backlog.gate_collision (
    collision_id  BIGSERIAL PRIMARY KEY,
    nonce         TEXT NOT NULL CHECK (nonce ~ '^[0-9a-f]{32}$'),
    tickets       TEXT[] NOT NULL DEFAULT '{}',
    mode          TEXT NOT NULL CHECK (mode IN ('AUDIT', 'BLOCK')),
    tier          TEXT NOT NULL CHECK (tier IN ('T0', 'T1')),
    files_n       INTEGER NOT NULL CHECK (files_n >= 0),
    new_pairs     INTEGER NOT NULL CHECK (new_pairs >= 0),
    event_ts      TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS idx_gate_collision_ts ON backlog.gate_collision (event_ts);

-- 3. Decisions on pairs. a < b (canonical order).
CREATE TABLE IF NOT EXISTS backlog.gate_adjudication (
    adjudication_id  BIGSERIAL PRIMARY KEY,
    a                TEXT NOT NULL REFERENCES backlog.BACKLOG(action_point_id) ON DELETE CASCADE,
    b                TEXT NOT NULL REFERENCES backlog.BACKLOG(action_point_id) ON DELETE CASCADE,
    decision         TEXT NOT NULL CHECK (decision IN ('OVERLAPS', 'RELATED', 'DEPENDS_ON', 'NONE', 'REVERT')),
    absorbs          TEXT,
    reason           TEXT NOT NULL CHECK (length(btrim(reason)) >= 10),
    actor            TEXT NOT NULL CHECK (actor ~ '^human:[A-Za-z0-9._-]+$'),
    event_ts         TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK (a < b)
);
CREATE INDEX IF NOT EXISTS idx_gate_adjudication_pair ON backlog.gate_adjudication (a, b);

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['gate_state', 'gate_collision', 'gate_adjudication'] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = ('backlog.' || t)::regclass AND tgname = 'append_only' AND NOT tgisinternal) THEN
            EXECUTE format('CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON backlog.%I FOR EACH ROW EXECUTE FUNCTION backlog.trg_append_only()', t);
        END IF;
    END LOOP;
END $$;

-- 4. Pairs of OVERLAPS (latest event per ordered pair) and the undecided ones: the latest event is a PROPOSED recorded by the gate.
CREATE OR REPLACE VIEW backlog.v_gate_pair AS
SELECT DISTINCT ON (least(from_id, to_id), greatest(from_id, to_id))
       least(from_id, to_id) AS a, greatest(from_id, to_id) AS b, status, origin, evidence, event_ts, event_id
  FROM backlog.backlog_link_event
 WHERE link_type = 'OVERLAPS'
 ORDER BY least(from_id, to_id), greatest(from_id, to_id), event_ts DESC, event_id DESC;

CREATE OR REPLACE VIEW backlog.v_gate_queue AS
SELECT p.a, p.b, p.evidence, p.event_ts AS proposed_ts,
       round((extract(epoch FROM clock_timestamp() - p.event_ts) / 3600.0)::numeric, 1) AS age_hours,
       CASE WHEN clock_timestamp() - p.event_ts >= interval '96 hours' THEN 'ALERTA'
            WHEN clock_timestamp() - p.event_ts >= interval '48 hours' THEN 'ATENCION' ELSE 'OK' END AS level
  FROM backlog.v_gate_pair p
 WHERE p.status = 'PROPOSED' AND p.origin = 'GATE';

-- 5. Modes.
CREATE OR REPLACE FUNCTION backlog.fn_mode_rank(p_mode text) RETURNS integer
LANGUAGE sql IMMUTABLE AS $$ SELECT CASE p_mode WHEN 'OFF' THEN 0 WHEN 'AUDIT' THEN 1 WHEN 'BLOCK' THEN 2 END $$;

-- The effective mode and tier: the lower of the ceiling and the latest event; T0 when there is no event.
CREATE OR REPLACE FUNCTION backlog.fn_collision_state(p_ceiling text) RETURNS TABLE (mode text, tier text, state_event bigint)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, backlog AS $$
DECLARE s backlog.gate_state%ROWTYPE;
BEGIN
    IF backlog.fn_mode_rank(p_ceiling) IS NULL THEN
        RAISE EXCEPTION 'fn_collision_state: ceiling % is not OFF, AUDIT or BLOCK', p_ceiling USING ERRCODE = '22023';
    END IF;
    SELECT * INTO s FROM backlog.gate_state ORDER BY event_id DESC LIMIT 1;
    IF NOT FOUND THEN
        RETURN QUERY SELECT p_ceiling, 'T0'::text, NULL::bigint;
    ELSE
        RETURN QUERY SELECT CASE WHEN backlog.fn_mode_rank(s.mode) < backlog.fn_mode_rank(p_ceiling) THEN s.mode ELSE p_ceiling END, s.tier, s.event_id;
    END IF;
END;
$$;

-- Share of the commits evaluated in the last p_days days that introduced a new undecided pair (NULL when none was evaluated).
CREATE OR REPLACE FUNCTION backlog.fn_gate_share(p_days integer DEFAULT 3) RETURNS numeric
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, backlog AS $$
    SELECT CASE WHEN count(*) = 0 THEN NULL ELSE round(count(*) FILTER (WHERE new_pairs > 0)::numeric / count(*), 4) END
      FROM backlog.gate_collision WHERE event_ts >= clock_timestamp() - make_interval(days => p_days) $$;

-- When the audit began: the first AUDIT event written by a person, else the first evaluation of the rule (NULL before either).
CREATE OR REPLACE FUNCTION backlog.fn_audit_start() RETURNS timestamptz
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, backlog AS $$
    SELECT COALESCE((SELECT min(event_ts) FROM backlog.gate_state WHERE mode = 'AUDIT' AND actor LIKE 'human:%'), (SELECT min(event_ts) FROM backlog.gate_collision)) $$;

CREATE OR REPLACE FUNCTION backlog.sp_set_collision_mode(p_mode text, p_tier text, p_actor text, p_reason text, p_ceiling text, p_evidence jsonb DEFAULT '{}'::jsonb) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, backlog AS $$
DECLARE cur record; v_id bigint; v_share numeric; v_start timestamptz;
BEGIN
    IF NOT backlog.fn_actor_ok(p_actor) OR NOT backlog.fn_actor_authorized(p_actor) THEN
        RAISE EXCEPTION 'sp_set_collision_mode: actor % is not canonical or not authorized from this connection', p_actor USING ERRCODE = '42501';
    END IF;
    IF p_mode NOT IN ('OFF', 'AUDIT', 'BLOCK') OR p_tier NOT IN ('T0', 'T1') THEN
        RAISE EXCEPTION 'sp_set_collision_mode: mode % / tier % is not valid', p_mode, p_tier USING ERRCODE = '22023';
    END IF;
    SELECT * INTO cur FROM backlog.fn_collision_state(p_ceiling);
    IF p_actor = 'system:calibrator' THEN
        -- the calibrator retires the gate (OFF) or tightens T0 -> T1 at the mode in force; it never lowers BLOCK to AUDIT and never loosens
        IF NOT ((p_mode = 'OFF' AND p_tier = cur.tier) OR (p_mode = cur.mode AND cur.tier = 'T0' AND p_tier = 'T1')) THEN
            RAISE EXCEPTION 'sp_set_collision_mode: system:calibrator may only retire the gate (OFF) or tighten T0 to T1 at the mode in force (now % %)', cur.mode, cur.tier USING ERRCODE = '42501';
        END IF;
    ELSIF p_actor NOT LIKE 'human:%' THEN
        RAISE EXCEPTION 'sp_set_collision_mode: only a person or system:calibrator sets the mode (got %)', p_actor USING ERRCODE = '42501';
    ELSIF p_mode = 'BLOCK' THEN
        v_start := backlog.fn_audit_start();
        v_share := backlog.fn_gate_share(3);
        IF v_start IS NULL OR clock_timestamp() - v_start < interval '14 days' THEN
            RAISE EXCEPTION 'sp_set_collision_mode: BLOCK needs 14 days of AUDIT (started %)', v_start USING ERRCODE = '55000';
        END IF;
        IF v_share IS NULL OR v_share > 0.10 THEN
            RAISE EXCEPTION 'sp_set_collision_mode: BLOCK needs the share of commits introducing an undecided pair at 10 %% or less (now %)', v_share USING ERRCODE = '55000';
        END IF;
    END IF;
    INSERT INTO backlog.gate_state (mode, tier, actor, reason, evidence) VALUES (p_mode, p_tier, p_actor, p_reason, COALESCE(p_evidence, '{}'::jsonb)) RETURNING event_id INTO v_id;
    RETURN v_id;
END;
$$;

-- 6. The rule.
CREATE OR REPLACE FUNCTION backlog.fn_collision_pairs(p_files text[], p_tickets text[], p_tier text) RETURNS TABLE (a text, b text, shared text[])
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, backlog AS $$
    WITH code_files AS (
        SELECT DISTINCT f FROM unnest(p_files) AS f WHERE length(btrim(f)) > 0 AND f !~* '\.(md|txt)$'
    ), corpus AS (                                           -- the text and the logs of every open ticket
        SELECT t.action_point_id AS id, t.status,
               lower(COALESCE(t.title, '') || ' ' || COALESCE(t.scenario_description, '') || ' '
                     || COALESCE((SELECT string_agg(l.comment, ' ') FROM backlog.BACKLOG_LOGS l WHERE l.action_point_id = t.action_point_id), '')) AS txt
          FROM backlog.BACKLOG t WHERE t.status <> 'CLOSED'
    ), hub AS (                                              -- how many open tickets cite each file
        SELECT cf.f, count(*) AS n FROM code_files cf JOIN corpus c ON strpos(c.txt, lower(cf.f)) > 0 GROUP BY cf.f
    ), cited AS (                                            -- files of this commit cited by ANOTHER ticket in progress
        SELECT c.id AS other, cf.f
          FROM corpus c JOIN code_files cf ON strpos(c.txt, lower(cf.f)) > 0 JOIN hub h ON h.f = cf.f AND h.n < 5
         WHERE c.status = 'IN_PROGRESS' AND c.id <> ALL (p_tickets)
    ), cand AS (
        SELECT least(t.action_point_id, c.other) AS a, greatest(t.action_point_id, c.other) AS b, array_agg(DISTINCT c.f ORDER BY c.f) AS shared
          FROM unnest(p_tickets) AS tk
          JOIN backlog.BACKLOG t ON t.action_point_id = tk
          JOIN cited c ON true
         GROUP BY least(t.action_point_id, c.other), greatest(t.action_point_id, c.other)
    )
    SELECT cand.a, cand.b, cand.shared FROM cand
     WHERE (p_tier = 'T0' OR cardinality(cand.shared) >= 2)
       AND NOT EXISTS (SELECT 1 FROM backlog.v_backlog_link l WHERE l.status = 'CONFIRMED'
                        AND ((l.from_id = cand.a AND l.to_id = cand.b) OR (l.from_id = cand.b AND l.to_id = cand.a)))
       AND NOT EXISTS (SELECT 1 FROM backlog.gate_adjudication j WHERE j.a = cand.a AND j.b = cand.b) $$;

-- Evaluates the rule for one commit and records what it finds. Returns {mode, tier, new: [{a, b, shared}], undecided: [{a, b}], held: [ticket]}.
CREATE OR REPLACE FUNCTION backlog.sp_gate_collision(p_files text[], p_tickets text[], p_ceiling text, p_nonce text, p_actor text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, backlog AS $$
DECLARE
    st record; r record; v_new jsonb := '[]'::jsonb; v_und jsonb := '[]'::jsonb; v_held text[] := '{}'; v_n integer := 0; tk text;
BEGIN
    IF NOT backlog.fn_actor_authorized(p_actor) THEN
        RAISE EXCEPTION 'sp_gate_collision: actor % is not canonical or not authorized here', p_actor USING ERRCODE = '42501';
    END IF;
    IF session_user = 'backlog_gate' AND p_actor <> 'system:gate' THEN
        RAISE EXCEPTION 'sp_gate_collision: the backlog_gate login writes only as system:gate (got %)', p_actor USING ERRCODE = '42501';
    END IF;
    SELECT * INTO st FROM backlog.fn_collision_state(p_ceiling);
    IF st.mode = 'OFF' THEN
        RETURN jsonb_build_object('mode', 'OFF', 'tier', st.tier, 'new', '[]'::jsonb, 'undecided', '[]'::jsonb, 'held', '[]'::jsonb);
    END IF;
    FOR r IN SELECT * FROM backlog.fn_collision_pairs(p_files, p_tickets, st.tier) LOOP
        IF NOT EXISTS (SELECT 1 FROM backlog.v_gate_pair p WHERE p.a = r.a AND p.b = r.b) THEN        -- no OVERLAPS event for the pair yet, either direction
            INSERT INTO backlog.backlog_link_event (from_id, to_id, link_type, status, evidence, username, origin)
            VALUES (r.a, r.b, 'OVERLAPS', 'PROPOSED',
                    format('collision gate %s (%s), commit %s: shared code file(s) %s', st.tier, st.mode, left(p_nonce, 8), array_to_string(r.shared, ', ')), p_actor, 'GATE');
            v_n := v_n + 1;
            v_new := v_new || jsonb_build_object('a', r.a, 'b', r.b, 'shared', to_jsonb(r.shared));
        END IF;
        IF EXISTS (SELECT 1 FROM backlog.v_gate_queue q WHERE q.a = r.a AND q.b = r.b) THEN
            v_und := v_und || jsonb_build_object('a', r.a, 'b', r.b);
        END IF;
    END LOOP;
    IF st.mode = 'BLOCK' THEN                                -- the hold keeps a ticket with an undecided pair from READY_FOR_DEPLOY and delivery closure
        FOREACH tk IN ARRAY COALESCE(p_tickets, '{}') LOOP
            IF EXISTS (SELECT 1 FROM backlog.v_gate_queue q WHERE tk IN (q.a, q.b))
               AND NOT EXISTS (SELECT 1 FROM backlog.v_open_hold h WHERE h.action_point_id = tk AND h.violation_code = 'V-COLLISION')
               AND EXISTS (SELECT 1 FROM backlog.BACKLOG WHERE action_point_id = tk) THEN
                PERFORM backlog.sp_raise_hold(tk, 'a commit touches a code file cited by another ticket in progress: adjudicate the pair (backlog.sp_adjudicate_overlap) to release this hold', 'V-COLLISION', p_actor);
                v_held := v_held || tk;
            END IF;
        END LOOP;
    END IF;
    INSERT INTO backlog.gate_collision (nonce, tickets, mode, tier, files_n, new_pairs) VALUES (p_nonce, COALESCE(p_tickets, '{}'), st.mode, st.tier, COALESCE(cardinality(p_files), 0), v_n);
    RETURN jsonb_build_object('mode', st.mode, 'tier', st.tier, 'new', v_new, 'undecided', v_und, 'held', to_jsonb(v_held));
END;
$$;

-- Undecided pairs that touch any of the tickets (the pre-push hook reads this).
CREATE OR REPLACE FUNCTION backlog.fn_gate_undecided(p_tickets text[]) RETURNS TABLE (a text, b text, age_hours numeric)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, backlog AS $$
    SELECT q.a, q.b, q.age_hours FROM backlog.v_gate_queue q WHERE q.a = ANY (p_tickets) OR q.b = ANY (p_tickets) ORDER BY q.a, q.b $$;

-- 7. Adjudication. The check is on the SESSION user (a SECURITY DEFINER routine runs as its owner, so current_user would always be the owner).
CREATE OR REPLACE FUNCTION backlog.sp_adjudicate_overlap(p_a text, p_b text, p_decision text, p_absorbs text, p_reason text, p_actor text) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, backlog AS $$
DECLARE
    v_a text := least(p_a, p_b); v_b text := greatest(p_a, p_b); v_id bigint; tk text; v_hold record;
BEGIN
    IF NOT (EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_adjudicator') AND pg_has_role(session_user, 'backlog_adjudicator', 'MEMBER')) THEN
        RAISE EXCEPTION 'sp_adjudicate_overlap: only the role backlog_adjudicator adjudicates (session user %)', session_user USING ERRCODE = '42501';
    END IF;
    IF p_actor IS NULL OR p_actor NOT LIKE 'human:%' OR NOT backlog.fn_actor_ok(p_actor) THEN
        RAISE EXCEPTION 'sp_adjudicate_overlap: the deciding identity must be a canonical human:<login> (got %)', p_actor USING ERRCODE = '42501';
    END IF;
    IF p_decision NOT IN ('OVERLAPS', 'RELATED', 'DEPENDS_ON', 'NONE', 'REVERT') THEN
        RAISE EXCEPTION 'sp_adjudicate_overlap: decision % is not OVERLAPS, RELATED, DEPENDS_ON, NONE or REVERT', p_decision USING ERRCODE = '22023';
    END IF;
    IF p_reason IS NULL OR length(btrim(p_reason)) < 10 THEN
        RAISE EXCEPTION 'sp_adjudicate_overlap: a reason of 10 or more characters is required' USING ERRCODE = '22023';
    END IF;
    IF p_decision = 'REVERT' AND p_reason !~ '[0-9a-f]{40}' THEN
        RAISE EXCEPTION 'sp_adjudicate_overlap: REVERT needs the sha of the revert commit (40 hex characters) in the reason' USING ERRCODE = '22023';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM backlog.v_gate_queue q WHERE q.a = v_a AND q.b = v_b) THEN
        RAISE EXCEPTION 'sp_adjudicate_overlap: the pair % / % is not undecided', v_a, v_b USING ERRCODE = '22023';
    END IF;
    -- the effect on the links: the proposal is always closed; the decision adds its own confirmed link
    IF p_decision IN ('OVERLAPS', 'REVERT') THEN
        INSERT INTO backlog.backlog_link_event (from_id, to_id, link_type, status, evidence, absorbs, username, origin)
        VALUES (v_a, v_b, 'OVERLAPS', 'CONFIRMED', 'adjudicated ' || p_decision || ': ' || btrim(p_reason), NULLIF(btrim(p_absorbs), ''), p_actor, 'GATE');
    ELSE
        INSERT INTO backlog.backlog_link_event (from_id, to_id, link_type, status, evidence, username, origin)
        VALUES (v_a, v_b, 'OVERLAPS', 'REJECTED', 'adjudicated ' || p_decision || ': ' || btrim(p_reason), p_actor, 'GATE');
        IF p_decision IN ('RELATED', 'DEPENDS_ON') THEN
            INSERT INTO backlog.backlog_link_event (from_id, to_id, link_type, status, evidence, absorbs, username, origin)
            VALUES (v_a, v_b, p_decision, 'CONFIRMED', 'adjudicated ' || p_decision || ': ' || btrim(p_reason), NULLIF(btrim(p_absorbs), ''), p_actor, 'GATE');
        END IF;
    END IF;
    INSERT INTO backlog.gate_adjudication (a, b, decision, absorbs, reason, actor) VALUES (v_a, v_b, p_decision, NULLIF(btrim(p_absorbs), ''), btrim(p_reason), p_actor) RETURNING adjudication_id INTO v_id;
    FOREACH tk IN ARRAY ARRAY[v_a, v_b] LOOP
        INSERT INTO backlog.BACKLOG_LOGS (action_point_id, username, comment)
        VALUES (tk, p_actor, format('Collision pair %s / %s adjudicated %s: %s', v_a, v_b, p_decision, btrim(p_reason)));
        IF NOT EXISTS (SELECT 1 FROM backlog.v_gate_queue q WHERE tk IN (q.a, q.b)) THEN       -- no undecided pair left: the collision holds of this ticket go
            FOR v_hold IN SELECT hold_id FROM backlog.v_open_hold WHERE action_point_id = tk AND violation_code = 'V-COLLISION' LOOP
                INSERT INTO backlog.hold_event (kind, action_point_id, raise_id, reason, actor)
                VALUES ('RELEASE', tk, v_hold.hold_id, 'every collision pair of this ticket is adjudicated', p_actor);
            END LOOP;
        END IF;
    END LOOP;
    RETURN v_id;
END;
$$;

-- Ownership and privileges, as in the earlier phases.
DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT p.oid::regprocedure AS sig, p.proname FROM pg_proc p
             WHERE p.pronamespace = 'backlog'::regnamespace
               AND p.proname IN ('fn_mode_rank', 'fn_collision_state', 'fn_gate_share', 'fn_audit_start', 'sp_set_collision_mode', 'fn_collision_pairs', 'sp_gate_collision', 'fn_gate_undecided', 'sp_adjudicate_overlap')
    LOOP
        EXECUTE format('ALTER FUNCTION %s OWNER TO backlog_owner', r.sig);
        EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC', r.sig);
    END LOOP;
    -- the commit-msg and pre-push hooks (the scoped login of the hook): the rule, the state and the undecided pairs, nothing more
    GRANT EXECUTE ON FUNCTION backlog.sp_gate_collision(text[], text[], text, text, text), backlog.fn_collision_state(text), backlog.fn_gate_undecided(text[]) TO backlog_gate;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_writer') THEN                     -- read for the reports; the calibrator and the owner set the mode through it
        GRANT EXECUTE ON FUNCTION backlog.fn_collision_state(text), backlog.fn_gate_share(integer), backlog.fn_audit_start(), backlog.fn_gate_undecided(text[]),
              backlog.sp_set_collision_mode(text, text, text, text, text, jsonb) TO backlog_writer;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_human') THEN
        GRANT EXECUTE ON FUNCTION backlog.sp_set_collision_mode(text, text, text, text, text, jsonb) TO backlog_human;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_adjudicator') THEN                -- created by the owner, never by this script
        GRANT USAGE ON SCHEMA backlog TO backlog_adjudicator;
        GRANT EXECUTE ON FUNCTION backlog.sp_adjudicate_overlap(text, text, text, text, text, text), backlog.fn_gate_undecided(text[]) TO backlog_adjudicator;
        GRANT SELECT ON backlog.v_gate_queue TO backlog_adjudicator;
    END IF;
END $$;
ALTER TABLE backlog.gate_state       OWNER TO backlog_owner;
ALTER TABLE backlog.gate_collision   OWNER TO backlog_owner;
ALTER TABLE backlog.gate_adjudication OWNER TO backlog_owner;
ALTER VIEW backlog.v_gate_pair  OWNER TO backlog_owner;
ALTER VIEW backlog.v_gate_queue OWNER TO backlog_owner;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_writer') THEN
        GRANT SELECT ON backlog.gate_state, backlog.gate_collision, backlog.gate_adjudication, backlog.v_gate_pair, backlog.v_gate_queue TO backlog_writer;
    END IF;
END $$;
-- END backlog_collision
