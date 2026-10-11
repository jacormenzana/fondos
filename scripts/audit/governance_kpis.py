#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
governance_kpis.py -- read-only KPIs of the dual-governance model (FND-0251, roadmap item E).

    python scripts/audit/governance_kpis.py [--runs 6] [--json]

Everything comes from tables that already exist (no DDL, nothing is written):
  1. promotions with / without a data override      ingestion_log  step P3_SCENARIO_PROMOTED       ("[OVERRIDE:" in the message)
  2. refusals by gate code                          ingestion_log  step P3_SCENARIO_PROMOTION_BLOCKED  ("[GATE:<X>]" codes)
  3. residual health                                control.audit_accepted_finding (active / expiring / lapsed, per rule and AP)
  4. recurrence                                     hard findings per rule across the last N persisted p2_metrics runs
  5. post-promotion defects                         hard findings on the PRODUCTION scenario's funds in runs persisted AFTER promoted_at (target 0)
Counts, not rates: promotions are monthly, so a rate over a handful of events says nothing. Early output is expected to be nearly empty
(a dry run writes no row); it earns its keep over cycles. The decision functions are pure and tested on synthetic rows.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

AUDIT_DOMAIN = "p2_metrics"
STEP_PROMOTED, STEP_BLOCKED = "P3_SCENARIO_PROMOTED", "P3_SCENARIO_PROMOTION_BLOCKED"
_GATE_RE = re.compile(r"\[GATE:([A-Z_]+)\]")
_OVERRIDE_RE = re.compile(r"\[OVERRIDE:([A-Z_]+)\]")


# ---------------- pure decision functions ----------------

def promotions_kpi(rows) -> dict:
    """rows: (step, message). {total, with_override, without_override, overrides: {gate: n}}."""
    promoted = [m or "" for step, m in rows if step == STEP_PROMOTED]
    with_ov = [m for m in promoted if _OVERRIDE_RE.search(m)]
    gates = Counter(g for m in with_ov for g in _OVERRIDE_RE.findall(m))
    return {"total": len(promoted), "with_override": len(with_ov), "without_override": len(promoted) - len(with_ov),
            "overrides": dict(sorted(gates.items()))}


def refusals_kpi(rows) -> dict:
    """rows: (step, message). Refusals by gate code; one refusal can carry several codes, so the per-gate counts need not add up to `total`."""
    refused = [m or "" for step, m in rows if step == STEP_BLOCKED]
    gates = Counter(g for m in refused for g in set(_GATE_RE.findall(m)))
    return {"total": len(refused), "by_gate": dict(sorted(gates.items(), key=lambda kv: (-kv[1], kv[0])))}


def residuals_kpi(rows, today: date, warn_days: int) -> dict:
    """rows: (rule_id, ap_id, isin, expires_at). Active = expires_at >= today; expiring = active and within warn_days; lapsed = expires_at < today
    (rows kept in the table past their expiry)."""
    active = [r for r in rows if r[3] >= today]
    expiring = [r for r in active if (r[3] - today).days <= warn_days]
    lapsed = [r for r in rows if r[3] < today]
    by_key = Counter((r[0], r[1]) for r in active)
    return {"active_isins": len(active), "expiring_isins": len(expiring), "lapsed_isins": len(lapsed),
            "active_by_rule_ap": [{"rule": k[0], "ap": k[1], "isins": n} for k, n in sorted(by_key.items())]}


def recurrence_kpi(findings_by_run: dict, n_runs: int) -> list:
    """findings_by_run: {run_id: {rule_id: count}} for the last runs. For each rule: in how many of the runs it fired, and the latest count."""
    runs = list(findings_by_run)
    rules = sorted({r for rr in findings_by_run.values() for r in rr})
    out = []
    for rule in rules:
        hit = [run for run in runs if findings_by_run[run].get(rule)]
        out.append({"rule": rule, "runs_with_finding": len(hit), "runs_considered": len(runs),
                    "latest_count": findings_by_run[runs[0]].get(rule, 0) if runs else 0})
    return sorted(out, key=lambda d: (-d["runs_with_finding"], d["rule"]))


def post_promotion_kpi(scenario_id, promoted_at, rows) -> dict:
    """rows: (run_id, rule_id, isin) hard findings on the PRODUCTION funds persisted after promoted_at. Target: zero."""
    return {"scenario": scenario_id, "promoted_at": str(promoted_at) if promoted_at else None, "hard_findings": len(rows),
            "funds": sorted({r[2] for r in rows}), "rules": sorted({r[1] for r in rows})}


# ---------------- loaders (read-only) ----------------

def collect(conn, n_runs: int = 6, today: date | None = None) -> dict:
    from shared.config import AUDIT_RESIDUAL_EXPIRY_WARN_DAYS
    today = today or date.today()
    steps = [(r[0], r[1]) for r in conn.execute(
        "SELECT step, message FROM ingestion_log WHERE step IN ('P3_SCENARIO_PROMOTED', 'P3_SCENARIO_PROMOTION_BLOCKED') ORDER BY created_at").fetchall()]
    out = {"as_of": str(today), "promotions": promotions_kpi(steps), "refusals": refusals_kpi(steps)}

    out["residuals"] = {"table_present": False}
    if conn.execute("SELECT to_regclass('audit_accepted_finding')").fetchone()[0] is not None:
        rows = [tuple(r) for r in conn.execute(
            "SELECT rule_id, ap_id, isin, expires_at FROM audit_accepted_finding WHERE domain = 'p2_metrics'").fetchall()]
        out["residuals"] = {"table_present": True, **residuals_kpi(rows, today, AUDIT_RESIDUAL_EXPIRY_WARN_DAYS)}

    runs = [r[0] for r in conn.execute(
        "SELECT run_id FROM audit_finding WHERE domain = 'p2_metrics' GROUP BY run_id ORDER BY MAX(detected_at) DESC LIMIT %s", (n_runs,)).fetchall()]
    by_run: dict = {r: {} for r in runs}
    if runs:
        for run_id, rule_id, n in conn.execute(
                "SELECT run_id, rule_id, COUNT(*) FROM audit_finding WHERE domain = 'p2_metrics' AND run_id = ANY(%s) "
                "AND (rule_class = 'HARD_INVARIANT' OR block = 'BLOCK2' OR rule_id = 'TIMESERIES_DUPLICATE') "
                "GROUP BY run_id, rule_id", (runs,)).fetchall():
            by_run[run_id][rule_id] = int(n)
    out["recurrence"] = recurrence_kpi(by_run, n_runs)
    out["runs_considered"] = runs

    prod = conn.execute("SELECT scenario_id, promoted_at FROM portfolio_scenarios WHERE status = 'PRODUCTION'").fetchone()
    out["post_promotion"] = None
    if prod is not None and prod[1] is not None:
        funds = [r[0] for r in conn.execute("SELECT isin FROM portfolio_weights WHERE scenario_id = %s", (prod[0],)).fetchall()]
        rows = []
        if funds and conn.execute("SELECT to_regclass('audit_finding_isin')").fetchone()[0] is not None:
            rows = [tuple(r) for r in conn.execute(
                "SELECT f.run_id, f.rule_id, i.isin FROM audit_finding f JOIN audit_finding_isin i ON i.run_id = f.run_id AND i.domain = f.domain "
                "AND i.rule_id = f.rule_id AND i.group_key = f.group_key WHERE f.domain = 'p2_metrics' AND f.detected_at > %s "
                "AND i.isin = ANY(%s) AND f.severity <> 'INFO' "
                "AND (f.rule_class = 'HARD_INVARIANT' OR f.block = 'BLOCK2' OR f.rule_id = 'TIMESERIES_DUPLICATE')",
                (prod[1], funds)).fetchall()]
        out["post_promotion"] = post_promotion_kpi(prod[0], prod[1], rows)
    return out


def render(k: dict) -> str:
    p, r, res = k["promotions"], k["refusals"], k["residuals"]
    L = [f"Governance KPIs as of {k['as_of']} (counts, not rates)", "",
         f"1. promotions: {p['total']} total, {p['without_override']} without a data override, {p['with_override']} with"
         + (f" ({', '.join(f'{g}={n}' for g, n in p['overrides'].items())})" if p["overrides"] else ""),
         f"2. refusals: {r['total']} recorded"
         + (" -- by gate: " + ", ".join(f"{g}={n}" for g, n in r["by_gate"].items()) if r["by_gate"] else "")]
    if res.get("table_present"):
        L.append(f"3. residuals: {res['active_isins']} ISINs active, {res['expiring_isins']} expiring soon, {res['lapsed_isins']} lapsed rows still in the table")
        for d in res["active_by_rule_ap"]:
            L.append(f"     {d['rule']} ({d['ap']}): {d['isins']} ISINs")
    else:
        L.append("3. residuals: control.audit_accepted_finding is not present (owner migration not applied)")
    L.append(f"4. recurrence over the last {len(k['runs_considered'])} persisted runs (hard findings):")
    L += [f"     {d['rule']}: in {d['runs_with_finding']}/{d['runs_considered']} runs, latest {d['latest_count']}" for d in k["recurrence"]] or ["     none"]
    pp = k["post_promotion"]
    L.append("5. post-promotion: " + ("no PRODUCTION scenario with a promotion time" if pp is None else
             f"{pp['hard_findings']} hard findings on the funds of {pp['scenario']} since {pp['promoted_at']} (target 0)"
             + (f"; rules {pp['rules']}; funds {pp['funds'][:8]}" if pp["hard_findings"] else "")))
    return "\n".join(L)


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--runs", type=int, default=6, help="persisted audit runs considered for recurrence (default 6)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    from shared.db import get_connection
    conn = get_connection()
    try:
        k = collect(conn, n_runs=args.runs)
        conn.rollback()
    finally:
        conn.close()
    print(json.dumps(k, indent=1, ensure_ascii=False, default=str) if args.json else render(k))
    return 0


if __name__ == "__main__":
    sys.exit(main())
