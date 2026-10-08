"""
shared/cycle_telemetry_ingest.py -- P2 run counters and audit counts into the cycle telemetry, WITHOUT touching run_pipeline (FND-0239 stage 3).

P2 already writes a structured `RUN_SUMMARY` row (and, for a backfill, `BACKFILL_START` / `BACKFILL_END`) to control.p2_pipeline_log. This module reads
those rows for the cycle's P2_CALC window and turns them into cycle_metric rows and flags, so P2 stays decoupled from telemetry.

NO SILENT NO-OP ON FORMAT DRIFT. The RUN_SUMMARY parser is strict: the message must start with `run_id=` and carry every key in
RUN_SUMMARY_REQUIRED with a numeric value (extra keys are tolerated). A row that does not match, or lacks a key, produces
  * a structured WARNING log line naming the row id and the problem, and
  * a cycle_flag INGEST_FORMAT_DRIFT (WARN),
and the ingest still returns normally: it never stops the business pipeline. tests/test_cycle_telemetry_ingest.py has a CONTRACT test that reads
proyecto2/src/pipeline/run_pipeline.py and fails if a key required here disappears from its RUN_SUMMARY f-string, so the drift is caught in CI,
not only at runtime.

ABRUPT P2 DEATH IS NOT SILENCE. run_pipeline writes RUN_SUMMARY from a `finally` block, so only a hard kill (OOM, OS termination) skips it. When the
P2_CALC step has ended (any rc) and the window holds NO RUN_SUMMARY, or a BACKFILL_START has no BACKFILL_END, INGEST_CRASH_DETECTED (HIGH) is raised,
distinct from the format drift above.

Also raised here, because only this module has the rows: BACKFILL_NO_WORK (a backfill that recomputed nothing / ran no OLS) and RUN_ERROR_ZERO_WORK
(errors but nothing processed or written). The pure functions take no connection (R-7); everything is failure-isolated like shared/cycle_telemetry.py.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

log = logging.getLogger("cycle_telemetry_ingest")

STEP_CODE = "P2_CALC"
RUN_SUMMARY_REQUIRED = ("processed", "skipped", "errors", "warnings", "written", "quarantined", "ols_funds",
                        "cond_guard_funds", "betas_nulled", "elapsed")
# RUN_SUMMARY key -> cycle_metric_def.metric_code (db/pg/35_control.sql)
METRIC_OF_KEY = {"processed": "p2_processed", "skipped": "p2_skipped", "errors": "p2_errors", "warnings": "p2_warnings",
                 "written": "p2_written_rows", "quarantined": "p2_quarantined", "ols_funds": "ols_funds",
                 "cond_guard_funds": "cond_guard_funds", "betas_nulled": "betas_nulled", "elapsed": "p2_elapsed_s"}
_KV = re.compile(r"(\w+)=(\S+)")
_RECOMPUTED = re.compile(r"recomputed=(\d+)")


# ── pure ──────────────────────────────────────────────────────────────────────────────────────────

def parse_run_summary(message: Optional[str]) -> tuple:
    """(values, problems). `values` = {key: float} for every required key (elapsed without its trailing `s`), or None when there are problems."""
    text = (message or "").strip()
    if not text.startswith("run_id="):
        return None, ["message does not start with run_id="]
    kv = dict(_KV.findall(text))
    problems, values = [], {}
    for key in RUN_SUMMARY_REQUIRED:
        if key not in kv:
            problems.append(f"missing key {key}")
            continue
        raw = kv[key][:-1] if key == "elapsed" and kv[key].endswith("s") else kv[key]
        try:
            values[key] = float(raw)
        except ValueError:
            problems.append(f"non-numeric {key}={kv[key]!r}")
    return (values if not problems else None), problems


def metrics_from_summary(values: dict) -> dict:
    """{metric_code: value} for one parsed RUN_SUMMARY, plus cache_hit_pct (share of funds the fingerprint cache skipped)."""
    out = {METRIC_OF_KEY[k]: v for k, v in values.items()}
    total = values["processed"] + values["skipped"]
    if total > 0:
        out["cache_hit_pct"] = 100.0 * values["skipped"] / total
    return out


def analyze(rows: list, p2_step: Optional[dict] = None) -> dict:
    """Pure. `rows` = [(id, step, status, message, batch_id)] of the window in id order; `p2_step` = {'ended': bool, 'rc': int|None} of the cycle's
    P2_CALC step, or None when unknown. Returns {'metrics': {...}, 'flags': [(code, severity, scope, detail, evidence)], 'summaries': n}."""
    flags, summaries, last_ok = [], [], None
    started, ended = {}, set()
    for rid, step, status, message, batch in rows:
        scope = str(batch or f"row{rid}")
        if step == "RUN_SUMMARY":
            summaries.append(rid)
            values, problems = parse_run_summary(message)
            if problems:
                flags.append(("INGEST_FORMAT_DRIFT", "WARN", scope, f"RUN_SUMMARY row {rid}: " + "; ".join(problems), None))
                continue
            last_ok = (scope, values)
            if values["errors"] > 0 and values["processed"] == 0 and values["written"] == 0:
                flags.append(("RUN_ERROR_ZERO_WORK", "HIGH", scope,
                              f"{int(values['errors'])} errors and nothing processed or written", values["errors"]))
        elif step == "BACKFILL_START":
            started[scope] = str(message or "")
        elif step == "BACKFILL_END":
            ended.add(scope)
            m = _RECOMPUTED.search(str(message or ""))
            if m and int(m.group(1)) == 0:
                flags.append(("BACKFILL_NO_WORK", "WARN", scope, "BACKFILL_END recomputed=0: the backfill touched no fund", 0.0))
    for scope, reason in started.items():
        if scope not in ended:
            flags.append(("INGEST_CRASH_DETECTED", "HIGH", scope,
                          f"BACKFILL_START ({reason[:80]}) has no BACKFILL_END: the run died between the summary and the end marker", None))
    if p2_step and p2_step.get("ended") and not summaries:
        flags.append(("INGEST_CRASH_DETECTED", "HIGH", "window",
                      f"P2_CALC ended (rc={p2_step.get('rc')}) but the window holds no RUN_SUMMARY: the run was killed before its finally block", None))
    metrics = {}
    if last_ok is not None:
        scope, values = last_ok
        metrics = metrics_from_summary(values)
        drift_backfill = any(scope == s and r.startswith("CALC_VERSION drift") for s, r in started.items())
        if drift_backfill and values["ols_funds"] == 0 and not any(f[0] == "BACKFILL_NO_WORK" and f[2] == scope for f in flags):
            flags.append(("BACKFILL_NO_WORK", "WARN", scope,
                          "CALC_VERSION-drift backfill finished with ols_funds=0: the macro OLS was not recomputed", 0.0))
    return {"metrics": metrics, "flags": flags, "summaries": len(summaries)}


def audit_counts(rows: list) -> dict:
    """{'n_alarm','n_warn','n_info'} from [(severity, count)] rows of control.audit_finding for one run_id."""
    by = {str(s).upper(): int(n) for s, n in rows}
    return {"n_alarm": by.get("ALARM", 0), "n_warn": by.get("WARN", 0), "n_info": by.get("INFO", 0)}


# ── database (every statement a literal, so tests/test_sql_explain_sweep_pg.py can EXPLAIN it) ─────

Q_P2_STEP = ("SELECT started_at, ended_at, rc FROM control.cycle_step WHERE cycle_id = %s AND step_code = 'P2_CALC' "
             "ORDER BY attempt DESC LIMIT 1")
Q_CYCLE_START = "SELECT started_at FROM control.cycle_run WHERE cycle_id = %s"
Q_P2_ROWS = ("SELECT id, step, status, message, batch_id FROM control.p2_pipeline_log "
             "WHERE step IN ('RUN_SUMMARY', 'BACKFILL_START', 'BACKFILL_END') AND created_at >= %s ORDER BY id")
Q_AUDIT_COUNTS = "SELECT severity, COUNT(*) FROM control.audit_finding WHERE run_id = %s GROUP BY severity"
Q_AUDIT_DIFF = (
    "WITH cur AS (SELECT DISTINCT rule_id, group_key, COALESCE(isin, '') AS k FROM control.audit_finding WHERE run_id = %s), "
    "base AS (SELECT DISTINCT rule_id, group_key, COALESCE(isin, '') AS k FROM control.audit_finding WHERE run_id = %s) "
    "SELECT (SELECT COUNT(*) FROM cur c WHERE NOT EXISTS (SELECT 1 FROM base b WHERE b.rule_id = c.rule_id AND b.group_key = c.group_key AND b.k = c.k)), "
    "(SELECT COUNT(*) FROM base b WHERE NOT EXISTS (SELECT 1 FROM cur c WHERE b.rule_id = c.rule_id AND b.group_key = c.group_key AND b.k = c.k)), "
    "(SELECT COUNT(*) FROM cur c WHERE EXISTS (SELECT 1 FROM base b WHERE b.rule_id = c.rule_id AND b.group_key = c.group_key AND b.k = c.k))")


def ingest_p2(conn, cycle_id: str, since=None) -> dict:
    """Reads the P2 window of `cycle_id` and records metrics + flags. Fail-soft: returns {'error': ...} instead of raising."""
    from shared import cycle_telemetry as ct
    try:
        with conn.transaction():          # a SAVEPOINT on a caller's connection: the failure below must not leave THEIR transaction aborted
            step = conn.execute(Q_P2_STEP, (cycle_id,)).fetchone()
            start = since or (step[0] if step else None)
            if start is None:
                row = conn.execute(Q_CYCLE_START, (cycle_id,)).fetchone()
                start = row[0] if row else None
            if start is None:
                return {"error": f"no start time for cycle {cycle_id} (give --since)"}
            rows = [tuple(r) for r in conn.execute(Q_P2_ROWS, (start,)).fetchall()]
            res = analyze(rows, {"ended": bool(step and step[1] is not None), "rc": step[2] if step else None} if step else None)
            for code, value in res["metrics"].items():
                ct.record_metric(STEP_CODE, code, value, cycle_id=cycle_id, conn=conn)
            for code, severity, scope, detail, evidence in res["flags"]:
                if code == "INGEST_FORMAT_DRIFT":
                    log.warning("cycle_telemetry_ingest: %s [%s] %s", code, scope, detail)
                ct.raise_flag(code, severity, scope=scope, detail=detail, evidence=evidence, cycle_id=cycle_id, conn=conn)
            return {"metrics": len(res["metrics"]), "flags": [f[0] for f in res["flags"]], "summaries": res["summaries"]}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {str(exc)[:160]}"}


def record_audit(conn, cycle_id: str, domain: str, phase: str, run_id: str, baseline_run_id: Optional[str] = None) -> dict:
    """cycle_audit_run counts read from control.audit_finding by run_id (and new / resolved / unchanged against a baseline run_id)."""
    from shared import cycle_telemetry as ct
    try:
        with conn.transaction():          # SAVEPOINT: see ingest_p2
            counts = audit_counts(conn.execute(Q_AUDIT_COUNTS, (run_id,)).fetchall())
            if baseline_run_id:
                new, resolved, unchanged = conn.execute(Q_AUDIT_DIFF, (run_id, baseline_run_id)).fetchone()
                counts.update(n_new=int(new), n_resolved=int(resolved), n_unchanged=int(unchanged))
            ct.record_audit_run(domain, phase, run_id, cycle_id=cycle_id, conn=conn, **counts)
            return counts
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {str(exc)[:160]}"}
