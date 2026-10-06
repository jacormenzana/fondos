#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
accept_audit_residual.py -- accept the CURRENT violators of one statistical-audit rule as a known residual (FND-0234(e)).

    python scripts/audit/accept_audit_residual.py --domain costs --rule OC_NOT_CONTAMINATED --ap FND-0034 \\
        --reason "OC copied from the ACI row; extractor cannot bind it (FND-0034)" [--days 90] [--by NAME] [--isin A,B]
    ... --apply        # without it: DRY RUN, nothing is written

What it does: runs the audit for the domain in memory (read-only, no persist), takes the ISINs that violate `--rule` right now
(optionally narrowed with --isin) and records them in control.audit_accepted_finding with the owning AP, the reason and an
expiry (default 90 days, capped at 180: nothing is accepted for ever). From then on the audit reports those ISINs as INFO
("ACCEPTED RESIDUAL") instead of an ALARM, and an ISIN that is NOT accepted keeps the rule an ALARM, so only new violations
alert. An acceptance is renewed by running this again; it lapses by itself when it expires.

It never decides for you: the owner runs it, with --apply, and the AP must exist in gestion.backlog. Table missing? Run
scripts/ops/migrate_audit_accepted_finding.py --apply first.
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


def _load_runner():
    spec = importlib.util.spec_from_file_location("run_statistical_audit", ROOT / "scripts" / "audit" / "run_statistical_audit.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["run_statistical_audit"] = mod
    spec.loader.exec_module(mod)
    return mod


def validate(ap: str, reason: str, days: int) -> list:
    """Problems with the arguments (empty list = fine). Pure, so it is testable without a database."""
    problems = []
    if not AP_RE.match(ap or ""):
        problems.append(f"--ap {ap!r} is not an action point id like FND-0034")
    if len((reason or "").strip()) < MIN_REASON_CHARS:
        problems.append(f"--reason must say why, in at least {MIN_REASON_CHARS} characters")
    if days < 1:
        problems.append("--days must be >= 1")
    return problems


def violators(findings: list, rule_id: str, only: "set | None" = None) -> tuple:
    """(isins, found_rule): the ISINs of the finding for `rule_id`, narrowed to `only` when given. Pure."""
    for f in findings:
        if f.get("rule_id") == rule_id:
            isins = [i for i in (f.get("violating_isins") or ()) if only is None or i in only]
            return sorted(set(isins)), True
    return [], False


def main(argv: "list | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--domain", choices=DOMAINS, required=True)
    ap.add_argument("--rule", required=True, help="rule_id as printed by the audit, e.g. OC_NOT_CONTAMINATED")
    ap.add_argument("--ap", required=True, help="the backlog action point that owns the fix, e.g. FND-0034")
    ap.add_argument("--reason", required=True)
    ap.add_argument("--days", type=int, default=90, help="validity in days (default 90, capped at 180)")
    ap.add_argument("--by", default=None, help="who accepts (default: the OS user)")
    ap.add_argument("--isin", default=None, help="comma-separated: accept only these (they must violate the rule now)")
    ap.add_argument("--apply", action="store_true", help="write the acceptance (default: dry run)")
    args = ap.parse_args(argv)

    problems = validate(args.ap, args.reason, args.days)
    if problems:
        for p in problems:
            print(f"error: {p}", file=sys.stderr)
        return 2

    import shared.config  # noqa: F401  (loads the repo .env)
    from shared.db import get_connection
    from shared.statistical_audit.accepted_residuals import expiry_after
    from shared.statistical_audit.persistence import emit_accepted_residuals

    runner = _load_runner()
    only = {i.strip() for i in args.isin.split(",") if i.strip()} if args.isin else None
    conn = get_connection()
    try:
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
        expires = expiry_after(args.days)
        by = args.by or getpass.getuser()
        print(f"domain={run.domain} rule={args.rule} ap={args.ap} expires={expires} by={by}")
        print(f"{len(isins)} ISINs violate the rule now; first 10: {', '.join(isins[:10])}")
        if not args.apply:
            print("\nDRY RUN: nothing written. Add --apply to record the acceptance.")
            return 0
        if conn.execute("SELECT to_regclass('control.audit_accepted_finding')").fetchone()[0] is None:
            print("error: control.audit_accepted_finding does not exist; run "
                  "scripts/ops/migrate_audit_accepted_finding.py --apply first", file=sys.stderr)
            return 2
        n = emit_accepted_residuals(conn, run.domain, args.rule, isins, args.ap, args.reason.strip(), expires, by)
        print(f"\nACCEPTED: {n} ISINs for {args.rule} until {expires}. Record it on {args.ap} in gestion.backlog.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
