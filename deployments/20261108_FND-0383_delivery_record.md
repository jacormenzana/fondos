# Delivery record: FND-0383 (P2 audit findings of 2026-11-04: stuck betas, NAV scale jumps, orphan ISINs)

Written 2026-11-08 for the backlog record. Delivering commits: `4ccb9d9` (code, full receipt PASS: 6,643 passed, 0 failed, 1,826 s); this record closes the ticket.

## What the ticket found, what was done, and the evidence

| Finding | Root cause | Fix | Evidence after the owner's P2 pass (`run_id=20261108_095000`, `quarantined=1`) |
|---|---|---|---|
| LU0232465467 kept 9 betas loaded 2026-07-16 | The pipeline admits funds with 36 NAV months (`MIN_NAV_MACRO`) but the OLS needs 60 overlapping months (`MACRO_OLS_MIN_OBS`), so the OLS returned no rows for it. `replace_beta_set` returned before its DELETE on an empty result while the pipeline still marked the OLS as run and advanced `fund_metric_state` to Q4: the old set stayed labelled fresh. Only this fund of 3,413 with betas was affected | An empty result now replaces the full macro set (betas, `macro_r2`, `macro_alpha`, `macro_n_obs`, `energy_sensitivity_pct`, `hy_spread_sensitivity_pct`) with nothing | The fund has 0 betas and 0 macro outputs; its other 392 metrics are intact (45 sortino rows) |
| LU0987206850: Sortino 54.9 (SORTINO_BOUND) | A stale carry-forward followed by a catch-up: NAV 61.2742 for 23 consecutive months, then +70% (2026-06). `validate_nav` only rejects steps above 8x | `validate_nav_stale_flat` (12 identical monthly NAVs then a move above 40%), monthly series only, `NAV_STALE_FLAT_ENABLED`. Census of 3,750 series / 677,329 rows: exactly 2 rejected (1 in the universe), both passing `validate_nav`; the 5-9 month runs of the Eastern Europe funds re-priced after the 2022 dealing suspension pass on purpose | The P2 log reads "NAV congelado 23 observaciones seguidas en 61.2742 y luego un salto de +70% en 2026-06"; the fund is INVALID_NAV with 0 metrics (414 rows removed) and appears in no finding of the new P2 audit except `CPI_COVERAGE_TRAILING_GAP` |
| LU2473381015: INVALID_NAV scale jumps | A 235x step: rejected by the existing 8x test | none needed | stays quarantined |
| 7 ISINs in `fund_nav_monthly` without `fund_master` | Cleared by a later P1 pass | none needed | 0 of 3,750 series have no `fund_master` row (2026-11-08) |

## Post-run audit (both domains, run on 2026-11-08 10:59)

- **P2 metrics** `post_20261108_105955_p2`: PASS_WITH_WARNINGS, universe 2,904 (was 2,905 before the quarantine). `SORTINO_BOUND` keeps 1 WARN, no longer for LU0987206850. The previous run `audit_20261107T220005Z` listed `SORTINO_BOUND` and `SNAPSHOT_HELD_OUT` for that fund; the new one lists neither.
- **Costs** `post_20261108_105955_costs`: FAIL. Not caused by this ticket: see the diagnostic below.

## Stated limits

- The rule that would show the stuck betas, `BETA_ORPHAN_BATCH`, has produced **no finding in any persisted P2 audit run**, before or after the fix. The audit therefore cannot show the betas clearing; the evidence for them is the direct query above.
- The audit was run on a working tree that other sessions had modified (`run_statistical_audit.py`, `run_pipeline.py`, `validators.py`, `db_readers.py`, `config.py`), so the run header records `git_clean = false`.
- The cost audit FAIL, diagnosed on 2026-11-08, is a separate matter: a new fund (LU2132880597) violates `OC_NOT_CONTAMINATED` and is not covered by the FND-0034 acceptance.
