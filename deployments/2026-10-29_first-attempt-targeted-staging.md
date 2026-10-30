# Deployment record: first attempt of the Backlog v2 unified payload, 2026-10-29 (target error, nothing applied to production)

Written for: the repository owner and anyone who audits the Backlog v2 rollout. Companion of `2026-10-29_backlog-v2-unified-bridgehead.md` (kept on `chore/deployment-records`).

| Item | Value |
|---|---|
| What was attempted | `--phase relations --apply` then `--phase iam --apply`, followed by the login bindings, from the code of `8cd0ad7` |
| Where it went | an isolated staging replica, `127.0.0.1:5433`, database `des` (confirmed by the owner) |
| Production state afterwards | unchanged: `127.0.0.1:5436/gestion` |
| How it was found | a read-only check from the agent session against the production backlog connection, with the code of `8cd0ad7` |

## Evidence that production was unchanged (read at 2026-10-29 23:35 Madrid, server primary, not a replica)
- Gate mode ENFORCE, `gate_switch` 2 rows (max event 2): the state before the deployment.
- No column `gate_switch.override`; none of the IAM objects: no `actor_login_event`, `binding_window_event`, `v_binding_audit`, `sp_bind_login`, `sp_set_binding_window`, `sp_append_log`, `sp_report_incident`, `fn_off_hours`, `fn_actor_bound`; none of the roles `backlog_reader`, `backlog_logger`, `backlog_incident`.
- No login binding; the writing logins were still `backlog_writer` and `backlog_human`, unbound.
- Newest ticket log 1662 and newest status event 401, both of 2026-10-28; A-2 counter 15 of 50.

## Baseline taken for the next attempt (production, read-only, 2026-10-29T22:41:46Z UTC)
`logs` 1365 (max log id 1662, md5 `45fb6bd3177e9afc6eddf29e914bc34f`), `events` 298 (max event id 304, md5 `bffbba1d92d5f5ba5b3a698a366b8359`), `tickets` 309. The file is `out/audit/fnd0331_baseline_20261029T224146Z.json` on the machine that took it (git-ignored); the values above are the record.

## Consequences and corrections
- The staging replica has the payload; production does not. The deployment is pending.
- Git: pushing `master` was refused by branch protection (`GH006`, changes must be made through a pull request). The same SHA is on `deploy/unified-backlog-v2` and `deploy/master-override`; no pull request is open.
- The playbook now asks for a target check before every `--apply` (section 0, item 5): the first line printed by the tool must be `host=127.0.0.1 port=5436 dbname=gestion`.
