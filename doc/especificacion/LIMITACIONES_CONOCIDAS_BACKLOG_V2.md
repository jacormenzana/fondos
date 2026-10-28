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

- **What.** Any session can write as any `agent:<model>`; the database only refuses the generic `agent:claude` and refuses `human:*` from every connection except the owner-only role.
- **Mitigation.** The human actor is protected; agent identities are accountable by session id and model name. **Would remove it:** one database role per agent, which is a credential-management decision.

## L4. Direct writes of logs and link events stay open until step A-2

- **What.** Until A-2 is applied, the shared writer can still `INSERT` log rows and link events directly, so the identity rule of the routines can be bypassed by a direct `INSERT` as `human:*`. A-2 is blocked behind its safety gate (50 real, non-system transitions with no direct write since).
- **Mitigation.** The author trigger of A-2 applies the same rule once applied; until then the single-door static test keeps repository code off the raw path.

## L5. The effectiveness of the descriptive-change history is not measured yet

- **What.** `field_change_event` is live and covers the eight descriptive columns, but its value (the share of changes that carry an actor) can only be measured on real edits made through `update_attributes()`.
- **Mitigation.** `v_field_change_attribution`. The limitation counts as resolved only when that share has been measured and logged in FND-0338.

## L6. The delivery gate is a database control, not a commit control

- **What.** It acts when a ticket is moved. Commits, switch flips and releases still do not write to the backlog (Phase C, deferred), so work done without moving its ticket is invisible to it.
- **Mitigation.** Review sweeps and `backlog_links.py --for`. **Would remove it:** Phase C (FND-0334), deferred by the owner.
