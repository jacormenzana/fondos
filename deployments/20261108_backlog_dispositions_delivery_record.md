# Delivery record: dispositions of the Backlog v2 / v3 tickets (owner decisions of 2026-11-08)

Written 2026-11-08 for the backlog record. Owner instruction (item 1): "verify-and-close" the pending tickets of the FND-0355 checkpoint, commit the checkpoint pack, close FND-0355. Every ticket below was checked against what runs today; a ticket whose delivered part is verified closes, and anything it leaves out is named and carried by a ticket that stays open.

## Corrections to the checkpoint pack

`deployments/20261107_FND-0355_checkpoint_pack.md` listed FND-0366 and FND-0373 as TODO. Both were already CLOSED (read from `backlog.backlog` on 2026-11-08); the pack is corrected in this commit. The pending set was therefore seven tickets, not nine.

## Verified and closed with this commit

| Ticket | What was verified (2026-11-08) | Left out, and where it lives |
|---|---|---|
| FND-0334 Phase C (system sync, hooks, adjudication, gate modes, calibrator) | The five hooks are installed in `.git/hooks` (`commit-msg`, `pre-commit`, `pre-push`, `pre-rebase`, `reference-transaction`) and act: at 09:25 today the commit-msg gate logged "work started (Refs: trailer)" on FND-0331 and FND-0363 as `system:gate`. `scripts/audit/backlog_sync.py`, `gate_adjudicate.py`, `gate_calibrator.py`, `verify_attestation.py` exist; the collision gate decides pairs (today's queue: 1 pair, none older than 48 h). | The signed attestation in CI, CI itself and branch protection are GitHub-bound (NORMAS_BACKLOG 4.7): FND-0390 |
| FND-0335 Phase D (group_tag, violation views, lean metrics, impact closure) | Queried live: `group_tag` 21 rows, `v_group_status` 21, `v_backlog_violations` 97, `v_violations_c` 15, `v_violations_d` 82, `v_backlog_lean_metrics` 344 (lead, cycle and RFD-wait hours per ticket), `v_impact_closure` 328. The earlier note that `v_backlog_lean_metrics` was missing was wrong: it is live. | The W6 quality signals are FND-0362 (open) |
| FND-0351 QA (no synthetic tests on the live backlog; staging with a production snapshot) | `scripts/ops/backlog_staging.py` exists (one command: read-only snapshot, throwaway container, real DDL, every scenario, teardown, and a check that the live sequences did not move). `tests/test_backlog_staging.py` and `tests/test_backlog_live_guard.py`: 42 passed today. The hermetic Postgres scenarios (`tests/test_backlog_staging_pg.py`, `tests/test_backlog_phase_b_replay_pg.py`, throwaway `postgres:17`, no credentials): 26 passed, 2 skipped (the skipped ones are the `replay` scenarios, which need a real snapshot; not run today). | The QA that needs three real operators overlapping on the live store waits on FND-0354 (deferred): FND-0390 |

## Not closed, on the evidence

| Ticket | Why it cannot close as delivered |
|---|---|
| FND-0362 W6 metrics | The views it names, `V-AUTO-REFUSED` and `V-AGENT-CLOSE-REVERTED`, do not exist (only the plan and a comment in `shared/backlog_autopilot.py` mention them). Closing it as `CODE_FIX` would state a delivery that did not happen. It stays open; the 2026-11-03 deferral said these views are not needed for the finish line. |
| FND-0372 token saving of NORMAS_SQL_TOKENS | Its closing condition is a recorded measurement (input + cache-creation + cache-read tokens, at least 3 sessions per model). None has been made. |
| FND-0384 | Not in the instruction to close. Its close condition is now the owner's: the 8 standing-accepted funds completely resolved or cleared (condition 49). |

FND-0331 (Phase A, including the UPDATE-grant revoke) and FND-0363 (apply A-2) close only after the revoke is applied and verified live; see the second record section when it exists.

## GitHub

Deferred by the owner (item 2). FND-0390 holds the GitHub-bound remainder, DEFERRED with the review event "the current operational backlog is cleared".
