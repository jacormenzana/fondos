# Backlog v3.1, phase NOW: remove the administrative tasks

## Context
Owner directive (2026-11-01): the administrative load is the problem, and recent rounds added to it. A human validating transitions or deliveries (inbox, adjudication queues, releasing holds, a DDL click or console run, reviewing the autopilot, a forced reopen) compensates for a state machine whose events were not fully designed. **This phase does one thing: every transition gets an automatic trigger and every recurring human touchpoint is removed.** Quality and learning layers are explicitly a LATER phase (listed at the end), not mixed into this one.

**Evidence (live backlog, read-only, 2026-11-01).** Reopening is modelled as misuse: `sp_transition` (db/pg_backlog/30_transitions.sql:313) needs `p_force` plus a 40-character reason, marks it forced, and `V-FORCED` counts it as a violation. Holds are releasable only by a human. Human queues added since 10-26: collision-pair adjudication (about 3 a day), hold release, link confirmation, the first-20 SHADOW review, the W5 inbox, the DDL click. DDL still needs a console or a click. Two transitions have no automatic trigger at all (BLOCKED -> IN_PROGRESS, DEFERRED -> TODO).

**Owner decisions:** close on delivery, reopening is the rejection; remove the human queues from the critical path; apply pending DDL automatically.

## Principles for this phase
1. Each transition has exactly one automatic trigger event. A person appears only as an event (new ticket, priority, reopen), never as an approver.
2. Reopening is safe in every direction (it adds work, hides nothing), so any actor may record it; only closing needs evidence.
3. Reopens keep a cause field now, so the later learning phase has data; nothing analyses it yet.
4. Nothing new is added that a person would have to look at. A failed automatic action falls back to a human only as the exception.

## Work now (each one PR; the DDL of N1-N4 ships as ONE phase `events`)
**Step 0.** Merge the owner-deploy PR. The `events` DDL is the LAST one applied through the existing click window; from N4 on it applies by itself.

**N1. Reopen as a normal event.**
- `backlog.reopen_event` (append-only: ap_id, actor, cause, reason, prior closure fields, event_ts) and `backlog.sp_reopen(ap, cause, reason)`, executable by agents, the autopilot and humans, no `p_force`, never counted as a violation. Causes (kept, not analysed yet): REGRESSION, INCOMPLETE_FIX, COVERAGE_GAP, WRONG_ROOT_CAUSE, DOWNSTREAM_EFFECT, ENVIRONMENT, SCOPE_CHANGE, OTHER. A reopen can be rescinded by its own actor within 1 h.
- `sp_transition`: CLOSED -> OPEN/TODO/IN_PROGRESS stops needing `p_force` and is excluded from `V-FORCED`; `sp_agent_transition` stops refusing it and routes to `sp_reopen`; client `backlog_client.reopen()`. The 4 known historic reopens (FND-0177, 0157, 0034, 0193) are backfilled with their cause.
- A commit with `Refs:`/`Closes:` that changes code or tests on a CLOSED ticket reopens it automatically (cause INCOMPLETE_FIX, replacing the `V-WORK-ON-CLOSED` violation row). This is the only automatic reopen in this phase.
- Reuse: the `sp_agent_transition` / `sp_record_link` patterns (97_autonomy.sql), the engine in `shared/backlog_autopilot.py` (one REOPEN action), `backlog_sync.py` commit facts.

**N2. The two missing automatic transitions.**
- Autopilot rules: BLOCKED -> IN_PROGRESS when the ticket it is blocked on CLOSES; DEFERRED -> TODO when its `revisit_by` date passes or its revisit event occurs. TODO is documented as optional. Pure rules in the existing engine, tested edge by edge.
- NORMAS_BACKLOG section 2 becomes the event table (edge, trigger, effect). A test enumerates the edges and fails if one needs a human approval.

**N3. Human queues out of the critical path.**
- Collision gate stays informational (AUDIT) for good: BLOCK and the pre-push block are retired. Agents record a pair's decision with stated evidence (`sp_adjudicate_overlap` gains an agent path); a person's earlier decision is still never overwritten.
- Holds raised by system codes auto-release when their cause clears (a function the autopilot runs); agents may release them. Human-raised holds stay human.
- The W5 inbox is dropped (it was never built). A weekly read-only digest is the only artefact, and it asks nothing.
- The autopilot's SHADOW -> ON switch stops needing a review: it is an event (`autopilot_mode_event`, like `gate_state`) that promotes by itself after 20 SHADOW decisions with none contradicted by a later outcome, and demotes by itself if an autopilot closure is reopened twice in 30 days. (The richer per-risk-class evidence is a later phase.)

**N4. Automatic DDL deploy.**
- `scripts/ops/owner_deploy.py` gets an AUTO mode (default): when `master` brings DDL it applies each pending phase without a window, using the gates that already exist (the hermetic migration and rollback tests that CI required for the merge, a restore point, the transactional phase, then `backlog_drift.py` must say `no drift`). On any failure: automatic `--down` of what it applied, an incident ticket through `report_incident`, and the existing click window as the fallback for that apply. Success: a toast and a receipt. No approval.
- The DEPLOY fact: scoped login `backlog_facts` (DSN in the owner vault) + `sp_record_fact` (kind DEPLOY only, actor `system:deploy`); `sp_record_ops_event` refuses DEPLOY from any other login so no agent can forge it. The watcher records it after a verified apply, and tickets whose merge changes `db/` then close by themselves (no manual closing).
- `revoke` (A-2) is applied by this path when its own gate holds (50 non-system transitions, 0 V-00; the migrate tool already refuses otherwise). `iam` stays out.
- Switch `BACKLOG_AUTO_DEPLOY_MODE` (AUTO | ASK) in `shared/config.py`; ASK is the window as built.

**N5. Documentation and consistency (small, part of each PR).**
- PROPUESTA decision log round 13 records, one row each, the rules superseded: DDL applied only by the owner, owner adjudication through a separate role (round 4), links decided by a person, delivery closure by `close_release.py`, reopening as a forced exception. AGENTS.md and NORMAS_BACKLOG (sections 2, 3, 4.x, 9) change in the same PR as the behaviour; the vault and deploy sections (9.2, 9.3) are updated.

## Human touchpoints: before and after
| Touchpoint | Now | After this phase |
|---|---|---|
| Apply DDL | console, then a click | automatic; notification |
| Close delivery tickets (including db/) | owner (`close_release`) | autopilot |
| Adjudicate collision pairs (about 3 a day) | owner | agents; informational gate |
| Release holds | human only | automatic when the cause clears |
| Confirm links | person | agents with evidence (built in W2) |
| Review the first 20 SHADOW decisions | owner | automatic promotion / demotion |
| Move BLOCKED / DEFERRED tickets | manual | automatic |
| Reopen | `p_force` + console, a violation | an event any actor records |
| Priorities, new tickets, scope, accepting a risk | owner | owner (genuine decisions, kept) |
| One-time setup (vault `set`, `init`, `install-startup`) | owner, once | the same, once |

## Deliberately LATER (not in this phase, so no new layer now)
The learning loop on reopens (analysis view, automatic corrective tickets, KPIs); the trust ledger by risk class and the qualitative promotion rule; production verification of critical tickets after the next real cycle (and the CYCLE fact); acceptance probes at opening and the probe-quality rule; the fail-before / pass-after test check and the PR test-touched check; the DDL lint and the staging-rehearsal gate before an automatic apply; the touchpoint baseline report; automatic reopen on a failing probe (regression); the self-growing CORE set. N1 keeps the data they need (reopen cause, closure facts), so none requires redoing this phase.

## Risks and limits, stated plainly
- **Automatic DDL with only the existing gates is the one place a wrong action touches production data.** Mitigations: CI hermetic tests, a restore point, the transactional phase, automatic verification and rollback, the click-window fallback, the `ASK` switch. Not mitigated until the later phase: a semantically wrong but valid additive change that the tests do not catch, and no rehearsal on a production snapshot. This is a risk you are accepting by deferring those gates.
- Closing on delivery without production verification is today's practice; reopen is the feedback. The later phase is what reduces reopens; this phase only makes them cheap and visible.
- Agents may reopen any ticket (the safe direction): a spurious reopen costs work; it is visible with its actor and cause.
- Same-user caveat unchanged: the vault and the watcher are guards, not boundaries.
- Self-evaluation for this scope (0-7): efficiency 6 (it removes the recurring touchpoints and builds little new), consistency 5 (the superseded rules are written down in N5, but the later phase will revisit them), reliability 4 (fewer new controls on purpose; the weak points are listed above and in the later list).

## Verification
- Pure: the edge-enumeration test (no edge needs a human approval); the new engine rules (REOPEN, UNBLOCK, UNDEFER) edge by edge; the mode-event promotion and demotion rule.
- Hermetic Postgres (`python scripts/ops/run_pg_tests.py --image postgres:17 -- <files>`): `sp_reopen` for each actor kind, no `V-FORCED` for a reopen, rescinding within 1 h, auto-release of holds, `sp_record_fact` accepted only from `backlog_facts`, DEPLOY refused through `sp_record_ops_event`, and the rollback keeping history.
- Deploy path: with fakes (like `tests/test_owner_deploy.py`) AUTO applies in order, rolls back on a failing phase and opens the incident; once for real on a staging container (the FND-0351 harness) with a deliberately failing phase.
- End to end after applying the `events` phase: a test branch with a `Closes:` commit and a db/ change merges, the DDL applies by itself, the DEPLOY fact is recorded, the autopilot closes the ticket; a later `Refs:` code commit on it reopens it with cause INCOMPLETE_FIX; `backlog_drift.py` prints `no drift`, with no human action.
- Success measure for the week after: no console command, no click, and no queue item for adjudication, holds, links, SHADOW review or closure; owner events are limited to new tickets, priorities and reopens.
