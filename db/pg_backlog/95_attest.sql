-- Backlog v2, Phase C slice 4: branch-protection facts of the collision gate and their violations (FND-0334). Spec: doc/especificacion/PROPUESTA_BACKLOG_V2.md 6.4.6 / 6.4.7 and the codes
-- V-GATE-UNPROTECTED, V-GATE-UNVERIFIED and V-GATE-EMERGENCY of section 7. Applied by the OWNER: scripts/ops/migrate_backlog_v2.py --phase attest (dry run by default; ONE transaction;
-- idempotent). Needs slice 2 (the catalogue view). Staged rollback: db/pg_backlog/down/95_attest_down.sql (drops the view and the routine; KEEPS the table).
--
-- What it adds:
--   1. backlog.gate_protection_event: append-only facts about whether `backlog-gate` is a REQUIRED status check of master. Two sources only:
--        API   (kinds PROTECTED / UNPROTECTED / UNVERIFIED, actor system:*): what scripts/audit/backlog_sync.py read from the GitHub API with a read-only token held outside the repository.
--        OWNER (kinds VERIFIED / EMERGENCY, actor human:*): an owner-written verification (it clears V-GATE-UNVERIFIED for 30 days) or the log of an emergency push (V-GATE-EMERGENCY).
--      Written only through sp_record_gate_protection.
--   2. backlog.v_violations_c3: V-GATE-UNPROTECTED (the latest API reading says it is not required), V-GATE-UNVERIFIED (the latest API reading could not tell -- no token, API unreachable,
--      not enough permission -- and no owner VERIFIED event of the last 30 days: a missing token never passes silently) and V-GATE-EMERGENCY (one row per emergency event). These are facts about
--      the repository, so their ticket is the one that owns the gate, FND-0334.

-- BEGIN backlog_attest
CREATE TABLE IF NOT EXISTS backlog.gate_protection_event (
    event_id   BIGSERIAL PRIMARY KEY,
    kind       TEXT NOT NULL CHECK (kind IN ('PROTECTED', 'UNPROTECTED', 'UNVERIFIED', 'VERIFIED', 'EMERGENCY')),
    source     TEXT NOT NULL CHECK (source IN ('API', 'OWNER')),
    reason     TEXT NOT NULL CHECK (length(btrim(reason)) >= 10),
    actor      TEXT NOT NULL CHECK (actor ~ '^(human|system):[A-Za-z0-9._-]+$'),
    event_ts   TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK ((source = 'API'   AND kind IN ('PROTECTED', 'UNPROTECTED', 'UNVERIFIED') AND actor LIKE 'system:%')
        OR (source = 'OWNER' AND kind IN ('VERIFIED', 'EMERGENCY')                  AND actor LIKE 'human:%'))
);
CREATE INDEX IF NOT EXISTS idx_gate_protection_event_ts ON backlog.gate_protection_event (source, event_id);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = 'backlog.gate_protection_event'::regclass AND tgname = 'append_only' AND NOT tgisinternal) THEN
        CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON backlog.gate_protection_event FOR EACH ROW EXECUTE FUNCTION backlog.trg_append_only();
    END IF;
END $$;

-- Records one fact; returns true when it was recorded. An API reading is recorded only when it differs from the latest API reading or the latest one is older than 24 hours (a daily
-- sync does not flood the table); an owner event is always recorded.
CREATE OR REPLACE FUNCTION backlog.sp_record_gate_protection(p_kind text, p_reason text, p_actor text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, backlog AS $$
DECLARE v_source text; last record;
BEGIN
    IF NOT backlog.fn_actor_ok(p_actor) OR NOT backlog.fn_actor_authorized(p_actor) THEN
        RAISE EXCEPTION 'sp_record_gate_protection: actor % is not canonical or not authorized from this connection', p_actor USING ERRCODE = '42501';
    END IF;
    v_source := CASE WHEN p_kind IN ('PROTECTED', 'UNPROTECTED', 'UNVERIFIED') THEN 'API' WHEN p_kind IN ('VERIFIED', 'EMERGENCY') THEN 'OWNER' END;
    IF v_source IS NULL THEN
        RAISE EXCEPTION 'sp_record_gate_protection: kind % is not PROTECTED, UNPROTECTED, UNVERIFIED, VERIFIED or EMERGENCY', p_kind USING ERRCODE = '22023';
    END IF;
    IF v_source = 'API' AND p_actor NOT LIKE 'system:%' THEN
        RAISE EXCEPTION 'sp_record_gate_protection: an API reading is written by a system actor (got %)', p_actor USING ERRCODE = '42501';
    END IF;
    IF v_source = 'OWNER' AND p_actor NOT LIKE 'human:%' THEN
        RAISE EXCEPTION 'sp_record_gate_protection: % events are written by a person (got %)', p_kind, p_actor USING ERRCODE = '42501';
    END IF;
    IF v_source = 'API' THEN
        SELECT kind, event_ts INTO last FROM backlog.gate_protection_event WHERE source = 'API' ORDER BY event_id DESC LIMIT 1;
        IF FOUND AND last.kind = p_kind AND last.event_ts > clock_timestamp() - interval '24 hours' THEN
            RETURN false;
        END IF;
    END IF;
    INSERT INTO backlog.gate_protection_event (kind, source, reason, actor) VALUES (p_kind, v_source, p_reason, p_actor);
    RETURN true;
END;
$$;

CREATE OR REPLACE VIEW backlog.v_violations_c3 AS
WITH api AS (SELECT kind, reason, event_ts FROM backlog.gate_protection_event WHERE source = 'API' ORDER BY event_id DESC LIMIT 1)
SELECT 'V-GATE-UNPROTECTED'::text AS code, 'C'::text AS phase, 'FND-0334'::text AS action_point_id, a.reason AS detail, a.event_ts
  FROM api a WHERE a.kind = 'UNPROTECTED'
UNION ALL
SELECT 'V-GATE-UNVERIFIED', 'C', 'FND-0334', a.reason, a.event_ts
  FROM api a
 WHERE a.kind = 'UNVERIFIED'
   AND NOT EXISTS (SELECT 1 FROM backlog.gate_protection_event v WHERE v.kind = 'VERIFIED' AND v.event_ts > clock_timestamp() - interval '30 days')
UNION ALL
SELECT 'V-GATE-EMERGENCY', 'C', 'FND-0334', e.reason, e.event_ts
  FROM backlog.gate_protection_event e WHERE e.kind = 'EMERGENCY';

-- Ownership and privileges, as in the earlier phases; then the catalogue view picks up v_violations_c3.
DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT p.oid::regprocedure AS sig FROM pg_proc p WHERE p.pronamespace = 'backlog'::regnamespace AND p.proname = 'sp_record_gate_protection'
    LOOP
        EXECUTE format('ALTER FUNCTION %s OWNER TO backlog_owner', r.sig);
        EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC', r.sig);
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_writer') THEN
            EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO backlog_writer', r.sig);
        END IF;
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_human') THEN
            EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO backlog_human', r.sig);
        END IF;
    END LOOP;
END $$;
ALTER TABLE backlog.gate_protection_event OWNER TO backlog_owner;
ALTER VIEW backlog.v_violations_c3 OWNER TO backlog_owner;
SELECT backlog.fn_rebuild_violations();
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_writer') THEN
        GRANT SELECT ON backlog.gate_protection_event, backlog.v_violations_c3 TO backlog_writer;
    END IF;
END $$;
-- END backlog_attest
