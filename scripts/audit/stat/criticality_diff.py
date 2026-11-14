#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
criticality_diff.py -- which persisted findings change their blocking status under the criticality model (FND-0262). Read-only.

    python scripts/audit/criticality_diff.py                  # the latest full run of each domain
    python scripts/audit/criticality_diff.py --all            # every persisted full run
    python scripts/audit/criticality_diff.py --run RUN_ID     # one run (any domain)

For each run it compares the legacy domain-agnostic predicate (gating.is_blocking without a domain) with the model
(shared/statistical_audit/criticality.py, decided per domain) over the run's persisted audit_finding rows, and prints the rules whose
findings change from blocking to not blocking or back. Expected by construction: no change in p2_metrics; in cost_attributes only the
BLOCK2 `X__EQUALS__Y` coincidence pairs stop blocking. Anything else is a regression of the model.

Exit codes: 0 report printed (and only the expected changes), 1 an unexpected change was found, 2 usage or connection error.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

DOMAINS = ("p2_metrics", "cost_attributes")


def diff_rows(domain: str, rows) -> list:
    """[(rule_id, rule_class, block, legacy_blocks, model_blocks, n_rows)] for the rules whose decision changes. Pure."""
    from shared.statistical_audit.gating import is_blocking
    changes: dict = {}
    for rule_id, rule_class, block in rows:
        legacy, model = is_blocking(rule_class, block, rule_id), is_blocking(rule_class, block, rule_id, domain)
        if legacy != model:
            key = (rule_id, rule_class, block, legacy, model)
            changes[key] = changes.get(key, 0) + 1
    return [(*k, n) for k, n in sorted(changes.items())]


def unexpected(domain: str, changes: list) -> list:
    """The changes the model does NOT intend: anything in p2_metrics, and anything in cost_attributes other than a non-hard BLOCK2."""
    if domain == "p2_metrics":
        return list(changes)
    return [c for c in changes if not (c[2] == "BLOCK2" and c[1] != "HARD_INVARIANT" and c[3] and not c[4])]


def main(argv: "list | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true", help="every persisted full run instead of the latest per domain")
    ap.add_argument("--run", default=None, help="one run id")
    args = ap.parse_args(argv)

    import shared.config  # noqa: F401  (loads the repo .env)
    from shared.db import get_connection
    try:
        conn = get_connection()
    except Exception as exc:                                     # noqa: BLE001
        print(f"error: connection failed ({type(exc).__name__})", file=sys.stderr)
        return 2
    bad = 0
    try:
        if args.run:
            runs = [tuple(r) for r in conn.execute("SELECT DISTINCT run_id, domain FROM audit_finding WHERE run_id = %s", (args.run,)).fetchall()]
        else:
            runs = []
            for domain in DOMAINS:
                found = [tuple(r) for r in conn.execute(
                    "SELECT run_id, domain FROM audit_finding WHERE domain = %s AND right(run_id, 7) <> '_sample' "
                    "GROUP BY run_id, domain ORDER BY MAX(detected_at) DESC", (domain,)).fetchall()]
                runs += found if args.all else found[:1]
        if not runs:
            print("no persisted run found")
            return 2
        for run_id, domain in runs:
            rows = conn.execute("SELECT rule_id, rule_class, block FROM audit_finding WHERE run_id = %s AND domain = %s",
                                (run_id, domain)).fetchall()
            changes = diff_rows(domain, [tuple(r) for r in rows])
            odd = unexpected(domain, changes)
            bad += len(odd)
            print(f"{run_id} [{domain}]: {len(rows)} findings, {len(changes)} rule(s) change their blocking status"
                  + (f", {len(odd)} UNEXPECTED" if odd else ""))
            for rule_id, rule_class, block, legacy, model, n in changes:
                mark = "!" if (rule_id, rule_class, block, legacy, model, n) in odd else " "
                print(f"  {mark} {rule_id} [{rule_class} {block}] x{n}: {'blocks' if legacy else 'no block'} -> "
                      f"{'blocks' if model else 'no block'}")
    finally:
        conn.rollback()
        conn.close()
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
