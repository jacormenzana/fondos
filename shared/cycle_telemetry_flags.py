"""
shared/cycle_telemetry_flags.py -- the flag catalogue evaluated at the end of a cycle (FND-0239 stage 3).

A flag is a fact a human should look at that no exit code expresses: a slow step, a diagnostic that failed in a cycle that ended OK, funds still waiting for
the family-nature refresh. The rules are PURE functions over `facts` (R-7) so each is unit-tested without a database; `load_facts` reads them from the
cycle's own tables and `evaluate_cycle` writes the flags with shared.cycle_telemetry.raise_flag (a standing ack in control.cycle_flag_ack links the flag
to its backlog ticket at write time, with the usual NOT_FOUND / LOOKUP_FAILED distinction).

Catalogue (flag_code -> who raises it):
  STEP_SLOW               here   a step whose duration is over its baseline ratio (WARN) or its alarm ratio / hard ceiling (HIGH)
  DIAG_FAILED_CYCLE_OK    here   a DIAGNOSTIC step failed (rc != 0) while the cycle itself ends OK: nothing else would tell
  FAMILY_REFRESH_PENDING  here   active funds still waiting for `run_block.py --family-nature-refresh` (the P3 gate will block on them)
  FAMILY_VERSION_STALE    here   a metric family whose rows are not all on the current algorithm_version (cycle_metric family_version_coverage_pct < 100)
  BACKFILL_NO_WORK        shared.cycle_telemetry_ingest   a backfill that recomputed nothing / ran no OLS
  RUN_ERROR_ZERO_WORK     shared.cycle_telemetry_ingest   errors but nothing processed or written
  INGEST_FORMAT_DRIFT     shared.cycle_telemetry_ingest   a RUN_SUMMARY row the strict parser cannot read
  INGEST_CRASH_DETECTED   shared.cycle_telemetry_ingest   P2 died without its RUN_SUMMARY / a BACKFILL_START without its END
  P3_STALE_BYPASS         shared.gate_bypass              an audited --allow-stale override of the P3 freshness gate
"""
from __future__ import annotations

from typing import Optional

CATALOGUE = ("STEP_SLOW", "DIAG_FAILED_CYCLE_OK", "FAMILY_REFRESH_PENDING", "FAMILY_VERSION_STALE", "BACKFILL_NO_WORK", "RUN_ERROR_ZERO_WORK",
             "INGEST_FORMAT_DRIFT", "INGEST_CRASH_DETECTED", "P3_STALE_BYPASS")
MAX_LISTED = 20


# ── pure rules: each returns [(flag_code, severity, scope, detail, evidence)] ──────────────────────

def rule_step_slow(steps: list) -> list:
    out = []
    for s in steps:
        if s.get("status") == "FAILED" or s.get("dur_s") is None:
            continue
        dur, ratio = float(s["dur_s"]), s.get("ratio")
        hard = s.get("hard_max_s") is not None and dur > float(s["hard_max_s"])
        alarm = ratio is not None and s.get("alarm_ratio") is not None and float(ratio) >= float(s["alarm_ratio"])
        warn = s.get("status") == "WARN"
        if hard or alarm or warn:
            why = ("over its hard ceiling of %.0f s" % s["hard_max_s"]) if hard else (
                  "at %.1fx its baseline" % ratio if ratio is not None else "over its limit")
            out.append(("STEP_SLOW", "HIGH" if (hard or alarm) else "WARN", str(s["step_code"]),
                        f"{s['step_code']} took {dur:.0f} s, {why}", float(ratio) if ratio is not None else dur))
    return out


def rule_diag_failed_cycle_ok(steps: list, cycle_status: str) -> list:
    if cycle_status != "OK":
        return []
    return [("DIAG_FAILED_CYCLE_OK", "WARN", str(s["step_code"]),
             f"diagnostic {s['step_code']} ended rc={s.get('rc')} but the cycle is OK (diagnostics never change the cycle's RC)", float(s.get("rc") or 0))
            for s in steps if s.get("step_kind") == "DIAGNOSTIC" and (s.get("rc") not in (None, 0))]


def rule_family_refresh_pending(pending: list) -> list:
    if not pending:
        return []
    shown = ", ".join(pending[:MAX_LISTED]) + (f" (+{len(pending) - MAX_LISTED} more)" if len(pending) > MAX_LISTED else "")
    return [("FAMILY_REFRESH_PENDING", "HIGH", "", f"{len(pending)} active funds await run_block.py --family-nature-refresh: {shown}; the P3 gate will block",
             float(len(pending)))]


def rule_family_version_stale(metrics: list) -> list:
    return [("FAMILY_VERSION_STALE", "WARN", str(m.get("scope") or ""),
             f"{m.get('scope') or 'a metric family'}: only {float(m['value_num']):.1f}% of its rows are on the current algorithm_version", float(m["value_num"]))
            for m in metrics if m.get("metric_code") == "family_version_coverage_pct" and m.get("value_num") is not None
            and float(m["value_num"]) < 100.0]


def evaluate(facts: dict, cycle_status: str) -> list:
    """All the rules this module owns, over `facts` = {'steps': [...], 'metrics': [...], 'pending_family_refresh': [...]}."""
    return (rule_step_slow(facts.get("steps", [])) + rule_diag_failed_cycle_ok(facts.get("steps", []), cycle_status)
            + rule_family_refresh_pending(facts.get("pending_family_refresh", [])) + rule_family_version_stale(facts.get("metrics", [])))


# ── database (literals, so the EXPLAIN sweep verifies them) ───────────────────────────────────────

Q_STEPS = ("SELECT s.step_code, s.step_kind, s.status, s.rc, s.dur_s, s.ratio, d.alarm_ratio, d.hard_max_s "
           "FROM control.cycle_step s LEFT JOIN control.cycle_step_def d ON d.step_code = s.step_code WHERE s.cycle_id = %s ORDER BY s.started_at")
Q_METRICS = "SELECT step_code, metric_code, scope, value_num FROM control.cycle_metric WHERE cycle_id = %s"
_STEP_COLS = ("step_code", "step_kind", "status", "rc", "dur_s", "ratio", "alarm_ratio", "hard_max_s")
_METRIC_COLS = ("step_code", "metric_code", "scope", "value_num")


def load_facts(conn, cycle_id: str) -> dict:
    from shared.family_refresh import pending_family_refresh
    facts = {"steps": [dict(zip(_STEP_COLS, r)) for r in conn.execute(Q_STEPS, (cycle_id,)).fetchall()],
             "metrics": [dict(zip(_METRIC_COLS, r)) for r in conn.execute(Q_METRICS, (cycle_id,)).fetchall()]}
    try:
        with conn.transaction():          # SAVEPOINT: a failed P1-table read must not abort the caller's transaction
            facts["pending_family_refresh"] = pending_family_refresh(conn)
    except Exception:
        facts["pending_family_refresh"] = []        # the P1 tables are not this module's business; no flag beats a wrong flag
    return facts


def evaluate_cycle(conn, cycle_id: str, cycle_status: str) -> dict:
    """Reads the facts, evaluates the catalogue and writes the flags. Fail-soft: returns {'error': ...} instead of raising."""
    from shared import cycle_telemetry as ct
    try:
        with conn.transaction():          # SAVEPOINT on a caller's connection
            flags = evaluate(load_facts(conn, cycle_id), cycle_status)
            for code, severity, scope, detail, evidence in flags:
                ct.raise_flag(code, severity, scope=scope, detail=detail, evidence=evidence, cycle_id=cycle_id, conn=conn)
            return {"flags": [f[0] for f in flags]}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {str(exc)[:160]}"}
