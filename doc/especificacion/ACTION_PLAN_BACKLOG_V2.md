# Backlog v2 — analysis, history audit, and evolution proposal

## Context
The owner wants `gestion.backlog` to manage and link action points better, and to keep ticket status in step with what the
system does (commits, releases, switch flips, cycles, audit incidents, promotions). Asked for: (1) an analysis of the current
system's weaknesses, risks and gaps; (2) a review of the history for bad practice; (3) a review of the other project's
backlog v4 docs (BigQuery: `pr_bl_ap_transition`, BLOCKS/IMPACTS/SHARES_OBJECT/SUPERSEDES, clusters, violation views,
lean metrics) for practices worth adopting; (4) an evolution proposal.

**Precedence rule:** this file grew by review rounds. Where two sections disagree, the later one wins (§5f > §5e > §5d > §5b/§5c >
§5). The deliverable (§7.2) is a single, consolidated specification with the round history reduced to a decision-log appendix;
nobody implements from the layered text.

## 1. Current system (as read, 2026-10-26)
- Store: `db/pg_backlog/00_schema.sql` (BACKLOG, BACKLOG_LOGS), `10_kpis_and_release.sql` (`vw_backlog_kpis`, `sp_close_release`),
  `20_links.sql` (append-only `backlog_link_event`, `v_backlog_link`, `v_open_link_graph`).
- Code: `shared/backlog_client.py` (incident capture + read API + `append_log` + `record_link_events`; **no create / transition API**),
  `scripts/audit/backlog_links.py` (S1–S4 discovery, review CSV), `scripts/audit/backlog_hygiene.py` (on-demand premise sweep,
  stale status), `scripts/ops/close_release.py` (raw `UPDATE … status`, then `sp_close_release`), `scripts/audit/rfd_matrix_probe.py`
  (closing conditions **hard-coded in a Python dict `SPEC`**).
- Consumers in `fondos` that hold an `ap_id` (cross-database, pull only): `control.cycle_flag`, `control.audit_incident`,
  `control.audit_accepted_finding`, P3 promotion override (`scripts/launch/p3_promote_scenario.py`), cycle report.
- Role `backlog_writer`: INSERT/SELECT/**UPDATE on `backlog`** (any column), INSERT/SELECT only on logs and link events.

## 2. Weaknesses, risks, gaps (structural)
| # | Gap | Consequence |
|---|---|---|
| G1 | Status, closure fields, priority are plain `UPDATE`s; no transition API, no status history | Transitions are untraceable (only 274/1265 logs even mention a status); cycle time cannot be computed; illegal jumps are possible |
| G2 | `DEPENDS_ON` is advisory by design ("no trigger blocks") | 6 tickets CLOSED while their prerequisite FND-0193 is IN_PROGRESS. Verified 10-26: 5 of them are REJECTED as moot after FND-0193's verdict (legitimate); only **FND-0223 (CODE_FIX)** is a delivery closure past an open prerequisite |
| G3 | Link types lack *notification* semantics (no IMPACTS / SUPERSEDES, OVERLAPS has no propagation) | A change in A never puts B on review; premise sweep is manual and on-demand |
| G4 | Links are recorded only by an offline review cycle | 534 ticket pairs cross-cite each other; only 201 (38 %) are linked. 236 link events in one owner burst (10-11..10-17), then 29 in 8 days: discipline decays |
| G5 | No ticket↔physical-object registry (`object_type` = `N/A` on 191/295; one `object_name` max) | Overlap must be guessed by text signals (S2/S3) instead of known. **Not addressed in this epic** (C2 rejected); manual enforcement |
| G6 | No sync from system operations to the backlog: commits, switch flips, releases, promotions never write an event | 6 OPEN/TODO tickets already have commits; 3 tickets received commits after `closure_ts` (silent reopen); 17/18 IN_PROGRESS have commits but no RFD signal |
| G7 | Closing conditions live in free text (29 logs) and in `rfd_matrix_probe.SPEC` (Python) | Not queryable; adding a ticket to the matrix is a code change; 8 SPEC tickets closed, table now stale |
| G8 | No grouping object (train / theme / owner decision) | "Locked recalculation batch", `RELEASE_WITH` pairs and owner holds are tracked in prose; "DECISION PENDIENTE" used 0 times |
| G9 | No violation views; hygiene is procedural (§4.1) | Every check below had to be written ad hoc |
| G10 | Actor identity is free text | 12 spellings for the agent (`claude`, `claude-code`, `CLAUDE_SESSION`, `claude-opus-5-5`, …); 95/295 tickets have no `session_id`; `assignee` null on 293/295 |
| G11 | Nothing reacts to a status change (consumers do read status live at their decision points) | A residual accepted under a ticket that just closed, or a cycle flag whose ticket closed, is noticed only at the next cycle report |

## 3. Historical bad practice (live data, read-only, 295 tickets / 1265 logs / 277 link events)
- **Retroactive registration:** 86 tickets created and closed within one hour (work done first, ticket written after → the
  "check neighbours before opening" step never ran).
- **Release discipline:** of 208 CODE_FIX/DATA_FIX closures, only 92 went through `sp_close_release`; 53 lack `deployment_ts`,
  96 lack `release_version`; 28 rows use non-standard labels (`v23`, `20261004`, `v2026.10.01`); 149 CODE_FIX closures never
  mention READY_FOR_DEPLOY (step skipped).
- **Gate ignored:** 1 delivery closure with an open `DEPENDS_ON` prerequisite (FND-0223 → FND-0193). The other five
  (FND-0160..0163, 0258) are REJECTED closures, which are legitimate. The links are correct (owner directive 2026-10-17).
- **BLOCKED without a blocker link:** 3 of 4. FND-0203 is *not* stale: its blocker FND-0236 is still IN_PROGRESS, but the
  dependency exists only as text. FND-0041 names no blocking condition (§4.1 rule 2). **Errata 2026-10-26:** FND-0029 does name one ("BLOCKED: external source; re-check after each harvest"); it only lacks the structured field (§5i).
- **DUPLICATED without link:** 2 of 3 (FND-0022, FND-0038). WORKAROUND closures (FND-0166, FND-0320): 1 has any link, no
  structured OPT follow-up.
- **Reopen untracked:** 12 tickets mention reopening in logs; no structured reopen event (FND-0034 reopened 10-25).
- **DEFERRED without revisit trigger:** 6 DEFERRED (avg 38 d old); 2 still carry only the legacy-migration note.
- **Logs as essays:** avg 699 chars, 25 logs > 3,000 chars — structured facts (counts, conditions, decisions) buried in prose.
Full query set reproduced in the proposal appendix (SQL kept in `backlog_client` / the new violations view, per §8 of NORMAS).

## 4. What to adopt from backlog v4 (and what not)
Adopt: single transition procedure + forbidden direct `UPDATE` (G1); hard gate at RFD/CLOSED with blocker list (G2);
force flag to reopen CLOSED (reopen event); IMPACTS → review-pending flag + ack procedure (G3); SUPERSEDES / DUPLICATES →
closure suggestion; cycle check on relate; clusters CHANGE_TRAIN / THEME / BUSINESS_BLOCK / OBJECT_FAMILY with
"ready to close" view (G8); violation views for domain / relations / clusters (G9); lean metrics (lead + cycle time, needs G1);
recursive impact-closure view (≤ 5 levels); WORKAROUND ⇒ auto-open OPT follow-up linked to it.
Do **not** adopt: `PRJ-NAT-NUM` ids (300+ `FND-nnnn` references in code, commits, `fondos` tables; nature is a column already);
nested `ARRAY<STRUCT>` log (Postgres append-only table is better); REVIEW_PENDING as a *status* (make it a flag so the
workflow status is not lost).

## 5. Proposal — Backlog v2 (phased; each phase = one ticket, one release)
**Phase A — Transition kernel (G1, G2, G10).** `db/pg_backlog/30_transitions.sql`:
- `backlog.status_event` (append-only: ap_id, from_status, to_status, actor, session_id, reason, forced, event_ts) + backfill
  one synthetic event per ticket from current state (marked `BACKFILL`).
- `backlog.sp_transition(p_ap_id, p_to, p_actor, p_session_id, p_comment, p_solution_type, p_release_version, p_force)`:
  legal-transition matrix (from NORMAS §2), closure field rules (CODE_FIX/DATA_FIX ⇒ release + deployment_ts; REJECTED /
  DUPLICATED ⇒ cited ticket and for DUPLICATED a `DUPLICATES` link), reopen only with `p_force`, gate per §B, writes the log
  row and the status event in the same transaction.
- `backlog.sp_open_ticket(...)` (id from sequence, mandatory session_id format, first log, returns id).
- Revoke `UPDATE(status, closure_ts, solution_type, deployment_ts, release_version)` from `backlog_writer`; procedures are
  `SECURITY DEFINER`. `sp_close_release` rewritten on top of `sp_transition`; `close_release.py` drops its raw UPDATE.
- `backlog_client`: `open_ticket()`, `transition()`, `current_actor()` (normalised `human:<login>` / `agent:<model>`).
**Phase B — Relationship semantics (G2, G3, G4).** `40_relations_v2.sql`: add `IMPACTS`, `SUPERSEDES` to `LINK_TYPES`
(keep the six existing; OVERLAPS ≙ SHARES_OBJECT, bidirectional notification); cycle check for `DEPENDS_ON`;
`backlog.review_flag` (append-only raise/ack) raised on the far end when a ticket reaches RFD/CLOSED through IMPACTS/OVERLAPS;
`sp_ack_review`. Gate inside `sp_transition` (scope approved 2026-10-26: READY_FOR_DEPLOY and **delivery closures** CODE_FIX/DATA_FIX/WORKAROUND only; REJECTED/DUPLICATED are never gated): refused while a confirmed `DEPENDS_ON` prerequisite is open, an open `hold` exists, or a
review flag is unacknowledged — override only with `p_force` + reason (logged, counted in violations). `--new` / open_ticket
returns the S1 mentions found in the text and proposes them as `PROPOSED` link events at write time (closes G4 at source).
**Phase C — System sync (G6, G7, G11).** (The former C2 — `ticket_object` registry and switch-flip review flags — is
**rejected from this epic** by owner directive, 2026-10-26. G5 stays under manual enforcement; any future need opens a new epic.
The §4.1 premise sweep stays as the existing on-demand tool.)
- `backlog.ops_event` (append-only: kind ∈ {COMMIT, RELEASE, PROMOTION, CYCLE_FLAG, AUDIT_INCIDENT}, ref, ap_id, payload,
  event_ts) + `scripts/audit/backlog_sync.py`: reads `git log` since the watermark (`Refs: FND-nnnn` trailers, subject ids as a
  fallback) and records events. Rules: `Refs:` commit on OPEN/TODO ⇒ automatic `transition()` to IN_PROGRESS (guards in §5d R3);
  commit after closure ⇒ violation "work on a CLOSED ticket"; **first commit citing a ticket earlier than its `creation_ts` ⇒
  violation `V-RETRO`** (work done before the ticket existed). Run from preflight and the cycle report (writes events only).
- commit-msg hook (§5b R3).
- `backlog.close_condition` (ap_id, probe_kind, args json, added_by) — `rfd_matrix_probe.py` reads it instead of `SPEC`;
  `--close-ready` lists tickets whose every probe passes.
- `backlog.v_status_feed` (status_event since a watermark) for `fondos` consumers; the cycle report reconciles
  `cycle_flag` / `audit_accepted_finding` against it each cycle (replaces ad-hoc checks in `accepted_residuals.py`).
**Phase D — Clusters, violations, metrics (G8, G9).** `backlog.cluster` + `cluster_member` (types CHANGE_TRAIN, THEME,
BUSINESS_BLOCK, OBJECT_FAMILY); `v_cluster_status` (READY_TO_CLOSE); BUSINESS_BLOCK replaces the free-text "DECISION
PENDIENTE"; `RELEASE_WITH` pairs migrate to CHANGE_TRAIN clusters. `v_backlog_violations` (one row per violation, code
`V-01..`: every check of §3 plus forced gates, BLOCKED without blocker, DEFERRED without revisit date, WORKAROUND without OPT);
`v_backlog_lean_metrics` (lead / cycle / RFD-wait time from status_event); `v_impact_closure` (recursive, ≤ 5 levels).
Cycle report shows violations count + top 3.

Governance: NORMAS_BACKLOG.md rewritten section by section with each phase (§2 state machine → procedure; §3 types; new
§ objects/sync/clusters/violations); AGENTS.md backlog paragraph updated; DDL applied by the owner via a
`migrate_backlog_v2.py` dry-run/`--apply` script mirroring `migrate_backlog_links.py`.

## 5b. Response to the risk assessment (verified 2026-10-26)
| Item | Verdict | Evidence / change to the proposal |
|---|---|---|
| R1 backfill contaminates metrics | **Accept, refined** | `v_backlog_lean_metrics` excludes `origin='BACKFILL'` events for cycle / RFD-wait time. Lead time keeps using the real `creation_ts`/`closure_ts` columns (those were recorded), so it stays valid for history |
| R2 lockout on grant revoke | **Accept** | Phase A gets a **shadow stage**: procedures set `SET LOCAL backlog.via_proc='on'`; a trigger logs any UPDATE of a status/closure column without it as violation `V-00` (not blocked). Grant revoked after 50 consecutive procedure transitions with zero `V-00` (round 3, §5d). Known raw writers today: `close_release.py`, `sp_close_release`, ad-hoc session SQL |
| R3 brittle commit parsing | **Accept, refined** | Real but small: 11 malformed ids in all history (`fnd0193`, `fnd0317`… mostly lower-case, no hyphen). Add a **commit-msg** hook (installed by the existing `scripts/audit/pre-commit.sh` pattern, FND-0314) that rejects malformed `FND` tokens and unknown ids; fails **open** with a warning when the backlog is unreachable (WSL idle-shutdown must not block commits). The reader is kept, not deprecated: a hook validates, it does not record events. Ids move to a `Refs: FND-nnnn` trailer (0 commits use one today) |
| S1 cross-DB staleness | **Partly reject** | Every consumer already reads status **live at decision time** (`get_action_point_status` in `p3_promote_scenario.py`, `accepted_residuals.py`, cycle report); only `control.cycle_flag.ap_status` stores a snapshot, with `refresh_unverified_flags`. There is no continuous consumer to keep fresh. Real gap is narrower: nothing *reacts* to a closure (e.g. residual accepted under a ticket that just closed). G11 restated accordingly |
| S2 four cluster types | **Accept** | Phase D becomes one `backlog.group_tag` (+ members) with `kind` CHECK ∈ {TRAIN, THEME, DECISION}; `v_group_status` gives READY_TO_CLOSE. `RELEASE_WITH` stays as a link type |
| S3 no rollback | **Accept, refined** | One transaction **per phase** (phases ship separately, so not one A–D block); Postgres DDL is transactional, so a mid-run failure rolls back whole — the pattern `migrate_backlog_links.py` already uses. Add a staged `down` script per phase (`DROP PROCEDURE/VIEW/TABLE`, re-GRANT), tested hermetically. No array columns exist, so no "legacy array" risk |
| AP-1 polling SLA + cron | **Refine** | No cron: consumers read at decision time (S1). Ticket scope: document the rule "status is read at the decision point; any stored copy carries its read time", and add the closure→reaction check to the cycle report. Linked to Phase C (where the feed lives), not Phase A |
| AP-2 agent identity | **Accept, refined** | Logs are append-only (grant + AGENTS rule): history is **not rewritten**. `backlog.actor_alias(raw, canonical)` maps the 12 spellings to `agent:<model>` / `human:<login>`; read views and the backfill use the canonical form. Prerequisite of Phase A |
| AP-3 PRJ-NAT-NUM sweep | **Reject** | Premise false: 0 `XXX-BUG-nnnn`-style ids in the repo; the 438 files with ticket refs already use `FND-nnnn`. (A few legacy `ACT-nn` comments in P1 exist; not worth a ticket.) `[BL-XX]` log tags are a logging convention, not ticket ids, and must not be touched |

## 5c. Self-review: remaining issues, now addressed
1. **The written rules teach the raw path.** AGENTS.md and NORMAS §2 tell sessions "INSERT into BACKLOG"; the audit skills do
   the same. Phase A must rewrite those instructions (AGENTS.md backlog paragraph, NORMAS §2, `.claude/skills/*` that open
   tickets) to use `open_ticket()` / `transition()`, or the shadow stage only records noise.
2. **No retroactive path (owner directive, 2026-10-26).** The API offers no shortcut for after-the-fact ticketing. The 86
   historical cases are already CLOSED rows, verified on the live data; the Phase A backfill gives each one its `BACKFILL` event,
   so no migration tool is needed. From deployment onward:
   - A ticket written after the work must go through every legal transition.
   - Each step is timestamped, so the shortcut is visible.
   - Two violations detect it: `V-RETRO` (first citing commit earlier than `creation_ts`, Phase C) and `V-FAST` (OPEN→CLOSED in
     under 1 h with a CODE_FIX/DATA_FIX, Phase D).
   - `p_force` on these transitions needs a reason and is counted.
3. **Gate replay before go-live** (same idea as the D-1 calibration gate). Replay the hard gate over history; the
   acceptance criterion is to refuse only FND-0223 (verified, §5d R5).
4. **Review-flag fatigue.** Propagation applies only to `IMPACTS` and to `OVERLAPS` links that carry `absorbs`, not to all
   72 OVERLAPS or the 100 RELATED links. A replay counts the flags each historical closure would have raised; the target is a
   median of ≤ 2 per closure.
5. **Scope final (owner directive, 2026-10-26).** Phases A, B, C and D are approved as written; the C2 work was rejected
   (see Phase C). If the linked-mentions target is missed after A+B, it is enforced manually until a new epic is opened.
6. **Success metrics for the proposal itself** (each re-measured with the appendix query):

   | Metric | Today | Target |
   |---|---|---|
   | Status changes through the procedure | 0 % | 100 % (`V-00` = 0) |
   | CODE_FIX/DATA_FIX closures with deployment_ts / with release_version | 75 % / 54 % (of 208) | 100 % |
   | Mention pairs that are linked | 38 % | ≥ 70 % |
   | Delivery closures (CODE_FIX/DATA_FIX/WORKAROUND) with an open prerequisite and no `p_force` | 1 | 0 |
   | BLOCKED tickets without a blocker | 3/4 | 0 |
   | Forced transitions | n/a | < 5 % |

## 5d. Round-3 directives (owner, 2026-10-26): verified disposition
| Directive | Disposition | Evidence / final design |
|---|---|---|
| R1 atomic skill + DB deploy | **Adopt** | Phase A ships as one branch: client API + `close_release.py` + `report_incident` on the procedures + AGENTS.md + NORMAS §2 + every `.claude/skills/*` that writes tickets. Order: the owner applies the additive DDL first, then merges the same day. New static test `tests/test_backlog_single_door.py` (pattern of `test_dialect_coverage.py`) fails on any `INSERT INTO backlog.backlog` / `UPDATE backlog.backlog` outside `backlog_client.py` and `db/pg_backlog/`. Agents call the Python client, not the SPs directly |
| R2 collision hook that blocks | **Adopt, with a narrower rule** | Measured on 219 FND commits since 10-01 (approximation: today's open set). The rule as written blocks **68 %**, which is unworkable. Narrow rule: full repo path, code files only, the other ticket is IN_PROGRESS, files cited by ≥ 5 open tickets ignored, no confirmed link. It blocks **27 %**. Final design: a **commit-msg** hook (it needs both the message and the staged files) blocks on the narrow rule. The commit clears the block with a trailer `Overlap: FND-x confirmed\|none`; `backlog_sync` turns the trailer into a CONFIRMED OVERLAPS or a REJECTED link event. A decided pair never blocks again, so the rate falls as pairs get decided, and G4 is fixed at the source. Fails open when the backlog is unreachable |
| R3 auto forward transitions | **Adopt, with guards** (overrides the earlier "suggest only") | Only from a `Refs: FND-nnnn` trailer, never from a mention in the subject. Of the 6 OPEN tickets with commits, 4 got their commits on their creation day: "opened" mentions, not work. Allowed moves: OPEN/TODO → IN_PROGRESS only; never from BLOCKED or DEFERRED, never backward. Actor `system:sync`, the commit sha as evidence. Closures stay manual. (V-RETRO is unrelated: it detects commits *older* than the ticket.) |
| R4 shadow exit by volume | **Adopt** | Revoke after **50 consecutive** procedure transitions with zero `V-00`, with no time floor. The time floor existed to catch rare writers. The R1 static test now proves no repo code writes raw SQL, so the only remaining raw path is ad-hoc session SQL, which the volume count covers |
| R5 pre-migration patch | **Adopt the mechanism, correct the premise** | The FND-0193 links are correct and are not reversed (see G2). The real lesson is a change to the gate: it applies to RFD and **delivery closures** (CODE_FIX/DATA_FIX/WORKAROUND), not to REJECTED/DUPLICATED. Step 0 of Phase B is `scripts/ops/backlog_pre_b_corrections.py`: a fixed, versioned list; dry run by default; idempotent; appends link events and calls `transition()` only, with no raw SQL and no reversal (append-only). Its list: link FND-0203 `DEPENDS_ON` FND-0236 (stated in the logs), and record FND-0223's past-gate closure as a known exception. FND-0029 and FND-0041 need **owner decisions** (what blocks them), recorded as DECISION group members. A script cannot invent a blocker |
| Flag fatigue baseline | **Measured: no consolidation needed** | Only 10 confirmed OVERLAPS carry `absorbs`, and there are 0 IMPACTS links. Replayed over the 233 closures: median **0** flags, max 2, 7 closures raise any. Below the 2.5 trigger, so consolidation is out of scope (C2 rule). The Phase B replay re-measures it, and a breach opens a new ticket |
| Cross-DB read time | **Adopt** | `gestion` holds no cache. `v_status_feed` exposes `event_ts`. `control.cycle_flag` gains `ap_status_read_ts` (fondos DDL, applied by the owner). Violation `V-STALE-READ`: a stored snapshot read before the ticket's latest status event |

## 5e. Self-review after round 3: new risks, now addressed
1. **Rubber-stamping the collision hook.** With 27 % of commits blocked at first, a session will reach for
   `Overlap: FND-x none` just to get through. Before this fix, a REJECTED pair was never proposed again, so a reflex `none`
   would hide a real overlap for good. Fix:
   - Trailer decisions are stored with `origin='HOOK_TRAILER'`.
   - `backlog_links.py` treats a `none` from a trailer as *not reviewed*: the pair can still be proposed in the review sweep.
   - The share of `none` answers is a tracked metric; above 80 % it is flagged as rubber-stamping.
2. **The block rate is an estimate.** The 27 % was measured against today's open set. Before go-live, replay the rule against
   the set that was open at each commit's time. Acceptance: ≤ 30 % in the first week, then falling.
3. **Auto-forward latency.** "Upon the first commit" would otherwise wait for the next sync run. A **post-commit** hook runs
   `backlog_sync.py --since HEAD~1`, best-effort and failing open, so the move happens right away. Preflight and the cycle
   report remain the backstop.
4. **Hooks are local only.** `.git/hooks` is per clone, and someone can skip it with `--no-verify`. `backlog_sync` re-applies
   the collision rule when it reads commits and records every commit that should have been blocked as violation `V-HOOK`. The
   hook is the first line; the sync is the guarantee.
5. **Machine transitions inflate the shadow counter.** Auto-forward moves by `system:sync` would reach 50 quickly without
   testing the paths people and agents use. The 50-transition exit counts **non-system actors only**.
6. **Violation codes are scattered.** The proposal doc gets one catalogue with code, rule, source view, phase and owner
   action for each: `V-00`, `V-RETRO`, `V-FAST`, `V-HOOK`, `V-STALE-READ`, forced gate, BLOCKED without blocker, DEFERRED
   without revisit date, WORKAROUND without OPT, DUPLICATED without link, work on a CLOSED ticket.

## 5f. Round-4 directives (owner, 2026-10-26): integrated; supersedes §5d R2 trailer design and §5e items 1, 3, 4
Approved as final by the owner: R5 (the FND-0193 links stay, FND-0203 is genuinely blocked, step 0 holds **no reversals**: it
only appends the FND-0203 → FND-0236 `DEPENDS_ON` and records FND-0223 as a known exception) and the gate scope (Phase B).

**One commit-msg hook does everything at commit time** (it has the message and the staged files; pre-commit has no message and
runs before other checks can still abort). In order: (1) validate `FND` tokens; (2) collision check; (3) auto-forward
OPEN/TODO → IN_PROGRESS through `backlog_client.transition()` for a `Refs:` ticket, synchronously (post-commit hook dropped);
(4) write a `backlog.gate_check` row and append the trailer `Backlog-Gate: <nonce>`. Total budget 3 s. Backlog unreachable:
fail open, trailer `Backlog-Gate: offline`, and `backlog_sync.py` runs the move and the check later with no penalty. The
only window is a commit aborted after the hook ran; the result is a ticket that is IN_PROGRESS with no commit, which is
harmless and is logged as "commit-msg gate".

| Action | Disposition | Final design |
|---|---|---|
| A1 external adjudication | **Adopt, corrected** | The `Overlap:` trailer is **removed**: the committer can no longer declare anything. A block clears only when `backlog.sp_adjudicate_overlap(a, b, decision, absorbs, reason)` has recorded the pair. `decision` ∈ {OVERLAPS, RELATED, DEPENDS_ON, NONE}, written as a CONFIRMED or REJECTED link event. **The SP checks `current_user = 'backlog_adjudicator'`, never a parameter.** That role and its DSN are created by the owner and kept **outside the repo `.env`** (owner's `.pgpass`/prompt), because every agent session on this machine can read the writer DSN, so an "actor ≠ committer" check inside software would be spoofable. No "Tech Lead" role exists: the owner is the only adjudicator. Workload (measured on October): 83 distinct pairs in 26 days, about 3 a day. Adjudication goes through the existing review CSV (`--review-out` → `decision` column → `--apply-review`, which now calls the SP), in batches, with a queue-age metric. Replaces the `HOOK_TRAILER` metric of §5e.1 |
| A2 punish bypass | **Adopt the violation, correct the sanction** | The sync classifies each commit citing a ticket: `Backlog-Gate` nonce present in `gate_check` ⇒ checked; `offline` ⇒ pending, no penalty; trailer absent or unknown nonce ⇒ `V-HOOK-BYPASS` (covers `--no-verify`, another clone, a forged trailer). Sanction: an append-only `backlog.hold` (raise/release) on the cited ticket. While a hold is open the ticket cannot reach READY_FOR_DEPLOY or a delivery closure, and it is shown in preflight and the cycle report. Release is by adjudication of the unadjudicated pairs, plus the owner's reason when the commit touched no overlapping file. **Rejected: forcing the ticket to `BLOCKED`.** Reasons: BLOCKED means "waiting on a named condition" (§4.1 rule 2), and it already fails that rule on 3 of 4 tickets; auto-forward never moves a BLOCKED ticket, so work would stall; the commit is already in master, so the state change prevents nothing, while the hold stops what matters (release and closure); and an outage would look like evasion without the `offline` trailer |
| A3 synchronous forward move | **Adopt, in commit-msg** | As above. `v_status_feed` carries `event_ts`, so a consumer can tell how fresh a read is |
| A4 audit mode 14 days | **Adopt audit mode, replace the exit criterion** | Audit mode: warns, logs to `gate_check` with `mode='AUDIT'`, never blocks. The measured steady state is driven by *new* pairs: 22 % of commits introduced an undecided pair and 34 new pairs appeared in the second half of October, so adjudicating the 83 existing pairs will not reach 10 %. Day-14 rule: replay the audit window with the adjudicated pairs removed. If the projected rate is ≤ 10 % ⇒ blocking mode. If it is higher ⇒ **tighten the rule first** (in order: ≥ 2 shared code files; hub threshold < 5; function-level match) and re-measure. Blocking never starts above 10 %. The switch is one `BACKLOG_COLLISION_GATE_MODE` setting in `shared/config.py` (kill-switch registry, NORMAS_IMPLEMENTACION §8): `OFF`, `AUDIT`, `BLOCK` |

New owner actions (DDL and credentials are never applied by a session): create role `backlog_adjudicator`; apply `30`/`40`/`50`
DDL via `migrate_backlog_v2.py`; install the commit-msg hook (`scripts/audit/pre-commit.sh` pattern, FND-0314).
The new tables are `status_event`, `actor_alias`, `review_flag`, `hold`, `gate_check`, `ops_event`, `group_tag`.

## 5g. Self-review after round 4: residual risks, stated and mitigated (item 3's pending decision is closed by §5h: rejected)
1. **No non-bypassable gate exists, and the spec says so.** Hooks are local. CI cannot enforce this rule either, because the
   backlog lives on `127.0.0.1` and is not reachable from CI. The real guarantee is *detection within one sync* plus the
   `hold`; NORMAS must say "detected and held", never "enforced".
2. **Nonce forgery.** Any session with the writer DSN could insert a `gate_check` row. Mitigation: rows are written only through
   `sp_gate_check(tree_hash)`, and the sync recomputes each commit's tree hash and compares it. A nonce cannot be reused across
   commits and a forgery needs an explicit false statement. It remains a deterrent, not a lock.
3. **Owner adjudication is a standing cost.** October replay: about 3 new undecided pairs a day, 83 in 26 days. In BLOCK mode
   an agent commit waits while the owner is away. Levers: batch review via the CSV, a queue-age metric, and the tightening
   steps of §5f A4. **Pending owner decision** (default: none, as directed): whether a long-pending queue (e.g. oldest pair
   unadjudicated > 3 days) may fall back from BLOCK to AUDIT automatically. Recorded as an open risk, not decided here.
4. **All block and pair rates are replay estimates.** They use today's open set and today's ticket text on past commits;
   tickets that were open then and are closed now are missed, and text written later is counted. The error runs in both
   directions, and the day-14 measurement in audit mode, on live data, is the number that counts.
5. **Hook actor.** Hook transitions use actor `system:gate`; every `system:*` actor is excluded from the 50-transition shadow
   counter (§5e.5).
6. **Catalogue and targets added.** Violations `V-HOOK-BYPASS` and `HOLD-OPEN` join the catalogue (§5e.6). Success targets
   added: bypass commits after day 14 = 0 per week; blocked-commit rate ≤ 10 % before BLOCK mode; adjudication queue median
   age ≤ 2 days.

## 5h. Round-5 mandates (owner, 2026-10-26): final design. This section wins over every earlier one
Owner decisions recorded: **no automatic downgrade from BLOCK to AUDIT, ever** (rejected); a queue older than 48 h raises an
escalating alert; the adjudication workload is accepted as a fixed cost. The owner authorized the final proposal document and
the six tickets; code stays out of scope for this session.

Facts measured today (read-only) that shape the design:
- **Commits go straight to `master`** in one shared working tree: 242 commits since 10-01, 0 merges, one local branch. Other
  sessions' uncommitted files sit in the same tree, and the repo rule is no ad-hoc worktrees.
- **CI exists** (GitHub Actions: `agents-sync.yml`, `pg-tests.yml`; remote `origin`), and `ssh-keygen` is available for signing.
- **Detection is low-recall.** Of 72 confirmed OVERLAPS, 9 (12 %) share ≥ 1 file path cited in the ticket texts and 1 shares
  ≥ 2; on the 37 REJECTED, 1 shares one. Existing signals recover 11–15 % each of the 533 explicit pairs (`--evaluate`: S2 15 %,
  S3 11 %, S4 12 %). Past proposals are 85 % precise. So: **file sharing is precise but rarely fires**.

| Mandate | Disposition | Final design |
|---|---|---|
| M1 quarantine branches | **Adopt the goal, change the mechanism** | A per-commit quarantine branch cannot be made safely here: moving a commit off `master` after the fact means resetting a working tree shared with other sessions, which would take or duplicate their uncommitted files. Instead the **gate moves to publication**. The commit-msg hook **never blocks** an agent: the commit succeeds in every mode, the pair is recorded, the ticket gets a `hold`, and an adjudication request is queued. `BLOCK` mode means (a) a **pre-push hook** refuses to publish while any pair from the pushed commits is undecided, and (b) the CI attestation below. The owner already performs the pushes, so the gate sits where only the owner passes, and agent velocity is decoupled from owner availability. Queue age: 48 h ⇒ `[ATENCION]` line in the cycle report and preflight; 96 h ⇒ `[ALERTA]` as the first line and a non-zero preflight exit. The mode setting is `OFF / AUDIT / BLOCK`, with no automatic transition away from BLOCK |
| M2 offline CI validation | **Adopt, with signing** (otherwise it proves nothing) | A hashed JSON produced on the same machine is forgeable by whoever pushes it. The attestation is therefore **signed by the owner** (`ssh-keygen -Y sign`; private key held by the owner, never in the repo or `.env`). The public key lives in `.github/backlog_gate_allowed_signers`. `backlog_sync.py --attest` runs at push time and writes `backlog_gate/attestation.json` + `.sig`. The content is only commit sha, tree hash, gate nonce, and pair ids with decided/undecided: **no ticket titles or text** (the remote is GitHub). New workflow `.github/workflows/backlog-gate.yml` fails the push if: the signature is missing or invalid; a commit in the range that cites an `FND` is absent from the attestation or its tree hash differs; or any pair is undecided. The attestation commit itself is excluded from the range. Stated limit: CI verifies that the owner's key signed a statement. It cannot check the database |
| M3 automatic calibration | **Adopt, over a fixed ladder** | Rule tiers are pre-measured constants in code (the NORMAS rule: thresholds are code, calibrated by replay). Measured on October: **T0** (1 shared code file, hub < 5) = 83 pairs, 22 % of commits introduce a new pair; **T1** (≥ 2 shared files) = 9 pairs, 3 %. The calibrator watches the trailing 3-day rate of commits that introduce a new undecided pair; on **Day 10** of AUDIT, above 10 % ⇒ it moves T0 → T1 itself, writes an `ops_event`, and shows the change in the cycle report. It only tightens, never loosens, never invents a rule. **"Function-level match" is not in the automatic ladder**: it is unimplemented and unmeasured; it becomes a tier only after a replay and a code change. BLOCK starts only at ≤ 10 % |

**Residual risks, now stated in the spec:**
1. **The gate is a decision prompt, not a collision detector.** Recall is about 12–15 %, and T1 catches 1 of 72 confirmed overlaps.
   Start-of-ticket `backlog_links.py --for` and the review sweep stay the real protection. Two metrics are tracked in AUDIT:
   *precision* (share of adjudicated pairs decided OVERLAPS / DEPENDS_ON rather than NONE) and *recall floor* (share of newly
   confirmed OVERLAPS the active tier would have flagged). If precision < 30 % or the floor is < 5 %, the cycle report prints
   "gate no longer informative" and the owner decides whether to retire or redesign it. That decision is the owner's, not
   automatic.
2. **Local `master` can run ahead of `origin`** with unadjudicated commits. The `hold` stops release and closure of those
   tickets meanwhile; the pre-push hook and CI stop publication.
3. **Single signer.** The owner's key is the only authority. Key loss means no pushes pass CI until a new
   `allowed_signers` is committed, so the spec carries a rotation procedure.
4. **Day-10 / Day-14 numbers are still replay estimates** (§5g.4). The live audit-window data replaces them.

## 5i. Round-6 directives (owner, 2026-10-26): final operational constraints. This section wins over every earlier one
Verdict received: approved. Proceed with preflight, the single consolidated `PROPUESTA_BACKLOG_V2.md` (decision log only in an
appendix) and the six tickets. No code.

| Directive | Disposition | Final design |
|---|---|---|
| Automatic gate retirement | **Adopt, with a sample floor and a safe mechanism** | Windows of 14 consecutive days. Floors: precision ≥ 30 % (adjudicated pairs decided OVERLAPS / DEPENDS_ON / REVERT rather than NONE) and recall floor ≥ 5 % (newly confirmed OVERLAPS the active tier would have flagged). **Minimum sample** or no verdict: ≥ 15 adjudicated pairs and ≥ 10 newly confirmed OVERLAPS; below it the window extends (maximum 42 days, then "insufficient data" is reported, never a retirement). Both floors are evaluated **in AUDIT too** (an extension of the order: a gate that fails them never reaches BLOCK). On failure the system retires the collision gate by itself, with `[ALERTA]` in the cycle report. **Mechanism:** the effective mode is the lower of the `shared/config.py` setting and the latest `backlog.gate_state` event; a retirement is an appended event (actor `system:calibrator`, the measured numbers as evidence), never an edit of a repo file. Re-enabling is an owner-written event with a reason, never automatic. **Scope of OFF:** only the collision gate (pairs, pre-push block, collision part of the attestation). Id validation, auto-forward, bypass detection and holds stay on. Governance then rests on the review sweeps and `--for` |
| Git revert protocol | **Adopt, with five additions** | Rule stands: an illegal overlapping commit is neutralized by a forward `git revert <sha>`; **no `rebase`, no `reset` on the shared `master`**; a `pre-rebase` hook refuses on `master`. Additions: (1) pre-push prints the exact offending shas and pairs, so nothing is buried in a stack; (2) the owner may publish the clean prefix (`git push origin <last-clean-sha>:master`), which needs no rewrite; (3) `sp_adjudicate_overlap` gains the decision `REVERT` (requires the revert sha, verified by the sync) so the pair counts as decided; (4) a revert commit inherits its original's pair status and creates no new pair; (5) revert in reverse order of the commits. NORMAS carries the protocol in its risks section |
| Key loss / emergency bypass | **Adopt, and state the dependency** | Fact: GitHub Actions on a push cannot block that push; it blocks only with **branch protection** on `master` requiring the `backlog-gate` check. This session cannot read the repo settings (`gh` is absent). **Owner action O-5:** confirm the protection state. Without it the CI is detective (red mark after publication). With it, publish by pushing `publish/<date>` first, wait for green, then fast-forward `master` (checks attach to the sha). **Emergency protocol:** the owner, as admin, bypasses via the GitHub bypass-actors setting or by disabling the rule temporarily; logs `V-GATE-EMERGENCY` in a backlog entry the same day; **within 48 h** generates a new key, commits the new `allowed_signers` (the one permitted bypass), and signs a catch-up attestation covering the emergency range; CI re-runs by `workflow_dispatch`. Key loss follows the same path |
| Historical data debt in step 0 | **Adopt; corrected facts; append-only** | `scripts/ops/backlog_step0_manifest.yaml` is a fixed, versioned list; the owner runs it (dry run by default). Everything goes through `transition()` and link events: logs are never rewritten (grant and AGENTS rule), so "rewritten" means a structured, appended record. `BLOCKED` and `DEFERRED` transitions now **require** `blocker` (a ticket id or a named condition) and `revisit_by` / `revisit_on_event`. **Manifest rows:** FND-0203: link `DEPENDS_ON` FND-0236 and structured blocker. FND-0029: structured blocker "external source (Deutsche Bank catalogue); re-check after each harvest" (its status stays; the population is WRONG_DOC, so the §4.1 rule-5 scope applies). **FND-0041: proposed default `BLOCKED → TODO`** (no blocker named in 2 logs; related OVERLAPS FND-0233 / FND-0250) with the owner's apply as approval. FND-0223: recorded as the one known past-gate closure. FND-0022 / FND-0038 (DUPLICATED without link): target derived from the closure log when exactly one `FND` is cited, otherwise listed for the owner. The 6 DEFERRED tickets: each gets a revisit condition (owner supplies; the manifest lists them) |

## 5j. Final mandates (owner, 2026-10-26): design closed. This section wins over every earlier one
- **V-GATE-UNPROTECTED.** `backlog_sync.py` checks that `backlog-gate` is a required status check on `master` through the GitHub
  API (read-only fine-grained token, held outside the repo, never in a committed file). The local git config **cannot** see
  remote protection, so it is not a source. Not required ⇒ `V-GATE-UNPROTECTED` and `[ALERTA]` in the cycle report. No token or
  API unreachable ⇒ `V-GATE-UNVERIFIED` (a sibling code, so a missing token never passes silently), cleared by an owner-written
  verification event (`backlog.gate_state`, with date) that expires after 30 days.
- **Step 0 final states (no owner input at execution).** FND-0041: `BLOCKED → TODO`. The 6 DEFERRED tickets: `revisit_by` =
  Phase B deployment date + 90 days, each marked `revisit_basis='DEFAULT_90D'` so a placeholder is distinguishable from a
  deliberate date; on expiry the ticket is listed in the cycle report (no automatic transition). FND-0029, FND-0203, FND-0223
  as in §5i.
- **Deliverables in order:** (1) `scripts/audit/preflight_check.py`; (2) the consolidated
  `doc/especificacion/PROPUESTA_BACKLOG_V2.md` (decision log in the appendix only; every ticket cited was read in full first);
  (3) six tickets (A, B, C, D, actor alias, decision-time read) via `backlog_links.py --new` → INSERT + creation log → link
  events; (4) commit of the document alone, then push (ordered by the owner). A push the classifier blocks is left for the
  owner to run (standing rule); the commit is not undone.

## 6. Owner decisions (2026-10-26)
- **Scope: proposal only** — no code, no DDL in this session.
- **Gate: hard + forced override** — `sp_transition` refuses RFD/CLOSED with an open `DEPENDS_ON` prerequisite or an unacked
  review flag; `p_force` + reason overrides, is logged and counted in `v_backlog_violations`. Supersedes NORMAS §3 "advisory".
- **Commit sync:** forward moves are automatic (`Refs:` trailer, OPEN/TODO → IN_PROGRESS, per round 3). Closures stay manual.

## 7. Deliverables of this session (after approval)
1. Run `python scripts/audit/preflight_check.py` (shared repo: the dirty `p1_db_harvest.py`, `db/oper/`, harvest test belong
   to another session — not touched, not committed).
2. Write `doc/especificacion/PROPUESTA_BACKLOG_V2.md` (English body, Spanish section names consistent with NORMAS where
   cited): context, current-system map (§1), gaps G1–G11 with evidence (§2), history audit (§3, figures dated 2026-10-26,
   population = all 295 tickets, query text in an appendix), v4 adopt/reject table (§4), Phases A–D with the decisions above
   (§5–6), migration/backfill plan, NORMAS sections each phase rewrites, risks (role change breaks raw-SQL habits of
   sessions; backfill events marked `BACKFILL`; cross-DB sync stays pull-based, no LISTEN/NOTIFY across databases),
   and acceptance criteria per phase.
3. Open six tickets through the normal procedure (`backlog_links.py --new "<text>"` for each first, then INSERT ticket +
   creation log): Phases A–D, AP-2 (actor alias, A `DEPENDS_ON` it), AP-1-refined (decision-time read rule, C `DEPENDS_ON` it);
   confirmed `DEPENDS_ON` events B→A, C→A, D→A. The proposal doc carries §5b verbatim. History findings that
   need a data decision go on the Phase B ticket as the step-0 correction list (§5d R5). The open owner decisions are
   FND-0029 / FND-0041 blockers and DUPLICATED-without-link FND-0022 / 0038.
4. Commit the proposal doc alone (`docs(backlog): …`, ticket ids in subject), after preflight; push left to the owner.
5. Offer (one line) to publish the proposal as a shareable page.

## 8. Verification
- Every figure in the doc re-run from the appendix queries (read-only connection, `default_transaction_read_only=on`) on the
  day of writing and matches.
- `python scripts/audit/sync_agents_md.py --check` (doc set change: new file in `doc/especificacion`, not `doc/reglas`, so no
  index row needed — confirm the check stays clean).
- The four tickets visible with `backlog_links.py --for FND-nnnn` showing their `DEPENDS_ON` links.
