#!/bin/bash
# Daily off-box copy of the LIVE Postgres 17 backups to the external disk (Windows D:).
#   wsl.exe -d Ubuntu -u root -- bash /mnt/c/desarrollo/fondos/scripts/ops/backup_offbox.sh [--deep] [--keep N] [--no-dump]
#
# Steps: (1) make sure D: is mounted inside WSL (drives plugged in after the distro booted are NOT
# automounted) -> (2) fresh logical dump via backup_live_pg.sh -> (3) copy the newest dump set to D:
# and compare SHA-256 -> (4) incrementally copy the PITR base backups + WAL archive (rsync skips files
# already on D:) -> (5) prune old dump sets on D: (never the PITR set).
#
# --deep    also re-verify the whole PITR copy with rsync -c (reads every byte on both sides; ~15 min)
# --no-dump skip step 2 and copy whatever the newest local dump set is (testing / a dump just taken)
# --mirror-prune  after a CLEAN sync, delete from D:/pitr what the source no longer has (the source is pruned by
#                 setup_wal_archive.sh prune, FND-0179). Refuses unless every guard passes: a complete verified base
#                 exists on the source AND on D:, the source WAL archive is plausible, and the deletions stay under
#                 60% of what D: holds (rsync --max-delete). A damaged / empty / unmounted source can never wipe D:.
# --mirror-dry-run  same checks, lists what WOULD be deleted, deletes nothing (implies --mirror-prune)
# Exit code 0 = everything verified; non-zero = something failed (see the log). Missing D: -> exit 3.
# Run as root (needed to read the PITR directory, owned by uid 999, and to mount D:).
# Without --mirror-prune nothing is ever deleted from D:/pitr (rsync runs without --delete) and it only grows.
set -uo pipefail
# Paths are overridable so the guards can be rehearsed on a scratch tree (OFFBOX_MNT set => no mount attempt).
DRIVE=D; MNT=${OFFBOX_MNT:-/mnt/d}
DEST=${OFFBOX_DEST:-$MNT/desarrollo/fondos/db/backup}
LOCAL=${OFFBOX_LOCAL:-/mnt/c/data/backups/fondos_pg}
PITR=${OFFBOX_PITR:-$(readlink -f /opt/docker/db/postgresql17/pitr)}
LOG=${OFFBOX_LOG:-/mnt/c/data/backups/offbox_copy.log}
HERE=$(cd "$(dirname "$0")" && pwd)
KEEP=7; DEEP=0; DUMP=1; MIRROR=0; MIRROR_DRY=0
while [ $# -gt 0 ]; do case "$1" in --deep) DEEP=1;; --no-dump) DUMP=0;; --mirror-prune) MIRROR=1;; --mirror-dry-run) MIRROR=1; MIRROR_DRY=1;; --keep) KEEP="$2"; shift;; *) echo "unknown arg $1"; exit 2;; esac; shift; done
exec > >(tee -a "$LOG") 2>&1
ts() { date '+%F %T'; }
FAIL=0
echo "=== [$(ts)] offbox backup start (deep=$DEEP dump=$DUMP keep=$KEEP mirror=$MIRROR dry=$MIRROR_DRY)"

# (1) mount D: if needed
if [ -z "${OFFBOX_MNT:-}" ] && ! mountpoint -q "$MNT"; then
  mkdir -p "$MNT"
  mount -t drvfs "$DRIVE:" "$MNT" 2>/dev/null || { echo "[$(ts)] FATAL: $DRIVE: cannot be mounted - is the external disk plugged in?"; exit 3; }
  echo "[$(ts)] mounted $DRIVE: at $MNT"
fi
[ -d "$MNT/desarrollo" ] || { echo "[$(ts)] FATAL: $MNT/desarrollo missing - wrong disk mounted as $DRIVE:?"; exit 3; }
mkdir -p "$DEST/pitr" || { echo "[$(ts)] FATAL: cannot write to $DEST"; exit 3; }
[ -d "$PITR/base" ] && [ -d "$PITR/wal" ] || { echo "[$(ts)] FATAL: PITR dir $PITR has no base/ wal/"; exit 3; }
free=$(df -B1 --output=avail "$MNT" | tail -1)
[ "$free" -gt $(( 20*1024*1024*1024 )) ] || { echo "[$(ts)] FATAL: less than 20 GB free on $DRIVE:"; exit 3; }
[ "$free" -gt $(( 80*1024*1024*1024 )) ] || echo "[$(ts)] WARN: only $(( free / 1024 / 1024 / 1024 )) GB free on $DRIVE: (floor 80 GB) - prune the PITR set (FND-0179)"

# (2) fresh logical dump (writes to C:\data\backups\fondos_pg)
if [ $DUMP -eq 1 ]; then
  bash "$HERE/backup_live_pg.sh" || { echo "[$(ts)] logical backup reported problems"; FAIL=1; }
fi

# (3) newest dump set -> D:, SHA-256 compare
for pat in 'fondos_2*.dump' 'gestion_2*.dump' 'globals_*.sql'; do
  f=$(ls -1t $LOCAL/$pat 2>/dev/null | head -1)
  [ -n "$f" ] || { echo "[$(ts)] no local file for $pat"; FAIL=1; continue; }
  b=$(basename "$f")
  if cp -f "$f" "$DEST/$b.partial" && mv -f "$DEST/$b.partial" "$DEST/$b"; then
    a=$(sha256sum "$f" | cut -d' ' -f1); c=$(sha256sum "$DEST/$b" | cut -d' ' -f1)
    if [ "$a" = "$c" ]; then echo "[$(ts)] $b: copied, SHA-256 MATCH ($(stat -c %s "$f") bytes)"; else echo "[$(ts)] $b: SHA-256 MISMATCH"; FAIL=1; fi
  else echo "[$(ts)] $b: copy FAILED"; FAIL=1; fi
done

# (4) PITR base backups + WAL, incremental (no --delete)
for d in base wal; do
  if rsync -rt --no-perms --no-owner --no-group --stats "$PITR/$d/" "$DEST/pitr/$d/" | grep -E "Number of (regular )?files transferred|Total transferred file size"; then
    echo "[$(ts)] pitr/$d: synced"
  else echo "[$(ts)] pitr/$d: rsync FAILED"; FAIL=1; fi
  if [ $DEEP -eq 1 ]; then
    diff=$(rsync -rtc --no-perms --no-owner --no-group -n --itemize-changes "$PITR/$d/" "$DEST/pitr/$d/" | grep -c '^[<>c]')
    [ "$diff" = "0" ] && echo "[$(ts)] pitr/$d: deep check (rsync -c) 100% identical" || { echo "[$(ts)] pitr/$d: deep check found $diff differing files"; FAIL=1; }
  fi
done
# every base backup on D: must carry its .complete marker as on the source
for m in "$PITR"/base/*.complete; do [ -e "$m" ] && [ ! -e "$DEST/pitr/base/$(basename "$m")" ] && { echo "[$(ts)] missing marker $(basename "$m") on $DRIVE:"; FAIL=1; }; done

# (4b) mirror the source's pruning to D: (FND-0179). Every guard must pass; any failure skips the deletion, never the copy.
if [ $MIRROR -eq 1 ]; then
  if [ $FAIL -ne 0 ]; then echo "[$(ts)] mirror: SKIPPED - the sync above had problems"
  else
    ok=1
    nb_src=$(ls -1 "$PITR"/base/*.complete 2>/dev/null | wc -l)
    [ "$nb_src" -ge 1 ] || { echo "[$(ts)] mirror: REFUSED - no complete base backup on the source"; ok=0; }
    for m in "$PITR"/base/*.complete; do                      # every source base marker must exist on D: with its backup_label
      [ -e "$m" ] || continue; b=$(basename "$m" .complete)
      [ -e "$DEST/pitr/base/$b.complete" ] && [ -e "$DEST/pitr/base/$b/backup_label" ] || { echo "[$(ts)] mirror: REFUSED - base $b is not complete on $DRIVE:"; ok=0; }
    done
    nw_src=$(find "$PITR/wal" -type f | wc -l); nw_dst=$(find "$DEST/pitr/wal" -type f | wc -l)
    [ "$nw_src" -ge 100 ] || { echo "[$(ts)] mirror: REFUSED - source WAL archive has only $nw_src files"; ok=0; }
    if [ $ok -eq 1 ]; then
      # Count first, delete second: --max-delete alone only stops AFTER N deletions, it does not refuse. A wipe
      # (source far smaller than D:) is refused outright when the deletions exceed 60% of what D: holds (min 200).
      for d in wal base; do
        nd_dst=$(find "$DEST/pitr/$d" -type f | wc -l); cap=$(( nd_dst * 6 / 10 )); [ "$cap" -ge 200 ] || cap=200
        nd=$(rsync -rtn --no-perms --no-owner --no-group --delete --itemize-changes "$PITR/$d/" "$DEST/pitr/$d/" | grep -c '^\*deleting')
        if [ "$nd" -gt "$cap" ]; then
          echo "[$(ts)] mirror pitr/$d: REFUSED - $nd deletions exceed the cap $cap (D: holds $nd_dst files, source $d has $(find "$PITR/$d" -type f | wc -l)); nothing deleted"
          FAIL=1; break
        fi
        if [ $MIRROR_DRY -eq 1 ]; then
          echo "[$(ts)] mirror pitr/$d: $nd item(s) WOULD be deleted (cap $cap)"
        elif rsync -rt --no-perms --no-owner --no-group --delete --max-delete=$cap "$PITR/$d/" "$DEST/pitr/$d/"; then
          echo "[$(ts)] mirror pitr/$d: $nd item(s) deleted (cap $cap)"
        else
          echo "[$(ts)] mirror pitr/$d: rsync FAILED during the deletion pass"; FAIL=1; break
        fi
      done
    else FAIL=1; fi
  fi
fi

# (5) prune old dump sets on D: only when this run was clean
if [ $FAIL -eq 0 ]; then
  for k in 'fondos_2*.dump' 'gestion_2*.dump' 'globals_*.sql'; do
    ls -1t $DEST/$k 2>/dev/null | tail -n +$((KEEP+1)) | xargs -r rm -v | sed 's/^/   pruned /'
  done
fi
sync
[ $FAIL -eq 0 ] && echo "=== [$(ts)] OFFBOX BACKUP OK" || echo "=== [$(ts)] OFFBOX BACKUP FINISHED WITH PROBLEMS"
exit $FAIL
