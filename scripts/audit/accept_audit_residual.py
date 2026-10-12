#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
accept_audit_residual.py -- accept the CURRENT violators of one statistical-audit rule as a known residual (FND-0234(e)), as a governed
two-step decision (FND-0263, decision D6).

    # 1. GRANT (or --renew): the ISINs that violate --rule now, the owning AP, the reason, an expiry (<= 90 days)
    python scripts/audit/accept_audit_residual.py --domain costs --rule OC_NOT_CONTAMINATED --ap FND-0034 \\
        --reason "OC copied from the ACI row; extractor cannot bind it (FND-0034)" [--days 90] [--by NAME] [--isin A,B] [--renew]
    # 2. APPROVE that grant, as a separate command (the same person may approve; no time pad): only now are the ISINs accepted
    python scripts/audit/accept_audit_residual.py --approve 17 [--by NAME]
    # withdraw a grant (all its ISINs, or --isin among them); cause OWNER | AP_CLOSED | RESOLVED
    python scripts/audit/accept_audit_residual.py --revoke 17 --cause RESOLVED --reason "fixed by FND-0034, re-audit clean"
    # the decision log of a domain (optionally one rule)
    python scripts/audit/accept_audit_residual.py --history --domain costs [--rule OC_NOT_CONTAMINATED]
    ... --apply        # every write needs it: without it, DRY RUN, nothing is written

The grant runs the audit for the domain in memory (read-only, no persist) and takes the ISINs that violate `--rule` right now. Each
decision is an event in control.audit_exception_event (append-only); control.audit_accepted_finding, which the audit, the promotion gate
and the scorer read, is refreshed from that history in the same transaction. From approval on, the audit reports those ISINs as INFO
("ACCEPTED RESIDUAL") instead of an ALARM; an ISIN that is NOT accepted keeps the rule an ALARM; an acceptance lapses by itself at expiry.

It never decides for you: the owner runs it, with --apply, and the AP must exist in gestion.backlog. Without the history table
(scripts/ops/migrate_audit_governance.py not applied) a grant falls back to the legacy direct write, unless
shared.config.AUDIT_EXCEPTION_EVENTS_ENABLED is on, in which case it refuses.
"""
from __future__ import annotations

import argparse
import getpass
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

AP_RE = re.compile(r"^[A-Z]{2,4}-\d{4}$")
MIN_REASON_CHARS = 20
DOMAINS = ("costs", "p2")
AUDIT_DOMAIN = {"costs": "cost_attributes", "p2": "p2_metrics"}     # CLI name -> audit_finding.domain
REVOKE_CAUSES = ("OWNER", "AP_CLOSED", "RESOLVED")                  # == accepted_residuals.REVOKE_CAUSES (asserted by the tests)


def _load_runner():
    spec = importlib.util.spec_from_file_location("run_statistical_audit", ROOT / "scripts" / "audit" / "run_statistical_audit.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["run_statistical_audit"] = mod
    spec.loader.exec_module(mod)
    return mod


def validate(ap: str, reason: str, days: int) -> list:
    """Problems with the grant arguments (empty list = fine). Pure, so it is testable without a database."""
    problems = []
    if not AP_RE.match(ap or ""):
        problems.append(f"--ap {ap!r} is not an action point id like FND-0034")
    if len((reason or "").strip()) < MIN_REASON_CHARS:
        problems.append(f"--reason must say why, in at least {MIN_REASON_CHARS} characters")
    if days < 1:
        problems.append("--days must be >= 1")
    return problems


def validate_revoke(cause: str, reason: str) -> list:
    """Problems with the revoke arguments. Pure."""
    problems = []
    if cause not in REVOKE_CAUSES:
        problems.append(f"--cause must be one of {', '.join(REVOKE_CAUSES)}")
    if len((reason or "").strip()) < MIN_REASON_CHARS:
        problems.append(f"--reason must say why, in at least {MIN_REASON_CHARS} characters")
    return problems


def violators(findings: list, rule_id: str, only: "set | None" = None) -> tuple:
    """(isins, found_rule): the ISINs of the finding for `rule_id`, narrowed to `only` when given. Pure."""
    for f in findings:
        if f.get("rule_id") == rule_id:
            isins = [i for i in (f.get("violating_isins") or ()) if only is None or i in only]
            return sorted(set(isins)), True
    return [], False


def mode_of(args) -> tuple:
    """(mode, problems): exactly one of grant / approve / revoke / history. Pure."""
    modes = [m for m, on in (("approve", args.approve is not None), ("revoke", args.revoke is not None), ("history", args.history)) if on]
    if len(modes) > 1:
        return None, [f"choose one of --approve, --revoke, --history (got {', '.join('--' + m for m in modes)})"]
    if modes:
        mode = modes[0]
        if mode == "history" and not args.domain:
            return None, ["--history needs --domain"]
        if mode == "revoke":
            return mode, validate_revoke(args.cause, args.reason)
        return mode, []
    missing = [f"--{n}" for n in ("domain", "rule", "ap", "reason") if not getattr(args, n)]
    if missing:
        return None, [f"a grant needs {', '.join(missing)}"]
    return "grant", validate(args.ap, args.reason, args.days)


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--domain", choices=DOMAINS)
    ap.add_argument("--rule", help="rule_id as printed by the audit, e.g. OC_NOT_CONTAMINATED")
    ap.add_argument("--ap", help="the backlog action point that owns the fix, e.g. FND-0034")
    ap.add_argument("--reason")
    ap.add_argument("--days", type=int, default=90, help="validity in days (default 90; values above 90 are capped at 90)")
    ap.add_argument("--by", default=None, help="who decides (default: the OS user)")
    ap.add_argument("--isin", default=None, help="comma-separated: grant only these (they must violate now) / revoke only these")
    ap.add_argument("--renew", action="store_true", help="record the grant as a RENEW of an existing acceptance")
    ap.add_argument("--approve", type=int, default=None, metavar="EVENT_ID", help="approve a pending GRANT / RENEW")
    ap.add_argument("--revoke", type=int, default=None, metavar="EVENT_ID", help="revoke a GRANT / RENEW (needs --cause and --reason)")
    ap.add_argument("--cause", default=None, choices=REVOKE_CAUSES)
    ap.add_argument("--history", action="store_true", help="print the decision log of --domain (and --rule)")
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    return ap


def _print_history(events: list, rule: "str | None") -> None:
    shown = [e for e in events if rule is None or e["rule_id"] == rule]
    print(f"{len(shown)} event(s)")
    for e in shown:
        what = (f"{len(e['isins'])} ISINs, {e['ap_id']}, until {e['expires_at']}" if e["event_type"] in ("GRANT", "RENEW")
                else f"of #{e['ref_event_id']}" + (f" cause {e['cause']}" if e["cause"] else "")
                + (f" ({len(e['isins'])} ISINs)" if e.get("isins") and e["event_type"] == "REVOKE" else ""))
        print(f"  #{e['event_id']} {e['event_ts']:%Y-%m-%d %H:%M} {e['event_type']:<7} {e['rule_id']} {what} by {e['actor']}"
              + (f" -- {e['reason']}" if e.get("reason") else ""))


def main(argv: "list | None" = None) -> int:
    args = _parser().parse_args(argv)
    mode, problems = mode_of(args)
    if problems:
        for p in problems:
            print(f"error: {p}", file=sys.stderr)
        return 2

    import shared.config as cfg  # (loads the repo .env)
    from shared.db import get_connection
    from shared.statistical_audit import accepted_residuals as ar
    from shared.statistical_audit import persistence as ps

    by = args.by or getpass.getuser()
    only = {i.strip() for i in args.isin.split(",") if i.strip()} if args.isin else None
    conn = get_connection()
    try:
        has_events = ps.exception_events_table_exists(conn)
        if mode in ("approve", "revoke", "history") and not has_events:
            print("error: control.audit_exception_event does not exist; run scripts/ops/migrate_audit_governance.py --apply first",
                  file=sys.stderr)
            return 2

        if mode == "history":
            _print_history(ar.load_events(conn, AUDIT_DOMAIN[args.domain]), args.rule)
            return 0

        if mode in ("approve", "revoke"):
            event_id = args.approve if mode == "approve" else args.revoke
            row = conn.execute("SELECT domain FROM audit_exception_event WHERE event_id = %s", (event_id,)).fetchone()
            if row is None:
                print(f"error: event {event_id} does not exist", file=sys.stderr)
                return 2
            events = ar.load_events(conn, row[0])
            grant = next(e for e in events if e["event_id"] == event_id)
            state = ar.grant_state(events, event_id)
            print(f"#{event_id} {grant['event_type']} {grant['domain']} {grant['rule_id']}: {state}; "
                  f"{len(grant.get('isins') or [])} ISINs, {grant.get('ap_id')}, until {grant.get('expires_at')}, granted by {grant['actor']}")
            if not args.apply:
                print(f"\nDRY RUN: nothing written. Add --apply to {mode}.")
                return 0
            try:
                if mode == "approve":
                    new_id = ps.approve_exception(conn, event_id, by)
                    print(f"\nAPPROVED (event #{new_id}): {len(grant['isins'])} ISINs of {grant['rule_id']} accepted until {grant['expires_at']}.")
                else:
                    new_id = ps.revoke_exception(conn, event_id, by, args.cause, args.reason.strip(), sorted(only) if only else None)
                    print(f"\nREVOKED (event #{new_id}, cause {args.cause}): "
                          f"{len(only) if only else len(grant['isins'])} ISINs of {grant['rule_id']} are no longer accepted.")
            except ValueError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
            return 0

        # ---- grant -----------------------------------------------------------------------------------------------------------------
        runner = _load_runner()
        run = runner.run_cost_audit(conn) if args.domain == "costs" else runner.run_p2_audit(conn)
        isins, found = violators(run.findings, args.rule, only)
        if not found:
            print(f"error: rule {args.rule!r} has no finding in the {run.domain} audit right now (nothing to accept)",
                  file=sys.stderr)
            return 2
        if not isins:
            print(f"error: the finding for {args.rule} carries no violating ISINs"
                  + (" in the --isin selection" if only else " (the rule does not report ISINs)"), file=sys.stderr)
            return 2
        expires = ar.expiry_after(args.days)
        kind = "RENEW" if args.renew else "GRANT"
        print(f"{kind}: domain={run.domain} rule={args.rule} ap={args.ap} expires={expires} by={by}")
        print(f"{len(isins)} ISINs violate the rule now; first 10: {', '.join(isins[:10])}")
        if not args.apply:
            print("\nDRY RUN: nothing written. Add --apply to record the grant.")
            return 0
        if has_events:
            event_id = ps.grant_exception(conn, run.domain, args.rule, isins, args.ap, args.reason.strip(), expires, by,
                                          renew=args.renew)
            print(f"\n{kind} RECORDED as event #{event_id}: PENDING, nothing is accepted yet. Approve it as a separate decision:\n"
                  f"  python scripts/audit/accept_audit_residual.py --approve {event_id} --apply\n"
                  f"Record it on {args.ap} in gestion.backlog.")
            return 0
        if cfg.AUDIT_EXCEPTION_EVENTS_ENABLED:
            print("error: AUDIT_EXCEPTION_EVENTS_ENABLED is on and control.audit_exception_event does not exist: run "
                  "scripts/ops/migrate_audit_governance.py --apply (the legacy direct write keeps no history)", file=sys.stderr)
            return 2
        if conn.execute("SELECT to_regclass('control.audit_accepted_finding')").fetchone()[0] is None:
            print("error: control.audit_accepted_finding does not exist; run "
                  "scripts/ops/migrate_audit_accepted_finding.py --apply first", file=sys.stderr)
            return 2
        n = ps.emit_accepted_residuals(conn, run.domain, args.rule, isins, args.ap, args.reason.strip(), expires, by)
        print(f"\nACCEPTED (legacy direct write, no history: apply scripts/ops/migrate_audit_governance.py): {n} ISINs for "
              f"{args.rule} until {expires}. Record it on {args.ap} in gestion.backlog.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
