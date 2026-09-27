#!/bin/bash
# Backup of Metabase's own app metadata (dashboards, cards, connections, users) — its embedded H2
# file store, bind-mounted at /opt/docker/bi/metabase/data on the WSL2 disk. Separate from
# backup_live_pg.sh (which covers only the fondos/gestion Postgres databases): this is a different
# store, on a different container, that no existing script protects (FND-0074).
#   bash /mnt/c/desarrollo/fondos/scripts/ops/backup_metabase.sh [--keep N]
#
# Live file copy while metabase-app keeps the H2 store open: H2 is crash-consistent (it replays its
# own transaction log on next open), so a live tar is safe for this low-write, infrequently-changing
# app-config store — the same risk profile as copying any embedded file DB while its process runs.
# Does not touch the container (no stop/restart): just tars the bind-mounted host directory.
set -uo pipefail
OUT=/mnt/c/data/backups/metabase
SRC=/opt/docker/bi/metabase/data
KEEP=7
while [ $# -gt 0 ]; do case "$1" in --keep) KEEP="$2"; shift;; *) echo "unknown arg $1"; exit 2;; esac; shift; done
mkdir -p "$OUT"
STAMP=$(date +%Y%m%d_%H%M)
f="$OUT/metabase_$STAMP.tar.gz"

if tar czf "$f.partial" -C "$(dirname "$SRC")" "$(basename "$SRC")" 2>/tmp/backup_metabase.err; then
  n=$(stat -c %s "$f.partial")
  if [ "$n" -gt 1024 ]; then
    mv "$f.partial" "$f"; echo "[$(date +%T)] metabase: OK $n bytes -> $f"
  else
    echo "[$(date +%T)] metabase: tar suspiciously small ($n bytes) - discarded"; rm -f "$f.partial"; exit 1
  fi
else
  echo "[$(date +%T)] metabase: tar FAILED"; cat /tmp/backup_metabase.err; rm -f "$f.partial"; exit 1
fi

ls -1t "$OUT"/metabase_*.tar.gz 2>/dev/null | tail -n +$((KEEP+1)) | xargs -r rm -v | sed 's/^/   pruned /'
