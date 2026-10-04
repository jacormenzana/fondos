#!/bin/bash
# pg_run_all.sh — runs the Postgres preventive-maintenance family by cadence.
#   bash pg_run_all.sh daily|weekly|monthly [--apply]
#   daily   : 01 health, 05 backup verify                       (read-only)
#   weekly  : daily + 02 vacuum/analyze + 04 capacity
#   monthly : weekly + 03 index maintenance (+ backup_live_pg.sh --verify-restore, ~6 min, only with --apply)
# Exit code = worst child result (0 OK, 1 warn, 2 critical, 3 script failure).
set -uo pipefail
D=$(cd "$(dirname "$0")" && pwd); C=${1:-daily}; shift || true
case "$C" in
  daily)   L="01_health_check 05_backup_verify";;
  weekly)  L="01_health_check 05_backup_verify 02_vacuum_analyze 04_capacity_report";;
  monthly) L="01_health_check 05_backup_verify 02_vacuum_analyze 04_capacity_report 03_index_maintenance";;
  *) echo "usage: $0 daily|weekly|monthly [--apply]"; exit 2;;
esac
W=0
for s in $L; do bash "$D/pg_$s.sh" "$@"; r=$?; [ $r -gt $W ] && W=$r; done
if [ "$C" = monthly ] && [[ " $* " == *" --apply "* ]]; then
  bash "$D/../../backup_live_pg.sh" --verify-restore || W=2
fi
echo "pg_run_all $C -> worst exit $W"; exit $W
