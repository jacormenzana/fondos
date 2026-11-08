# Record: full P2 cycle of 2026-11-08 ran without a cycle restore point

Written 2026-11-08 (owner directive: "formally document the lack of a restore point in this cycle"). Epic FND-0391, ticket FND-0393.

## What ran
`scripts/launch/P2_runCycle.bat`, started 14:33:03 and ended `Fin OK` 17:38:28, by the agent session on the owner's instruction (log `proyecto2/log/log_P2_runCycle_20261108_143304.log`).
Steps: PRE audit `pre_20261108_143304_p2`, macro + NAV load, indicators (`run_id=20261108_155048`: 2,755 processed, 988 cache hits, 0 errors, 871,907 rows written, 68,775 stale rolling rows pruned, 3 funds quarantined),
POST audit `post_20261108_173304_p2` (PASS_WITH_WARNINGS).

## What was missing
The launcher's preflight printed `[preflight] WARN FONDOS_PG_DSN_OWNER not set: no restore point will be created for this cycle`. The owner connection string is owner-only (vault, never in the agent environment), so the cycle created
no restore point of its own. The preflight treats this as a warning, not a block, and the cycle ran.

## What the cycle wrote (all derived or append-only, all recomputable)
`fund_metrics`, `fund_metric_timeseries` (replaced per fund), `fund_metric_alerts`, `fund_metric_state`, the gold materialised views, the macro and monthly/daily NAV loads, `p2_pipeline_log`, `control.audit_*`.
Three funds had their metrics cleared by the NAV guards and were set to `INVALID_NAV` (LU0987206850 in the universe; LU0135928025, 482 metric rows, and LU2473381015 outside it); each re-opens by itself when its series is repaired.
No classification (`fund_master`), no portfolio scenario and no backlog row was written by the cycle.

## Why the exposure is limited, and what is NOT verified
- A P2 pass is idempotent and re-derives everything above from `fund_nav_monthly`, `series_macro` and the `fund_metric_state` fingerprints; a bad pass is repaired by running P2 again, not by restoring.
- Recovery assets exist on paper: WAL archiving with a PITR restore drill (FND-0071, closed 2026-09-26) and an off-box copy of the logical dumps (FND-0070, closed 2026-10-02).
  **Neither was re-verified on 2026-11-08**: whether WAL archiving is still running and when the newest off-box dump was written are unknown to this record.
- The macro and NAV loads pulled new vendor data into bronze; those rows are append-only history and are not undone by a re-run.

## Owner options
1. Accept: the data written is recomputable; no action.
2. Check the recovery assets now (`FND-0070` / `FND-0071` runbooks) so that the next cycle has a known-good point to return to.
3. For the next cycle, start it from a shell where `FONDOS_PG_DSN_OWNER` is available so the launcher creates its restore point.
