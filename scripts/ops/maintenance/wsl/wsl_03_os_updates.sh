#!/bin/bash
# wsl_03_os_updates.sh — OS patching without surprising the live database (run weekly; needs root for --apply).
#   bash wsl_03_os_updates.sh [--apply]
# Lists pending upgrades. --apply runs apt-get update + upgrade EXCLUDING docker*/containerd*/runc
# (upgrading those restarts the daemon and with it every container, including live Postgres — do that by
# hand in a window with `wsl_03_os_updates.sh --apply --include-docker`, after a backup). Afterwards:
# apt autoremove + clean. Reboot-required is only reported; a reboot is `wsl --shutdown` from Windows
# and must be scheduled by the owner (stops all containers).
set -uo pipefail
. "$(dirname "$0")/../lib/common.sh"; maint_args "$@"; maint_start wsl_03_os_updates
INCL=0; [[ " ${ARGS[*]:-} " == *" --include-docker "* ]] && INCL=1
HOLD_RE='^(docker|containerd|runc)'
[ $APPLY -eq 1 ] && [ "$(id -u)" -ne 0 ] && { crit "--apply needs root (use: wsl.exe -d Ubuntu -u root -- bash $0 --apply)"; maint_end; }

[ "$(id -u)" -eq 0 ] && apt-get update -qq || info "not root: using the cached package lists (may be stale)"
mapfile -t UP < <(apt-get -s upgrade 2>/dev/null | awk '/^Inst/{print $2}')
if [ ${#UP[@]} -eq 0 ]; then ok "system up to date"; else
  DOCK=(); REST=()
  for p in "${UP[@]}"; do if [[ "$p" =~ $HOLD_RE ]]; then DOCK+=("$p"); else REST+=("$p"); fi; done
  info "${#UP[@]} pending: ${#REST[@]} regular, ${#DOCK[@]} docker-stack (${DOCK[*]:-none})"
  [ ${#DOCK[@]} -gt 0 ] && [ $INCL -eq 0 ] && warn "docker-stack updates pending — held back (they would restart all containers)"
  if need_apply "upgrade ${#REST[@]} package(s)"; then
    if [ $INCL -eq 0 ] && [ ${#DOCK[@]} -gt 0 ]; then apt-mark hold "${DOCK[@]}" >/dev/null; fi
    DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::=--force-confold upgrade && ok "upgrade done" || crit "apt-get upgrade failed"
    [ $INCL -eq 0 ] && [ ${#DOCK[@]} -gt 0 ] && apt-mark unhold "${DOCK[@]}" >/dev/null
    apt-get -y autoremove >/dev/null && apt-get -y clean && ok "autoremove + clean done"
  fi
fi
[ -f /var/run/reboot-required ] && warn "reboot required — schedule a window (wsl --shutdown stops all containers)"
if docker ps >/dev/null 2>&1; then ok "docker still up: $(docker ps -q | wc -l) container(s) running"; fi
maint_end
