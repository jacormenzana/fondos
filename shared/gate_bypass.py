"""
shared/gate_bypass.py -- the audit trail of a P3 `--allow-stale` override (FND-0244 follow-up, owner directive 5).

Overriding the freshness gate is allowed, but never silently: before anything is scored or persisted, every bypassed
check leaves a durable record that says WHO bypassed WHAT, WHEN and for WHICH scenario.

    * control.ingestion_log   -- always (existing append-only audit trail, free-text status, no DDL): one `P3_STALE_BYPASS`
                                 row (status ALARM) per bypassed check, plus one row per fund still pending a family refresh.
                                 If this write fails the bypass is REFUSED: an override that cannot be audited does not run.
    * control.audit_finding   -- once the owner migration `scripts/ops/migrate_audit_finding_p3_gate.py` has extended the
                                 `domain` CHECK with 'p3_gate' (detected from pg_constraint, no flag, no code change); until then it
                                 is skipped. `control.v_p3_gate_bypass` reads both, so a dashboard needs one object whatever the state.
    * control.cycle_flag      -- HIGH, only when a telemetry cycle is active (shared/cycle_telemetry.py is opt-in).

The message builders are pure (R-7); only `record_gate_bypass` touches the database.
"""
from __future__ import annotations

import getpass
import socket
from datetime import datetime, timezone

STEP_BYPASS = "P3_STALE_BYPASS"
FLAG_CODE = "P3_STALE_BYPASS"
AUDIT_DOMAIN = "p3_gate"
MAX_ISINS = 50


def _who() -> tuple:
    try:
        user = getpass.getuser()
    except Exception:
        user = "unknown"
    try:
        host = socket.gethostname()
    except Exception:
        host = "unknown"
    return user, host


def check_message(check_name: str, detail: str, scenario: str, user: str, host: str, pending: list,
                  limit: int = MAX_ISINS) -> str:
    """The text of the row for ONE bypassed check."""
    base = f"--allow-stale bypassed check {check_name} by {user}@{host} for scenario {scenario}: {detail}"
    if check_name == "family_refresh_pending" and pending:
        shown = ", ".join(pending[:limit])
        base += f"; pending family refresh: {len(pending)} funds ({shown}{', ...' if len(pending) > limit else ''})"
    return base


def bypass_rows(stale: list, scenario: str, user: str, host: str, pending: list, limit: int = MAX_ISINS) -> list:
    """[(isin, check_name, message)]: one row (isin None) per bypassed check and one per pending ISIN. `stale` = [(name, detail)]."""
    rows = [(None, name, check_message(name, detail, scenario, user, host, pending, limit)) for name, detail in stale]
    rows += [(isin, "family_refresh_pending",
              f"--allow-stale by {user}@{host} for scenario {scenario}: this fund's nature-derived attributes were NOT refreshed")
             for isin in pending]
    return rows


def audit_finding_admits_p3_gate(conn) -> bool:
    """True once control.audit_finding's `domain` CHECK admits 'p3_gate' (the owner migration was applied)."""
    row = conn.execute(
        "SELECT 1 FROM pg_constraint WHERE conrelid = to_regclass('audit_finding') AND contype = 'c' "
        "AND pg_get_constraintdef(oid) ILIKE %s LIMIT 1", ("%" + AUDIT_DOMAIN + "%",)).fetchone()
    return row is not None


def record_gate_bypass(conn, stale: list, scenario: str, pending: list, user: str | None = None, host: str | None = None,
                       now: datetime | None = None) -> dict:
    """Writes the audit trail. `stale` = [(check name, detail)]. Raises if the ingestion_log write fails (the caller refuses the
    bypass); the audit_finding and cycle_flag legs are best-effort. It does NOT commit: the caller commits (and so a test can wrap it in a
    rolled-back savepoint). Returns {'ingestion_log': n, 'audit_finding': n, 'cycle_flag': bool}."""
    u, h = _who()
    user, host = user or u, host or h
    stamp = (now or datetime.now(timezone.utc))
    ts = stamp.isoformat(timespec="seconds")
    rows = bypass_rows(stale, scenario, user, host, pending)
    for isin, _check, message in rows:
        conn.execute("INSERT INTO ingestion_log (isin, step, status, message, created_at) VALUES (%s, %s, 'ALARM', %s, %s)",
                     (isin, STEP_BYPASS, message, ts))
    out = {"ingestion_log": len(rows), "audit_finding": 0, "cycle_flag": False}

    try:
        if audit_finding_admits_p3_gate(conn):
            run_id = f"p3_bypass_{stamp.strftime('%Y%m%d_%H%M%S')}"
            n = 0
            with conn.transaction():                           # a savepoint: a failure here must not undo the ingestion_log rows
                for isin, check, message in rows:
                    if isin is not None:
                        continue                               # per-fund detail lives in ingestion_log; one finding per check
                    conn.execute(
                        "INSERT INTO audit_finding (run_id, domain, block, rule_id, rule_class, severity, group_key, isin, evidence) "
                        "VALUES (%s, %s, 'GATE', %s, 'HARD_INVARIANT', 'ALARM', %s, NULL, %s)",
                        (run_id, AUDIT_DOMAIN, STEP_BYPASS, check, message))
                    n += 1
            out["audit_finding"] = n
    except Exception as exc:                                   # secondary leg: never turns an audited bypass into a crash
        print(f"[gate_bypass] audit_finding leg skipped: {type(exc).__name__}: {str(exc)[:120]}")

    try:
        from shared import cycle_telemetry
        names = ", ".join(c for _, c, _m in rows if _ is None) or "-"
        out["cycle_flag"] = bool(cycle_telemetry.raise_flag(
            FLAG_CODE, "HIGH", scope=scenario, detail=f"{user}@{host} bypassed: {names}; pending={len(pending)}"))
    except Exception:
        pass
    return out
