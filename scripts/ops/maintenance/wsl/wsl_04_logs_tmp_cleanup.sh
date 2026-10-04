#!/bin/bash
# wsl_04_logs_tmp_cleanup.sh — trim logs, caches and /tmp (run weekly; root needed for --apply).
#   bash wsl_04_logs_tmp_cleanup.sh [--apply]
# /tmp is a ~3.9 GB tmpfs (counts against RAM). --apply: journald vacuum (JOURNAL_DAYS, default 14),
# delete rotated /var/log/*.gz|*.[0-9] older than LOG_DAYS (30), /tmp files untouched for TMP_DAYS (3),
# apt cache clean, then `fstrim` so freed blocks can be reclaimed from the VHDX (see wsl_05_host_vhdx.ps1).
# Files that Postgres/docker hold open are never matched (only old, closed, rotated or /tmp files).
set -uo pipefail
. "$(dirname "$0")/../lib/common.sh"; maint_args "$@"; maint_start wsl_04_logs_tmp_cleanup
JD=${JOURNAL_DAYS:-14}; LD=${LOG_DAYS:-30}; TD=${TMP_DAYS:-3}
[ $APPLY -eq 1 ] && [ "$(id -u)" -ne 0 ] && { crit "--apply needs root (wsl.exe -d Ubuntu -u root -- bash $0 --apply)"; maint_end; }

info "journal: $(journalctl --disk-usage 2>/dev/null | grep -o '[0-9.]*[KMG]' | head -1 || echo n/a)"
old_logs=$(find /var/log -type f \( -name '*.gz' -o -name '*.[0-9]' -o -name '*.old' \) -mtime +"$LD" 2>/dev/null | wc -l)
old_tmp=$(find /tmp -xdev -mindepth 1 -type f -atime +"$TD" -mtime +"$TD" 2>/dev/null | wc -l)
tmp_use=$(df -P /tmp | awk 'NR==2{gsub("%","",$5); print $5}')
level_ge "$tmp_use" 60 85 "/tmp tmpfs used" "%"
info "candidates: $old_logs rotated log file(s) > ${LD}d, $old_tmp /tmp file(s) idle > ${TD}d, apt cache $(du -sm /var/cache/apt 2>/dev/null | cut -f1) MB"
du -xsm /var/log /home /opt/docker 2>/dev/null | awk '{printf "[INFO] %s MB  %s\n",$1,$2}'

if need_apply "journal vacuum, rotated-log + /tmp cleanup, apt clean, fstrim"; then
  act "journalctl --vacuum-time=${JD}d"; journalctl --vacuum-time="${JD}d" 2>&1 | tail -1
  act "rotated logs"; find /var/log -type f \( -name '*.gz' -o -name '*.[0-9]' -o -name '*.old' \) -mtime +"$LD" -delete
  act "/tmp idle files"; find /tmp -xdev -mindepth 1 -type f -atime +"$TD" -mtime +"$TD" -delete; find /tmp -xdev -mindepth 1 -type d -empty -mtime +"$TD" -delete 2>/dev/null
  act "apt clean"; apt-get clean
  act "fstrim"; fstrim -av 2>&1 | sed 's/^/   /' || warn "fstrim failed"
fi
maint_end
