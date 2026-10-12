"""Accepted-residual baseline (FND-0234(e)): findings the owner has decided to live with, until an expiry.

A hard-invariant violation that the pipeline cannot repair yet (e.g. the 92 funds whose ongoing charge was copied from the
ACI row, FND-0034) used to raise the same ALARM in every audit run. This module lets such a (domain, rule, ISIN) be ACCEPTED
with the owning AP and a mandatory expiry:

* a finding whose violating ISINs are ALL accepted is downgraded to INFO / rule_class ACCEPTED_RESIDUAL (still reported and
  persisted, so nothing is hidden, and --mode check no longer blocks on it);
* a finding with ANY not-accepted ISIN stays as it was, restricted to the new ISINs (distance = how many are new);
* an expired acceptance counts as not accepted, so a forgotten residual comes back by itself.

Only findings that carry `violating_isins` can be accepted; group-level statistics (BLOCK1-3) cannot. The acceptance key is
set membership, not a value: a violation that gets worse for an already-accepted ISIN is not detected (the expiry bounds it).

Pure functions plus read-only loaders; the writers live in persistence.py (the package's only DB writer). Since FND-0263 the table is
the current-state projection of the append-only history control.audit_exception_event (fold_events below).
"""
from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Mapping, Sequence

ACCEPTED_CLASS = "ACCEPTED_RESIDUAL"

# {rule_id: {isin: (ap_id, expires_at)}}
Accepted = Mapping[str, Mapping[str, tuple]]


def load_accepted(conn: "psycopg.Connection", domain: str, today: date | None = None) -> dict:
    """Active (not expired) acceptances for `domain`. Read-only. Raises if the table is missing: the caller decides
    whether that means "nothing accepted" (see run_statistical_audit._apply_accepted_residuals)."""
    today = today or date.today()
    rows = conn.execute(
        "SELECT rule_id, isin, ap_id, expires_at FROM audit_accepted_finding "
        "WHERE domain = %s AND expires_at >= %s",
        (domain, today),
    ).fetchall()
    out: dict = {}
    for rule_id, isin, ap_id, expires_at in rows:
        out.setdefault(rule_id, {})[isin] = (ap_id, expires_at)
    return out


def apply_accepted(findings: Sequence[Mapping[str, Any]], accepted: Accepted) -> tuple:
    """Returns (new_findings, notes). `findings` is not mutated; each changed finding is a copy. `notes` are one-line
    human summaries for the report (empty when nothing was accepted)."""
    new_findings: list = []
    notes: list = []
    for f in findings:
        isins = tuple(f.get("violating_isins") or ())
        by_isin = accepted.get(f.get("rule_id"), {}) if isins else {}
        hit = [i for i in isins if i in by_isin]
        if not hit:
            new_findings.append(f)
            continue
        remaining = [i for i in isins if i not in by_isin]
        aps = sorted({by_isin[i][0] for i in hit})
        until = min(by_isin[i][1] for i in hit)
        g = dict(f)
        if not remaining:
            g["severity"] = "INFO"
            g["accepted_from_class"] = f.get("rule_class")      # control.audit_finding's CHECK has no ACCEPTED_RESIDUAL class
            g["rule_class"] = ACCEPTED_CLASS
            g["evidence"] = (f"ACCEPTED RESIDUAL ({', '.join(aps)}, until {until}): {len(hit)}/{len(isins)} ISINs; "
                             f"was: {f.get('evidence')}")
            notes.append(f"{f['rule_id']}: {len(hit)} ISINs accepted ({', '.join(aps)}, until {until}) -> INFO")
        else:
            g["violating_isins"] = tuple(remaining)
            g["distance"] = float(len(remaining))
            g["evidence"] = (f"{len(remaining)} NEW ISINs violate ({len(hit)} more accepted under "
                             f"{', '.join(aps)}); was: {f.get('evidence')}")
            notes.append(f"{f['rule_id']}: {len(hit)} accepted, {len(remaining)} NEW ISINs still violate")
        new_findings.append(g)
    return new_findings, notes


def expiring_soon(conn: "psycopg.Connection", domain: str, within_days: int = 30, today: date | None = None) -> list:
    """ACTIVE acceptances that lapse within `within_days` (inclusive), grouped by (rule, AP): [(rule_id, ap_id, expires_at, n_isins)],
    soonest first. Read-only. An expired acceptance is not listed (it already counts as not accepted: see load_accepted). Raises when the
    table is missing; the callers decide whether that is silence or a warning (FND-0251)."""
    from datetime import timedelta
    today = today or date.today()
    rows = conn.execute(
        "SELECT rule_id, ap_id, MIN(expires_at), COUNT(*) FROM audit_accepted_finding "
        "WHERE domain = %s AND expires_at >= %s AND expires_at <= %s GROUP BY rule_id, ap_id ORDER BY 3, 1, 2",
        (domain, today, today + timedelta(days=within_days))).fetchall()
    return [(r[0], r[1], r[2], int(r[3])) for r in rows]


def attention_lines(conn: "psycopg.Connection", domain: str, within_days: int = 30, today: date | None = None,
                    ap_status=None) -> list:
    """Sentences for the audit report, the promotion and the cycle report: residuals about to lapse, and residuals whose AP is CLOSED
    (the fix was declared done yet the violation is still accepted: either revoke it or the fix did not work). `ap_status(ap_id)` is
    injectable (shared.backlog_client.get_action_point_status: 'LOOKUP_FAILED' is never read as 'closed'). Never raises."""
    today = today or date.today()
    lines: list = []
    try:
        if conn.execute("SELECT to_regclass('audit_accepted_finding')").fetchone()[0] is None:
            return lines                                        # migration not applied: nothing is accepted, nothing can lapse
        for rule_id, ap_id, expires_at, n in expiring_soon(conn, domain, within_days, today):
            lines.append(f"residual aceptado {rule_id} ({n} ISINs, {ap_id}) caduca el {expires_at} "
                         f"({(expires_at - today).days} dias): renovar con accept_audit_residual.py o corregir la causa antes")
        if ap_status is not None:
            aps = conn.execute("SELECT DISTINCT ap_id FROM audit_accepted_finding WHERE domain = %s AND expires_at >= %s ORDER BY 1",
                               (domain, today)).fetchall()
            for (ap_id,) in aps:
                if ap_status(ap_id) == "CLOSED":
                    lines.append(f"residual aceptado bajo {ap_id}, que esta CLOSED: la correccion se dio por hecha pero la "
                                 "violacion sigue aceptada; revocar la aceptacion o reabrir el ticket")
    except Exception as exc:                                    # noqa: BLE001 -- a monitor never breaks its caller
        try:
            conn.rollback()
        except Exception:                                       # noqa: BLE001
            pass
        lines.append(f"no se pudo evaluar la caducidad de residuales aceptados ({type(exc).__name__})")
    return lines


# ---- exception history (FND-0263, GOV-04) -------------------------------------------------------------------------------------------
# control.audit_exception_event is the append-only history; control.audit_accepted_finding is its current-state projection, which every
# reader keeps using. fold_events is THE definition of that projection: the writer refreshes the table from it, and drift() is how a
# reader proves the table still says what the history says.

GRANT_TYPES = ("GRANT", "RENEW")
REVOKE_CAUSES = ("OWNER", "AP_CLOSED", "RESOLVED")
EVENT_COLUMNS = ("event_id", "event_ts", "event_type", "domain", "rule_id", "isins", "ap_id", "reason", "expires_at", "cause",
                 "ref_event_id", "actor")


def fold_events(events: Iterable[Mapping[str, Any]]) -> dict:
    """{(rule_id, isin): {"ap_id", "reason", "expires_at", "granted_by", "approved_by", "approved_at", "grant_event_id"}} -- the latest
    APPROVED grant of each key that no later REVOKE of that same grant withdrew. Expired keys stay (readers filter by date, exactly like
    the table always did). Pure; `events` of ONE domain, any order (folded by event_id)."""
    grants: dict = {}
    revoked: dict = {}                                           # grant_event_id -> ISINs withdrawn so far (an APPROVE never revives them)
    active: dict = {}
    for e in sorted(events, key=lambda e: e["event_id"]):
        kind = e["event_type"]
        if kind in GRANT_TYPES:
            grants[e["event_id"]] = e
        elif kind == "APPROVE":
            g = grants.get(e["ref_event_id"])
            if g is None:
                continue
            for isin in g["isins"]:
                if isin in revoked.get(g["event_id"], ()):
                    continue
                active[(g["rule_id"], isin)] = {
                    "ap_id": g["ap_id"], "reason": g["reason"], "expires_at": g["expires_at"], "granted_by": g["actor"],
                    "approved_by": e["actor"], "approved_at": e["event_ts"], "grant_event_id": g["event_id"]}
        elif kind == "REVOKE":
            g = grants.get(e["ref_event_id"])
            if g is None:
                continue
            for isin in (e.get("isins") or g["isins"]):
                revoked.setdefault(g["event_id"], set()).add(isin)
                cur = active.get((g["rule_id"], isin))
                if cur is not None and cur["grant_event_id"] == g["event_id"]:
                    del active[(g["rule_id"], isin)]
    return active


def grant_state(events: Iterable[Mapping[str, Any]], grant_event_id: int) -> str:
    """'MISSING' | 'NOT_A_GRANT' | 'PENDING' | 'APPROVED' | 'REVOKED' for one grant (REVOKED wins over APPROVED: a fully revoked
    grant, i.e. a REVOKE without an ISIN subset). Pure."""
    events = list(events)
    g = next((e for e in events if e["event_id"] == grant_event_id), None)
    if g is None:
        return "MISSING"
    if g["event_type"] not in GRANT_TYPES:
        return "NOT_A_GRANT"
    refs = [e for e in events if e.get("ref_event_id") == grant_event_id]
    if any(e["event_type"] == "REVOKE" and not e.get("isins") for e in refs):
        return "REVOKED"
    return "APPROVED" if any(e["event_type"] == "APPROVE" for e in refs) else "PENDING"


def drift(projection: Iterable[tuple], folded: Mapping[tuple, Mapping[str, Any]]) -> list:
    """Disagreements between the table and the history: [(rule_id, isin, table (ap_id, expires_at) or None, history (...) or None)].
    `projection` = rows (rule_id, isin, ap_id, expires_at) of control.audit_accepted_finding for one domain. Empty = consistent. Pure."""
    table = {(r[0], r[1]): (r[2], r[3]) for r in projection}
    hist = {k: (v["ap_id"], v["expires_at"]) for k, v in folded.items()}
    return [(k[0], k[1], table.get(k), hist.get(k)) for k in sorted(set(table) | set(hist)) if table.get(k) != hist.get(k)]


def load_events(conn: "psycopg.Connection", domain: str) -> list:
    """Every exception event of `domain`, as dicts (EVENT_COLUMNS). Read-only; raises when the table is missing or unreadable (the
    caller decides: with AUDIT_EXCEPTION_EVENTS_ENABLED the promotion refuses)."""
    rows = conn.execute(_EVENTS_SQL, (domain,)).fetchall()
    return [dict(zip(EVENT_COLUMNS, tuple(r))) for r in rows]


# A literal (the EXPLAIN sweep validates it against the real DDL); its column list equals EVENT_COLUMNS (asserted by the tests).
_EVENTS_SQL = ("SELECT event_id, event_ts, event_type, domain, rule_id, isins, ap_id, reason, expires_at, cause, ref_event_id, actor "
               "FROM audit_exception_event WHERE domain = %s ORDER BY event_id")


def verify_governed(conn: "psycopg.Connection", domain: str) -> dict:
    """The governed view of the residuals of `domain`: {"events": n, "drift": [...], "sources": {(rule_id, isin): grant_event_id}}.
    Raises when the history cannot be read. Read-only."""
    events = load_events(conn, domain)
    folded = fold_events(events)
    projection = conn.execute("SELECT rule_id, isin, ap_id, expires_at FROM audit_accepted_finding WHERE domain = %s",
                              (domain,)).fetchall()
    return {"events": len(events), "drift": drift([tuple(r) for r in projection], folded),
            "sources": {k: v["grant_event_id"] for k, v in folded.items()}}


# FND-0251 (owner decision 2026-10-11): an acceptance lives at most 90 days (it was 180). A residual that needs longer is RENEWED, which is
# a new decision with a new look at the finding, not a longer waiver. Acceptances made before this change keep their own expiry.
MAX_RESIDUAL_DAYS = 90


def expiry_after(days: int, today: date | None = None, max_days: int = MAX_RESIDUAL_DAYS) -> date:
    """Expiry date for an acceptance, capped at MAX_RESIDUAL_DAYS so that no residual can be accepted for ever."""
    from datetime import timedelta
    if days < 1:
        raise ValueError("days must be >= 1")
    return (today or date.today()) + timedelta(days=min(days, max_days))
