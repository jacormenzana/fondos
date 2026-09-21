-- Fase 5 (2026-09-21 session) + hardening pass (same day, post code-review): analytics view
-- and release-closure procedure for gestion.backlog. Requires 00_schema.sql already applied.

CREATE OR REPLACE VIEW backlog.vw_backlog_kpis AS
SELECT
    project_code,
    nature,
    priority,
    status,
    count(*)                                                                      AS n_items,
    avg(EXTRACT(EPOCH FROM (closure_ts - creation_ts)) / 86400.0)
        FILTER (WHERE closure_ts IS NOT NULL)                                     AS avg_lead_time_days,
    avg(EXTRACT(EPOCH FROM (deployment_ts - creation_ts)) / 86400.0)
        FILTER (WHERE deployment_ts IS NOT NULL)                                  AS avg_deployment_time_days,
    min(creation_ts)                                                              AS oldest_creation_ts,
    max(update_ts)                                                                AS most_recent_update_ts
FROM backlog.BACKLOG
GROUP BY project_code, nature, priority, status;

-- p_dry_run (added in the hardening pass, defaults FALSE): preserves the original Fase 5
-- behavior (immediate execution) for any existing/scripted caller, while letting an operator
-- preview exactly which tickets a release-version tag would close before it mutates anything —
-- closes the "accidental bulk release closure" risk flagged in code review (two unrelated
-- tickets sharing a release tag would otherwise be closed together with no way to see it coming).
DROP PROCEDURE IF EXISTS backlog.sp_close_release(TEXT, TEXT);

CREATE OR REPLACE PROCEDURE backlog.sp_close_release(
    p_release_version TEXT,
    p_username TEXT DEFAULT 'SYSTEM_RELEASE',
    p_dry_run BOOLEAN DEFAULT FALSE
)
LANGUAGE plpgsql
AS $$
DECLARE
    r RECORD;
    n INT := 0;
BEGIN
    IF p_release_version IS NULL OR p_release_version = '' THEN
        RAISE EXCEPTION 'sp_close_release: p_release_version must not be empty';
    END IF;

    IF p_dry_run THEN
        FOR r IN
            SELECT action_point_id, title
            FROM backlog.BACKLOG
            WHERE release_version = p_release_version
              AND status = 'READY_FOR_DEPLOY'
            ORDER BY action_point_id
        LOOP
            RAISE NOTICE 'DRY RUN would close: % — %', r.action_point_id, r.title;
            n := n + 1;
        END LOOP;
        RAISE NOTICE 'sp_close_release(%, dry_run=true): % ticket(s) WOULD be closed (nothing changed)',
            p_release_version, n;
        RETURN;
    END IF;

    FOR r IN
        SELECT action_point_id
        FROM backlog.BACKLOG
        WHERE release_version = p_release_version
          AND status = 'READY_FOR_DEPLOY'
    LOOP
        UPDATE backlog.BACKLOG
        SET status         = 'CLOSED',
            closure_ts     = now(),
            deployment_ts  = now(),
            solution_type  = COALESCE(solution_type, 'CODE_FIX')
        WHERE action_point_id = r.action_point_id;

        INSERT INTO backlog.BACKLOG_LOGS (action_point_id, username, comment)
        VALUES (
            r.action_point_id, p_username,
            format('Closed by sp_close_release(%L) -- release deployed', p_release_version)
        );

        n := n + 1;
    END LOOP;

    RAISE NOTICE 'sp_close_release(%): % ticket(s) closed', p_release_version, n;
END;
$$;
