-- Backlog v2, Phase B: relationship semantics (FND-0333). Owner authorization 2026-10-28: implement Phase B only; applied by the OWNER on his express order.
-- Applied by scripts/ops/migrate_backlog_v2.py --phase relations (dry run by default). That ONE command runs, in ONE transaction, the block of step A-1
-- (30_transitions.sql, idempotent: it carries the gate hooks of sp_transition and the transition matrix) and then the block between the markers below, so there is no
-- separate "re-apply A-1" step and a failure in either leaves the database as it was. Idempotent.
-- Staged rollback: db/pg_backlog/down/40_relations_v2_down.sql (back to step A-1 with the hooks as no-op stubs).
--
-- What it adds (spec: doc/especificacion/PROPUESTA_BACKLOG_V2.md section 6.3):
--   1. Link types IMPACTS and SUPERSEDES and the column origin of backlog_link_event (the view v_backlog_link is NOT touched: 20_links.sql can be re-applied).
--   2. A cycle guard: a DEPENDS_ON link that would close a cycle of PROPOSED / CONFIRMED DEPENDS_ON links is refused when it is recorded.
--   3. backlog.review_flag (append-only RAISE / ACK), raised on the far end of a confirmed IMPACTS link, or of a confirmed OVERLAPS link that carries `absorbs`,
--      when a ticket reaches READY_FOR_DEPLOY or CLOSED; backlog.sp_ack_review acknowledges. SUPERSEDES only suggests (view v_supersede_suggestion).
--   4. backlog.hold_event (append-only RAISE / RELEASE): a hold blocks the delivery gate; only a human actor (the owner-only role) can release one.
--   5. The delivery gate itself: backlog.fn_gate_blockers (called by sp_transition for READY_FOR_DEPLOY and for CODE_FIX / DATA_FIX / WORKAROUND closures) and its MODE
--      (owner mitigation 2026-10-28, phased rollout): backlog.gate_switch (append-only) holds OFF / AUDIT / ENFORCE; the deployment seeds AUDIT, in which every gated move is
--      EVALUATED and LOGGED (backlog.gate_audit_event) but never refused. Only a human actor changes the mode (backlog.sp_set_gate), and ENFORCE is refused until the audit
--      has run for 48 hours, with no violation logged in the last 48 hours and gated moves on at least FIVE DIFFERENT tickets in them (view v_gate_audit_window; owner
--      decision 2026-10-28: one trivial move must not stand for a stress test of the logic under real load).
--   6. backlog.sp_annotate_wait: the structured blocker / revisit date of a ticket that is ALREADY BLOCKED or DEFERRED (used by step 0, scripts/ops/backlog_step0.py).

-- BEGIN backlog_relations_v2
-- 1. Vocabulary.
ALTER TABLE backlog.backlog_link_event DROP CONSTRAINT IF EXISTS backlog_link_event_link_type_check;
ALTER TABLE backlog.backlog_link_event ADD CONSTRAINT backlog_link_event_link_type_check
    CHECK (link_type IN ('DEPENDS_ON','RELEASE_WITH','OVERLAPS','DUPLICATES','SPLIT_FROM','RELATED','IMPACTS','SUPERSEDES'));
-- origin: how the event came to exist. REVIEW = the offline review of backlog_links.py (the default, every event written before Phase B); GATE = recorded by a gate (reserved for
-- Phase C); STEP0 = the historical corrections of scripts/ops/backlog_step0.py; SOURCE = proposed by backlog_client.open_ticket() from the FND-nnnn ids named in the text of a new
-- ticket. The vocabulary is pinned by a test against backlog_client.LINK_ORIGINS and documented in NORMAS_BACKLOG section 3.
ALTER TABLE backlog.backlog_link_event ADD COLUMN IF NOT EXISTS origin TEXT NOT NULL DEFAULT 'REVIEW';
ALTER TABLE backlog.backlog_link_event DROP CONSTRAINT IF EXISTS backlog_link_event_origin_check;
ALTER TABLE backlog.backlog_link_event ADD CONSTRAINT backlog_link_event_origin_check CHECK (origin IN ('REVIEW','GATE','STEP0','SOURCE'));

-- 2. Cycle guard.
CREATE OR REPLACE FUNCTION backlog.trg_link_cycle() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, backlog AS $$
BEGIN
    IF NEW.link_type = 'DEPENDS_ON' AND NEW.status IN ('PROPOSED', 'CONFIRMED') THEN
        IF EXISTS (
            WITH RECURSIVE walk(id, depth) AS (
                SELECT NEW.to_id, 1
                UNION
                SELECT l.to_id, w.depth + 1
                  FROM walk w
                  JOIN backlog.v_backlog_link l ON l.from_id = w.id AND l.link_type = 'DEPENDS_ON' AND l.status IN ('PROPOSED', 'CONFIRMED')
                 WHERE w.depth < 50)
            SELECT 1 FROM walk WHERE id = NEW.from_id) THEN
            RAISE EXCEPTION 'link cycle: % DEPENDS_ON % would close a cycle of DEPENDS_ON links', NEW.from_id, NEW.to_id USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS link_cycle ON backlog.backlog_link_event;
CREATE TRIGGER link_cycle BEFORE INSERT ON backlog.backlog_link_event FOR EACH ROW EXECUTE FUNCTION backlog.trg_link_cycle();

-- 3. Review flags. A RAISE row sits on the ticket that must review (the far end); an ACK row closes it. One ACK per RAISE.
CREATE TABLE IF NOT EXISTS backlog.review_flag (
    flag_id         BIGSERIAL PRIMARY KEY,
    kind            TEXT NOT NULL CHECK (kind IN ('RAISE', 'ACK')),
    action_point_id TEXT NOT NULL REFERENCES backlog.BACKLOG(action_point_id) ON DELETE CASCADE,
    source_id       TEXT NOT NULL REFERENCES backlog.BACKLOG(action_point_id) ON DELETE CASCADE,
    link_type       TEXT NOT NULL CHECK (link_type IN ('IMPACTS', 'OVERLAPS')),
    raise_id        BIGINT REFERENCES backlog.review_flag(flag_id),
    actor           TEXT NOT NULL CHECK (actor ~ '^(human|agent|system):[A-Za-z0-9._-]+$'),
    comment         TEXT,
    event_ts        TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK ((kind = 'RAISE' AND raise_id IS NULL) OR (kind = 'ACK' AND raise_id IS NOT NULL AND length(btrim(COALESCE(comment, ''))) >= 10))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_review_flag_one_ack ON backlog.review_flag (raise_id) WHERE kind = 'ACK';
CREATE INDEX IF NOT EXISTS idx_review_flag_ap ON backlog.review_flag (action_point_id, flag_id);

-- 4. Holds. A RAISE row blocks the ticket; a RELEASE row (by a human) lifts it.
CREATE TABLE IF NOT EXISTS backlog.hold_event (
    hold_id         BIGSERIAL PRIMARY KEY,
    kind            TEXT NOT NULL CHECK (kind IN ('RAISE', 'RELEASE')),
    action_point_id TEXT NOT NULL REFERENCES backlog.BACKLOG(action_point_id) ON DELETE CASCADE,
    raise_id        BIGINT REFERENCES backlog.hold_event(hold_id),
    violation_code  TEXT CHECK (violation_code ~ '^V-[A-Z0-9-]+$'),
    reason          TEXT NOT NULL CHECK (length(btrim(reason)) >= 10),
    actor           TEXT NOT NULL CHECK (actor ~ '^(human|agent|system):[A-Za-z0-9._-]+$'),
    event_ts        TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK ((kind = 'RAISE' AND raise_id IS NULL) OR (kind = 'RELEASE' AND raise_id IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_hold_event_one_release ON backlog.hold_event (raise_id) WHERE kind = 'RELEASE';
CREATE INDEX IF NOT EXISTS idx_hold_event_ap ON backlog.hold_event (action_point_id, hold_id);

-- 5. Gate mode (append-only). The row with the highest event_id is the current mode; no row at all reads as AUDIT.
CREATE TABLE IF NOT EXISTS backlog.gate_switch (
    event_id BIGSERIAL PRIMARY KEY,
    gate     TEXT NOT NULL CHECK (gate IN ('DELIVERY')),
    mode     TEXT NOT NULL CHECK (mode IN ('OFF', 'AUDIT', 'ENFORCE')),
    actor    TEXT NOT NULL CHECK (actor ~ '^(human|system):[A-Za-z0-9._-]+$'),
    reason   TEXT NOT NULL CHECK (length(btrim(reason)) >= 10),
    event_ts TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
-- One row per gated move that went through while the mode was AUDIT or ENFORCE: what blocked it (an empty array = nothing), whether it was forced.
CREATE TABLE IF NOT EXISTS backlog.gate_audit_event (
    event_id        BIGSERIAL PRIMARY KEY,
    action_point_id TEXT NOT NULL REFERENCES backlog.BACKLOG(action_point_id) ON DELETE CASCADE,
    to_status       TEXT NOT NULL,
    delivery        BOOLEAN NOT NULL,
    blockers        JSONB NOT NULL,
    mode            TEXT NOT NULL CHECK (mode IN ('AUDIT', 'ENFORCE')),
    actor           TEXT NOT NULL CHECK (actor ~ '^(human|agent|system):[A-Za-z0-9._-]+$'),
    forced          BOOLEAN NOT NULL,
    event_ts        TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS idx_gate_audit_event_ts ON backlog.gate_audit_event (event_ts);
INSERT INTO backlog.gate_switch (gate, mode, actor, reason)
SELECT 'DELIVERY', 'AUDIT', 'system:deploy', 'Phase B deployed in AUDIT mode: violations are evaluated and logged, nothing is refused (owner decision 2026-10-28)'
 WHERE NOT EXISTS (SELECT 1 FROM backlog.gate_switch WHERE gate = 'DELIVERY');

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['review_flag', 'hold_event', 'gate_switch', 'gate_audit_event'] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = ('backlog.' || t)::regclass AND tgname = 'append_only' AND NOT tgisinternal) THEN
            EXECUTE format('CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON backlog.%I FOR EACH ROW EXECUTE FUNCTION backlog.trg_append_only()', t);
        END IF;
    END LOOP;
END $$;

CREATE OR REPLACE VIEW backlog.v_open_review_flag AS
SELECT r.flag_id, r.action_point_id, r.source_id, r.link_type, r.actor, r.event_ts
  FROM backlog.review_flag r
 WHERE r.kind = 'RAISE' AND NOT EXISTS (SELECT 1 FROM backlog.review_flag a WHERE a.kind = 'ACK' AND a.raise_id = r.flag_id);

CREATE OR REPLACE VIEW backlog.v_open_hold AS
SELECT h.hold_id, h.action_point_id, h.violation_code, h.reason, h.actor, h.event_ts
  FROM backlog.hold_event h
 WHERE h.kind = 'RAISE' AND NOT EXISTS (SELECT 1 FROM backlog.hold_event x WHERE x.kind = 'RELEASE' AND x.raise_id = h.hold_id);

-- The audit log of the gate: what would have been (AUDIT) or was (ENFORCE) refused, and the window the owner reads before authorizing enforcement. `clear` is the DB-side condition
-- of sp_set_gate(ENFORCE): audit running for 48 hours or more, no violation logged in the last 48 hours, and gated moves evaluated in those 48 hours on at least
-- required_tickets (five) DIFFERENT tickets.
CREATE OR REPLACE VIEW backlog.v_gate_audit_violation AS
SELECT event_id, action_point_id, to_status, delivery, blockers, mode, actor, forced, event_ts
  FROM backlog.gate_audit_event WHERE jsonb_array_length(blockers) > 0;

CREATE OR REPLACE VIEW backlog.v_gate_audit_window AS
WITH cur AS (SELECT mode, event_ts FROM backlog.gate_switch WHERE gate = 'DELIVERY' ORDER BY event_id DESC LIMIT 1)
SELECT COALESCE((SELECT mode FROM cur), 'AUDIT') AS mode,
       (SELECT event_ts FROM cur) AS since_ts,
       CASE WHEN COALESCE((SELECT mode FROM cur), 'AUDIT') = 'AUDIT' THEN extract(epoch FROM clock_timestamp() - (SELECT event_ts FROM cur)) / 3600.0 END AS hours_in_audit,
       (SELECT count(*) FROM backlog.gate_audit_event e WHERE e.event_ts >= (SELECT event_ts FROM cur)) AS evaluated,
       (SELECT count(DISTINCT e.action_point_id) FROM backlog.gate_audit_event e WHERE e.event_ts >= (SELECT event_ts FROM cur)
           AND e.event_ts > clock_timestamp() - interval '48 hours') AS tickets_48h,
       5 AS required_tickets,
       (SELECT count(*) FROM backlog.gate_audit_event e WHERE jsonb_array_length(e.blockers) > 0 AND e.event_ts >= (SELECT event_ts FROM cur)) AS violations_since,
       (SELECT count(*) FROM backlog.gate_audit_event e WHERE jsonb_array_length(e.blockers) > 0 AND e.event_ts >= (SELECT event_ts FROM cur)
           AND e.event_ts > clock_timestamp() - interval '48 hours') AS violations_48h,
       COALESCE((SELECT mode FROM cur), 'AUDIT') = 'AUDIT'
         AND COALESCE(extract(epoch FROM clock_timestamp() - (SELECT event_ts FROM cur)) / 3600.0, 0) >= 48
         AND (SELECT count(DISTINCT e.action_point_id) FROM backlog.gate_audit_event e WHERE e.event_ts >= (SELECT event_ts FROM cur)
                 AND e.event_ts > clock_timestamp() - interval '48 hours') >= 5
         AND (SELECT count(*) FROM backlog.gate_audit_event e WHERE jsonb_array_length(e.blockers) > 0 AND e.event_ts >= (SELECT event_ts FROM cur)
                 AND e.event_ts > clock_timestamp() - interval '48 hours') = 0 AS clear;

-- SUPERSEDES: `from` supersedes `to`. It only SUGGESTS closing the superseded ticket (DUPLICATED or CODE_FIX); nothing is forced.
CREATE OR REPLACE VIEW backlog.v_supersede_suggestion AS
SELECT l.from_id AS superseding_id, s.status AS superseding_status, l.to_id AS superseded_id, o.status AS superseded_status,
       'close ' || l.to_id || ' as DUPLICATED or CODE_FIX: ' || l.from_id || ' supersedes it' AS suggestion
  FROM backlog.v_backlog_link l
  JOIN backlog.BACKLOG s ON s.action_point_id = l.from_id
  JOIN backlog.BACKLOG o ON o.action_point_id = l.to_id
 WHERE l.link_type = 'SUPERSEDES' AND l.status = 'CONFIRMED' AND s.status IN ('READY_FOR_DEPLOY', 'CLOSED') AND o.status <> 'CLOSED';

CREATE OR REPLACE FUNCTION backlog.fn_gate_mode(p_gate text) RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, backlog AS
$$ SELECT COALESCE((SELECT mode FROM backlog.gate_switch WHERE gate = p_gate ORDER BY event_id DESC LIMIT 1), 'AUDIT') $$;

-- One audit row per gated move evaluated in AUDIT or ENFORCE mode (called by sp_transition after the move).
CREATE OR REPLACE FUNCTION backlog.fn_gate_record(p_ap_id text, p_to text, p_delivery boolean, p_blockers jsonb, p_mode text, p_actor text, p_forced boolean) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, backlog AS $$
BEGIN
    INSERT INTO backlog.gate_audit_event (action_point_id, to_status, delivery, blockers, mode, actor, forced) VALUES (p_ap_id, p_to, p_delivery, p_blockers, p_mode, p_actor, p_forced);
END;
$$;

-- The gate: what blocks `p_ap_id` from READY_FOR_DEPLOY or from a delivery closure. Returns a jsonb array (empty = nothing blocks).
CREATE OR REPLACE FUNCTION backlog.fn_gate_blockers(p_ap_id text, p_to text, p_delivery boolean) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, backlog AS $$
    SELECT CASE WHEN backlog.fn_gate_mode('DELIVERY') = 'OFF' THEN '[]'::jsonb ELSE COALESCE((
        SELECT jsonb_agg(b ORDER BY ord) FROM (
            SELECT 1 AS ord, jsonb_build_object('kind', 'PREREQUISITE', 'ticket', l.to_id, 'status', t.status) AS b
              FROM backlog.v_backlog_link l JOIN backlog.BACKLOG t ON t.action_point_id = l.to_id
             WHERE l.from_id = p_ap_id AND l.link_type = 'DEPENDS_ON' AND l.status = 'CONFIRMED' AND t.status <> 'CLOSED'
            UNION ALL
            SELECT 2, jsonb_build_object('kind', 'HOLD', 'hold_id', h.hold_id, 'code', h.violation_code)
              FROM backlog.v_open_hold h WHERE h.action_point_id = p_ap_id
            UNION ALL
            SELECT 3, jsonb_build_object('kind', 'REVIEW_FLAG', 'flag_id', f.flag_id, 'source', f.source_id, 'link_type', f.link_type)
              FROM backlog.v_open_review_flag f WHERE f.action_point_id = p_ap_id) q), '[]'::jsonb) END $$;

-- Flags raised when `p_ap_id` reaches READY_FOR_DEPLOY or CLOSED: one RAISE on the far end of every confirmed IMPACTS link (from -> to) and of every confirmed
-- OVERLAPS link that carries `absorbs` (both directions), if that far end is not closed and was not flagged by this ticket before. Returns how many.
CREATE OR REPLACE FUNCTION backlog.fn_raise_review_flags(p_ap_id text, p_to text, p_actor text) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, backlog AS $$
DECLARE n integer := 0; r record;
BEGIN
    IF p_to NOT IN ('READY_FOR_DEPLOY', 'CLOSED') THEN
        RETURN 0;
    END IF;
    FOR r IN
        SELECT DISTINCT far.id AS far_id, far.link_type
          FROM (SELECT l.to_id AS id, l.link_type FROM backlog.v_backlog_link l
                 WHERE l.from_id = p_ap_id AND l.status = 'CONFIRMED' AND (l.link_type = 'IMPACTS' OR (l.link_type = 'OVERLAPS' AND l.absorbs IS NOT NULL))
                UNION ALL
                SELECT l.from_id, l.link_type FROM backlog.v_backlog_link l
                 WHERE l.to_id = p_ap_id AND l.status = 'CONFIRMED' AND l.link_type = 'OVERLAPS' AND l.absorbs IS NOT NULL) far
          JOIN backlog.BACKLOG t ON t.action_point_id = far.id AND t.status <> 'CLOSED'
         WHERE NOT EXISTS (SELECT 1 FROM backlog.review_flag f WHERE f.kind = 'RAISE' AND f.action_point_id = far.id AND f.source_id = p_ap_id AND f.link_type = far.link_type)
    LOOP
        INSERT INTO backlog.review_flag (kind, action_point_id, source_id, link_type, actor, comment)
        VALUES ('RAISE', r.far_id, p_ap_id, r.link_type, 'system:gate', p_ap_id || ' reached ' || p_to || ' through a ' || r.link_type || ' link: review this ticket');
        n := n + 1;
    END LOOP;
    RETURN n;
END;
$$;

CREATE OR REPLACE FUNCTION backlog.sp_ack_review(p_flag_id bigint, p_actor text, p_comment text) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, backlog AS $$
DECLARE f backlog.review_flag%ROWTYPE; v_id bigint;
BEGIN
    IF NOT backlog.fn_actor_ok(p_actor) THEN
        RAISE EXCEPTION 'sp_ack_review: actor % is not canonical', p_actor USING ERRCODE = '22023';
    END IF;
    IF NOT backlog.fn_actor_authorized(p_actor) THEN
        RAISE EXCEPTION 'sp_ack_review: actor % is not authorized from this connection', p_actor USING ERRCODE = '42501';
    END IF;
    IF p_comment IS NULL OR length(btrim(p_comment)) < 10 THEN
        RAISE EXCEPTION 'sp_ack_review: say in 10+ characters what was reviewed' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO f FROM backlog.review_flag WHERE flag_id = p_flag_id AND kind = 'RAISE';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'sp_ack_review: flag % does not exist', p_flag_id USING ERRCODE = '22023';
    END IF;
    IF EXISTS (SELECT 1 FROM backlog.review_flag WHERE kind = 'ACK' AND raise_id = p_flag_id) THEN
        RAISE EXCEPTION 'sp_ack_review: flag % is already acknowledged', p_flag_id USING ERRCODE = '22023';
    END IF;
    INSERT INTO backlog.review_flag (kind, action_point_id, source_id, link_type, raise_id, actor, comment)
    VALUES ('ACK', f.action_point_id, f.source_id, f.link_type, p_flag_id, p_actor, p_comment) RETURNING flag_id INTO v_id;
    INSERT INTO backlog.BACKLOG_LOGS (action_point_id, username, comment)
    VALUES (f.action_point_id, p_actor, 'Review flag ' || p_flag_id || ' (from ' || f.source_id || ', ' || f.link_type || ') acknowledged: ' || p_comment);
    RETURN v_id;
END;
$$;

CREATE OR REPLACE FUNCTION backlog.sp_raise_hold(p_ap_id text, p_reason text, p_code text, p_actor text) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, backlog AS $$
DECLARE v_id bigint;
BEGIN
    IF NOT backlog.fn_actor_ok(p_actor) THEN
        RAISE EXCEPTION 'sp_raise_hold: actor % is not canonical', p_actor USING ERRCODE = '22023';
    END IF;
    IF NOT backlog.fn_actor_authorized(p_actor) THEN
        RAISE EXCEPTION 'sp_raise_hold: actor % is not authorized from this connection', p_actor USING ERRCODE = '42501';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM backlog.BACKLOG WHERE action_point_id = p_ap_id) THEN
        RAISE EXCEPTION 'sp_raise_hold: ticket % does not exist', p_ap_id USING ERRCODE = '22023';
    END IF;
    INSERT INTO backlog.hold_event (kind, action_point_id, violation_code, reason, actor) VALUES ('RAISE', p_ap_id, p_code, p_reason, p_actor) RETURNING hold_id INTO v_id;
    INSERT INTO backlog.BACKLOG_LOGS (action_point_id, username, comment) VALUES (p_ap_id, p_actor, 'Hold ' || v_id || ' raised (' || COALESCE(p_code, 'no code') || '): ' || p_reason);
    RETURN v_id;
END;
$$;

-- Only a human actor (so only the owner-only role, once step human exists) releases a hold.
CREATE OR REPLACE FUNCTION backlog.sp_release_hold(p_hold_id bigint, p_actor text, p_reason text) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, backlog AS $$
DECLARE h backlog.hold_event%ROWTYPE; v_id bigint;
BEGIN
    IF p_actor IS NULL OR p_actor NOT LIKE 'human:%' OR NOT backlog.fn_actor_ok(p_actor) THEN
        RAISE EXCEPTION 'sp_release_hold: only a human actor releases a hold (got %)', p_actor USING ERRCODE = '42501';
    END IF;
    IF NOT backlog.fn_actor_authorized(p_actor) THEN
        RAISE EXCEPTION 'sp_release_hold: actor % is not authorized from this connection', p_actor USING ERRCODE = '42501';
    END IF;
    SELECT * INTO h FROM backlog.hold_event WHERE hold_id = p_hold_id AND kind = 'RAISE';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'sp_release_hold: hold % does not exist', p_hold_id USING ERRCODE = '22023';
    END IF;
    IF EXISTS (SELECT 1 FROM backlog.hold_event WHERE kind = 'RELEASE' AND raise_id = p_hold_id) THEN
        RAISE EXCEPTION 'sp_release_hold: hold % is already released', p_hold_id USING ERRCODE = '22023';
    END IF;
    INSERT INTO backlog.hold_event (kind, action_point_id, raise_id, reason, actor) VALUES ('RELEASE', h.action_point_id, p_hold_id, p_reason, p_actor) RETURNING hold_id INTO v_id;
    INSERT INTO backlog.BACKLOG_LOGS (action_point_id, username, comment) VALUES (h.action_point_id, p_actor, 'Hold ' || p_hold_id || ' released: ' || p_reason);
    RETURN v_id;
END;
$$;

-- Only a human actor changes the gate mode (an agent can never loosen the control that binds it). ENFORCE additionally needs the audit window to be clear: audit
-- running for 48 hours, no violation logged in the last 48 hours, gated moves on at least five different tickets in them (v_gate_audit_window.clear). Going back to AUDIT or OFF is always possible; a new AUDIT
-- row starts a new window.
CREATE OR REPLACE FUNCTION backlog.sp_set_gate(p_gate text, p_mode text, p_actor text, p_reason text) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, backlog AS $$
DECLARE v_id bigint; w record;
BEGIN
    IF p_actor IS NULL OR p_actor NOT LIKE 'human:%' OR NOT backlog.fn_actor_ok(p_actor) OR NOT backlog.fn_actor_authorized(p_actor) THEN
        RAISE EXCEPTION 'sp_set_gate: only a human actor, through the owner-only connection, changes a gate (got %)', p_actor USING ERRCODE = '42501';
    END IF;
    IF p_mode IS NULL OR p_mode NOT IN ('OFF', 'AUDIT', 'ENFORCE') THEN
        RAISE EXCEPTION 'sp_set_gate: mode must be OFF, AUDIT or ENFORCE (got %)', p_mode USING ERRCODE = '22023';
    END IF;
    IF p_mode = 'ENFORCE' THEN
        SELECT * INTO w FROM backlog.v_gate_audit_window;
        IF NOT COALESCE(w.clear, false) THEN
            RAISE EXCEPTION 'sp_set_gate: ENFORCE needs a clear audit window (mode %, % hours in audit, gated moves on % different tickets in the last 48 hours, % violations in them; needs 48 hours, % different tickets, 0 violations)',
                w.mode, round(COALESCE(w.hours_in_audit, 0)::numeric, 1), w.tickets_48h, w.violations_48h, w.required_tickets USING ERRCODE = '22023';
        END IF;
    END IF;
    INSERT INTO backlog.gate_switch (gate, mode, actor, reason) VALUES (p_gate, p_mode, p_actor, p_reason) RETURNING event_id INTO v_id;
    RETURN v_id;
END;
$$;

-- Structured blocker / revisit date for a ticket that is ALREADY BLOCKED or DEFERRED (no status change, so no status_event). Appends a wait_event and a log.
CREATE OR REPLACE FUNCTION backlog.sp_annotate_wait(
    p_ap_id text, p_actor text, p_comment text, p_blocker text DEFAULT NULL, p_condition text DEFAULT NULL,
    p_revisit_by date DEFAULT NULL, p_revisit_on_event text DEFAULT NULL, p_basis text DEFAULT 'DECLARED') RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, backlog AS $$
DECLARE t backlog.BACKLOG%ROWTYPE; v_id bigint;
BEGIN
    IF NOT backlog.fn_actor_ok(p_actor) THEN
        RAISE EXCEPTION 'sp_annotate_wait: actor % is not canonical', p_actor USING ERRCODE = '22023';
    END IF;
    IF NOT backlog.fn_actor_authorized(p_actor) THEN
        RAISE EXCEPTION 'sp_annotate_wait: actor % is not authorized from this connection', p_actor USING ERRCODE = '42501';
    END IF;
    IF p_comment IS NULL OR length(btrim(p_comment)) < 10 THEN
        RAISE EXCEPTION 'sp_annotate_wait: a comment of 10+ characters is required' USING ERRCODE = '22023';
    END IF;
    IF p_basis NOT IN ('DECLARED', 'DEFAULT_90D') THEN
        RAISE EXCEPTION 'sp_annotate_wait: unknown revisit basis %', p_basis USING ERRCODE = '22023';
    END IF;
    SELECT * INTO t FROM backlog.BACKLOG WHERE action_point_id = p_ap_id FOR UPDATE;
    IF NOT FOUND OR t.status NOT IN ('BLOCKED', 'DEFERRED') THEN
        RAISE EXCEPTION 'sp_annotate_wait: % is not a BLOCKED or DEFERRED ticket', p_ap_id USING ERRCODE = '22023';
    END IF;
    IF t.status = 'BLOCKED' THEN
        IF p_blocker IS NOT NULL THEN
            IF p_blocker = p_ap_id OR NOT EXISTS (SELECT 1 FROM backlog.BACKLOG WHERE action_point_id = p_blocker) THEN
                RAISE EXCEPTION 'sp_annotate_wait: blocker % does not exist or is the ticket itself', p_blocker USING ERRCODE = '22023';
            END IF;
        ELSIF p_condition IS NULL OR length(btrim(p_condition)) < 10 THEN
            RAISE EXCEPTION 'sp_annotate_wait: BLOCKED needs a blocker ticket or a named condition (10+ characters)' USING ERRCODE = '22023';
        END IF;
    ELSIF p_revisit_by IS NULL AND (p_revisit_on_event IS NULL OR length(btrim(p_revisit_on_event)) < 5) THEN
        RAISE EXCEPTION 'sp_annotate_wait: DEFERRED needs revisit_by or revisit_on_event' USING ERRCODE = '22023';
    END IF;
    INSERT INTO backlog.wait_event (action_point_id, kind, blocker_ref, condition_text, revisit_by, revisit_on_event, revisit_basis, actor)
    VALUES (p_ap_id, t.status, p_blocker, p_condition, p_revisit_by, p_revisit_on_event, p_basis, p_actor) RETURNING wait_id INTO v_id;
    INSERT INTO backlog.BACKLOG_LOGS (action_point_id, username, comment) VALUES (p_ap_id, p_actor, p_comment);
    RETURN v_id;
END;
$$;

-- REMOVED by the owner's order of 2026-10-28: a routine that corrects deployment_ts is not part of the schema. An earlier text of this phase created
-- backlog.sp_correct_deployment_ts (used once, for FND-0193: status event 353); applying this block drops it wherever it still exists, so a database that applied
-- the earlier text ends in the same final state as one that never had it. The history it wrote (status event 353, log 1584) is append-only and stays.
DROP FUNCTION IF EXISTS backlog.sp_correct_deployment_ts(text, timestamptz, text, text, text);

-- Ownership and privileges, as in step A-1: the objects belong to the non-superuser backlog_owner; backlog_writer may only EXECUTE the routines and read.
DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT p.oid::regprocedure AS sig FROM pg_proc p
             WHERE p.pronamespace = 'backlog'::regnamespace
               AND p.proname IN ('trg_link_cycle', 'fn_gate_mode', 'fn_gate_record', 'fn_gate_blockers', 'fn_raise_review_flags', 'sp_ack_review', 'sp_raise_hold', 'sp_release_hold', 'sp_set_gate', 'sp_annotate_wait')
    LOOP
        EXECUTE format('ALTER FUNCTION %s OWNER TO backlog_owner', r.sig);
    END LOOP;
END $$;
ALTER TABLE backlog.review_flag OWNER TO backlog_owner;
ALTER TABLE backlog.hold_event  OWNER TO backlog_owner;
ALTER TABLE backlog.gate_switch OWNER TO backlog_owner;
ALTER TABLE backlog.gate_audit_event OWNER TO backlog_owner;
ALTER VIEW backlog.v_gate_audit_violation OWNER TO backlog_owner;
ALTER VIEW backlog.v_gate_audit_window OWNER TO backlog_owner;
ALTER VIEW backlog.v_open_review_flag    OWNER TO backlog_owner;       -- the gate (owned by backlog_owner) reads these views; they read tables the same role owns
ALTER VIEW backlog.v_open_hold           OWNER TO backlog_owner;
ALTER VIEW backlog.v_supersede_suggestion OWNER TO backlog_owner;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA backlog FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_writer') THEN
        GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA backlog TO backlog_writer;
        GRANT SELECT ON backlog.review_flag, backlog.hold_event, backlog.gate_switch, backlog.gate_audit_event, backlog.v_open_review_flag, backlog.v_open_hold,
                         backlog.v_supersede_suggestion, backlog.v_gate_audit_violation, backlog.v_gate_audit_window TO backlog_writer;
    END IF;
END $$;
-- END backlog_relations_v2
