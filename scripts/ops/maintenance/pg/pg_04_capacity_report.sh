#!/bin/bash
# pg_04_capacity_report.sh — size / growth / disk headroom (run weekly). READ-ONLY apart from appending
# one row to $MAINT_LOG_DIR/pg_capacity_history.csv so growth can be trended.
#   bash pg_04_capacity_report.sh [--db fondos]
# Warns when the filesystem holding PGDATA has < CAP_WARN_FREE_PCT free (default 20) / crit < CAP_CRIT_FREE_PCT (10)
# or pg_wal exceeds CAP_WAL_WARN_GB (default 20, ~1 day of WAL at P2 load).
set -uo pipefail
. "$(dirname "$0")/../lib/common.sh"; maint_args "$@"; maint_start pg_04_capacity_report; pg_init
WF=${CAP_WARN_FREE_PCT:-20}; CF=${CAP_CRIT_FREE_PCT:-10}; WALG=${CAP_WAL_WARN_GB:-20}
pgt "select datname, pg_size_pretty(pg_database_size(datname)) size from pg_database where not datistemplate order by pg_database_size(datname) desc"
pgt "select n.nspname||'.'||c.relname rel, c.relkind kind, pg_size_pretty(pg_total_relation_size(c.oid)) total, pg_size_pretty(pg_relation_size(c.oid)) heap, c.reltuples::bigint est_rows
     from pg_class c join pg_namespace n on n.oid=c.relnamespace where c.relkind in ('r','m') and n.nspname not in ('pg_catalog','information_schema') order by pg_total_relation_size(c.oid) desc limit 12"

dd=$(pg "show data_directory")
free_pct=$(docker exec "$PG_CONTAINER" df -P "$dd" | awk 'NR==2{gsub("%","",$5); print 100-$5}')
if   [ "$free_pct" -lt "$CF" ]; then crit "PGDATA filesystem ${free_pct}% free (< ${CF}%)"
elif [ "$free_pct" -lt "$WF" ]; then warn "PGDATA filesystem ${free_pct}% free (< ${WF}%)"
else ok "PGDATA filesystem ${free_pct}% free"; fi
wal_mb=$(docker exec "$PG_CONTAINER" du -sm "$dd/pg_wal" | cut -f1)
if [ "$wal_mb" -gt $((WALG*1024)) ]; then warn "pg_wal = ${wal_mb} MB (> ${WALG} GB) — archiver stalled or a slot pins WAL?"; else ok "pg_wal = ${wal_mb} MB"; fi

H="$MAINT_LOG_DIR/pg_capacity_history.csv"; [ -f "$H" ] || echo "date,db,db_bytes,pg_wal_mb,free_pct" > "$H"
now=$(pg "select pg_database_size('$DB')")
echo "$(date +%F),$DB,$now,$wal_mb,$free_pct" >> "$H"
prev=$(awk -F, -v d="$(date -d '28 days ago' +%F)" -v db="$DB" '$2==db && $1<=d {l=$3} END{print l}' "$H")
if [ -n "$prev" ]; then info "growth vs ~28d ago: $(( (now-prev)/1048576 )) MB ($(( (now-prev)*100/prev ))%)"; else info "no 28-day baseline yet in $H"; fi
maint_end
