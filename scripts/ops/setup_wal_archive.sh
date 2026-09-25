#!/bin/bash
# FND-0071 — real WAL archiving + physical base backup + point-in-time-recovery (PITR) drill for the
# LIVE Postgres 17 (container fondos_postgres, port 5436).
#
# Run inside WSL:   bash /mnt/c/desarrollo/fondos/scripts/ops/setup_wal_archive.sh [mode] [flags]
#
#   setup        (default) preflight -> prepare archive dir -> RECREATE the container with the archive mount
#                + archive_command -> prove archiving works -> first base backup -> restore drill.
#                Downtime ~1-2 min (recreate). Pause the launchers / close DBeaver+Metabase first, or --force.
#   basebackup   one physical base backup (pg_basebackup + pg_verifybackup). Schedule weekly.
#   drill        restore the newest base + archived WAL into a SCRATCH container up to a marker LSN and
#                compare a probe query with live. Live is only read (plus one pg_switch_wal()).
#   prune        keep the newest KEEP_BASES (default 2) base backups and delete WAL older than the oldest
#                kept one. Dry run unless --yes.
#   status       archiver state, archive/base sizes, free disk.
#
# Why archive_command was 'true' until now: it "succeeds" while archiving nothing, so there was no PITR.
# DANGER this script guards against: once archive_command is real, a FAILING archive (dir missing / not
# writable / disk full) makes Postgres keep every WAL segment in pg_wal until the disk fills. Hence the
# writability test before the recreate, the compose bind mount with create_host_path:false, and the
# archiver check right after it. If setup ever prints ARCHIVING NOT WORKING, revert the override first.
#
# What this is NOT: an off-box copy. $PITR lives on the same disk as the data (see FND-0070) — copy it to a
# NAS/cloud/USB for real disaster recovery.
#
# Every path/name is overridable by env var so the same logic can be rehearsed against a scratch container:
#   PG_BASE PITR_ROOT REPO_DOCKER PG_CONTAINER PG_USER PG_DB PG_IMAGE PG_PASSWORD_FILE DRILL_PORT KEEP_BASES PROBE_SQL
set -euo pipefail

MODE="setup"; case "${1:-}" in setup|basebackup|drill|prune|status) MODE="$1"; shift;; esac
FORCE=0; YES=0; KEEP_DRILL=${KEEP_DRILL:-0}
for a in "$@"; do case "$a" in --force) FORCE=1;; --yes) YES=1;; --keep-drill) KEEP_DRILL=1;; *) echo "unknown flag: $a"; exit 2;; esac; done

BASE=${PG_BASE:-/opt/docker/db/postgresql17}
PITR=${PITR_ROOT:-$BASE/pitr}
REPO=${REPO_DOCKER:-/mnt/c/desarrollo/fondos/docker}
CTR=${PG_CONTAINER:-fondos_postgres}
PGU=${PG_USER:-fondos_owner}
PGD=${PG_DB:-fondos}
IMG=${PG_IMAGE:-postgres:17}
PWF=${PG_PASSWORD_FILE:-$BASE/secrets/postgres_password}
DRILL_PORT=${DRILL_PORT:-5459}
KEEP_BASES=${KEEP_BASES:-2}
PROBE_SQL=${PROBE_SQL:-select count(*) from silver.fund_master}
DRILL_CTR=fondos_pitr_drill
CF="-p docker -f $REPO/docker-compose.yml -f $REPO/docker-compose.wsl.override.yml"

say()  { echo "[$(date +%T)] $*"; }
die()  { echo "[$(date +%T)] ABORT: $*" >&2; exit 1; }
ENVPW=(); [ -r "$PWF" ] && ENVPW=(-e "PGPASSWORD=$(cat "$PWF")")
psqlc() { docker exec "${ENVPW[@]}" "$CTR" psql -h 127.0.0.1 -U "$PGU" -d "$PGD" -Atqc "$1"; }
# root helper: same image, ONE bind mount, no network — used only for chown/rm/cp on $PITR
rootrun() { docker run --rm --network none -v "$PITR:/pitr" --entrypoint sh "$IMG" -c "$1"; }
# $PITR is owned by uid 999 with mode 700: inspect it THROUGH the container, not from the host.
dx() { docker exec "$CTR" "$@"; }
free_bytes() { df -B1 --output=avail "$1" | tail -1 | tr -d ' '; }
hr() { numfmt --to=iec "$1" 2>/dev/null || echo "$1"; }
archiver() { psqlc "select archived_count, failed_count, coalesce(last_archived_wal,'-'), coalesce(last_failed_wal,'-') from pg_stat_archiver" | tr '|' ' '; }
# completeness marker lives NEXT TO the base dir (the dir itself is 700): /pitr/base/<ts>.complete
list_bases() { dx sh -c 'ls -1 /pitr/base/*.complete 2>/dev/null | sort -r' | sed -E 's#.*/([0-9_]+)\.complete#\1#'; }
latest_base() { list_bases | head -1; }

preflight() {
  say "[preflight] docker + container"
  command -v docker >/dev/null || die "docker not found (run this inside WSL Ubuntu)"
  [ "$(docker inspect -f '{{.State.Health.Status}}' "$CTR" 2>/dev/null)" = "healthy" ] || die "container $CTR is not running/healthy"
  say "[preflight] free disk vs database size"
  local dbsz free need
  dbsz=$(psqlc "select sum(pg_database_size(datname)) from pg_database")
  mkdir -p "$PITR"; free=$(free_bytes "$PITR"); need=$(( dbsz * 3 + 20*1024*1024*1024 ))
  say "   databases $(hr "$dbsz") | free $(hr "$free") | need >= $(hr "$need") (base + drill copy + WAL headroom)"
  [ "$free" -ge "$need" ] || die "not enough free disk on $PITR"
  say "[preflight] postgres settings"
  [ "$(psqlc 'show wal_level')" != "minimal" ] || die "wal_level=minimal cannot archive"
  [ "$(psqlc 'show archive_mode')" = "on" ] || die "archive_mode is not on (changing it needs a restart cycle - fix postgresql.conf first)"
  say "   archive_command now: $(psqlc 'show archive_command')"
}

prepare_dir() {
  say "[prepare] $PITR/{wal,base} owned by the postgres uid (999), mode 700"
  rootrun 'mkdir -p /pitr/wal /pitr/base && chown -R 999:999 /pitr && chmod 700 /pitr /pitr/wal /pitr/base'
  docker run --rm --network none -u 999 -v "$PITR:/pitr" --entrypoint sh "$IMG" -c 'touch /pitr/wal/.w && rm /pitr/wal/.w' \
    || die "uid 999 cannot write $PITR/wal - archiving would fail and pg_wal would fill the disk"
  say "   writable by uid 999: OK"
}

recreate() {
  say "[recreate] validating compose config BEFORE touching the running container"
  docker compose $CF config -q || die "compose config invalid"
  local cfg; cfg=$(docker compose $CF config)
  echo "$cfg" | grep -q "target: /pitr"       || die "override has no /pitr mount (git pull? docker/docker-compose.wsl.override.yml)"
  echo "$cfg" | grep -q "archive_command="    || die "override has no archive_command"
  if docker inspect -f '{{range .Mounts}}{{.Destination}} {{end}}' "$CTR" | grep -qw /pitr; then
    say "   container already has the /pitr mount - skipping recreate"; return
  fi
  local n; n=$(psqlc "select count(*) from pg_stat_activity where backend_type='client backend' and pid<>pg_backend_pid()")
  if [ "$n" != "0" ] && [ "$FORCE" != 1 ]; then
    psqlc "select coalesce(application_name,''), usename, count(*) from pg_stat_activity where backend_type='client backend' and pid<>pg_backend_pid() group by 1,2" | sed 's/^/   client: /'
    die "$n client connection(s) open (pipelines, DBeaver, Metabase on :5436?). Close them or rerun with --force"
  fi
  local ts; ts=$(date +%Y%m%d_%H%M%S); mkdir -p "$BASE/backups/conf_$ts"
  cp -p "$REPO"/docker-compose*.yml "$REPO"/postgresql.conf "$BASE/backups/conf_$ts/"
  docker inspect "$CTR" > "$BASE/backups/conf_$ts/container_inspect.json"
  say "   previous config saved in $BASE/backups/conf_$ts (rollback: git checkout the override + $BASE/up.sh)"
  say "[recreate] $(date +%T) downtime starts"
  docker compose $CF up -d postgres
  local i; for i in $(seq 1 60); do
    [ "$(docker inspect -f '{{.State.Health.Status}}' "$CTR" 2>/dev/null)" = "healthy" ] && break; sleep 4
  done
  [ "$(docker inspect -f '{{.State.Health.Status}}' "$CTR" 2>/dev/null)" = "healthy" ] \
    || die "container not healthy after 4 min. Roll back: restore $BASE/backups/conf_$ts/docker-compose.wsl.override.yml into the repo and run $BASE/up.sh"
  say "[recreate] $(date +%T) back up and healthy"
}

verify_archiving() {
  say "[verify-archive] forcing a WAL switch and waiting for the archiver"
  [ "$(psqlc 'show archive_command')" != "true" ] || die "archive_command is still 'true'"
  local before after failed_before failed_after i
  read -r before failed_before _ _ <<<"$(archiver)"
  psqlc "select pg_switch_wal()" >/dev/null
  for i in $(seq 1 30); do
    read -r after failed_after _ _ <<<"$(archiver)"; [ "$after" -gt "$before" ] && break; sleep 2
  done
  if [ "$after" -le "$before" ] || [ "$failed_after" -gt "$failed_before" ]; then
    archiver | sed 's/^/   archiver: /'
    die "ARCHIVING NOT WORKING (archived $before->$after, failed $failed_before->$failed_after). pg_wal will grow until fixed: revert the archive_command override NOW"
  fi
  say "   archived_count $before -> $after, failed_count unchanged; newest: $(dx sh -c 'ls -1t /pitr/wal | head -1')"
}

basebackup() {
  local ts dest t0; ts=$(date +%Y%m%d_%H%M%S); dest="/pitr/base/$ts"; t0=$(date +%s)
  [ "$(psqlc 'show archive_command')" != "true" ] || die "archive_command is 'true': a base backup without WAL archiving is not a PITR base"
  say "[basebackup] -> $PITR/base/$ts (plain format, WAL streamed, fast checkpoint)"
  docker exec "${ENVPW[@]}" "$CTR" pg_basebackup -h 127.0.0.1 -U "$PGU" -D "$dest" -Fp -X stream -c fast -l "fondos_$ts" \
    || { dx mv "/pitr/base/$ts" "/pitr/base/$ts.FAILED" 2>/dev/null || true; die "pg_basebackup failed (partial kept as $ts.FAILED)"; }
  docker exec "$CTR" pg_verifybackup "$dest" >/dev/null 2>&1 \
    || { dx mv "/pitr/base/$ts" "/pitr/base/$ts.FAILED"; die "pg_verifybackup FAILED for $ts (kept as $ts.FAILED)"; }
  dx touch "/pitr/base/$ts.complete"
  say "[basebackup] OK $(dx du -sh "$dest" | cut -f1) in $(( $(date +%s) - t0 ))s, verified"
}

cleanup_drill() {
  docker rm -f "$DRILL_CTR" >/dev/null 2>&1 || true
  [ "$KEEP_DRILL" = 1 ] || rootrun 'rm -rf /pitr/drill_data' 2>/dev/null || true
}

drill() {
  local base; base=$(latest_base); [ -n "$base" ] || die "no complete base backup in $PITR/base (run: basebackup)"
  say "[drill] base=$base -> scratch container on 127.0.0.1:$DRILL_PORT (live is only read)"
  trap cleanup_drill EXIT
  cleanup_drill
  # Marker = a position that exists in the WAL NOW (taken before the switch: pg_current_wal_lsn() after a
  # switch is the start of the next, not-yet-archived segment and recovery could never reach it). Then force
  # that segment out to the archive and wait for the file itself (archived_count also counts .backup files).
  local marker seg probe_live i
  probe_live=$(psqlc "$PROBE_SQL")
  marker=$(psqlc "select pg_current_wal_lsn()")
  seg=$(psqlc "select pg_walfile_name('$marker')")
  psqlc "select pg_switch_wal()" >/dev/null
  for i in $(seq 1 60); do dx test -f "/pitr/wal/$seg" && break; sleep 2; done
  dx test -f "/pitr/wal/$seg" || die "segment $seg containing the marker never reached the archive - cannot drill"
  say "   marker LSN=$marker  live probe='$probe_live'"
  rootrun "cp -a /pitr/base/$base /pitr/drill_data && rm -f /pitr/drill_data/postmaster.pid \
    && printf \"restore_command = 'cp /pitr/wal/%%f \\\"%%p\\\"'\nrecovery_target_lsn = '$marker'\nrecovery_target_action = 'promote'\nrecovery_target_inclusive = true\n\" >> /pitr/drill_data/postgresql.auto.conf \
    && touch /pitr/drill_data/recovery.signal && chown -R 999:999 /pitr/drill_data && chmod 700 /pitr/drill_data"
  docker run -d --name "$DRILL_CTR" -v "$PITR/drill_data:/var/lib/postgresql/data" -v "$PITR/wal:/pitr/wal:ro" \
    -p "127.0.0.1:$DRILL_PORT:5432" "$IMG" postgres -c archive_mode=off -c listen_addresses='*' >/dev/null
  say "   waiting for recovery to reach the marker and promote (max 15 min)"
  local ok=0
  for i in $(seq 1 180); do
    if [ "$(docker exec "$DRILL_CTR" psql -U "$PGU" -d "$PGD" -Atqc 'select pg_is_in_recovery()' 2>/dev/null || true)" = "f" ]; then ok=1; break; fi
    docker ps -q -f "name=$DRILL_CTR" | grep -q . || break; sleep 5
  done
  if [ "$ok" != 1 ]; then docker logs --tail 40 "$DRILL_CTR" 2>&1 | sed 's/^/   drill log: /'; die "DRILL FAILED: scratch server never finished recovery"; fi
  local logs restored probe_rest
  logs=$(docker logs "$DRILL_CTR" 2>&1)
  restored=$(echo "$logs" | grep -c "restored log file" || true)
  echo "$logs" | grep -qE "recovery stopping|reached recovery target|recovery target LSN" || say "   WARN: no explicit 'recovery stopping' line in the log"
  probe_rest=$(docker exec "$DRILL_CTR" psql -U "$PGU" -d "$PGD" -Atqc "$PROBE_SQL")
  say "   promoted; WAL segments restored from the archive: $restored; probe restored='$probe_rest' live='$probe_live'"
  [ "$restored" -ge 1 ] || die "DRILL FAILED: recovery used no archived WAL (archive not exercised)"
  [ "$probe_rest" = "$probe_live" ] || die "DRILL FAILED: probe differs (a pipeline may have written between marker and probe - rerun while idle)"
  cleanup_drill; trap - EXIT
  say "[drill] PASSED: base + archived WAL restored to $marker and matched live. Scratch removed."
}

prune() {
  local bases n oldest label file cnt
  mapfile -t bases < <(list_bases)
  n=${#bases[@]}; say "[prune] $n complete base backup(s); keeping newest $KEEP_BASES"
  [ "$n" -gt "$KEEP_BASES" ] || { say "   nothing to prune"; return; }
  oldest="${bases[$((KEEP_BASES-1))]}"
  file=$(dx sh -c "grep -m1 '^START WAL LOCATION' /pitr/base/$oldest/backup_label" | sed -E 's/.*\(file ([0-9A-F]+)\).*/\1/')
  [ -n "$file" ] || die "cannot read START WAL LOCATION from $oldest/backup_label"
  cnt=$(docker exec "$CTR" pg_archivecleanup -n /pitr/wal "$file" | wc -l)
  say "   would delete $((n-KEEP_BASES)) old base(s) and $cnt WAL file(s) older than $file"
  [ "$YES" = 1 ] || { say "   dry run - rerun with --yes to delete"; return; }
  for ((i=KEEP_BASES; i<n; i++)); do rootrun "rm -rf /pitr/base/${bases[$i]} /pitr/base/${bases[$i]}.complete"; done
  docker exec "$CTR" pg_archivecleanup /pitr/wal "$file"
  say "[prune] done"
}

status() {
  say "[status] archive_command: $(psqlc 'show archive_command')"
  say "   archiver (archived failed last_ok last_failed): $(archiver)"
  say "   WAL archive: $(dx sh -c 'ls -1 /pitr/wal | wc -l') files, $(dx du -sh /pitr/wal | cut -f1)"
  say "   base backups: $(list_bases | wc -l) complete ($(dx du -sh /pitr/base | cut -f1)); newest: $(latest_base)"
  say "   free on $PITR: $(hr "$(free_bytes "$PITR")")"
}

case "$MODE" in
  setup)      preflight; prepare_dir; recreate; verify_archiving; basebackup; drill
              say "SETUP COMPLETE. Next: schedule weekly '$0 basebackup' then '$0 prune --yes' (Task Scheduler -> wsl.exe -d Ubuntu -e bash ...), and copy $PITR off this machine (FND-0070)." ;;
  basebackup) basebackup ;;
  drill)      drill ;;
  prune)      prune ;;
  status)     status ;;
esac
