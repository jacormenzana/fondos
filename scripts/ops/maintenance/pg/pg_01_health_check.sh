#!/bin/bash
# pg_01_health_check.sh — READ-ONLY health snapshot of the live Postgres (run daily).
#   bash pg_01_health_check.sh [--db fondos]
# Checks: connections, idle-in-transaction / long queries, XID wraparound, inactive replication slots
# (they pin WAL and fill the disk), WAL archiver failures, invalid indexes, cache hit ratio,
# forced checkpoints, deadlocks / temp-file spill since stats reset.
set -uo pipefail
. "$(dirname "$0")/../lib/common.sh"; maint_args "$@"; maint_start pg_01_health_check; pg_init

used=$(pg "select count(*) from pg_stat_activity where backend_type='client backend'"); max=$(pg "show max_connections")
level_ge $((used*100/max)) 70 90 "connections ${used}/${max} used" "%"

n=$(pg "select count(*) from pg_stat_activity where state like 'idle in transaction%' and now()-xact_start > interval '5 min'")
if [ "$n" -gt 0 ]; then
  warn "$n session(s) idle in transaction > 5 min (they block vacuum):"
  pgt "select pid,usename,application_name,now()-xact_start as age,left(query,80) q from pg_stat_activity where state like 'idle in transaction%' and now()-xact_start > interval '5 min'"
else ok "no stale idle-in-transaction sessions"; fi
n=$(pg "select count(*) from pg_stat_activity where state='active' and now()-query_start > interval '30 min' and backend_type='client backend'")
if [ "$n" -gt 0 ]; then
  info "$n active query(ies) running > 30 min (may be a legitimate P2/P3 run):"
  pgt "select pid,usename,now()-query_start as age,left(query,80) q from pg_stat_activity where state='active' and now()-query_start > interval '30 min' and backend_type='client backend'"
else ok "no query running > 30 min"; fi

age=$(pg "select max(age(datfrozenxid))::bigint from pg_database")
level_ge $((age/1000000)) 500 1500 "oldest datfrozenxid age (M xids, hard stop at ~2100M)" "M"

n=$(pg "select count(*) from pg_replication_slots where not active")
if [ "$n" -gt 0 ]; then
  crit "$n inactive replication slot(s) — retained WAL grows unbounded:"
  pgt "select slot_name,slot_type,pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(),restart_lsn)) retained from pg_replication_slots where not active"
else ok "no inactive replication slots"; fi

arch=$(pg "select archived_count||'|'||failed_count||'|'||coalesce(to_char(last_failed_time,'YYYY-MM-DD HH24:MI'),'-')||'|'||(last_failed_time is not null and (last_archived_time is null or last_failed_time>last_archived_time)) from pg_stat_archiver")
IFS='|' read -r a f lf bad <<<"$arch"
if [ "$bad" = "t" ]; then crit "WAL archiver is FAILING (last failure $lf; archived=$a failed=$f) — pg_wal will fill the disk"
else ok "WAL archiver healthy (archived=$a, failed_total=$f)"; fi

n=$(pg "select count(*) from pg_index where not indisvalid")
if [ "$n" -gt 0 ]; then warn "$n invalid index(es) — run pg_03_index_maintenance.sh"; else ok "no invalid indexes"; fi

hit=$(pg "select coalesce(round(100*sum(blks_hit)/nullif(sum(blks_hit+blks_read),0)),100)::int from pg_stat_database")
if [ "$hit" -lt 95 ]; then warn "cache hit ratio ${hit}% (< 95%)"; else ok "cache hit ratio ${hit}%"; fi

IFS='|' read -r t q <<<"$(pg "select num_timed||'|'||num_requested from pg_stat_checkpointer")"
if [ $((t+q)) -gt 0 ] && [ $((q*100/(t+q))) -gt 50 ]; then warn "checkpoints: $q requested vs $t timed — consider raising max_wal_size"
else ok "checkpoints timed=$t requested=$q"; fi
info "deadlocks=$(pg "select sum(deadlocks) from pg_stat_database") temp_files=$(pg "select sum(temp_files) from pg_stat_database") ($(pg "select pg_size_pretty(sum(temp_bytes)) from pg_stat_database") spilled) since stats reset"
info "uptime: $(pg "select now()-pg_postmaster_start_time()")"
maint_end
