#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
backlog_wait_for_deployment.py -- wait, read-only, until the Backlog v2 DDL is visible on production; then verify it and ingest the pending notes (FND-0354, owner order 2026-10-30).

    python scripts/ops/backlog_wait_for_deployment.py [--interval 15] [--timeout-hours 12] [--baseline-dir D] [--record-dir deployments]

Every --interval seconds (default 15) it reads production (one connection, read-only queries) and asks four questions at once: does the column gate_switch.override exist (step relations),
does the table actor_login_event exist, does the routine sp_append_log exist, do the three IAM roles exist (step iam). Waiting for the column alone would fire between the two steps, when
the verification would fail and the notes could not be appended: so ALL FOUR must hold, on two consecutive polls (a half-applied state or a flapping read does not count).
Then, in order, it runs backlog_verify_deployment.py (zero-input) and, ONLY if every criterion passes, backlog_ingest_pending_logs.py --apply. A failed verification is retried every
--retry seconds (default 60, up to --attempts, default 30: the owner may still be binding logins) and never ingests. Nothing is written until the verification has passed. A connection
error is a not-ready poll, never a crash. Exit 0 = deployed, verified and ingested; 1 = verification never passed or the ingestion failed; 2 = timed out waiting, or usage.
PERSISTENCE (owner order 2026-10-30): the records go straight to the repository's own deployments/ directory (--repo, --record-dir; never a temporary directory) and are COMMITTED on
--branch (git add + git commit of exactly the new files, hooks included) right after each verification attempt, BEFORE anything is ingested; if that commit fails, nothing is ingested.
After the ingestion its record is committed too, together with the removal of PENDING_FND_LOGS.md when the ingestion is complete. A push of the branch is then attempted (a failure is logged,
the commit stands). Run it from an immutable copy of the code (a git archive) with --repo pointing at the working tree that is on --branch.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RC_OK, RC_FAIL, RC_TIMEOUT = 0, 1, 2
IAM_ROLES = ("backlog_reader", "backlog_logger", "backlog_incident")


def probe(conn) -> dict:
    """The four facts, read from `conn` (a backlog_client session). Reads only."""
    return {
        "override_column": bool(conn.execute("SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'backlog' AND table_name = 'gate_switch' AND column_name = 'override')").fetchone()[0]),
        "actor_login_event": bool(conn.execute("SELECT to_regclass('backlog.actor_login_event') IS NOT NULL").fetchone()[0]),
        "sp_append_log": bool(conn.execute("SELECT to_regprocedure('backlog.sp_append_log(text,text,text)') IS NOT NULL").fetchone()[0]),
        "iam_roles": conn.execute("SELECT count(*) FROM pg_roles WHERE rolname = ANY(%s)", (list(IAM_ROLES),)).fetchone()[0] == len(IAM_ROLES),
    }


REQUIRED = ("override_column", "actor_login_event", "sp_append_log", "iam_roles")


def ready(flags: dict) -> bool:
    """True only when every one of the four facts is exactly True (a probe error, an unexpected key or a truthy non-boolean is NOT ready)."""
    return all(flags.get(k) is True for k in REQUIRED)


def wait(probe_fn, *, interval: float, timeout_s: float, stable: int = 2, sleep=time.sleep, clock=time.monotonic, log=print) -> bool:
    """Poll until `ready(probe_fn())` holds on `stable` consecutive polls; False on timeout. A probe that raises counts as not ready."""
    start, streak, last, polls = clock(), 0, None, 0
    while True:
        polls += 1
        try:
            flags = probe_fn()
        except Exception as exc:  # noqa: BLE001 -- the database may be restarting: not ready, keep going
            flags = {"error": type(exc).__name__}
        streak = streak + 1 if ready(flags) else 0
        if flags != last or polls % 4 == 0:
            log(f"poll {polls}: {flags} (stable {streak}/{stable})")
            last = flags
        if streak >= stable:
            return True
        if clock() - start >= timeout_s:
            return False
        sleep(interval)


class RecordError(RuntimeError):
    """A deployment record could not be made persistent (committed): the next phase must not start."""


def listing(directory: Path) -> set:
    return {p.name for p in Path(directory).glob("*")} if Path(directory).is_dir() else set()


def persist(repo: Path, record_dir: Path, before: set, message: str, *, branch: str, remove=(), push: bool = True, run_git=None, log=print) -> str:
    """Make the records written since `before` PERSISTENT, immediately: `git add` and `git commit` of exactly those files (a pathspec commit: nothing else in the index or the tree is
    included) on `branch`, in the repository's own working tree (never a temporary directory). `remove` lists tracked files deleted in the same commit (the pending-notes outbox after a
    complete ingestion). Refuses unless HEAD is `branch` and a record exists; the repository's hooks run (never --no-verify). A push is attempted afterwards and its failure is only logged:
    the commit is what makes the record persistent. Returns the commit id. Raises RecordError."""
    import subprocess
    git = run_git or (lambda *a, inp=None: subprocess.run(["git", "-C", str(repo), *a], input=inp, capture_output=True, text=True, encoding="utf-8"))
    head = git("symbolic-ref", "--short", "HEAD")
    if head.returncode or head.stdout.strip() != branch:
        raise RecordError(f"the working tree is on {head.stdout.strip() or '(detached)'}, not on {branch}: records are committed there only")
    rel_dir = Path(record_dir).resolve().relative_to(Path(repo).resolve()).as_posix()
    new = sorted(f"{rel_dir}/{name}" for name in listing(record_dir) - before)
    if not new:
        raise RecordError(f"no record was produced in {rel_dir}")
    paths = new + [str(Path(r).as_posix()) for r in remove]
    for step in (("add", "--", *new), *((("rm", "-q", "--", *remove),) if remove else ())):
        res = git(*step)
        if res.returncode:
            raise RecordError(f"git {step[0]} failed: {res.stderr.strip()[:300]}")
    res = git("commit", "-F", "-", "--", *paths, inp=message)
    if res.returncode:
        raise RecordError(f"git commit failed (hooks included): {(res.stderr.strip() or res.stdout.strip())[:400]}")
    commit = git("rev-parse", "--short", "HEAD").stdout.strip()
    log(f"record committed on {branch}: {commit} ({', '.join(paths)})")
    if push:
        pushed = git("push", "origin", branch)
        log("record pushed" if pushed.returncode == 0 else f"push failed (the commit stands): {pushed.stderr.strip()[:200]}")
    return commit


def run(wait_fn, verify_fn, ingest_fn, *, attempts: int, retry_s: float, persist_verify=None, persist_ingest=None, sleep=time.sleep, log=print) -> int:
    """wait -> verify (retried; each attempt's record is committed before anything else) -> ingest only after a passed verification AND a committed record; the ingestion record is committed
    too (and the outbox removed only after a complete ingestion). A failure to persist stops the sequence before the next write."""
    persist_verify = persist_verify or (lambda attempt, rc: None)
    persist_ingest = persist_ingest or (lambda rc: None)
    if not wait_fn():
        log("timed out waiting for the deployment to become visible")
        return RC_TIMEOUT
    log("the DDL is visible; verifying")
    for attempt in range(1, attempts + 1):
        rc = verify_fn()
        log(f"verification attempt {attempt}/{attempts}: exit code {rc}")
        try:
            persist_verify(attempt, rc)
        except RecordError as exc:
            log(f"STOP: the verification record could not be committed ({exc}); nothing was ingested")
            return RC_FAIL
        if rc == 0:
            break
        if attempt < attempts:
            sleep(retry_s)
    else:
        log("the verification never passed: nothing was ingested")
        return RC_FAIL
    rc = ingest_fn()
    log(f"ingestion exit code {rc}")
    try:
        persist_ingest(rc)
    except RecordError as exc:
        log(f"the ingestion record could not be committed ({exc}); it is in the working tree, uncommitted")
        return RC_FAIL
    return RC_OK if rc == 0 else RC_FAIL


def main(argv: "list | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--interval", type=float, default=15.0)
    ap.add_argument("--timeout-hours", type=float, default=12.0)
    ap.add_argument("--retry", type=float, default=60.0)
    ap.add_argument("--attempts", type=int, default=30)
    ap.add_argument("--baseline-dir", default=str(ROOT / "out" / "audit"))
    ap.add_argument("--record-dir", default=str(ROOT / "deployments"))
    ap.add_argument("--pending-file", default=str(ROOT / "PENDING_FND_LOGS.md"))
    ap.add_argument("--repo", default=str(ROOT), help="the repository whose working tree receives the records (default: the one this script is in)")
    ap.add_argument("--branch", default="chore/deployment-records-final", help="the branch the working tree must be on; the records are committed there")
    ap.add_argument("--no-push", action="store_true", help="commit only (default: also try to push the branch; a failed push never stops the run)")
    args = ap.parse_args(argv)

    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "scripts" / "audit"))
    sys.path.insert(0, str(ROOT / "scripts" / "ops"))
    import shared.config  # noqa: F401  (loads the repo .env: FONDOS_BACKLOG_PG_DSN)
    from shared import backlog_client as bc
    import backlog_ingest_pending_logs as ing
    import backlog_verify_deployment as vd

    def stamped(msg: str) -> None:
        print(f"{datetime.now(timezone.utc).strftime('%H:%M:%SZ')} {msg}", flush=True)

    def one_probe() -> dict:
        with bc._session() as conn:
            return probe(conn)

    repo, record_dir = Path(args.repo), Path(args.record_dir)
    state = {"before": listing(record_dir)}

    def verify() -> int:
        state["before"] = listing(record_dir)
        return vd.main(["--baseline-dir", args.baseline_dir, "--record-dir", str(record_dir)])

    def persist_verify(attempt: int, rc: int) -> None:
        persist(repo, record_dir, state["before"], f"docs(ops): FND-0354 - deployment verification record, attempt {attempt} ({'VERIFIED' if rc == 0 else 'NOT VERIFIED'})\n\n"
                f"Written by backlog_wait_for_deployment.py right after backlog_verify_deployment.py (exit code {rc}); committed before any note is ingested.\n\n"
                "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>\n", branch=args.branch, push=not args.no_push, log=stamped)

    def ingest() -> int:
        state["before"] = listing(record_dir)
        return ing.main(["--apply", "--file", args.pending_file, "--record-dir", str(record_dir)])

    def persist_ingest(rc: int) -> None:
        pending = Path(args.pending_file)
        remove = [pending.resolve().relative_to(repo.resolve()).as_posix()] if rc == 0 and pending.exists() else []
        persist(repo, record_dir, state["before"], "docs(ops): FND-0354 - ingestion record of PENDING_FND_LOGS.md" + (" and removal of the outbox" if remove else " (INCOMPLETE: the outbox stays)") +
                f"\n\nbacklog_ingest_pending_logs.py exit code {rc}. The record lists every note appended with its log id" + ("; the outbox is removed in the same change, as its header says." if remove else ".") +
                "\n\nCo-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>\n", branch=args.branch, remove=remove, push=not args.no_push, log=stamped)

    stamped(f"waiting for {bc._target_of(bc._dsn() or '')} every {args.interval:g} s (read-only), for at most {args.timeout_hours:g} h; records are committed on {args.branch} in {repo}")
    return run(lambda: wait(one_probe, interval=args.interval, timeout_s=args.timeout_hours * 3600, log=stamped), verify, ingest,
               attempts=args.attempts, retry_s=args.retry, persist_verify=persist_verify, persist_ingest=persist_ingest, log=stamped)


if __name__ == "__main__":
    sys.exit(main())
