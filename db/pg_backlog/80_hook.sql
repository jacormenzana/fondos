-- Backlog v2, Phase C slice 2: the commit-msg gate (FND-0334). Spec: doc/especificacion/PROPUESTA_BACKLOG_V2.md 6.4.1 and 6.4.6 and the code V-HOOK-BYPASS of
-- section 7. Applied by the OWNER: scripts/ops/migrate_backlog_v2.py --phase hook (dry run by default; ONE transaction; idempotent). Needs Phase C slice 1
-- (v_violations_c, fn_rebuild_violations) and Phase B (sp_raise_hold). Staged rollback: db/pg_backlog/down/80_hook_down.sql (drops the view and the routines; KEEPS the
-- tables).
--
-- What it adds:
--   1. backlog.gate_check: one row per run of the commit-msg hook that reached the backlog: a 32-hex nonce bound to the staged tree hash and the tickets the message
--      cites. Written only through sp_gate_check. The hook puts the nonce in the commit as the trailer `Backlog-Gate: <nonce>`.
--   2. backlog.hook_bypass: a commit that cites a ticket and does not carry a valid gate trailer (trailer missing, unknown nonce, tree mismatch: --no-verify, another
--      clone, a forged trailer). Written by scripts/audit/backlog_sync.py through sp_record_hook_bypass, which also raises a HOLD on the cited ticket (once per commit and
--      ticket; none on a CLOSED ticket, which V-WORK-ON-CLOSED already reports).
--   3. backlog.v_violations_c2: V-HOOK-BYPASS, joined into v_backlog_violations by fn_rebuild_violations().

-- BEGIN backlog_hook
-- 1. The nonces.
CREATE TABLE IF NOT EXISTS backlog.gate_check (
    check_id    BIGSERIAL PRIMARY KEY,
    nonce       TEXT NOT NULL UNIQUE CHECK (nonce ~ '^[0-9a-f]{32}$'),
    tree_hash   TEXT NOT NULL CHECK (tree_hash ~ '^[0-9a-f]{40}([0-9a-f]{24})?$'),
    tickets     TEXT[] NOT NULL DEFAULT '{}',
    actor       TEXT NOT NULL CHECK (actor ~ '^(human|agent|system):[A-Za-z0-9._-]+$'),
    check_ts    TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

-- 2. The bypasses.
CREATE TABLE IF NOT EXISTS backlog.hook_bypass (
    bypass_id        BIGSERIAL PRIMARY KEY,
    sha              TEXT NOT NULL CHECK (sha ~ '^[0-9a-f]{40}([0-9a-f]{24})?$'),
    action_point_id  TEXT NOT NULL REFERENCES backlog.BACKLOG(action_point_id) ON DELETE CASCADE,
    reason           TEXT NOT NULL CHECK (reason IN ('TRAILER_MISSING', 'UNKNOWN_NONCE', 'TREE_MISMATCH')),
    fact_ts          TIMESTAMPTZ NOT NULL,                                 -- the commit's author time
    actor            TEXT NOT NULL CHECK (actor ~ '^(human|agent|system):[A-Za-z0-9._-]+$'),
    recorded_ts      TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT uq_hook_bypass UNIQUE (sha, action_point_id)
);
CREATE INDEX IF NOT EXISTS idx_hook_bypass_ap ON backlog.hook_bypass (action_point_id, fact_ts);

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['gate_check', 'hook_bypass'] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = ('backlog.' || t)::regclass AND tgname = 'append_only' AND NOT tgisinternal) THEN
            EXECUTE format('CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON backlog.%I FOR EACH ROW EXECUTE FUNCTION backlog.trg_append_only()', t);
        END IF;
    END LOOP;
END $$;

-- Writes one gate_check row and returns its nonce. The tickets are not checked against the backlog: the hook warns about an unknown id, it does not refuse the commit.
CREATE OR REPLACE FUNCTION backlog.sp_gate_check(p_tree text, p_tickets text[], p_actor text) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, backlog AS $$
DECLARE v_nonce text;
BEGIN
    IF NOT backlog.fn_actor_authorized(p_actor) THEN
        RAISE EXCEPTION 'sp_gate_check: actor % is not canonical or not authorized here', p_actor USING ERRCODE = '42501';
    END IF;
    v_nonce := replace(gen_random_uuid()::text, '-', '');
    INSERT INTO backlog.gate_check (nonce, tree_hash, tickets, actor) VALUES (v_nonce, p_tree, COALESCE(p_tickets, '{}'), p_actor);
    RETURN v_nonce;
END;
$$;

-- Records one bypass (idempotent per commit and ticket); returns true when it was new. A NEW bypass on a ticket that is not CLOSED raises a hold on it.
CREATE OR REPLACE FUNCTION backlog.sp_record_hook_bypass(p_sha text, p_ap_id text, p_reason text, p_fact_ts timestamptz, p_actor text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, backlog AS $$
DECLARE v_id bigint; v_status text;
BEGIN
    IF NOT backlog.fn_actor_authorized(p_actor) THEN
        RAISE EXCEPTION 'sp_record_hook_bypass: actor % is not canonical or not authorized here', p_actor USING ERRCODE = '42501';
    END IF;
    SELECT status INTO v_status FROM backlog.BACKLOG WHERE action_point_id = p_ap_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'sp_record_hook_bypass: ticket % does not exist', p_ap_id USING ERRCODE = '22023';
    END IF;
    INSERT INTO backlog.hook_bypass (sha, action_point_id, reason, fact_ts, actor)
    VALUES (p_sha, p_ap_id, p_reason, p_fact_ts, p_actor)
    ON CONFLICT ON CONSTRAINT uq_hook_bypass DO NOTHING
    RETURNING bypass_id INTO v_id;
    IF v_id IS NULL THEN
        RETURN false;
    END IF;
    IF v_status <> 'CLOSED' THEN
        PERFORM backlog.sp_raise_hold(p_ap_id, format('commit %s cites this ticket without a valid commit-msg gate (%s); adjudicate it or release the hold with a reason',
                                                      left(p_sha, 12), p_reason), 'V-HOOK-BYPASS', p_actor);
    END IF;
    RETURN true;
END;
$$;

-- 3. V-HOOK-BYPASS.
CREATE OR REPLACE VIEW backlog.v_violations_c2 AS
SELECT 'V-HOOK-BYPASS'::text AS code, 'C'::text AS phase, b.action_point_id,
       format('commit %s: %s', left(b.sha, 12), lower(replace(b.reason, '_', ' '))) AS detail, b.fact_ts AS event_ts
  FROM backlog.hook_bypass b;

-- Ownership and privileges, as in the earlier phases; then the catalogue view picks up v_violations_c2.
DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT p.oid::regprocedure AS sig FROM pg_proc p
             WHERE p.pronamespace = 'backlog'::regnamespace AND p.proname IN ('sp_gate_check', 'sp_record_hook_bypass')
    LOOP
        EXECUTE format('ALTER FUNCTION %s OWNER TO backlog_owner', r.sig);
        EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC', r.sig);
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_writer') THEN
            EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO backlog_writer', r.sig);
        END IF;
    END LOOP;
END $$;
ALTER TABLE backlog.gate_check  OWNER TO backlog_owner;
ALTER TABLE backlog.hook_bypass OWNER TO backlog_owner;
ALTER VIEW backlog.v_violations_c2 OWNER TO backlog_owner;
SELECT backlog.fn_rebuild_violations();
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_writer') THEN
        GRANT SELECT ON backlog.gate_check, backlog.hook_bypass, backlog.v_violations_c2 TO backlog_writer;
    END IF;
END $$;
-- END backlog_hook
