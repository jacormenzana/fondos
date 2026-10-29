# Backlog v2 IAM: follow-ups (FND-0354)

Written for: the owner, who decides what enters the next cycle.

## S1. Binding audit: the declared human actor in every binding. IMPLEMENTED in the deployment payload (owner order 2026-10-29)
Not deferred: the owner ordered it into this deployment. What was built (`50_iam.sql`, hermetic tests, rehearsed with the real tools):
1. `sp_bind_login` takes a mandatory seventh argument `p_declared_by`, a canonical `human:*` identity checked with `fn_actor_authorized` (once bindings exist a bound login can declare only its own identity). The old six-argument form is dropped.
2. `actor_login_event.declared_by`, written by the routine only. A table created by an earlier version is upgraded by re-applying the step: old rows keep NULL (append-only) and are flagged `LEGACY_NO_DECLARER`; the constraint is NOT VALID, so only new rows must name a declarer.
3. `v_binding_audit` flags `SELF_BINDING`, `DECLARER_NOT_BOUND_TO_SET_BY`, `LEGACY_NO_DECLARER`, `OFF_HOURS` and `RECENT`.
4. `OFF_HOURS`: a binding written outside the operational window. The default is restrictive and hardcoded: Monday to Friday, 07:00 to 19:00 (end excluded) in the server's time zone. The owner changes it with `sp_set_binding_window` (same rule as `sp_bind_login`: owner only, a declared human identity, a valid time zone and interval); every change is an append-only row and the latest wins (`v_binding_window`).
5. The pre-flight prints an `[ATENCION]` line and a CAUTION for every binding of the last 24 hours; `--verify iam` checks that the column, the constraint, the audit view and the window exist; `--verify retire` requires every active binding to name its declarer.
Stated limit: a superuser connection can declare any human identity, because the database cannot know who sits behind it. The audit makes this visible (`DECLARER_NOT_BOUND_TO_SET_BY` is always raised for it); it does not prevent it.

## S2. Granular non-human identities
**Gap.** Agent, system and incident logins all carry `person = 'non-human'`, so no metric can tell an autonomous agent from a CI pipeline or the incident webhook.

**Specification.**
1. Replace the literal by a subject drawn from an enumerated list, enforced by a CHECK exactly as the human persons are: proposed `system:pipeline` (P1/P2/P3 jobs), `system:telemetry` (incident capture, `system:autodetect`), `system:release` (`close_release.py`), `system:ci` (signed CI/CD deployments), `system:sync` (commit and gate automation), `agent:session` (an interactive agent session). The owner confirms the list; today's `system:*` actors map onto it.
2. A second CHECK ties the bound identity to its subject, as for humans (`system:autodetect` only under `system:telemetry`, and so on); the subject is a property of the login, not of the identity string.
3. The catalog and the pre-flight report bound logins and transitions per subject; the A-2 pool still counts only HUMAN persons.
4. Acceptance: a login cannot be bound under a subject that does not own its identity; adding a subject is a reviewed migration, never free text.

## S3. Rollback of the IAM step keeps its history (recommendation, not yet ordered)
`--phase iam --down` drops `actor_login_event`, `binding_window_event` and their views, as the first Phase B rollback dropped its tables. Apply the same principle: drop the roles, routines and views, keep the tables and their rows, so a later `--phase iam` resumes with the history intact. Same static test as for Phase B. Needs the owner's decision before it is written.
