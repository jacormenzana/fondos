# Backlog v3: automation by default, human by exception

## Context
Backlog v2 delivered correlation (links, gate, review flags, holds, groups, impact closure), traceability (append-only status/ops/link events, violation views) and a governed state machine (`sp_transition`). But it moved almost every write onto the owner. The 2026-10-29 live-write ban (`shared/backlog_client.py::confirm_live_write`: non-interactive writes are refused, except `agent_log`, `incident` and `gate`) and owner-only publication left the system largely idle. The previous model (the HTML MasterBacklog) was updated end to end by the agent.

**Live evidence (read-only, 2026-10-31):**
- Only 3 status events in the last 3 days.
- 17 of 21 `IN_PROGRESS` tickets have been idle for more than 5 days; there is 1 `READY_FOR_DEPLOY` ticket.
- The A-2 counter is stuck at 20/50.
- 0 `close_condition` rows; 0 review flags ever raised.
- 13 `V-WORK-ON-CLOSED` violations.
- Only 17 of 76 commits since 10-26 carry `Refs:`.
- 26 merges and 6 `chore: attestation` commits, all made by hand.
- FND-0350 was merged but never applied, and nothing flagged it.

**Owner decisions (2026-10-31, this session), which supersede the 10-29 ban for the operations listed:**
1. **Broad agent autonomy.** Agents may make any legal transition, closures included. Human only: `p_force`, reopen, gate/waiver/mode decisions, collision adjudication.
2. **GitHub Actions** opens the PR and turns on auto-merge, so a PR merges once the 4 required checks are green.
3. **Attestation becomes advisory:** no per-push signing and no extra commit.
4. **Automatic closure on deploy evidence.**

**Kept unchanged:**
- Append-only history.
- The single door (`sp_transition`/`sp_open_ticket`).
- The delivery gate (DEPENDS_ON / holds / review flags).
- DDL is applied by the owner.
- The incident gate (FND-0264).
- The no-synthetic-writes-on-live rule (staging only, FND-0351).

## Delivery status (2026-11-01)
W1, W2 and W3 are delivered and live. This section is the delivery record that closes FND-0356, FND-0357 and FND-0358 through the autopilot. W4b (FND-0360) is delivered and W4 (FND-0359) is recorded at the end of this section; W5 and W6 are still to be reassessed. Evidence read on 2026-11-01:
- **W1 (FND-0356), auto-PR:** the merge commit of PR #35 is authored by `github-actions[bot]`, so the workflow opened the pull request and auto-merge landed it after the required checks. The same holds for the earlier pull requests recorded in the branch-cleanup notes.
- **W2 (FND-0357), agent write authority:** `scripts/ops/backlog_drift.py` reports only the `modules` phase of W4b as missing in the live backlog, so every routine and login of the `autonomy` phase is present there.
- **W3 (FND-0358), autopilot:** `scripts/audit/autopilot_mode.py` reports ceiling ON, database mode ON and effective ON, with 0 autopilot closures reopened in the last 30 days.
- Not verified here: the first automatic closure on a real delivery. This record is that delivery, so the closure of the three tickets is its test. (Verified afterwards: the autopilot closed FND-0356, FND-0357 and FND-0358, then FND-0368 and FND-0322.)

### W4 (FND-0359) and the hook that runs it (FND-0369), delivered 2026-11-01
W4 shipped in two parts, and its delivery was rejected once: PR #41 merged with `Closes:` before the owner's review reached it, the owner held that an informational note is not a control, and the autopilot undid the rejection from the old merge (FND-0368, fixed first). The record below is what stands.
- **Part 1, PR #41:** a merge that cites tickets puts the open tickets it may affect on notice with one coalesced `[impact]` note each (signals: confirmed links, text citations, module facts once the clusters prove themselves; at most one note per ticket per 24 hours).
- **Part 2, PR #43:** the same step invalidates the probe status of every affected ticket in progress, and the delivery gate (`fn_probe_blockers`) refuses a delivery closure with `NO_PROBES` (a ticket with no closing condition) or `STALE_PROBES` (conditions invalidated and not re-run green since). The probe `tests_pass` reads the check runs of the delivering change from GitHub with no secret. The phase also gave every ticket in flight the default `tests_pass` in the same transaction as the gate.
- **Live evidence, 2026-11-01:** the phase applied at 21:53:47 UTC with no drift; 27 tickets have an active closing condition, all 25 in flight among them; the autopilot closed FND-0368 from its `pushed` probe and FND-0322 from the default `tests_pass` on the CI of its delivering PR; the first live run wrote 10 impact notes (one per ticket, 13 merges folded into the largest) and left FND-0331, FND-0334 and FND-0335 STALE, which they stay until they deliver.
- **FND-0369:** the installed hook starts the autopilot only when `shared/` and `scripts/` are byte for byte the code of `origin/master` (a clean master that is only behind is fast-forwarded first); it was reinstalled on the owner's clone on 2026-11-01 and its condition was checked by hand on a clean master. The first natural fetch after it is its first real run.
- **Not done, on purpose or by limit:** the automatic TRAIN-group proposals (the `MOD.*` clusters already carry that signal); a probe specific to each ticket (the default checks CI, not the ticket's own criterion); the unauthenticated GitHub limit of 60 requests an hour (an exhausted limit makes tickets wait, never pass); a ticket that is not delivered by a merge with `Closes:` is not closed by the autopilot.

## Design principles
1. **Authority comes from the database, not the console.** Each automated actor gets a scoped login with EXECUTE on specific routines only. The routines check the actor kind and the evidence. This reuses the pattern the owner already accepted for `backlog_gate` / `backlog_logger` (`dsn_for_operation`, `_connect_allowlisted`, no fallback DSN).
2. **Evidence, not declarations.** An automatic move cites a recorded `ops_event` (commit, merge, deploy, cycle). The routine verifies that the event exists.
3. **Notify, don't block.** Correlation raises review flags and writes impact notes. Only the existing delivery gate refuses moves.
4. **Kill-switches** (registered in `NORMAS_IMPLEMENTACION.md` §8): `BACKLOG_AGENT_WRITES_ENABLED`, `BACKLOG_AUTOPILOT_MODE` (`OFF`/`SHADOW`/`ON`), `BACKLOG_IMPACT_NOTES_ENABLED`.

## Workstreams (in delivery order; each is one ticket and one PR)

### W1 — Publication automation (CI only, no DDL; ships first)
- New `.github/workflows/auto-pr.yml`:
  - Trigger: push to `feat/**`, `fix/**`, `chore/**`, `docs/**`.
  - Steps: open the PR if none exists (`gh` on the runner with `GITHUB_TOKEN`; title and body from the commits, with their FND ids), then `gh pr merge --auto --merge`.
  - Permissions: `contents: write`, `pull-requests: write`.
  - Check runs already attach to the head sha through the existing `push` triggers (`pg-tests.yml`, `agents-sync.yml`, `backlog-gate.yml` run on `**`), so the PR needs no `pull_request` re-run.
- Exclusions: a PR touching `.github/workflows/**` cannot be merged with `GITHUB_TOKEN`. It gets the label `needs-owner` and no auto-merge.
- `backlog-gate.yml` becomes advisory: it still verifies an attestation when one is present, and only warns (annotation) when one is missing. `github_protect_master.py`: `--with-backlog-gate` is deprecated in the help text.
- Owner one-time actions:
  - repo setting "Allow GitHub Actions to create and approve pull requests";
  - "Allow auto-merge";
  - set `delete_branch_on_merge`.
- Docs: `AGENTS.md` publishing paragraph, `NORMAS_BACKLOG.md` §4.7, PROPUESTA §6.4.6 + decision log (round 12).

### W2 — Agent write authority (DDL phase `autonomy`, `db/pg_backlog/97_autonomy.sql` + `down/`)
- Role `backlog_agent` LOGIN, created without a password (the owner sets it; DSN `FONDOS_BACKLOG_PG_DSN_AGENT` in `.env`). Its EXECUTE grants:
  - `sp_open_ticket`, `sp_transition`;
  - `sp_record_link` (a new append-only routine that replaces raw insert E2 for this role);
  - `sp_create_group` / `sp_group_member`, `sp_annotate_wait`, `sp_add_close_condition`;
  - `sp_append_log`, created here only when absent (the stub pattern of 40; IAM 50 stays parked).
- `sp_transition` additions, enforced inside the database: for `agent:*` and `system:*`, `p_force` is refused, reopening is refused, and gate/mode routines are not granted. The delivery gate applies unchanged.
- Client `shared/backlog_client.py`:
  - add `agent` and `autopilot` to `NON_INTERACTIVE_OPERATIONS`;
  - `dsn_for_operation` maps them to `_AGENT` / `_AUTOPILOT` (no fallback, like `gate`);
  - `open_ticket()`, `transition()`, `record_link_events()`, `append_log()` choose the operation from the actor kind (`agent:*` → `agent`).
  - `human:*` is unchanged (console + `backlog_human`).
- Links: an agent may CONFIRM a link when both tickets cite each other (S1 explicit, 85 % precision) or when it states the evidence; otherwise it records PROPOSED. Rewrite `NORMAS_BACKLOG.md` §3 "decided by a person" → "decided by a person or an agent with evidence; the owner can REJECT later (append-only)".
- Effect on A-2: agent transitions are non-system, so `v_shadow_stage` now advances naturally.

### W3 — Autopilot: the evidence engine (extends `scripts/audit/backlog_sync.py`; same phase `autonomy`)
- Role `backlog_autopilot` LOGIN, with EXECUTE on `sp_record_ops_event`, `sp_append_log` and a new `sp_auto_transition(ap, to, evidence_event_id, solution_type, release)`. That routine accepts only the edges below and checks that the cited `ops_event` exists and belongs to that ticket. It then calls `sp_transition` as `system:autopilot`.
- New `ops_event` kinds: `MERGE` (merge commit on origin/master; PR number parsed from "Merge pull request #n"), `DEPLOY` (drift check clean or migration receipt), `CYCLE` (a `control.cycle_run` after the merge).
- New trailer `Closes: FND-nnnn`, validated by the existing commit-msg hook just like `Refs:` (`scripts/audit/commit-msg.sh`, `shared/backlog_gate.py`).
- Rules, reusing `plan()` / `apply_plan()`:

  | From → To | Evidence |
  |---|---|
  | OPEN/TODO → IN_PROGRESS | `Refs:` commit (exists today) |
  | IN_PROGRESS → READY_FOR_DEPLOY | `MERGE` containing a `Closes:` commit; release label `R<yyyy>.<mm>-PR<n>` |
  | RFD → CLOSED `CODE_FIX` | merged on origin/master; if the PR touched `db/**` ⇒ a `DEPLOY` event (`scripts/ops/backlog_drift.py` exit 0 / fondos migration receipt); every `close_condition` probe passes (`rfd_matrix_probe.py`); incident gate passes; delivery gate passes |
  | RFD → CLOSED `DATA_FIX` | as above, plus at least one `close_condition` and a `CYCLE` after the merge |
  | commit on a CLOSED ticket | impact note + inbox item (reopening is the owner's) |
  | IN_PROGRESS with no commit for 14 days | note + inbox; no automatic move |

- Refactor the incident gate out of `scripts/ops/close_release.py::check_tickets`/incident check into `shared/backlog_release.py`, so `close_release.py` and the autopilot share one rule (P#11).
- Trigger points (no daemon, no new credentials):
  1. A git `reference-transaction` hook fires when `refs/remotes/origin/master` moves, i.e. after any fetch (best-effort, detached, fails open); installed by the existing hook installer pattern.
  2. `scripts/audit/preflight_check.py`, which every session runs.
  3. The end of the cycle report.
- `BACKLOG_AUTOPILOT_MODE=SHADOW` for the first 20 decisions: they are written as notes ("would move X → Y, evidence e"), compared against owner judgment, then switched `ON`.

### W4 — Correlation automation (user point 1: a change in a shared module affects other tickets)
- On each `MERGE`, compute the changed files, then the impacted open tickets from two sources:
  - links: `v_impact_closure` over IMPACTS / OVERLAPS / DEPENDS_ON;
  - path citations: the T0 rule, reused from `fn_collision_pairs` / `shared/backlog_gate.py`; hub threshold 5, code files only.
- Actions:
  - raise `review_flag` (extend `fn_raise_review_flags` to fire on MERGE, not only on RFD/closure);
  - append one impact note per impacted ticket ("PR #n for FND-x changed `path`: re-check whether this is resolved or regressed");
  - for `SUPERSEDES` / `absorbs`, the agent verifies and may close the absorbed ticket (`DUPLICATED`/`CODE_FIX`), or leaves it in the inbox.
- Clusters: tickets co-changed in at least 2 merges ⇒ auto-proposed `TRAIN` group (`sp_create_group`). `v_group_status` READY_TO_CLOSE is listed by the autopilot.
- Fatigue guard: replay the October merges and target a median of ≤ 2 notes per merge; the hub threshold rises if it is exceeded (code constant, calibrated by replay).

### W4b — Clustering by target module (the module whose logic will change to fix or upgrade)
**This explicitly overrides the 2026-10-26 rejection of the former "C2" `ticket_object` registry (owner, 2026-10-31).** The difference: the registry is derived automatically and is never maintained by hand. It is recorded in the PROPUESTA decision log.

**Measured baseline (live, read-only, 2026-10-31):**
- 40 of 69 open tickets cite at least one module path in their text, so text alone misses 42 %.
- 11 modules are shared by 2 or more open tickets:
  - `scripts/audit/audit_incident.py` (9 tickets: FND-0276..0282…);
  - `proyecto1/core/cost_table_parser.py` (4: FND-0278, 0279, 0283, 0284);
  - `db/pg/35_control.sql` (3);
  - `shared/config.py` (4) is a registry hub, not shared logic.

**Store:** `backlog.ticket_module_event` (append-only) with the columns
- `ap_id`, `module_path`, `symbol` (optional function/class);
- `role` ∈ {`TARGET` (logic will change), `TOUCHED` (a commit changed it), `CITED` (mentioned in text)};
- `source` ∈ {`DECLARED`, `COMMIT`, `TEXT`, `OBJECT_NAME`};
- `actor`, `retracted`, `event_ts`.

The view `v_ticket_module` gives the current set.

**How the set is filled (all automatic except the declaration, which an agent makes):**
1. `TEXT` / `OBJECT_NAME`: repo paths in the title, description and logs, and `object_name`. These are proposals only (`CITED`).
2. `DECLARED`:
   - At `open_ticket()`, and when moving to `IN_PROGRESS`, the agent declares the `TARGET` modules (new `p_target_modules` argument). The client pre-fills them from (1).
   - A `CODE_FIX`-nature ticket cannot enter `IN_PROGRESS` without at least one `TARGET`. The check is in `sp_transition`, with `p_force` as the escape.
3. `COMMIT`: the files changed by every `Refs:`/`Closes:` commit become `TOUCHED`. The autopilot does this using `git diff-tree` on the shas it records.
   - Backfill: the 376 existing COMMIT `ops_event` rows plus text citations for all open tickets.

**Granularity:**
- The unit is the file (module). An optional `symbol` gives a finer match when declared.
- Package rollup (e.g. `proyecto1/core/`) is a weaker `THEME`-level signal.
- Registry/hub files (`shared/config.py`, `AGENTS.md`, `doc/reglas/*.md`, `migrate_backlog_v2.py`) never form a cluster on their own. This is a code constant, plus the existing hub threshold (cited by 5 or more open tickets ⇒ a `symbol` match is required), calibrated by replay.

**Cluster:**
- New `group_tag.kind = 'MODULE'` (the CHECK is widened from {TRAIN, THEME, DECISION}).
- The name is derived from the path (e.g. `MOD.SCRIPTS.AUDIT.AUDIT_INCIDENT`).
- Members are the open tickets with `TARGET` or `TOUCHED` on that module. `CITED` alone only proposes membership.
- The autopilot maintains membership (`sp_create_group` / `sp_group_member`).

**What the cluster drives:**
1. **Before work (dispersion → one fix):** `open_ticket()` and `backlog_links.py --for` return the module mates with their status and declared symbols. The agent records the decision with evidence: absorb (`OVERLAPS` + `absorbs`), sequence (`DEPENDS_ON`), batch (a `TRAIN` with a shared release), or independent.
2. **Prioritisation:** `backlog_inbox.py --clusters` (and the backlog-priorizer skill) ranks MODULE clusters by open members × priority. Example: "one change to `audit_incident.py` addresses 9 tickets".
3. **Indirect resolution:** when a member closes with `CODE_FIX` on the module, the other open members get a review flag. If they have `close_condition` probes, the autopilot re-runs them. If all pass, the agent may close the ticket ("resolved by FND-x, PR #n").
4. **Indirect regression:** after a merge on the module, the autopilot re-runs the probes of members closed in the last 90 days. A failing probe ⇒ an `[ALERTA]` and a reopen request in the owner inbox (reopening stays human).
5. **Collision gate recall:** the T0 rule reads `v_ticket_module` (`TARGET`/`TOUCHED`) instead of text paths only. The measured text recall was 12–15 %. The Phase C calibrator re-measures it.

**Clustering model: improvements over a single module criterion (best practices).**
1. **Weighted, explainable multi-signal edges, not one rule.** A link between two open tickets gets a score from several signals. Every membership stores its signals and evidence, so a cluster can always be explained ("FND-a and FND-b: same TARGET `cost_table_parser.py::parse_rows`, co-changed 3 times"). Signals, strongest first:

   | Signal | Source | Weight |
   |---|---|---|
   | Same `TARGET` symbol (function/class) | declaration | highest |
   | Same `TARGET` module | declaration | high |
   | Same module `TOUCHED` by both | commits | high |
   | Same data object (table.column, e.g. `fund_master.Fund_Nature`) | `schema_name`/`object_name` normalised against `SCHEMA_REFERENCE.md` | high (an R-2 fix spans classifier + INTER rule + migration, i.e. several modules but one object) |
   | Change coupling (files that historically change together) | `git log` co-change ratio | medium |
   | Reverse import dependency (B imports A, depth ≤ 2) | Python `ast` import graph | low: impact flags only, never membership |
   | Text citation only | `CITED` | proposal only |

   Clusters are the connected components above a threshold after hub pruning. This is deliberately simple and explainable: no opaque community detection.
2. **Normalise to logical modules before clustering.**
   - Test files map to the module they test (`tests/test_backlog_client.py` → `shared/backlog_client.py`).
   - A DDL file and its client form one logical unit when their change coupling is ≥ 0.6.
   - Generated or registry files are excluded.
   - Without this, tests and registries create false clusters.
3. **Impact radius is not membership.** The import graph tells us that changing `shared/classify_utils.py` reaches every P1 classifier. That raises review flags on the dependants (W4) but does not put them in the cluster. Otherwise every shared utility would become one mega-cluster.
4. **Size and stability rules.**
   - A cluster above 8 open members is split by symbol, or by data object when no symbol is declared.
   - Cluster ids are stable; merges and splits are append-only events.
   - A cluster closes when all its members are closed. One with no activity for 30 days is listed as stale.
5. **Membership is a decision with feedback.**
   - Auto-derived memberships start as `PROPOSED`. The agent confirms them with evidence or rejects them, and a rejected pair is not proposed again.
   - Precision (confirmed / proposed) and recall (confirmed overlaps the model would have found) are measured.
   - The thresholds are code constants calibrated by replay (`NORMAS_BACKLOG.md` §6 rule), re-measured monthly.
6. **Hotspots → refactor proposals.** A module with ≥ N tickets in 90 days and high churn (defect density × change frequency) auto-proposes an `OPT` ticket to refactor or harden it, linked to the cluster. On today's data, `audit_incident.py` (9 open tickets) would qualify.
7. **Working a cluster.**
   - One `TRAIN` and one PR where feasible, with a shared test plan.
   - Each member keeps its own `close_condition`: a cluster never closes members by assumption.
   - Explicit `DEPENDS_ON` gating stays: the cluster informs, it never replaces a dependency decision.

**Violations:**
- `V-NO-TARGET`: an IN_PROGRESS CODE_FIX ticket with no TARGET.
- `V-TOUCH-UNDECLARED`: a commit touched a module that is neither TARGET nor in a decided cluster. This is advisory and feeds the declaration habit.

### W5 — Owner decision inbox and one-command deploy (the residual manual work, batched)
- New `scripts/audit/backlog_inbox.py`. It lists everything only a human may decide:
  - collision adjudications (with the agent's recommendation);
  - reopen requests (work on CLOSED);
  - `p_force` requests filed by agents as notes;
  - holds;
  - gate/mode/waiver events due;
  - stale tickets.
- Output is a CSV; `--apply <csv>` runs the whole batch after one confirmation phrase, reusing the review-CSV pattern of `backlog_links.py --apply-review` / `gate_adjudicate.py`. Preflight prints the count and the oldest age.
- `scripts/ops/migrate_backlog_v2.py --pending`: uses the fingerprints of `backlog_drift.py` to find the phases that are on master but not live, dry-runs them all, takes one confirmation, applies them and verifies, then records a `DEPLOY` `ops_event`. The autopilot then closes the tickets that were waiting on it.

### W6 — Metrics and governance
- `v_backlog_lean_metrics`: automation ratio (status changes by `system:*` + `agent:*` versus `human:*`), median idle time of IN_PROGRESS tickets, RFD wait, inbox age.
- New violation codes:
  - `V-AUTO-REFUSED`: the autopilot was refused by the gate, so the ticket stays visible;
  - `V-AGENT-CLOSE-REVERTED`: an agent closure that a human later reopened. This is the quality signal of the autonomy.
- Documents: `AGENTS.md` (backlog + publishing paragraphs), `NORMAS_BACKLOG.md` §2 / §3 / §4.4 / §4.7 / §9, `NORMAS_IMPLEMENTACION.md` §8, `GOBIERNO_CONTROLES.md` (gate entry becomes advisory), the audit skills' §8, and PROPUESTA appendix A, round 12. The sentinel sync is checked with `sync_agents_md.py --check`.

## Bootstrapping and tickets
- Agents cannot open tickets until W2 is live. So the session prepares a one-shot script that the owner runs once at his console: an epic plus W1–W6, each child `DEPENDS_ON` the epic's prerequisites (W3/W4/W5 → W2). Before it runs, `backlog_links.py --new` is checked for overlaps with FND-0334 (Phase C), FND-0331 (A-2), FND-0350 and FND-0351.
- From W2 onward the agent opens, moves and closes its own tickets: the system is used on itself.

## Owner actions (the only manual steps left)
| # | Action | When |
|---|---|---|
The labels carry the `V3` prefix because `PROPUESTA_BACKLOG_V2.md` §8 already uses `O-1`..`O-9` for other actions.

| # | Action | When |
|---|---|---|
| O-V3-1 | Run the ticket bootstrap script | before W1 |
| O-V3-2 | GitHub settings: Actions may create PRs; allow auto-merge; delete merged branches | W1 |
| O-V3-3 | Apply `--phase autonomy` (dry run, then apply) and set the passwords for `backlog_agent` / `backlog_autopilot` plus their `.env` DSNs | W2/W3 |
| O-V3-4 | Review the 20 SHADOW decisions, then set `BACKLOG_AUTOPILOT_MODE="ON"` (merged automatically like any PR) | W3 |
| O-V3-5 | `relations` (FND-0350 drift) is applied (2026-11-01); A-2 when the counter reaches 50 | any time |
| O-V3-6 | Store the owner strings in the vault once, `init` and install the deploy watcher once: from then on pending DDL needs a click in a window, not a console (owner decision 2026-11-01) | after the vault and watcher PRs merge |

### Step by step

Run them in the order of the "When" column. Each step says what to do, how to know it worked, and what happens if it is skipped.

**O-V3-1 — Ticket bootstrap** (`scripts/mig/backlog_v3_bootstrap.py` and `scripts/mig/backlog_v3_manifest.yaml`: 8 tickets, 13 links; written and tested, dry run verified read-only on 2026-10-31)
1. In your own terminal (a real console, `cmd.exe`), from `C:\desarrollo\fondos`, set your identity and the owner-only connection string (never in `.env`):
   `set FONDOS_ACTOR=human:<your canonical login>` and `set FONDOS_BACKLOG_PG_DSN_HUMAN=<the connection string of the role backlog_human>`.
2. Dry run, and read the list of tickets and links it would create (links show CONFIRMED for a human actor):
   `C:\Users\jacor\.conda\envs\des\python.exe scripts\mig\backlog_v3_bootstrap.py`
3. Apply it, and type the live-write confirmation phrase when asked:
   `C:\Users\jacor\.conda\envs\des\python.exe scripts\mig\backlog_v3_bootstrap.py --apply`
   It opens the 8 tickets (the database mints the ids), records the 13 links, and prints each id. It is safe to run twice: a ticket whose exact title exists is reused, and a link already in place is skipped.
4. Done when the dry run prints `0 ticket(s) and 0 link(s) to do.` and `backlog_links.py --for <epic id>` lists the six workstream tickets.
5. Then review the `PROPOSED RELATED` links that the texts created towards existing tickets (FND-0264, FND-0331, FND-0334, FND-0350, FND-0351) in the next link review: they are proposals until a person decides.
6. If skipped: W1 still works, but the work is not registered in the backlog and no agent can open the W2 to W6 tickets.

**O-V3-2 — GitHub settings** (repository admin, in the GitHub web UI)
1. Settings → Actions → General → "Workflow permissions": tick **Allow GitHub Actions to create and approve pull requests**, then Save.
2. Settings → General → "Pull Requests": tick **Allow auto-merge**.
3. Same place: tick **Automatically delete head branches** (optional housekeeping).
4. None of the three settings can be read without an admin token (the public API returns `null` for `allow_auto_merge` and `delete_branch_on_merge`, measured 2026-10-31), so the test in step 5 is the check.
5. Test: re-run the failed "Auto PR" job of the pushed branch (Actions tab → the run → "Re-run failed jobs"), or push another commit to the branch. Expected: the run is green, a PR exists, and its merge box says "Auto-merge enabled" (for a branch that does not touch `.github/workflows`; the first PR gets the `needs-owner` label instead, see step 6). Until step 1 is done the "Auto PR" run is expected to fail, most likely at `gh pr create` (its first run on 2026-10-31 failed with the setting off; the log itself is only readable when logged in to GitHub): that failure is the signal that the setting is off.
6. The first PR, the one that adds `auto-pr.yml`, changes `.github/workflows/**`, so it gets the `needs-owner` label and no auto-merge: merge that one by hand once its four checks are green. Every later PR that does not touch the workflows merges by itself.
7. If skipped: with setting 1 off, no PR is opened; with setting 2 off, the PR opens but the run warns "could not enable auto-merge" and you merge by hand, as today.
8. Veto at any time: close the PR, or press "Disable auto-merge" on it.

**O-V3-3 — Apply the autonomy DDL** (W2 and the database half of W3: built and tested; it is one phase, applied once, and re-applying it is safe)
1. Merge the W2 PR, then in your own terminal (your owner DSN, a real console) dry run it: `C:\Users\jacor\.conda\envs\des\python.exe scripts\ops\migrate_backlog_v2.py --phase autonomy`. It lists the objects and changes nothing.
2. Run it with `--apply`. It creates the roles `backlog_agent` (nine EXECUTE grants) and `backlog_autopilot` (three: `sp_auto_transition`, `sp_record_ops_event`, `sp_append_log`, plus read access) WITHOUT passwords, and widens the fact kinds of `ops_event` with MERGE, DEPLOY and CYCLE.
3. Set the password of each role in your own session (`ALTER ROLE backlog_agent PASSWORD '...'`, `ALTER ROLE backlog_autopilot PASSWORD '...'`), then put the connection strings in the git-ignored `.env` as `FONDOS_BACKLOG_PG_DSN_AGENT` and `FONDOS_BACKLOG_PG_DSN_AUTOPILOT`. Without those lines nothing changes: agents keep writing through the console and the autopilot has no login.
4. Done when `C:\Users\jacor\.conda\envs\des\python.exe scripts\ops\backlog_drift.py` prints `no drift` (it fingerprints master's DDL against the live catalog). The session then checks the role's privileges read-only (an acceptance script for this phase is a follow-up of W2; it is not built yet).
5. Rollback if needed: `--phase autonomy --down` (dry run first). It drops the three routines (`sp_agent_transition`, `sp_record_link`, `sp_auto_transition`) and the grants, and keeps both roles, the widened fact kinds and everything written through them.

**O-V3-4 — Install the trigger and switch the autopilot on** (W3 is built; this needs O-V3-3 first)
1. Optional, once per clone: `bash scripts/audit/reference-transaction.sh`. It starts the autopilot in the background whenever a fetch moves `origin/master`, and never blocks git. Without it, run `C:\Users\jacor\.conda\envs\des\python.exe scripts\audit\backlog_autopilot.py --apply` by hand, or let a session do it (it needs no terminal once its login exists).
2. A commit delivers a ticket with the trailer `Closes: FND-nnnn` (in the same final paragraph as `Co-Authored-By`; `Solution: DATA_FIX` only for a data fix). Nothing is delivered by a `Refs:` alone.
3. The autopilot ships in SHADOW: for each delivery it writes ONE note on the ticket ("[autopilot SHADOW] MOVE FND-x: IN_PROGRESS -> READY_FOR_DEPLOY ... evidence MERGE abc123"). Read the first 20 notes (a log of each ticket, or the dry run of the script, which prints the same decisions).
4. If they match your judgement, change `BACKLOG_AUTOPILOT_MODE` to `"ON"` in `shared/config.py` (a normal pull request, merged automatically).
5. If a decision was wrong, say which one: the rule is corrected and the notes are written again under the new evidence.
6. Limits to know: a merge that changes `db/**` waits until a DEPLOY fact is recorded (your migration run, W5), and a DATA_FIX waits for a CYCLE fact; the autopilot never records those itself.

**O-V3-5 — Already open from v2**
1. `FND-0350` / drift: dry run, then `migrate_backlog_v2.py --phase relations --apply`, then `backlog_drift.py` must print `no drift`.
2. A-2: only when the counter reaches 50 (20 now): `--phase revoke`.

**O-V3-6 — No console for pending DDL** (owner request 2026-11-01: "reduce the administrative tasks, no console"). Once, in your own terminal, after the PRs of the vault and the watcher are merged and your tree is on `master`:
1. Store the owner strings (hidden prompt; skip the ones you do not use): `C:\Users\jacor\.conda\envs\des\python.exe scripts\ops\owner_vault.py set FONDOS_PG_DSN_OWNER`, then `... set FONDOS_BACKLOG_PG_DSN_HUMAN` and `... set FONDOS_BACKLOG_PG_DSN_ADJUDICATOR`; check with `... owner_vault.py check`.
2. Remember that the live backlog matches `master` now (read-only drift check, about two minutes): `C:\Users\jacor\.conda\envs\des\python.exe scripts\ops\owner_deploy.py init`. It must end with "remembered".
3. Start the watcher at every Windows logon: `C:\Users\jacor\.conda\envs\des\python.exe scripts\ops\owner_deploy.py install-startup` (a launcher in your Startup folder, no admin rights; `remove-startup` undoes it). Start it for this session with `... owner_deploy.py watch`.
4. From then on, whenever `master` brings DDL under `db/pg_backlog`, a window opens by itself with the commits, the phases and the DDL diff. **Apply now** runs each phase as one transaction with the owner string from the vault, verifies with the drift tool and remembers the commit; **Remind me in 4 h** and **Ignore this version** do what they say. Nothing is applied without your click.
5. What is not offered here, on purpose: `revoke` (A-2) and `iam`: they need your decision.
6. Not yet built (next slice): the DEPLOY fact that lets the autopilot close tickets whose merge changes `db/` (it needs a scoped login of its own so that no agent can forge it).

## Critical files
- `shared/backlog_client.py`: allow-list, `dsn_for_operation`, routing by actor kind.
- `scripts/audit/backlog_sync.py`: autopilot rules, MERGE/DEPLOY/CYCLE events, `Closes:` parsing.
- `scripts/audit/commit-msg.sh`, `shared/backlog_gate.py`: `Closes:` validation; new `reference-transaction` hook.
- `db/pg_backlog/97_autonomy.sql` (+ `down/`): roles, `sp_auto_transition`, `sp_record_link`, the `sp_transition` actor checks, the `fn_raise_review_flags` MERGE trigger.
- `scripts/ops/migrate_backlog_v2.py`: phase `autonomy`, `--pending`.
- `scripts/ops/close_release.py` → `shared/backlog_release.py` (shared incident gate).
- `.github/workflows/auto-pr.yml` (new), `.github/workflows/backlog-gate.yml` (advisory).
- `scripts/audit/backlog_inbox.py` (new), `scripts/audit/preflight_check.py` (inbox + autopilot run).
- `shared/config.py`: three switches, plus `BACKLOG_MODULE_CLUSTERS_ENABLED` and the hub/registry constant list.
- `db/pg_backlog/97_autonomy.sql`: also `ticket_module_event`, `v_ticket_module`, the `MODULE` group kind, the `p_target_modules` argument of `sp_open_ticket`/`sp_transition`, and the two W4b violations.
- New `shared/backlog_modules.py`: path extraction/normalisation (test → subject, logical units), hub rules, signal scoring, cluster derivation, size split, the `ast` import graph and the `git log` change-coupling matrix (cached per HEAD); pure. It is reused by the autopilot, the client and the collision rule (P#11).

## Verification
- Pure tests:
  - autopilot rule table (each edge with evidence present / missing / gate refused);
  - `Closes:` parsing;
  - actor-kind routing in the client;
  - the impact computation on fixed file lists;
  - the inbox CSV round-trip.
- Hermetic Postgres tests (`python scripts/ops/run_pg_tests.py --image postgres:17 -- <files>`):
  - `backlog_agent` cannot force or reopen and cannot call gate routines;
  - `backlog_autopilot` is refused without a matching `ops_event`;
  - the down script keeps all history tables;
  - `tests/test_backlog_single_door.py`, `tests/test_backlog_live_guard.py`, `tests/test_backlog_iam_client.py` are updated for the new allow-list.
- Replay on the staging container loaded with a read-only snapshot (`scripts/ops/backlog_staging.py`, pattern of `backlog_phase_b_replay.py`), on October history: how many tickets the autopilot would move or close, with zero closures past an open prerequisite (FND-0223 excepted as known), and an impact-note median ≤ 2 per merge.
- W4b checks:
  - Pure tests: path normalisation, hub exclusion, cluster derivation.
  - Replay on the staging snapshot: the backfill reproduces today's 11 shared modules and the `audit_incident.py` cluster of 9.
  - The October merges re-measure collision recall using the registry, compared with the 12–15 % text recall.
  - A hermetic test: a CODE_FIX ticket with no TARGET is refused at IN_PROGRESS.
  - Model quality on history: confirmed OVERLAPS (72) are the positives and REJECTED (37) the negatives. Acceptance before go-live: precision ≥ 70 % and recall clearly above the text baseline. The thresholds are recorded with the replay figures.
- W1 end to end: push a throwaway `docs/` branch; the PR opens, auto-merge turns on, and it merges when the 4 checks are green. A workflow-touching branch gets `needs-owner`.
- After deployment: `backlog_drift.py` prints `no drift`. Over one week, the automation ratio is ≥ 80 %, IN_PROGRESS idle time > 5 days falls from 17 to ≤ 5, and `V-AGENT-CLOSE-REVERTED` stays ≤ 5 %.
