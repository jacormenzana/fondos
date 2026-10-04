#!/bin/bash
# pg_03_index_maintenance.sh — index hygiene (run monthly).
#   bash pg_03_index_maintenance.sh [--db fondos] [--apply]
# Reports: invalid indexes (left by a failed CREATE/REINDEX CONCURRENTLY), exact-duplicate indexes,
# never-used non-unique indexes > IDX_UNUSED_MB. --apply ONLY rebuilds invalid indexes with
# REINDEX INDEX CONCURRENTLY. Unused/duplicate indexes are reported, never dropped (a drop is DDL:
# owner + reviewed migration; idx_scan counters reset on stats reset / restore).
set -uo pipefail
. "$(dirname "$0")/../lib/common.sh"; maint_args "$@"; maint_start pg_03_index_maintenance; pg_init
MB=${IDX_UNUSED_MB:-50}

mapfile -t INV < <(pg "select format('%I.%I',n.nspname,c.relname) from pg_index i join pg_class c on c.oid=i.indexrelid join pg_namespace n on n.oid=c.relnamespace where not i.indisvalid")
if [ ${#INV[@]} -eq 0 ]; then ok "no invalid indexes"; else
  warn "${#INV[@]} invalid index(es): ${INV[*]}"
  if need_apply "REINDEX INDEX CONCURRENTLY on invalid indexes"; then
    for ix in "${INV[@]}"; do
      act "REINDEX INDEX CONCURRENTLY $ix"
      if pg "REINDEX INDEX CONCURRENTLY $ix" >/dev/null; then info "  rebuilt"; else crit "  failed (a leftover *_ccnew index may need a manual DROP)"; fi
    done
  fi
fi

DUPQ="from pg_index i join pg_class c on c.oid=i.indexrelid join pg_namespace n on n.oid=c.relnamespace
      group by i.indrelid,i.indkey,i.indclass,i.indcollation,coalesce(i.indexprs::text,''),coalesce(i.indpred::text,'') having count(*)>1"
d=$(pg "select count(*) from (select 1 $DUPQ) x")
if [ "$d" -gt 0 ]; then
  warn "$d group(s) of exact-duplicate indexes:"
  pgt "select string_agg(format('%I.%I',n.nspname,c.relname),' = ') duplicates, pg_size_pretty(sum(pg_relation_size(i.indexrelid))) total_size $DUPQ"
else ok "no duplicate indexes"; fi

UQ="from pg_stat_user_indexes s join pg_index i using(indexrelid)
    where s.idx_scan=0 and not i.indisunique and not i.indisprimary and pg_relation_size(s.indexrelid) > $MB*1024*1024"
u=$(pg "select count(*) $UQ")
if [ "$u" -gt 0 ]; then
  info "$u never-scanned non-unique index(es) > ${MB}MB since stats reset (review; never auto-dropped):"
  pgt "select s.schemaname||'.'||s.indexrelname idx, s.relname tbl, pg_size_pretty(pg_relation_size(s.indexrelid)) size $UQ order by pg_relation_size(s.indexrelid) desc limit 20"
else ok "no large unused indexes"; fi
maint_end
