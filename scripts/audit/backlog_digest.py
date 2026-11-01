#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
backlog_digest.py -- the weekly read-only digest of the backlog (Backlog v3.1, FND-0355; NORMAS_BACKLOG.md section 2). It asks nothing: it reports what happened and how much of it a person did.

    python scripts/audit/backlog_digest.py             # print this week's digest (reads only)
    python scripts/audit/backlog_digest.py --write     # write out/backlog_digest/<ISO week>.md unless this week's already exists (the autopilot run does this by itself)

The number that matters is the first one under "Who moved tickets": how many of the week's transitions a PERSON made. The plan's goal is that people appear only as events (new tickets, priorities,
reopenings), so that share is the measure of the administrative load. It reads the live backlog through the reader login, writes nothing to it, and a block whose objects are not applied yet is shown as
"not available" instead of failing.
Exit codes: 0 ok, 2 the backlog is unreachable.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "out" / "backlog_digest"
RC_OK, RC_USAGE = 0, 2


def week_key(d: date) -> str:
    """The ISO week of `d` as YYYY-Www. Pure."""
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _n(v) -> str:
    return "not available" if v is None else str(v)


def render(facts: dict, record: dict | None, a2: dict | None, violations: dict | None, today: date, days: int = 7) -> str:
    """The digest as markdown. Pure. `facts`: backlog_client.digest_facts(); `record`: autopilot_track_record(); `a2`: shadow_progress(); `violations`: violation_summary()."""
    lines = [f"# Backlog digest {week_key(today)}", "", f"Generated {today.isoformat()} from the live backlog, last {days} days. Read-only; it asks nothing.", ""]
    moves = facts.get("moves_by_actor_kind")
    lines.append("## Who moved tickets")
    if moves is None:
        lines.append("- not available")
    else:
        total = sum(v["n"] for v in moves.values())
        human = moves.get("human", {}).get("n", 0)
        lines.append(f"- **{human} of {total} transitions were made by a person**" + (f" ({human / total:.0%})" if total else ""))
        for kind in ("agent", "system", "human"):
            if kind in moves:
                lines.append(f"- {kind}: {moves[kind]['n']}" + (f" (forced: {moves[kind]['forced']})" if moves[kind]["forced"] else ""))
    lines += ["", "## Reopenings"]
    rc = facts.get("reopens_by_cause")
    lines.append("- not available" if rc is None else ("- none" if not rc else "- " + ", ".join(f"{cause}: {n}" for cause, n in rc.items())))
    lines += ["", "## Autopilot"]
    if record is None:
        lines.append("- not available")
    else:
        lines.append(f"- mode {record['mode']}; since the last mode event: {record['decisions']} decision(s) to act, {record['contradicted']} followed by a reopening; "
                     f"autopilot closures reopened in 30 days: {record['reopened_closures']}")
    lines += ["", "## Tickets by status"]
    st = facts.get("by_status")
    lines.append("- not available" if st is None else "- " + ", ".join(f"{k}: {v}" for k, v in st.items()))
    lines += ["", "## Worth a look (nothing here needs approval)",
              f"- IN_PROGRESS tickets idle for 14 days or more: {_n(facts.get('idle_in_progress'))}",
              f"- open holds: {_n(facts.get('open_holds'))}; undecided collision pairs: {_n(facts.get('undecided_pairs'))} (agents decide them)",
              f"- DEPLOY facts recorded this week: {_n(facts.get('deploy_facts'))}"]
    if violations and violations.get("applied"):
        lines.append(f"- violations on record: {violations['total']}" + (" (" + ", ".join(f"{r['code']} {r['n']}" for r in violations["top"]) + ")" if violations["top"] else ""))
    if a2:
        lines.append(f"- A-2 gate (`revoke`): {a2['live_non_system_since_last_v00']} of 50 non-system transitions since the last direct write; {a2['v00_total']} direct write(s) in total")
    return "\n".join(lines) + "\n"


def write_if_due(build, today: date, out_dir: Path = OUT_DIR) -> Path | None:
    """Write the digest of `today`'s ISO week to out_dir unless that week's file exists; returns the path written, else None. `build()` returns the text. Not pure: it writes a file."""
    path = Path(out_dir) / f"{week_key(today)}.md"
    if path.exists():
        return None
    text = build()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def build_text(bc, today: date) -> str:
    """Read everything the digest shows. A block that cannot be read is passed as None."""
    def attempt(fn):
        try:
            return fn()
        except Exception:                                   # noqa: BLE001 -- an object of an unapplied phase, or a hiccup: the block says "not available"
            return None

    facts = bc.digest_facts()
    return render(facts, attempt(bc.autopilot_track_record), attempt(bc.shadow_progress), attempt(bc.violation_summary), today)


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help="write out/backlog_digest/<ISO week>.md unless it exists")
    args = ap.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    import shared.config  # noqa: F401 -- loads .env
    from shared import backlog_client as bc
    today = date.today()
    try:
        if args.write:
            path = write_if_due(lambda: build_text(bc, today), today)
            print(f"digest written: {path}" if path else f"digest of {week_key(today)} already exists: nothing written")
        else:
            print(build_text(bc, today))
    except Exception as exc:                                # noqa: BLE001 -- never echo a DSN
        print(f"backlog unreachable: {type(exc).__name__}", file=sys.stderr)
        return RC_USAGE
    return RC_OK


if __name__ == "__main__":
    sys.exit(main())
