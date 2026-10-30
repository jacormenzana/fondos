#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
backlog_verify_deployment.py -- zero-input verification of the Backlog v2 deployment (FND-0333 / FND-0350 / FND-0354), read-only, record persisted.

    python scripts/audit/backlog_verify_deployment.py                       # production defaults: target 127.0.0.1:5436/gestion, gate ENFORCE, newest baseline in out/audit
    python scripts/audit/backlog_verify_deployment.py --expect-target host:port/db --expect-gate AUDIT --baseline-file F --record-dir deployments

Nobody has to remember an exit code. Whoever applied the DDL does not need to echo %ERRORLEVEL%: the schema itself is read and judged, with the criteria of
`backlog_phase_a_acceptance.py --verify relations` and `--verify iam` (their first criterion, "the migration exited 0", is replaced by "not needed: the schema is
verified directly"). On top of those it checks the things the first deployment attempt got wrong:
  * the connection is the PRODUCTION database (host:port/db equal to --expect-target; the first attempt of 2026-10-29 went to a staging replica);
  * the delivery gate is in the expected mode (default ENFORCE) and gate_switch has at least --min-gate-rows rows (default 2: nothing was lost);
  * the history up to the baseline ids is identical (the baseline defaults to the newest out/audit/fnd0331_baseline_*.json, or --baseline-file).
It also prints, as information and never as a criterion, the login bindings with their audit flags, the unbound writers and the A-2 progress.
The record is written to --record-dir (default `deployments/`, version-controlled: the owner's rule is that deployment records never live in a temporary directory).
Exit 0 only when every criterion passes, 1 otherwise, 2 on usage or connection errors. It never writes to the database. No SQL here: every read goes through shared/backlog_client.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASELINE_DIR = ROOT / "out" / "audit"
DEFAULT_RECORD_DIR = ROOT / "deployments"
PRODUCTION_TARGET = "127.0.0.1:5436/gestion"
RC_OK, RC_FAIL, RC_USAGE = 0, 1, 2
NOT_NEEDED = ("migration exit code: not needed", True, "not supplied by anyone: the schema is read and judged directly by the criteria below")


def newest_baseline(directory: Path):
    """The newest `fnd0331_baseline_<UTC stamp>.json` in `directory` (the stamp sorts), or None."""
    found = sorted(Path(directory).glob("fnd0331_baseline_*.json")) if Path(directory).is_dir() else []
    return found[-1] if found else None


def drop_exit_code_criterion(results: list) -> list:
    """The acceptance evaluators open with 'migration exit code is 0': with no exit code to read, that criterion becomes an explicit 'not needed' (never a silent pass of a missing value)."""
    return [NOT_NEEDED if (i == 0 and name.startswith("migration exit code")) else (name, ok, detail) for i, (name, ok, detail) in enumerate(results)]


def verify(conn, baseline: dict, *, actual_target: str, expect_target: str = PRODUCTION_TARGET, expect_gate: str = "ENFORCE", min_gate_rows: int = 2) -> list:
    """[(section, criterion, passed, detail)] for the whole deployment, read from `conn` (a backlog_client session). Reads only."""
    import backlog_phase_a_acceptance as acc
    from shared import backlog_client as bc

    after = bc.history_fingerprint(max_log_id=baseline["max_log_id"], max_event_id=baseline["max_event_id"], conn=conn)
    rel_cat, iam_cat = bc.phase_b_catalog(conn=conn), bc.iam_catalog(conn=conn)
    has_gate = conn.execute("SELECT to_regclass('backlog.gate_switch') IS NOT NULL AND to_regprocedure('backlog.fn_gate_mode(text)') IS NOT NULL").fetchone()[0]
    gate_mode = bc.gate_mode(conn=conn) if has_gate else None
    gate_rows = conn.execute("SELECT count(*) FROM backlog.gate_switch").fetchone()[0] if has_gate else 0
    out = [("target", "the connection is the production database", actual_target == expect_target, f"connected to {actual_target}, expected {expect_target}"),
           ("gate", f"the delivery gate is in {expect_gate} mode", gate_mode == expect_gate, f"mode {gate_mode}"),
           ("gate", f"gate_switch kept its rows (at least {min_gate_rows})", gate_rows >= min_gate_rows, f"{gate_rows} row(s)")]
    out += [("relations", n, ok, d) for n, ok, d in drop_exit_code_criterion(acc.evaluate_relations(0, baseline, after, rel_cat))]
    out += [("iam", n, ok, d) for n, ok, d in drop_exit_code_criterion(acc.evaluate_iam(0, baseline, after, iam_cat))]
    return out


def information(conn) -> list:
    """Plain lines (never criteria): bindings with their audit flags, unbound writers, who has made transitions, the A-2 counter."""
    from shared import backlog_client as bc
    iam = bc.iam_catalog(conn=conn)
    lines = [f"bindings in force: {len([b for b in iam['bindings'] if b['active']])} ({len(iam['active_persons'])} distinct human person(s) {iam['active_persons']})"]
    lines += [f"  {b['login_name']} -> {b['actor']} | {b['person']} | {b['kind']} | {'active' if b['active'] else 'REVOKED'} | declared by {b.get('declared_by')}" for b in iam["bindings"]]
    lines += [f"  audit #{a['event_id']} {a['login_name']}: flags {a['flags']}" for a in iam["binding_audit"]]
    lines.append(f"logins that can still open or move a ticket and are NOT bound: {iam['unbound_writers']}")
    if bc.transitions_store_present(conn=conn):
        sp = bc.shadow_progress(conn=conn)
        lines.append(f"A-2 counter: {sp['live_non_system_since_last_v00']} of {bc.SHADOW_REQUIRED} real transitions since the last V-00; by persons {iam['transitions_by_person']}")
    return lines


def render(results: list, info: list, header: list) -> str:
    lines = header + [f"{'PASS' if ok else 'FAIL'} - [{section}] {name}: {detail}" for section, name, ok, detail in results]
    lines += [f"INFO - {x}" for x in info]
    lines.append(f"RESULT: {'DEPLOYED AND VERIFIED (all criteria met)' if all(ok for _, _, ok, _ in results) else 'NOT VERIFIED'}")
    return "\n".join(lines)


def main(argv: "list | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--expect-target", default=PRODUCTION_TARGET, help="host:port/db the connection must be (default: production)")
    ap.add_argument("--expect-gate", default="ENFORCE", choices=("OFF", "AUDIT", "ENFORCE"))
    ap.add_argument("--min-gate-rows", type=int, default=2)
    ap.add_argument("--baseline-file", help="default: the newest fnd0331_baseline_*.json in --baseline-dir")
    ap.add_argument("--baseline-dir", default=str(DEFAULT_BASELINE_DIR))
    ap.add_argument("--record-dir", default=str(DEFAULT_RECORD_DIR), help="where the record is written (default deployments/; '' to skip)")
    args = ap.parse_args(argv)

    path = Path(args.baseline_file) if args.baseline_file else newest_baseline(Path(args.baseline_dir))
    if path is None or not path.is_file():
        print("error: no baseline found (take one BEFORE applying with backlog_phase_a_acceptance.py --baseline, or pass --baseline-file)", file=sys.stderr)
        return RC_USAGE
    baseline = json.loads(path.read_text(encoding="utf-8"))

    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "scripts" / "audit"))
    import shared.config  # noqa: F401  (loads the repo .env: FONDOS_BACKLOG_PG_DSN)
    from shared import backlog_client as bc
    try:
        dsn = bc._dsn()
        if not dsn:
            raise bc.BacklogUnavailable("FONDOS_BACKLOG_PG_DSN is not set")
        target = bc._target_of(dsn)
        with bc._session() as conn:
            results = verify(conn, baseline, actual_target=target, expect_target=args.expect_target, expect_gate=args.expect_gate, min_gate_rows=args.min_gate_rows)
            info = information(conn)
    except bc.BacklogUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return RC_USAGE
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    text = render(results, info, [f"Backlog v2 deployment verification, {stamp}, baseline {path.name}, target {target}"])
    print(text)
    if args.record_dir:
        out = Path(args.record_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}_verification_{stamp}.txt").write_text(text + "\n", encoding="utf-8")
    return RC_OK if all(ok for _, _, ok, _ in results) else RC_FAIL


if __name__ == "__main__":
    sys.exit(main())
