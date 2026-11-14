#!/bin/bash
# backup_rotating.sh -- the logical backup of the LIVE Postgres: one set per day, only the TWO newest dates kept on the computer AND on the external disk,
# never while a work cycle runs (FND-0290, FND-0407; owner order 2026-11-14).
#
#   wsl.exe -d Ubuntu -u root -- bash /mnt/c/desarrollo/fondos/scripts/mnt/backup/backup_rotating.sh [--keep-dates N] [--no-external] [--force]
#
# What one run does, in this order:
#   0. PRIORITY / CYCLE GUARD  a running cycle always wins (lib/cycle_guard.sh): while a cycle holds the lock the run exits 75 = DEFERRED (not a failure)
#                              and the scheduled task retries on its next trigger. The process itself runs at idle I/O class and the lowest CPU priority.
#   1. DUMP                    backup_live_pg.sh: pg_dump -Fc of fondos + gestion + roles into $OUT, checked by its table of contents. Skipped when a complete set
#                              of TODAY already exists (so an hourly trigger takes ONE dump a day); --force takes a new one.
#   2. LOCAL ROTATION          only after the new set is verified: of every calendar date keep only its newest COMPLETE set (fondos + gestion + globals), and
#                              of those keep the --keep-dates (default 2) newest dates. Everything else with a dump-set name is deleted. Files younger than
#                              BACKUP_PRUNE_MIN_AGE_MIN (15) are never deleted (a dump taken by hand at the same moment).
#   3. EXTERNAL COPY           the newest set to the external disk D:, each file SHA-256-compared with its source (a mismatch deletes the bad copy and fails the
#                              copy). D: absent or unwritable is NOT a failure of the backup: the local set stands, the run ends LOCAL_ONLY (exit 1) and the next
#                              trigger copies it once D: is back. The dump never depends on D: (the FND-0290 defect: backup_offbox.sh mounted D: first and took no dump).
#   4. EXTERNAL ROTATION       the same two-dates rule on D:, only when this run's copy was verified.
#
# Exit codes: 0 OK (every requested target updated) | 1 LOCAL_ONLY (dump fine, external disk missing or its copy failed) | 2 FAILED (dump failed or unverifiable;
#             nothing was deleted) | 3 pre-flight (another rotating backup runs, bad argument) | 75 DEFERRED (a cycle is running).
#
# Scope: the LOGICAL dump sets only. The physical PITR set (base backups + WAL) keeps its own policy (setup_wal_archive.sh: newest 2 bases, prune; backup_offbox.sh
# mirrors it to D:), which is cycle-aware too.
#
# Overridable for tests / other layouts: BACKUP_OUT, BACKUP_EXT_DIR, OFFBOX_MNT (a set value disables the D: mount attempt), BACKUP_DUMP_SCRIPT, BACKUP_LOG,
#   BACKUP_LOCKDIR, BACKUP_TODAY (YYYYMMDD), BACKUP_PRUNE_MIN_AGE_MIN, FONDOS_BACKUP_IGNORE_CYCLE (emergency), FONDOS_CYCLE_GUARD / FONDOS_CYCLE_LOCK_FILE.
set -uo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/../lib/cycle_guard.sh"

OUT=${BACKUP_OUT:-/mnt/backups/pg_fondos/dump}
MNT=${OFFBOX_MNT:-/mnt/d}
EXT=${BACKUP_EXT_DIR:-$MNT/desarrollo/fondos/db/backups/pg_fondos/dump}      # the same folder backup_offbox.sh uses for the dumps on D:
DUMP_SCRIPT=${BACKUP_DUMP_SCRIPT:-$HERE/backup_live_pg.sh}
LOG=${BACKUP_LOG:-/mnt/c/data/backups/backup_rotating.log}
LOCKDIR=${BACKUP_LOCKDIR:-/tmp/fondos_backup_rotating.lock}
TODAY=${BACKUP_TODAY:-$(date +%Y%m%d)}
MINAGE=${BACKUP_PRUNE_MIN_AGE_MIN:-15}
KEEP_DATES=2; USE_EXT=1; FORCE=0; DRY=0; VIRTUAL_STAMP=""

while [ $# -gt 0 ]; do case "$1" in
  --keep-dates) KEEP_DATES="${2:-}"; shift;;
  --no-external) USE_EXT=0;;
  --force) FORCE=1;;
  --dry-run) DRY=1;;                                  # changes NOTHING: prints what would be dumped, copied and deleted (a new set for today is assumed)
  -h|--help) sed -n '2,32p' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
  *) echo "unknown argument: $1 (see --help)"; exit 3;; esac; shift; done
[[ "$KEEP_DATES" =~ ^[0-9]+$ ]] && [ "$KEEP_DATES" -ge 1 ] || { echo "--keep-dates must be an integer >= 1"; exit 3; }

mkdir -p "$(dirname "$LOG")" 2>/dev/null || true
exec > >(tee -a "$LOG") 2>&1
ts() { date '+%F %T'; }

cycle_guard_or_defer "the rotating backup"           # a running cycle always wins: exit 75
lower_own_priority

# single run at a time (the task repeats hourly; MultipleInstances IgnoreNew is the first line of defence, this is the second)
if mkdir "$LOCKDIR" 2>/dev/null; then echo $$ > "$LOCKDIR/pid"
else
  p=$(cat "$LOCKDIR/pid" 2>/dev/null || true)
  if [ -n "$p" ] && kill -0 "$p" 2>/dev/null; then echo "[$(ts)] another rotating backup is running (pid $p): nothing to do"; exit 3; fi
  echo "[$(ts)] stale lock (pid ${p:-?} is gone): taking it over"; rm -rf "$LOCKDIR"; mkdir "$LOCKDIR" && echo $$ > "$LOCKDIR/pid"
fi
trap 'rm -rf "$LOCKDIR"' EXIT

run_low() {
  if command -v nice >/dev/null 2>&1; then
    if command -v ionice >/dev/null 2>&1; then nice -n 19 ionice -c3 "$@"; else nice -n 19 "$@"; fi
  else "$@"; fi
}

# ---- sets -----------------------------------------------------------------------------------------------------------------------------------------------
# A COMPLETE set = fondos_<stamp>.dump + gestion_<stamp>.dump + globals_<stamp>.sql, all non-empty; stamp = YYYYMMDD_HHMM. Newest first.
complete_stamps() {
  local d=$1 f s
  for f in "$d"/fondos_2*.dump; do
    [ -e "$f" ] || continue
    s=${f##*/fondos_}; s=${s%.dump}
    [[ "$s" =~ ^[0-9]{8}_[0-9]{4}$ ]] || continue
    [ -s "$d/fondos_$s.dump" ] && [ -s "$d/gestion_$s.dump" ] && [ -s "$d/globals_$s.sql" ] && echo "$s"
  done | sort -r
  [ -z "$VIRTUAL_STAMP" ] || echo "$VIRTUAL_STAMP"      # --dry-run only: the set a real run would add today (sorted by the caller below)
}

# The stamps to keep: the newest complete set of each of the $2 newest dates.
keep_stamps() {
  local d=$1 n=$2 s day last="" c=0
  while read -r s; do
    [ -n "$s" ] || continue
    day=${s%%_*}; [ "$day" = "$last" ] && continue
    last=$day; echo "$s"; c=$((c+1)); [ "$c" -ge "$n" ] && break
  done < <(complete_stamps "$d" | sort -r)
}

recent() { [ "$MINAGE" -gt 0 ] && [ -n "$(find "$1" -mmin "-$MINAGE" 2>/dev/null)" ]; }

rotate() {                                            # $1 dir, $2 dates to keep
  local d=$1 n=$2 keep f b s removed=0 kept
  keep=$(keep_stamps "$d" "$n" | tr '\n' ' ')
  [ -n "${keep// /}" ] || { echo "[$(ts)] rotation skipped in $d: no complete set to keep (nothing is deleted)"; return 0; }
  for f in "$d"/fondos_2*.dump "$d"/gestion_2*.dump "$d"/globals_2*.sql; do
    [ -e "$f" ] || continue
    b=${f##*/}; s=${b#*_}; s=${s%.*}
    case " $keep " in *" $s "*) continue;; esac
    if recent "$f"; then echo "   kept $b (younger than $MINAGE min)"; continue; fi
    if [ "$DRY" = 1 ]; then echo "   would delete $b"; removed=$((removed+1)); continue; fi
    rm -f -- "$f" && { echo "   deleted $b"; removed=$((removed+1)); }
  done
  kept=$(keep_stamps "$d" "$n" | tr '\n' ' ')
  echo "[$(ts)] rotation in $d: kept ${kept}(newest set of each of the $n newest dates), $([ "$DRY" = 1 ] && echo "would delete" || echo "deleted") $removed file(s)"
}

set_bytes() { local s=$1 t=0 f; for f in "$OUT/fondos_$s.dump" "$OUT/gestion_$s.dump" "$OUT/globals_$s.sql"; do t=$((t + $(stat -c %s "$f" 2>/dev/null || echo 0))); done; echo "$t"; }

# ---- external disk --------------------------------------------------------------------------------------------------------------------------------------
EXT_NOTE=""
ext_ready() {
  [ "$USE_EXT" = 1 ] || { EXT_NOTE="disabled (--no-external)"; return 1; }
  if [ -z "${BACKUP_EXT_DIR:-}" ] && [ -z "${OFFBOX_MNT:-}" ] && ! mountpoint -q "$MNT" 2>/dev/null; then
    mkdir -p "$MNT"
    mount -t drvfs D: "$MNT" 2>/dev/null || { EXT_NOTE="D: cannot be mounted (is the external disk plugged in?)"; return 1; }
    echo "[$(ts)] mounted D: at $MNT"
  fi
  if [ -z "${BACKUP_EXT_DIR:-}" ]; then [ -d "$MNT/desarrollo" ] || { EXT_NOTE="$MNT/desarrollo is missing (disk absent or the wrong disk)"; return 1; }; fi
  mkdir -p "$EXT" 2>/dev/null && [ -w "$EXT" ] || { EXT_NOTE="cannot write to $EXT"; return 1; }
  return 0
}

copy_one() {                                          # $1 source file; the destination is $EXT/<same name>
  local src=$1 dst="$EXT/${1##*/}" a c
  if [ -s "$dst" ] && [ "$(stat -c %s "$src")" = "$(stat -c %s "$dst")" ] \
     && [ "$(run_low sha256sum "$src" | cut -d' ' -f1)" = "$(run_low sha256sum "$dst" | cut -d' ' -f1)" ]; then
    echo "   ${src##*/}: already on the external disk (SHA-256 match)"; return 0
  fi
  if ! { run_low cp -f -- "$src" "$dst.partial" && mv -f -- "$dst.partial" "$dst"; }; then rm -f -- "$dst.partial"; echo "   ${src##*/}: copy FAILED"; return 1; fi
  a=$(run_low sha256sum "$src" | cut -d' ' -f1); c=$(run_low sha256sum "$dst" | cut -d' ' -f1)
  if [ -n "$a" ] && [ "$a" = "$c" ]; then echo "   ${src##*/}: copied, SHA-256 match ($(stat -c %s "$src") bytes)"; return 0; fi
  echo "   ${src##*/}: SHA-256 MISMATCH, bad copy deleted"; rm -f -- "$dst"; return 1
}

echo "=== [$(ts)] rotating backup start (keep $KEEP_DATES date(s), external=$USE_EXT, force=$FORCE, dry-run=$DRY, today=$TODAY, out=$OUT)"

# ---- 1. dump ---------------------------------------------------------------------------------------------------------------------------------------------
mkdir -p "$OUT" 2>/dev/null || { echo "[$(ts)] FAILED: cannot create $OUT"; echo "RESULT: FAILED"; exit 2; }
newest=$(complete_stamps "$OUT" | head -1)
if [ -n "$newest" ] && [ "${newest%%_*}" = "$TODAY" ] && [ "$FORCE" = 0 ]; then
  echo "[$(ts)] a complete set of today already exists ($newest): no new dump"
elif [ "$DRY" = 1 ]; then
  VIRTUAL_STAMP="${TODAY}_9999"; newest=$VIRTUAL_STAMP
  echo "[$(ts)] DRY RUN: a dump would be taken now (no complete set of today); the plan below assumes its set exists"
else
  stamp="${TODAY}_$(date +%H%M)"
  echo "[$(ts)] dump -> $OUT (stamp $stamp)"
  if ! BACKUP_OUT="$OUT" BACKUP_STAMP="$stamp" BACKUP_KEEP=9999 bash "$DUMP_SCRIPT"; then
    echo "[$(ts)] FAILED: the dump reported problems; nothing was rotated or deleted"; echo "RESULT: FAILED"; exit 2
  fi
  for f in "$OUT/fondos_$stamp.dump" "$OUT/gestion_$stamp.dump" "$OUT/globals_$stamp.sql"; do
    [ -s "$f" ] || { echo "[$(ts)] FAILED: ${f##*/} is missing or empty after the dump; nothing was rotated or deleted"; echo "RESULT: FAILED"; exit 2; }
  done
  newest=$stamp
fi

# ---- 2. local rotation (only now that a verified set exists) ---------------------------------------------------------------------------------------------
rotate "$OUT" "$KEEP_DATES"

# ---- 3 + 4. external disk --------------------------------------------------------------------------------------------------------------------------------
RESULT=OK; RC=0
if ext_ready; then
  echo "[$(ts)] external copy of $newest -> $EXT"
  ok=1
  if [ "$DRY" = 1 ]; then echo "   DRY RUN: the three files of $newest would be copied and SHA-256-verified"; VIRTUAL_STAMP=$newest      # the plan for D: includes the set that would arrive
  else for f in "$OUT/fondos_$newest.dump" "$OUT/gestion_$newest.dump" "$OUT/globals_$newest.sql"; do copy_one "$f" || ok=0; done; fi
  if [ "$ok" = 1 ]; then rotate "$EXT" "$KEEP_DATES"
  else echo "[$(ts)] external copy had problems: the external disk is NOT rotated (its older sets stay)"; RESULT=LOCAL_ONLY; RC=1; fi
elif [ "$USE_EXT" = 1 ]; then
  echo "[$(ts)] WARN: external copy not made: $EXT_NOTE. The local set stands; the next run copies it when the disk is back."
  RESULT=LOCAL_ONLY; RC=1
else
  echo "[$(ts)] external copy skipped: $EXT_NOTE"
fi
echo "[$(ts)] local   sets: $(keep_stamps "$OUT" "$KEEP_DATES" | tr '\n' ' ')"
[ "$USE_EXT" = 1 ] && [ "$RESULT" = OK ] && echo "[$(ts)] external sets: $(keep_stamps "$EXT" "$KEEP_DATES" | tr '\n' ' ')"
echo "RESULT: $RESULT"
exit $RC
