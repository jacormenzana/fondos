#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
cost_gate_replay.py -- what the cost-domain promotion gate WOULD have done on the past (FND-0265, decision D8). Read-only.

    python scripts/audit/cost_gate_replay.py                 # every persisted full cost_attributes run x every scenario
    python scripts/audit/cost_gate_replay.py --last 5        # only the 5 newest runs
    python scripts/audit/cost_gate_replay.py --scenario ID   # one scenario

Decision D8: before COST_DOMAIN_GATE_ENABLED may block a promotion, replay the persisted cost audits against the scenarios they would
have judged, then run one shadow cycle. For each full (non-sample) cost_attributes run and each scenario, this lists the open hard
defects (gating.is_blocking) ON the scenario's funds -- exactly what [GATE:COST] would have refused -- through the same loader the gate
uses (gating.scope_for_run). Accepted residuals are TODAY's (the acceptance history before FND-0263 does not exist), so a past run may
show a defect that was accepted at the time; the report says so.

Exit codes: 0 always (a report, never a gate), 2 usage or connection error.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

DOMAIN = "cost_attributes"


def replay_rows(runs: list, scenarios: dict, scope_of) -> list:
    """[(run_id, run_ts, scenario_id, status, n_funds, [(rule_id, n_isins)])] for every run x scenario. `scope_of(run_id, run_ts,
    isins)` -> AuditScope. Pure over its inputs."""
    out = []
    for run_id, run_ts in runs:
        for sid, (status, isins) in scenarios.items():
            scope = scope_of(run_id, run_ts, isins)
            out.append((run_id, run_ts, sid, status, len(isins), [(d.rule_id, len(d.isins)) for d in scope.on_scenario]))
    return out


def main(argv: "list | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--last", type=int, default=None, help="only the N newest runs")
    ap.add_argument("--scenario", default=None, help="only this scenario id")
    ap.add_argument("--verbose", action="store_true", help="one line per refused run x scenario with every rule")
    args = ap.parse_args(argv)

    import shared.config  # noqa: F401  (loads the repo .env)
    from shared.db import get_connection
    from shared.statistical_audit import accepted_residuals, gating

    try:
        conn = get_connection()
    except Exception as exc:                                     # noqa: BLE001
        print(f"error: connection failed ({type(exc).__name__})", file=sys.stderr)
        return 2
    try:
        runs = [tuple(r) for r in conn.execute(
            "SELECT run_id, MAX(detected_at) FROM audit_finding WHERE domain = %s AND right(run_id, 7) <> '_sample' "
            "GROUP BY run_id ORDER BY MAX(detected_at) DESC", (DOMAIN,)).fetchall()]
        if args.last:
            runs = runs[:args.last]
        rows = conn.execute("SELECT s.scenario_id, s.status, w.isin FROM portfolio_scenarios s "
                            "JOIN portfolio_weights w ON w.scenario_id = s.scenario_id ORDER BY 1").fetchall()
        scenarios: dict = {}
        for sid, status, isin in rows:
            if args.scenario and sid != args.scenario:
                continue
            scenarios.setdefault(sid, (status, set()))[1].add(isin)
        try:
            accepted = accepted_residuals.load_accepted(conn, DOMAIN)
        except Exception:                                        # noqa: BLE001 -- no residual table = nothing accepted
            conn.rollback()
            accepted = {}
        out = replay_rows(runs, scenarios, lambda rid, ts, isins: gating.scope_for_run(conn, DOMAIN, rid, ts, isins, accepted))
    finally:
        conn.rollback()
        conn.close()

    print(f"cost-gate replay (D8): {len(runs)} full cost run(s) x {len(scenarios)} scenario(s); residuals = today's acceptances")
    for line in summary_lines(out):
        print(line)
    if args.verbose:
        for run_id, run_ts, sid, status, n, defects in out:
            if defects:
                print(f"  WOULD BLOCK  {run_id} ({run_ts:%Y-%m-%d}) x {sid} [{status}, {n} funds]: "
                      + ", ".join(f"{r} ({k or 'sin ISIN'})" for r, k in defects))
    would_block = sum(1 for r in out if r[5])
    print(f"{would_block} of {len(out)} run x scenario pair(s) would have been refused by [GATE:COST]; the rest pass.")
    return 0


def summary_lines(out: list) -> list:
    """One line per run: how many scenarios it would have refused, and which rules (distinct; 'no ISIN' = not attributable, assumed to
    touch the scenario). Pure."""
    by_run: dict = {}
    for run_id, run_ts, _sid, _status, _n, defects in out:
        e = by_run.setdefault(run_id, {"ts": run_ts, "pairs": 0, "blocked": 0, "rules": set(), "unplaced": set()})
        e["pairs"] += 1
        e["blocked"] += bool(defects)
        for rule_id, k in defects:
            (e["rules"] if k else e["unplaced"]).add(rule_id)
    return [f"  {rid} ({e['ts']:%Y-%m-%d}): {e['blocked']}/{e['pairs']} scenario(s) refused; {len(e['rules'])} rule(s) on scenario funds, "
            f"{len(e['unplaced'])} rule(s) without ISIN detail" for rid, e in by_run.items()]


if __name__ == "__main__":
    sys.exit(main())
