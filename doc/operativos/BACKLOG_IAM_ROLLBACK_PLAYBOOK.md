# Backlog v2 IAM rollout: rollback playbook (FND-0354, FND-0333, FND-0350)

Written for: the owner, at his own terminal (cmd.exe), applying `--phase relations` and `--phase iam` to the live `gestion` database.
Every state below was rehearsed on PostgreSQL 17 against a copy of production as it is today (master `0979d80`: step A-1 plus Phase B, the gate in ENFORCE, a hold, and the `system:owner-waiver` row). Nothing here touches tickets, logs, status events, wait events or link events.

Conventions: repo root `C:\desarrollo\fondos` with the stack AND the fix `fix/relations-down-keeps-history` checked out; interpreter `C:\data\envs\des\python.exe`; the owner DSN is typed for the session (`set FONDOS_PG_DSN_OWNER=...`), never stored. Tool exit codes: 0 done, 1 failed and rolled back, 2 usage or connection.

## 0. Before applying
1. **Check the safe down script is the one you will run.** This must print NOTHING (the old script dropped the Phase B tables):
   `findstr /I /C:"DROP TABLE" /C:"DELETE" db\pg_backlog\down\40_relations_v2_down.sql | findstr /V /B /C:"--"`
   If it prints a line, do not run any `--down` until the fix is merged. (A test fails the build if the script ever gains a `DROP TABLE`, `DELETE`, `TRUNCATE`, `ALTER TABLE` or `UPDATE`.)
2. Restore point (the live `gestion` database is included): `wsl -d Ubuntu -e bash /mnt/c/desarrollo/fondos/scripts/ops/backup_live_pg.sh`
3. Baseline of the history: `C:\data\envs\des\python.exe scripts\audit\backlog_phase_a_acceptance.py --baseline` (keep the file name it prints).
4. Today's gate state, to compare after: `SELECT backlog.fn_gate_mode('DELIVERY'); SELECT count(*) FROM backlog.gate_switch; SELECT count(*) FROM backlog.hold_event;`

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
Expected after `relations`: gate mode still ENFORCE, gate rows unchanged, `sp_set_gate` has ONE form, five arguments. Expected after `iam`: three roles, table `actor_login_event`, four routines.

## 2. A phase fails in the middle: nothing to roll back
Output: `FAILED, rolled back (nothing committed)`, exit code 1. DDL, roles and grants are transactional in PostgreSQL: the rehearsal injected an error at the end of each block and the catalog was identical afterwards (functions, views, columns, tables, roles, row counts). Read the error, fix, run the same command again. The common cause is `lock_timeout` (5 s) because a session holds a lock: retry.

## 3. Rollback levels (pick the smallest one that fixes the problem)
Every level uses the native tool. No custom script is run in production.

| Level | When | Commands | State afterwards |
|---|---|---|---|
| L1 | `iam` is wrong or unwanted, `relations` is fine | `...migrate_backlog_v2.py --phase iam --down` (dry run), then `--phase iam --down --apply` | Roles `backlog_reader/logger/incident`, table `actor_login_event`, view `v_actor_login`, routines `sp_append_log`, `sp_bind_login`, `sp_report_incident` gone. `fn_actor_bound` is a stub that allows everything: no binding is enforced. LOST: the binding history (see the next-cycle spec). Tickets, logs and the gate are untouched. |
| L2 | the `relations` upgrade (gate override, hook in A-1) is wrong; do L1 first if `iam` is applied | (a) `...migrate_backlog_v2.py --phase relations --down --apply` from the stack; (b) from the previous code (master `0979d80`) `...migrate_backlog_v2.py --phase relations --apply` | After (a): Phase B disabled, EVERY row kept (`gate_switch`, `hold_event`, `review_flag`, `gate_audit_event` identical in count and content), `fn_gate_mode` answers OFF, `sp_transition` works, the gate does not bind. After (b): the definitions of `0979d80` are back (four-argument `sp_set_gate`, no `last_override_ts`, no binding hook) and the gate is ENFORCE again, because the last gate row says ENFORCE. Inert leftovers: function `fn_actor_bound` (stub) and column `gate_switch.override` (false on every row). |
| L3 | Phase B must be switched off for a while | (a) of L2 only | As after L2 (a). To bring it back: `--phase relations --apply`. |
| L4 | last resort | restore schema `backlog` of the dump taken in section 0 (`pg_restore --schema=backlog --clean --if-exists`) | Back to the minute of the dump; anything written since is lost. Not rehearsed. |

While the gate is disabled (between L2 (a) and (b), or in L3) deliveries are NOT gated: keep that window short and tell whoever is moving tickets. L2 (b) was rehearsed as the SQL the previous tool runs, not as a run of the previous tool itself.
There is deliberately no command that drops the Phase B tables: removing that history is a decision of the owner, made by hand after exporting them.

Before L1: put the generic writer string back into `.env` (`FONDOS_BACKLOG_PG_DSN`) and remove the `..._LOGGER` / `..._INCIDENT` strings: they point at roles that will no longer exist.
After L1 or L2, revert the code in the reverse order of the merges (`git revert -m 1 <merge>` for iam, then live-guard, gate-override, actor-env) so the client matches the database; until then the client's `append_log` waits and `report_incident` falls back to the old flow.

## 4. Checks after any level
```
SELECT backlog.fn_gate_mode('DELIVERY');                                   -- ENFORCE after L1 and after L2 (b); OFF after L2 (a) and L3
SELECT count(*) FROM backlog.gate_switch;                                  -- same as section 0, always
SELECT count(*) FROM backlog.hold_event;                                   -- same as section 0, always
SELECT to_regprocedure('backlog.sp_set_gate(text,text,text,text)');        -- not null after L2 (b)
SELECT to_regprocedure('backlog.sp_set_gate(text,text,text,text,boolean)');-- null after L2 (b), not null with the stack applied
SELECT rolname FROM pg_roles WHERE rolname IN ('backlog_reader','backlog_logger','backlog_incident');   -- empty after L1
```
and take the fingerprint again with `C:\data\envs\des\python.exe scripts\audit\backlog_phase_a_acceptance.py --baseline` and compare its `logs`, `logs_md5`, `events` and `events_md5` with the file of section 0: they are equal unless someone wrote a ticket in between (no phase or rollback above touches those tables).
