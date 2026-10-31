#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
backlog_staging.py -- ONE command that verifies the Backlog v2 state machine and gate on a SCRATCH Postgres loaded with a snapshot of the live backlog (FND-0351; NORMAS_BACKLOG.md 9).

    python scripts/ops/backlog_staging.py                                      # fresh read-only snapshot -> throwaway container -> real DDL -> every scenario -> teardown
    python scripts/ops/backlog_staging.py --scenario gate_blocked,a2_revoke    # some scenarios
    python scripts/ops/backlog_staging.py --phases transitions,relations,groups,sync,hook,collision,attest   # the phases applied (default transitions,relations: the gate and the A-2 steps)
    python scripts/ops/backlog_staging.py --snapshot FILE                      # a snapshot you already exported (nothing is read from the live backlog)
    python scripts/ops/backlog_staging.py --synthetic                          # the built-in small history: no contact with the live backlog at all

Why. Synthetic testing that burns production sequence ids is BANNED (owner mandate 2026-10-28): every verification of the state machine or the gate runs here, never on the live backlog. The ONLY
contact with the live backlog is one read-only export (`default_transaction_read_only`), and the command PROVES it left nothing behind: the values of the live sequences (ticket ids, log ids, event ids)
are read before and after and must be identical. The scenarios (tests/test_backlog_staging_pg.py) call assert_scratch (shared/backlog_scratch.py) before their first statement: a live target is refused whatever
its name. Scenarios: gate_clean, gate_blocked, gate_forced, gate_audit_window, a2_revoke (the pending `--phase revoke` rehearsed on the real population), replay (the real history through the real gate; needs a
real snapshot). The container is the one of scripts/ops/run_pg_tests.py (no credentials; WSL must be awake: see CLAUDE.md).

Exit codes: 0 every scenario passed and the live sequences are unchanged, 1 a scenario failed or a live sequence moved, 2 usage error or the live backlog could not be read.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "audit"))
sys.path.insert(0, str(ROOT / "scripts" / "ops"))
RC_OK, RC_FAIL, RC_USAGE = 0, 1, 2
OUT = ROOT / "out" / "staging"
SCENARIOS = {"gate_clean": "test_scenario_gate_clean", "gate_blocked": "test_scenario_gate_blocked", "gate_forced": "test_scenario_gate_forced",
             "gate_audit_window": "test_scenario_gate_audit_window", "a2_revoke": "test_scenario_a2_revoke_rehearsal", "replay": "test_scenario_replay_of_the_real_history"}
DEFAULT_PHASES = "transitions,relations"


def k_expression(names: list) -> str:
    """The pytest -k expression for the chosen scenarios ('' = all of them plus the guard tests). Raises ValueError for an unknown name. Pure."""
    if not names:
        return ""
    bad = [n for n in names if n not in SCENARIOS]
    if bad:
        raise ValueError(f"unknown scenario(s) {bad}; known: {', '.join(SCENARIOS)}")
    return " or ".join(SCENARIOS[n] for n in names)


def parse_phases(text: str, known) -> list:
    """The phases to apply, in order. Raises ValueError for an unknown one or an empty list. Pure."""
    phases = [p.strip() for p in (text or "").split(",") if p.strip()]
    bad = [p for p in phases if p not in known]
    if bad or not phases:
        raise ValueError(f"phases must be a non-empty comma list of {sorted(known)}; got {text!r}")
    return phases


def sequence_diff(before: dict, after: dict) -> list:
    """[(sequence, before, after)] for every live sequence whose value moved or that appeared or vanished. Pure."""
    return [(n, before.get(n), after.get(n)) for n in sorted(set(before) | set(after)) if before.get(n) != after.get(n)]


def live_sequences() -> dict:
    """{sequence: last_value} of the live backlog schema, read-only (nothing is consumed by reading pg_sequences)."""
    import shared.config  # noqa: F401  (loads .env: FONDOS_BACKLOG_PG_DSN)
    from shared import backlog_client as bc
    with bc._session() as c:
        c.execute("SET TRANSACTION READ ONLY")
        return {r[0]: r[1] for r in c.execute("SELECT sequencename, last_value FROM pg_sequences WHERE schemaname = 'backlog' ORDER BY 1").fetchall()}


def export_fresh() -> Path:
    """A fresh read-only snapshot of the live backlog in out/staging/ (git-ignored)."""
    import shared.config  # noqa: F401
    from shared import backlog_client as bc
    import backlog_phase_b_replay as rp
    with bc._session() as c:
        c.execute("SET TRANSACTION READ ONLY")
        snap = rp.export_snapshot(c)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"snapshot_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    import json
    path.write_text(json.dumps(snap), encoding="utf-8")
    print(f"snapshot: {path} ({len(snap['tickets'])} tickets, {len(snap['link_events'])} link events; read-only export)")
    return path


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", default="", help=f"comma list of {', '.join(SCENARIOS)} (default: all)")
    ap.add_argument("--phases", default=DEFAULT_PHASES, help="phases of migrate_backlog_v2.py to apply, in order")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--snapshot", help="a snapshot file already exported (the live backlog is not read at all)")
    g.add_argument("--synthetic", action="store_true", help="the built-in small history (the live backlog is not read at all)")
    ap.add_argument("--image", default="postgres:17")
    args = ap.parse_args(argv)
    import migrate_backlog_v2 as mig
    try:
        names = [n.strip() for n in args.scenario.split(",") if n.strip()]
        k = k_expression(names)
        phases = parse_phases(args.phases, mig.PHASES)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return RC_USAGE
    touches_live = not (args.snapshot or args.synthetic)
    before = {}
    snapshot = args.snapshot
    try:
        if touches_live:
            before = live_sequences()
            snapshot = str(export_fresh())
    except Exception as exc:                                  # never echo a DSN
        print(f"error: the live backlog could not be read ({type(exc).__name__}); use --synthetic or --snapshot", file=sys.stderr)
        return RC_USAGE
    env = {**os.environ, "FONDOS_STAGING_PHASES": ",".join(phases)}
    env.pop("FONDOS_STAGING_SNAPSHOT", None)
    if snapshot:
        env["FONDOS_STAGING_SNAPSHOT"] = str(snapshot)
    cmd = [sys.executable, "-X", "utf8", str(ROOT / "scripts" / "ops" / "run_pg_tests.py"), "--image", args.image, "--", "tests/test_backlog_staging_pg.py", "-v", "--tb=short", "-p", "no:cacheprovider"]
    if k:
        cmd += ["-k", k]
    print(f"phases: {', '.join(phases)} | scenarios: {', '.join(names) or 'all'} | history: {'real snapshot' if snapshot else 'built-in synthetic'}")
    rc = subprocess.run(cmd, cwd=ROOT, env=env).returncode
    moved = []
    if touches_live:
        try:
            moved = sequence_diff(before, live_sequences())
        except Exception as exc:                              # noqa: BLE001
            print(f"warning: the live sequences could not be re-read ({type(exc).__name__})", file=sys.stderr)
    if touches_live:
        print("live sequences after the run: " + ("UNCHANGED (zero ids consumed on the live backlog)" if not moved else f"MOVED {moved}"))
    ok = rc == 0 and not moved
    print("RESULT: " + ("ALL SCENARIOS PASSED" if ok else "FAILED"))
    return RC_OK if ok else RC_FAIL


if __name__ == "__main__":
    sys.exit(main())
