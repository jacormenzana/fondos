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

import re
from datetime import date
from typing import Any, Iterable, Mapping, Sequence

ACCEPTED_CLASS = "ACCEPTED_RESIDUAL"

# {rule_id: {isin: (ap_id, expires_at)}}
Accepted = Mapping[str, Mapping[str, tuple]]

# ---- standing acceptances (owner decision 2026-11-06, replaces the 90-day clock for defects of the DOCUMENT) --------------------------------------
# A violation that comes from what the publisher PRINTED (PUBLISHER_DEFECT: a KID whose own figures contradict each other) or from a layout the
# extractor cannot read (EXTRACTION_LIMIT) does not get better by waiting, so a calendar expiry only creates a renewal chore. A standing
# acceptance has no calendar expiry (expires_at = STANDING_UNTIL); instead it is bound to its EVIDENCE: the reason carries a fingerprint of each
# fund's stored KID text, and load_accepted re-derives that fingerprint on every read. While the document is the one the owner decided on, the
# acceptance stands; the day the publisher issues a new KID the fingerprint changes, the acceptance lapses by itself, the finding is an ALARM
# again and attention_lines says why. Costs domain only: the evidence is the KID text (fund_kiid_metadata.raw_kiid_text).
STANDING_CLASSES = ("PUBLISHER_DEFECT", "EXTRACTION_LIMIT")
STANDING_UNTIL = date(2099, 12, 31)
_STANDING_RE = re.compile(r"\[STANDING:(" + "|".join(STANDING_CLASSES) + r")\|([^\]]*)\]")
FINGERPRINT_SQL = "left(md5(coalesce(raw_kiid_text, '')), 12)"


def standing_marker(cls: str, fingerprints: Mapping[str, str]) -> str:
    """The suffix of a standing grant's reason: class and the evidence fingerprint of every ISIN. Pure."""
    if cls not in STANDING_CLASSES:
        raise ValueError(f"class must be one of {STANDING_CLASSES}")
    return f"[STANDING:{cls}|" + ";".join(f"{i}={fp}" for i, fp in sorted(fingerprints.items())) + "]"


def parse_standing(reason: str | None):
    """(class, {isin: fingerprint}) of a standing reason, or None when the reason is not a standing one. Pure."""
    m = _STANDING_RE.search(reason or "")
    if m is None:
        return None
    return m.group(1), dict(p.split("=", 1) for p in m.group(2).split(";") if "=" in p)


def current_fingerprints(conn: "psycopg.Connection", isins: Iterable[str]) -> dict:
    """{isin: fingerprint of its stored KID text} (the same function at grant time and at every read). Read-only."""
    isins = sorted(set(isins))
    if not isins:
        return {}
    rows = conn.execute(f"SELECT isin, {FINGERPRINT_SQL} FROM fund_kiid_metadata WHERE isin = ANY(%s)", (isins,)).fetchall()
    return {r[0]: r[1] for r in rows}


def standing_status(rows: Iterable[tuple], current: Mapping[str, str]) -> tuple:
    """(valid_keys, lapsed) of the STANDING rows (rule_id, isin, ap_id, reason): a row is valid while the fingerprint recorded for its ISIN equals
    `current[isin]`. `lapsed` = [(rule_id, isin, ap_id)] whose document changed (or is gone). Rows that are not standing are ignored. Pure."""
    valid, lapsed = set(), []
    for rule_id, isin, ap_id, reason in rows:
        st = parse_standing(reason)
        if st is None:
            continue
        if st[1].get(isin) is not None and current.get(isin) == st[1][isin]:
            valid.add((rule_id, isin))
        else:
            lapsed.append((rule_id, isin, ap_id))
    return valid, lapsed


def _standing_rows(conn: "psycopg.Connection", domain: str, today: date) -> list:
    rows = conn.execute(
        "SELECT rule_id, isin, ap_id, reason FROM audit_accepted_finding WHERE domain = %s AND expires_at >= %s AND reason LIKE %s",
        (domain, today, "%[STANDING:%")).fetchall()
    return [tuple(r) for r in rows]


def load_accepted(conn: "psycopg.Connection", domain: str, today: date | None = None) -> dict:
    """Active (not expired) acceptances for `domain`. Read-only. Raises if the table is missing: the caller decides
    whether that means "nothing accepted" (see run_statistical_audit._apply_accepted_residuals). A STANDING acceptance is active only while the
    fingerprint of its fund's KID text equals the one recorded at the decision (see STANDING_CLASSES)."""
    today = today or date.today()
    rows = conn.execute(
        "SELECT rule_id, isin, ap_id, expires_at FROM audit_accepted_finding "
        "WHERE domain = %s AND expires_at >= %s",
        (domain, today),
    ).fetchall()
    standing = _standing_rows(conn, domain, today)
    valid, _lapsed = standing_status(standing, current_fingerprints(conn, {r[1] for r in standing})) if standing else (set(), [])
    standing_keys = {(r[0], r[1]) for r in standing}
    out: dict = {}
    for rule_id, isin, ap_id, expires_at in rows:
        if (rule_id, isin) in standing_keys and (rule_id, isin) not in valid:
            continue                                            # the document changed since the decision: it counts as not accepted
        out.setdefault(rule_id, {})[isin] = (ap_id, expires_at)
    return out


def _until_text(until: date) -> str:
    return "standing, evidence-validated" if until >= STANDING_UNTIL else f"until {until}"


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
            g["evidence"] = (f"ACCEPTED RESIDUAL ({', '.join(aps)}, {_until_text(until)}): {len(hit)}/{len(isins)} ISINs; "
                             f"was: {f.get('evidence')}")
            notes.append(f"{f['rule_id']}: {len(hit)} ISINs accepted ({', '.join(aps)}, {_until_text(until)}) -> INFO")
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
        standing = _standing_rows(conn, domain, today)
        if standing:
            _valid, lapsed = standing_status(standing, current_fingerprints(conn, {r[1] for r in standing}))
            for rule_id, isin, ap_id in sorted(lapsed):
                lines.append(f"aceptacion permanente {rule_id} de {isin} ({ap_id}) CADUCADA: el KID almacenado cambio desde la decision del propietario; "
                             "la violacion vuelve a ser ALARM hasta decidir de nuevo")
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
