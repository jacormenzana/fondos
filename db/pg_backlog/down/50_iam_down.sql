-- ROLLBACK of db/pg_backlog/50_iam.sql (FND-0354). Run by scripts/ops/migrate_backlog_v2.py --phase iam --down --apply (dry run without --apply).
--
-- DISABLES the IAM step and KEEPS ITS HISTORY (owner order 2026-10-29: a rollback never drops an append-only history table). In ONE transaction it removes the behavior: the three roles,
-- the routines (sp_bind_login, sp_set_binding_window, sp_append_log, sp_report_incident, fn_off_hours) and the views (v_actor_login, v_binding_audit, v_binding_window), and puts the hook
-- fn_actor_bound back to the stub that allows everything (no binding is enforced while the step is rolled back).
--   kept, with every row: actor_login_event (the binding history, including declared_by) and binding_window_event (the window history), with their append-only triggers, ownership,
--         constraints and sequences. Applying `--phase iam` again resumes with the history intact and enforcement comes back with it.
--   untouched: tickets, logs, status events, wait events, link events, the gate.
-- It ends by checking that both tables have exactly the rows it found; otherwise it raises and the whole rollback is undone.
-- There is deliberately NO script that drops these tables: removing that history is a decision of the owner, made by hand after exporting them.

-- BEGIN backlog_iam_down
DO $$
BEGIN
    IF to_regclass('backlog.actor_login_event') IS NOT NULL AND to_regclass('backlog.binding_window_event') IS NOT NULL THEN
        PERFORM set_config('backlog.rb_iam_rows', (SELECT count(*) || ':' || COALESCE(max(event_id), 0) FROM backlog.actor_login_event)
                           || '/' || (SELECT count(*) || ':' || COALESCE(max(event_id), 0) FROM backlog.binding_window_event), true);
    ELSE
        PERFORM set_config('backlog.rb_iam_rows', '', true);
    END IF;
END $$;

CREATE OR REPLACE FUNCTION backlog.fn_actor_bound(p_actor text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, backlog AS $$ SELECT true $$;
DROP VIEW IF EXISTS backlog.v_binding_audit;
DROP VIEW IF EXISTS backlog.v_binding_window;
DROP VIEW IF EXISTS backlog.v_actor_login;
DROP FUNCTION IF EXISTS backlog.sp_report_incident(text, text, text, text, text, text, interval, text, text, text, text);
DROP FUNCTION IF EXISTS backlog.sp_append_log(text, text, text);
DROP FUNCTION IF EXISTS backlog.sp_bind_login(text, text, text, text, boolean, text, text);
DROP FUNCTION IF EXISTS backlog.sp_bind_login(text, text, text, text, boolean, text);
DROP FUNCTION IF EXISTS backlog.sp_set_binding_window(text, smallint[], time, time, text, text);
DROP FUNCTION IF EXISTS backlog.fn_off_hours(timestamptz);
DO $$
DECLARE r text;
BEGIN
    FOREACH r IN ARRAY ARRAY['backlog_logger', 'backlog_incident', 'backlog_reader'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
            EXECUTE format('DROP OWNED BY %I', r);
            EXECUTE format('DROP ROLE %I', r);
        END IF;
    END LOOP;
END $$;

-- history invariant: both tables have exactly the rows this rollback found; otherwise the whole rollback is undone
DO $$
DECLARE v_rows text;
BEGIN
    IF current_setting('backlog.rb_iam_rows') <> '' THEN
        v_rows := (SELECT count(*) || ':' || COALESCE(max(event_id), 0) FROM backlog.actor_login_event)
                  || '/' || (SELECT count(*) || ':' || COALESCE(max(event_id), 0) FROM backlog.binding_window_event);
        IF v_rows IS DISTINCT FROM current_setting('backlog.rb_iam_rows') THEN
            RAISE EXCEPTION 'rollback aborted: the binding or window history would change from % to % (nothing was changed)', current_setting('backlog.rb_iam_rows'), v_rows USING ERRCODE = '55000';
        END IF;
        RAISE NOTICE 'IAM is disabled: the binding and window history is kept (% )', v_rows;
    END IF;
END $$;
-- END backlog_iam_down
