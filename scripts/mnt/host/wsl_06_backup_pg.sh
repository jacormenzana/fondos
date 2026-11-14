#!/bin/bash
# wsl_06_backup_pg.sh — launch / catalogue the POSTGRES backups (fondos + gestion) from WSL. Run as root.
# Scope: PostgreSQL only (live container fondos_postgres). Not Metabase, config/secrets or the WSL image.
#
#   bash wsl_06_backup_pg.sh <type> [options]
#   bash wsl_06_backup_pg.sh list  [--dest DIR]            show the catalogue (MANIFEST.csv) + what is on disk
#
# Types (each is recorded in the manifest with its type):
#   logical   pg_dump -Fc of fondos + gestion + roles (globals)   -> <dest>/{fondos,gestion}_<stamp>.dump, globals_<stamp>.sql
#   physical  base backup + verify + prune old WAL (PITR)          -> <pitr>/base/<stamp>(.complete); location fixed by the
#             WAL-archive mount (archive_command writes there), so --dest does NOT apply to it
#   offbox    copy the EXISTING local dumps + PITR to the external disk D: (never takes a new dump; use `all` or run `logical`
#             first)                                              -> $OFFBOX_DEST (default root /mnt/d/desarrollo/fondos/db/backups/pg_fondos: dumps in <root>/dump, PITR in <root>/pitr)
#   all       logical, physical, offbox in that order
#
# Options:
#   --dest DIR        root for the logical backup files (default /mnt/backups/pg_fondos/dump); also where MANIFEST.csv lives
#   --stamp YYYYMMDD_HHMM   date stamp used in file names (default: now). Refuses to overwrite an existing stamp.
#   --label TEXT      free text stored in the manifest (e.g. pre-vhdx-compact, pre-migration)
#   --keep N          retention of logical sets (default 7; applied only when the whole set succeeded)
#   --offbox-keep N   retention on D: (default 7; independent from --keep, which only prunes the local set)
#   --offbox-catchup  ONE-OFF: allow the D: mirror to delete more than 60% of what D: holds (needed once to clear
#                     WAL/bases that D: accumulated while the old guard refused). Safe: the continuity guard still applies
#                     (source archive must contain the start segment of its oldest kept base). Preview first with:
#                     bash ../../backup_offbox.sh --no-dump --mirror-dry-run --mirror-allow-large
#   --verify-restore  logical only: restore the dump into a scratch DB and compare (~6 min)
#   --no-checksum     skip SHA-256 of the artifacts (a 5+ GB dump takes ~20-40 s)
# Adds over the underlying scripts: single-run lock, pre-flight (container up, free space >= 2x last dump),
# stamp/destination parametrisation, post-check and a manifest row per artifact:
#   stamp,type,label,artifact,bytes,sha256,status,seconds,pg_version
# offbox notes:
#   * It reads the local dumps from --dest and sends them to D: with SHA-256 verification; D: retention = --offbox-keep.
#   * D: mirroring deletes from D: only what the source pruned. Guards: the source archive must contain the start
#     segment of its oldest kept base (continuity), every source base must be complete on D:, and deletions must stay
#     under 60% of D: (use --offbox-catchup once to clear a large backlog). A refused guard is NOT a failed copy: the run
#     ends with a WARNING and manifest status COPIED_PRUNE_SKIPPED, and D: keeps its extra files. Real failures (SHA-256 mismatch, D: not mounted, rsync error) are CRITICAL.
# Manifest status values: OK | PROBLEMS | MISSING | FAILED | UNKNOWN | COPIED_PRUNE_SKIPPED
# Exit: 0 OK, 1 warnings (e.g. COPIED_PRUNE_SKIPPED, stale local dump), 2 critical (backup/copy failed or artifact missing),
#       3 pre-flight / usage error (lock held, container down, no space, bad argument).
set -uo pipefail
. "$(dirname "$0")/../lib/common.sh"; maint_args "$@"
OPS=$(cd "$(dirname "$0")/../.." && pwd)
TYPE=""; DEST=/mnt/backups/pg_fondos/dump; STAMP=$(date +%Y%m%d_%H%M); LABEL=""; KEEP=7; VERIFY=""; SUM=1; OFFKEEP=7; CATCHUP=""
set -- "${ARGS[@]:-}"
while [ $# -gt 0 ]; do case "$1" in
  logical|physical|offbox|all|list) TYPE="$1";;
  --dest) DEST="$2"; shift;; --stamp) STAMP="$2"; shift;; --label) LABEL="$2"; shift;; --keep) KEEP="$2"; shift;; --offbox-keep) OFFKEEP="$2"; shift;; --offbox-catchup) CATCHUP="--mirror-allow-large";;
  --verify-restore) VERIFY="--verify-restore";; --no-checksum) SUM=0;; "") ;;
  *) echo "unknown arg: $1 (see --help)"; exit 3;; esac; shift; done
[ -n "$TYPE" ] || { echo "usage: $0 logical|physical|offbox|all|list [options]  (--help for details)"; exit 3; }
[[ "$STAMP" =~ ^[0-9]{8}_[0-9]{4}$ ]] || { echo "--stamp must be YYYYMMDD_HHMM"; exit 3; }
[[ "$KEEP" =~ ^[0-9]+$ ]] && [ "$KEEP" -ge 1 ] || { echo "--keep must be an integer >= 1"; exit 3; }
MAN="$DEST/MANIFEST.csv"

if [ "$TYPE" = list ]; then
  [ -f "$MAN" ] && { echo "--- $MAN (last 25)"; { head -1 "$MAN"; tail -n +2 "$MAN" | tail -25; } | column -s, -t | cut -c1-200; } || echo "no manifest at $MAN yet"
  echo "--- on disk in $DEST"; ls -lht --time-style='+%F %H:%M' "$DEST" 2>/dev/null | head -20
  exit 0
fi

maint_start wsl_06_backup_pg
mkdir -p "$DEST" || { crit "cannot create $DEST"; exit 3; }
exec 9>/run/lock/fondos_backup.lock 2>/dev/null || exec 9>/tmp/fondos_backup.lock
flock -n 9 || { crit "another backup is already running (lock held)"; exit 3; }
docker exec "$PG_CONTAINER" pg_isready -h 127.0.0.1 -q || { crit "$PG_CONTAINER is not accepting connections"; exit 3; }
PGV=$(docker exec "$PG_CONTAINER" postgres --version 2>/dev/null | awk '{print $3}')
[ -f "$MAN" ] || echo "stamp,type,label,artifact,bytes,sha256,status,seconds,pg_version" > "$MAN"
info "type=$TYPE stamp=$STAMP dest=$DEST label='${LABEL:-}' keep=$KEEP pg=$PGV"

record() {  # record <type> <artifact path> <status> <seconds>
  local f=$2 b=0 h=-; if [ -f "$f" ]; then b=$(stat -c %s "$f"); [ $SUM -eq 1 ] && h=$(sha256sum "$f" | cut -d' ' -f1)
  elif [ -d "$f" ]; then b=$(du -sb "$f" 2>/dev/null | cut -f1); fi
  echo "$STAMP,$1,${LABEL//,/ },$f,$b,$h,$3,$4,$PGV" >> "$MAN"
}

if [ "$TYPE" = logical ] || [ "$TYPE" = all ]; then
  if ls "$DEST"/fondos_${STAMP}.dump >/dev/null 2>&1; then crit "stamp $STAMP already exists in $DEST (use another --stamp)"; exit 3; fi
  last=$(ls -1t "$DEST"/fondos_2*.dump 2>/dev/null | head -1); need=$(( $( [ -n "$last" ] && stat -c %s "$last" || echo 0 ) * 2 / 1048576 + 1024 ))
  free=$(df -Pm "$DEST" | awk 'NR==2{print $4}'); [ "$free" -lt "$need" ] && { crit "only ${free} MB free in $DEST (need ~${need} MB)"; exit 3; }
  act "logical backup -> $DEST ($STAMP) $VERIFY"; t0=$(date +%s)
  BACKUP_OUT="$DEST" BACKUP_STAMP="$STAMP" BACKUP_KEEP="$KEEP" bash "$OPS/backup_live_pg.sh" $VERIFY; rc=$?; secs=$(( $(date +%s)-t0 ))
  [ $rc -ne 0 ] && crit "backup_live_pg.sh reported problems (rc=$rc)"
  for f in "$DEST/fondos_$STAMP.dump" "$DEST/gestion_$STAMP.dump" "$DEST/globals_$STAMP.sql"; do
    if [ -f "$f" ]; then record logical "$f" "$([ $rc -eq 0 ] && echo OK || echo PROBLEMS)" "$secs"; ok "$(basename "$f") $(( $(stat -c %s "$f")/1048576 )) MB"
    else record logical "$f" MISSING "$secs"; crit "expected artifact missing: $f"; fi
  done
fi

if [ "$TYPE" = physical ] || [ "$TYPE" = all ]; then
  act "physical base backup + prune"; t0=$(date +%s); PITR=$(readlink -f /opt/docker/db/postgresql17/pitr 2>/dev/null)
  if bash "$OPS/setup_wal_archive.sh" basebackup; then
    secs=$(( $(date +%s)-t0 )); nb=$(ls -1t "$PITR"/base/*.complete 2>/dev/null | head -1); nb=${nb%.complete}
    [ -n "$nb" ] && { record physical "$nb" OK "$secs"; ok "base backup ${nb##*/}"; } || { record physical "$PITR/base" UNKNOWN "$secs"; warn "base backup ran but no .complete marker found"; }
    bash "$OPS/setup_wal_archive.sh" prune --yes || warn "WAL prune reported problems"
  else record physical "$PITR/base" FAILED $(( $(date +%s)-t0 )); crit "base backup failed — prune skipped"; fi
fi

if [ "$TYPE" = offbox ] || [ "$TYPE" = all ]; then
  # Copy-only: backup_offbox.sh would otherwise take ITS OWN dump (a second, redundant one) before copying.
  act "off-box mirror (copies the local dumps + PITR; no new dump)"; t0=$(date +%s); OD=${OFFBOX_DEST:-/mnt/d/desarrollo/fondos/db/backups/pg_fondos}
  newest=$(ls -1t "$DEST"/fondos_2*.dump 2>/dev/null | head -1)
  if [ -z "$newest" ]; then crit "no local dump in $DEST to copy off-box (run: logical)"
  else
    [ $(( ($(date +%s)-$(stat -c %Y "$newest"))/3600 )) -gt 36 ] && warn "newest local dump is >36h old (${newest##*/}); run 'logical' first if you want a fresh one on D:"
    OFFLOG=$(mktemp); OFFBOX_LOCAL="$DEST" bash "$OPS/backup_offbox.sh" --no-dump --keep "$OFFKEEP" --mirror-prune $CATCHUP 2>&1 | tee "$OFFLOG"; rc=${PIPESTATUS[0]}
    secs=$(( $(date +%s)-t0 ))
    if [ $rc -eq 0 ]; then record offbox "$OD" OK "$secs"; ok "off-box copy + mirror done"
    elif grep -q "mirror.*REFUSED" "$OFFLOG" && ! grep -qE "MISMATCH|FAILED|not mounted|SKIPPED" "$OFFLOG"; then
      # The copy and its SHA-256 checks passed; only the deletion of old WAL on D: was refused by backup_offbox.sh's
      # safety guards (e.g. "source WAL archive has only N files": < 100 segments right after two base backups
      # close together + prune). Nothing was lost; D: just keeps extra WAL until the source accumulates normally.
      record offbox "$OD" COPIED_PRUNE_SKIPPED "$secs"; warn "off-box copy OK; D: pruning skipped by a safety guard ($(grep -m1 'mirror.*REFUSED' "$OFFLOG" | sed 's/^\[[^]]*\] //'))"
    else record offbox "$OD" FAILED "$secs"; crit "off-box copy failed (rc=$rc): see the lines above (is D: attached?)"; fi
    rm -f "$OFFLOG"
  fi
fi
maint_end
