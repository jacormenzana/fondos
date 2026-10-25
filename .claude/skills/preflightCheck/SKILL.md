---
name: preflightCheck
description: Read-only pre-flight before acting on a stored plan, summary or handoff, or after a long idle — git dirty files, running jobs and cycle lock, DB state, memory, new tickets. Use at the start of every continued session and before any commit, push, live write, launcher or ticket.
---

# Skill: preflightCheck

**Description:** Read-only pre-flight before acting on a stored plan, summary or handoff, or after a long idle — git dirty files, running jobs and cycle lock, DB state, memory, new tickets. Use at the start of every continued session and before any commit, push, live write, launcher or ticket.

---

## 1. Why this exists

Several Claude sessions (and the owner) work on this repository at the same time, and they share files, the live database and the long
jobs. A plan written an hour ago, a context summary or a memory file describes the world *then*. On 2026-10-11 one session was about to
implement a promotion-gate fix while another had already shipped a better gate, promoted the portfolio and started a 5 h recompute with
uncommitted edits in `shared/config.py`, `run_pipeline.py` and `AGENTS.md`. Five read-only checks, taking seconds, would have shown it.

Use this skill **first**, before reading the plan again, whenever:
- the session continues from a summary / compaction, a handoff or a stored plan file;
- the user says "update the context", "what changed", "sync", "before going on", or comes back after a pause;
- you are about to commit, push, apply DDL, write to `gestion.backlog`, promote, or launch a pipeline / launcher / the full hermetic suite;
- a tool result or file note says a file "changed on disk since you last read it".

## 2. Procedure

```
C:\data\envs\des\python.exe -X utf8 scripts\audit\preflight_check.py --mine <paths you changed this session>
```

`--mine` takes the files/prefixes *you* edited: they are listed apart and do not raise the caution (the script cannot tell them from
another session's work). Add `--json` to parse it, `--strict` to make a CAUTION exit 1 (for a launcher), `--no-db` when the DSN is
unavailable. It never writes, never fetches (the ahead/behind figures are as of the last `git fetch`) and never prints a DSN.

| Section | What it reads | Why it matters |
|---|---|---|
| 1 git | dirty tracked/new files, ahead/behind `origin/master`, stashes, worktrees | a dirty file that is not yours is another session's change in flight |
| 2 processes | running pipeline/test/audit processes, the launcher cycle lock (the repo's own `lock_file_held`) | a long job is using the DB, CPU and files |
| 3 database | PRODUCTION scenario, newest persisted audit and its age, `fund_metrics.algorithm_version` vs code `CALC_VERSION`, last `p2_pipeline_log` row, rows logged in the last 10 min | a recompute pending/running, a stale audit (the promotion gate refuses), no production scenario |
| 4 memory | newest topic files with the `originSessionId` that wrote them | another session moved shared state — read its file |
| 5 backlog | tickets opened in the last 24 h, open count | what you were about to file may exist now |

## 3. Reading the verdict

**CAUTION** means acting on the stored plan is unsafe *right now*. Do not argue with it, change the plan:
- *files not yours are dirty* → never edit, stage, test over, revert or `git add -A` them; if the work needs them, wait or ask the owner;
- *a pipeline/test/audit process is running or P2 logged in the last 10 min* → no P2/P3 run, full suite, launcher or `P3_generateReport.bat`
  until it ends (its memory file says when); read-only work and documents elsewhere are fine;
- *cycle lock held* → a launcher cycle owns the DB writers;
- *no PRODUCTION scenario* → the report fails closed; this is a state to report, not to "fix" by promoting.

**CLEAR** with notes is not a green light for everything: notes tell you what to read next (commits not on origin: who pushes? stored
metrics on a different `CALC_VERSION`: recompute pending; audit older than 3 days: the promotion gate will refuse as stale).

## 4. After the check — reconcile before acting

1. Read the memory files the check lists as recent (and any with another session id) and the tickets opened since.
2. Take the stored plan and strike every item that is done, superseded or owned by another session. Say which, with the evidence
   (commit, DB row, ticket). Do not redo it, do not "complete" it in parallel.
3. Report to the owner in 3-5 bullets: verdict, what changed since the plan, what is now obsolete, what is still yours, what you will
   NOT touch and why. Then continue with what remains.
4. If your own memory files contradict reality, rewrite them now (they are the next session's starting point).

## 5. Limits — what the check cannot see

- Another session's *intent* (a plan not yet written to disk) or work in an unregistered worktree on another machine/WSL: the 10-minute
  `p2_pipeline_log` signal covers the biggest case; for the rest, ask.
- `origin` is as of the last fetch. Fetching is a network read that changes no working file; do it only when the answer matters.
- A clean tree is not proof nobody is about to edit. Re-run before a commit/push/live write — it costs seconds.

## 6. Standing rules this protects

A pasted assessment is not an authorization (re-ask with explicit options). Live writes — DDL, backlog, promotion, push, launching a
pipeline — need the owner's explicit yes each time. Never touch another session's uncommitted files. Tests of this script:
`tests/test_preflight_check.py`.
