-- Canonical author identity for backlog logs and link events (FND-0330; doc/especificacion/PROPUESTA_BACKLOG_V2.md section 6.2).
-- Requires 00_schema.sql; the views read backlog_link_event, so 20_links.sql must be applied first. Applied by the OWNER through
-- scripts/ops/migrate_backlog_v2.py --phase alias (dry run by default), which runs exactly the block between the markers below
-- (single source, P#11). Idempotent. Staged rollback: db/pg_backlog/down/25_actor_alias_down.sql.
--
-- BACKLOG_LOGS and backlog_link_event are append-only, so the raw `username` values already stored are NEVER rewritten: this table
-- maps each raw spelling to one canonical identity, and the views read through it. Canonical form: human:<login> |
-- agent:<model> | system:<name>. An agent whose model was not recorded maps to agent:claude (lossy by design, said in `note`);
-- new writes carry the model (backlog_client.current_actor()).
--
-- The mapping is itself append-only (rows are never updated or deleted, so who-is-who cannot change silently). A correction is a
-- deliberate DDL act by the owner: drop the trigger, fix, re-apply this block.

-- BEGIN backlog_actor_alias
CREATE TABLE IF NOT EXISTS backlog.actor_alias (
    raw_name   TEXT PRIMARY KEY,
    canonical  TEXT NOT NULL CHECK (canonical ~ '^(human|agent|system):[A-Za-z0-9._-]+$'),
    kind       TEXT GENERATED ALWAYS AS (split_part(canonical, ':', 1)) STORED,
    note       TEXT,
    created_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by TEXT NOT NULL DEFAULT current_user
);

CREATE OR REPLACE FUNCTION backlog.trg_actor_alias_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'backlog.actor_alias is append-only: % of %.% is refused (a correction is a deliberate DDL act by the owner)',
        TG_OP, TG_TABLE_SCHEMA, TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS actor_alias_immutable ON backlog.actor_alias;
CREATE TRIGGER actor_alias_immutable
    BEFORE UPDATE OR DELETE ON backlog.actor_alias
    FOR EACH ROW EXECUTE FUNCTION backlog.trg_actor_alias_immutable();

-- The 13 author strings found in backlog_logs on 2026-10-26 (Q13 of the proposal appendix) and nothing else.
INSERT INTO backlog.actor_alias (raw_name, canonical, note) VALUES
    ('jacor',                 'human:jacor',                      'owner'),
    ('claude',                'agent:claude',                     'model not recorded'),
    ('claude-code',           'agent:claude',                     'model not recorded'),
    ('CLAUDE_CODE',           'agent:claude',                     'model not recorded'),
    ('CLAUDE_SESSION',        'agent:claude',                     'model not recorded'),
    ('CLAUDE_DEV_SESSION',    'agent:claude',                     'model not recorded'),
    ('claude_p3_assessment',  'agent:claude',                     'model not recorded; P3 assessment sessions'),
    ('claude-opus-5-5',       'agent:claude-opus-5-5',            NULL),
    ('claude-sonnet-5',       'agent:claude-sonnet-5',            NULL),
    ('LEGACY_HTML_MIGRATION', 'system:legacy_html_migration',     'one-shot migration of the HTML backlog, 2026-09-21'),
    ('SYSTEM_AUTODETECT',     'system:autodetect',                'backlog_client.report_incident()'),
    ('SYSTEM_RELEASE',        'system:release',                   'backlog.sp_close_release'),
    ('backlog_writer',        'system:backlog_writer',            'a script writing under the database role itself')
ON CONFLICT (raw_name) DO NOTHING;

-- Every log row with its canonical actor. A value that is already canonical (what backlog_client.current_actor() writes from now on)
-- maps to itself; any other spelling with no alias shows as 'unmapped:<raw>' and mapped = false.
CREATE OR REPLACE VIEW backlog.v_backlog_log_actor AS
SELECT l.log_id, l.action_point_id, l.log_ts, l.username AS raw_actor,
       COALESCE(a.canonical,
                CASE WHEN l.username ~ '^(human|agent|system):[A-Za-z0-9._-]+$' THEN l.username ELSE 'unmapped:' || l.username END) AS actor,
       (a.raw_name IS NOT NULL OR l.username ~ '^(human|agent|system):[A-Za-z0-9._-]+$') AS mapped,
       l.comment
FROM backlog.BACKLOG_LOGS l
LEFT JOIN backlog.actor_alias a ON a.raw_name = l.username;

-- Coverage check: author strings (logs and link events) that have no alias and are not canonical. Empty = covered.
CREATE OR REPLACE VIEW backlog.v_actor_unmapped AS
SELECT u.raw_actor, count(*) AS n_rows, min(u.ts) AS first_ts, max(u.ts) AS last_ts
FROM (SELECT username AS raw_actor, log_ts AS ts FROM backlog.BACKLOG_LOGS
      UNION ALL
      SELECT username, event_ts FROM backlog.backlog_link_event) u
LEFT JOIN backlog.actor_alias a ON a.raw_name = u.raw_actor
WHERE a.raw_name IS NULL
  AND NOT (u.raw_actor ~ '^(human|agent|system):[A-Za-z0-9._-]+$')
GROUP BY u.raw_actor;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_writer') THEN
        GRANT SELECT ON backlog.actor_alias, backlog.v_backlog_log_actor, backlog.v_actor_unmapped TO backlog_writer;
    END IF;
END $$;
-- END backlog_actor_alias
