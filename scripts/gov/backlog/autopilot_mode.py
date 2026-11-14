#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
autopilot_mode.py -- the owner's command to read and set the mode of the backlog autopilot (Backlog v3.1, FND-0355; NORMAS_BACKLOG.md section 2, phase `events`).

    python scripts/audit/autopilot_mode.py                                          # --show: the ceiling, the database mode, the effective mode and the track record (read-only)
    python scripts/audit/autopilot_mode.py --set ON --reason "owner decision 2026-11-01: ..."
    python scripts/audit/autopilot_mode.py --set SHADOW --reason "..."              # back to notes only
    python scripts/audit/autopilot_mode.py --set OFF --reason "..."                 # off; only a person can lift an OFF

The mode is an appended event (backlog.autopilot_mode_event). The autopilot itself moves only SHADOW <-> ON, by its own track record (20 uncontradicted SHADOW decisions to promote, two reopened
closures in 30 days to demote); a person sets any mode at any time, which is what this command does. A person's ON skips the 20-decision evidence on purpose and is recorded as that person's
decision, with the reason. The effective mode of a run is the lowest of the ceiling in shared/config.py (BACKLOG_AUTOPILOT_MODE), this event and --mode. A write needs a human identity in
FONDOS_ACTOR (set FONDOS_ACTOR=human:<login>), the owner-only login (from the owner vault) and the usual typed confirmation; a shell that is not a real console is refused.

Exit codes: 0 ok, 1 the write was refused or failed, 2 usage error (no database, the events DDL is not applied, a bad argument).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RC_OK, RC_FAILED, RC_USAGE = 0, 1, 2
_RANK = {"OFF": 0, "SHADOW": 1, "ON": 2}


def effective(ceiling: str, stored: str) -> str:
    """The lowest of the configured ceiling and the stored mode (an unknown ceiling is OFF). Pure."""
    top = ceiling if ceiling in _RANK else "OFF"
    cap = stored if stored in _RANK else "SHADOW"
    return top if _RANK[top] <= _RANK[cap] else cap


def show_lines(ceiling: str, rec: dict, promote_after: int, demote_after: int, window_days: int) -> list:
    """The read-only summary. Pure. `rec`: backlog_client.autopilot_track_record()."""
    eff = effective(ceiling, rec["mode"])
    lines = [f"autopilot: ceiling {ceiling} (shared/config.py), database mode {rec['mode']}, effective {eff}",
             f"  track record since the last mode event: {rec['decisions']} SHADOW decision(s) to act (promotion at {promote_after}), {rec['contradicted']} followed by a reopening;",
             f"  autopilot closures reopened in the last {window_days} days: {rec['reopened_closures']} (demotion at {demote_after})"]
    if rec["mode"] == "SHADOW":
        lines.append("  it moves nothing in SHADOW: it writes one note per decision. It switches itself ON after the evidence above, or a person sets ON now (--set ON --reason ...).")
    elif rec["mode"] == "ON":
        lines.append("  it acts: delivery moves, closures, reopenings, unblocks, undefers and the release of system holds.")
    else:
        lines.append("  OFF: a person set it; only a person lifts it.")
    return lines


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--set", choices=("OFF", "SHADOW", "ON"), help="append an autopilot_mode_event with this mode (default: only show)")
    ap.add_argument("--reason", help="why (10 or more characters)")
    args = ap.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    import shared.config as cfg
    from shared import backlog_autopilot as ap_mod
    from shared import backlog_client as bc

    ceiling = cfg.BACKLOG_AUTOPILOT_MODE if cfg.BACKLOG_AUTOPILOT_MODE in _RANK else "OFF"
    try:
        if not bc.events_store_present():
            print("the events store is not applied (owner: the deploy watcher applies it; python scripts/ops/migrate_backlog_v2.py --phase events)", file=sys.stderr)
            return RC_USAGE
        rec = bc.autopilot_track_record(ap_mod.DEMOTE_WINDOW_DAYS)
    except Exception as exc:                                  # never echo a DSN
        print(f"backlog unreachable: {type(exc).__name__}", file=sys.stderr)
        return RC_USAGE
    print("\n".join(show_lines(ceiling, rec, ap_mod.PROMOTE_AFTER, ap_mod.DEMOTE_AFTER, ap_mod.DEMOTE_WINDOW_DAYS)))
    if not args.set:
        return RC_OK
    if not args.reason or len(args.reason.strip()) < 10:
        print("--set needs --reason of 10 or more characters", file=sys.stderr)
        return RC_USAGE
    if args.set == rec["mode"]:
        print(f"the mode is already {args.set}: nothing to do")
        return RC_OK
    refusal = bc.require_live_write_cli(bc.dsn_for_actor("human:x") if _human_dsn_ready(bc) else None)
    if refusal is not None:
        return refusal
    try:
        event_id = bc.set_autopilot_mode(args.set, args.reason.strip())
    except Exception as exc:
        print(f"REFUSED: {type(exc).__name__}: {str(exc)[:300]}", file=sys.stderr)
        return RC_FAILED
    print(f"\nAPPLIED: autopilot_mode_event {event_id} ({rec['mode']} -> {args.set}). The effective mode is the lower of this and the ceiling {ceiling}: {effective(ceiling, args.set)}.")
    return RC_OK


def _human_dsn_ready(bc) -> bool:
    try:
        return bool(bc.dsn_for_actor(bc.current_actor()))
    except Exception:                                         # noqa: BLE001  (no identity, or no owner-only DSN: the write below says so)
        return False


if __name__ == "__main__":
    sys.exit(main())
