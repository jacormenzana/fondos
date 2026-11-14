#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
p1_cost_recompute.py -- the unattended cost recompute + audit + certification (owner directive 2026-11-04, Directive D-0).

Until now the owner ran `run_block.py --nature-first --master-db --recompute-costs` by hand, pasted the tail of its console, and then an agent ran the audit and certified
the incidents. This module is that whole sequence as ONE native sub-process chain, with the exit code of every step captured and the log tail parsed:

    1. preflight   no launcher cycle lock, no other pipeline / audit job running, no other instance of this script
    2. recompute   python proyecto1/run_block.py --nature-first --master-db --recompute-costs   (stdout/stderr -> the P1 log directory, cost_recompute_<stamp>.log)
    3. log tail    RC 0 AND "Registros publicados: N" with N > 0 AND no Traceback / [ERROR line in the log, else the run stops here (RC 111)
    4. audit       python scripts/audit/stat/run_statistical_audit.py --domain costs --mode report --persist --run-id post_<stamp>_costs   (FULL, complete)
    5. certify     for every OPEN cost incident: python scripts/audit/stat/audit_incident.py --certify ID --run <that run> --apply
                   (RC 1 = the re-audit still shows a violator: recorded, not a failure of this run)
    6. receipt     one JSON line per run in out/audit/cost_recompute_receipts.jsonl

Usage:
    python scripts/launch/p1_cost_recompute.py                      # the whole sequence
    python scripts/launch/p1_cost_recompute.py --dry-run            # preflight + the plan, runs nothing
    python scripts/launch/p1_cost_recompute.py --if-changed OLD NEW # only when the git range OLD..NEW touches the cost extraction code and NEW was not processed yet
                                                                    #   (the call the owner's deploy watcher makes: idempotent per commit)

Switch: COST_RECOMPUTE_AUTO_ENABLED (shared/config.py) gates --if-changed only; a direct run is the owner's explicit order.
Exit codes (own range, NORMAS_BATCH): 0 done | 100 usage | 101 busy (another instance) | 102 preflight refused (cycle lock / job running) | 110 recompute failed (RC != 0)
| 111 recompute log reports errors or published nothing | 120 audit failed | 130 audit done, at least one incident NOT certifiable (see the receipt) | 140 switch off / nothing to do.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parents[2]

EXIT_OK, EXIT_USAGE, EXIT_BUSY, EXIT_PREFLIGHT = 0, 100, 101, 102
EXIT_RECOMPUTE, EXIT_LOG, EXIT_AUDIT, EXIT_NOT_CERTIFIED, EXIT_NOTHING = 110, 111, 120, 130, 140

# The code whose change makes the stored cost schedule stale (the recompute re-reads the stored KID text with it).
WATCH_FILES = (
    "proyecto1/core/priips_cost_extractor.py",
    "proyecto1/core/ucits_cost_extractor.py",
    "proyecto1/core/cost_table_parser.py",
    "proyecto1/core/cost_cross_validator.py",
    "proyecto1/core/cost_arbitration.py",
    "proyecto1/core/cost_scale.py",
)
LOCK_STALE_HOURS = 6.0

_PUBLISHED = re.compile(r"Registros publicados:\s*(\d+)")
_RECON = re.compile(r"\[UNIVERSE-RECON\]")
_ERROR_LINE = re.compile(r"(?:^Traceback \(most recent call last\)|\[ERROR[\]-])", re.MULTILINE)
_INCIDENT = re.compile(r"^\s*#(\d+)\s+(\w+)\s+(\S+)\s+ap=(\S+)", re.MULTILINE)


# ---------------------------------------------------------------------------------------------------------------------------------------------------
# Pure functions (unit-tested in tests/test_p1_cost_recompute.py)
# ---------------------------------------------------------------------------------------------------------------------------------------------------

def tail(text: str, n: int = 40) -> str:
    return "\n".join((text or "").splitlines()[-n:])


def parse_recompute_log(text: str) -> dict:
    """What the log of run_block.py says: {'published': int|None, 'reconciled': bool, 'errors': int}. Only the parts that decide the verdict."""
    m = _PUBLISHED.findall(text or "")
    return {"published": int(m[-1]) if m else None, "reconciled": bool(_RECON.search(text or "")), "errors": len(_ERROR_LINE.findall(text or ""))}


def recompute_verdict(rc: int, log: dict) -> int:
    """EXIT_OK when the recompute really happened and finished; else the exit code that says why."""
    if rc != 0:
        return EXIT_RECOMPUTE
    if log["errors"] or not log["published"]:
        return EXIT_LOG
    return EXIT_OK


def parse_incident_list(text: str) -> list:
    """`audit_incident.py --list` -> [{'id', 'state', 'key', 'ap'}]."""
    return [{"id": int(i), "state": s, "key": k, "ap": a} for i, s, k, a in _INCIDENT.findall(text or "")]


def open_cost_incidents(items: list) -> list:
    """The incidents this run may certify: domain cost_attributes (finding_key starts with it), state OPEN."""
    return [x for x in items if x["state"] == "OPEN" and x["key"].startswith("cost_attributes|")]


def changed_watch_files(changed: list) -> list:
    return sorted(set(changed) & set(WATCH_FILES))


def final_exit(certify_rcs: dict) -> int:
    """EXIT_OK when every certification succeeded (or there was none), EXIT_NOT_CERTIFIED when at least one was refused."""
    return EXIT_NOT_CERTIFIED if any(rc != 0 for rc in certify_rcs.values()) else EXIT_OK


# ---------------------------------------------------------------------------------------------------------------------------------------------------
# Effects (injected in tests)
# ---------------------------------------------------------------------------------------------------------------------------------------------------

def run_cmd(cmd: list, log_path: Optional[Path] = None, timeout: int = 4 * 3600) -> tuple:
    """(rc, combined output). With `log_path` the output is also written there (the recompute log)."""
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, stdin=subprocess.DEVNULL,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    out = (r.stdout or "") + (r.stderr or "")
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(out, encoding="utf-8")
    return r.returncode, out


def acquire_lock(path: Path, now: Optional[datetime] = None) -> bool:
    """O_EXCL lock file; a lock older than LOCK_STALE_HOURS is a crashed run and is taken over."""
    now = now or datetime.now()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        age_h = (now.timestamp() - path.stat().st_mtime) / 3600.0
        if age_h < LOCK_STALE_HOURS:
            return False
        path.unlink()
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    os.write(fd, f"{os.getpid()} {now.isoformat()}\n".encode())
    os.close(fd)
    return True


def preflight() -> Optional[str]:
    """None when the shared tree is free for a long write job, else the reason it is not (cycle lock held / a pipeline or audit job running)."""
    sys.path.insert(0, str(ROOT / "scripts" / "gov" / "repo"))
    import preflight_check as pf
    if pf.cycle_lock_held():
        return "the launcher cycle lock is held"
    procs = pf.read_processes()
    if procs.get("unreadable"):
        return "the process list is unreadable: cannot prove the tree is free"
    if procs.get("running"):
        return f"{len(procs['running'])} pipeline/test/audit process(es) running (first: {procs['running'][0]['cmd'][:80]})"
    return None


def git_changed(old: str, new: str) -> list:
    r = subprocess.run(["git", "diff", "--name-only", f"{old}..{new}"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]


def already_processed(state_path: Path, sha: str) -> bool:
    try:
        return sha in json.loads(state_path.read_text(encoding="utf-8")).get("processed", [])
    except (OSError, ValueError):
        return False


def mark_processed(state_path: Path, sha: str) -> None:
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    state["processed"] = (state.get("processed", []) + [sha])[-50:]
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=1), encoding="utf-8")


# ---------------------------------------------------------------------------------------------------------------------------------------------------
# The sequence
# ---------------------------------------------------------------------------------------------------------------------------------------------------

def sequence(python: str, stamp: str, run: Callable = run_cmd, log_dir: Optional[Path] = None) -> dict:
    """Steps 2-5. Returns the receipt dict (always), with 'exit' = the process exit code. `run(cmd, log_path=None)` -> (rc, output)."""
    if log_dir is None:
        sys.path.insert(0, str(ROOT))
        from shared.config import get_log_dir
        log_dir = get_log_dir("p1")                      # the registered P1 log directory (tests/test_log_paths.py)
    log_path = log_dir / f"cost_recompute_{stamp}.log"
    run_id = f"post_{stamp}_costs"
    rec: dict = {"stamp": stamp, "audit_run_id": run_id, "steps": {}}

    rc, out = run([python, "-X", "utf8", str(ROOT / "proyecto1" / "run_block.py"), "--nature-first", "--master-db", "--recompute-costs"], log_path=log_path)
    log = parse_recompute_log(out)
    rec["steps"]["recompute"] = {"rc": rc, **log, "log": str(log_path), "tail": tail(out, 12)}
    rec["exit"] = recompute_verdict(rc, log)
    if rec["exit"] != EXIT_OK:
        return rec

    rc, out = run([python, "-X", "utf8", str(ROOT / "scripts" / "audit" / "stat" / "run_statistical_audit.py"), "--domain", "costs", "--mode", "report", "--persist", "--run-id", run_id])
    rec["steps"]["audit"] = {"rc": rc, "tail": tail(out, 8)}
    if rc != 0 or f"run_id={run_id}" not in out:
        rec["exit"] = EXIT_AUDIT
        return rec

    rc, out = run([python, "-X", "utf8", str(ROOT / "scripts" / "audit" / "stat" / "audit_incident.py"), "--list"])
    incidents = open_cost_incidents(parse_incident_list(out)) if rc == 0 else []
    rcs: dict = {}
    for inc in incidents:
        rc, out = run([python, "-X", "utf8", str(ROOT / "scripts" / "audit" / "stat" / "audit_incident.py"), "--certify", str(inc["id"]), "--run", run_id, "--apply"])
        rcs[inc["id"]] = rc
        rec["steps"].setdefault("certify", {})[str(inc["id"])] = {"ap": inc["ap"], "rc": rc, "verdict": "CERTIFIED" if rc == 0 else "NOT CERTIFIABLE", "tail": tail(out, 6)}
    rec["exit"] = final_exit(rcs)
    return rec


def write_receipt(rec: dict, path: Optional[Path] = None) -> Path:
    path = path or (ROOT / "out" / "audit" / "cost_recompute_receipts.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, sort_keys=True, ensure_ascii=False) + "\n")
    return path


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="preflight and print the plan; run nothing")
    ap.add_argument("--if-changed", nargs=2, metavar=("OLD", "NEW"), help="run only when OLD..NEW touches the cost extraction code and NEW was not processed")
    args = ap.parse_args(argv)

    sha = None
    if args.if_changed:
        sys.path.insert(0, str(ROOT))
        import shared.config as cfg
        if not cfg.COST_RECOMPUTE_AUTO_ENABLED:
            print("[cost-recompute] COST_RECOMPUTE_AUTO_ENABLED is off: nothing to do")
            return EXIT_NOTHING
        old, sha = args.if_changed
        state = ROOT / "out" / "audit" / "cost_recompute_state.json"
        hit = changed_watch_files(git_changed(old, sha))
        if not hit or already_processed(state, sha):
            print(f"[cost-recompute] {'no cost extraction file changed' if not hit else 'commit already processed'} in {old[:8]}..{sha[:8]}: nothing to do")
            return EXIT_NOTHING
        print(f"[cost-recompute] cost extraction code changed in {old[:8]}..{sha[:8]}: {', '.join(hit)}")

    why = preflight()
    if why:
        print(f"[cost-recompute] REFUSED (RC {EXIT_PREFLIGHT}): {why}")
        return EXIT_PREFLIGHT
    if args.dry_run:
        print("[cost-recompute] DRY RUN: preflight passed; would run recompute -> costs audit (--persist) -> certify the OPEN cost incidents")
        return EXIT_OK

    lock = ROOT / "out" / "locks" / "cost_recompute.lock"
    if not acquire_lock(lock):
        print(f"[cost-recompute] BUSY (RC {EXIT_BUSY}): another instance holds {lock}")
        return EXIT_BUSY
    try:
        rec = sequence(sys.executable, datetime.now().strftime("%Y%m%d_%H%M%S"))
        rec["finished_at"] = datetime.now().isoformat(timespec="seconds")
        rec["commit"] = sha
        print(f"[cost-recompute] receipt: {write_receipt(rec)} | exit {rec['exit']}")
        if sha and rec["exit"] in (EXIT_OK, EXIT_NOT_CERTIFIED):
            mark_processed(ROOT / "out" / "audit" / "cost_recompute_state.json", sha)
        return rec["exit"]
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
