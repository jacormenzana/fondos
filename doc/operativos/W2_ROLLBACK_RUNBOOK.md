# W2 Rollback Runbook — first live cycle on Postgres 17 (FND-0099)

**Scope.** Recovery if the first real load + monthly cycle + P3 build on the live Postgres
(`fondos_postgres`, `127.0.0.1:5436`) fails or writes bad data. **Not** a rollback to SQLite:
`db/fondos.sqlite` is sealed at the 2026-09-23 cutover and lacks everything written since (the
`fondos_app` role and grants, `gold.mv_*` matviews, audit rows, `control.*` tables, all new loads).
Use it only as a forensic baseline.

**Gate.** W2 does not start until §1 is complete: a verified pre-W2 dump and FND-0071 live (WAL
archiving + a passed drill). The off-box copy (FND-0070) was **waived for W2 by the owner on
2026-09-26** — the data stays on the local disk and is transferred to an external device later; until
then a single-disk failure loses data, dumps and WAL together (FND-0070 stays open).

## 0. Decide: re-run or roll back

Every write in the four launcher steps is idempotent (upsert / full replace / append-only log;
enforced by `tests/test_sql_explain_sweep_pg.py`). **The default answer to a failed step is fix the
cause and resume with `P1_P2_Complete.bat --from N`, not a restore.** The guard refuses a resume past
the failed step (`proyecto1/log/P1_P2_Complete.state`, `scripts/launch/p1p2_state.py`).

Roll back only when data is **wrong**, not merely incomplete:
- the drift report `proyecto1/log/log_P1_P2_audit_<STAMP>.log` or the P1 cycle summary shows a mass
  change no code change explains (e.g. `Fund_Nature` flips across many funds, metrics collapsing);
- a failure cannot be fixed and resumed within the working session and leaves tables half-written
  in a way `--from N` cannot repair;
- a bad manual/SQL action ran against live.

The owner makes the call. Until then **freeze**: disable Task Scheduler jobs, close DBeaver/Metabase,
do not start another launcher.

## 1. Before W2 (all mandatory)

1. **Backup + restore proof** (WSL Ubuntu, keepalive running): 
   `bash /mnt/c/desarrollo/fondos/scripts/ops/backup_live_pg.sh --verify-restore`
   Expect `BACKUP OK` and `MATCH` on every table (~6 min). Note the file name `fondos_<STAMP>.dump`
   (in `C:\data\backups\fondos_pg\`) — this is the **restore point**. Record it in the W2 ticket.
2. **WAL/PITR live** (FND-0071): `bash .../setup_wal_archive.sh status` shows a real
   `archive_command`, `failed=0`, and a complete base backup; `... drill` printed `DRILL PASSED`.
3. **Named restore point**: as `fondos_owner` run `select pg_create_restore_point('pre_w2_<YYYYMMDD>')`
   and note the returned LSN plus the wall-clock time (used in §3).
4. **Off-box copy** (FND-0070) of the dump, `globals_*.sql` and the newest base backup + WAL, verified
   readable at the destination. *(Waived for W2 — see the Gate note; still to be done.)*
5. **Files outside Postgres**: list `C:\data\fondos\kiid_retired\` (rollback of the database does not
   move PDFs). Run `p1_kiid_sync --sync` (additive) in W2, but keep `--retire-orphans` for a
   separate step after review — it moves PDFs on disk, which a database restore does not undo.
6. Confirm `.env` DSNs and `FONDOS_BACKLOG_PG_DSN` are set, and that the current backlog is backed up
   (the same script dumps `gestion`).

## 2. Path A — logical restore (available now)

Restores the `fondos` database from the pre-W2 dump. Loses everything written after it. Rehearsed
piece: the restore into a scratch database is exactly what `--verify-restore` does.

1. Freeze (§0). Keep the failed state: **do not drop it**.
2. Restore next to it (WSL; password file under `/opt/docker/db/postgresql17/secrets/`):
   ```
   P="docker exec -e PGPASSWORD=$(cat /opt/docker/db/postgresql17/secrets/postgres_password) fondos_postgres"
   $P psql -h 127.0.0.1 -U fondos_owner -d postgres -c "CREATE DATABASE fondos_restore"
   docker cp /mnt/c/data/backups/fondos_pg/fondos_<STAMP>.dump fondos_postgres:/tmp/restore.dump
   $P pg_restore -h 127.0.0.1 -U fondos_owner -d fondos_restore -j 4 --no-owner /tmp/restore.dump
   ```
3. Validate `fondos_restore`: row counts of `silver.fund_master`, `bronze.fund_nav_daily`,
   `gold.fund_metric_timeseries`, `gold.fund_metrics`, `gold.fund_scores`, `control.migration_state`
   match the pre-W2 values recorded in §1.1; index count matches.
4. Swap (needs zero connections to both databases; stop launchers, DBeaver, Metabase first):
   ```
   ALTER DATABASE fondos RENAME TO fondos_failed_w2;
   ALTER DATABASE fondos_restore RENAME TO fondos;
   ```
5. Re-check grants for the pipeline role, then run the acceptance below. If `fondos_app` lacks
   privileges, reapply with the owner (`db/pg/`, `scripts/ops/create_readonly_roles.sh` for the
   read-only roles) — do not widen `fondos_app` beyond DML.
6. Physical files: move back from `kiid_retired/<date>/` any PDF moved by a rolled-back
   `--retire-orphans` (compare with the §1.5 listing).
7. Keep `fondos_failed_w2` for forensics until the incident ticket is closed, then drop it.

## 3. Path B — point-in-time (after FND-0071 is live)

Use when the dump is too old or the damage began at a known moment (e.g. a bad step at 14:10):
restore the newest base backup + archived WAL up to `pre_w2_<date>` (named restore point) or a time
just before the damage, in a **scratch** container, exactly as `setup_wal_archive.sh drill` does
(`recovery_target_name` / `recovery_target_time` instead of the drill's LSN marker). Inspect it, then
promote it to live by the same rename procedure as Path A (dump the scratch database with `pg_dump -Fc`
and restore it as `fondos_restore`).

**Drilled 2026-09-26 (FND-0101):** `setup_wal_archive.sh drill --named --swap` creates a named restore
point on live, recovers the newest base + WAL to it in a scratch container, then rehearses the promote
steps there (dump → `fondos_restore` → `ALTER DATABASE` rename swap) and checks the probe and the
privileges of `fondos_app` / `fondos_ro`. Result: PASSED — recovery ≈ 3 min (408 WAL segments), dump+restore
≈ 10 min for the whole database, `fondos_app` and `fondos_ro` privileges intact after the swap. Budget
≈ 15 min of downtime for a real Path B on this hardware. Not covered by the drill: a *time* target
(`recovery_target_time`) — same mechanism, different key in `postgresql.auto.conf`.

## 4. Acceptance after any restore

- `python -X utf8 -m proyecto2.src.pipeline.run_pipeline --isin <ISIN> --dry-run` (as `fondos_app`)
- `python scripts/launch/p3_build_portfolio.py --dry-run`
- `python scripts/ops/run_pg_tests.py` unaffected (hermetic, does not touch live)
- backlog reachable: `python -m shared.backlog_client --check-dsn`
- record the outcome and the restore point used on FND-0099 / the incident ticket.

## 5. After a rollback

Fix the root cause, rehearse the fix on a restored scratch copy (never first on live), take a fresh
verified backup, and only then repeat W2. Open a backlog ticket for every defect found.
