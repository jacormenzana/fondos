# PENDING_FND_LOGS

Backlog log entries that could not be written to `gestion.backlog` yet, kept here so the decisions are version-controlled (owner order, 2026-10-29).
They are ingested with `backlog.sp_append_log` (login `backlog_logger`) once `--phase iam` is applied; after ingestion this file is removed in the same change that records the log ids.

Format, one entry per block, in this order (a script can parse it without guessing):
- a heading `### <ticket> / <nn> - <title>`;
- the lines `actor:` (who writes the log), `date:` (when the decision or fact happened) and `source:` (where it comes from);
- a blank line, then the log text, up to the next heading. Each text is at most 20000 characters.

All entries are written by `agent:claude-sonnet-5-5` on behalf of the decision-makers named in the text. Figures are as last read in the session of 2026-10-29 and are dated.

## FND-0354

### FND-0354 / 01 - Operators, non-human writers and retirement date
actor: agent:claude-sonnet-5-5
date: 2026-10-29
source: owner review of 2026-10-29

Owner decisions on the identity rollout:
- Operators: DevOps Lead (`human:a.gomez_devops`, "Andres Gomez (DevOps)"), Data Engineering Lead (`human:j.cormenzana_data`, "Jose Antonio Cormenzana (Data)"), Owner (`human:j.cormenzana_owner`, "Jose Antonio Cormenzana (Owner)"). The alias `human:jacor` stays for the history.
- Non-human writers (incident capture, release automation, agent sessions, read-only tools) receive distinct client certificates in the same rollout. Agent identities do not count toward the human minimum.
- The shared writer login is dropped entirely. The group role keeps the name `backlog_writer`; `fondos_app` is not touched.
- Target retirement date: Friday 2026-11-06.

### FND-0354 / 02 - Mutual TLS: what the application does and does not do
actor: agent:claude-sonnet-5-5
date: 2026-10-29
source: owner review of 2026-10-29 (mitigation 3)

The hermetic mTLS test is struck from the ETA. The application only accepts the standard libpq parameters in a connection string (`sslmode=verify-full`, `sslcert`, `sslkey`, `sslrootcert`) and passes them to the driver untouched (tested). Certificate generation and distribution, `pg_hba.conf` and the container configuration belong to DevOps. The live server was `ssl=off` when last read.

### FND-0354 / 03 - Agent bookkeeping, incident capture, read-only agent login
actor: agent:claude-sonnet-5-5
date: 2026-10-29
source: owner review of 2026-10-29 (decisions 1 to 5)

Owner decisions:
- Agent bookkeeping: a restricted path limited to appending logs to EXISTING tickets. An agent may not create tickets, transition states or modify links on the live database.
- Incident reporting: `report_incident` is an explicitly allow-listed automatic, non-interactive write path, with database permissions limited to insert-only on the incident tables.
- Read-only agent login: `backlog_reader` is approved; the generic writer connection string leaves `.env`; the owner provisions the roles.
- Aliases and names: as in entry 01; the new writer group role is `backlog_writer`.

### FND-0354 / 04 - Implementation delivered (branch feat/backlog-v2-iam)
actor: agent:claude-sonnet-5-5
date: 2026-10-29
source: commit 5fafc72 on branch feat/backlog-v2-iam (stacked on feat/backlog-v2-live-guard)

Built (not applied, not merged): `db/pg_backlog/50_iam.sql` and its down script, `migrate_backlog_v2.py --phase iam`.
- Roles `backlog_reader` (SELECT only), `backlog_logger` (reader plus `sp_append_log`: a note on an existing ticket, agent/system identities only) and `backlog_incident` (reader plus `sp_report_incident`, insert-only), created without a password.
- `backlog.actor_login_event` (append-only), `v_actor_login`, `sp_bind_login` (superuser or `backlog_human` only) and `fn_actor_bound`, called by `fn_actor_authorized`: a bound login writes only as its identity; a human identity is accepted only from a login bound to it; nothing is enforced while no binding exists. The `person` column makes several logins of one person count once.
- Client: allow-list `NON_INTERACTIVE_OPERATIONS = (agent_log, incident)` in the connection factory; own connection-string variables `FONDOS_BACKLOG_PG_DSN_LOGGER` and `FONDOS_BACKLOG_PG_DSN_INCIDENT`; the test sandbox hides them.
- Pre-flight `IAM:` line; acceptance `--verify iam` and `--verify retire`.
- Tests: full backlog, EXPLAIN-sweep, pre-flight and dialect suites passed on PG17 (exit code 0); 134 tests re-run on a git archive of the commit; the sentinel check is OK.
- Runbook: `doc/operativos/BACKLOG_IAM_ROLLOUT.md`.

### FND-0354 / 05 - A-2 pool: three distinct human persons
actor: agent:claude-sonnet-5-5
date: 2026-10-29
source: owner review of 2026-10-29 (mitigation 1)

Owner decision: three logins across two people is not enough. A third distinct human operator joins the required pool: `human:m.ruiz_qa` (Maria Ruiz, QA Lead; actor name "Maria Ruiz (QA)"). `--verify retire` must validate transitions from three distinct bound human identities, not just three logins. Implemented as: at least three distinct bound human PERSONS (`human:a.gomez`, `human:j.cormenzana`, `human:m.ruiz`; the two Cormenzana logins are one person) and transitions actually made by that many, counted only after each login was bound to its identity. Agents never count. The `person` value is not free text: a database CHECK accepts exactly `human:a.gomez`, `human:j.cormenzana` and `human:m.ruiz` for a human login (and `non-human` for the others), and the identity bound must belong to that person, so a typo cannot make one human count as two (owner decision 2026-10-29).

### FND-0354 / 06 - Process decisions: no stacking, merge order, SLA
actor: agent:claude-sonnet-5-5
date: 2026-10-29
source: owner review of 2026-10-29

Owner decisions:
- No net-new feature code until the four-branch stack (actor-env, gate-override, live-guard, iam) is merged into master and verified on the live staging environment.
- The owner opens the pull requests and merges them in that order; the local tree is not synced until the owner gives the clear.
- The runbook sequence (steps 1 to 5) is approved. The owner applies `--phase relations`, then `--phase iam`, with an ephemeral owner connection string immediately after the merges.
- DevOps TLS delivery by 2026-11-03 is an executive-backed SLA owned by the owner.

### FND-0354 / 07 - Binding audit implemented in this deployment
actor: agent:claude-sonnet-5-5
date: 2026-10-29
source: owner review of 2026-10-29

Owner decision: the identity traceability gap is closed in this deployment, not deferred. Implemented: `sp_bind_login` takes a mandatory `p_declared_by` (a canonical `human:*` identity checked with `fn_actor_authorized`: once bindings exist a bound login can declare only its own identity), stored in the append-only `actor_login_event.declared_by`; the view `v_binding_audit` flags `SELF_BINDING`, `DECLARER_NOT_BOUND_TO_SET_BY`, `LEGACY_NO_DECLARER` and `RECENT`; the pre-flight prints an `[ATENCION]` line and a CAUTION for every binding of the last 24 hours; the view also flags `OFF_HOURS` against an operational window whose restrictive default (Monday to Friday 07:00-19:00, server time) the owner can change with `sp_set_binding_window`; `--verify iam` checks the audit exists and `--verify retire` requires every active binding to name its declarer. Stated limit: a superuser connection can declare any human identity; the audit makes it visible, it does not prevent it.

### FND-0354 / 08 - Native rollback of Phase B is atomic and keeps the gate
actor: agent:claude-sonnet-5-5
date: 2026-10-29
source: owner review of 2026-10-29

Owner orders: the native rollback must not drop the append-only history tables or delete gate rows, and it must be atomic and self-contained, with no window in which the gate is off. `--phase relations --down` now returns Phase B to the definitions of the previous release in one transaction: it removes the five-argument `sp_set_gate`, `v_gate_override_event` and the column `last_override_ts` of the window view, restores the four-argument setter and the view with their ownership and privileges, and never touches the gate engine (`fn_gate_mode`, `fn_gate_blockers`, the holds, the review flags, the cycle trigger). It reads the gate mode first and ends by checking that the mode and every row of `review_flag`, `hold_event`, `gate_switch` and `gate_audit_event` are what it found; otherwise it raises and the whole rollback is undone. A watcher session sampling the gate mode during a rollback by the real command-line tool saw only ENFORCE. A static test forbids `DROP TABLE`, `DELETE`, `TRUNCATE`, `ALTER TABLE` and `UPDATE` in the script. Switching the gate off is a governed decision of the owner (`sp_set_gate`), not part of a rollback. The rollback playbook is `doc/operativos/BACKLOG_IAM_ROLLBACK_PLAYBOOK.md`.

### FND-0354 / 09 - The IAM rollback keeps the binding and window history
actor: agent:claude-sonnet-5-5
date: 2026-10-29
source: owner review of 2026-10-29

Owner order: rollback scripts never drop append-only history tables. `--phase iam --down` now disables the IAM step (roles, routines and views removed, the hook back to the stub that allows everything) and keeps `actor_login_event` and `binding_window_event` with every row, still append-only; it checks the row counts at the end of its single transaction and undoes itself otherwise. Applying `--phase iam` again resumes with the history and enforcement. A static test forbids `DROP TABLE`, `DELETE`, `TRUNCATE`, `ALTER TABLE`, `DROP COLUMN` and `UPDATE` in the script.

## FND-0331

### FND-0331 / 01 - Hard fail for non-interactive live writes (mitigation 1)
actor: agent:claude-sonnet-5-5
date: 2026-10-29
source: owner review of 2026-10-29; commit 0a0968a on branch feat/backlog-v2-live-guard

Owner order: any non-interactive session that writes to the live backlog hard-fails with exit code 1, with no exception in the code. Implemented: `LiveWriteForbidden`; `close_release.py` (also its dry run), `backlog_step0.py --apply` and `backlog_links.py --apply-review --confirm` refuse before opening any connection; a person at a real console types `I CONFIRM THIS IS NOT A SYNTHETIC TEST`. Found and fixed on the way: on Windows `isatty()` is true for the NUL device (the stdin of a tool shell), so a console is detected with `GetConsoleMode`. A static test fails if a script calls a write helper without the gate. The two exemptions the owner later allowed are in FND-0354 / 03. The gate is a client guard, not a security boundary (limitation L3).

### FND-0331 / 02 - Owner connection string purged from the agent environment (mitigation 2)
actor: agent:claude-sonnet-5-5
date: 2026-10-29
source: owner review of 2026-10-29

Owner order: the owner superuser connection string is removed from the `.env` the agent session can read. Done and verified. The owner provides it as an ephemeral per-session variable when migrations or DDL are needed; it is never stored locally. Consequence: the session cannot run the dry runs of the scripts that need the owner connection string.

### FND-0331 / 03 - A-2 stays blocked; the pool is three distinct human persons
actor: agent:claude-sonnet-5-5
date: 2026-10-29
source: owner reviews of 2026-10-29

Step A-2 (revoke raw UPDATE) is not applied and FND-0331 stays open. Conditions: 50 real non-system transitions since the last V-00 (15 of 50 as last read in the session, V-00 total 0), and transitions made by three distinct bound human persons (see FND-0354 / 05). Agent identities do not count toward the human minimum. The acceptance check for the retirement is `backlog_phase_a_acceptance.py --verify retire`.

## FND-0351

### FND-0351 / 01 - Programmatic live-write guard and staging harness: state
actor: agent:claude-sonnet-5-5
date: 2026-10-29
source: owner reviews of 2026-10-29

The programmatic guard asked for in this ticket exists as the hard fail and the allow-list (FND-0331 / 01 and FND-0354 / 03, FND-0354 / 04). The staging harness (a container plus a snapshot, so that tests of the live-backlog tools never touch the live database) is NOT started; it remains the way to test anything that needs the real backlog. Under the owner's ban on net-new feature code until the stack is merged and verified on staging (FND-0354 / 06), it stays unstarted. Signed CI/CD deployments are the only non-interactive writes the owner has mentioned beyond the two allow-listed operations; no mechanism for them exists in the code.

### FND-0353 / 01 - Acceptance check 5 (external log sink) waived by the owner, conditionally
actor: agent:claude-sonnet-5-5
date: 2026-10-30
source: owner decision of 2026-10-30

The owner waived acceptance check 5 and the external-sink requirement of spec section 7 (decision D6) for the initial deployment. Interim telemetry: the local Security log of CAJADEPANDORA (audit rule on the key directory; events 4656, 4663, 4660, 4670). The external Windows Event Forwarding collector, the CA and certificates, and the immutable store are deferred to a post-deployment DevOps sprint; no date was given.
Stated consequences, not decided by the owner: (1) a holder of SYSTEM can clear or stop the local log and no remote copy exists, so tampering is detectable only on the machine; (2) the provisioning scripts do not raise the Security log to the 256 MB of spec section 7, because that step lives in provision_log_sink.ps1, which will not be run; unless the owner sets it (wevtutil sl Security /ms:268435456, elevated) the Windows default ring of about 20 MB with overwrite applies and key-access events can be overwritten; (3) the local alert task of section 7 (alerts\key_access.log) is not built yet.
provision_log_sink.ps1 (commit 9a8f1b00, blob 873ef363) and LOG_SINK_RUNBOOK.md (608f440) stay in the repository, unexecuted. Acceptance 5 is WAIVED, not passed, and must not be reported as passed.

### FND-0353 / 02 - Provisioning receipts of 2026-10-30 not corroborated; restart on a verified host
actor: agent:claude-sonnet-5-5
date: 2026-10-30
source: read-only checks in the agent session of 2026-10-30 and the owner's acknowledgement

The receipts the owner pasted as executed against runbook b63f5f9 did not match the machine (CAJADEPANDORA), read-only: neither fondos_ci_runner nor fondos_db_owner existed; C:\ProgramData\fondos did not exist (no MANIFEST.sha256, no key_staging, no boundary receipt in deployments/); the pasted manifest SHA-256 e3b0c44298fc1c14... is the SHA-256 of empty input; the pasted runtime count 1477 differs from the 1478 the real check computes on this environment. The owner acknowledged the receipts came from a disconnected environment and will re-run the runbook on a verified session. Nothing was recorded as evidence in deployments/. Same pattern as the earlier claims about the merge and the DDL, which origin and the production database did not show.

### FND-0353 / 03 - Specification-first pull-request pipeline built; nothing deployed
actor: agent:claude-sonnet-5-5
date: 2026-10-30
source: branch chore/ci-credential-spec-final, commits 7547668 and 608f440

At the owner's order the interface contract (OPEN_PR_INTERFACE_CONTRACT.md), the locked request and receipt schemas (pinned by SHA-256) and the acceptance tests were written before the code; then open_pr_schema.py (strict JSON, validator of the locked keyword subset), open_pr.py (runner) and open_pr_request.py (agent side). The runner's only third-party package is cryptography; jsonschema, PyJWT and requests are not used. Decision D8 added to the spec: before the inbox is opened the runner checks that the token GitHub issued carries exactly pull_requests write, checks read, metadata read, on the single repository (the permissions come from the token-creation response; GET /installation/repositories carries none). 162 tests pass (including the provisioning scripts' tests); 12 deliberate mutations of the code were all caught by the tests. Not built, by order or because they need the real machine: the runner's deploy script, config.json, the scheduled tasks fondos_open_pr and the alert task, acceptance 2, 4, 5 (waived) and 15.

### FND-0353 / 04 - Runbook amendment: interim log hardening; local alert task becomes critical path
actor: agent:claude-sonnet-5-5
date: 2026-10-30
source: owner decision of 2026-10-30

With the external log sink waived (note 01), the owner amended the provisioning runbook: he runs `wevtutil sl Security /ms:268435456` himself in the elevated session after the App installation (Step 4a), and reports `wevtutil gl Security` showing maxSize 268435456, so the local Security log holds 256 MB instead of the default ring of about 20 MB. Group Policy that sets a smaller size would override it; if the reported value differs the sequence stops. The owner also approved building the local alert task of spec section 7 (event-triggered, appends to alerts\key_access.log for access to the key by anyone but fondos_ci_runner) and made it critical-path work, to be built with the deploy script after the real provisioning receipts are verified on the machine. Design point to settle then: the alerts directory ACL has no SYSTEM entry, so the task identity needs an explicit, narrowly scoped grant.
