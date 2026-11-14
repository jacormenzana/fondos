#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
audit_incident.py -- closed-loop remediation of CRITICAL audit findings (FND-0264, GOV-05). Decisions: shared/statistical_audit/incidents.py.

    python scripts/audit/audit_incident.py --plan [--domain p2|costs] [--policy first|recurrence]   # READ-ONLY: the incidents that WOULD open
    python scripts/audit/audit_incident.py --list                                                    # READ-ONLY: control.audit_incident
    python scripts/audit/audit_incident.py --close-check FND-nnnn                                    # READ-ONLY: may this ticket be closed?
    python scripts/audit/audit_incident.py --open [--apply]            # AUDIT_INCIDENT_LOOP_ENABLED: record + open the backlog tickets
    python scripts/audit/audit_incident.py --annotate ID [--ap FND-nnnn] [--root-cause ..] [--action ..] [--fix-kind RECOMPUTE|CODE|DATA|ACCEPT] --apply
    python scripts/audit/audit_incident.py --certify ID --run RUN_ID [--snapshot before.json] --apply   # a later re-audit shows it fixed
    python scripts/audit/audit_incident.py --accept ID --apply         # every affected ISIN has an ACTIVE accepted residual (FND-0263)
    python scripts/audit/audit_incident.py --close ID --apply          # the backlog ticket was closed on top of CERTIFIED / ACCEPTED

The auditor stays read-only on the data: a fix runs in its owning pipeline (run_pipeline.py --isin .. --force, run_block.py ..); this tool
only records and certifies it with a later audit run. A recompute alone never certifies: --certify needs the run_id of a complete re-audit
(FULL, or a --isin run covering the affected ISINs) in which the affected ISINs no longer violate the rule, and for a RECOMPUTE fix the
--snapshot taken BEFORE it (scripts/audit/run_statistical_audit.py --state-snapshot) to prove every input_hash changed.

Exit codes: 0 ok / closable, 1 not closable or not certifiable, 2 usage, connection or missing table / switch.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

DOMAINS = {"p2": "p2_metrics", "costs": "cost_attributes"}


def full_runs(conn, domain: str, limit: int = 2) -> list:
    """[(run_id, finished_at, scope, status, manifest)] newest first: the FULL, not INCOMPLETE headers of `domain` (control.audit_run)."""
    rows = conn.execute("SELECT run_id, finished_at, scope, status, manifest FROM audit_run WHERE domain = %s AND scope = 'FULL' "
                        "AND status <> 'INCOMPLETE' ORDER BY finished_at DESC LIMIT %s", (domain, limit)).fetchall()
    return [tuple(r) for r in rows]


def run_findings(conn, run_id: str, domain: str) -> tuple:
    """(findings, isins_by_key) of one persisted run, ready for incidents.incidents_to_open."""
    from shared.statistical_audit.incidents import finding_key
    cols = ("rule_id", "rule_class", "block", "group_key", "isin", "evidence")
    findings = [dict(zip(cols, r)) for r in conn.execute(
        "SELECT rule_id, rule_class, block, group_key, isin, evidence FROM audit_finding WHERE run_id = %s AND domain = %s",
        (run_id, domain)).fetchall()]
    by_key: dict = {}
    if conn.execute("SELECT to_regclass('audit_finding_isin')").fetchone()[0] is not None:
        for rule_id, group_key, isin in conn.execute(
                "SELECT rule_id, group_key, isin FROM audit_finding_isin WHERE run_id = %s AND domain = %s", (run_id, domain)).fetchall():
            by_key.setdefault(finding_key(domain, rule_id, group_key), set()).add(isin)
    return findings, by_key


def build_reaudit(domain: str, header: tuple, findings: list, isins_by_key: dict):
    """incidents.Reaudit from a run header (run_id, finished_at, scope, status, manifest) and its findings. Pure."""
    from shared.statistical_audit.incidents import Reaudit, critical_findings, finding_key
    run_id, finished_at, scope, status, manifest = header
    violators: dict = {}
    for f in critical_findings(domain, findings):
        key = finding_key(domain, f["rule_id"], f.get("group_key") or f["rule_id"])
        violators.setdefault(key, set()).update(isins_by_key.get(key, ()))
        if f.get("isin"):
            violators[key].add(f["isin"])
    scope_isins = frozenset((manifest or {}).get("scope_isins") or ()) if scope != "FULL" else None
    return Reaudit(run_id, domain, scope, status, finished_at, {k: frozenset(v) for k, v in violators.items()}, scope_isins)


def plan(conn, domains, policy: str, live_keys) -> list:
    """The would-be incidents of the latest FULL run of each domain (the D7 shadow log). Read-only."""
    from shared.statistical_audit.incidents import critical_findings, finding_key, incidents_to_open
    out = []
    for domain in domains:
        runs = full_runs(conn, domain, 2)
        if not runs:
            print(f"[{domain}] no FULL, complete audit run persisted: nothing to plan")
            continue
        findings, by_key = run_findings(conn, runs[0][0], domain)
        previous = set()
        if len(runs) > 1:
            prev_findings, _ = run_findings(conn, runs[1][0], domain)
            previous = {finding_key(domain, f["rule_id"], f.get("group_key") or f["rule_id"]) for f in critical_findings(domain, prev_findings)}
        out += incidents_to_open(domain, runs[0][0], findings, by_key, live_keys, previous, policy)
    return out


def _print_plan(cands: list, policy: str) -> None:
    print(f"{len(cands)} incident(s) would open (policy {policy}):")
    for c in cands:
        print(f"  [{c.domain}] {c.rule_id} / {c.group_key}: {len(c.isins)} ISIN(s), run {c.run_id}: {c.evidence[:110]}")


def main(argv: "list | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--open", action="store_true")
    ap.add_argument("--close-check", metavar="AP")
    ap.add_argument("--annotate", type=int, metavar="ID")
    ap.add_argument("--certify", type=int, metavar="ID")
    ap.add_argument("--accept", type=int, metavar="ID")
    ap.add_argument("--close", type=int, metavar="ID")
    ap.add_argument("--domain", choices=sorted(DOMAINS), default=None)
    ap.add_argument("--policy", choices=("first", "recurrence"), default="first")
    ap.add_argument("--run", default=None, help="the re-audit run id (--certify)")
    ap.add_argument("--snapshot", default=None, help="BEFORE state snapshot JSON (--certify of a RECOMPUTE fix)")
    ap.add_argument("--ap", default=None)
    ap.add_argument("--root-cause", default=None)
    ap.add_argument("--action", default=None)
    ap.add_argument("--fix-kind", default=None)
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    args = ap.parse_args(argv)
    modes = [m for m in ("plan", "list", "open", "close_check", "annotate", "certify", "accept", "close") if getattr(args, m) not in (None, False)]
    if len(modes) != 1:
        ap.error("choose exactly one of --plan --list --open --close-check --annotate --certify --accept --close")
    mode = modes[0]
    if mode == "certify" and not args.run:
        ap.error("--certify needs --run RUN_ID (the re-audit)")

    import shared.config as cfg  # (loads the repo .env)
    from shared.db import get_connection
    from shared.statistical_audit import accepted_residuals as ar
    from shared.statistical_audit import incidents as inc
    from shared.statistical_audit import persistence as ps

    domains = [DOMAINS[args.domain]] if args.domain else list(DOMAINS.values())
    conn = get_connection()
    try:
        has_table = ps.incident_table_exists(conn)
        rows = inc.load_incidents(conn) if has_table else []
        if mode in ("annotate", "certify", "accept", "close", "open") and not has_table:
            print("error: control.audit_incident does not exist; run scripts/ops/migrate_audit_incident.py --apply first", file=sys.stderr)
            return 2

        if mode == "plan":
            live = {r["finding_key"] for r in rows if r["state"] in inc.LIVE_STATES}
            _print_plan(plan(conn, domains, args.policy, live), args.policy)
            if not has_table:
                print("(control.audit_incident is not applied yet: every critical key counts as new)")
            return 0

        if mode == "list":
            if not has_table:
                print("control.audit_incident is not applied")
                return 0
            print(f"{len(rows)} incident(s)")
            for r in rows:
                print(f"  #{r['incident_id']} {r['state']:<9} {r['finding_key']} ap={r['ap_id'] or '-'} fix={r['fix_kind'] or '-'} "
                      f"isins={len(r['isins'])} opened={r['opened_run_id']} certified={r['certified_run_id'] or '-'}")
            return 0

        if mode == "close_check":
            if not has_table:
                if cfg.AUDIT_INCIDENT_LOOP_ENABLED:
                    print("NOT CLOSABLE: AUDIT_INCIDENT_LOOP_ENABLED is on and control.audit_incident does not exist (fail closed)")
                    return 1
                print(f"{args.close_check}: no incident store: nothing links it to an audit defect")
                return 0
            verdict = inc.closable(args.close_check, rows)
            linked = [r for r in rows if r["ap_id"] == args.close_check]
            print(f"{args.close_check}: {len(linked)} linked incident(s): {'CLOSABLE' if verdict.ok else 'NOT CLOSABLE'}")
            for reason in verdict.reasons:
                print(f"  - {reason}")
            return 0 if verdict.ok else 1

        if mode == "open":
            if not cfg.AUDIT_INCIDENT_LOOP_ENABLED:
                print("error: AUDIT_INCIDENT_LOOP_ENABLED is off: --plan shows what would open; the owner flips the switch after reading it",
                      file=sys.stderr)
                return 2
            live = {r["finding_key"] for r in rows if r["state"] in inc.LIVE_STATES}
            cands = plan(conn, domains, args.policy, live)
            _print_plan(cands, args.policy)
            if not args.apply:
                print("\nDRY RUN: nothing written. Add --apply to record the incidents and open their backlog tickets.")
                return 0
            from shared import backlog_client
            from shared.governance.run_identity import calc_version
            for c in cands:
                ap_id = backlog_client.report_incident(
                    object_name=f"audit_incident:{c.key}", schema_name="control", object_type="TAB",
                    title=f"[AUDIT-INCIDENT] {c.domain} {c.rule_id}: {len(c.isins)} fund(s)",
                    scenario_description=(f"CRITICAL finding {c.rule_id} / {c.group_key} in {c.domain} (run {c.run_id}): {c.evidence} "
                                          f"Affected: {len(c.isins)} ISIN(s). Close only on a certifying re-audit "
                                          "(scripts/audit/audit_incident.py --certify) or an active accepted residual."))
                incident_id = ps.open_incident(conn, c, ap_id=ap_id, affected_calc_version=calc_version())
                print(f"  incident #{incident_id} {c.key} -> ticket {ap_id or '(backlog unavailable: not linked)'}")
            return 0

        # --- the per-incident commands ----------------------------------------------------------------------------------------------------
        target_id = args.annotate or args.certify or args.accept or args.close
        incident = next((r for r in rows if r["incident_id"] == target_id), None)
        if incident is None:
            print(f"error: incident {target_id} does not exist", file=sys.stderr)
            return 2
        print(f"#{incident['incident_id']} {incident['state']} {incident['finding_key']} ap={incident['ap_id']} fix={incident['fix_kind']} "
              f"isins={len(incident['isins'])}")

        if mode == "annotate":
            if not args.apply:
                print("DRY RUN: nothing written. Add --apply.")
                return 0
            try:
                ps.annotate_incident(conn, target_id, ap_id=args.ap, root_cause=args.root_cause, corrective_action=args.action,
                                     fix_kind=args.fix_kind)
            except ValueError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
            print("ANNOTATED")
            return 0

        if mode == "certify":
            header = next((h for h in conn.execute(
                "SELECT run_id, finished_at, scope, status, manifest FROM audit_run WHERE run_id = %s AND domain = %s",
                (args.run, incident["domain"])).fetchall()), None)
            if header is None:
                print(f"error: run {args.run} has no control.audit_run header for {incident['domain']}: only persisted, headed runs certify",
                      file=sys.stderr)
                return 2
            findings, by_key = run_findings(conn, args.run, incident["domain"])
            reaudit = build_reaudit(incident["domain"], tuple(header), findings, by_key)
            recompute_ok = None
            if args.snapshot:
                import pandas as pd
                from shared.statistical_audit.recompute_gate import assert_recompute_happened, capture_state
                before = pd.DataFrame(json.load(open(args.snapshot, encoding="utf-8")))
                result = assert_recompute_happened(before, capture_state(conn, list(incident["isins"])))
                recompute_ok = result.unchanged.empty and result.missing_after.empty
            verdict = inc.certify(incident, reaudit, recompute_ok)
            print(f"certification by {args.run}: {'OK' if verdict.ok else 'REFUSED'}")
            for reason in verdict.reasons:
                print(f"  - {reason}")
            if verdict.ok and args.apply:
                ps.certify_incident(conn, target_id, args.run)
                print("CERTIFIED")
            elif verdict.ok:
                print("DRY RUN: nothing written. Add --apply.")
            return 0 if verdict.ok else 1

        if mode == "accept":
            accepted = ar.load_accepted(conn, incident["domain"]).get(incident["rule_id"], {})
            verdict = inc.acceptance(incident, accepted.keys())
            print(f"acceptance: {'OK' if verdict.ok else 'REFUSED'}")
            for reason in verdict.reasons:
                print(f"  - {reason}")
            if verdict.ok and args.apply:
                ps.accept_incident(conn, target_id)
                print("ACCEPTED")
            elif verdict.ok:
                print("DRY RUN: nothing written. Add --apply.")
            return 0 if verdict.ok else 1

        if mode == "close":
            if incident["state"] not in (inc.STATE_CERTIFIED, inc.STATE_ACCEPTED):
                print(f"error: only a CERTIFIED or ACCEPTED incident closes (this one is {incident['state']})", file=sys.stderr)
                return 1
            if not args.apply:
                print("DRY RUN: nothing written. Add --apply.")
                return 0
            ps.close_incident(conn, target_id)
            print("CLOSED")
            return 0
        return 2
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
