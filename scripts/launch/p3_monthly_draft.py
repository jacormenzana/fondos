# scripts/launch/p3_monthly_draft.py
# -*- coding: utf-8 -*-
"""The capture check and the record of the scheduled monthly P3 build (FND-0447). Owner standing order 2026-11-26: scheduled monthly builds are
authorised strictly in DRAFT-only mode. They generate the DRAFT and the shadow rows and never promote to PRODUCTION without the owner's explicit order.
This helper builds nothing and promotes nothing: `P3_monthlyDraft.bat` asks it whether this calendar month still needs a build, runs `P3_runCycle.bat`
(DRAFT, the P2 audit after it, the DRAFT report) when it does, and asks it to record the outcome.

    python scripts/launch/p3_monthly_draft.py status            exit 0 = this month still needs a build, 3 = already captured (EXIT_CAPTURED)
    python scripts/launch/p3_monthly_draft.py record --rc N     one ingestion_log row (step P3_MONTHLY_DRAFT); exit 0, or 5 (EXIT_NOT_CAPTURED) when the build
                                                                ended RC 0 and a required row is still missing this month

What "captured" means (one calendar month in UTC, the month rule of the R6 reader, scripts/audit/p3/p3_shadow_read.py::month_of): a scenario was built this
month (portfolio_scenarios.built_at) AND every shadow stream whose switch is ON wrote a row whose `built_at` falls in this month. A month with a row is never
rebuilt by the schedule, so the task can fire every day: it builds once and then does nothing until the next month, and a failed day is retried the next.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

STEP = "P3_MONTHLY_DRAFT"
ROW_VERSION = 1
EXIT_CAPTURED = 3          # status: nothing to build this month
EXIT_NOT_CAPTURED = 5      # record: the build ended RC 0 but a required row is missing
DRAFT_ITEM = "DRAFT"       # the scenario built this month (always required)

# (switch in shared/config.py, the ingestion_log step of its stream). A stream whose switch is OFF is not required. The baseline stream (FND-0445) is listed
# ahead of its code: until its switch exists, getattr gives False and nothing changes.
SHADOW_STREAMS = (
    ("P3_FEE_PENALTY_SHADOW_ENABLED", "P3_FEE_PENALTY_SHADOW"),
    ("P3_HYSTERESIS_SHADOW_ENABLED", "P3_HYSTERESIS_SHADOW"),
    ("P3_BASELINE_SHADOW_ENABLED", "P3_BASELINE_SHADOW"),
)
ROWS_PER_STREAM = 12       # enough to see the current month: one build a month, a few same-month rebuilds


# ---------------------------------------------------------------------------------------------------------------------------------------------------
# pure
# ---------------------------------------------------------------------------------------------------------------------------------------------------
def required_items(cfg) -> list:
    """The DRAFT plus the step of every shadow stream whose switch is True in `cfg` (a module or any object with the switch attributes)."""
    return [DRAFT_ITEM] + [step for switch, step in SHADOW_STREAMS if getattr(cfg, switch, False) is True]


def month_of(stamp) -> str:
    """'YYYY-MM' of an ISO timestamp string (the reader's rule) or of a datetime, in UTC."""
    if isinstance(stamp, datetime):
        stamp = (stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat()
    return str(stamp)[:7]


def missing_items(months_by_item: dict, required: list, month: str) -> list:
    """The required items with no row in `month` (`months_by_item` = {item: set of 'YYYY-MM'})."""
    return [item for item in required if month not in months_by_item.get(item, set())]


def outcome(build_rc: int, missing: list) -> "tuple[str, int]":
    """(status of the record row, exit code of `record`). A failed build is FAILED and `record` exits 0 (the launcher propagates the build's own code);
    a build that ended 0 without every required row is INCOMPLETE and exits EXIT_NOT_CAPTURED, so the scheduled task's result shows it."""
    if build_rc != 0:
        return "FAILED", 0
    return ("OK", 0) if not missing else ("INCOMPLETE", EXIT_NOT_CAPTURED)


# ---------------------------------------------------------------------------------------------------------------------------------------------------
# database (read the current state; write one row)
# ---------------------------------------------------------------------------------------------------------------------------------------------------
def month_start_utc(now: datetime) -> datetime:
    now = now.astimezone(timezone.utc)
    return datetime(now.year, now.month, 1, tzinfo=timezone.utc)


def months_by_item(conn, required: list, now: datetime) -> dict:
    """{item: set of months with a row}: the DRAFT from portfolio_scenarios.built_at since the start of this month, each stream from its stored rows."""
    from proyecto3.src import hysteresis_shadow as hs
    out = {}
    if DRAFT_ITEM in required:
        n = conn.execute("SELECT COUNT(*) FROM portfolio_scenarios WHERE built_at >= %s", (month_start_utc(now),)).fetchone()[0]
        out[DRAFT_ITEM] = {month_of(now)} if int(n) > 0 else set()
    for step in required:
        if step == DRAFT_ITEM:
            continue
        out[step] = {month_of(r["built_at"]) for r in hs.read_rows(conn, limit=ROWS_PER_STREAM, step=step) if r.get("built_at")}
    return out


def write_record(conn, status: str, message: dict) -> None:
    conn.execute("INSERT INTO ingestion_log (isin, step, status, message, created_at) VALUES (NULL, %s, %s, %s, now())", (STEP, status, json.dumps(message)))
    conn.commit()


def _state(conn, now: datetime) -> "tuple[str, list, list]":
    import shared.config as cfg
    required = required_items(cfg)
    month = month_of(now)
    return month, required, missing_items(months_by_item(conn, required, now), required, month)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Capture check and record of the scheduled monthly DRAFT-only P3 build (FND-0447).")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="exit 0 = this month needs a build, 3 = already captured")
    rec = sub.add_parser("record", help="write the outcome row of this attempt")
    rec.add_argument("--rc", type=int, required=True, help="the exit code of P3_runCycle.bat")
    args = p.parse_args(argv)

    from shared.db import get_connection
    now = datetime.now(timezone.utc)
    conn = get_connection()
    try:
        month, required, missing = _state(conn, now)
        if args.cmd == "status":
            if missing:
                print(f"[P3_MONTHLY_DRAFT] {month}: build needed, missing {missing} (required {required})")
                return 0
            print(f"[P3_MONTHLY_DRAFT] {month}: already captured ({required}); nothing to build")
            return EXIT_CAPTURED
        status, code = outcome(args.rc, missing)
        write_record(conn, status, {"v": ROW_VERSION, "month": month, "build_rc": args.rc, "required": required, "missing": missing,
                                    "at": now.isoformat(timespec="seconds")})
        print(f"[P3_MONTHLY_DRAFT] {month}: {status} (build RC {args.rc}; missing {missing or 'nothing'})")
        return code
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
