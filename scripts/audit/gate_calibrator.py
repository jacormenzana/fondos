#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
gate_calibrator.py -- the calibrator and the automatic retirement of the collision gate (Backlog v2 Phase C slice 3, FND-0334; PROPUESTA_BACKLOG_V2.md 6.4.5; NORMAS_BACKLOG.md 4.6).

    python scripts/audit/gate_calibrator.py                  # DRY RUN: the measurements and what the calibrator would do; writes nothing
    python scripts/audit/gate_calibrator.py --apply          # appends the gate_state event it decided on (actor system:calibrator), after the usual typed confirmation
    python scripts/audit/gate_calibrator.py --json           # the evaluation as JSON

It does exactly two things and only ever TIGHTENS:
  * TIGHTEN. On day 10 of AUDIT or later, if the share of the commits evaluated in the last 3 days that introduced a new undecided pair is above 10 %, the rule moves from T0 to T1
    (at least 2 shared code files). One event; never repeated, never undone by the calibrator.
  * RETIRE. Windows start at the latest gate_state event (the audit start, a tightening, a re-enabling) and need 14 days. A verdict needs at least 15 adjudicated pairs and 10 newly
    confirmed OVERLAPS in the window: below that the window extends (at most 42 days, then "insufficient data" is reported and NOTHING is retired). With the sample: precision (adjudicated
    pairs decided OVERLAPS, DEPENDS_ON or REVERT, rather than RELATED or NONE) below 30 %, or a recall floor (the share of the OVERLAPS confirmed by a person elsewhere, not through the
    gate, that the gate had already proposed) below 5 %, sets the gate to OFF with the numbers as evidence. Both floors count in AUDIT and in BLOCK.
It never lowers BLOCK to AUDIT and never raises anything: re-enabling is an event written by a person, with a reason (sp_set_collision_mode refuses the calibrator anything else).
A retirement prints `[ALERTA]` on its first line. The effective mode is the lower of BACKLOG_COLLISION_GATE_MODE and the latest gate_state event, so no repository file is edited.

Writing to the live backlog is done by a person at a terminal (the confirmation phrase); the identity of this process is system:calibrator.

Exit codes: 0 ok (or dry run, or nothing to do), 1 the write failed or was refused, 2 usage error (no database, the collision store is not applied), 3 a retirement was DECIDED (dry run) or
APPLIED, so a script can raise the alarm.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ACTOR = "system:calibrator"
RC_OK, RC_FAILED, RC_USAGE, RC_RETIRED = 0, 1, 2, 3
TIGHTEN_AFTER_DAYS, SHARE_LIMIT = 10, 0.10
WINDOW_DAYS, MAX_WINDOW_DAYS = 14, 42
MIN_ADJUDICATED, MIN_CONFIRMED = 15, 10
MIN_PRECISION, MIN_RECALL = 0.30, 0.05


def evaluate(state: dict, now: datetime, audit_start, anchor, share3, window: dict) -> dict:
    """What the calibrator decides, from numbers only. Pure.

    state: {'mode', 'tier'} (effective); audit_start / anchor: aware datetimes or None (the audit start; the latest gate_state event); share3: share of the last 3 days (0..1) or None;
    window: {'adjudicated', 'positive', 'confirmed', 'flagged'} counted since the anchor (the last 42 days at most).
    Returns {'action': 'NONE' | 'TIGHTEN' | 'RETIRE', 'verdict': ..., 'reason': ..., 'evidence': {...}}."""
    evidence = {"mode": state["mode"], "tier": state["tier"], "share_3d": share3,
                "audit_days": None if audit_start is None else round((now - audit_start).total_seconds() / 86400, 1),
                "window_days": None if anchor is None else round((now - anchor).total_seconds() / 86400, 1), **window}
    if state["mode"] == "OFF":
        return {"action": "NONE", "verdict": "OFF", "reason": "the gate is OFF: nothing to calibrate (re-enabling is a person's event)", "evidence": evidence}
    if audit_start is None:
        return {"action": "NONE", "verdict": "NO_DATA", "reason": "no audit has started (no AUDIT event of a person and no evaluation of the rule yet)", "evidence": evidence}
    # 1. retirement first: a gate that fails its floors must not be tightened into a stricter version of itself
    elapsed = (now - (anchor or audit_start)).total_seconds() / 86400
    if elapsed >= WINDOW_DAYS:
        adjudicated, confirmed = window["adjudicated"], window["confirmed"]
        if adjudicated < MIN_ADJUDICATED or confirmed < MIN_CONFIRMED:
            if elapsed >= MAX_WINDOW_DAYS:
                return {"action": "NONE", "verdict": "INSUFFICIENT_DATA", "evidence": evidence,
                        "reason": f"{elapsed:.0f} days with {adjudicated} adjudicated pairs (need {MIN_ADJUDICATED}) and {confirmed} confirmed overlaps (need {MIN_CONFIRMED}): reported, nothing is retired"}
            return {"action": "NONE", "verdict": "EXTENDING", "evidence": evidence,
                    "reason": f"window of {elapsed:.0f} days extends: {adjudicated}/{MIN_ADJUDICATED} adjudicated pairs, {confirmed}/{MIN_CONFIRMED} confirmed overlaps"}
        precision = window["positive"] / adjudicated
        recall = window["flagged"] / confirmed
        evidence.update(precision=round(precision, 4), recall_floor=round(recall, 4))
        if precision < MIN_PRECISION or recall < MIN_RECALL:
            why = []
            if precision < MIN_PRECISION:
                why.append(f"precision {precision:.0%} < {MIN_PRECISION:.0%}")
            if recall < MIN_RECALL:
                why.append(f"recall floor {recall:.0%} < {MIN_RECALL:.0%}")
            return {"action": "RETIRE", "verdict": "RETIRE", "evidence": evidence, "reason": "the gate is no longer informative: " + "; ".join(why)}
        verdict = "OK"
    else:
        verdict = "WINDOW_OPEN"
    # 2. tightening
    days = (now - audit_start).total_seconds() / 86400
    if state["mode"] == "AUDIT" and state["tier"] == "T0" and days >= TIGHTEN_AFTER_DAYS and share3 is not None and share3 > SHARE_LIMIT:
        return {"action": "TIGHTEN", "verdict": verdict, "evidence": evidence,
                "reason": f"day {days:.0f} of AUDIT: {share3:.0%} of the commits of the last 3 days introduced a new undecided pair (limit {SHARE_LIMIT:.0%}): T0 -> T1"}
    return {"action": "NONE", "verdict": verdict, "evidence": evidence, "reason": "no change"}


def report_lines(ev: dict, applied: bool = False) -> list:
    """The lines printed for one evaluation; a retirement starts with [ALERTA]. Pure."""
    head = []
    if ev["action"] == "RETIRE":
        head.append(f"[ALERTA] collision gate {'RETIRED' if applied else 'would be RETIRED'}: {ev['reason']}")
    e = ev["evidence"]
    body = [f"collision gate calibrator: {e['mode']} {e['tier']}, audit day {e['audit_days']}, window {e['window_days']} d, share(3 d) {e['share_3d']}",
            f"  window: adjudicated {e.get('adjudicated')}, positive {e.get('positive')}, confirmed overlaps {e.get('confirmed')}, flagged by the gate {e.get('flagged')}"
            + (f"; precision {e['precision']:.0%}, recall floor {e['recall_floor']:.0%}" if "precision" in e else ""),
            f"  verdict {ev['verdict']}: {ev['reason']}"]
    return head + body


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="append the decided gate_state event (default: dry run)")
    ap.add_argument("--json", action="store_true", help="print the evaluation as JSON")
    args = ap.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    os.environ["FONDOS_ACTOR"] = ACTOR                        # one process, one identity: this process IS the calibrator
    import shared.config as cfg
    from shared import backlog_client as bc

    ceiling = cfg.BACKLOG_COLLISION_GATE_MODE if cfg.BACKLOG_COLLISION_GATE_MODE in bc.COLLISION_MODES else "OFF"
    try:
        if not bc.collision_store_present():
            print("the collision store is not applied (owner: python scripts/ops/migrate_backlog_v2.py --phase collision)", file=sys.stderr)
            return RC_USAGE
        m = bc.gate_measurements(ceiling)
    except Exception as exc:                                  # never echo a DSN
        print(f"backlog unreachable: {type(exc).__name__}", file=sys.stderr)
        return RC_USAGE
    now = datetime.now(timezone.utc)
    ev = evaluate(m["state"], now, m["audit_start"], m["anchor"], m["share_3d"], m["window"])
    if args.json:
        print(json.dumps(ev, indent=2, default=str))
    else:
        print("\n".join(report_lines(ev)))
    if ev["action"] == "NONE":
        return RC_OK
    mode, tier = ("OFF", m["state"]["tier"]) if ev["action"] == "RETIRE" else (m["state"]["mode"], "T1")
    if not args.apply:
        print(f"\nDRY RUN: nothing written. --apply would append gate_state {mode} {tier} as {ACTOR}.")
        return RC_RETIRED if ev["action"] == "RETIRE" else RC_OK
    refusal = bc.require_live_write_cli()
    if refusal is not None:
        return refusal
    try:
        event_id = bc.set_collision_mode(mode, tier, ev["reason"], ceiling, evidence=ev["evidence"], actor=ACTOR)
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}: {str(exc)[:300]}", file=sys.stderr)
        return RC_FAILED
    print(f"\nAPPLIED: gate_state event {event_id} ({mode} {tier}).")
    return RC_RETIRED if ev["action"] == "RETIRE" else RC_OK


if __name__ == "__main__":
    sys.exit(main())
