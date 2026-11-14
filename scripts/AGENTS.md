# scripts/ - how the scripts are organised (area guide)

Loaded by Claude Code the first time a file under `scripts/` is read (`scripts/CLAUDE.md` imports this file); any other agent reads it **before adding, moving or renaming a script**. Cross-project rules (design principles, restrictions, backlog, Directive D-0) live in the root `AGENTS.md`. Each domain below that has its own guide (`launch/`, `gov/repo/`) is covered by it for the details of that folder.

## The model: effect x object

A script lives in **domain / object** - the *domain* says what kind of work it does to the system (its effect), the *object* says what it acts on. Place a script by the **first row that matches**:

| # | Effect of the script | Domain | Object folders |
|---|---|---|---|
| 1 | Runs a pipeline stage: a `launch/*.bat` calls it, or it persists data or a run artifact that another script reads | `launch/` | (flat: it is the owner's cycle surface, see `launch/AGENTS.md`) |
| 2 | One-shot schema change or data correction, **and its acceptance check** (run once, `--apply`) | `mig/` | `fondos/`, `backlog/`, `cutover/` |
| 3 | Recurring upkeep of infrastructure: backups, recovery, database and host maintenance, Windows schedules | `mnt/` | `backup/`, `db/`, `host/`, `sched/`, `lib/` |
| 4 | **Read-only** on the database; its output is a report for a person and nothing another script reads (audits, forensics, OFF/ON diffs, simulations) of product data and models | `audit/` | `stat/`, `nav/`, `p1/`, `p2/`, `p3/` |
| 5 | Governs change: backlog operation, delivery and publishing, repository hygiene, documents and git hooks | `gov/` | `backlog/`, `delivery/`, `repo/` (+ `repo/hooks/`) |

Tie-breakers: a script that both reads and writes the database is **not** `audit/`; a one-shot that fixes data belongs in `mig/` even if it also reports; a tool about the backlog or git belongs in `gov/` even if it is read-only; shell helpers shared by one domain live in its `lib/`.

## The folders (every leaf folder is listed here; `tests/test_scripts_layout.py` enforces it)

| Folder | Holds |
|---|---|
| `launch/` | cycle launchers (`P1_`, `P2_`, `P3_`, `*_runCycle`), their `lib/`, and the python helpers they call (state, report, gates, P3 build / report / promote / PIT backtest) |
| `mig/fondos/` | migrations and one-shot fixes of the operational `fondos` database (`migrate_*`, `apply_*`, `clean_*`) |
| `mig/backlog/` | the backlog database: `migrate_backlog_*`, the step-0 and v3 bootstrap manifests, and the acceptance and replay checks of each phase |
| `mig/cutover/` | the SQLite-to-Postgres cutover tools (seed, reconcile, BI compat view, SQLite archive); historical, kept until archived |
| `mnt/backup/` | logical backup, rotating backup, WAL archive and PITR, off-box copy, Metabase backup |
| `mnt/db/` | Postgres preventive maintenance (`pg_01..05`, `pg_run_all.sh`), read-only roles, relocation |
| `mnt/host/` | the WSL2 / Docker host maintenance (`wsl_01..06`, `wsl_run_all.sh`) |
| `mnt/sched/` | the registrations of the Windows scheduled tasks |
| `mnt/lib/` | shell helpers shared by `mnt/` (`common.sh`, `cycle_guard.sh`) |
| `audit/stat/` | the statistical distribution audit, its incidents, accepted residuals and catalog checks |
| `audit/nav/` | NAV forensics, fund dossiers, data-suspicion report, fault-injection harness |
| `audit/p1/` | P1 classification and cost audits and release checks |
| `audit/p2/` | P2 metric audits, perf harness, period-key invariance, shadow reconciliation |
| `audit/p3/` | P3 selection simulations, switch diffs, the PIT value / OOS / cadence studies and reports |
| `gov/backlog/` | the backlog tooling: autopilot, links, modules, impact, gates, deploy watcher, vault, close release |
| `gov/delivery/` | delivery and publication: test receipt, auto publish, impact-selection evidence, hermetic PG tests, branch tools |
| `gov/repo/` | repository hygiene: pre-flight, AGENTS.md sentinel sync and split check, fixture lint, retention report |
| `gov/repo/hooks/` | the git hook installers (`commit-msg`, `pre-commit`, `post-commit`, `reference-transaction`) |

## Rules

- **Never** put a file directly in `audit/`, `mig/`, `mnt/` or `gov/` (only in an object folder), add an object folder that is not in the table above, or add a top-level domain, without the owner. `launch/` is flat by design.
- Python tools are run by path and import each other by bare module name: a script that imports a sibling from **another** folder puts that folder on `sys.path` explicitly (see `gov/backlog/owner_deploy.py`); the repository root is derived from the file's own location (`Path(__file__).resolve().parents[3]` for a script two folders under `scripts/`), never hard-coded.
- Shell scripts reach their `lib/` as `$(dirname "$0")/../lib/...`; Windows scheduled tasks and the Startup launcher hold absolute paths: moving a script means re-pointing them (`mnt/sched/`, `gov/backlog/owner_deploy.py install-startup`).
- A path-keyed registry (`shared/test_impact.py`, `shared/backlog_writers.py`, `shared/backlog_modules.py`, `owner_deploy.WATCH_CODE`) names scripts by path: changing one is a rule change, not a rename.
- Before moving or renaming a script: a reference scan of the whole repository, one atomic commit, and `tests/test_scripts_layout.py` green.

## Commands the owner types from memory (old -> new)

| What | Now |
|---|---|
| close deployed tickets (dry run first) | `python scripts/gov/backlog/close_release.py` (was `scripts/ops/close_release.py`) |
| read / decide the collision queue | `python scripts/gov/backlog/gate_adjudicate.py` (was `scripts/audit/gate_adjudicate.py`) |
| collision-gate mode | `python scripts/gov/backlog/gate_mode.py` (was `scripts/audit/gate_mode.py`) |
| autopilot mode | `python scripts/gov/backlog/autopilot_mode.py` (was `scripts/audit/autopilot_mode.py`) |
| owner vault | `python scripts/gov/backlog/owner_vault.py` (was `scripts/ops/owner_vault.py`) |
| deploy watcher / pending backlog DDL | `python scripts/gov/backlog/owner_deploy.py` (was `scripts/ops/owner_deploy.py`) |
| publish master by hand (hotfix: `--now`) | `python scripts/gov/delivery/auto_publish.py` (was `scripts/audit/auto_publish.py`) |
| test receipt for the commit | `python scripts/gov/delivery/delivery_attest.py` (was `scripts/audit/delivery_attest.py`) |
| read-only pre-flight | `python scripts/gov/repo/preflight_check.py` (was `scripts/audit/preflight_check.py`) |
| ticket relationships (`--new`, `--for`) | `python scripts/gov/backlog/backlog_links.py` (was `scripts/audit/backlog_links.py`) |
| hermetic Postgres tests | `python scripts/gov/delivery/run_pg_tests.py` (was `scripts/ops/run_pg_tests.py`) |
| AGENTS.md sentinel sync (`--check` / `--write`) | `python scripts/gov/repo/sync_agents_md.py` (was `scripts/audit/sync_agents_md.py`) |
| statistical audit | `python scripts/audit/stat/run_statistical_audit.py` (was `scripts/audit/run_statistical_audit.py`) |
| logical backup, rotating (two newest dates) | `bash scripts/mnt/backup/backup_rotating.sh` (was `scripts/ops/backup_rotating.sh`) |

Cycle launchers (`scripts/launch/*.bat`) did not move.

## Old -> new path map (every file that moved on 2026-11-14)

Dated records (`deployments/`, `doc/decisiones/`, `doc/memoria/`, `doc/analisis/`, `doc/especificacion/`, closed `doc/backlog/` handoffs, `db/` DDL comments) keep the paths of their day; resolve them with this table. Two stray files were deleted: `launch/redireccion` (empty), `launch/P2_LoadMetrics.txt` (an old hard-coded draft of `P2_discoverLoadMetrics.bat`).

| Old (under `scripts/`) | New (under `scripts/`) |
|---|---|
| `audit/data_suspect_report.py` | `audit/nav/data_suspect_report.py` |
| `audit/fault_injection_fixture.py` | `audit/nav/fault_injection_fixture.py` |
| `audit/fault_injection_run.py` | `audit/nav/fault_injection_run.py` |
| `audit/fund_dossier.py` | `audit/nav/fund_dossier.py` |
| `audit/nav_forensics.py` | `audit/nav/nav_forensics.py` |
| `debug/db_fondos_scraper.py` | `audit/p1/db_fondos_scraper.py` |
| `debug/debug_mstarpy.py` | `audit/p1/debug_mstarpy.py` |
| `diag/diag_cost_extraction.py` | `audit/p1/diag_cost_extraction.py` |
| `audit/eur_view_rehearsal.py` | `audit/p1/eur_view_rehearsal.py` |
| `audit/family_key_switch_diff.py` | `audit/p1/family_key_switch_diff.py` |
| `audit/official_name_coverage.py` | `audit/p1/official_name_coverage.py` |
| `audit/verify_family_refresh.py` | `audit/p1/verify_family_refresh.py` |
| `audit/verify_fund_currency.py` | `audit/p1/verify_fund_currency.py` |
| `audit/beta_shift_audit.py` | `audit/p2/beta_shift_audit.py` |
| `audit/macro_freshness_check.py` | `audit/p2/macro_freshness_check.py` |
| `audit/p2_perf_harness.py` | `audit/p2/p2_perf_harness.py` |
| `audit/p2_vol_floor_impact.py` | `audit/p2/p2_vol_floor_impact.py` |
| `audit/period_key_invariance.py` | `audit/p2/period_key_invariance.py` |
| `audit/shadow_reconciliation.py` | `audit/p2/shadow_reconciliation.py` |
| `audit/eligibility_switch_diff.py` | `audit/p3/eligibility_switch_diff.py` |
| `audit/nature_move_impact.py` | `audit/p3/nature_move_impact.py` |
| `launch/p3_pit_cadence.py` | `audit/p3/p3_pit_cadence.py` |
| `launch/p3_pit_oos.py` | `audit/p3/p3_pit_oos.py` |
| `launch/p3_pit_value.py` | `audit/p3/p3_pit_value.py` |
| `audit/pit_trial_ledger.py` | `audit/p3/pit_trial_ledger.py` |
| `audit/pit_variants_report.py` | `audit/p3/pit_variants_report.py` |
| `audit/sovereign_stress_backtest.py` | `audit/p3/sovereign_stress_backtest.py` |
| `audit/wrong_doc_switch_diff.py` | `audit/p3/wrong_doc_switch_diff.py` |
| `audit/accept_audit_residual.py` | `audit/stat/accept_audit_residual.py` |
| `audit/audit_incident.py` | `audit/stat/audit_incident.py` |
| `audit/check_audit_skill_sync.py` | `audit/stat/check_audit_skill_sync.py` |
| `audit/check_catalog_consistency.py` | `audit/stat/check_catalog_consistency.py` |
| `audit/cost_gate_replay.py` | `audit/stat/cost_gate_replay.py` |
| `audit/criticality_diff.py` | `audit/stat/criticality_diff.py` |
| `audit/governance_kpis.py` | `audit/stat/governance_kpis.py` |
| `audit/run_statistical_audit.py` | `audit/stat/run_statistical_audit.py` |
| `audit/autopilot_mode.py` | `gov/backlog/autopilot_mode.py` |
| `audit/backlog_autopilot.py` | `gov/backlog/backlog_autopilot.py` |
| `audit/backlog_digest.py` | `gov/backlog/backlog_digest.py` |
| `ops/backlog_drift.py` | `gov/backlog/backlog_drift.py` |
| `audit/backlog_hygiene.py` | `gov/backlog/backlog_hygiene.py` |
| `audit/backlog_impact.py` | `gov/backlog/backlog_impact.py` |
| `audit/backlog_links.py` | `gov/backlog/backlog_links.py` |
| `audit/backlog_links_calibration.py` | `gov/backlog/backlog_links_calibration.py` |
| `audit/backlog_modules.py` | `gov/backlog/backlog_modules.py` |
| `ops/backlog_staging.py` | `gov/backlog/backlog_staging.py` |
| `audit/backlog_sync.py` | `gov/backlog/backlog_sync.py` |
| `ops/close_release.py` | `gov/backlog/close_release.py` |
| `audit/commit_msg_gate.py` | `gov/backlog/commit_msg_gate.py` |
| `audit/gate_adjudicate.py` | `gov/backlog/gate_adjudicate.py` |
| `audit/gate_calibrator.py` | `gov/backlog/gate_calibrator.py` |
| `audit/gate_mode.py` | `gov/backlog/gate_mode.py` |
| `audit/gate_protection.py` | `gov/backlog/gate_protection.py` |
| `ops/owner_deploy.py` | `gov/backlog/owner_deploy.py` |
| `ops/owner_vault.py` | `gov/backlog/owner_vault.py` |
| `audit/pre_push_gate.py` | `gov/backlog/pre_push_gate.py` |
| `audit/rfd_matrix_probe.py` | `gov/backlog/rfd_matrix_probe.py` |
| `audit/verify_attestation.py` | `gov/backlog/verify_attestation.py` |
| `audit/writer_proof.py` | `gov/backlog/writer_proof.py` |
| `audit/auto_publish.py` | `gov/delivery/auto_publish.py` |
| `audit/delivery_attest.py` | `gov/delivery/delivery_attest.py` |
| `ops/github_protect_master.py` | `gov/delivery/github_protect_master.py` |
| `audit/impact_selection_evidence.py` | `gov/delivery/impact_selection_evidence.py` |
| `ops/prune_merged_branches.py` | `gov/delivery/prune_merged_branches.py` |
| `ops/publish_master.py` | `gov/delivery/publish_master.py` |
| `ops/run_pg_tests.py` | `gov/delivery/run_pg_tests.py` |
| `audit/AGENTS.md` | `gov/repo/AGENTS.md` |
| `audit/CLAUDE.md` | `gov/repo/CLAUDE.md` |
| `audit/agents_md_split_ledger.txt` | `gov/repo/agents_md_split_ledger.txt` |
| `audit/artifact_retention.py` | `gov/repo/artifact_retention.py` |
| `audit/check_agents_md_split.py` | `gov/repo/check_agents_md_split.py` |
| `audit/fixture_schema_lint.py` | `gov/repo/fixture_schema_lint.py` |
| `audit/commit-msg.sh` | `gov/repo/hooks/commit-msg.sh` |
| `audit/post-commit.sh` | `gov/repo/hooks/post-commit.sh` |
| `audit/pre-commit.sh` | `gov/repo/hooks/pre-commit.sh` |
| `audit/reference-transaction.sh` | `gov/repo/hooks/reference-transaction.sh` |
| `audit/preflight_check.py` | `gov/repo/preflight_check.py` |
| `audit/sync_agents_md.py` | `gov/repo/sync_agents_md.py` |
| `audit/backlog_actor_alias_acceptance.py` | `mig/backlog/backlog_actor_alias_acceptance.py` |
| `audit/backlog_phase_a_acceptance.py` | `mig/backlog/backlog_phase_a_acceptance.py` |
| `audit/backlog_phase_b_replay.py` | `mig/backlog/backlog_phase_b_replay.py` |
| `audit/backlog_phase_c_acceptance.py` | `mig/backlog/backlog_phase_c_acceptance.py` |
| `ops/backlog_step0.py` | `mig/backlog/backlog_step0.py` |
| `ops/backlog_step0_manifest.yaml` | `mig/backlog/backlog_step0_manifest.yaml` |
| `mig/backlog_v3_bootstrap.py` | `mig/backlog/backlog_v3_bootstrap.py` |
| `mig/backlog_v3_manifest.yaml` | `mig/backlog/backlog_v3_manifest.yaml` |
| `ops/migrate_backlog_links.py` | `mig/backlog/migrate_backlog_links.py` |
| `ops/migrate_backlog_v2.py` | `mig/backlog/migrate_backlog_v2.py` |
| `ops/archive_sqlite.py` | `mig/cutover/archive_sqlite.py` |
| `mig/gen_bi_compat.py` | `mig/cutover/gen_bi_compat.py` |
| `mig/pg_reconcile.py` | `mig/cutover/pg_reconcile.py` |
| `mig/pg_seed.py` | `mig/cutover/pg_seed.py` |
| `ops/apply_benchmark_ms_checks.py` | `mig/fondos/apply_benchmark_ms_checks.py` |
| `ops/apply_fmts_pivot.py` | `mig/fondos/apply_fmts_pivot.py` |
| `ops/clean_stale_metric_rows.py` | `mig/fondos/clean_stale_metric_rows.py` |
| `ops/fnd0177_delete_bad_daily_rows.py` | `mig/fondos/fnd0177_delete_bad_daily_rows.py` |
| `ops/migrate_audit_accepted_finding.py` | `mig/fondos/migrate_audit_accepted_finding.py` |
| `ops/migrate_audit_finding_isin.py` | `mig/fondos/migrate_audit_finding_isin.py` |
| `ops/migrate_audit_finding_lineage.py` | `mig/fondos/migrate_audit_finding_lineage.py` |
| `ops/migrate_audit_finding_p3_gate.py` | `mig/fondos/migrate_audit_finding_p3_gate.py` |
| `ops/migrate_audit_governance.py` | `mig/fondos/migrate_audit_governance.py` |
| `ops/migrate_audit_incident.py` | `mig/fondos/migrate_audit_incident.py` |
| `ops/migrate_audit_run.py` | `mig/fondos/migrate_audit_run.py` |
| `ops/migrate_cycle_flag_read_ts.py` | `mig/fondos/migrate_cycle_flag_read_ts.py` |
| `ops/migrate_cycle_telemetry.py` | `mig/fondos/migrate_cycle_telemetry.py` |
| `ops/migrate_fund_metric_family_state.py` | `mig/fondos/migrate_fund_metric_family_state.py` |
| `ops/migrate_fund_metric_state_ols_calc_version.py` | `mig/fondos/migrate_fund_metric_state_ols_calc_version.py` |
| `ops/migrate_portfolio_scenario_status.py` | `mig/fondos/migrate_portfolio_scenario_status.py` |
| `ops/backup_live_pg.sh` | `mnt/backup/backup_live_pg.sh` |
| `ops/backup_metabase.sh` | `mnt/backup/backup_metabase.sh` |
| `ops/backup_offbox.sh` | `mnt/backup/backup_offbox.sh` |
| `ops/backup_rotating.sh` | `mnt/backup/backup_rotating.sh` |
| `ops/pitr_cycle.sh` | `mnt/backup/pitr_cycle.sh` |
| `ops/setup_wal_archive.sh` | `mnt/backup/setup_wal_archive.sh` |
| `ops/create_readonly_roles.sh` | `mnt/db/create_readonly_roles.sh` |
| `ops/maintenance/pg/pg_01_health_check.sh` | `mnt/db/pg_01_health_check.sh` |
| `ops/maintenance/pg/pg_02_vacuum_analyze.sh` | `mnt/db/pg_02_vacuum_analyze.sh` |
| `ops/maintenance/pg/pg_03_index_maintenance.sh` | `mnt/db/pg_03_index_maintenance.sh` |
| `ops/maintenance/pg/pg_04_capacity_report.sh` | `mnt/db/pg_04_capacity_report.sh` |
| `ops/maintenance/pg/pg_05_backup_verify.sh` | `mnt/db/pg_05_backup_verify.sh` |
| `ops/maintenance/pg/pg_run_all.sh` | `mnt/db/pg_run_all.sh` |
| `ops/relocate_live_pg17.sh` | `mnt/db/relocate_live_pg17.sh` |
| `ops/maintenance/wsl/wsl_01_health_check.sh` | `mnt/host/wsl_01_health_check.sh` |
| `ops/maintenance/wsl/wsl_02_docker_cleanup.sh` | `mnt/host/wsl_02_docker_cleanup.sh` |
| `ops/maintenance/wsl/wsl_03_os_updates.sh` | `mnt/host/wsl_03_os_updates.sh` |
| `ops/maintenance/wsl/wsl_04_logs_tmp_cleanup.sh` | `mnt/host/wsl_04_logs_tmp_cleanup.sh` |
| `ops/maintenance/wsl/wsl_05_host_vhdx.ps1` | `mnt/host/wsl_05_host_vhdx.ps1` |
| `ops/maintenance/wsl/wsl_06_backup_pg.sh` | `mnt/host/wsl_06_backup_pg.sh` |
| `ops/maintenance/wsl/wsl_run_all.sh` | `mnt/host/wsl_run_all.sh` |
| `ops/maintenance/lib/common.sh` | `mnt/lib/common.sh` |
| `ops/lib/cycle_guard.sh` | `mnt/lib/cycle_guard.sh` |
| `ops/register_backup_tasks.ps1` | `mnt/sched/register_backup_tasks.ps1` |
| `ops/maintenance/register_maintenance_tasks.ps1` | `mnt/sched/register_maintenance_tasks.ps1` |
