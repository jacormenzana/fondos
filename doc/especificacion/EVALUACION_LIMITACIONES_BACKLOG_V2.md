# Backlog v2 — assessment of the two documented limitations

**Status:** for the owner's decision, 2026-10-27. No code, no DDL, nothing here is applied.
**Context:** `doc/especificacion/PROPUESTA_BACKLOG_V2.md` §6.2 and §10 documented two limitations and the owner asked (2026-10-26) that they be evaluated before the governance is declared closed. This note measures them, lists the options with cost and residual risk, and recommends one. Every figure comes from the live registry on 2026-10-27 through a read-only connection (queries in the appendix).

## 1. Declared identity of the actor

**What it is.** Every routine takes the actor as a parameter (`agent:<model>`, `human:<login>`, `system:<name>`). The database refuses anything that is not canonical and records who *claimed* what; it cannot tell which agent is really behind the shared writer connection, because all sessions run under the owner's account and read the same `.env`.

**What was measured.**
- Of 1,295 log rows, 897 (69.3 %) resolve only to `agent:claude`: the model was never recorded. That is the historical cost of free-text authors; since the explicit-actor rule every new event carries the model (2 live status events so far, both `agent:claude-opus-5-5`).
- 227 rows (17.5 %) are `human:jacor`. Today nothing prevents a session from writing as the owner.

**Who the limitation affects.** There is no hostile agent in this system; the failure modes are (a) a wrong or copied `FONDOS_ACTOR`, and (b) a session writing as `human:jacor`, which would present an agent's decision as the owner's. (b) is the one that matters for governance, because owner decisions are what unlock A-2, closures and DDL.

| Option | What it buys | Cost | Residual risk |
|---|---|---|---|
| A. Accept and keep documented (today) | nothing new | none | (a) and (b) stay possible and invisible |
| B. Record client evidence next to the claim (`session_user`, `application_name`) and flag a mismatch | catches mistakes (a) | one additive column set and a violation view; Phase D size | `application_name` is also client-asserted, so (b) is not stopped |
| C. One database role per agent | attribution by credential | high: a role and DSN per agent | none gained here: every session runs under the same OS account and can read every DSN, so the credentials do not separate anyone |
| D. Human identities only through an owner-only role | (b) closed: `human:*` actors accepted only when `session_user` is a role whose credential lives in the owner's `.pgpass`, outside `.env` and outside the environment agents inherit | low: one role, one check inside `sp_transition` / `sp_open_ticket`, the owner's own tools use their own DSN; the same pattern as the planned `backlog_adjudicator` of Phase C | agents remain asserted among themselves (a) |

**Recommendation: A for agents plus D for humans.** It closes the one case that touches governance, costs about half a day, and reuses a pattern already approved. Add B later only if mismatches ever show up in practice. C is rejected: it adds credential management without separating anyone. One caveat for D: the owner's human DSN must not be exported in a shell that also launches agents; it stays in `.pgpass` or is typed when needed.

## 2. Traceability of descriptive changes (exception E3)

**What it is.** After A-2 the writer may still update `title`, `nature`, `schema_name`, `object_type`, `object_name`, `priority`, `session_id` and `scenario_description` directly. `update_ts` shows that something changed, never what, by whom, or the previous value.

**What was measured.** None of the 302 tickets has an update later than its last log + 10 minutes, and none lacks a log: so far every edit has been accompanied by a progress note. The convention holds, but a note does not say which field changed or what it used to say, and nothing enforces the convention.

| Option | What it buys | Cost | Residual risk |
|---|---|---|---|
| A. Accept (convention + `update_ts`) | nothing new | none | old values of priority, nature or title are lost |
| B. Append-only `field_change_event` filled by a trigger on those eight columns (old value, new value, `session_user`, the actor when the client sets it) | full before/after history, additive, reversible | small: about 40 lines of DDL plus tests; no change to how sessions edit | the actor is "unknown" unless the client sets it |
| C. Remove E3: edits through a routine `sp_update_attributes(actor, reason, ...)` and revoke the column privileges | actor and reason mandatory | medium: new client API, every habit changes, friction for one-word fixes | none |

**Recommendation: B now, C only if B shows many "unknown actor" rows.** Measure first, as in every other decision of this work: B is additive and removable, and the share of unattributed edits it records is exactly the evidence for or against C. It fits Phase D (violations and metrics) and does not gate A-2.

## 3. Decisions requested

1. Identity: accept A + D (human identities through an owner-only role), or prefer A alone, or A + B.
2. Descriptive changes: accept B (trigger-based history), or A, or C.
3. When: both are independent of A-2 and of the 50-transition gate; proposed as part of Phase D, with D (the owner-only role) possibly earlier because it touches who may write as the owner.

Nothing is built until the owner decides, and each DDL step stays the owner's to apply.

## Appendix — queries (read-only, 2026-10-27)

```sql
-- identity: who is behind the log rows, through backlog.actor_alias
SELECT actor, count(*) FROM backlog.v_backlog_log_actor GROUP BY 1 ORDER BY 2 DESC;
-- live actors of the routines
SELECT actor, count(*) FROM backlog.status_event WHERE origin = 'LIVE' GROUP BY 1;
-- descriptive edits not accompanied by a log within 10 minutes
WITH ll AS (SELECT action_point_id, max(log_ts) m FROM backlog.backlog_logs GROUP BY 1)
SELECT count(*) FROM backlog.backlog b JOIN ll USING (action_point_id) WHERE b.update_ts > ll.m + interval '10 minutes';
-- tickets without any log
SELECT count(*) FROM backlog.backlog b WHERE NOT EXISTS (SELECT 1 FROM backlog.backlog_logs l WHERE l.action_point_id = b.action_point_id);
```
Results: 1,295 log rows, 897 as `agent:claude` (69.3 %), 227 as `human:jacor` (17.5 %), 89 as `agent:claude-opus-5-5`; 2 live status events, both `agent:claude-opus-5-5`; 302 tickets, 0 with an update beyond its last log + 10 minutes, 0 without a log.
