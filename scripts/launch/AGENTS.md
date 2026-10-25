# scripts/launch — cycle launchers (area guide)

Loaded by Claude Code the first time a file under `scripts/launch/` is read (`scripts/launch/CLAUDE.md` imports this file); any other agent reads it before changing code here. Cross-project rules (design principles P#1-P#11, restrictions R-1..R-8, backlog, live-store safety) are in the root `AGENTS.md` and apply here.

## Cycle launchers and commands

All commands: activate Conda env `des` first.

**Full P1 + P2 cycle (recommended for monthly runs):**
```batch
cd C:\desarrollo\fondos\scripts\launch
P1P2_runCycle.bat
```
Runs sequentially: P1_refreshBenchmarks → P1_discoverAllFunds → P2_discoverLoadMetrics → P2_calculateIndicators.
Aborts on the first step that fails. Log: `proyecto1/log/log_P1P2_runCycle_YYYYMMDD_HHMMSS.log`.

**Resume after a failure:** `P1P2_runCycle.bat --from N` re-runs step N in full and the steps after it.
Safe because every write in the four steps is idempotent (enforced by `tests/test_sql_explain_sweep_pg.py`);
allowed only when the last run failed at step N or later (state in `proyecto1/log/P1P2_runCycle.state`,
logic in `scripts/launch/p1p2_state.py`); `--from-any` overrides the guard and is recorded in `ingestion_log`.
The launcher also runs the statistical audits (`p2`, `costs`) with `--persist` before PASO 1 and again with
`--compare-to` after a successful run, writing the drift report to `proyecto1/log/log_P1_P2_audit_<STAMP>.log`. (There is
no repair step after PASO 2 any more: `Ongoing_Charge_Recurrent` has a single writer, `cost_scale.resolve_ongoing_charge`, FND-0034.)
The benchmark loader exits 3 (stopping PASO 1) when more than a tenth of its Morningstar calls fail or 10 fail in a row.

**Cycle phases around the four steps** (steps keep their numbers; `P1P2_runCycle.bat --help` lists every option):
a preflight (`p1p2_state.py preflight`: DB reachable, owner DSN, `FRED_API_KEY`, disk; a dead DB aborts with RC 104
without touching the resume state), then the baseline audit and a macro-beta snapshot, then the harvest
(`P1_harvestFunds.bat`, read-only on PDFs unless `--harvest-sync` / `--harvest-retire`; a failed harvest counts as
step 1). After a successful cycle, non-blocking diagnostics: final audit with drift, beta comparison
(`scripts/audit/beta_shift_audit.py`, run with the current `CALC_VERSION`), the P3 freshness verdict
(`scripts/launch/p3_freshness_check.py`, same gate as P3) and the cycle report (`scripts/launch/p1p2_cycle_report.py`,
`[ATENCION]` lines, JSON in `proyecto1/log/`). Step options are forwarded to the sub-launchers (`--no-export`,
`--skip-macro`, `--workers N`, `--force`, `--benchmarks-load`, `-- <run_pipeline args>`); optional diagnostics:
`--shadow N`, `--benchmark-gaps`, `--dashboard`, `--diag-cost`. The diagnostics never change the cycle's RC.
**Audit ownership and launcher names** (`[Prefix]_[camelCase].bat`, `NORMAS_BATCH.md` sections 14-15): the `*_runCycle.bat` orchestrators
(`P1_`, `P2_`, `P3_`, `P1P2_`, `P2P3_`, `P1P2P3_`) own the audits through `scripts/launch/lib/cyclePhases.bat`: a frozen PRE
(`pre_<stamp>_p2` / `pre_<stamp>_costs`) before anything writes - invalid or INCOMPLETE means RC 107, `--allow-no-baseline` overrides - and a
POST with `--compare-to` at each domain's last writer (P1 after step 2; P2 once, after the DRAFT when P3 is in the flow, via
`P3_buildPortfolio --audit-compare-to`: the same run is the drift and the promotion evidence; `P1P2_runCycle --defer-post-audit`). Step
launchers (`P1_discoverAllFunds`, `P2_calculateIndicators`) audit only with `--audit`. The partial orchestrators never write the P1+P2 cycle state.
Audit files live inside the repo, one folder per project: `out/audit/P1/` (benchmark B1-B7 JSON) and `out/audit/P2/` (macro-beta snapshots and
outliers); `shared.config.AUDIT_DIRS` for Python, `AUDIT_P1_DIR` / `AUDIT_P2_DIR` from `lib/common.bat` for the launchers (never under `C:\data`).
Every launcher follows `doc/reglas/NORMAS_BATCH.md` (shared `scripts/launch/lib/common.bat`): paths derived from the file's
own location, no hard-coded versions/dates, the launcher's own exit codes in 100-199 (100 usage, 101 interpreter, 102 resume
refused, 103 state, 104 preflight, 105 another instance running, 106 PostgreSQL driver blocked by Application Control — checked by `:init` and `shared/env_guard.py`; tool codes 1-99 pass through untouched), single-instance lock
shared by every `*_runCycle.bat` (`P1`, `P2`, `P3`, `P1P2`, `P2P3`, `P1P2P3`; the last is the integrated P1→P2→beta gate→P3 run: `--only-p3`
resumes just the gate + P3 on the last OK cycle), and the real standby timeout saved and restored.
Postgres SQL validity is checked by an `EXPLAIN` sweep over every statement production code executes — see
`doc/reglas/NORMAS_IMPLEMENTACION.md` §7 before adding SQL or an allowlist entry.

### Canonical launchers (machine-verified)

<!-- AUTO:BEGIN launchers -->
| Script | Domain |
|--------|--------|
| `FND-0246.bat` | — |
| `FND-0246_V2.bat` | — |
| `P1P2P3_runCycle.bat` | P1+P2+P3 |
| `P1P2_auditStatistical.bat` | P1+P2 |
| `P1P2_runCycle.bat` | P1+P2 |
| `P1_audit.bat` | P1 |
| `P1_diagCost.bat` | P1 |
| `P1_discoverAllFunds.bat` | P1 |
| `P1_harvestFunds.bat` | P1 |
| `P1_refreshBenchmarks.bat` | P1 |
| `P1_runCycle.bat` | P1 |
| `P2P3_runCycle.bat` | P2+P3 |
| `P2_audit.bat` | P2 |
| `P2_calculateIndicators.bat` | P2 |
| `P2_discoverLoadMetrics.bat` | P2 |
| `P2_runCycle.bat` | P2 |
| `P3_buildPortfolio.bat` | P3 |
| `P3_generateReport.bat` | P3 |
| `P3_runCycle.bat` | P3 |
<!-- AUTO:END launchers -->
