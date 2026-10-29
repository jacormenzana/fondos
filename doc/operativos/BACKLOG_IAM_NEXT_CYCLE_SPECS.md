# Backlog v2 IAM: specifications for the next cycle (FND-0354 follow-ups)

Written for: the owner, who decides what enters the next cycle. Nothing here is implemented; the ban on new feature code until the stack is merged and verified on staging stands (owner, 2026-10-29).

## S1. Binding audit: the declared human actor in every binding
**Gap.** `backlog.actor_login_event` already stores the bound identity (`actor`), the person, the reason and `set_by`, which is `session_user`: the database LOGIN that ran `sp_bind_login`, not the canonical identity of the person behind it. After the logins `human:j.cormenzana_data` and `human:j.cormenzana_owner` exist, "who bound whom" is readable only by translating a login name.

**Specification.**
1. `sp_bind_login` takes a mandatory `p_declared_by` (a canonical `human:*` identity). It is refused unless `fn_actor_authorized(p_declared_by)` is true from the calling connection (so it must be a human identity bound to the calling login, once bindings exist) and the caller is the owner role (as today).
2. New column `actor_login_event.declared_by NOT NULL` (CHECK canonical `human:*`), written by the routine only; the table stays append-only. Rows written before the change keep `declared_by = 'human:j.cormenzana_owner'` by the one-time migration, which is the only person who could bind.
3. View `v_binding_audit(event_id, login_name, bound_actor, person, declared_by, set_by, reason, event_ts, flags)` with the flags `SELF_BINDING` (declared_by equals the bound actor), `DECLARER_NOT_BOUND_TO_SET_BY` (the declared identity is not the one bound to the login that ran the call), `OFF_HOURS` (outside the owner's configured window) and `RECENT` (last 24 h).
4. Alert: the pre-flight prints an `[ATENCION]` line for any binding in the last 24 h or with a flag, and exits non-zero in `--strict`. No trigger: a trigger cannot know more than the routine, and the routine is the only writer (the table has no direct INSERT privilege for anyone but `backlog_owner`).
5. Acceptance: hermetic tests for each flag; a binding with a missing or unauthorized `p_declared_by` is rejected; the history before the change is unchanged; `--verify` gains a criterion "every active binding has a declared_by".

## S2. Granular non-human identities
**Gap.** Agent, system and incident logins all carry `person = 'non-human'`, so no metric can tell an autonomous agent from a CI pipeline or the incident webhook.

**Specification.**
1. Replace the literal by a subject drawn from an enumerated list, enforced by a CHECK exactly as the human persons are: proposed `system:pipeline` (P1/P2/P3 jobs), `system:telemetry` (incident capture, `system:autodetect`), `system:release` (`close_release.py`), `system:ci` (signed CI/CD deployments), `system:sync` (commit and gate automation), `agent:session` (an interactive agent session). The owner confirms the list; today's `system:*` actors map onto it.
2. A second CHECK ties the bound identity to its subject, as for humans (`system:autodetect` only under `system:telemetry`, and so on); the subject is a property of the login, not of the identity string.
3. The catalog and the pre-flight report bound logins and transitions per subject; the A-2 pool still counts only HUMAN persons.
4. Acceptance: a login cannot be bound under a subject that does not own its identity; adding a subject is a reviewed migration, never free text.

## S3. Rollback of the IAM step keeps the binding history (recommendation, not yet ordered)
`--phase iam --down` currently drops `actor_login_event` and its view, as the first Phase B rollback dropped its tables. Apply the same principle: drop the roles, routines and the view, keep the table and its rows, so a later `--phase iam` resumes with the history intact. Same static test as for Phase B (no `DROP TABLE`, `DELETE`, `TRUNCATE`, `ALTER TABLE`, `UPDATE`). Needs the owner's decision before it is written.
