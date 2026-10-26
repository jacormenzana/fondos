# Backlog v2 — proposal for the evolution of `gestion.backlog`

**Status:** proposal, design approved by the owner on 2026-10-26; code and DDL are out of scope of the session that wrote it.
**Tickets:** FND-0330 (actor alias), FND-0331 (Phase A), FND-0332 (decision-time read rule), FND-0333 (Phase B), FND-0334 (Phase C), FND-0335 (Phase D).
**Readers:** the owner and the engineers and agents who implement the phases. When a phase ships, `doc/reglas/NORMAS_BACKLOG.md` is rewritten as listed in §13 and becomes the canonical rule text; this document stays as the rationale and the evidence.
**Date of every figure:** 2026-10-26, read from the live registry through a read-only connection (queries in Appendix B).

---

## 1. Summary

The backlog records tickets and relationships well, but it does not **govern** them. Status and closure fields are plain `UPDATE`s with no history, dependencies are advisory, links appear only when an offline review is run, and nothing the system does (commits, releases, promotions) reaches the backlog. The evidence is in §4 and §5.

The proposal has four phases plus two small prerequisites, each one ticket and one release:

| Ticket | Delivers | Closes gaps |
|---|---|---|
| FND-0330 | Canonical author identity (`actor_alias`) | G10 |
| FND-0331 **Phase A** | Transition procedure `sp_transition`, `sp_open_ticket`, append-only `status_event`, single-door client, shadow stage, then revoke of the raw `UPDATE` grant | G1, G10 |
| FND-0333 **Phase B** | `IMPACTS`/`SUPERSEDES`, review flags, `hold`, hard gate on delivery closures, step-0 correction manifest | G2, G3, G4 |
| FND-0332 | Decision-time read rule and `V-STALE-READ` | G11 |
| FND-0334 **Phase C** | `backlog_sync.py`, commit-msg / pre-push / pre-rebase hooks, owner adjudication, signed attestation checked by CI, gate modes, calibrator and automatic retirement, `close_condition` | G6, G7, G11 |
| FND-0335 **Phase D** | `group_tag`, violation views, lean metrics, impact closure | G8, G9 |

Four decisions shape everything else:

1. **Append-only everywhere.** Every change is a new event row; no row is rewritten, including during migration.
2. **One door.** Tickets are opened and moved only through the client API and the procedures behind it; a static test enforces it.
3. **The gate protects delivery, not bookkeeping.** `DEPENDS_ON`, holds and review flags gate `READY_FOR_DEPLOY` and the closures that claim something was delivered (`CODE_FIX`, `DATA_FIX`, `WORKAROUND`). `REJECTED` and `DUPLICATED` are never gated.
4. **The commit-time collision check is a decision prompt, not a detector, and it judges itself.** Measured recall is about 12–15 %. It starts in audit mode, never blocks a commit, gates only publication, and retires itself if its own precision or recall floors fail.

Out of scope by owner decision: a `ticket_object` registry and switch-flip flags (former "C2"), a retroactive-ticketing path, and any code in this session.

## 2. Scope, population and method

- **Population:** all tickets with id up to FND-0329 — 295 tickets, 1,265 logs, 265 link events — as they stood before the six v2 tickets were opened. The `WRONG_DOC` exclusion of `NORMAS_BACKLOG.md` §4.1 rule 5 does not apply (the backlog is not an ISIN population); it matters only for FND-0029 (§5.1).
- **Registry figures:** queries Q1–Q22 in Appendix B, run on a connection with `default_transaction_read_only=on`. SQL and result are printed together so any figure can be re-measured.
- **Replay figures** (commit collision rates, signal recall, flag fatigue, commits versus ticket status): defined in Appendix C. They were produced by ad hoc session scripts; Phase C delivers `scripts/audit/backlog_gate_replay.py` so they become reproducible (rule 6 of §4.1). Every replay uses **today's** open-ticket set and ticket text on **past** commits; the error runs in both directions, and the audit-mode window on live data is the number that counts.
- **Ticket states** cited in this document (FND-0022, 0029, 0033, 0035, 0038, 0041, 0078, 0088, 0158, 0160–0163, 0166, 0167, 0185, 0193, 0203, 0209, 0223, 0233, 0236, 0250, 0258, 0320) were read from the live registry on 2026-10-26.

## 3. Current system

| Element | What it is today |
|---|---|
| Store | `db/pg_backlog/00_schema.sql` (`BACKLOG`, `BACKLOG_LOGS`), `10_kpis_and_release.sql` (`vw_backlog_kpis`, `sp_close_release`), `20_links.sql` (append-only `backlog_link_event`, `v_backlog_link`, `v_open_link_graph`) |
| Client | `shared/backlog_client.py`: incident capture, read API, `append_log`, `record_link_events`. **No create or transition API.** |
| Tools | `scripts/audit/backlog_links.py` (signals S1–S4, review CSV), `backlog_hygiene.py` (on-demand premise sweep, stale status), `scripts/ops/close_release.py` (raw `UPDATE`, then `sp_close_release`, plus the audit-incident gate of FND-0264), `scripts/audit/rfd_matrix_probe.py` (closing conditions in a Python dict) |
| Consumers holding a ticket id | `control.cycle_flag`, `control.audit_incident`, `control.audit_accepted_finding`, the P3 promotion override, the cycle report. They read status at their decision point; only `cycle_flag` stores a snapshot. |
| Connecting role | `backlog_writer`: `INSERT, SELECT, UPDATE` on `backlog` (any column); `INSERT, SELECT` on logs and link events (Q16) |
| Rules | `NORMAS_BACKLOG.md`: domains, flow, links (§3), procedure (§4), hygiene rules S1–S7 (§4.1, FND-0323), absorption (§5) |

## 4. Gaps

| # | Gap | Evidence (appendix query) |
|---|---|---|
| G1 | Status, closure fields and priority are plain `UPDATE`s: no transition API and no status history | Only 274 of 1,265 logs mention any status word (Q12); cycle time cannot be computed; illegal jumps are possible |
| G2 | `DEPENDS_ON` is advisory by design | One delivery closure passed an open prerequisite: FND-0223 (`CODE_FIX`, closed 2026-10-24) over FND-0193 (Q6). The other five rows of Q6 are `REJECTED` closures, which are legitimate |
| G3 | No notification semantics: no `IMPACTS` or `SUPERSEDES`, and `OVERLAPS` propagates nothing | A change in A never puts B on review; the premise sweep is on demand |
| G4 | Links are recorded only by an offline review | 534 ticket pairs cite each other, 201 (38 %) have a confirmed link (Q15); 236 of the 265 link events came from one owner burst, 2026-10-11 to 10-17, then 29 in eight days (Q18) |
| G5 | No ticket-to-object registry | `object_type = 'N/A'` on 191 of 295 tickets (Q3). **Not addressed in this epic** (owner decision); manual enforcement |
| G6 | Nothing the system does reaches the backlog | Commit and ticket status drift apart (Appendix C, C4): tickets with commits while `OPEN`/`TODO`, commits after `closure_ts` |
| G7 | Closing conditions live in free text and in code | 29 logs declare a closing condition in prose (Q19); the `rfd_matrix_probe.py` `SPEC` dict is code, and 8 of the 9 of its tickets checked are now `CLOSED` (Q20) |
| G8 | No grouping object | The structured label "DECISION PENDIENTE" of §4.1 rule 2 has been used 0 times (Q21); `RELEASE_WITH` pairs and owner holds live in prose |
| G9 | No violation views; hygiene is procedural | Every check in §5 had to be written ad hoc |
| G10 | Actor identity is free text | 13 distinct author strings in the logs, one of them a human login (Q13); 95 of 295 tickets have no `session_id` and 293 no `assignee` (Q3) |
| G11 | Nothing reacts to a status change | Consumers read live at their decision points, so the real gap is narrow: a residual accepted under a ticket that just closed, or a cycle flag whose ticket closed, is noticed only at the next cycle report |

## 5. History audit and lessons from backlog v4

### 5.1 Bad practice found in the history

| Finding | Evidence |
|---|---|
| **Registered after the work.** Tickets created and closed within one hour: the "check neighbours before opening" step never ran | 86 (Q5) |
| **Release discipline.** Of 208 `CODE_FIX`/`DATA_FIX` closures: 92 went through `sp_close_release`; 53 lack `deployment_ts`; 96 lack `release_version`; 25 carry a non-standard release label (`v23`, `20261004`, `v2026.10.01`); 153 never mention `READY_FOR_DEPLOY` | Q3, Q4 |
| **Gate ignored.** One delivery closure with an open prerequisite: FND-0223 over FND-0193. The links are correct (owner directive 2026-10-17). **FND-0193 is itself `IN_PROGRESS` although its logs of 2026-10-19 record the verdict of record and "PROMOTED AND CLOSED OUT"**: a stale status in the sense of §4.1 rule 2. If it is closed, FND-0223 and the `BLOCKED` FND-0158 stop appearing as violations. That is the owner's call, not this proposal's | Q6, ticket logs |
| **`BLOCKED` without a blocker link.** 3 of 4 `BLOCKED` tickets have no open `DEPENDS_ON` prerequisite. FND-0203 states its blocker in text (it closes together with FND-0236, `IN_PROGRESS`) but has no link. FND-0029 names its condition ("external source; re-check after each harvest") but only in text. FND-0041 has two logs and none names a blocker | Q7 |
| **`DUPLICATED` without a link.** FND-0022 and FND-0038; their targets are legacy codes (P2-12, P1-03) that the migration logs resolve to FND-0015 and FND-0046 | Q8 |
| **`WORKAROUND` without a follow-up rule.** FND-0166 has a link, FND-0320 has none; no rule requires an optimisation follow-up | Q9 |
| **Reopening is untracked.** 12 tickets mention reopening in their logs (weak text match); FND-0034 was reopened on 2026-10-25 with no structured event | Q11 |
| **`DEFERRED` without a revisit trigger.** Six tickets, 22 to 64 days old; two carry only the legacy-migration note | Q10 |
| **Logs as essays.** Average 699 characters, 25 logs over 3,000: counts, conditions and decisions are buried in prose | Q12 |

### 5.2 What to take from backlog v4 (BigQuery project)

| v4 practice | Here |
|---|---|
| Single transition procedure; direct `UPDATE` of status forbidden | **Adopt** (Phase A) |
| Closure rules: `solution_type` required; `CODE_FIX`/`DATA_FIX`/`WORKAROUND` need `deployment_ts` and `release_version`; reopen only with a force flag | **Adopt** (Phase A) |
| Gate before `READY_FOR_DEPLOY` listing the blocking tickets | **Adopt, widened to delivery closures** (Phase B) |
| `BLOCKS` | Same as `DEPENDS_ON` with the direction reversed (A `BLOCKS` B ≡ B `DEPENDS_ON` A); kept as `DEPENDS_ON` |
| `IMPACTS`, `SUPERSEDES` | **Adopt** as new link types (Phase B) |
| `SHARES_OBJECT` | Covered by `OVERLAPS`; propagation only when it carries an `absorbs` note |
| `DUPLICATES`, `RELATED` | Already present |
| `REVIEW_PENDING` as a status | **Adapt:** a `review_flag` plus `sp_ack_review`, so the workflow status is not lost |
| Cycle check on `pr_bl_rl_relate` | **Adopt** (Phase B) |
| `WORKAROUND` opens an `OPT` ticket automatically | **Adopt:** closing as `WORKAROUND` needs a follow-up ticket id, or `sp_transition` opens an `OPT` ticket linked `SPLIT_FROM` (Phase A); bypass only with `p_force` and a reason |
| Four cluster types | **Adapt:** one `group_tag` with `kind` ∈ {`TRAIN`, `THEME`, `DECISION`} (Phase D) |
| `V_BL_*_VIOLATIONS`, lean metrics, recursive impact closure to 5 levels | **Adopt** (Phase D); lean metrics exclude backfilled events |
| Session id `[PROYECTO]_[YYYYMMDD]_[TIPO]_[TEMA]` | Already in the norm; `sp_open_ticket` and `sp_transition` now enforce it |
| `PRJ-NAT-NUM` ids | **Reject:** 438 files already cite `FND-nnnn`, and nature is a column |
| Nested `ARRAY<STRUCT>` log | **Reject:** `BACKLOG_LOGS` is already an append-only table |
| Domain differences (`DEFERRED` is a `solution_type` there, a status here; `DSET` there, `SCHEMA` here) | **Keep the local domains** |

## 6. Target design

### 6.1 Principles

1. Append-only: events, never rewritten rows; logs and link events stay immutable.
2. Single door: the client API and the procedures behind it are the only writers; `tests/test_backlog_single_door.py` fails on any `INSERT INTO backlog.backlog` or `UPDATE backlog.backlog` outside `shared/backlog_client.py` and `db/pg_backlog/`.
3. The sanction for a missed check is a **hold**, never a status change: `BLOCKED` means "waiting on a named condition" (§4.1 rule 2) and must not be used as punishment.
4. Infrastructure failure fails **open and is recorded** (WSL idle shutdown, backlog unreachable): the commit goes through, the trailer says `offline`, and the sync completes the check later without penalty.
5. Every threshold is a code constant calibrated by replay (the rule of `NORMAS_BACKLOG.md` §6); a calibrator may move along a pre-measured ladder, never invent a rule.
6. DDL and credentials are applied by the owner (dry run by default, one transaction per phase, a staged `down` script per phase); a session never applies them.
7. Actors with the prefix `system:` are excluded from every "exit" count (shadow stage, adjudication rates).

### 6.2 Phase A — transition kernel (FND-0331) and actor alias (FND-0330)

**FND-0330 — `backlog.actor_alias(raw, canonical, kind)`.** Maps every raw author spelling to `human:<login>`, `agent:<model>` or `system:<name>`. Logs are append-only, so history is not rewritten: read views and the backfill use the canonical form. `backlog_client.current_actor()` returns the canonical form for new writes. Acceptance: every distinct author string in `backlog_logs` has an alias row, and a test fails when a new spelling has none. Prerequisite of the Phase A backfill.

**FND-0331 — owner decisions of 2026-10-26, now part of the design.** D1: the privileged routines belong to a new **non-superuser** role `backlog_owner` (no login), and every routine pins `search_path`. D2: Phase A ships in two owner-approved steps, **A-1** (additive) and **A-2** (revocation); A-2 is conditioned on 50 consecutive non-system transitions without a direct update and the migration script refuses to run it otherwise. D3: writes on state, closure, release, identity and audit go through controlled routines; the direct-write exceptions are enumerated and justified before the revocation (below). D4: the three stale audit skills are rewritten in the same change. D5: the actor is explicit (`actor=`), with `FONDOS_ACTOR` as a validated alternative and no guessing. D6: `sp_open_ticket` and `sp_transition` are functions.

**FND-0331 — objects of step A-1** (`db/pg_backlog/30_transitions.sql`, rollback `down/30_transitions_down.sql`):

| Object | Content |
|---|---|
| role `backlog_owner` | `NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE`; owns the routines and the three audit tables below; holds only the table privileges they need. Created inside the A-1 block, so it is part of the owner's A-1 approval |
| `backlog.status_event` | append-only: `event_id, action_point_id, from_status, to_status, actor, session_id, reason, forced, origin ∈ {LIVE, BACKFILL}, detail (jsonb: the closure fields before a reopening, the follow-up, the blocker), event_ts` (clock time, so events of one transaction stay ordered) |
| `backlog.wait_event` | append-only record behind `BLOCKED` and `DEFERRED`: `kind, blocker_ref` (a ticket) or `condition_text`, `revisit_by` or `revisit_on_event`, `revisit_basis ∈ {DECLARED, DEFAULT_90D}`, `actor, event_ts`, with `CHECK`s that make the blocker or the revisit mandatory |
| `backlog.raw_update_event` | the V-00 record: a direct write to a governed column (op, old and new values, database user, application) — written only by the shadow trigger |
| `backlog.sp_open_ticket(...)` | **function**; id from `seq_backlog_fnd`, canonical reporter, session id in the norm's format, first log and first status event; returns the id |
| `backlog.sp_transition(p_ap_id, p_to, p_actor, p_session_id, p_comment, p_solution_type, p_release_version, p_blocker, p_condition, p_revisit_by, p_revisit_on_event, p_followup, p_assignee, p_force)` | **function**; one transaction: legal-transition check, closure rules, waiting states, ticket update, log row (the comment, verbatim), status event; returns the event id |
| `backlog.sp_close_release` | the existing procedure **rebuilt on `sp_transition`**, same name, signature, dry-run behaviour and log text; the user name is resolved through `actor_alias` |
| `backlog.v_shadow_stage` | non-system status changes recorded after the last V-00: the gate of A-2 |
| trigger `governed_writes` | records (never blocks) a direct `INSERT` or a change of `status`, `closure_ts`, `solution_type`, `deployment_ts`, `release_version`, `reporter`, `assignee`, `action_point_id`, `project_code` or `creation_ts` made outside the routines |

**`sp_transition` rules.**

- Legal moves: the flow of `NORMAS_BACKLOG.md` §2 (`OPEN → TODO → IN_PROGRESS ⇄ BLOCKED → READY_FOR_DEPLOY → CLOSED`, back one step when QA fails, `DEFERRED` from any pending state). Reopening a `CLOSED` ticket needs `p_force` and a reason, and writes a reopen event.
- Closing: `solution_type` required, and the routine sets `closure_ts`. A **delivery closure** (`CODE_FIX`, `DATA_FIX`, `WORKAROUND`) comes from `READY_FOR_DEPLOY`, needs a release label of the form `R<yyyy>.<mm>-<TAG>` and gets `deployment_ts`; `WORKAROUND` needs `p_followup` or opens an `OPT` ticket linked `SPLIT_FROM`. `REJECTED` cites the ticket that supersedes it (`p_followup`) and `DUPLICATED` the ticket that is kept, with its `DUPLICATES` link created in the same call when absent. Any shortcut (a delivery closure from another status, `REJECTED` with no ticket, an illegal move, reopening) needs `p_force` and a reason of 40+ characters, and is recorded as forced.
- Same-status calls are allowed only to assign a responsible (`p_assignee`) or to attach the release label of a `READY_FOR_DEPLOY` ticket; the closure fields of a closed ticket are never touched by them.
- `BLOCKED` requires `p_blocker` (a ticket id, which also guarantees a `DEPENDS_ON` link, or a named condition); `DEFERRED` requires `revisit_by` or `revisit_on_event`.
- The gate of Phase B is applied here.

**Backfill.** One `BACKFILL` event per existing ticket from its current state (`event_ts` = `closure_ts` or `update_ts`). Lead time keeps using the recorded `creation_ts`/`closure_ts`; cycle time and RFD-wait exclude `BACKFILL` events, so no metric is computed on invented timestamps.

**Client.** `backlog_client.open_ticket()`, `transition()`, `resolve_actor()` (explicit actor first, `FONDOS_ACTOR` second, never the OS user), `make_session_id()`, `shadow_progress()`; `report_incident()` opens tickets through `sp_open_ticket` (reporter `system:autodetect`). `sp_close_release` and `close_release.py` are rebuilt on `sp_transition`; the audit-incident gate of FND-0264 stays in `close_release.py`.

**Shadow stage (A-1), then revoke (A-2).** Each routine carries a function-level `SET backlog.via_proc = 'on'`, which Postgres restores when the routine returns (a first draft used a transaction-wide setting and a hermetic test showed it let a raw write slip through after any routine call in the same transaction). The `governed_writes` trigger records a write made without it as V-00 and never blocks it. The setting is not a security boundary (any session can set it): the shadow stage measures habits and accidents, and the enforcement is the revocation. Step A-2 (`31_revoke_raw_update.sql`) removes `INSERT` and `UPDATE` on `backlog.BACKLOG` from `backlog_writer`, grants back only the descriptive columns, and adds the author check on logs and link events; `migrate_backlog_v2.py --phase revoke --apply` **refuses** unless `v_shadow_stage` shows **50** non-system status changes since the last V-00 (no time floor).

**Direct-write exceptions after A-2 (D3, enumerated before the revocation).** E1: `INSERT` into `BACKLOG_LOGS`, append-only notes, the author must be canonical or have an alias. E2: `INSERT` into `backlog_link_event`, append-only with `FK` and `CHECK`, same author rule; Phase B moves it behind a routine. E3: `UPDATE` of `title`, `nature`, `schema_name`, `object_type`, `object_name`, `priority`, `session_id` and `scenario_description`, which are descriptive and triage attributes; `update_ts` records that something changed but not what, a known audit gap (risk 10) and a Phase D candidate. Everything else is closed to the writer.

**Identity limit, stated plainly.** The actor is a parameter: every session holding the shared DSN can claim any canonical identity. The database refuses anything that is not canonical and records who claimed what, but it cannot authenticate which agent is behind the DSN.

**One change, not two.** Phase A ships as one change: client API, `close_release.py`, `report_incident`, `AGENTS.md`, `NORMAS_BACKLOG.md` §2, `BACKLOG_RFD_VERIFICATION_MATRIX.md` and the three audit skills (`pipelineP1Audit`, `pipelineP2Audit`, `pipelineP1P2Audit`, whose §8 and pre-flight line still pointed at the HTML artifact retired on 2026-09-21), so agents do not keep teaching the raw path. **The working tree is what the pipelines run**, so the client detects whether A-1 exists (`transitions_store_present()`): until the owner applies it, `report_incident` and `close_release.py` keep their previous statements and the new write functions fail loudly; the legacy statements are the only allowlisted raw SQL of `tests/test_backlog_single_door.py` and are removed once A-2 is accepted.

### 6.3 Phase B — relationship semantics (FND-0333)

- **Link types.** Add `IMPACTS` and `SUPERSEDES` (the `CHECK` in `20_links.sql` and `LINK_TYPES` in `backlog_client` change together; `tests/test_backlog_links_client.py` already verifies they match). Add an `origin` column to `backlog_link_event` (`REVIEW` default, `GATE`, `STEP0`). `DEPENDS_ON` is checked for cycles when recorded.
- **Review flags.** `backlog.review_flag` (append-only raise and acknowledge). A flag is raised on the far end only when a ticket reaches `READY_FOR_DEPLOY` or closes through an `IMPACTS` link or an `OVERLAPS` link that carries `absorbs` — not for all 72 confirmed `OVERLAPS` or the 100 `RELATED`. Replayed over the 233 closures: median 0 flags, maximum 2, 7 closures raise any (Q22). If a later replay shows a median above 2.5, consolidating flags per source ticket becomes a new ticket. `sp_ack_review` acknowledges. `SUPERSEDES` only suggests closing the superseded ticket (`DUPLICATED` or `CODE_FIX`).
- **Holds.** `backlog.hold_event` (append-only raise and release): reason, source violation code, actor.
- **Gate in `sp_transition`.** Applies to `READY_FOR_DEPLOY` and to closures with `CODE_FIX`, `DATA_FIX` or `WORKAROUND`; **never** to `REJECTED` or `DUPLICATED`. Refused while a confirmed `DEPENDS_ON` prerequisite is open, an open hold exists, or a review flag is unacknowledged; the error lists the blockers. `p_force` with a reason overrides it; the override is logged and counted (`V-FORCED`). Switch: `BACKLOG_DELIVERY_GATE_ENABLED`.
- **Links at the source.** `open_ticket()` returns the `FND-nnnn` mentions found in the text and records them as `PROPOSED` link events, so linking no longer waits for the next review (G4).
- **Gate replay before go-live.** Replay the gate over history. Acceptance: it refuses exactly the delivery closures with an open prerequisite on that day (FND-0223 at the time of writing).
- **Step 0** (§6.6) is the first thing the owner runs after Phase B is deployed.

### 6.4 Phase C — system sync (FND-0334) and the decision-time rule (FND-0332)

#### 6.4.1 What runs at commit time

One **commit-msg** hook (it has both the message and the staged files; `pre-commit` has no message and runs before other checks can still abort). In order, within a 3-second budget:

1. validate `FND` tokens (malformed ids such as `fnd0193` are rejected; unknown ids are warned);
2. collision check (§6.4.2);
3. for a `Refs: FND-nnnn` trailer on a ticket in `OPEN`/`TODO`: **synchronous** `transition()` to `IN_PROGRESS` as actor `system:gate`, with the commit message as evidence — never from `BLOCKED` or `DEFERRED`, never backward, never from a mention in the subject (a subject id cannot tell work from a mention, Appendix C4). Switch: `BACKLOG_AUTO_FORWARD_ENABLED`. Closures stay manual;
4. write a `backlog.gate_check` row through `sp_gate_check(tree_hash)` and append the trailer `Backlog-Gate: <nonce>`, the nonce bound to the staged tree.

**The hook never blocks a commit, in any mode.** Backlog unreachable: fail open, trailer `Backlog-Gate: offline`; `backlog_sync.py` runs the move and the check later with no penalty. A commit aborted after the hook ran leaves a ticket `IN_PROGRESS` with no commit, which is harmless and logged as "commit-msg gate". Hooks are installed through the `scripts/audit/pre-commit.sh` pattern of FND-0314.

#### 6.4.2 The collision rule and its tiers

Pairs are recorded as `PROPOSED` `OVERLAPS` link events with `origin = 'GATE'`; "undecided" means the latest event of the pair is such a proposal.

| Tier | Rule | Measured, October replay |
|---|---|---|
| **T0** | the commit touches a code file (`.md`/`.txt` excluded) whose full repo path is cited in the text or logs of another `IN_PROGRESS` ticket; files cited by 5 or more open tickets are ignored; pairs with a confirmed link are skipped | 83 distinct pairs; 22 % of commits introduce a new undecided pair |
| **T1** | T0 and at least 2 shared files | 9 pairs; 3 % of commits |

"Function-level match" is **not** a tier: it is unimplemented and unmeasured. It can become one only after a replay and a code change.

#### 6.4.3 Adjudication

`backlog.sp_adjudicate_overlap(a, b, decision, absorbs, reason)` with `decision ∈ {OVERLAPS, RELATED, DEPENDS_ON, NONE, REVERT}` writes a `CONFIRMED` or `REJECTED` link event. The procedure checks `current_user = 'backlog_adjudicator'`, never a parameter. That role and its DSN are created by the owner and kept **outside the repository `.env`**, because every agent session on the machine can read the writer DSN, so an "actor is not the committer" check inside software would be spoofable. The owner is the only adjudicator. Decisions go through the existing review CSV (`--review-out`, a `decision` column, `--apply-review`, which now calls the procedure), in batches. The queue is visible in preflight and the cycle report: a pair pending over **48 h** raises `[ATENCION]`; over **96 h** `[ALERTA]` as the first line and a non-zero preflight exit. Nothing relaxes the gate when the queue ages: the mode never downgrades because traffic backs up.

#### 6.4.4 Modes

`BACKLOG_COLLISION_GATE_MODE` in `shared/config.py` (kill-switch registry, `NORMAS_IMPLEMENTACION.md` §8) is the **ceiling**: `OFF`, `AUDIT`, `BLOCK`. The effective mode is the lower of the ceiling and the latest `backlog.gate_state` event.

- `AUDIT` (first 14 days): records pairs and warns; blocks nothing.
- `BLOCK`: a **pre-push** hook refuses to publish while any pair from the pushed commits is undecided, and prints the exact shas and pairs; the CI attestation (§6.4.6) repeats the check. Agents are never stalled: the commit has already succeeded. While a pair is undecided the ticket's hold prevents `READY_FOR_DEPLOY` and delivery closure.

#### 6.4.5 Calibrator and automatic retirement

- **Tightening.** On day 10 of `AUDIT`, if the trailing 3-day share of commits introducing a new undecided pair is above 10 %, the calibrator moves T0 → T1, writes an `ops_event`, and the cycle report shows it. It only tightens, never loosens. `BLOCK` starts only at a share of 10 % or less.
- **Retirement.** Over windows of 14 consecutive days, in `AUDIT` and in `BLOCK`: *precision* (adjudicated pairs decided `OVERLAPS`, `DEPENDS_ON` or `REVERT` rather than `NONE`) must be at least 30 %, and the *recall floor* (newly confirmed `OVERLAPS` that the active tier would have flagged) at least 5 %. A verdict needs at least 15 adjudicated pairs and 10 newly confirmed overlaps; below that the window extends (at most 42 days, then "insufficient data" is reported and nothing is retired). On failure the system sets the gate to `OFF` by appending a `gate_state` event (actor `system:calibrator`, the measured numbers as evidence), with `[ALERTA]` in the cycle report. A repository file is never edited; re-enabling is an owner-written event with a reason, never automatic.
- **Scope of `OFF`:** only the collision gate (pairs, pre-push block, collision part of the attestation). Id validation, auto-forward, bypass detection and holds stay on. Governance then rests on the review sweeps and `backlog_links.py --for` at the start of each ticket.

#### 6.4.6 Detection of bypass, signed attestation and CI

- **Classification** (`backlog_sync.py`, run from preflight and the cycle report): each commit citing a ticket is *checked* (its nonce is in `gate_check` and the sync recomputes the same tree hash), *pending* (`offline`), or a **bypass** (trailer missing, unknown nonce, or tree mismatch: `--no-verify`, another clone, a forged trailer). A bypass raises `V-HOOK-BYPASS` and a **hold** on the cited ticket; the hold is released by adjudicating its unadjudicated pairs, or by the owner's reason when the commit touched no overlapping file.
- **Attestation.** `backlog_sync.py --attest`, run by the owner at push time, writes `backlog_gate/attestation.json` and `.sig`, signed with `ssh-keygen -Y sign` using the owner's private key (never in the repository or `.env`). The public key is in `.github/backlog_gate_allowed_signers`. Content: for each commit in the range, sha, tree hash, nonce, and pair ids with decided/undecided. **No ticket titles or text** (the remote is GitHub). The attestation commit is excluded from the range.
- **CI.** `.github/workflows/backlog-gate.yml` fails when the signature is missing or invalid, a commit citing an `FND` is absent from the attestation or its tree hash differs, or any pair is undecided. Stated limit: CI proves that the owner's key signed a statement; it cannot check the database.
- **Branch protection.** An Action on a push cannot block that push; it blocks only if `master` is protected with `backlog-gate` as a required check (owner action O-5). With it, publish by pushing `publish/<date>`, wait for green, then fast-forward `master` (checks attach to the sha). Without it the check is detective only. `backlog_sync.py` checks this through the GitHub API with a read-only fine-grained token held outside the repository: not required → `V-GATE-UNPROTECTED` and `[ALERTA]`; no token or API unreachable → `V-GATE-UNVERIFIED` (so a missing token never passes silently), cleared by an owner-written verification event that expires after 30 days. The local git configuration cannot see remote protection and is not a source.

#### 6.4.7 Operational protocols

- **Reverting an illegal commit.** A forward `git revert <sha>`, then publish. **No `rebase` and no `reset` on the shared `master`**; a `pre-rebase` hook refuses on `master`. Revert in reverse order of the commits; a revert commit inherits its original's pair status and creates no new pair; the pair is adjudicated `REVERT` with the revert sha (verified by the sync). When the pre-push hook refuses, the owner may publish the clean prefix with `git push origin <last-clean-sha>:master`: no rewrite is needed.
- **Emergency push** (urgent, CI or signing unavailable, or key lost): the owner, as admin, bypasses through the GitHub bypass-actors setting or disables the rule temporarily; logs `V-GATE-EMERGENCY` in a backlog entry the same day; **within 48 h** generates a new key, commits the new `allowed_signers` (the one permitted bypass), and signs a catch-up attestation for the emergency range; CI is re-run with `workflow_dispatch`.
- **Key rotation:** same path as key loss.

#### 6.4.8 The rest of the sync

- `backlog.ops_event` (append-only; kinds `COMMIT`, `RELEASE`, `PROMOTION`, `CYCLE_FLAG`, `AUDIT_INCIDENT`; ticket id, payload) and `backlog_sync.py` reading `git log` since a watermark (`Refs:` trailers; subject ids as a fallback).
- **`V-RETRO`:** the first commit citing a ticket is older than its `creation_ts` (work done before the ticket existed). **Work on a closed ticket:** a commit after `closure_ts`.
- `backlog.close_condition (ap_id, probe_kind, args, added_by)`; `rfd_matrix_probe.py` reads it instead of its `SPEC` dict; `--close-ready` lists tickets whose every probe passes. Closing stays a decision.
- `backlog.v_status_feed`: `status_event` since a watermark, with `event_ts`, for the consumers in `fondos`.
- **Decision-time read rule (FND-0332):** status is read at the point of decision and never cached in `gestion`; any stored copy carries its read time. `control.cycle_flag` gains `ap_status_read_ts` (fondos DDL applied by the owner); `V-STALE-READ` flags a snapshot read before the ticket's latest status event; the cycle report adds the closure-reaction check (a residual accepted under a ticket that just closed). No cron is needed.

### 6.5 Phase D — groups, violations, metrics (FND-0335)

- `backlog.group_tag` and `group_member`, `kind ∈ {TRAIN, THEME, DECISION}`; `v_group_status` gives `READY_TO_CLOSE` when every member is `CLOSED`. A `DECISION` group replaces the free-text "DECISION PENDIENTE" label of §4.1 rule 2; `RELEASE_WITH` pairs are migrated to `TRAIN` groups (the link type stays).
- `v_backlog_violations`: one row per violation, with the codes of §7.
- `v_backlog_lean_metrics`: lead time from the recorded columns; cycle time and RFD-wait from live `status_event` rows only.
- `v_impact_closure`: recursive, up to 5 levels, showing which open tickets a change can reach.
- The cycle report shows the violation count and the top 3.

### 6.6 Step 0 — historical data, applied by the owner after Phase B

`scripts/ops/backlog_step0_manifest.yaml`: a fixed, versioned list; dry run by default; idempotent; everything through `transition()` and link events (logs and rows are never rewritten, so "correcting" means an appended, structured record).

| Ticket | Action | Basis |
|---|---|---|
| FND-0203 | link `DEPENDS_ON` FND-0236; structured blocker | its own log: it closes together with FND-0236, which is `IN_PROGRESS` |
| FND-0029 | structured blocker "external source (Deutsche Bank catalogue); re-check after each harvest"; status unchanged | the condition is already in its last log; its population is `WRONG_DOC` |
| FND-0041 | `BLOCKED → TODO` | two logs, none names a blocker; related `OVERLAPS` FND-0233 and FND-0250 (both `OPEN`) |
| FND-0223 | recorded as the one known past-gate closure | Q6 |
| FND-0022, FND-0038 | `DUPLICATES` links to FND-0015 and FND-0046 | legacy codes P2-12 and P1-03 resolved through the migration logs |
| FND-0033, 0035, 0078, 0167, 0185, 0209 (`DEFERRED`) | `revisit_by` = deployment date of Phase B + 90 days, `revisit_basis = DEFAULT_90D` | uniform placeholder; on expiry the ticket is listed in the cycle report, **nothing transitions automatically**; the owner may replace the date later |

No manifest row reverses a link or a closure.

## 7. Violation catalogue

| Code | Rule | Source | Phase |
|---|---|---|---|
| `V-00` | a raw update of status or closure columns outside the procedures | trigger (shadow stage) | A |
| `V-FORCED` | a transition made with `p_force` | `status_event.forced` | A, B |
| `V-FAST` | `OPEN → CLOSED` in under 1 h with `CODE_FIX`/`DATA_FIX` | `status_event` | D |
| `V-RETRO` | first commit citing a ticket is older than its `creation_ts` | `ops_event` | C |
| `V-WORK-ON-CLOSED` | a commit citing a ticket after its `closure_ts` | `ops_event` | C |
| `V-HOOK-BYPASS` | commit with no valid `Backlog-Gate` evidence (also raises a hold) | `backlog_sync.py` | C |
| `V-GATE-UNPROTECTED` | `backlog-gate` is not a required check on `master` | GitHub API | C |
| `V-GATE-UNVERIFIED` | protection cannot be read and no owner verification is current | `gate_state` | C |
| `V-GATE-EMERGENCY` | an emergency push was used | owner log | C |
| `V-STALE-READ` | a stored status snapshot read before the ticket's latest status event | `cycle_flag` | FND-0332 |
| `V-BLOCKED-NO-BLOCKER` | `BLOCKED` with no `wait_event` blocker | `wait_event` | D |
| `V-DEFERRED-NO-REVISIT` | `DEFERRED` with no `revisit_by` or `revisit_on_event` | `wait_event` | D |
| `V-REVISIT-EXPIRED` | a `revisit_by` date has passed | `wait_event` | D |
| `V-WORKAROUND-NO-OPT` | `WORKAROUND` closure with no follow-up ticket | links | D |
| `V-DUP-NO-LINK` | `DUPLICATED` closure with no `DUPLICATES` link | links | D |
| `V-HOLD-OPEN` | a hold open beyond the queue alert thresholds | `hold_event` | D |

## 8. Credentials and owner actions

Sessions never apply DDL or create credentials. Each phase's ticket lists the actions it needs.

| # | Action | Needed by |
|---|---|---|
| O-1 | Create the role `backlog_adjudicator` and keep its credential outside the repository `.env` (owner's `.pgpass` or prompt) | Phase C |
| O-2 | Apply the DDL of each phase with `scripts/ops/migrate_backlog_v2.py` (dry run by default, one transaction per phase, staged `down` script) and the `control.cycle_flag` DDL of FND-0332 | every phase |
| O-3 | Install the hooks (commit-msg, pre-push, pre-rebase) through the `scripts/audit/pre-commit.sh` pattern | Phase C |
| O-4 | Create the signing key (`ssh-keygen`), keep the private key out of the repository, commit `.github/backlog_gate_allowed_signers` | Phase C |
| O-5 | Confirm or set branch protection on `master` requiring `backlog-gate` (this session could not read the repository settings) | Phase C |
| O-6 | Optional: a read-only fine-grained GitHub token for the protection check, held outside the repository | Phase C |
| O-7 | Run the step-0 manifest (dry run, then `--apply`) | after Phase B |
| O-8 | Phase A step A-1: review the commit, dry run `migrate_backlog_v2.py --phase transitions`, apply (it creates the role `backlog_owner`, so the approval covers it), run `scripts/audit/backlog_phase_a_acceptance.py --baseline` before and `--verify a1` after | Phase A |
| O-9 | Phase A step A-2: only when the A-2 dry run shows the shadow stage open (50 non-system transitions since the last V-00) and the enumerated exceptions E1–E3 are accepted; then `--phase revoke --apply` and `--verify a2` | Phase A |

## 9. Rollout and rollback

Order: FND-0330 → Phase A (shadow stage, then revoke) → Phase B (with step 0) → FND-0332 → Phase C (`AUDIT` 14 days, `BLOCK` only if the share is ≤ 10 %) → Phase D. Each phase is one ticket and one release, so a failure never needs more than one phase undone.

- **Transactions:** `migrate_backlog_v2.py` applies one phase per transaction (Postgres DDL is transactional); the pattern is `scripts/ops/migrate_backlog_links.py`. A mid-run failure rolls back whole.
- **Rollback:** a staged `down` script per phase (`DROP` of that phase's procedures, views and tables; re-`GRANT` for Phase A), tested in the hermetic Postgres suite. No array columns exist, so there is no legacy-array risk.
- **Switches** (registered in `NORMAS_IMPLEMENTACION.md` §8): `BACKLOG_DELIVERY_GATE_ENABLED`, `BACKLOG_AUTO_FORWARD_ENABLED`, `BACKLOG_COLLISION_GATE_MODE`.
- **Tests per phase:** pure tests (transition matrix, closure field rules, alias coverage, tier rules, retirement floors), hermetic Postgres tests (`python scripts/ops/run_pg_tests.py -- <files>`), the single-door test, the gate replay, and a dry run of `close_release.py` on a live `READY_FOR_DEPLOY` ticket.

## 10. Risks and limits, stated plainly

1. **No gate here is non-bypassable.** Hooks are local and `--no-verify` exists; CI cannot read the database; GitHub cannot block a push without branch protection (O-5). The guarantee is **detection within one sync, a hold on the ticket, and a signed check at publication** — the specification says "detected and held", never "enforced".
2. **The detector is weak.** Of 72 confirmed `OVERLAPS`, 9 share at least one file path cited in both tickets' texts and 1 shares two; the existing signals S2, S3 and S4 recover 15 %, 11 % and 12 % of the 533 explicit pairs (Appendix C, C2). The commit gate is a prompt to decide on high-precision pairs; `backlog_links.py --for` at the start of each ticket and the review sweeps remain the real protection. T1 is nearly blind (1 of 72), which is why retirement is automatic.
3. **Owner adjudication is a standing cost.** About 3 new undecided pairs a day in October (83 in 26 days). In `BLOCK` mode the owner's availability gates publication, not agent work. The levers are batch review, the queue alerts and tightening to T1.
4. **Replay figures are estimates** (§2). Thresholds (10 %, 5 %, 30 %, sample floors) are calibrated by the first 14 days of live audit data.
5. **Local `master` can run ahead of `origin`** with undecided commits; holds stop their release and closure, the pre-push hook and CI stop publication.
6. **Single signer.** Key loss blocks pushes until a new `allowed_signers` is committed (§6.4.7).
7. **Nonce forgery.** Any session holding the writer DSN could insert a `gate_check` row; the sync recomputes the tree hash, so a nonce is not reusable across commits and forging needs an explicit false statement. It is a deterrent, not a lock.
8. **A new secret.** The optional GitHub token is read-only and kept outside the repository; without it the protection state is "unverified", visibly.
9. **Raw SQL by habit.** Until the grant is revoked, ad hoc session SQL still works; the shadow stage measures it, and the revoke follows the count, not the calendar.
10. **Stale statuses outside this design.** FND-0193 (§5.1) shows that a status can lag the facts; `V-STALE-STATUS` is not added: `backlog_hygiene.py --stale-status` stays the on-demand tool, as decided in FND-0323.

## 11. Acceptance criteria and success metrics

| Phase / ticket | Done when |
|---|---|
| FND-0330 | every author string in `backlog_logs` has an alias; a test fails on a new one |
| FND-0331 | **A-1 accepted** when `backlog_phase_a_acceptance.py --verify a1` gives `ACCEPTED`: migration exit code 0; `backlog_owner` exists, is not a superuser and cannot log in; the routines are `SECURITY DEFINER`, owned by `backlog_owner`, with a pinned `search_path`; the audit tables belong to `backlog_owner`; every ticket of the baseline has a `BACKFILL` event; the history up to the baseline ids is identical; the writer lost nothing and cannot write the audit tables; the shadow trigger exists. **A-2 accepted** when `--verify a2` gives `ACCEPTED`: exit code 0 (and the script refused before 50 transitions); the writer cannot update the governed columns, insert tickets or write the audit tables, and keeps exactly E1–E3 and the routines; the author-check triggers exist; history identical. **Phase A complete** only when, besides both steps: the single-door test passes; the `close_release.py` dry run on a live `READY_FOR_DEPLOY` ticket equals today's result; the documents and the three skills of §6.2 are changed in the same release and the AGENTS.md sentinel and split checks are clean; every test of the phase is green (hermetic and pure); evidence of each step is in the FND-0331 log |
| FND-0333 | the gate replay refuses exactly the delivery closures with an open prerequisite on that day; the flag replay has median ≤ 2 per closure; step 0 dry run lists every row of §6.6 and changes nothing reversible |
| FND-0332 | `ap_status_read_ts` populated for new snapshots; `V-STALE-READ` and the closure-reaction check appear in the cycle report |
| FND-0334 | hooks install and fail open; the attestation verifies in CI with the owner's key; `AUDIT` runs 14 days; the calibrator and retirement are covered by replay tests; `scripts/audit/backlog_gate_replay.py` reproduces Appendix C |
| FND-0335 | every code of §7 has a row source; `v_backlog_violations` reproduces the findings of §5.1 on 2026-10-26 data |

| Metric | Today | Target |
|---|---|---|
| Status changes through the procedure | 0 % | 100 % (`V-00` = 0) |
| `CODE_FIX`/`DATA_FIX` closures with `deployment_ts` / with `release_version` (of 208) | 75 % / 54 % | 100 % |
| Ticket pairs that cite each other and are linked | 38 % | ≥ 70 % (enforced manually if missed after Phases A and B) |
| Delivery closures with an open prerequisite and no `p_force` | 1 | 0 |
| `BLOCKED` tickets without a blocker link or structured blocker (Q7) | 3 of 4 | 0 |
| Forced transitions | not recorded | < 5 % |
| Commits that bypassed the hook, after day 14 | not measured | 0 per week |
| Share of commits introducing an undecided pair, before `BLOCK` | 22 % (T0, replay) | ≤ 10 % |
| Median age of the adjudication queue | not measured | ≤ 2 days |

## 12. Tickets and dependencies

| Ticket | Title (short) | Priority | Depends on |
|---|---|---|---|
| FND-0330 | Actor alias | HIGH | — |
| FND-0331 | Phase A: transition kernel | HIGH | FND-0330 |
| FND-0332 | Decision-time read rule, `V-STALE-READ` | MEDIUM | — |
| FND-0333 | Phase B: relationship semantics | MEDIUM | FND-0331 |
| FND-0334 | Phase C: system sync | MEDIUM | FND-0331, FND-0332 |
| FND-0335 | Phase D: groups, violations, metrics | MEDIUM | FND-0331 (confirmed); FND-0333 (proposed, for the owner's review) |

`DEPENDS_ON` events were recorded `CONFIRMED` for the dependencies the owner approved and `PROPOSED` for FND-0335 → FND-0333; four `RELATED` events (FND-0331 → FND-0264, FND-0334 → FND-0314 and FND-0323, FND-0332 → FND-0239) are `PROPOSED` for the next review sweep, as links are decided by a person.

## 13. Documents each phase changes

| Phase | Document changes |
|---|---|
| A | `AGENTS.md` backlog paragraph; `NORMAS_BACKLOG.md` §2 (procedure replaces "INSERT"); `NORMAS_IMPLEMENTACION.md` §8 switches; `.claude/skills/*` that open or move tickets |
| B | `NORMAS_BACKLOG.md` §3 (link types, gate: "advisory" becomes "gate on delivery closures"), §4 |
| C | `NORMAS_BACKLOG.md`: commit-time path, adjudication, modes, retirement, protocols (§6.4.7), owner actions; `GOBIERNO_CONTROLES.md` entry for the gate; `scripts/audit/AGENTS.md` for the hooks |
| D | `NORMAS_BACKLOG.md` §4.1 (rules mapped to violation codes), cycle report lines |

---

## Appendix A — Decision log

Where a later row disagrees with an earlier one, the later row wins.

| Round | Directive | Disposition and evidence |
|---|---|---|
| Plan | Scope: proposal only | Accepted; no code or DDL in the session |
| Plan | Gate: hard with forced override | Accepted; later narrowed to delivery closures |
| Plan | Commit sync: suggest only | Superseded by round 3 (automatic forward moves) |
| 1 | Backfill must not feed metrics | Accepted: cycle time and RFD-wait exclude `BACKFILL` |
| 1 | Shadow stage before revoking `UPDATE` | Accepted |
| 1 | Replace the commit parser by a hook | Accepted as a commit-msg hook; the reader stays |
| 1 | Cross-database staleness | Partly rejected: consumers read live; the real gap is reaction (G11) |
| 1 | Four cluster types | Accepted: one `group_tag` |
| 1 | Rollback strategy | Accepted: one transaction and a `down` script per phase |
| 1 | Polling SLA and cron | Reworked: the decision-time read rule, no cron |
| 1 | Normalise 12 agent spellings (13 measured) | Accepted without rewriting history: `actor_alias` |
| 1 | Sweep `PRJ-NAT-NUM` ids | Rejected: 0 such ids in the repository; 438 files cite `FND-nnnn` |
| 2 | Kill the optional "C2" | Accepted: rejected from this epic |
| 2 | Stop weighing the build size | Accepted |
| 2 | Retroactive ticketing as a one-time tool | Accepted differently: no retroactive path exists; the 86 historical cases are `CLOSED` rows covered by the backfill; `V-RETRO` and `V-FAST` detect repeats |
| 3 | Atomic deploy of API, skills and documents | Accepted: Phase A is one change, enforced by the single-door test |
| 3 | Collision hook that blocks commits | Measured: as written it blocks 68 % of commits; narrowed rule 27 %; later moved to publication (round 5) |
| 3 | Automatic forward transitions | Accepted with guards (`Refs:` trailer only, `OPEN`/`TODO` → `IN_PROGRESS`) |
| 3 | Shadow exit by volume | Accepted: 50 consecutive non-system transitions |
| 3 | Pre-migration patch reversing the FND-0193 dependency errors | Mechanism accepted, premise corrected: the links are correct; five of the six rows are legitimate `REJECTED` closures; the gate scope became delivery closures |
| 3 | Review-flag baseline | Measured: median 0; no consolidation needed |
| 4 | Adjudication by a different actor | Accepted with a separate database role (`backlog_adjudicator`); the `Overlap:` trailer is removed |
| 4 | Force a bypassing ticket to `BLOCKED` | Violation accepted, sanction changed to a `hold` (§6.1 principle 3) |
| 4 | Move the forward transition into the hook | Accepted, in the commit-msg hook |
| 4 | Audit mode for 14 days | Accepted; exit criterion replaced (22 % of commits keep creating new pairs, so clearing the backlog of pairs alone cannot reach 10 %) |
| 5 | Quarantine branches for agents | Goal accepted, mechanism changed to a publication gate (242 commits straight on `master` since 2026-10-01, one shared working tree, no ad hoc worktrees) |
| 5 | Offline CI validation | Accepted with an owner-signed attestation (an unsigned hash is forgeable) |
| 5 | Automatic calibration | Accepted over a fixed ladder T0/T1 |
| 5 | Automatic downgrade to `AUDIT` on a stale queue | **Rejected by the owner:** a gate that unlatches under load is not a gate; the 48 h / 96 h alerts apply |
| 6 | Automatic retirement of a failing gate | Accepted with sample floors, in `AUDIT` too, through `gate_state` events |
| 6 | Forward-only reverts; no rebase or reset on `master` | Accepted with five additions (§6.4.7) |
| 6 | Emergency path and key loss | Accepted; the GitHub dependency stated (O-5) |
| 6 | Historical data debt in step 0 | Accepted, append-only; corrected facts: FND-0029 does name its blocker |
| 7 | `V-GATE-UNPROTECTED` | Accepted through the GitHub API only; local git configuration cannot see remote protection; `V-GATE-UNVERIFIED` added |
| 7 | FND-0041 → `TODO`; uniform 90-day revisit for six `DEFERRED` | Accepted; the 90-day rows are marked `DEFAULT_90D` |
| 8 | D1: a non-superuser `backlog_owner` owns the privileged routines; `search_path` pinned | Accepted. Found in the live catalogue: every backlog object is owned by `fondos_owner`, a superuser, and `sp_close_release` ran as its caller, so the revocation would have broken `close_release.py` unless it is rebuilt as a definer routine first |
| 8 | D2: A-1 (additive) apart from A-2 (revocation), A-2 after 50 non-system transitions without a direct update | Accepted; the migration script itself refuses A-2 until `v_shadow_stage` shows 50 |
| 8 | D3: writes on state, closure, release, identity and audit through controlled routines; exceptions enumerated and justified before the revocation | Accepted: E1 logs, E2 link events, E3 descriptive columns (§6.2) |
| 8 | D4: update the three obsolete audit skills in the same change | Accepted; their §8 and pre-flight line pointed at the HTML artifact retired on 2026-09-21 |
| 8 | D5: explicit actor, `FONDOS_ACTOR` as a validated alternative | Accepted; never guessed from the OS user |
| 8 | D6: `sp_open_ticket` and `sp_transition` as functions | Accepted |
| 8 | Found while building A-1 | The first draft marked the routines' writes with a transaction-wide setting, so a raw write after any routine call in the same transaction escaped the shadow stage; fixed with a function-level `SET`, with a regression test. Event timestamps use the clock, not the transaction start, so events of one transaction stay ordered |

**Errata of the analysis, recorded for the reader:** the link-event total is 265 (an earlier draft said 277); the number of past-gate delivery closures is 1, not 6; FND-0203 is not stale (its blocker is still open); FND-0029 does name its blocker; the agent author spellings are 13 strings, not 12.

## Appendix B — Registry queries (read-only), Q1–Q22

All run on 2026-10-26 with `default_transaction_read_only=on` and `statement_timeout=30000`. Q1, Q2, Q3 and Q12–Q15 are restricted to the original population (ids up to FND-0329); Q18 is restricted by date (before 2026-10-26).

**Q1. Tickets by status**

```sql
SELECT status, count(*) FROM backlog.backlog WHERE action_point_id <= 'FND-0329' GROUP BY 1 ORDER BY 2 DESC
```

Result 2026-10-26 (columns: status, count):

```
CLOSED | 233
OPEN | 30
IN_PROGRESS | 18
DEFERRED | 6
BLOCKED | 4
TODO | 3
READY_FOR_DEPLOY | 1
```

**Q2. Totals (tickets, logs, link events)**

```sql
SELECT (SELECT count(*) FROM backlog.backlog WHERE action_point_id <= 'FND-0329'), (SELECT count(*) FROM backlog.backlog_logs WHERE action_point_id <= 'FND-0329'), (SELECT count(*) FROM backlog.backlog_link_event WHERE from_id <= 'FND-0329' AND to_id <= 'FND-0329')
```

Result 2026-10-26 (columns: count, count, count):

```
295 | 1265 | 265
```

**Q3. Closure integrity**

```sql
SELECT
 count(*) FILTER (WHERE status='CLOSED' AND solution_type IN ('CODE_FIX','DATA_FIX')) AS fix_closures,
 count(*) FILTER (WHERE status='CLOSED' AND solution_type IN ('CODE_FIX','DATA_FIX') AND deployment_ts IS NULL) AS fix_no_deployment_ts,
 count(*) FILTER (WHERE status='CLOSED' AND solution_type IN ('CODE_FIX','DATA_FIX') AND release_version IS NULL) AS fix_no_release,
 count(*) FILTER (WHERE session_id IS NULL) AS no_session,
 count(*) FILTER (WHERE assignee IS NULL) AS no_assignee,
 count(*) FILTER (WHERE object_type = 'N/A') AS object_type_na
 FROM backlog.backlog WHERE action_point_id <= 'FND-0329'
```

Result 2026-10-26 (columns: fix_closures, fix_no_deployment_ts, fix_no_release, no_session, no_assignee, object_type_na):

```
208 | 53 | 96 | 95 | 293 | 191
```

**Q4. Closure path and release label format (CODE_FIX / DATA_FIX closures)**

```sql
SELECT
 count(*) FILTER (WHERE EXISTS (SELECT 1 FROM backlog.backlog_logs l WHERE l.action_point_id=b.action_point_id AND l.comment LIKE 'Closed by sp_close_release%')) AS via_sp_close_release,
 count(*) AS fix_closures,
 count(*) FILTER (WHERE release_version IS NOT NULL AND NOT release_version ~ '^R[0-9]{4}[.][0-9]{2}-') AS nonstandard_release_label,
 count(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM backlog.backlog_logs l WHERE l.action_point_id=b.action_point_id AND l.comment ILIKE '%READY_FOR_DEPLOY%')) AS never_mention_rfd
 FROM backlog.backlog b WHERE status='CLOSED' AND solution_type IN ('CODE_FIX','DATA_FIX')
```

Result 2026-10-26 (columns: via_sp_close_release, fix_closures, nonstandard_release_label, never_mention_rfd):

```
92 | 208 | 25 | 153
```

**Q5. Registered after the work (created and closed within one hour)**

```sql
SELECT count(*) FROM backlog.backlog WHERE status='CLOSED' AND closure_ts - creation_ts < interval '1 hour'
```

Result 2026-10-26 (columns: count):

```
86
```

**Q6. Closed while a confirmed DEPENDS_ON prerequisite is still open**

```sql
SELECT from_id, solution_type, to_id, to_status FROM backlog.v_open_link_graph g JOIN backlog.backlog b ON b.action_point_id=g.from_id WHERE g.from_status='CLOSED' AND g.link_type='DEPENDS_ON' AND g.to_status<>'CLOSED' ORDER BY 1
```

Result 2026-10-26 (columns: from_id, solution_type, to_id, to_status):

```
FND-0160 | REJECTED | FND-0193 | IN_PROGRESS
FND-0161 | REJECTED | FND-0193 | IN_PROGRESS
FND-0162 | REJECTED | FND-0193 | IN_PROGRESS
FND-0163 | REJECTED | FND-0193 | IN_PROGRESS
FND-0223 | CODE_FIX | FND-0193 | IN_PROGRESS
FND-0258 | REJECTED | FND-0193 | IN_PROGRESS
```

**Q7. BLOCKED tickets and whether they have an open DEPENDS_ON prerequisite**

```sql
SELECT b.action_point_id, EXISTS (SELECT 1 FROM backlog.v_open_link_graph g WHERE g.from_id=b.action_point_id AND g.blocked_by_open) AS has_open_prereq_link FROM backlog.backlog b WHERE b.status='BLOCKED' ORDER BY 1
```

Result 2026-10-26 (columns: action_point_id, has_open_prereq_link):

```
FND-0029 | False
FND-0041 | False
FND-0158 | True
FND-0203 | False
```

**Q8. DUPLICATED closures and whether a confirmed DUPLICATES link exists**

```sql
SELECT b.action_point_id, EXISTS (SELECT 1 FROM backlog.v_backlog_link k WHERE k.from_id=b.action_point_id AND k.link_type='DUPLICATES' AND k.status='CONFIRMED') AS linked FROM backlog.backlog b WHERE b.solution_type='DUPLICATED' ORDER BY 1
```

Result 2026-10-26 (columns: action_point_id, linked):

```
FND-0022 | False
FND-0038 | False
FND-0088 | True
```

**Q9. WORKAROUND closures and whether they have any confirmed link**

```sql
SELECT b.action_point_id, EXISTS (SELECT 1 FROM backlog.v_backlog_link k WHERE (k.from_id=b.action_point_id OR k.to_id=b.action_point_id) AND k.status='CONFIRMED') AS any_link FROM backlog.backlog b WHERE b.solution_type='WORKAROUND' ORDER BY 1
```

Result 2026-10-26 (columns: action_point_id, any_link):

```
FND-0166 | True
FND-0320 | False
```

**Q10. DEFERRED tickets (age in days)**

```sql
SELECT action_point_id, round(extract(epoch FROM now()-creation_ts)/86400) AS age_days FROM backlog.backlog WHERE status='DEFERRED' ORDER BY 1
```

Result 2026-10-26 (columns: action_point_id, age_days):

```
FND-0033 | 64
FND-0035 | 64
FND-0078 | 31
FND-0167 | 25
FND-0185 | 23
FND-0209 | 22
```

**Q11. Tickets whose logs mention a reopening (text match, weak)**

```sql
SELECT count(DISTINCT action_point_id) FROM backlog.backlog_logs WHERE comment ~* '(re-?open|reabiert|reabre)'
```

Result 2026-10-26 (columns: count):

```
12
```

**Q12. Log statistics**

```sql
SELECT round(avg(length(comment))) AS avg_chars, count(*) FILTER (WHERE length(comment)>3000) AS over_3000, count(*) FILTER (WHERE comment ~ '(OPEN|TODO|IN_PROGRESS|READY_FOR_DEPLOY|BLOCKED|DEFERRED|CLOSED)') AS mention_a_status, count(*) AS logs FROM backlog.backlog_logs WHERE action_point_id <= 'FND-0329'
```

Result 2026-10-26 (columns: avg_chars, over_3000, mention_a_status, logs):

```
699 | 25 | 274 | 1265
```

**Q13. Distinct author spellings in logs**

```sql
SELECT username, count(*) FROM backlog.backlog_logs WHERE action_point_id <= 'FND-0329' GROUP BY 1 ORDER BY 2 DESC
```

Result 2026-10-26 (columns: username, count):

```
claude | 534
jacor | 227
claude-code | 194
CLAUDE_SESSION | 102
claude-opus-5-5 | 59
claude_p3_assessment | 56
LEGACY_HTML_MIGRATION | 54
SYSTEM_AUTODETECT | 13
SYSTEM_RELEASE | 9
CLAUDE_DEV_SESSION | 8
claude-sonnet-5 | 5
CLAUDE_CODE | 3
backlog_writer | 1
```

**Q14. Link events and current links by type and state**

```sql
SELECT link_type, status, count(*) FROM backlog.v_backlog_link WHERE from_id <= 'FND-0329' AND to_id <= 'FND-0329' GROUP BY 1,2 ORDER BY 1,2
```

Result 2026-10-26 (columns: link_type, status, count):

```
DEPENDS_ON | CONFIRMED | 19
DEPENDS_ON | PROPOSED | 2
DUPLICATES | CONFIRMED | 1
OVERLAPS | CONFIRMED | 72
OVERLAPS | REJECTED | 37
RELATED | CONFIRMED | 100
RELATED | PROPOSED | 8
RELATED | REJECTED | 1
RELEASE_WITH | CONFIRMED | 6
SPLIT_FROM | CONFIRMED | 16
SPLIT_FROM | PROPOSED | 3
```

**Q15. Ticket pairs that cite each other (title, description, logs) versus pairs with a CONFIRMED link**

```sql
WITH m AS (
  SELECT b.action_point_id src, (regexp_matches(coalesce(b.title,'')||' '||coalesce(b.scenario_description,''),'FND-[0-9]{4}','g'))[1] dst FROM backlog.backlog b
  UNION SELECT l.action_point_id, (regexp_matches(l.comment,'FND-[0-9]{4}','g'))[1] FROM backlog.backlog_logs l),
 p AS (SELECT DISTINCT least(src,dst) a, greatest(src,dst) b FROM m WHERE src<>dst AND src <= 'FND-0329' AND dst <= 'FND-0329'),
 k AS (SELECT DISTINCT least(from_id,to_id) a, greatest(from_id,to_id) b FROM backlog.v_backlog_link WHERE status='CONFIRMED' AND from_id <= 'FND-0329' AND to_id <= 'FND-0329')
 SELECT (SELECT count(*) FROM p) AS mention_pairs, (SELECT count(*) FROM p JOIN k USING (a,b)) AS linked_pairs
```

Result 2026-10-26 (columns: mention_pairs, linked_pairs):

```
534 | 201
```

**Q16. Privileges of the connecting role on the backlog schema**

```sql
SELECT current_user, table_name, string_agg(privilege_type, ',' ORDER BY privilege_type) FROM information_schema.role_table_grants WHERE table_schema='backlog' AND grantee=current_user GROUP BY 2 ORDER BY 2
```

Result 2026-10-26 (columns: current_user, table_name, string_agg):

```
backlog_writer | backlog | INSERT,SELECT,UPDATE
backlog_writer | backlog_link_event | INSERT,SELECT
backlog_writer | backlog_logs | INSERT,SELECT
backlog_writer | v_backlog_link | SELECT
backlog_writer | v_open_link_graph | SELECT
```

**Q17. Confirmed OVERLAPS: with and without an `absorbs` note**

```sql
SELECT count(*) FILTER (WHERE absorbs IS NOT NULL) AS with_absorbs, count(*) AS overlaps FROM backlog.v_backlog_link WHERE status='CONFIRMED' AND link_type='OVERLAPS'
```

Result 2026-10-26 (columns: with_absorbs, overlaps):

```
10 | 72
```

**Q18. Link events by author and period (before the six v2 tickets were opened)**

```sql
SELECT username, min(event_ts)::date AS first_day, max(event_ts)::date AS last_day, count(*) FROM backlog.backlog_link_event WHERE event_ts < '2026-10-26' GROUP BY 1 ORDER BY 4 DESC
```

Result 2026-10-26 (columns: username, first_day, last_day, count):

```
jacor | 2026-10-11 | 2026-10-17 | 236
claude | 2026-10-18 | 2026-10-25 | 17
claude-code | 2026-10-25 | 2026-10-25 | 12
```

**Q19. Logs that declare a closing condition (text match, weak)**

```sql
SELECT count(*) FROM backlog.backlog_logs WHERE comment ~* '(cerrar|close|closes|cierra) (tras|after|when|cuando)'
```

Result 2026-10-26 (columns: count):

```
29
```

**Q20. Status today of nine tickets listed in the hard-coded SPEC of rfd_matrix_probe.py**

```sql
SELECT status, count(*) FROM backlog.backlog WHERE action_point_id IN ('FND-0073','FND-0181','FND-0196','FND-0200','FND-0203','FND-0235','FND-0240','FND-0241','FND-0242') GROUP BY 1 ORDER BY 1
```

Result 2026-10-26 (columns: status, count):

```
BLOCKED | 1
CLOSED | 8
```

**Q21. Uses of the structured label 'DECISION PENDIENTE' (NORMAS_BACKLOG 4.1 rule 2)**

```sql
SELECT count(*) FROM backlog.backlog_logs WHERE comment ILIKE '%DECISION PENDIENTE%'
```

Result 2026-10-26 (columns: count):

```
0
```

**Q22. Review flags each historical closure would have raised: far ends of confirmed OVERLAPS with an absorbs note that were still open or closed later**

```sql
WITH e AS (
  SELECT from_id a, to_id b FROM backlog.v_backlog_link WHERE status='CONFIRMED' AND link_type='OVERLAPS' AND absorbs IS NOT NULL
  UNION ALL
  SELECT to_id, from_id FROM backlog.v_backlog_link WHERE status='CONFIRMED' AND link_type='OVERLAPS' AND absorbs IS NOT NULL),
 n AS (SELECT b.action_point_id, count(e.b) FILTER (WHERE o.closure_ts IS NULL OR o.closure_ts > b.closure_ts) AS k
       FROM backlog.backlog b LEFT JOIN e ON e.a = b.action_point_id LEFT JOIN backlog.backlog o ON o.action_point_id = e.b
       WHERE b.status='CLOSED' GROUP BY 1)
 SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY k) AS median_flags, max(k) AS max_flags, count(*) FILTER (WHERE k>0) AS closures_raising_any, count(*) AS closures FROM n
```

Result 2026-10-26 (columns: median_flags, max_flags, closures_raising_any, closures):

```
0.0 | 2 | 7 | 233
```


## Appendix C — Replay and measurement definitions

These figures come from session scripts that are not yet in the repository; FND-0334 delivers `scripts/audit/backlog_gate_replay.py` to reproduce them.

**C1. Collision replay.** *Population:* the commits since 2026-10-01 whose subject cites at least one `FND-nnnn` (217 in the final replay; the first pass counted 219 with a differently written script, and the two-commit difference is unexplained and does not change a conclusion). *Open set:* tickets not `CLOSED` today, with title, description and all logs as text. *Pair rule:* a commit's non-`.md`/`.txt` files, each looked up by full repo path in other tickets' text. *First rule* (basename match, any open ticket): blocks 150 of 219 commits (68 %). *T0* (full path, other ticket `IN_PROGRESS`, files cited by 5 or more open tickets ignored, no confirmed link): 59 of 217 commits (27 %) would be blocked, 83 distinct pairs, 47 commits (22 %) introduce a pair not seen earlier in the replay; new pairs by half-month: 49 and 34. *T1* (at least 2 shared files): 9 pairs, 6 commits (3 %). Hub threshold 3 gave the same result as 5.

**C2. Recall.** *Confirmed `OVERLAPS`* (72): 9 share at least one repo file path cited in both tickets' texts (12 %), 1 shares at least two; among the 10 with an `absorbs` note, 4 and 1. *Rejected `OVERLAPS`* (37): 1 shares one path. *`backlog_links.py --evaluate`* on 2026-10-26: 533 explicit (S1) pairs; recall of S2 15 % (threshold 0.3, 79 pairs), S3 11 % (0.25, 56), S4 12 % (0.35, 64); precision of past proposals 85 % (252 judged); 531 unreviewed candidates, 213 from S2–S4 alone.

**C3. Review-flag baseline.** Q22.

**C4. Commits versus ticket status.** Commits since 2026-09-21 (340; 278 cite an `FND` in the subject), subject ids only. Six `OPEN`/`TODO` tickets have commits (FND-0021, 0034, 0259, 0276, 0280, 0285), and for four of them (FND-0259, 0276, 0280, 0285) every commit is dated the ticket's creation day, so the subject alone cannot tell the commit that opened the ticket from follow-on work; three tickets received commits after `closure_ts` (FND-0071, 0265, 0300); 17 of the 18 `IN_PROGRESS` tickets have commits. This is why automatic forward moves require the `Refs:` trailer, a deliberate statement, and never a subject mention.
