-- Backlog v2 descriptive change history (FND-0338; owner decision of 2026-10-27, option B of doc/especificacion/EVALUACION_LIMITACIONES_BACKLOG_V2.md).
-- Requires step A-1 as re-applied with FND-0337 (backlog.trg_append_only, backlog.fn_actor_ok, backlog.fn_actor_authorized and the role backlog_owner must exist).
-- Applied by the OWNER through scripts/ops/migrate_backlog_v2.py --phase history (dry run by default), which runs exactly the block between the markers below.
-- Idempotent. Staged rollback: db/pg_backlog/down/33_descriptive_history_down.sql.
--
-- It records, append-only, every change of the eight descriptive columns the writer keeps updating directly after step A-2 (exception E3): title, nature, schema_name,
-- object_type, object_name, priority, session_id, scenario_description. One row per column that actually changed (old and new value). The actor is what the client
-- declares for the transaction (SELECT set_config('backlog.actor', '<actor>', true); backlog_client.update_attributes() does it); a change made without a declared
-- actor, or with one that is not canonical or not authorized from that connection, is recorded with actor NULL and the database user, so the share of unattributed edits
-- is measurable (v_field_change_attribution). That share is the evidence for or against removing exception E3 later (option C of the assessment).
-- It works with or without step A-2, and it does not touch what the routines write (status, closure, release, assignee, reporter).

-- BEGIN backlog_descriptive_history
CREATE TABLE IF NOT EXISTS backlog.field_change_event (
    event_id        BIGSERIAL PRIMARY KEY,
    action_point_id TEXT NOT NULL REFERENCES backlog.BACKLOG(action_point_id) ON DELETE CASCADE,
    field           TEXT NOT NULL CHECK (field IN ('title', 'nature', 'schema_name', 'object_type', 'object_name', 'priority', 'session_id', 'scenario_description')),
    old_value       TEXT,
    new_value       TEXT,
    actor           TEXT CHECK (actor IS NULL OR actor ~ '^(human|agent|system):[A-Za-z0-9._-]+$'),
    db_user         TEXT NOT NULL,
    app_name        TEXT,
    event_ts        TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS idx_field_change_event_ap ON backlog.field_change_event (action_point_id, event_id);

DROP TRIGGER IF EXISTS append_only ON backlog.field_change_event;
CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON backlog.field_change_event FOR EACH ROW EXECUTE FUNCTION backlog.trg_append_only();

CREATE OR REPLACE FUNCTION backlog.trg_field_changes() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, backlog AS $$
DECLARE
    v_actor text := nullif(current_setting('backlog.actor', true), '');
    v_app   text := current_setting('application_name', true);
BEGIN
    IF v_actor IS NOT NULL AND NOT backlog.fn_actor_authorized(v_actor) THEN
        v_actor := NULL;                       -- a claim that is not canonical or not allowed from this connection is not recorded as an actor
    END IF;
    IF OLD.title                IS DISTINCT FROM NEW.title                THEN INSERT INTO backlog.field_change_event (action_point_id, field, old_value, new_value, actor, db_user, app_name) VALUES (NEW.action_point_id, 'title',                OLD.title,                NEW.title,                v_actor, session_user, v_app); END IF;
    IF OLD.nature               IS DISTINCT FROM NEW.nature               THEN INSERT INTO backlog.field_change_event (action_point_id, field, old_value, new_value, actor, db_user, app_name) VALUES (NEW.action_point_id, 'nature',               OLD.nature,               NEW.nature,               v_actor, session_user, v_app); END IF;
    IF OLD.schema_name          IS DISTINCT FROM NEW.schema_name          THEN INSERT INTO backlog.field_change_event (action_point_id, field, old_value, new_value, actor, db_user, app_name) VALUES (NEW.action_point_id, 'schema_name',          OLD.schema_name,          NEW.schema_name,          v_actor, session_user, v_app); END IF;
    IF OLD.object_type          IS DISTINCT FROM NEW.object_type          THEN INSERT INTO backlog.field_change_event (action_point_id, field, old_value, new_value, actor, db_user, app_name) VALUES (NEW.action_point_id, 'object_type',          OLD.object_type,          NEW.object_type,          v_actor, session_user, v_app); END IF;
    IF OLD.object_name          IS DISTINCT FROM NEW.object_name          THEN INSERT INTO backlog.field_change_event (action_point_id, field, old_value, new_value, actor, db_user, app_name) VALUES (NEW.action_point_id, 'object_name',          OLD.object_name,          NEW.object_name,          v_actor, session_user, v_app); END IF;
    IF OLD.priority             IS DISTINCT FROM NEW.priority             THEN INSERT INTO backlog.field_change_event (action_point_id, field, old_value, new_value, actor, db_user, app_name) VALUES (NEW.action_point_id, 'priority',             OLD.priority,             NEW.priority,             v_actor, session_user, v_app); END IF;
    IF OLD.session_id           IS DISTINCT FROM NEW.session_id           THEN INSERT INTO backlog.field_change_event (action_point_id, field, old_value, new_value, actor, db_user, app_name) VALUES (NEW.action_point_id, 'session_id',           OLD.session_id,           NEW.session_id,           v_actor, session_user, v_app); END IF;
    IF OLD.scenario_description IS DISTINCT FROM NEW.scenario_description THEN INSERT INTO backlog.field_change_event (action_point_id, field, old_value, new_value, actor, db_user, app_name) VALUES (NEW.action_point_id, 'scenario_description', OLD.scenario_description, NEW.scenario_description, v_actor, session_user, v_app); END IF;
    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS field_changes ON backlog.BACKLOG;
CREATE TRIGGER field_changes
    AFTER UPDATE OF title, nature, schema_name, object_type, object_name, priority, session_id, scenario_description
    ON backlog.BACKLOG
    FOR EACH ROW EXECUTE FUNCTION backlog.trg_field_changes();

-- The measure of effectiveness: of all recorded descriptive changes, how many carry an actor. One row per field and one for all.
CREATE OR REPLACE VIEW backlog.v_field_change_attribution AS
SELECT field, count(*) AS n_changes, count(actor) AS n_with_actor,
       round(100.0 * count(actor) / NULLIF(count(*), 0), 1) AS pct_with_actor
FROM backlog.field_change_event GROUP BY field
UNION ALL
SELECT 'ALL', count(*), count(actor), round(100.0 * count(actor) / NULLIF(count(*), 0), 1) FROM backlog.field_change_event;

ALTER TABLE backlog.field_change_event OWNER TO backlog_owner;
ALTER FUNCTION backlog.trg_field_changes() OWNER TO backlog_owner;
REVOKE EXECUTE ON FUNCTION backlog.trg_field_changes() FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backlog_writer') THEN
        GRANT SELECT ON backlog.field_change_event, backlog.v_field_change_attribution TO backlog_writer;
    END IF;
END $$;
-- END backlog_descriptive_history
