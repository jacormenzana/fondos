#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
gate_mode.py -- the owner's command to read and set the mode of the collision gate (Backlog v2 Phase C slice 3, FND-0334; PROPUESTA_BACKLOG_V2.md 6.4.4; NORMAS_BACKLOG.md 4.6).

    python scripts/audit/gate_mode.py                                   # --show: the ceiling, the effective mode and tier, the audit clock and what BLOCK still needs (read-only)
    python scripts/audit/gate_mode.py --set AUDIT --reason "start of the 14-day audit of the collision gate"
    python scripts/audit/gate_mode.py --set BLOCK --reason "..."        # refused by the DATABASE until 14 days of AUDIT and a share of commits that introduce a pair of 10 % or less
    python scripts/audit/gate_mode.py --set OFF --reason "..."          # retire the gate by hand

The modes are OFF, AUDIT (records pairs and warns, blocks nothing) and BLOCK (also a hold on the ticket and a refused push). Only a PERSON raises a mode: the calibrator (gate_calibrator.py)
can only retire the gate or tighten T0 to T1. Setting a mode is an appended gate_state event: it takes effect only up to the CEILING in shared/config.py (BACKLOG_COLLISION_GATE_MODE: the
effective mode is the lower of the two), so going live needs both: the ceiling raised in a reviewed change, and this event. A write needs a human identity in FONDOS_ACTOR, the owner-only
login (FONDOS_BACKLOG_PG_DSN_HUMAN, typed in this terminal, never kept in .env) and the usual typed confirmation.

Exit codes: 0 ok, 1 the write was refused or failed, 2 usage error (no database, the collision store is not applied, a bad argument).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RC_OK, RC_FAILED, RC_USAGE = 0, 1, 2


def show_lines(ceiling: str, m: dict, queue: list) -> list:
    """The read-only summary. Pure. `m`: backlog_client.gate_measurements(); `queue`: backlog_client.gate_queue()."""
    st = m["state"]
    audit_days = None if m["audit_start"] is None else round((m["now"] - m["audit_start"]).total_seconds() / 86400, 1)
    share = m["share_3d"]
    lines = [f"collision gate: ceiling {ceiling} (shared/config.py), effective {st['mode']} {st['tier']}",
             f"  audit clock: {'not started' if audit_days is None else f'day {audit_days}'}; share of commits introducing a new pair (3 days): {'no evaluation yet' if share is None else f'{share:.1%}'}",
             f"  undecided pairs: {len(queue)}" + (f" ({sum(1 for r in queue if r['level'] == 'ALERTA')} over 96 h, {sum(1 for r in queue if r['level'] == 'ATENCION')} over 48 h)" if queue else "")]
    if st["mode"] != "BLOCK":
        need = []
        if audit_days is None or audit_days < 14:
            need.append(f"14 days of AUDIT ({'not started' if audit_days is None else f'{audit_days} so far'})")
        if share is None or share > 0.10:
            need.append(f"a share of 10 % or less ({'no data' if share is None else f'{share:.1%} now'})")
        lines.append("  BLOCK needs: " + ("; ".join(need) if need else "nothing more: the database would accept it"))
    return lines


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--set", choices=("OFF", "AUDIT", "BLOCK"), help="append a gate_state event with this mode (default: only show)")
    ap.add_argument("--tier", choices=("T0", "T1"), help="the rule tier of the event (default: the one in force)")
    ap.add_argument("--reason", help="why (10 or more characters)")
    args = ap.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    import shared.config as cfg
    from shared import backlog_client as bc

    ceiling = cfg.BACKLOG_COLLISION_GATE_MODE if cfg.BACKLOG_COLLISION_GATE_MODE in bc.COLLISION_MODES else "OFF"
    try:
        if not bc.collision_store_present():
            print("the collision store is not applied (owner: python scripts/ops/migrate_backlog_v2.py --phase collision)", file=sys.stderr)
            return RC_USAGE
        m = bc.gate_measurements(ceiling)
        queue = bc.gate_queue()
    except Exception as exc:                                  # never echo a DSN
        print(f"backlog unreachable: {type(exc).__name__}", file=sys.stderr)
        return RC_USAGE
    print("\n".join(show_lines(ceiling, m, queue)))
    if not args.set:
        return RC_OK
    if not args.reason or len(args.reason.strip()) < 10:
        print("--set needs --reason of 10 or more characters", file=sys.stderr)
        return RC_USAGE
    refusal = bc.require_live_write_cli(bc.dsn_for_actor("human:x") if _human_dsn_ready(bc) else None)
    if refusal is not None:
        return refusal
    try:
        event_id = bc.set_collision_mode(args.set, args.tier or m["state"]["tier"], args.reason.strip(), ceiling, evidence={"ceiling": ceiling, "before": m["state"]})
    except Exception as exc:
        print(f"REFUSED: {type(exc).__name__}: {str(exc)[:300]}", file=sys.stderr)
        return RC_FAILED
    print(f"\nAPPLIED: gate_state event {event_id} ({args.set} {args.tier or m['state']['tier']}). The effective mode is the lower of this and the ceiling {ceiling}.")
    return RC_OK


def _human_dsn_ready(bc) -> bool:
    try:
        return bool(bc.dsn_for_actor(bc.current_actor()))
    except Exception:                                         # noqa: BLE001  (no identity, or no owner-only DSN: the write below says so)
        return False


if __name__ == "__main__":
    sys.exit(main())
