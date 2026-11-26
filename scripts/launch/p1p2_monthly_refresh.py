# scripts/launch/p1p2_monthly_refresh.py
# -*- coding: utf-8 -*-
"""The window and freshness check and the record of the scheduled monthly P1+P2 cycle (FND-0448). Owner order 2026-11-26: the monthly NAV refresh is automated
so that the scheduled DRAFT-only P3 build (FND-0447, P3_monthlyDraft.bat) does not fail on stale data. The standing order is extended EXCLUSIVELY to the P1/P2
ingestion and calculation cycle. This helper refreshes nothing itself: `P1P2_monthlyRefresh.bat` asks it whether the cycle is due, runs `P1P2_runCycle.bat` when it
is, and asks it to record the outcome.

    python scripts/launch/p1p2_monthly_refresh.py status            exit 0 = run the cycle, 3 = nothing to do (EXIT_NOTHING_TO_DO: outside the window, or fresh enough)
    python scripts/launch/p1p2_monthly_refresh.py record --rc N     one ingestion_log row (step P1P2_MONTHLY_REFRESH); exit 0, or 6 (EXIT_STILL_STALE) when the cycle
                                                                    ended RC 0 and the data would still fail the P3 gate on the build's last retry day

The rule: from day WINDOW_START_DAY to the end of the month, the cycle runs while the NAV (`nav_p10`) or the harvest would be older than the P3 gate's limits
(shared/config.py P3_FRESHNESS_MAX_AGE_DAYS) on the LAST RETRY DAY of the next monthly build (day 1 + BUILD_RETRY_DAYS of the next month). The ages are the gate's
own arithmetic (proyecto3/src/data_freshness.py). Once the data is fresh enough, the task does nothing for the rest of the month; a day that fails, or a cycle
that leaves the data still too old (the NAV sources lag), is retried the next day.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_ROOT), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

STEP = "P1P2_MONTHLY_REFRESH"
ROW_VERSION = 1
WINDOW_START_DAY = 25      # the owner's window (2026-11-26: "from the 25th"); it runs to the end of the month so a failed day is retried
BUILD_RETRY_DAYS = 3       # the monthly build (FondosP3MonthlyDraft) runs on day 1 and retries daily; the data must still pass on day 1 + 3
EXIT_NOTHING_TO_DO = 3
EXIT_STILL_STALE = 6
CHECKS = ("nav_p10", "harvest")


# ---------------------------------------------------------------------------------------------------------------------------------------------------
# pure
# ---------------------------------------------------------------------------------------------------------------------------------------------------
def in_window(today: date) -> bool:
    return today.day >= WINDOW_START_DAY


def judge_day(today: date) -> date:
    """The last retry day of the next monthly build: day 1 of next month + BUILD_RETRY_DAYS."""
    first_next = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first_next + timedelta(days=BUILD_RETRY_DAYS)


def stale_reasons(newest: dict, limits: dict, on: date) -> list:
    """The checks (of CHECKS) whose newest date would be older than its limit on day `on`; a missing date is stale (no data is the stalest case)."""
    out = []
    for name in CHECKS:
        d = newest.get(name)
        if d is None or (on - d).days > int(limits[name]):
            out.append(f"{name} newest={d} age_on_{on}={None if d is None else (on - d).days}d > {limits[name]}d")
    return out


def outcome(cycle_rc: int, reasons: list) -> "tuple[str, int]":
    """(status of the record row, exit code of `record`). A failed cycle is FAILED and `record` exits 0 (the launcher propagates the cycle's own code);
    a cycle that ended 0 but left the data too old is STALE and exits EXIT_STILL_STALE, so the task's result shows it."""
    if cycle_rc != 0:
        return "FAILED", 0
    return ("OK", 0) if not reasons else ("STALE", EXIT_STILL_STALE)


# ---------------------------------------------------------------------------------------------------------------------------------------------------
# database
# ---------------------------------------------------------------------------------------------------------------------------------------------------
def newest_dates(conn, today: date) -> dict:
    """{check: newest date} for CHECKS, computed with the P3 gate's own functions (no second copy of the percentile or of the harvest stamp)."""
    from proyecto3.src.data_freshness import evaluate_freshness, load_freshness_inputs
    from shared.config import P3_FRESHNESS_MAX_AGE_DAYS, P3_NAV_UNIVERSE_PERCENTILE
    nav_dates, harvest_ts, _versions = load_freshness_inputs(conn)
    checks = evaluate_freshness(nav_dates, {}, harvest_ts, today, P3_FRESHNESS_MAX_AGE_DAYS, P3_NAV_UNIVERSE_PERCENTILE, ())
    return {c.name: c.newest for c in checks if c.name in CHECKS}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Window and freshness check and record of the scheduled monthly P1+P2 cycle (FND-0448).")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="exit 0 = run the cycle, 3 = nothing to do")
    rec = sub.add_parser("record", help="write the outcome row of this attempt")
    rec.add_argument("--rc", type=int, required=True, help="the exit code of P1P2_runCycle.bat")
    args = p.parse_args(argv)

    from shared.config import P3_FRESHNESS_MAX_AGE_DAYS
    import p3_monthly_draft as draft
    now = datetime.now(timezone.utc)
    today = now.astimezone().date()                    # the local calendar day the task fires on
    on = judge_day(today)
    if args.cmd == "status" and not in_window(today):
        print(f"[P1P2_MONTHLY_REFRESH] {today}: outside the window (from day {WINDOW_START_DAY}); nothing to do")
        return EXIT_NOTHING_TO_DO
    from shared.db import get_connection
    conn = get_connection()
    try:
        newest = newest_dates(conn, today)
        reasons = stale_reasons(newest, P3_FRESHNESS_MAX_AGE_DAYS, on)
        if args.cmd == "status":
            if reasons:
                print(f"[P1P2_MONTHLY_REFRESH] {today}: cycle due, the next build (until {on}) would refuse: {reasons}")
                return 0
            print(f"[P1P2_MONTHLY_REFRESH] {today}: fresh enough for the next build (until {on}); nothing to do")
            return EXIT_NOTHING_TO_DO
        status, code = outcome(args.rc, reasons)
        draft.write_record(conn, status, {"v": ROW_VERSION, "day": str(today), "judge_day": str(on), "cycle_rc": args.rc,
                                          "newest": {k: (str(v) if v else None) for k, v in newest.items()}, "stale": reasons,
                                          "at": now.isoformat(timespec="seconds")}, step=STEP)
        print(f"[P1P2_MONTHLY_REFRESH] {today}: {status} (cycle RC {args.rc}; stale {reasons or 'nothing'})")
        return code
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
