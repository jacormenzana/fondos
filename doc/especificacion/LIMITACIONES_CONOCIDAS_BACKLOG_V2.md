# Backlog v2: known system limitations

Written for: the owner and anyone who reads the backlog's timestamps, metrics or controls and needs to know where they can mislead. Facts verified 2026-10-28.
Each entry says what the limitation is, what it affects, what mitigates it today, and what would remove it. Nothing here is a defect report: these are properties of the design.
Companion documents: `PROPUESTA_BACKLOG_V2.md` (design and decision log), `EVALUACION_LIMITACIONES_BACKLOG_V2.md` (the identity and traceability assessment), `doc/reglas/NORMAS_BACKLOG.md` (the rules).

## L1. `deployment_ts` and `closure_ts` are system time, not business time

- **What.** The routines stamp both with the moment the ticket is closed (`now()`); `sp_transition` has no parameter for an earlier date. A ticket closed after its deployment therefore reads as deployed on the day of the closing.
- **Case.** FND-0193: the study was deployed on 2026-10-19 (published and promoted that day; its sibling tickets of release `R2026.10-P3VALUE-02` carry 2026-10-19 22:50:11), but it was formally closed on 2026-10-28, so the routines stamped 2026-10-28 21:57:14.
- **Affects.** Anything that measures from `deployment_ts`: deployment lead time, release cadence, SLIs. `closure_ts` is the true moment of the closing and is **not** corrected; lead time from creation to closure stays honest.
- **Mitigation.** The one case so far, FND-0193, was corrected once with a routine (`sp_correct_deployment_ts`, status event 353, log 1584, value read from the sibling tickets of the same release); on the owner's order of 2026-10-28 that routine was **removed** from the schema, so no tool can alter a deployment time any more. The correction and its reason stay in the append-only history. A raw `UPDATE` is the only remaining way and is deliberately unattractive: `deployment_ts` is a governed column, so it is recorded as a V-00 (a direct write) and resets the counter of step A-2.
- **Rule.** When a ticket is closed after the deployment, say the true deployment date and its source in the closing log; the structured column keeps the closing time. A structured correction needs a new, explicit owner decision.
- **Would remove it.** An optional `deployed_at` parameter on the closing of `sp_transition`. Not done: it changes the core routine and needs its own decision.

## L2. The audit exit criterion counts only gated moves

- **What.** The delivery gate evaluates `READY_FOR_DEPLOY` and the `CODE_FIX` / `DATA_FIX` / `WORKAROUND` closures, so the audit window can only be filled by those moves. Work on earlier phases (`IN_PROGRESS`, `BLOCKED`) does not exercise the gate and does not count toward the five different tickets that `ENFORCE` needs.
- **Affects.** The time until `ENFORCE` can be authorized: it depends on delivery activity, not on elapsed time alone. In the last 28 days, 67 of 108 sliding 48-hour windows had delivery closures on five or more different tickets (median 8), but the last days were quiet (1 to 2 a day), so the window can take longer than 48 hours.
- **Mitigation.** The window is visible at any time (`backlog_client.gate_audit_window()`, and the acceptance tool prints it); the audit never refuses a move, so waiting costs no work.
- **Would remove it.** Nothing: counting moves the gate does not evaluate would measure nothing about the gate.

## L3. Agents are declarative among themselves

- **What.** Any session can write as any `agent:<model>`; the database only refuses the generic `agent:claude` and refuses `human:*` from every connection except the owner-only role. The client now takes the identity from `FONDOS_ACTOR` only (required, canonical, one per process; an explicit `actor` must equal it), which removes typing mistakes such as the `agent:claude-opus-5-5` labels written by a session that ran as Sonnet on 2026-10-28, but it cannot prove which model is running: the harness exposes none to child processes, and under `"model": "opusplan"` the planning (read-only) and the executing models differ.
- **The live-write confirmation is friction, not a lock.** `confirm_live_write` asks a person at a terminal to type `I CONFIRM THIS IS NOT A SYNTHETIC TEST` once per process; a non-interactive caller (an agent's shell tool, a pipeline, a hook) cannot be asked, so the 2026-10-28 incident class (an agent running a probe through a non-interactive shell) is covered only by the tests having no live DSN, the positive scratch-database guard of the harnesses (FND-0351) and the standing rule.
- **Mitigation.** The human actor is protected; agent identities are accountable by session id and model name; the variable is set in `.claude/settings.local.json` and must be changed together with the configured model. **Would remove it:** one database role per agent and per operator (FND-0354), which is a credential-management decision.

## L4. Direct writes of logs and link events stay open until step A-2

- **What.** Until A-2 is applied, the shared writer can still `INSERT` log rows and link events directly, so the identity rule of the routines can be bypassed by a direct `INSERT` as `human:*`. A-2 is blocked behind its safety gate (50 real, non-system transitions with no direct write since).
- **Mitigation.** The author trigger of A-2 applies the same rule once applied; until then the single-door static test keeps repository code off the raw path.

## L5. The effectiveness of the descriptive-change history is not measured yet

- **What.** `field_change_event` is live and covers the eight descriptive columns, but its value (the share of changes that carry an actor) can only be measured on real edits made through `update_attributes()`.
- **Mitigation.** `v_field_change_attribution`. The limitation counts as resolved only when that share has been measured and logged in FND-0338.

## L6. The delivery gate is a database control, not a commit control

- **What.** It acts when a ticket is moved. Commits, switch flips and releases still do not write to the backlog (Phase C, deferred), so work done without moving its ticket is invisible to it.
- **Mitigation.** Review sweeps and `backlog_links.py --for`. **Would remove it:** Phase C (FND-0334), deferred by the owner.

## L8. One direct row in `gate_switch` precedes the governed executive override

- **What.** On 2026-10-28 the delivery gate went from AUDIT to ENFORCE by ONE direct row in the append-only `backlog.gate_switch` (event 2, actor `system:owner-waiver`), written with the owner role by an agent session on the owner's written directive, because `sp_set_gate` then had no way to waive the audit window. The row cites the directive and the evidence; it does not impersonate the owner.
- **Affects.** Reading the history of the gate: that row is the only mode change not made through `sp_set_gate`, and its `override` column reads false (the column did not exist yet).
- **Mitigation.** FND-0350: `sp_set_gate(..., p_executive_override => true)`, human-only, reason of 80+ characters, flagged and listed (`v_gate_override_event`); a repository test fails on any new raw insert. **Would remove it:** nothing (history is append-only); the precedent stays documented here.
