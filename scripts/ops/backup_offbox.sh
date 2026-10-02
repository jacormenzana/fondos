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
# Exit code 0 = everything verified; non-zero = something failed (see the log). Missing D: -> exit 3.
# Run as root (needed to read the PITR directory, owned by uid 999, and to mount D:).
# Retention of the PITR copy is NOT automatic: nothing is deleted from D:/pitr (rsync runs without --delete),
# so it only grows - prune it together with setup_wal_archive.sh prune.
set -uo pipefail
DRIVE=D; MNT=/mnt/d
DEST=$MNT/desarrollo/fondos/db/backup
LOCAL=/mnt/c/data/backups/fondos_pg
PITR=$(readlink -f /opt/docker/db/postgresql17/pitr)
HERE=$(cd "$(dirname "$0")" && pwd)
KEEP=7; DEEP=0; DUMP=1
while [ $# -gt 0 ]; do case "$1" in --deep) DEEP=1;; --no-dump) DUMP=0;; --keep) KEEP="$2"; shift;; *) echo "unknown arg $1"; exit 2;; esac; shift; done
LOG=/mnt/c/data/backups/offbox_copy.log
exec > >(tee -a "$LOG") 2>&1
ts() { date '+%F %T'; }
FAIL=0
echo "=== [$(ts)] offbox backup start (deep=$DEEP dump=$DUMP keep=$KEEP)"

# (1) mount D: if needed
if ! mountpoint -q "$MNT"; then
  mkdir -p "$MNT"
  mount -t drvfs "$DRIVE:" "$MNT" 2>/dev/null || { echo "[$(ts)] FATAL: $DRIVE: cannot be mounted - is the external disk plugged in?"; exit 3; }
  echo "[$(ts)] mounted $DRIVE: at $MNT"
fi
[ -d "$MNT/desarrollo" ] || { echo "[$(ts)] FATAL: $MNT/desarrollo missing - wrong disk mounted as $DRIVE:?"; exit 3; }
mkdir -p "$DEST/pitr" || { echo "[$(ts)] FATAL: cannot write to $DEST"; exit 3; }
[ -d "$PITR/base" ] && [ -d "$PITR/wal" ] || { echo "[$(ts)] FATAL: PITR dir $PITR has no base/ wal/"; exit 3; }
free=$(df -B1 --output=avail "$MNT" | tail -1)
[ "$free" -gt $(( 20*1024*1024*1024 )) ] || { echo "[$(ts)] FATAL: less than 20 GB free on $DRIVE:"; exit 3; }

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

# (5) prune old dump sets on D: only when this run was clean
if [ $FAIL -eq 0 ]; then
  for k in 'fondos_2*.dump' 'gestion_2*.dump' 'globals_*.sql'; do
    ls -1t $DEST/$k 2>/dev/null | tail -n +$((KEEP+1)) | xargs -r rm -v | sed 's/^/   pruned /'
  done
fi
sync
[ $FAIL -eq 0 ] && echo "=== [$(ts)] OFFBOX BACKUP OK" || echo "=== [$(ts)] OFFBOX BACKUP FINISHED WITH PROBLEMS"
exit $FAIL
