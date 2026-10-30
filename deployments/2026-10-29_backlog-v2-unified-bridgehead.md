# Deployment record: Backlog v2 unified payload (bridgehead)

Written for: the repository owner and anyone who audits this deployment later. This is the system of record for the bridgehead; nothing about it lives only on a workstation.

| Item | Value |
|---|---|
| Date written | 2026-10-29 (UTC) |
| Remote ref | `origin/deploy/unified-backlog-v2` (`refs/heads/deploy/unified-backlog-v2`, unprotected feature branch) |
| Exact SHA | `8cd0ad7ad8356491bff3acd3fd4c67e4560f2e14` |
| Base | `origin/master` = `0979d804c62e36feac792c35d158984f12e3c37f`, an ancestor of the bridgehead: a fast-forward is possible; 19 commits ahead |
| State of the branch | locked for deployment by the owner (2026-10-29): nobody pushes to it |
| Built by | the agent session as the local integration node (git plumbing, no working tree touched), pushed by order of the owner |

## Contents, in merge order
`feat/backlog-v2-actor-env` e0a7a2c, `feat/backlog-v2-gate-override` 2e8bd4d, `feat/backlog-v2-live-guard` 0a0968a, `feat/backlog-v2-iam` ea4d5cd, `fix/relations-down-keeps-history` 2dda3a1, `chore/iam-rollback-docs` d0736f5;
then on top: binding audit 45dc8ec, atomic Phase B rollback and off-hours flag d74d63a, rollback playbook 23ca8ef, non-destructive IAM rollback 8cd0ad7.

Conflicts resolved while merging: `doc/especificacion/LIMITACIONES_CONOCIDAS_BACKLOG_V2.md` (both L7 and L8 kept) and `db/pg_backlog/down/40_relations_v2_down.sql` (the incoming fix, the strict superset). No conflict marker remains.

## Evidence (all on exactly this commit)
- Local, hermetic PostgreSQL 17, on a `git archive` of the commit: sentinel check OK; the full backlog, EXPLAIN-sweep, pre-flight and dialect suites in normal order (exit 0), in reverse order (exit 0), and every pure test file alone (234 passed; live-only tests skipped by design).
- Real command-line tool rehearsal on a throwaway `gestion` database: relations and iam apply and verify ACCEPTED; a lock timeout fails with exit code 1 and leaves nothing; `--phase iam --down` keeps the binding and window history; `--phase relations --down` keeps the gate in ENFORCE (41 samples from a second session during the rollback).
- GitHub checks on the SHA, all four required ones, read through the public API at 2026-10-29T22:17Z: Full test suite (environment-linux.yml, real Postgres 17) success (22:15:40Z); PG fixture smoke test success; Verify all AUTO sentinels are in sync success; Verify statistical-audit catalog consistency success.

## How to publish and deploy
1. Pull request `deploy/unified-backlog-v2` into `master` with **Create a merge commit** (not squash, not rebase: they would change the SHAs).
2. Deploy the database changes from `doc/operativos/BACKLOG_IAM_ROLLBACK_PLAYBOOK.md`, section 0 (checks, restore point, baseline) and then section 1; rollback levels are in section 3.
3. Orphaned branch: `origin/main` = `36e7ab955f62018e0ba7e02db0dac7d1c695838f` ("Initial commit") has no common ancestor with `master` or the bridgehead. GitHub's default branch is `master`, so `main` can be deleted; it has no open pull request.

## Record keeping rule
Deployment artifacts, bridgehead references and release logs are written here (`deployments/`), in version control, never in a temporary or scratch directory (owner order, 2026-10-29).
