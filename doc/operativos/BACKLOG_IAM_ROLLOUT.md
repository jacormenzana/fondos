# Backlog v2 IAM rollout (FND-0354) - runbook for the owner and DevOps

Written for: the owner and the DevOps lead who provision the per-identity logins. The application side is code; this page is what a person does.

## What changes
| Login (database role) | Who | Can do | Connection string variable |
|---|---|---|---|
| `backlog_reader` | agent sessions | read the whole `backlog` schema | `FONDOS_BACKLOG_PG_DSN` (replaces the generic writer in `.env`) |
| `backlog_logger` | agent sessions | append a note to an EXISTING ticket (`sp_append_log`), nothing else | `FONDOS_BACKLOG_PG_DSN_LOGGER` |
| `backlog_incident` | pipelines | automatic incident capture (`sp_report_incident`), insert-only | `FONDOS_BACKLOG_PG_DSN_INCIDENT` |
| one login per operator | `human:a.gomez_devops`, `human:j.cormenzana_data`, `human:j.cormenzana_owner`, `human:m.ruiz_qa` (Maria Ruiz, QA lead) | open, move, link (as the bound human) | `FONDOS_BACKLOG_PG_DSN_HUMAN`, typed in the operator's own terminal, never in `.env` |

Several logins of one person count as one person (`person` column, a database CHECK: exactly `human:a.gomez`, `human:j.cormenzana` or `human:m.ruiz` for a human login, `non-human` for the others; the identity bound must be the person's own): the A-2 pool needs THREE distinct persons (owner decision 2026-10-29). An agent is never an operator. `human:jacor` stays an alias of the history.

## Order (the owner applies every DDL; nothing here is applied by a session)
1. Re-apply step A-1 with the hook and apply the IAM step in the owner's terminal (owner DSN typed for the session, never stored):
   `python scripts/ops/migrate_backlog_v2.py --phase relations --apply` then `--phase iam` (dry run first; `--down` rolls back). Baseline and verify: `scripts/audit/backlog_phase_a_acceptance.py --baseline` / `--verify iam`.
2. Passwords or certificates: the roles are created WITHOUT a password (like `backlog_human`). Set them in the owner's psql session. For the cryptographic binding, DevOps configures TLS on the server and
   `pg_hba.conf` (`hostssl gestion <role> <cidr> cert` or `scram-sha-256 clientcert=verify-full`) and issues one client certificate per login, including the non-human ones. The server is `ssl=off` today.
3. Create one login per operator, each a member of `backlog_human` (so it may be a human identity) and of `backlog_writer` (same privileges as today), then bind (owner session):
   `SELECT backlog.sp_bind_login('<login>', 'human:j.cormenzana_owner', 'human:j.cormenzana', 'HUMAN', true, '<reason>', '<declaring human identity>');` (agents and system logins: person `non-human`). The last argument is mandatory: the canonical `human:*` identity that declares the binding; once bindings exist a bound login can declare only its own identity. It is stored in the append-only event (`declared_by`) and `backlog.v_binding_audit` flags what needs attention (`SELF_BINDING` is expected only for the very first binding, `DECLARER_NOT_BOUND_TO_SET_BY` for every binding made from a superuser connection, `RECENT` for the last 24 hours, `OFF_HOURS` outside the operational window); the pre-flight prints an `[ATENCION]` line for each recent binding. The window defaults to Monday to Friday, 07:00 to 19:00 (end excluded) in the server's time zone; change it, as the owner, with `SELECT backlog.sp_set_binding_window('Europe/Madrid', ARRAY[1,2,3,4,5]::smallint[], '07:00', '19:00', '<reason>', '<declaring human identity>');` (every change is an append-only row, the latest wins; `backlog.v_binding_window` shows the one in force).
   Bind the old `backlog_human` login to `human:jacor` while it exists. Bind `backlog_logger` to `agent:%` and `backlog_incident` to `system:autodetect`.
4. Connection strings carry the standard libpq parameters, e.g. `postgresql://backlog_logger@127.0.0.1:5436/gestion?sslmode=verify-full&sslcert=...&sslkey=...&sslrootcert=...`; the client passes them untouched.
5. Put the three agent strings in `.env`; remove the generic writer string. Verify with the pre-flight (`IAM:` line).
6. Retire the shared writer: `ALTER ROLE <shared login> NOLOGIN` (then `DROP ROLE` when nothing uses it). `--verify retire` must pass (no unbound login can open or move a ticket; at least three distinct bound human PERSONS, and transitions actually made by that many).
   The role `backlog_writer` stays as the privilege group; `fondos_app` is not touched.

## Rollback
`python scripts/ops/migrate_backlog_v2.py --phase iam --down --apply` removes the roles, the routines and the binding history (the hook goes back to a stub that allows everything). Tickets, logs and events are not touched.
