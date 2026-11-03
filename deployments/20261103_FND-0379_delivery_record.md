# Delivery record: FND-0379 (the autopilot hook runs from a clean clone of origin/master)

Written 2026-11-03 for the backlog record. The code was delivered in `8538f08`; the hook was installed by the owner at 21:5x the same day (`bash scripts/audit/reference-transaction.sh`). This record is the live evidence that closes the ticket.

## What was asked

When `origin/master` moves, the autopilot must run from a clean clone of it, so that nothing in the shared working tree (a dirty file, another branch, another session's work) can stop it or change what it runs. Before, the hook refused with `autopilot NOT started: shared/ and scripts/ are not the merged code of origin/master` (hook log, 2026-11-03 21:48:07, the old hook).

## Evidence (hook log `out/audit/backlog_autopilot_hook.log`, clone `out/autopilot_clone`)

- Runs after the reinstall, each from the clone at the commit that had just been pushed: 21:58, 22:38, 22:48, 22:58 and 23:08, every one `autopilot ON`, none `NOT started`.
- **The decisive run, 23:08:45** (push of `bd7b210`): the shared tree held an uncommitted edit under `scripts/audit/` (`writer_proof.py`, a scratch line left on purpose; the exact condition that used to block the hook). The autopilot ran from the clone and completed. The same tree edit was never staged or committed.
- The clone is pinned with explicit `--git-dir` / `--work-tree`, so a damaged clone can never make git operate on the main repository (regression test in `tests/test_backlog_autopilot_run.py`, mutation-checked).
- Side effect that proved useful: the same runs wrote the 19 module groups of FND-0381 (`module clusters 74 event(s)`, 22:58), which needed the code of the pushed commit and not of the working tree.

## Stated limit

The failure path (a damaged or unreachable clone) is covered by tests, not exercised live. A run that cannot build the clone logs why and exits without touching the tree.
