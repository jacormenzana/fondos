# scripts/launch/p3_monthly_draft.py
# -*- coding: utf-8 -*-
"""The capture check and the record of the scheduled monthly P3 build (FND-0447). Owner standing order 2026-11-26: scheduled monthly builds are
authorised strictly in DRAFT-only mode. They generate the DRAFT and the shadow rows and never promote to PRODUCTION without the owner's explicit order.
This helper builds nothing and promotes nothing: `P3_monthlyDraft.bat` asks it whether this calendar month still needs a build, runs `P3_runCycle.bat`
(DRAFT, the P2 audit after it, the DRAFT report) when it does, and asks it to record the outcome.

    python scripts/launch/p3_monthly_draft.py status            exit 0 = build, 3 = captured (EXIT_CAPTURED), 7 = a shadow stream still has no row after the month's attempts
                                                                (EXIT_GAVE_UP), 8 / 9 / 10 = retry only the audit / the draft report / both (no build)
    python scripts/launch/p3_monthly_draft.py record --rc N [--followup]
                                                                one ingestion_log row (step P3_MONTHLY_DRAFT); exit 0, or 5 (EXIT_NOT_CAPTURED) when the run
                                                                ended RC 0 and something required is still missing this month

What "captured" means (one calendar month in UTC, the month rule of the R6 reader, scripts/audit/p3/p3_shadow_read.py::month_of): a scenario was built this
month (portfolio_scenarios.built_at) AND every shadow stream whose switch is ON wrote a row whose `built_at` falls in this month. A month with a row is never
rebuilt by the schedule, so the task can fire every day: it builds once and then does nothing until the next month, and a failed day is retried the next.

FND-0454: (item 5) after MAX_STREAM_ATTEMPTS builds of the month that still lacked a stream's row, the month is not rebuilt again (each rebuild moves the month's
counted row); the task ends with EXIT_GAVE_UP so the failure stays visible, and a build by hand still captures it. (item 7) The month's DRAFT also needs its promotion
evidence (a persisted p2 audit newer than the build, the gate's own rule) and its draft report: whichever is missing is retried ALONE, without a build.
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
EXIT_GAVE_UP = 7           # status: a stream still has no row after MAX_STREAM_ATTEMPTS builds; nothing to follow up
EXIT_NEEDS_AUDIT = 8       # status: only the audit of the month's DRAFT is missing
EXIT_NEEDS_REPORT = 9      # status: only its report is missing
EXIT_NEEDS_BOTH = 10       # status: both are
MAX_STREAM_ATTEMPTS = 3    # builds of one month that may end without a stream's row before the schedule stops rebuilding
AUDIT_ITEM, REPORT_ITEM = "AUDIT", "REPORT"
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


def followups(draft, audit_ts, has_report: bool) -> list:
    """What the month's newest scenario still lacks. `draft` = (scenario_id, built_at, status) or None; only a DRAFT is followed up (once promoted the owner has taken it over)."""
    if draft is None or draft[2] != "DRAFT":
        return []
    out = [] if audit_ts is not None and audit_ts > draft[1] else [AUDIT_ITEM]
    return out if has_report else out + [REPORT_ITEM]


def plan(missing: list, follow: list, attempts: int) -> int:
    """The exit code of `status`: build (0) while the DRAFT is missing, or a stream is and attempts remain; else the follow-ups; else gave up; else captured."""
    if missing and (DRAFT_ITEM in missing or attempts < MAX_STREAM_ATTEMPTS):
        return 0
    if follow:
        return EXIT_NEEDS_BOTH if len(follow) > 1 else (EXIT_NEEDS_AUDIT if follow == [AUDIT_ITEM] else EXIT_NEEDS_REPORT)
    return EXIT_GAVE_UP if missing else EXIT_CAPTURED


def count_stream_attempts(messages: list) -> int:
    """How many of the stored INCOMPLETE records were BUILDS that still lacked a shadow stream's row (a record without `kind` was a build)."""
    streams = {step for _switch, step in SHADOW_STREAMS}
    n = 0
    for text in messages:
        try:
            body = json.loads(text)
            n += body.get("kind", "build") == "build" and bool(streams & set(body.get("missing") or ()))
        except (ValueError, TypeError, AttributeError):
            continue
    return n


def report_exists(scenario_id: str, folder) -> bool:
    """A draft report names its scenario (proyecto3/src/monthly_report.py)."""
    return any(Path(folder).glob(f"informe_cartera_*_{scenario_id}.xlsx"))


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


def write_record(conn, status: str, message: dict, step: str = STEP) -> None:
    """One append-only ingestion_log row of a scheduled job (this one's step by default; p1p2_monthly_refresh.py passes its own)."""
    conn.execute("INSERT INTO ingestion_log (isin, step, status, message, created_at) VALUES (NULL, %s, %s, %s, now())", (step, status, json.dumps(message)))
    conn.commit()


def followup_items(conn, now: datetime) -> list:
    """`followups` of the newest scenario built this month: its audit evidence by the promotion gate's own selection (P#11) and its draft report on disk."""
    row = conn.execute("SELECT scenario_id, built_at, status FROM portfolio_scenarios WHERE built_at >= %s ORDER BY built_at DESC LIMIT 1", (month_start_utc(now),)).fetchone()
    if row is None:
        return []
    from shared.config import AUDIT_COMPLETENESS_CONTRACT_ENABLED, REPORT_DIRS
    from shared.statistical_audit.gating import select_evidence_run
    from proyecto3.src.monthly_report import DRAFTS_SUBDIR
    run, _note = select_evidence_run(conn, "p2_metrics", AUDIT_COMPLETENESS_CONTRACT_ENABLED)
    return followups((row[0], row[1], row[2]), None if run is None else run[1], report_exists(row[0], REPORT_DIRS["p3"] / DRAFTS_SUBDIR))


def stream_attempts(conn, now: datetime) -> int:
    rows = conn.execute("SELECT message FROM ingestion_log WHERE step = %s AND status = 'INCOMPLETE' AND created_at >= %s", (STEP, month_start_utc(now))).fetchall()
    return count_stream_attempts([r[0] for r in rows])


def _state(conn, now: datetime) -> "tuple[str, list, list]":
    import shared.config as cfg
    required = required_items(cfg)
    month = month_of(now)
    return month, required, missing_items(months_by_item(conn, required, now), required, month)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Capture check and record of the scheduled monthly DRAFT-only P3 build (FND-0447).")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="exit 0 = build, 3 = captured, 7 = gave up on a stream, 8/9/10 = retry audit / report / both")
    rec = sub.add_parser("record", help="write the outcome row of this attempt")
    rec.add_argument("--rc", type=int, required=True, help="the exit code of P3_runCycle.bat (or of the follow-up)")
    rec.add_argument("--followup", action="store_true", help="this attempt retried the audit / report without building")
    args = p.parse_args(argv)

    from shared.db import get_connection
    now = datetime.now(timezone.utc)
    conn = get_connection()
    try:
        month, required, missing = _state(conn, now)
        follow = followup_items(conn, now)
        if args.cmd == "status":
            code = plan(missing, follow, stream_attempts(conn, now) if missing else 0)
            text = {0: f"build needed, missing {missing} (required {required})", EXIT_CAPTURED: f"already captured ({required}); nothing to build",
                    EXIT_GAVE_UP: f"{missing} still without a row after {MAX_STREAM_ATTEMPTS} builds this month; not rebuilding (build by hand once the stream is fixed)"}
            print(f"[P3_MONTHLY_DRAFT] {month}: " + text.get(code, f"the month's DRAFT lacks {follow}; retrying that alone, no build"))
            return code
        status, code = outcome(args.rc, missing + follow)
        write_record(conn, status, {"v": ROW_VERSION, "month": month, "build_rc": args.rc, "required": required, "missing": missing + follow,
                                    "kind": "followup" if args.followup else "build", "at": now.isoformat(timespec="seconds")})
        print(f"[P3_MONTHLY_DRAFT] {month}: {status} (RC {args.rc}; missing {missing + follow or 'nothing'})")
        return code
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
