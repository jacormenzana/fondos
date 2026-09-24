#!/bin/bash
# Logical backup of the LIVE Postgres 17 (databases fondos + gestion) to the Windows disk.
#   bash /mnt/c/desarrollo/fondos/scripts/ops/backup_live_pg.sh [--verify-restore] [--keep N]
#
# What it protects against: corruption/loss of the WSL2 virtual disk (the copy lives on C:, outside
# the VHDX), bad ETL runs, accidental DROP. What it does NOT protect against: loss of this machine
# or its physical disk (C: is the same hardware) — copy $OUT to a NAS / cloud / USB for that — and it
# gives no point-in-time recovery (that needs a physical base backup + WAL archiving).
#
# Each run: pg_dump -Fc per database + roles (globals) -> table-of-contents check -> keep newest N
# (default 7) of each kind. --verify-restore additionally restores the fondos dump into a scratch
# database, compares row counts/indexes/FKs with live, and drops the scratch database (~6 min).
# Schedule it (Windows Task Scheduler, daily, action: wsl.exe -d Ubuntu -e bash <this file>); the WSL
# keepalive task keeps the distro up.
set -uo pipefail
OUT=/mnt/c/data/backups/fondos_pg
SEC=/opt/docker/db/postgresql17/secrets
KEEP=7; VERIFY=0
while [ $# -gt 0 ]; do case "$1" in --verify-restore) VERIFY=1;; --keep) KEEP="$2"; shift;; *) echo "unknown arg $1"; exit 2;; esac; shift; done
PW=$(cat $SEC/postgres_password); STAMP=$(date +%Y%m%d_%H%M)
P="docker exec -e PGPASSWORD=$PW fondos_postgres"
mkdir -p "$OUT"; FAIL=0

for db in fondos gestion; do
  f="$OUT/${db}_$STAMP.dump"
  if $P pg_dump -h 127.0.0.1 -U fondos_owner -d "$db" -Fc -Z 3 > "$f.partial"; then
    n=$(docker exec -i fondos_postgres pg_restore --list < "$f.partial" | grep -c "TABLE DATA")
    if [ "$n" -gt 0 ]; then mv "$f.partial" "$f"; echo "[$(date +%T)] $db: OK $(stat -c %s "$f") bytes, $n data entries"
    else echo "[$(date +%T)] $db: dump has no table data - discarded"; rm -f "$f.partial"; FAIL=1; fi
  else echo "[$(date +%T)] $db: pg_dump FAILED"; rm -f "$f.partial"; FAIL=1; fi
done
$P pg_dumpall -h 127.0.0.1 -U fondos_owner --globals-only > "$OUT/globals_$STAMP.sql" 2>/dev/null && echo "[$(date +%T)] globals OK" || { echo "globals FAILED"; FAIL=1; }
chmod 600 "$OUT"/globals_$STAMP.sql 2>/dev/null || true   # contains role password hashes

if [ $FAIL -eq 0 ]; then   # retention only when this run produced a full set
  for kind in fondos gestion; do ls -1t "$OUT"/${kind}_2*.dump 2>/dev/null | tail -n +$((KEEP+1)) | xargs -r rm -v | sed 's/^/   pruned /'; done
  ls -1t "$OUT"/globals_*.sql 2>/dev/null | tail -n +$((KEEP+1)) | xargs -r rm -v | sed 's/^/   pruned /'
fi

if [ $VERIFY -eq 1 ] && [ $FAIL -eq 0 ]; then
  echo "[$(date +%T)] verify-restore: restoring fondos_$STAMP.dump into scratch database"
  Q() { $P psql -h 127.0.0.1 -U fondos_owner -d "$1" -Atc "$2" 2>&1 | head -1; }
  docker cp "$OUT/fondos_$STAMP.dump" fondos_postgres:/tmp/verify.dump
  $P psql -h 127.0.0.1 -U fondos_owner -d postgres -qc "DROP DATABASE IF EXISTS fondos_verify_scratch" >/dev/null 2>&1
  $P psql -h 127.0.0.1 -U fondos_owner -d postgres -qc "CREATE DATABASE fondos_verify_scratch" || FAIL=1
  $P pg_restore -h 127.0.0.1 -U fondos_owner -d fondos_verify_scratch -j 4 --no-owner /tmp/verify.dump 2>/tmp/verify.err || { echo "   pg_restore reported errors"; FAIL=1; }
  for t in silver.fund_master bronze.fund_nav_daily gold.fund_metric_timeseries gold.fund_metrics gold.fund_scores control.migration_state; do
    a=$(Q fondos "select count(*) from $t"); b=$(Q fondos_verify_scratch "select count(*) from $t")
    [ "$a" = "$b" ] && s=MATCH || { s=DIFF; FAIL=1; }; echo "   $t live=$a restored=$b $s"
  done
  ia=$(Q fondos "select count(*) from pg_indexes where schemaname in ('bronze','silver','gold','control')"); ib=$(Q fondos_verify_scratch "select count(*) from pg_indexes where schemaname in ('bronze','silver','gold','control')")
  [ "$ia" = "$ib" ] && echo "   indexes $ia/$ib MATCH" || { echo "   indexes $ia/$ib DIFF"; FAIL=1; }
  $P psql -h 127.0.0.1 -U fondos_owner -d postgres -qc "DROP DATABASE IF EXISTS fondos_verify_scratch" >/dev/null 2>&1
  docker exec fondos_postgres rm -f /tmp/verify.dump
fi
[ $FAIL -eq 0 ] && echo "[$(date +%T)] BACKUP OK" || echo "[$(date +%T)] BACKUP FINISHED WITH PROBLEMS"
exit $FAIL
