#!/bin/bash
# pg_02_vacuum_analyze.sh — dead-tuple / stale-statistics maintenance (run weekly, after the P2 run).
#   bash pg_02_vacuum_analyze.sh [--db fondos] [--apply]
# Lists tables in bronze/silver/gold/control with dead_tuples >= VAC_MIN_DEAD and dead% >= VAC_DEAD_PCT,
# or with >= STAT_MOD_PCT% rows modified since the last ANALYZE. --apply runs plain
# VACUUM (ANALYZE) on those, one table at a time (non-blocking). Never VACUUM FULL (exclusive lock).
# Env: VAC_MIN_DEAD=10000 VAC_DEAD_PCT=10 STAT_MOD_PCT=10
set -uo pipefail
. "$(dirname "$0")/../lib/common.sh"; maint_args "$@"; maint_start pg_02_vacuum_analyze; pg_init
MIN=${VAC_MIN_DEAD:-10000}; PCT=${VAC_DEAD_PCT:-10}; MOD=${STAT_MOD_PCT:-10}
WHERE="schemaname in ('bronze','silver','gold','control') and (
  (n_dead_tup >= $MIN and 100.0*n_dead_tup/greatest(n_live_tup+n_dead_tup,1) >= $PCT)
  or (n_mod_since_analyze >= $MIN and 100.0*n_mod_since_analyze/greatest(n_live_tup,1) >= $MOD))"

mapfile -t T < <(pg "select format('%I.%I',schemaname,relname) from pg_stat_user_tables where $WHERE order by n_dead_tup desc")
if [ ${#T[@]} -eq 0 ]; then ok "no table needs vacuum/analyze"; maint_end; fi

pgt "select schemaname||'.'||relname as tbl, n_live_tup live, n_dead_tup dead,
            round(100.0*n_dead_tup/greatest(n_live_tup+n_dead_tup,1),1) dead_pct, n_mod_since_analyze mod_since_analyze,
            greatest(last_autovacuum,last_vacuum)::date last_vacuum, greatest(last_autoanalyze,last_analyze)::date last_analyze
     from pg_stat_user_tables where $WHERE order by n_dead_tup desc limit 30"
warn "${#T[@]} table(s) need vacuum/analyze"
if need_apply "VACUUM (ANALYZE) on ${#T[@]} table(s)"; then
  for t in "${T[@]}"; do
    s=$(date +%s); act "VACUUM (ANALYZE) $t"
    if pg "VACUUM (ANALYZE) $t" >/dev/null; then info "  done in $(( $(date +%s)-s ))s"; else crit "  VACUUM failed on $t"; fi
  done
fi
maint_end
