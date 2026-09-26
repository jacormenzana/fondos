#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
p1p2_state.py — orchestration state for P1_P2_Complete.bat (resume guard + run telemetry).

Why a Python helper: the launcher is a batch file, and batch is a poor place for state logic (it
cannot be unit-tested, and it is Windows-only). The batch file stays thin and only calls this
module; everything decidable lives here and is covered by tests/test_p1p2_state.py.

The four steps of the chain re-run safely: every write in them is an idempotent upsert, a full
replace (DELETE then INSERT) or an append-only log (tests/test_sql_explain_sweep_pg.py enforces it).
So after a failure the operator fixes the cause and resumes with `--from N`, which re-runs step N
IN FULL. That is only safe when N is the step that failed (or an earlier one), never a later one:
resuming later would skip a step whose output the later ones consume. This module enforces it.

State file (default proyecto1/log/P1_P2_Complete.state, override with env P1P2_STATE_FILE):
    LAST_RESULT=OK|FAILED
    FAILED_STEP=N            (0 when OK)
    STAMP=YYYYMMDD_HHMMSS
    LAST_FROM_ANY=0|1        (1 = the last run used the --from-any override)
    BASELINE_RUN_ID=<id>     (statistical-audit baseline of the chain, so a resumed run compares to it)

Sub-commands (exit codes):
    check  [--from N] [--from-any]      0 allowed | 2 refused | 3 state path not writable | 4 bad args
    write  --result OK|FAILED --failed-step N --stamp S [--from-any-used]
    step   --step N --rc R --start HHMMSS --end HHMMSS     always exits 0 (best-effort telemetry)
    baseline                            prints BASELINE_RUN_ID of the last run (empty if none)
    oc-before                           snapshot of the funds whose ongoing charge equals ACI_RHP
    oc-newly                            prints, comma-separated, the funds contaminated SINCE oc-before

The file is the guard because it must work when the database is down. The database is used only for
telemetry (ingestion_log rows, and a backlog ticket on failure) and every database call here is
best-effort: it prints and moves on, it never changes an exit code.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATE = ROOT / "proyecto1" / "log" / "P1_P2_Complete.state"
STEPS = (1, 2, 3, 4)
STEP_NAMES = {1: "P1_refreshBenchmarks", 2: "P1_discoverAllFunds",
              3: "P2_discoverLoadMetrics", 4: "P2_calculateIndicators"}

RC_OK, RC_REFUSED, RC_NOT_WRITABLE, RC_BAD_ARGS = 0, 2, 3, 4


def state_path() -> Path:
    override = os.environ.get("P1P2_STATE_FILE")
    return Path(override) if override else DEFAULT_STATE


def read_state(path: Path | None = None) -> dict | None:
    """The parsed state, or None when the file is missing, unreadable or malformed."""
    p = path or state_path()
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return None
    state: dict = {}
    for line in text.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            state[k.strip()] = v.strip()
    try:
        if state.get("LAST_RESULT") not in ("OK", "FAILED"):
            return None
        state["FAILED_STEP"] = int(state.get("FAILED_STEP", ""))
        state["LAST_FROM_ANY"] = int(state.get("LAST_FROM_ANY", "0"))
    except ValueError:
        return None
    state.setdefault("BASELINE_RUN_ID", "")
    return state


def is_writable(path: Path) -> bool:
    """Open for append and close; writes nothing (an existing file is left byte-for-byte alone)."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8"):
            pass
        return True
    except OSError:
        return False


def write_state(result: str, failed_step: int, stamp: str, from_any_used: bool,
                path: Path | None = None, baseline_id: str = "") -> None:
    """Atomic: a temp file in the same directory, then os.replace. A kill mid-write can never leave
    a half-written state file, which would read as malformed and (fail closed) block a resume."""
    p = path or state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    body = (f"LAST_RESULT={result}\nFAILED_STEP={failed_step}\nSTAMP={stamp}\n"
            f"LAST_FROM_ANY={1 if from_any_used else 0}\nBASELINE_RUN_ID={baseline_id}\n")
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=p.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(body)
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def check(from_step: int | None, from_any: bool, path: Path | None = None) -> tuple[int, str]:
    """(exit code, message). Decides whether this run may proceed."""
    p = path or state_path()
    if not is_writable(p):
        return RC_NOT_WRITABLE, (f"[ERROR] cannot write the state file {p}: aborting before the run "
                                 "rather than after 30+ minutes of work. Fix the path or its permissions.")
    state = read_state(p)
    notes = []
    if state is not None and state["LAST_FROM_ANY"] == 1 and from_step is None:
        notes.append("[WARN] the previous run used --from-any (it bypassed the resume guard). "
                     "Check that its intermediate data is consistent.")
    if from_step is None:
        return RC_OK, "\n".join(notes)

    if from_step not in STEPS:
        return RC_BAD_ARGS, f"[ERROR] --from expects a step number 1-4, got {from_step!r}"
    if from_any:
        return RC_OK, ("[WARN] --from-any: the resume guard is bypassed; step "
                       f"{from_step} will run without checking the last run's outcome.")
    if state is None:
        return RC_REFUSED, (f"[REFUSED] --from {from_step}: no readable state from a previous run "
                            f"({p}). Nothing proves a step failed there, so resuming could skip a "
                            "step whose output later steps consume. Run the whole chain, or use "
                            "--from-any if you are sure.")
    if state["LAST_RESULT"] == "OK":
        return RC_REFUSED, (f"[REFUSED] --from {from_step}: the last run finished OK "
                            f"({state.get('STAMP', '?')}); there is nothing to resume. Run the whole "
                            "chain, or use --from-any.")
    if from_step > state["FAILED_STEP"]:
        return RC_REFUSED, (f"[REFUSED] --from {from_step}: the last run failed at step "
                            f"{state['FAILED_STEP']} ({STEP_NAMES.get(state['FAILED_STEP'], '?')}); "
                            f"resume from {state['FAILED_STEP']} or earlier, never later. "
                            "Or use --from-any.")
    return RC_OK, ""


# ── ongoing-charge contamination: repair only what THIS chain's P1 pass broke ────────────────────
# A P1 pass re-extracts costs for some cached funds and can write ongoing_charge_recurrent = ACI_RHP
# (the KID's annual cost impact, not the ongoing charge). Verified 2026-09-26 with the committed HEAD
# code: pre-existing behaviour, 4 funds newly contaminated (91 -> 95 on live), two of which held
# values that match their KID text. `run_block.py --recompute-costs` (cache-only, no downloads)
# restores them exactly. This snapshots the contaminated set before PASO 2 and, after it, repairs
# ONLY the funds contaminated since: the ~91 pre-existing ones are left exactly as they are.

# Same predicate as pipeline.py's OC-CONTAMINATION-GUARD (tolerance 0.01 percentage points).
_OC_SQL = ("SELECT ISIN FROM fund_master WHERE In_Current_Universe = 1 "
           "AND Ongoing_Charge_Recurrent IS NOT NULL AND ACI_RHP IS NOT NULL "
           "AND ABS(Ongoing_Charge_Recurrent * 100 - ACI_RHP) <= 0.01")


def oc_file() -> Path:
    override = os.environ.get("P1P2_OC_FILE")
    return Path(override) if override else ROOT / "proyecto1" / "log" / "P1_P2_Complete.oc_before"


def contaminated_isins() -> set | None:
    """ISINs whose ongoing charge equals ACI_RHP right now; None if the database cannot be read."""
    try:
        sys.path.insert(0, str(ROOT))
        from shared.db import get_connection
        conn = get_connection()
        try:
            return {r[0] for r in conn.execute(_OC_SQL).fetchall()}
        finally:
            conn.close()
    except Exception as exc:
        print(f"[p1p2_state] ongoing-charge snapshot skipped: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None


def oc_before(path: Path | None = None) -> bool:
    """Write the pre-PASO-2 contaminated set. On failure remove any old file so a stale snapshot
    can never be mistaken for this run's."""
    p = path or oc_file()
    found = contaminated_isins()
    if found is None:
        try:
            p.unlink()
        except OSError:
            pass
        return False
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(sorted(found)) + ("\n" if found else ""), encoding="utf-8")
    return True


def oc_newly(path: Path | None = None) -> list:
    """Funds contaminated now but not in the pre-PASO-2 snapshot. Empty (repair nothing) when there is
    no snapshot or the database cannot be read: never guess."""
    p = path or oc_file()
    try:
        before = {ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()}
    except OSError:
        return []
    now = contaminated_isins()
    return sorted(now - before) if now is not None else []


# ── best-effort telemetry ────────────────────────────────────────────────────────────────────────

def _seconds(start: str, end: str) -> int | None:
    try:
        a = datetime.strptime(start, "%H%M%S")
        b = datetime.strptime(end, "%H%M%S")
    except ValueError:
        return None
    diff = int((b - a).total_seconds())
    return diff if diff >= 0 else diff + 86400          # crossed midnight


def log_event(status: str, message: str) -> bool:
    """One ingestion_log row (step='P1P2_ORCH'). Best-effort: returns False and prints on failure."""
    try:
        sys.path.insert(0, str(ROOT))
        sys.path.insert(0, str(ROOT / "proyecto1"))
        from shared.db import get_connection
        from core.sqlite_writer import log_ingestion
        conn = get_connection()
        try:
            log_ingestion(conn, None, "P1P2_ORCH", status, message[:500])
            conn.commit()
        finally:
            conn.close()
        return True
    except Exception as exc:                            # never change an exit code for telemetry
        print(f"[p1p2_state] telemetry row skipped: {type(exc).__name__}: {exc}", file=sys.stderr)
        return False


def report_failure(step: int, rc: int, secs: int | None) -> None:
    """Open (or update, within 24 h) a backlog ticket for a failed step. Inert without
    FONDOS_BACKLOG_PG_DSN; best-effort like everything here."""
    try:
        sys.path.insert(0, str(ROOT))
        import shared.config  # noqa: F401  (loads .env, so FONDOS_BACKLOG_PG_DSN is visible)
        from shared.backlog_client import report_incident
        report_incident(
            object_name=f"P1_P2_Complete.bat PASO {step} ({STEP_NAMES.get(step, '?')})",
            scenario_description=(f"PASO {step} ({STEP_NAMES.get(step, '?')}) failed with RC={rc}"
                                  + (f" after {secs}s" if secs is not None else "")
                                  + ". See the step's log under proyecto1/log or proyecto2/log."),
        )
    except Exception as exc:
        print(f"[p1p2_state] backlog incident skipped: {type(exc).__name__}: {exc}", file=sys.stderr)


# ── CLI ──────────────────────────────────────────────────────────────────────────────────────────

def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check")
    c.add_argument("--from", dest="from_step", type=int, default=None)
    c.add_argument("--from-any", action="store_true")

    w = sub.add_parser("write")
    w.add_argument("--result", choices=("OK", "FAILED"), required=True)
    w.add_argument("--failed-step", type=int, default=0)
    w.add_argument("--stamp", required=True)
    w.add_argument("--from-any-used", action="store_true")
    w.add_argument("--baseline-id", default="")

    sub.add_parser("baseline")
    sub.add_parser("oc-before")
    sub.add_parser("oc-newly")

    s = sub.add_parser("step")
    s.add_argument("--step", type=int, required=True)
    s.add_argument("--rc", type=int, required=True)
    s.add_argument("--start", default="")
    s.add_argument("--end", default="")
    s.add_argument("--from-any-used", action="store_true")

    try:
        args = ap.parse_args(argv)
    except SystemExit:
        return RC_BAD_ARGS

    if args.cmd == "check":
        rc, msg = check(args.from_step, args.from_any)
        if msg:
            print(msg)
        if rc == RC_OK and args.from_any and args.from_step is not None:
            # durable, queryable record of a guard bypass (the orchestration log has it too)
            log_event("FROM_ANY", f"--from-any --from {args.from_step} at {datetime.now():%Y%m%d_%H%M%S}")
        return rc
    if args.cmd == "write":
        write_state(args.result, args.failed_step, args.stamp, args.from_any_used,
                    baseline_id=args.baseline_id)
        return RC_OK
    if args.cmd == "baseline":
        state = read_state()
        print(state["BASELINE_RUN_ID"] if state else "")
        return RC_OK
    if args.cmd == "oc-before":
        oc_before()                                     # best-effort: a failure only disables the repair
        return RC_OK
    if args.cmd == "oc-newly":
        print(",".join(oc_newly()))
        return RC_OK
    # step: telemetry row (+ backlog ticket on failure); never fails the launcher
    secs = _seconds(args.start, args.end)
    status = "STEP_OK" if args.rc == 0 else "STEP_FAILED"
    msg = f"PASO {args.step} rc={args.rc}" + (f" secs={secs}" if secs is not None else "")
    if args.from_any_used:
        msg += " from-any"
    log_event(status, msg)
    if args.rc != 0:
        report_failure(args.step, args.rc, secs)
    return RC_OK


if __name__ == "__main__":
    sys.exit(main())
