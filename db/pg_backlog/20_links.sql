-- Relationships between action points (owner request 2026-10-10; doc/reglas/NORMAS_BACKLOG.md).
-- Requires 00_schema.sql. Applied by the OWNER through scripts/ops/migrate_backlog_links.py (dry run by default), which runs
-- exactly the block between the markers below (single source, P#11). Idempotent.
--
-- Model: ONE append-only event table. Every proposal, confirmation, rejection or retype of a link is a NEW row (who, when,
-- evidence); the current state of a pair is its latest event (v_backlog_link). Nothing is ever updated or deleted by the
-- tools, the same rule as backlog_logs (AGENTS.md "never overwrite prior progress").
--
-- Link types (one direction per pair, from -> to):
--   DEPENDS_ON    from cannot be finished/validated before to
--   RELEASE_WITH  both must be activated in the same release
--   OVERLAPS      same code object, column, switch or signal: a change in one changes the other (`absorbs` says which scope)
--   DUPLICATES    from duplicates to (to is the one kept)
--   SPLIT_FROM    from was carved out of to
--   RELATED       context only

-- BEGIN backlog_links
CREATE TABLE IF NOT EXISTS backlog.backlog_link_event (
    event_id    BIGSERIAL PRIMARY KEY,
    from_id     TEXT NOT NULL REFERENCES backlog.BACKLOG(action_point_id) ON DELETE CASCADE,
    to_id       TEXT NOT NULL REFERENCES backlog.BACKLOG(action_point_id) ON DELETE CASCADE,
    link_type   TEXT NOT NULL
                CHECK (link_type IN ('DEPENDS_ON','RELEASE_WITH','OVERLAPS','DUPLICATES','SPLIT_FROM','RELATED')),
    status      TEXT NOT NULL
                CHECK (status IN ('PROPOSED','CONFIRMED','REJECTED')),
    evidence    TEXT NOT NULL,
    absorbs     TEXT,
    username    TEXT NOT NULL,
    event_ts    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (from_id <> to_id)
);
CREATE INDEX IF NOT EXISTS idx_link_event_pair ON backlog.backlog_link_event (from_id, to_id, link_type, event_ts DESC, event_id DESC);
CREATE INDEX IF NOT EXISTS idx_link_event_to   ON backlog.backlog_link_event (to_id);

-- Current state of every (from, to, type): its latest event. Ties on event_ts are broken by event_id (S-3: deterministic).
CREATE OR REPLACE VIEW backlog.v_backlog_link AS
SELECT DISTINCT ON (from_id, to_id, link_type)
       from_id, to_id, link_type, status, evidence, absorbs, username, event_ts, event_id
FROM backlog.backlog_link_event
ORDER BY from_id, to_id, link_type, event_ts DESC, event_id DESC;

-- Confirmed links that touch at least one open ticket, with the state of both ends; blocked_by_open = a DEPENDS_ON whose
-- prerequisite (to) is still open.
CREATE OR REPLACE VIEW backlog.v_open_link_graph AS
SELECT l.from_id, f.status AS from_status, f.title AS from_title,
       l.to_id,   t.status AS to_status,   t.title AS to_title,
       l.link_type, l.absorbs, l.evidence, l.event_ts,
       (l.link_type = 'DEPENDS_ON' AND t.status <> 'CLOSED') AS blocked_by_open
FROM backlog.v_backlog_link l
JOIN backlog.BACKLOG f ON f.action_point_id = l.from_id
JOIN backlog.BACKLOG t ON t.action_point_id = l.to_id
WHERE l.status = 'CONFIRMED'
  AND (f.status <> 'CLOSED' OR t.status <> 'CLOSED');

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_writer') THEN
        GRANT SELECT, INSERT ON backlog.backlog_link_event TO backlog_writer;
        GRANT USAGE ON SEQUENCE backlog.backlog_link_event_event_id_seq TO backlog_writer;
        GRANT SELECT ON backlog.v_backlog_link, backlog.v_open_link_graph TO backlog_writer;
    END IF;
END $$;
-- END backlog_links
