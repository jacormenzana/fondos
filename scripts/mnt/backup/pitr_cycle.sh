#!/bin/bash
# PITR refresh cycle for the LIVE Postgres 17 (FND-0071): status -> new base backup -> restore drill ->
# prune dry run -> prune -> status. A thin wrapper over setup_wal_archive.sh: every step stops the cycle on
# failure, so old base backups are only pruned after the NEW base has just passed the restore drill.
#
# Run inside WSL (from PowerShell: wsl -d Ubuntu):
#   bash /mnt/c/desarrollo/fondos/scripts/ops/pitr_cycle.sh         # shows the prune dry run, asks before deleting
#   bash /mnt/c/desarrollo/fondos/scripts/ops/pitr_cycle.sh --yes   # unattended: prunes without asking
# Without a terminal and without --yes it stops after the dry run (nothing deleted).
# KEEP_BASES = base backups kept by the prune (default 1 here; setup_wal_archive.sh alone defaults to 2).
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
WAL="$HERE/setup_wal_archive.sh"
YES=0
for a in "$@"; do case "$a" in --yes) YES=1;; -h|--help) sed -n '2,10p' "$0"; exit 0;; *) echo "unknown flag: $a" >&2; exit 2;; esac; done
export KEEP_BASES=${KEEP_BASES:-1}

step() { echo; echo "=== [$(date +%T)] $*"; }

step "1/6 current state: WAL size, base backups, free disk"
bash "$WAL" status
step "2/6 new base backup (pg_basebackup, then pg_verifybackup)"
bash "$WAL" basebackup
step "3/6 restore drill: the NEW base + archived WAL must restore before anything old is deleted"
bash "$WAL" drill
step "4/6 prune, dry run (KEEP_BASES=$KEEP_BASES)"
bash "$WAL" prune
if [ "$YES" != 1 ]; then
  [ -t 0 ] || { echo "no terminal and no --yes: stopping after the dry run (nothing deleted)"; exit 0; }
  read -r -p "Delete the base backups and WAL listed above? [y/N] " ans
  case "$ans" in y|Y|yes) ;; *) echo "prune skipped (nothing deleted)"; exit 0;; esac
fi
step "5/6 prune for real"
bash "$WAL" prune --yes
step "6/6 confirm space was freed"
bash "$WAL" status
