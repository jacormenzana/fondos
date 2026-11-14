#!/bin/bash
# wsl_run_all.sh — runs the WSL preventive-maintenance family by cadence (inside WSL, as root for --apply).
#   bash wsl_run_all.sh daily|weekly [--apply]
#   daily  : 01 health                                  (read-only)
#   weekly : daily + 02 docker cleanup + 03 OS updates (docker stack held back) + 04 logs/tmp
# The Windows-side check/compaction is wsl_05_host_vhdx.ps1 (separate: it needs PowerShell).
# Exit code = worst child result (0 OK, 1 warn, 2 critical, 3 script failure).
set -uo pipefail
D=$(cd "$(dirname "$0")" && pwd); C=${1:-daily}; shift || true
case "$C" in
  daily)  L="01_health_check";;
  weekly) L="01_health_check 02_docker_cleanup 03_os_updates 04_logs_tmp_cleanup";;
  *) echo "usage: $0 daily|weekly [--apply]"; exit 2;;
esac
W=0
for s in $L; do bash "$D/wsl_$s.sh" "$@"; r=$?; [ $r -gt $W ] && W=$r; done
echo "wsl_run_all $C -> worst exit $W"; exit $W
