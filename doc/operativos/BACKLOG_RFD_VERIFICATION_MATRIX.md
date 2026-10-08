# READY_FOR_DEPLOY: verification matrix and closing workflow

`READY_FOR_DEPLOY` = developed, not deployed. A ticket is closed when what it says must be true **in production** is true, with evidence. It is never closed
in bulk: the 14 tickets of 2026-10-08 are not alike (see the table below), and several wait for an owner action no script can perform.

`scripts/audit/rfd_matrix_probe.py` (read-only, closes nothing) states, per ticket, the predicates that decide it and evaluates the machine-checkable ones:
commit on `origin/master`, flag value in `shared.config`, share of `fund_metrics` rows on the current `CALC_VERSION`, retired metric absent, no code
reference left, a P2 run with `ols_funds > 0`, a dependent ticket closed, the last result of a Windows scheduled task. The executable form of each closing
condition is the `SPEC` table at the top of the script (one line per ticket).

## Verdicts (state of 2026-10-08)

| Verdict | Tickets | Meaning |
|---|---|---|
| READY (8) | FND-0181, 0196, 0200, 0201, 0202, 0225, 0226, 0229 | every probe passes: the 20261004 bundle is deployed (flags on, 99.7% of the metric rows recomputed, OLS ran) |
| BLOCKED (owner hold) | FND-0235, 0240, 0241, 0242 | dormant flags still off; ship with the locked recalculation batch (and FND-0243 for FND-0235) |
| BLOCKED (precondition) | FND-0073, 0203 | 0073: scheduled tasks last result 267011 (never ran) and 3 (external disk absent); 0203: closes with FND-0236 (IN_PROGRESS) |

## Workflow, one ticket at a time (next work session)

1. `python scripts/audit/rfd_matrix_probe.py --md out/backlog/rfd_matrix_<date>.md`: the matrix, with nothing changed.
2. For each **READY** ticket, run its end-to-end check and keep the output as the closing evidence:
   - FND-0181 / 0196 / 0226: `pytest proyecto2/tests/calculations/test_macro_sensitivity.py` + `scripts/audit/beta_shift_audit.py --compare <snapshot> --version <CALC_VERSION>` (the P1_P2_P3 beta gate) must pass.
   - FND-0200 / 0202: `pytest proyecto2/tests/calculations -k "persistence or capture"` + the statistical audit of the P2 metrics (`AUDIT_P2.bat --mode report`) with no new ALARM against the pre-recompute baseline.
   - FND-0229: `pytest proyecto2/tests/pipeline -k ols` and a P2 RUN_SUMMARY with `ols_funds > 0` after the bump (the probe).
   - FND-0201: `pytest proyecto2/tests/readers` and the 7 STALE_FROZEN funds skipped in the next P2 RUN_SUMMARY.
   - FND-0225: `pytest proyecto3/tests` (scorer) and the probe (no code reference to the retired betas).
3. Close it with the evidence in its log: `status = CLOSED`, `closure_ts`, `solution_type = CODE_FIX`, and `deployment_ts` + `release_version` (AGENTS.md section 4); one `BACKLOG_LOGS` row per ticket. One ticket per transaction.
4. **BLOCKED** tickets get no status change; they get the named precondition in a log row, and the probe is re-run after the owner action.
5. After the owner's recalculation batch (flags 0240 / 0241 / 0242 / 0235 on): re-run the probe and the post-recompute statistical audit; close those four only with a clean audit.
6. `backlog.vw_backlog_kpis` shows the burn-down; the target is the number of tickets whose probes pass, not a number fixed in advance.
