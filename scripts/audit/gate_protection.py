#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
gate_protection.py -- the owner's events about the branch protection of the collision gate (Backlog v2 Phase C slice 4, FND-0334; PROPUESTA_BACKLOG_V2.md 6.4.6 and 6.4.7; NORMAS_BACKLOG.md 4.7).

    python scripts/audit/gate_protection.py                                      # --show: the latest API reading, the owner's latest verification, the emergencies (read-only)
    python scripts/audit/gate_protection.py --verified "checked in Settings > Branches on 2026-11-02: backlog-gate is a required check"
    python scripts/audit/gate_protection.py --emergency "pushed abc1234 with the rule disabled: CI was down; new key within 48 h"

--verified clears V-GATE-UNVERIFIED for 30 days: use it when the API cannot be read (no token, not enough permission) and you have looked at the repository settings yourself. It does not
clear V-GATE-UNPROTECTED: that one goes only when the API reads `backlog-gate` among the required checks.
--emergency logs the emergency push of the protocol (PROPUESTA 6.4.7): the owner bypassed the rule as administrator. It raises V-GATE-EMERGENCY, which stays in the record; within 48 h generate
a new key, commit its public part in .github/backlog_gate_allowed_signers, sign a catch-up attestation and re-run the check with workflow_dispatch.

Both are written by a person: a human identity in FONDOS_ACTOR, the owner-only login (FONDOS_BACKLOG_PG_DSN_HUMAN, typed in this terminal, never kept in .env) and the usual typed confirmation.
The reading of the API itself is done by backlog_sync.py with the read-only token BACKLOG_GATE_GITHUB_TOKEN.

Exit codes: 0 ok, 1 the write was refused or failed, 2 usage error (no database, the attest store is not applied, a bad argument).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RC_OK, RC_FAILED, RC_USAGE = 0, 1, 2


def show_lines(state: dict) -> list:
    """The read-only summary. Pure. `state`: backlog_client.gate_protection_state()."""
    api = state["api"]
    lines = ["branch protection of the collision gate:"]
    lines.append("  latest API reading: " + ("none yet (backlog_sync.py --apply records it)" if api is None else f"{api[0]} at {api[2]:%Y-%m-%d %H:%M} -- {api[1]}"))
    lines.append("  owner verification in the last 30 days: " + ("none" if state["verified"] is None else f"{state['verified']:%Y-%m-%d %H:%M}"))
    lines.append(f"  emergency pushes logged: {state['emergencies']}")
    if api is not None and api[0] == "UNPROTECTED":
        lines.append("  [ALERTA] V-GATE-UNPROTECTED is open: `backlog-gate` is not a required status check (owner action O-5).")
    elif api is not None and api[0] == "UNVERIFIED" and state["verified"] is None:
        lines.append("  V-GATE-UNVERIFIED is open: the API cannot be read; set a read-only token or write --verified after checking the settings yourself.")
    return lines


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--verified", metavar="REASON", help="record that you checked the repository settings yourself (clears V-GATE-UNVERIFIED for 30 days)")
    g.add_argument("--emergency", metavar="REASON", help="log an emergency push (V-GATE-EMERGENCY)")
    args = ap.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    import shared.config  # noqa: F401  (loads .env: the ordinary read login)
    from shared import backlog_client as bc

    try:
        if not bc.attest_store_present():
            print("the attest store is not applied (owner: python scripts/ops/migrate_backlog_v2.py --phase attest)", file=sys.stderr)
            return RC_USAGE
        state = bc.gate_protection_state()
    except Exception as exc:                                  # never echo a DSN
        print(f"backlog unreachable: {type(exc).__name__}", file=sys.stderr)
        return RC_USAGE
    print("\n".join(show_lines(state)))
    kind, reason = ("VERIFIED", args.verified) if args.verified else (("EMERGENCY", args.emergency) if args.emergency else (None, None))
    if kind is None:
        return RC_OK
    if len((reason or "").strip()) < 10:
        print("the reason needs 10 or more characters", file=sys.stderr)
        return RC_USAGE
    try:
        refusal = bc.require_live_write_cli(bc.dsn_for_actor(bc.current_actor()))
    except Exception as exc:                                  # noqa: BLE001  (no human identity, or no owner-only DSN)
        print(f"REFUSED: {type(exc).__name__}: {str(exc)[:250]}", file=sys.stderr)
        return RC_FAILED
    if refusal is not None:
        return refusal
    try:
        bc.record_gate_protection(kind, reason.strip())
    except Exception as exc:
        print(f"REFUSED: {type(exc).__name__}: {str(exc)[:300]}", file=sys.stderr)
        return RC_FAILED
    print(f"\nRECORDED: {kind}.")
    return RC_OK


if __name__ == "__main__":
    sys.exit(main())
