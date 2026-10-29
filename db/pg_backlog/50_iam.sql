-- Backlog v2 IAM (FND-0354; owner decisions of 2026-10-29). Requires step A-1 re-applied with FND-0354 (backlog.fn_actor_bound hook, fn_actor_authorized, trg_append_only).
-- Applied by the OWNER through scripts/ops/migrate_backlog_v2.py --phase iam (dry run by default), which runs exactly the block between the markers below.
-- Idempotent. Staged rollback: db/pg_backlog/down/50_iam_down.sql.
--
-- What it adds
--   1. Three LOGIN roles WITHOUT password, created like backlog_human (the owner sets the passwords / client certificates in his own session; nothing here sees them):
--        backlog_reader    SELECT on the whole schema, nothing else. The only identity an agent session keeps in .env (FONDOS_BACKLOG_PG_DSN).
--        backlog_logger    reader + EXECUTE backlog.sp_append_log: appends a note to an EXISTING ticket and nothing else (no ticket, no transition, no link). FONDOS_BACKLOG_PG_DSN_LOGGER.
--        backlog_incident  reader + EXECUTE backlog.sp_report_incident: the automatic incident capture, insert-only (a ticket authored system:autodetect, a reincidence note).
--                          FONDOS_BACKLOG_PG_DSN_INCIDENT.
--   2. backlog.actor_login_event (append-only) and backlog.v_actor_login: which database login may write as which identity. backlog.fn_actor_bound (the hook of
--      fn_actor_authorized) enforces it: a bound login writes ONLY as its bound identity; a human identity is refused from a login that is not bound to it. Nothing is enforced
--      while no binding exists (like backlog_human before step 32). The owner binds with backlog.sp_bind_login (superuser or backlog_human only).
--   3. The shared writer retires by the owner's decision (no DDL here: ALTER ROLE ... NOLOGIN / DROP ROLE in his session, after the per-identity logins work); the acceptance check
--      lists every login that can still write and is not bound.
-- The "cryptographic" part is the TLS client certificate each login presents (pg_hba.conf: hostssl ... cert / clientcert=verify-full), which is DevOps' to configure; this file binds
-- logins to identities inside the database and the client passes the standard libpq parameters (sslmode, sslcert, sslkey, sslrootcert) untouched.

-- BEGIN backlog_iam
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_reader') THEN
        CREATE ROLE backlog_reader LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_logger') THEN
        CREATE ROLE backlog_logger LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_incident') THEN
        CREATE ROLE backlog_incident LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
    END IF;
    GRANT backlog_reader TO backlog_logger;
    GRANT backlog_reader TO backlog_incident;
END $$;

-- 1. Which database login writes as which identity. Append-only: a revocation is a new row with active = false. `actor` may use LIKE wildcards ONLY for agents
--    (agent:% = any model from this login); a human or system identity is exact. `person` is the human behind it: several logins of one person count as ONE person.
CREATE TABLE IF NOT EXISTS backlog.actor_login_event (
    event_id   BIGSERIAL PRIMARY KEY,
    login_name TEXT NOT NULL,
    actor      TEXT NOT NULL CHECK (actor ~ '^(human|agent|system):[A-Za-z0-9._%-]+$' AND (actor NOT LIKE '%\%%' OR actor LIKE 'agent:%')),
    person     TEXT NOT NULL CHECK (btrim(person) <> ''),
    kind       TEXT NOT NULL CHECK (kind IN ('HUMAN', 'AGENT', 'SYSTEM')),
    active     BOOLEAN NOT NULL,
    reason     TEXT NOT NULL CHECK (length(btrim(reason)) >= 10),
    set_by     TEXT NOT NULL DEFAULT session_user,
    event_ts   TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK ((kind = 'HUMAN') = (actor LIKE 'human:%') AND (kind = 'AGENT') = (actor LIKE 'agent:%') AND (kind = 'SYSTEM') = (actor LIKE 'system:%'))
);
CREATE INDEX IF NOT EXISTS idx_actor_login_event_login ON backlog.actor_login_event (login_name, event_id);
DROP TRIGGER IF EXISTS append_only ON backlog.actor_login_event;
CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON backlog.actor_login_event FOR EACH ROW EXECUTE FUNCTION backlog.trg_append_only();
ALTER TABLE backlog.actor_login_event OWNER TO backlog_owner;

CREATE OR REPLACE VIEW backlog.v_actor_login AS
SELECT DISTINCT ON (login_name) login_name, actor, person, kind, active, reason, set_by, event_ts, event_id
FROM backlog.actor_login_event
ORDER BY login_name, event_id DESC;
GRANT SELECT ON backlog.actor_login_event, backlog.v_actor_login TO backlog_owner;

-- 2. The hook of fn_actor_authorized. Looks at session_user (who logged in), never at current_user.
CREATE OR REPLACE FUNCTION backlog.fn_actor_bound(p_actor text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, backlog AS
$$ SELECT CASE
       WHEN NOT EXISTS (SELECT 1 FROM backlog.v_actor_login WHERE active) THEN true                                       -- no binding exists yet: nothing is enforced
       WHEN COALESCE((SELECT rolsuper FROM pg_roles WHERE rolname = session_user), false) THEN true                       -- the DDL role
       WHEN EXISTS (SELECT 1 FROM backlog.v_actor_login WHERE login_name = session_user AND active)
            THEN EXISTS (SELECT 1 FROM backlog.v_actor_login WHERE login_name = session_user AND active AND p_actor LIKE actor)   -- a bound login writes ONLY as its identity
       WHEN p_actor LIKE 'human:%' THEN false                                                                              -- a human identity only from a login bound to it
       ELSE true                                                                                                           -- agent/system from an UNBOUND login: allowed until the shared writer retires (reported as UNBOUND)
       END $$;
ALTER FUNCTION backlog.fn_actor_bound(text) OWNER TO backlog_owner;

-- 3. Bind (or unbind) a login. Only a superuser or a member of backlog_human (the owner) may.
CREATE OR REPLACE FUNCTION backlog.sp_bind_login(p_login text, p_actor text, p_person text, p_kind text, p_active boolean, p_reason text) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, backlog AS $$
DECLARE
    v_id bigint;
BEGIN
    IF NOT (COALESCE((SELECT rolsuper FROM pg_roles WHERE rolname = session_user), false)
            OR (EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_human') AND pg_has_role(session_user, 'backlog_human', 'MEMBER'))) THEN
        RAISE EXCEPTION 'sp_bind_login: only the owner (superuser or backlog_human) binds logins to identities' USING ERRCODE = '42501';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = p_login AND rolcanlogin) THEN
        RAISE EXCEPTION 'sp_bind_login: % is not a login role', p_login USING ERRCODE = '22023';
    END IF;
    INSERT INTO backlog.actor_login_event (login_name, actor, person, kind, active, reason) VALUES (p_login, p_actor, p_person, p_kind, p_active, p_reason)
    RETURNING event_id INTO v_id;
    RETURN v_id;
END $$;
ALTER FUNCTION backlog.sp_bind_login(text, text, text, text, boolean, text) OWNER TO backlog_owner;
GRANT INSERT ON backlog.actor_login_event TO backlog_owner;
GRANT USAGE ON SEQUENCE backlog.actor_login_event_event_id_seq TO backlog_owner;

-- 4. The agent bookkeeping path (owner decision 2026-10-29): append a note to an EXISTING ticket. Agent and system identities only (a human appends through his own role).
CREATE OR REPLACE FUNCTION backlog.sp_append_log(p_ap_id text, p_actor text, p_comment text) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, backlog AS $$
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
END $$;
ALTER FUNCTION backlog.sp_append_log(text, text, text) OWNER TO backlog_owner;

-- 5. The automatic incident capture (owner decision 2026-10-29): insert-only. The client decides the overlap (it reads with its own SELECT) and passes the result; the routine
--    dedups, then writes a reincidence note on the open ticket or opens one authored system:autodetect. It cannot transition, link or edit anything.
CREATE OR REPLACE FUNCTION backlog.sp_report_incident(
    p_object_name text, p_description text, p_project_code text DEFAULT 'FND', p_schema_name text DEFAULT NULL, p_object_type text DEFAULT 'JOB', p_title text DEFAULT NULL,
    p_dedup interval DEFAULT '24 hours', p_overlap_ticket text DEFAULT NULL, p_overlap_reason text DEFAULT NULL, p_suggest_ticket text DEFAULT NULL, p_suggest_reason text DEFAULT NULL)
RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, backlog AS $$
DECLARE
    v_actor constant text := 'system:autodetect';
    v_id    text;
BEGIN
    IF NOT backlog.fn_actor_authorized(v_actor) THEN
        RAISE EXCEPTION 'sp_report_incident: % is not authorized from this connection', v_actor USING ERRCODE = '42501';
    END IF;
    IF btrim(coalesce(p_object_name, '')) = '' OR btrim(coalesce(p_description, '')) = '' OR length(p_description) > 20000 OR p_dedup > interval '7 days' THEN
        RAISE EXCEPTION 'sp_report_incident: object name and description are required (description <= 20000 characters, dedup window <= 7 days)' USING ERRCODE = '22023';
    END IF;
    SELECT action_point_id INTO v_id FROM backlog.BACKLOG
    WHERE project_code = p_project_code AND object_name = p_object_name AND status IN ('OPEN', 'TODO', 'IN_PROGRESS', 'BLOCKED') AND creation_ts >= now() - p_dedup
    ORDER BY creation_ts DESC LIMIT 1;
    IF v_id IS NOT NULL THEN
        INSERT INTO backlog.BACKLOG_LOGS (action_point_id, username, comment) VALUES (v_id, v_actor, 'Reincidence detected: ' || p_description);
        RETURN v_id;
    END IF;
    IF p_overlap_ticket IS NOT NULL AND EXISTS (SELECT 1 FROM backlog.BACKLOG WHERE action_point_id = p_overlap_ticket) THEN
        INSERT INTO backlog.BACKLOG_LOGS (action_point_id, username, comment)
        VALUES (p_overlap_ticket, v_actor, 'Reincidence detected (overlap: ' || coalesce(p_overlap_reason, '?') || ') on ' || p_object_name || ': ' || p_description);
        RETURN p_overlap_ticket;
    END IF;
    RETURN backlog.sp_open_ticket(
        p_title => coalesce(p_title, '[auto] ' || p_object_name || ': ' || left(p_description, 100)), p_nature => 'BUG', p_object_type => p_object_type, p_object_name => p_object_name,
        p_priority => 'HIGH', p_actor => v_actor, p_session_id => 'FND_' || to_char(now() AT TIME ZONE 'UTC', 'YYYYMMDD') || '_REVB_AUTODETECT', p_description => p_description,
        p_first_log => 'Auto-opened: ' || p_description
                       || CASE WHEN p_suggest_ticket IS NULL THEN '' ELSE E'\n\nPossibly related (NOT merged): ' || p_suggest_ticket || ' -- ' || coalesce(p_suggest_reason, '?') END,
        p_schema_name => p_schema_name, p_project_code => p_project_code);
END $$;
ALTER FUNCTION backlog.sp_report_incident(text, text, text, text, text, text, interval, text, text, text, text) OWNER TO backlog_owner;

-- 6. Privileges. A new function is executable by PUBLIC in Postgres: revoke it first, then grant exactly what each role needs.
REVOKE EXECUTE ON FUNCTION backlog.fn_actor_bound(text) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION backlog.sp_bind_login(text, text, text, text, boolean, text) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION backlog.sp_append_log(text, text, text) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION backlog.sp_report_incident(text, text, text, text, text, text, interval, text, text, text, text) FROM PUBLIC;
GRANT USAGE ON SCHEMA backlog TO backlog_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA backlog TO backlog_reader;
GRANT EXECUTE ON FUNCTION backlog.sp_append_log(text, text, text) TO backlog_logger;
GRANT EXECUTE ON FUNCTION backlog.sp_report_incident(text, text, text, text, text, text, interval, text, text, text, text) TO backlog_incident;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_writer') THEN
        GRANT EXECUTE ON FUNCTION backlog.sp_append_log(text, text, text) TO backlog_writer;                 -- the shared writer keeps working until it retires
        GRANT EXECUTE ON FUNCTION backlog.sp_report_incident(text, text, text, text, text, text, interval, text, text, text, text) TO backlog_writer;
        GRANT EXECUTE ON FUNCTION backlog.sp_bind_login(text, text, text, text, boolean, text) TO backlog_writer;   -- the routine itself refuses anyone but the owner
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_human') THEN
        GRANT EXECUTE ON FUNCTION backlog.sp_bind_login(text, text, text, text, boolean, text) TO backlog_human;
    END IF;
END $$;
-- END backlog_iam
