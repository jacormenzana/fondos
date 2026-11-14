#!/bin/bash
# pg_05_backup_verify.sh — are the backups actually there and sane? (run daily, READ-ONLY, fast).
#   bash pg_05_backup_verify.sh
# Checks the logical dumps (backup_live_pg.sh), the physical base backups + WAL archive
# (setup_wal_archive.sh) and the off-box mirror (backup_offbox.sh): newest age, size vs previous,
# TOC readable. The slow restore test is `backup_live_pg.sh --verify-restore` (monthly, see pg_run_all.sh).
# Env: BK_LOCAL BK_PITR BK_OFFBOX BK_MAX_AGE_H=36 BK_BASE_MAX_AGE_H=96
set -uo pipefail
. "$(dirname "$0")/../lib/common.sh"; maint_args "$@"; maint_start pg_05_backup_verify
LOCAL=${BK_LOCAL:-/mnt/backups/pg_fondos/dump}; PITR=${BK_PITR:-$(readlink -f /opt/docker/db/postgresql17/pitr 2>/dev/null)}
OFFBOX=${BK_OFFBOX:-/mnt/d/desarrollo/fondos/db/backups/pg_fondos/dump}; MAXH=${BK_MAX_AGE_H:-36}; BASEH=${BK_BASE_MAX_AGE_H:-96}
age_h() { echo $(( ($(date +%s)-$(stat -c %Y "$1"))/3600 )); }

for db in fondos gestion; do
  mapfile -t F < <(ls -1t "$LOCAL"/${db}_2*.dump 2>/dev/null)
  if [ ${#F[@]} -eq 0 ]; then crit "no $db dump in $LOCAL"; continue; fi
  h=$(age_h "${F[0]}"); sz=$(stat -c %s "${F[0]}")
  if   [ "$h" -ge $((MAXH*2)) ]; then crit "$db newest dump ${h}h old"
  elif [ "$h" -ge "$MAXH" ];     then warn "$db newest dump ${h}h old"
  else ok "$db newest dump ${h}h old ($((sz/1048576)) MB, ${#F[@]} kept)"; fi
  if [ ${#F[@]} -ge 2 ]; then
    p=$(stat -c %s "${F[1]}"); [ "$p" -gt 0 ] && [ $((sz*100/p)) -lt 60 ] && crit "$db dump shrank to $((sz*100/p))% of the previous one"
  fi
  if docker exec -i "$PG_CONTAINER" pg_restore --list < "${F[0]}" 2>/dev/null | grep -q "TABLE DATA"; then ok "$db dump TOC readable"
  else crit "$db dump TOC unreadable / no table data"; fi
done
g=$(ls -1t "$LOCAL"/globals_*.sql 2>/dev/null | head -1)
if [ -n "$g" ]; then ok "globals dump ${g##*/} ($(age_h "$g")h)"; else warn "no globals dump"; fi

if [ -n "$PITR" ] && [ -d "$PITR" ]; then
  b=$(ls -1dt "$PITR"/base/* 2>/dev/null | head -1)
  if [ -n "$b" ]; then
    h=$(age_h "$b"); if [ "$h" -gt "$BASEH" ]; then warn "newest base backup ${h}h old (policy: every 3 days)"; else ok "newest base backup ${h}h old (${b##*/})"; fi
  else crit "no base backup under $PITR/base"; fi
  w=$(find "$PITR" -path '*wal*' -type f -mmin -120 2>/dev/null | head -1)
  if [ -n "$w" ]; then ok "WAL archive received a segment in the last 2h"; else warn "no new WAL segment archived in 2h (idle DB, or archiver stalled)"; fi
else warn "PITR directory not found (${PITR:-unset})"; fi

if [ -d "$OFFBOX" ]; then
  n=$(ls -1t "$OFFBOX"/*fondos*.dump 2>/dev/null | head -1)
  if [ -n "$n" ]; then h=$(age_h "$n"); if [ "$h" -gt 48 ]; then warn "off-box copy ${h}h old"; else ok "off-box copy ${h}h old"; fi
  else warn "off-box dir has no fondos dump"; fi
else warn "off-box mirror not mounted ($OFFBOX) — is D: attached?"; fi
maint_end
