# Backlog v2 IAM rollout: rollback playbook (FND-0354, FND-0333, FND-0350)

Written for: the owner, at his own terminal (cmd.exe), applying `--phase relations` and `--phase iam` to the live `gestion` database.
Every state below was rehearsed with the real command-line tools on PostgreSQL 17 against a copy of production as it is today (master `0979d80`: step A-1 plus Phase B, the gate in ENFORCE, a hold, and the `system:owner-waiver` row). Nothing here touches tickets, logs, status events, wait events or link events.

Conventions: repo root `C:\desarrollo\fondos` with the stack checked out; interpreter `C:\data\envs\des\python.exe`; the owner DSN is typed for the session (`set FONDOS_PG_DSN_OWNER=...`), never stored. Tool exit codes: 0 done, 1 failed and rolled back, 2 usage or connection. Every level uses the native tool: no custom script is run in production.

## 0. Before applying
1. **Check the rollback script is the safe one.** This must print NOTHING (the first version dropped the Phase B tables):
   `findstr /I /C:"DROP TABLE" /C:"DELETE" db\pg_backlog\down\40_relations_v2_down.sql | findstr /V /B /C:"--"`
   (A test fails the build if the script ever gains a `DROP TABLE`, `DELETE`, `TRUNCATE`, `ALTER TABLE` or `UPDATE`.)
2. Restore point (the live `gestion` database is included): `wsl -d Ubuntu -e bash /mnt/c/desarrollo/fondos/scripts/ops/backup_live_pg.sh`
3. Baseline of the history: `C:\data\envs\des\python.exe scripts\audit\backlog_phase_a_acceptance.py --baseline` (keep the file name it prints).
4. Today's gate state, to compare after: `SELECT backlog.fn_gate_mode('DELIVERY'); SELECT count(*) FROM backlog.gate_switch; SELECT count(*) FROM backlog.hold_event;`
5. **Confirm the TARGET before any `--apply`** (the first deployment attempt of 2026-10-29 went to a staging replica, `127.0.0.1:5433/des`, and production stayed unchanged). The owner DSN you type decides the server: the tool only swaps the database name to `gestion`. Every command of section 1 prints, as its first line, `target: host=... port=... dbname=... user=...`. **For production it must read exactly `host=127.0.0.1 port=5436 dbname=gestion`.** Any other port or database: stop and fix the DSN; do not apply. The dry run prints the same line, so the check costs nothing.

## 1. Apply (each phase is ONE transaction)
```
C:\data\envs\des\python.exe scripts\ops\migrate_backlog_v2.py --phase relations
C:\data\envs\des\python.exe scripts\ops\migrate_backlog_v2.py --phase relations --apply
C:\data\envs\des\python.exe scripts\audit\backlog_phase_a_acceptance.py --verify relations --baseline-file <file> --migration-rc %ERRORLEVEL%
C:\data\envs\des\python.exe scripts\ops\migrate_backlog_v2.py --phase iam
C:\data\envs\des\python.exe scripts\ops\migrate_backlog_v2.py --phase iam --apply
C:\data\envs\des\python.exe scripts\audit\backlog_phase_a_acceptance.py --verify iam --baseline-file <file> --migration-rc %ERRORLEVEL%
```
The first line of each pair is the dry run: it changes nothing. Capture `%ERRORLEVEL%` right after the `--apply` line, before running anything else.
Right after each `--apply`, confirm on the production connection that it landed there (section 4, first block). Expected after `relations`: gate mode still ENFORCE, gate rows unchanged, `sp_set_gate` has ONE form, five arguments. Expected after `iam`: three roles, tables `actor_login_event` and `binding_window_event`, the binding audit and window views, the routines.
Then bind the logins with `sp_bind_login(..., '<declaring human identity>')` (the last argument is mandatory) and, if the default window (Monday to Friday 07:00-19:00, server time) is not yours, set it with `sp_set_binding_window`; both are in `BACKLOG_IAM_ROLLOUT.md`.

**Zero-input verification (no exit code needed).** After both phases, run, on any machine that has the production backlog connection in `.env`:
`C:\data\envs\des\python.exe scripts\audit\backlog_verify_deployment.py`
It reads the schema itself and judges it with the criteria of `--verify relations` and `--verify iam` (their "migration exit code" criterion becomes "not needed"), checks that the connection is `127.0.0.1:5436/gestion`, that the gate is in ENFORCE with its rows, and that the history equals the newest baseline in `out\audit`. It writes a record to `deployments\` (version-controlled) and exits 0 only when everything passes. Use `--expect-target`, `--expect-gate` and `--baseline-file` for anything other than production.

## 2. A phase fails in the middle: nothing to roll back
Output: `FAILED, rolled back (nothing committed)`, exit code 1. DDL, roles and grants are transactional in PostgreSQL. Rehearsed with the real tool: a session holding a lock made `--phase relations --apply` fail after the 5-second `lock_timeout`, and the catalog and the row counts were identical afterwards. Read the error, fix, run the same command again.

## 3. Rollback levels (pick the smallest one that fixes the problem)
| Level | When | Command | State afterwards |
|---|---|---|---|
| L1 | `iam` is wrong or unwanted, `relations` is fine | `...migrate_backlog_v2.py --phase iam --down` (dry run), then `--phase iam --down --apply` | ONE transaction. Roles `backlog_reader/logger/incident`, the views (`v_actor_login`, `v_binding_audit`, `v_binding_window`) and the routines (`sp_append_log`, `sp_bind_login`, `sp_set_binding_window`, `sp_report_incident`, `fn_off_hours`) gone. `fn_actor_bound` is a stub that allows everything: no binding is enforced. **KEPT, with every row: `actor_login_event` (the binding history) and `binding_window_event` (the window history), still append-only**; the script checks that both have exactly the rows it found and otherwise raises `rollback aborted` and undoes everything. Applying `--phase iam` again resumes with the history and enforcement comes back with it. Tickets, logs and the gate are untouched. |
| L2 | the `relations` upgrade (the executive override of FND-0350 and the hook in A-1) is wrong; do L1 first if `iam` is applied | `...migrate_backlog_v2.py --phase relations --down` (dry run), then `--phase relations --down --apply` | ONE transaction. Phase B is back at the definitions of master `0979d80`: four-argument `sp_set_gate`, `v_gate_audit_window` without `last_override_ts`, no `v_gate_override_event`. **The gate never leaves its mode**: the script reads the mode first, never touches the gate engine (`fn_gate_mode`, `fn_gate_blockers`, the holds, the review flags, the cycle trigger) and ends by checking that the mode and every row of `review_flag`, `hold_event`, `gate_switch`, `gate_audit_event` are what it found; if not, it raises `rollback aborted: ...` and the whole rollback is undone. Rehearsed with a second session sampling the mode every 20 ms during the rollback: only ENFORCE. Inert leftovers: function `fn_actor_bound` (stub), the hook inside `fn_actor_authorized` and the column `gate_switch.override` (false on every row). |
| L3 | the gate must be switched off or to AUDIT | not a rollback: `SELECT backlog.sp_set_gate('DELIVERY', 'OFF', '<human identity>', '<reason>')` on the owner connection | A governed, recorded decision (a row in `gate_switch`), reversible by the next row. |
| L4 | last resort | restore schema `backlog` of the dump taken in section 0 (`pg_restore --schema=backlog --clean --if-exists`) | Back to the minute of the dump; anything written since is lost. Not rehearsed. |

`--phase relations --down` refuses (`nothing to roll back`) when Phase B is not applied. There is deliberately no command that drops the Phase B tables or the binding and window tables: removing that history is a decision of the owner, made by hand after exporting them.

Before L1: put the generic writer string back into `.env` (`FONDOS_BACKLOG_PG_DSN`) and remove the `..._LOGGER` / `..._INCIDENT` strings: they point at roles that will no longer exist.
After L1 or L2, revert the code in the reverse order of the merges (`git revert -m 1 <merge>` for iam, then live-guard, gate-override, actor-env) so the client matches the database. The readers tolerate the previous window view (`--verify relations` still runs); until the code is reverted `append_log` waits and `report_incident` falls back to the old flow.

## 4. Checks after any level
```
SELECT current_database(), inet_server_port();                             -- gestion; the production container (the port seen from your side is 5436)
SELECT backlog.fn_gate_mode('DELIVERY');                                   -- ENFORCE after L1 and L2 (unless you ran L3)
SELECT count(*) FROM backlog.gate_switch;                                  -- same as section 0, always
SELECT count(*) FROM backlog.hold_event;                                   -- same as section 0, always
SELECT to_regprocedure('backlog.sp_set_gate(text,text,text,text)');        -- not null after L2
SELECT to_regprocedure('backlog.sp_set_gate(text,text,text,text,boolean)');-- null after L2, not null with the stack applied
SELECT rolname FROM pg_roles WHERE rolname IN ('backlog_reader','backlog_logger','backlog_incident');   -- empty after L1
```
and take the fingerprint again with `C:\data\envs\des\python.exe scripts\audit\backlog_phase_a_acceptance.py --baseline` and compare its `logs`, `logs_md5`, `events` and `events_md5` with the file of section 0: they are equal unless someone wrote a ticket in between (no phase or rollback above touches those tables).
