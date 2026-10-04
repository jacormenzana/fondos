#!/bin/bash
# wsl_01_health_check.sh — READ-ONLY health snapshot of the WSL2 Ubuntu host (run daily, inside WSL).
#   bash wsl_01_health_check.sh
# Checks: disk + inode headroom (/, /opt/docker, /home, /mnt/c), RAM/swap pressure, load, tmpfs /tmp,
# failed systemd units, docker daemon + container state/restarts, clock drift, pending reboot,
# pending apt updates. Env: WSL_DISK_WARN=80 WSL_DISK_CRIT=90 (percent used)
set -uo pipefail
. "$(dirname "$0")/../lib/common.sh"; maint_args "$@"; maint_start wsl_01_health_check
DW=${WSL_DISK_WARN:-80}; DC=${WSL_DISK_CRIT:-90}

for m in / /opt/docker /home /tmp /mnt/c; do
  [ -d "$m" ] || continue
  u=$(df -P "$m" | awk 'NR==2{gsub("%","",$5); print $5}'); level_ge "$u" "$DW" "$DC" "disk $m used" "%"
  i=$(df -Pi "$m" | awk 'NR==2{gsub("%","",$5); print ($5==""||$5=="-")?0:$5}'); [ "$i" -ge 85 ] && warn "inodes $m used ${i}%"
done

read -r mt ma <<<"$(awk '/MemTotal/{t=$2} /MemAvailable/{a=$2} END{print int(t/1024), int(a/1024)}' /proc/meminfo)"
level_ge $(( (mt-ma)*100/mt )) 85 95 "RAM used (${ma} MB available of ${mt})" "%"
read -r st su <<<"$(awk '/SwapTotal/{t=$2} /SwapFree/{f=$2} END{print int(t/1024), int((t-f)/1024)}' /proc/meminfo)"
if [ "$st" -gt 0 ]; then level_ge $((su*100/st)) 50 80 "swap used (${su}/${st} MB)" "%"; fi
oom=$(dmesg 2>/dev/null | grep -ci "out of memory\|oom-kill"); [ "${oom:-0}" -gt 0 ] && warn "$oom OOM-killer event(s) in dmesg since boot" || ok "no OOM events since boot"
cores=$(nproc); l1=$(awk '{print int($1*100)}' /proc/loadavg)
[ "$l1" -gt $((cores*200)) ] && warn "load $(cut -d' ' -f1-3 /proc/loadavg) on $cores cores" || ok "load $(cut -d' ' -f1-3 /proc/loadavg) on $cores cores"
info "uptime: $(uptime -p)"

if command -v systemctl >/dev/null && [ "$(systemctl is-system-running 2>/dev/null)" != "offline" ]; then
  f=$(systemctl --failed --no-legend 2>/dev/null | wc -l); [ "$f" -gt 0 ] && { warn "$f failed systemd unit(s):"; systemctl --failed --no-legend | sed 's/^/   /'; } || ok "no failed systemd units"
fi

if docker info >/dev/null 2>&1; then
  ok "docker daemon up"
  while read -r n s rc; do
    case "$s" in running*) [ "$rc" -gt 3 ] && warn "container $n restarted ${rc}x (crash loop / WSL idle shutdown?)" || ok "container $n running (restarts=$rc)";;
                 *) if [ "$n" = "$PG_CONTAINER" ]; then crit "container $n is $s"; else warn "container $n is $s (stale ad-hoc container? wsl_02_docker_cleanup.sh)"; fi;; esac
  done < <(docker ps -a --format '{{.Names}}' | while read -r n; do echo "$n $(docker inspect -f '{{.State.Status}} {{.RestartCount}}' "$n")"; done)
else crit "docker daemon not reachable"; fi

d=$(( $(date +%s) - $(date -d "$(curl -sI --max-time 5 https://www.google.com 2>/dev/null | awk -F': ' 'tolower($1)=="date"{print $2}')" +%s 2>/dev/null || date +%s) ))
[ "${d#-}" -gt 5 ] && warn "clock differs ${d}s from the web (WSL clock drift after host sleep: run 'sudo hwclock -s')" || ok "clock in sync (<5s)"

[ -f /var/run/reboot-required ] && warn "reboot required (kernel/libc update pending) — plan a wsl --shutdown window" || ok "no reboot pending"
if command -v apt-get >/dev/null; then
  n=$(apt-get -s upgrade 2>/dev/null | grep -c '^Inst'); sec=$(apt-get -s upgrade 2>/dev/null | grep '^Inst' | grep -ci security)
  [ "$sec" -gt 0 ] && warn "$sec security update(s) pending ($n total) — wsl_03_os_updates.sh" || ok "no security updates pending ($n total pending)"
fi
maint_end
