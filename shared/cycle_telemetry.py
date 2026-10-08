"""Orchestrated-cycle telemetry (FND-0239): one writer, failure-isolated, opt-in.

What it records (tables: db/pg/35_control.sql, block `cycle_telemetry`): a row per cycle and per step attempt (duration, rc, status),
typed scalar metrics, audit runs, and flags linked to the backlog. The executive view is control.v_cycle_exec.

Design (the three review rounds of the 2026-10-05 plan):
  * FAILURE ISOLATION. Every public function catches `Exception` (never BaseException: Ctrl-C / SystemExit must still stop a hung cycle),
    connects with connect_timeout=3 s and statement_timeout=3 s, opens one short connection per emit, holds no transaction across a step
    and never retries. A failed write is appended to a JSONL fallback file instead. The functions never raise and never change a launcher RC.
  * OPT-IN. Nothing is written unless FONDOS_CYCLE_ID is set (the launcher library exports it); without it every call returns False at once.
  * IDEMPOTENT EVENTS. Every write is an event applied as a PK upsert, so the fallback can be replayed any number of times (begin_cycle()
    replays it first; PREFLIGHT only reports pending bytes).
  * BASELINES AT INGEST. step_end / metric compute baseline_s / baseline_value (median of the previous <= 5 OK values) and the ratio when
    the row is written; nothing is flagged on a ratio before MIN_BASELINE_CYCLES values exist (warm-up). Absolute ceilings
    (cycle_step_def.hard_max_s) apply from the first cycle and stay as a backstop.
  * BACKLOG LINK AT WRITE TIME. A flag's ap_status is read from gestion.backlog with the same 3 s limits and is one of a real status,
    NOT_FOUND (reachable, no such AP) or LOOKUP_FAILED (outage: never counted as an orphan, retried by refresh_unverified_flags()).

The pure functions (median_baseline, ratio_of, judge) take no connection (R-7). Never prints a DSN.
"""
from __future__ import annotations

import json
import math
import os
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

try:
    import psycopg
except ImportError:  # pragma: no cover - psycopg3 not installed: every emit falls back to the file
    psycopg = None  # type: ignore[assignment]

ENV_CYCLE_ID = "FONDOS_CYCLE_ID"
ENV_FALLBACK = "FONDOS_CYCLE_FALLBACK"
ENV_DSN = "FONDOS_PG_DSN"
DEFAULT_FALLBACK = Path(__file__).resolve().parents[1] / "log" / "cycle_telemetry_fallback.jsonl"

BASELINE_K = 5            # previous OK values the baseline is the median of
MIN_BASELINE_CYCLES = 3   # warm-up: no ratio-based judgement before this many baseline values
CONNECT_TIMEOUT_S = 3
STATEMENT_TIMEOUT_MS = 3000
AP_STATUSES = ("OPEN", "TODO", "IN_PROGRESS", "READY_FOR_DEPLOY", "BLOCKED", "DEFERRED", "CLOSED", "NOT_FOUND", "LOOKUP_FAILED")


# --------------------------------------------------------------------------------------------- pure
def median_baseline(values: Iterable, k: int = BASELINE_K, min_cycles: int = MIN_BASELINE_CYCLES) -> Optional[float]:
    """Median of the first `k` finite values of `values` (most recent first); None during warm-up (fewer than `min_cycles`)."""
    vals = [float(v) for v in values if v is not None and math.isfinite(float(v))][:k]
    return float(statistics.median(vals)) if len(vals) >= min_cycles else None


def ratio_of(value: Optional[float], baseline: Optional[float]) -> Optional[float]:
    if value is None or baseline is None or not math.isfinite(value) or not math.isfinite(baseline) or baseline <= 0:
        return None
    return float(value) / float(baseline)


def judge(value: Optional[float], baseline: Optional[float] = None, warn_ratio: Optional[float] = None,
          alarm_ratio: Optional[float] = None, hard_max: Optional[float] = None, hard_min: Optional[float] = None) -> str:
    """'HARD' (absolute ceiling / floor, valid from cycle 1) > 'ALARM' > 'WARN' (ratio to the baseline, only once a baseline exists) > 'OK'."""
    if value is None or not math.isfinite(value):
        return "OK"
    if (hard_max is not None and value > hard_max) or (hard_min is not None and value < hard_min):
        return "HARD"
    r = ratio_of(value, baseline)
    if r is not None:
        if alarm_ratio is not None and r >= float(alarm_ratio):
            return "ALARM"
        if warn_ratio is not None and r >= float(warn_ratio):
            return "WARN"
    return "OK"


# --------------------------------------------------------------------------------------------- plumbing
def _ensure_repo_on_path() -> None:
    import sys
    root = str(Path(__file__).resolve().parents[1])
    if root not in sys.path:
        sys.path.insert(0, root)


def current_cycle_id() -> Optional[str]:
    return os.environ.get(ENV_CYCLE_ID) or None


def fallback_path() -> Path:
    override = os.environ.get(ENV_FALLBACK)
    return Path(override) if override else DEFAULT_FALLBACK


def pending_fallback_bytes() -> int:
    """Size of the un-replayed fallback file (PREFLIGHT reports it as a WARN); 0 when there is none or it cannot be read."""
    try:
        p = fallback_path()
        return p.stat().st_size if p.exists() else 0
    except Exception:
        return 0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(dsn: Optional[str] = None):
    dsn = dsn or os.environ.get(ENV_DSN)
    if not dsn:
        try:        # run as a script by a launcher: shared.config autoloads the repo .env (FONDOS_PG_DSN)
            _ensure_repo_on_path()
            import shared.config  # noqa: F401
        except Exception:
            pass
        dsn = os.environ.get(ENV_DSN)
    if not dsn or psycopg is None:
        raise RuntimeError("no telemetry DSN / psycopg")
    return psycopg.connect(dsn, autocommit=True, connect_timeout=CONNECT_TIMEOUT_S,
                           options=f"-c statement_timeout={STATEMENT_TIMEOUT_MS}")


def _write_fallback(event: dict) -> None:
    try:
        p = fallback_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass            # a read-only filesystem or a full disk must not break the cycle either


def _epoch(ts: str) -> float:
    return datetime.fromisoformat(ts).timestamp()


# --------------------------------------------------------------------------------------------- applying one event (idempotent)
def _ensure_cycle(conn, ev: dict) -> None:
    """The FK target of every other row: a stub when the begin event was lost (outage), completed later by cycle_begin / cycle_end."""
    conn.execute(
        "INSERT INTO control.cycle_run (cycle_id, launcher, started_at) VALUES (%s, %s, %s::timestamptz) ON CONFLICT (cycle_id) DO NOTHING",
        (ev["cycle_id"], ev.get("launcher") or "UNKNOWN", ev.get("cycle_started_at") or ev.get("ts") or _now()))


def _step_def(conn, step_code: str) -> tuple:
    row = conn.execute("SELECT step_kind, seq, warn_ratio, alarm_ratio, hard_max_s FROM control.cycle_step_def WHERE step_code = %s",
                       (step_code,)).fetchone()
    return tuple(row) if row else ("DIAGNOSTIC", None, None, None, None)


def _apply(conn, ev: dict) -> None:
    op = ev["op"]
    with conn.transaction():
        _ensure_cycle(conn, ev)
        if op == "cycle_begin":
            conn.execute(
                "UPDATE control.cycle_run SET launcher = %s, started_at = %s::timestamptz, options = %s::jsonb, "
                "calc_version = COALESCE(%s, calc_version), git_commit = COALESCE(%s, git_commit), "
                "restore_point = COALESCE(%s, restore_point), resume_from = COALESCE(%s, resume_from), "
                "parent_cycle_id = COALESCE((SELECT cycle_id FROM control.cycle_run WHERE cycle_id = %s), parent_cycle_id) "
                "WHERE cycle_id = %s",
                (ev["launcher"], ev["ts"], json.dumps(ev.get("options") or {}), ev.get("calc_version"), ev.get("git_commit"),
                 ev.get("restore_point"), ev.get("resume_from"), ev.get("parent_cycle_id"), ev["cycle_id"]))
        elif op == "cycle_end":
            conn.execute(
                "UPDATE control.cycle_run SET ended_at = %s::timestamptz, status = %s, rc = %s, failed_step = %s, "
                "universe_active = COALESCE(%s, universe_active), regime = COALESCE(%s, regime), semaforo = COALESCE(%s, semaforo) "
                "WHERE cycle_id = %s",
                (ev["ts"], ev["status"], ev.get("rc"), ev.get("failed_step"), ev.get("universe_active"), ev.get("regime"),
                 ev.get("semaforo"), ev["cycle_id"]))
        elif op == "step_begin":
            kind, seq, *_ = _step_def(conn, ev["step_code"])
            conn.execute(
                "INSERT INTO control.cycle_step (cycle_id, step_code, attempt, step_kind, seq, started_at, status, log_path) "
                "VALUES (%s, %s, %s, %s, %s, %s::timestamptz, 'RUNNING', %s) "
                "ON CONFLICT (cycle_id, step_code, attempt) DO UPDATE SET started_at = EXCLUDED.started_at "
                "WHERE control.cycle_step.status = 'RUNNING'",
                (ev["cycle_id"], ev["step_code"], ev.get("attempt", 1), kind, seq, ev["ts"], ev.get("log_path")))
        elif op == "step_end":
            kind, seq, warn, alarm, hard_max = _step_def(conn, ev["step_code"])
            dur = max(0.0, _epoch(ev["ts"]) - _epoch(ev["started_at"])) if ev.get("started_at") else None
            prev = [r[0] for r in conn.execute(
                "SELECT dur_s FROM control.cycle_step WHERE step_code = %s AND status = 'OK' AND dur_s IS NOT NULL "
                "AND ended_at < %s::timestamptz AND NOT (cycle_id = %s AND attempt = %s) ORDER BY ended_at DESC LIMIT %s",
                (ev["step_code"], ev["ts"], ev["cycle_id"], ev.get("attempt", 1), BASELINE_K)).fetchall()]
            base = median_baseline(prev)
            verdict = judge(dur, base, warn, alarm, hard_max)
            status = "FAILED" if (ev.get("rc") not in (None, 0)) else ("OK" if verdict == "OK" else "WARN")
            if ev.get("skipped"):
                status = "SKIPPED"
            conn.execute(
                "INSERT INTO control.cycle_step (cycle_id, step_code, attempt, step_kind, seq, started_at, ended_at, rc, dur_s, baseline_s, "
                "ratio, status, warn_count, error_count, batch_id, log_path) "
                "VALUES (%s, %s, %s, %s, %s, COALESCE(%s::timestamptz, %s::timestamptz), %s::timestamptz, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (cycle_id, step_code, attempt) DO UPDATE SET ended_at = EXCLUDED.ended_at, rc = EXCLUDED.rc, "
                "dur_s = EXCLUDED.dur_s, baseline_s = EXCLUDED.baseline_s, ratio = EXCLUDED.ratio, status = EXCLUDED.status, "
                "warn_count = COALESCE(EXCLUDED.warn_count, control.cycle_step.warn_count), "
                "error_count = COALESCE(EXCLUDED.error_count, control.cycle_step.error_count), "
                "batch_id = COALESCE(EXCLUDED.batch_id, control.cycle_step.batch_id), log_path = COALESCE(EXCLUDED.log_path, control.cycle_step.log_path)",
                (ev["cycle_id"], ev["step_code"], ev.get("attempt", 1), kind, seq, ev.get("started_at"), ev["ts"], ev["ts"], ev.get("rc"),
                 dur, base, ratio_of(dur, base), status, ev.get("warn_count"), ev.get("error_count"), ev.get("batch_id"), ev.get("log_path")))
        elif op == "metric":
            prev = [r[0] for r in conn.execute(
                "SELECT value_num FROM control.cycle_metric WHERE step_code = %s AND metric_code = %s AND scope = %s "
                "AND value_num IS NOT NULL AND cycle_id < %s ORDER BY cycle_id DESC LIMIT %s",
                (ev["step_code"], ev["metric_code"], ev.get("scope", ""), ev["cycle_id"], BASELINE_K)).fetchall()]
            base = median_baseline(prev)
            val = ev.get("value_num")
            conn.execute(
                "INSERT INTO control.cycle_metric (cycle_id, step_code, metric_code, scope, value_num, value_text, baseline_value, ratio) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (cycle_id, step_code, metric_code, scope) DO UPDATE SET "
                "value_num = EXCLUDED.value_num, value_text = EXCLUDED.value_text, baseline_value = EXCLUDED.baseline_value, "
                "ratio = EXCLUDED.ratio, recorded_at = now()",
                (ev["cycle_id"], ev["step_code"], ev["metric_code"], ev.get("scope", ""), val, ev.get("value_text"), base,
                 ratio_of(val, base) if val is not None else None))
        elif op == "audit_run":
            conn.execute(
                "INSERT INTO control.cycle_audit_run (cycle_id, domain, phase, run_id, n_alarm, n_warn, n_info, n_new, n_resolved, "
                "n_unchanged, n_stagnant) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (cycle_id, domain, phase) DO UPDATE SET run_id = EXCLUDED.run_id, n_alarm = EXCLUDED.n_alarm, "
                "n_warn = EXCLUDED.n_warn, n_info = EXCLUDED.n_info, n_new = EXCLUDED.n_new, n_resolved = EXCLUDED.n_resolved, "
                "n_unchanged = EXCLUDED.n_unchanged, n_stagnant = EXCLUDED.n_stagnant",
                (ev["cycle_id"], ev["domain"], ev["phase"], ev["run_id"], ev.get("n_alarm"), ev.get("n_warn"), ev.get("n_info"),
                 ev.get("n_new"), ev.get("n_resolved"), ev.get("n_unchanged"), ev.get("n_stagnant")))
        elif op == "flag":
            ap_id, ap_status = ev.get("ap_id"), ev.get("ap_status")
            if ap_id is None:                       # a standing ack (the zero-entropy policy) supplies the AP when the caller has none
                ack = conn.execute(
                    "SELECT ap_id FROM control.cycle_flag_ack WHERE flag_code = %s AND scope IN (%s, '') "
                    "AND (expires_at IS NULL OR expires_at >= current_date) ORDER BY scope DESC LIMIT 1",
                    (ev["flag_code"], ev.get("scope", ""))).fetchone()
                ap_id = ack[0] if ack else None
                ap_status = "LOOKUP_FAILED" if ack else None       # not resolved yet: refresh_unverified_flags() reads the backlog
            conn.execute(
                "INSERT INTO control.cycle_flag (cycle_id, flag_code, scope, severity, detail, evidence, ap_id, ap_status) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (cycle_id, flag_code, scope) DO UPDATE SET "
                "severity = EXCLUDED.severity, detail = EXCLUDED.detail, evidence = EXCLUDED.evidence, ap_id = EXCLUDED.ap_id, "
                "ap_status = EXCLUDED.ap_status",
                (ev["cycle_id"], ev["flag_code"], ev.get("scope", ""), ev["severity"], ev.get("detail"), ev.get("evidence"), ap_id, ap_status))
        else:
            raise ValueError(f"unknown telemetry op {op!r}")


def _is_not_installed(exc: BaseException) -> bool:
    """True when the failure is "the telemetry tables do not exist" (psycopg UndefinedTable / UndefinedSchema, SQLSTATE 42P01 / 3F000)."""
    return getattr(exc, "sqlstate", None) in ("42P01", "3F000") or type(exc).__name__ in ("UndefinedTable", "InvalidSchemaName")


def emit(event: dict, conn=None) -> bool:
    """Apply one event. True when it reached the database; otherwise it is appended to the fallback file and False is returned.
    Never raises (Exception only: KeyboardInterrupt / SystemExit propagate on purpose)."""
    try:
        if conn is not None:
            _apply(conn, event)
            return True
        c = _connect()
        try:
            _apply(c, event)
            return True
        finally:
            c.close()
    except Exception as exc:
        if _is_not_installed(exc):
            return False        # the DDL is not applied (yet): a CONFIGURATION absence, not an outage -- writing a fallback would only pile up debt
        _write_fallback(event)
        return False


def replay_fallback(conn=None) -> int:
    """Apply the events of the fallback file in order (idempotent upserts). On full success the file is rotated to `<name>.done`; on the
    first failure the rest stays in place. Returns the number of events applied; never raises."""
    try:
        path = fallback_path()
        if not path.exists() or path.stat().st_size == 0:
            return 0
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        own = conn is None
        c = conn if conn is not None else _connect()
        applied = 0
        try:
            for ln in lines:
                _apply(c, json.loads(ln))
                applied += 1
        except Exception:
            rest = lines[applied:]
            path.write_text("\n".join(rest) + "\n", encoding="utf-8")
            return applied
        finally:
            if own:
                c.close()
        path.replace(path.with_name(path.name + ".done"))
        return applied
    except Exception:
        return 0


# --------------------------------------------------------------------------------------------- backlog link
def lookup_ap_status(ap_id: str) -> str:
    """Status of a backlog ticket: its real status, 'NOT_FOUND' (the backlog answered, no such AP) or 'LOOKUP_FAILED' (no DSN, outage,
    timeout: never an orphan, retried by refresh_unverified_flags). The SQL lives in shared.backlog_client (single owner of every
    backlog read, P#11)."""
    try:
        from shared.backlog_client import get_action_point_status
        st = get_action_point_status(ap_id)
        return st if st in AP_STATUSES else "LOOKUP_FAILED"
    except Exception:
        return "LOOKUP_FAILED"


def refresh_unverified_flags(conn, lookup=lookup_ap_status) -> int:
    """Re-resolve the flags whose backlog lookup failed at write time; returns how many were resolved. Never raises."""
    try:
        with conn.transaction():          # its own SAVEPOINT: a failed statement is rolled back here, the caller's transaction is not poisoned
            rows = conn.execute("SELECT cycle_id, flag_code, scope, ap_id FROM control.cycle_flag "
                                "WHERE ap_status = 'LOOKUP_FAILED' AND ap_id IS NOT NULL").fetchall()
            n = 0
            for cycle_id, flag_code, scope, ap_id in rows:
                st = lookup(ap_id)
                if st != "LOOKUP_FAILED":
                    conn.execute("UPDATE control.cycle_flag SET ap_status = %s WHERE cycle_id = %s AND flag_code = %s AND scope = %s",
                                 (st, cycle_id, flag_code, scope))
                    n += 1
        return n
    except Exception:
        return 0


# --------------------------------------------------------------------------------------------- public API (opt-in, never raises)
def _event(op: str, cycle_id: Optional[str], **fields) -> Optional[dict]:
    cid = cycle_id or current_cycle_id()
    if not cid:
        return None
    return {"op": op, "cycle_id": cid, "ts": fields.pop("ts", None) or _now(), **fields}


def _send(op: str, cycle_id: Optional[str], conn=None, **fields) -> bool:
    try:
        ev = _event(op, cycle_id, **fields)
        return False if ev is None else emit(ev, conn)
    except Exception:
        return False


def begin_cycle(launcher: str, options: Optional[dict] = None, *, cycle_id: Optional[str] = None, conn=None, **fields) -> bool:
    """Replays the fallback first (so ordering across steps cannot lose data), then records the cycle start."""
    try:
        if not (cycle_id or current_cycle_id()):
            return False
        replay_fallback(conn)
    except Exception:
        pass
    return _send("cycle_begin", cycle_id, conn, launcher=launcher, options=options or {}, **fields)


def end_cycle(status: str, rc: Optional[int] = None, *, cycle_id: Optional[str] = None, conn=None, **fields) -> bool:
    return _send("cycle_end", cycle_id, conn, status=status, rc=rc, **fields)


def step_begin(step_code: str, *, cycle_id: Optional[str] = None, conn=None, **fields) -> bool:
    return _send("step_begin", cycle_id, conn, step_code=step_code, **fields)


def step_end(step_code: str, rc: Optional[int], *, started_at: Optional[str] = None, cycle_id: Optional[str] = None, conn=None, **fields) -> bool:
    return _send("step_end", cycle_id, conn, step_code=step_code, rc=rc, started_at=started_at, **fields)


def record_metric(step_code: str, metric_code: str, value: Optional[float] = None, *, scope: str = "", value_text: Optional[str] = None,
                  cycle_id: Optional[str] = None, conn=None) -> bool:
    return _send("metric", cycle_id, conn, step_code=step_code, metric_code=metric_code, scope=scope,
                 value_num=None if value is None else float(value), value_text=value_text)


def record_audit_run(domain: str, phase: str, run_id: str, *, cycle_id: Optional[str] = None, conn=None, **counts) -> bool:
    return _send("audit_run", cycle_id, conn, domain=domain, phase=phase, run_id=run_id, **counts)


def raise_flag(flag_code: str, severity: str, *, scope: str = "", detail: Optional[str] = None, evidence: Optional[float] = None,
               ap_id: Optional[str] = None, cycle_id: Optional[str] = None, conn=None, lookup=lookup_ap_status,
               ts: Optional[str] = None) -> bool:
    """Records a flag; when `ap_id` is given its backlog status is read now (real status / NOT_FOUND / LOOKUP_FAILED)."""
    try:
        ap_status = lookup(ap_id) if ap_id else None
    except Exception:
        ap_status = "LOOKUP_FAILED" if ap_id else None
    return _send("flag", cycle_id, conn, flag_code=flag_code, severity=severity, scope=scope, detail=detail, evidence=evidence,
                 ap_id=ap_id, ap_status=ap_status, ts=ts)


# --------------------------------------------------------------------------------------------- command line (what the .bat launchers call)
def _state_path(cycle_id: str) -> Path:
    return fallback_path().with_name(f"cycle_telemetry_state_{cycle_id}.json")


def _read_state(cycle_id: str) -> dict:
    try:
        return json.loads(_state_path(cycle_id).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_state(cycle_id: str, state: dict) -> None:
    try:
        p = _state_path(cycle_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state), encoding="utf-8")
    except Exception:
        pass


def _database_command(a, cycle_id: str) -> None:
    """The sub-commands that READ the database (P2 window, cycle facts, audit findings). Failure-isolated like every emit: a missing table
    (DDL not applied) or an outage leaves nothing behind and changes nothing."""
    _ensure_repo_on_path()
    c = _connect()
    try:
        if a.cmd == "ingest-p2":
            from shared.cycle_telemetry_ingest import ingest_p2
            ingest_p2(c, cycle_id, since=a.since)
        elif a.cmd == "evaluate-flags":
            from shared.cycle_telemetry_flags import evaluate_cycle
            evaluate_cycle(c, cycle_id, a.status)
        else:
            from shared.cycle_telemetry_ingest import record_audit
            record_audit(c, cycle_id, a.domain, a.phase, a.run_id, baseline_run_id=a.baseline)
    finally:
        c.close()


def main(argv: Optional[Sequence[str]] = None) -> int:
    """`python -m shared.cycle_telemetry <begin|end|step-begin|step-end|metric|flag|ingest-p2|evaluate-flags|audit-run|pending> ...`

    ALWAYS returns 0 (NORMAS_BATCH: a launcher's RC is its own; telemetry must never change it) and prints nothing unless asked (`pending`).
    Without FONDOS_CYCLE_ID every sub-command is a no-op. The step start time travels between step-begin and step-end in a small state
    file next to the fallback, because a batch file cannot hold an ISO timestamp portably."""
    import argparse
    try:
        ap = argparse.ArgumentParser(prog="shared.cycle_telemetry")
        sub = ap.add_subparsers(dest="cmd", required=True)
        b = sub.add_parser("begin"); b.add_argument("--launcher", required=True); b.add_argument("--parent"); b.add_argument("--resume-from", type=int)
        b.add_argument("--calc-version"); b.add_argument("--restore-point"); b.add_argument("--option", action="append", default=[])
        e = sub.add_parser("end"); e.add_argument("--status", required=True, choices=["OK", "FAILED", "ABORTED"]); e.add_argument("--rc", type=int)
        e.add_argument("--failed-step"); e.add_argument("--regime"); e.add_argument("--semaforo")
        sb = sub.add_parser("step-begin"); sb.add_argument("step"); sb.add_argument("--attempt", type=int, default=1); sb.add_argument("--log")
        se = sub.add_parser("step-end"); se.add_argument("step"); se.add_argument("rc", type=int); se.add_argument("--attempt", type=int, default=1)
        se.add_argument("--warn", type=int); se.add_argument("--error", type=int); se.add_argument("--log"); se.add_argument("--skipped", action="store_true")
        m = sub.add_parser("metric"); m.add_argument("step"); m.add_argument("code"); m.add_argument("value", type=float); m.add_argument("--scope", default="")
        f = sub.add_parser("flag"); f.add_argument("code"); f.add_argument("severity", choices=["INFO", "WARN", "HIGH"]); f.add_argument("--scope", default="")
        f.add_argument("--detail"); f.add_argument("--ap")
        ip = sub.add_parser("ingest-p2"); ip.add_argument("--since")           # RUN_SUMMARY / BACKFILL_* rows of the P2_CALC window -> metrics + flags
        ef = sub.add_parser("evaluate-flags"); ef.add_argument("--status", default="OK")   # the end-of-cycle flag catalogue
        ar = sub.add_parser("audit-run"); ar.add_argument("domain"); ar.add_argument("phase", choices=["pre", "post"]); ar.add_argument("run_id")
        ar.add_argument("--baseline")                                          # counts read from control.audit_finding by run_id
        sub.add_parser("pending")
        a = ap.parse_args(list(argv) if argv is not None else None)
    except SystemExit:
        return 0                                    # a malformed call must not fail the launcher either
    except Exception:
        return 0
    try:
        if a.cmd == "pending":
            print(pending_fallback_bytes())
            return 0
        cid = current_cycle_id()
        if not cid:
            return 0
        if a.cmd == "begin":
            opts = dict(o.split("=", 1) for o in a.option if "=" in o)
            begin_cycle(a.launcher, opts, parent_cycle_id=a.parent, resume_from=a.resume_from, calc_version=a.calc_version,
                        restore_point=a.restore_point)
            _write_state(cid, {})
        elif a.cmd == "end":
            end_cycle(a.status, a.rc, failed_step=a.failed_step, regime=a.regime, semaforo=a.semaforo)
        elif a.cmd == "step-begin":
            ts = _now()
            st = _read_state(cid)
            st[f"{a.step}#{a.attempt}"] = ts
            _write_state(cid, st)
            step_begin(a.step, attempt=a.attempt, ts=ts, log_path=a.log)
        elif a.cmd == "step-end":
            st = _read_state(cid)
            step_end(a.step, a.rc, started_at=st.get(f"{a.step}#{a.attempt}"), attempt=a.attempt, warn_count=a.warn, error_count=a.error,
                     log_path=a.log, skipped=a.skipped)
        elif a.cmd == "metric":
            record_metric(a.step, a.code, a.value, scope=a.scope)
        elif a.cmd == "flag":
            raise_flag(a.code, a.severity, scope=a.scope, detail=a.detail, ap_id=a.ap)
        elif a.cmd in ("ingest-p2", "evaluate-flags", "audit-run"):
            _database_command(a, cid)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    import sys
    _ensure_repo_on_path()          # `python shared/cycle_telemetry.py ...` (how common.bat calls it): `shared` must be importable
    sys.exit(main())
