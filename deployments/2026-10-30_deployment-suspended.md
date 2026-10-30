# Deployment record: Backlog v2 deployment SUSPENDED, 2026-10-30

Written for: the repository owner and anyone who audits the Backlog v2 rollout. Companion of `2026-10-29_backlog-v2-unified-bridgehead.md` and `2026-10-29_first-attempt-targeted-staging.md`.

## Decision
The owner suspended the deployment on 2026-10-30 (about 07:30Z): the DDL (`--phase relations`, `--phase iam`), the merge into `master` and the login bindings did not reach the real targets in three reported attempts. The payload is frozen and valid; no further backlog schema change is attempted until the owner can verifiably push code and execute DDL against the true targets.

## State when the deployment was suspended (read from the agent session; every line was read, none assumed)
| Item | Value |
|---|---|
| `origin/master` | `0979d80` (the bridgehead was never merged; direct pushes are refused by branch protection) |
| `origin/main` | `36e7ab9`, orphan, still present |
| Pull requests | none open for the two final branches |
| Production `127.0.0.1:5436/gestion` | unchanged: gate ENFORCE with 2 rows, no `gate_switch.override`, no IAM objects, no bindings; polled every 15 s from 07:08Z to 07:30Z, all four DDL facts false |
| Baseline for a later verification | `out/audit/fnd0331_baseline_20261029T224146Z.json` (logs 1365, events 298, tickets 309; md5 values in the first-attempt record) |
| Poller | stopped at the owner's order; it never verified or ingested anything |
| `PENDING_FND_LOGS.md` | still in the tree: its 13 notes are NOT in the backlog (dry run against production: all 13 `TO_APPEND`, the three tickets exist) |

## Why it failed (as far as the evidence shows)
Three consecutive reports of success (DDL, merge, branch deletion) were contradicted by origin and by production at the moment they were made. The first attempt is documented as going to the staging replica `127.0.0.1:5433/des`. The owner's conclusion on 2026-10-30: his execution environment is disconnected from the true origin and the true production database (a shadow context). Not a defect of the payload.

## Valid artifacts, frozen
- `deploy/unified-backlog-v2` and `deploy/master-override` at `8cd0ad7`: the payload (all four required checks green on that SHA).
- `chore/deployment-records-final`: the zero-input verifier, the target check, the post-deployment automation (wait, verify, ingest), this record.
- `chore/ci-credential-spec-final`: the CI/CD service account specification.
- `chore/abandoned-p1-harvest`: unrelated work kept in version control.

## Next
1. Re-link the execution environment: the first output line of the migration tool must read `target: host=127.0.0.1 port=5436 dbname=gestion`, and `git ls-remote origin master` must print the SHA that was pushed.
2. Establish the GitHub App credentials of `chore/ci-credential-spec-final`.
3. Then merge `deploy/master-override`, apply the two phases, run `scripts/audit/backlog_verify_deployment.py`, and ingest `PENDING_FND_LOGS.md` (`scripts/ops/backlog_wait_for_deployment.py` does the last two on its own).
