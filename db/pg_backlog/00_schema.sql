-- Fase 1 (2026-09-21 session): Action-Points Backlog relational model.
-- Target: `gestion` database (separate database on the same Postgres server used for the
-- P1/P2/P3 operational migration, db/pg/ -- NOT a schema inside `fondos`), schema `backlog`.
-- Source of the domain design: user-provided specification, 2026-09-21 session.
--
-- Deploy: psql "postgresql://<role>@<host>:5432/gestion" -f db/pg_backlog/00_schema.sql
-- (create the `gestion` database first if it doesn't exist yet: CREATE DATABASE gestion;)

CREATE SCHEMA IF NOT EXISTS backlog;

-- One SEQUENCE per project (per spec Sec.4.1). Fondos project code = FND.
CREATE SEQUENCE IF NOT EXISTS backlog.seq_backlog_fnd START WITH 1 INCREMENT BY 1;

CREATE TABLE IF NOT EXISTS backlog.BACKLOG (
    action_point_id      TEXT PRIMARY KEY,
    title                TEXT NOT NULL,
    project_code         TEXT NOT NULL,
    nature               TEXT NOT NULL
                         CHECK (nature IN ('BUG','REQ','CHG','OPT','INV','DOC','TSK')),
    schema_name          TEXT,
    object_type          TEXT NOT NULL
                         CHECK (object_type IN ('TAB','VW','SP','UDF','TVF','JOB','SCHEMA','N/A')),
    object_name          TEXT,
    status               TEXT NOT NULL DEFAULT 'OPEN'
                         CHECK (status IN ('OPEN','TODO','IN_PROGRESS','READY_FOR_DEPLOY',
                                            'BLOCKED','DEFERRED','CLOSED')),
    priority             TEXT NOT NULL
                         CHECK (priority IN ('HIGH','MEDIUM','LOW')),
    reporter             TEXT,
    assignee             TEXT,
    creation_ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    update_ts            TIMESTAMPTZ NOT NULL DEFAULT now(),
    closure_ts           TIMESTAMPTZ,
    deployment_ts        TIMESTAMPTZ,
    release_version      TEXT,
    solution_type        TEXT
                         CHECK (solution_type IS NULL
                                OR solution_type IN ('CODE_FIX','DATA_FIX','WORKAROUND',
                                                      'REJECTED','DUPLICATED')),
    session_id           TEXT,
    scenario_description TEXT
);

CREATE TABLE IF NOT EXISTS backlog.BACKLOG_LOGS (
    log_id           BIGSERIAL PRIMARY KEY,
    action_point_id  TEXT NOT NULL REFERENCES backlog.BACKLOG(action_point_id) ON DELETE CASCADE,
    log_ts           TIMESTAMPTZ NOT NULL DEFAULT now(),
    username         TEXT NOT NULL,
    comment          TEXT NOT NULL
);

-- update_ts auto-maintenance trigger
CREATE OR REPLACE FUNCTION backlog.trg_set_update_ts() RETURNS trigger AS $$
BEGIN
    NEW.update_ts := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS set_update_ts ON backlog.BACKLOG;
CREATE TRIGGER set_update_ts
    BEFORE UPDATE ON backlog.BACKLOG
    FOR EACH ROW
    EXECUTE FUNCTION backlog.trg_set_update_ts();

CREATE INDEX IF NOT EXISTS idx_backlog_status       ON backlog.BACKLOG (status);
CREATE INDEX IF NOT EXISTS idx_backlog_project_code ON backlog.BACKLOG (project_code);
CREATE INDEX IF NOT EXISTS idx_backlog_assignee     ON backlog.BACKLOG (assignee);
CREATE INDEX IF NOT EXISTS idx_backlog_creation_ts  ON backlog.BACKLOG (creation_ts);
CREATE INDEX IF NOT EXISTS idx_backlog_logs_apid    ON backlog.BACKLOG_LOGS (action_point_id);
