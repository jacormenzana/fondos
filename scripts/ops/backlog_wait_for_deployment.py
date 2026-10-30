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


def run(wait_fn, verify_fn, ingest_fn, *, attempts: int, retry_s: float, sleep=time.sleep, log=print) -> int:
    """wait -> verify (retried) -> ingest only after a passed verification."""
    if not wait_fn():
        log("timed out waiting for the deployment to become visible")
        return RC_TIMEOUT
    log("the DDL is visible; verifying")
    for attempt in range(1, attempts + 1):
        rc = verify_fn()
        log(f"verification attempt {attempt}/{attempts}: exit code {rc}")
        if rc == 0:
            break
        if attempt < attempts:
            sleep(retry_s)
    else:
        log("the verification never passed: nothing was ingested")
        return RC_FAIL
    rc = ingest_fn()
    log(f"ingestion exit code {rc}")
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

    stamped(f"waiting for {bc._target_of(bc._dsn() or '')} every {args.interval:g} s (read-only), for at most {args.timeout_hours:g} h")
    return run(lambda: wait(one_probe, interval=args.interval, timeout_s=args.timeout_hours * 3600, log=stamped),
               lambda: vd.main(["--baseline-dir", args.baseline_dir, "--record-dir", args.record_dir]),
               lambda: ing.main(["--apply", "--file", args.pending_file, "--record-dir", args.record_dir]),
               attempts=args.attempts, retry_s=args.retry, log=stamped)


if __name__ == "__main__":
    sys.exit(main())
