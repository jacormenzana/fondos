#!/bin/bash
# Shared helpers for the preventive-maintenance script families (pg/, wsl/). Source it, do not run it.
#
# Conventions (all scripts):
#   * READ-ONLY by default. Anything that changes state needs an explicit --apply.
#   * Exit code: 0 = all OK, 1 = warnings, 2 = critical findings, 3 = the script itself failed.
#   * Output is mirrored to $MAINT_LOG_DIR/<script>_<stamp>.log (kept $MAINT_LOG_KEEP days).
#   * Never stops/recreates containers, never prints secrets, never touches volumes.
MAINT_LOG_DIR=${MAINT_LOG_DIR:-/mnt/c/data/logs/maintenance}
MAINT_LOG_KEEP=${MAINT_LOG_KEEP:-60}
PG_CONTAINER=${PG_CONTAINER:-fondos_postgres}
PG_SECRETS_DIR=${PG_SECRETS_DIR:-/opt/docker/db/postgresql17/secrets}
PGU=${PGU:-fondos_owner}
DB=${DB:-fondos}
APPLY=0; STATUS=0; ARGS=()

maint_args() {   # sets APPLY, DB (--db NAME) and ARGS (the remaining flags)
  while [ $# -gt 0 ]; do
    case "$1" in
      --apply) APPLY=1;;
      --db) DB="$2"; shift;;
      -h|--help) sed -n '2,/^set -/p' "$0" | grep '^#' | sed 's/^# \{0,1\}//'; exit 0;;
      *) ARGS+=("$1");;
    esac; shift
  done
}

maint_start() {  # maint_start <script-name> : tee everything to a log file, prune old logs
  mkdir -p "$MAINT_LOG_DIR" 2>/dev/null || true
  LOG="$MAINT_LOG_DIR/${1}_$(date +%Y%m%d_%H%M%S).log"
  exec > >(tee -a "$LOG") 2>&1
  find "$MAINT_LOG_DIR" -name '*.log' -mtime +"$MAINT_LOG_KEEP" -delete 2>/dev/null || true
  echo "=== $1 $(date '+%F %T') host=$(hostname) apply=$APPLY ==="
}

ok()   { echo "[ OK ] $*"; }
info() { echo "[INFO] $*"; }
warn() { echo "[WARN] $*"; [ $STATUS -lt 1 ] && STATUS=1; return 0; }
crit() { echo "[CRIT] $*"; STATUS=2; return 0; }
act()  { echo "[ACT ] $*"; }
need_apply() { [ $APPLY -eq 1 ] || { info "dry-run: $* (re-run with --apply)"; return 1; }; }

maint_end() {
  case $STATUS in 0) echo "=== RESULT: OK ===";; 1) echo "=== RESULT: WARNINGS ===";; *) echo "=== RESULT: CRITICAL ===";; esac
  sleep 0.3   # let the tee process flush before exit
  exit $STATUS
}

# --- Postgres (docker exec into the live container; same access path as backup_live_pg.sh) ---
pg_init() {
  [ -r "$PG_SECRETS_DIR/postgres_password" ] || { echo "[CRIT] cannot read $PG_SECRETS_DIR/postgres_password (run as the WSL user that owns /opt/docker, or as root)"; exit 3; }
  PGPW=$(cat "$PG_SECRETS_DIR/postgres_password")
  docker exec "$PG_CONTAINER" pg_isready -h 127.0.0.1 -q || { echo "[CRIT] $PG_CONTAINER is not accepting connections"; exit 3; }
}
# pg  "<sql>" -> unaligned, tuples-only, '|' separated (for scripting)
pg()  { docker exec -e PGPASSWORD="$PGPW" "$PG_CONTAINER" psql -h 127.0.0.1 -U "$PGU" -d "$DB" -X -Atq -F '|' -c "$1"; }
# pgt "<sql>" -> aligned table (for humans)
pgt() { docker exec -e PGPASSWORD="$PGPW" "$PG_CONTAINER" psql -h 127.0.0.1 -U "$PGU" -d "$DB" -X -q -c "$1"; }

# level_ge <value> <warn> <crit> <label> [unit] : integer threshold check
level_ge() {
  local v=$1 w=$2 c=$3 label=$4 unit=${5:-}
  if   [ "$v" -ge "$c" ]; then crit "$label = $v$unit (>= $c$unit)"
  elif [ "$v" -ge "$w" ]; then warn "$label = $v$unit (>= $w$unit)"
  else ok "$label = $v$unit"; fi
}
