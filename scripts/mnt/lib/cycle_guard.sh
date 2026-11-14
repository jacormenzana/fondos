#!/bin/bash
# scripts/mnt/lib/cycle_guard.sh -- the ONE predicate "is a work cycle running?" for every backup entry point (FND-0290, FND-0407).
#
# Why: on 2026-11-14 the 01:00 base-backup task ran inside the owner's P1P2P3 cycle; its I/O slowed P2 from milliseconds to seconds per statement and,
# four minutes in, a server process died (signal 13), Postgres crash-recovered and the cycle failed at PASO 3. No backup script knew about the cycle.
#
# Policy (owner order 2026-11-14): a RUNNING CYCLE ALWAYS WINS. A backup that finds the cycle lock held does not start: it exits 75 (EX_TEMPFAIL,
# "deferred, not a failure") and the scheduled task retries on its next trigger. Emergency override: FONDOS_BACKUP_IGNORE_CYCLE=1 (printed as a WARN).
#
# How the lock is read: every *_runCycle.bat holds proyecto1\log\fondos_cycle.lock OPEN with write sharing denied for the whole run (the file merely
# existing means nothing). From WSL, opening that file for append fails while the cycle runs and succeeds when it is free (verified 2026-11-14 on a
# scratch file: HELD while a Windows process held it, FREE after). A lock file that cannot be opened for ANY reason counts as held: a backup that
# defers once too often is cheap, one that overlaps a cycle is not.
#
# Test hooks: FONDOS_CYCLE_GUARD=held|free forces the answer; FONDOS_CYCLE_LOCK_FILE points at another lock file.
#
# Usage (source it):  . "$(dirname "$0")/../lib/cycle_guard.sh"; cycle_guard_or_defer "the base backup"

CYCLE_LOCK=${FONDOS_CYCLE_LOCK_FILE:-/mnt/c/desarrollo/fondos/proyecto1/log/fondos_cycle.lock}
EX_DEFERRED=75

cycle_active() {
  case "${FONDOS_CYCLE_GUARD:-auto}" in held) return 0;; free) return 1;; esac
  [ -e "$CYCLE_LOCK" ] || return 1
  if ( exec 3>>"$CYCLE_LOCK" ) 2>/dev/null; then return 1; else return 0; fi
}

# $1 = what would run. Returns normally when no cycle runs; otherwise prints one line and exits 75.
cycle_guard_or_defer() {
  local what=${1:-the backup}
  if [ "${FONDOS_BACKUP_IGNORE_CYCLE:-0}" = 1 ]; then
    echo "[$(date +%T)] WARN: FONDOS_BACKUP_IGNORE_CYCLE=1: $what runs WITHOUT checking the cycle lock"
    return 0
  fi
  if cycle_active; then
    echo "[$(date +%T)] DEFERRED: $what not started: a work cycle holds $CYCLE_LOCK. Nothing is wrong; the scheduled task retries on its next trigger."
    exit "$EX_DEFERRED"
  fi
}

# The backup is the lowest-priority process on the machine: idle I/O class and the lowest CPU priority for this shell and everything it starts
# (cp, sha256sum, rsync, the docker client). pg_dump itself runs inside the container and is not reniced by this.
lower_own_priority() {
  renice -n 19 -p $$ >/dev/null 2>&1 || true
  if command -v ionice >/dev/null 2>&1; then ionice -c3 -p $$ >/dev/null 2>&1 || true; fi
}

# Age in seconds of a stamp YYYYMMDD_HHMMSS (base backup names) or YYYYMMDD_HHMM (dump names); empty when the stamp is malformed.
stamp_age_seconds() {
  local s=$1 hh mm ss=00 then
  [[ "$s" =~ ^[0-9]{8}_[0-9]{4}([0-9]{2})?$ ]] || return 1
  hh=${s:9:2}; mm=${s:11:2}; [ "${#s}" -ge 15 ] && ss=${s:13:2}
  then=$(date -d "${s:0:4}-${s:4:2}-${s:6:2} $hh:$mm:$ss" +%s 2>/dev/null) || return 1
  echo $(( $(date +%s) - then ))
}
