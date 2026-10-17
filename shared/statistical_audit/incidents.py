# shared/statistical_audit/incidents.py
# -*- coding: utf-8 -*-
"""
Closed-loop remediation of audit defects (FND-0264, GOV-05; control G-REMEDIATION in doc/reglas/GOBIERNO_CONTROLES.md). Pure.

A CRITICAL finding (shared/statistical_audit/criticality.py) becomes an INCIDENT, keyed by domain + rule + group (one incident per rule and
group, not per fund: the ISINs it affects are a snapshot on the incident). It is closed by EVIDENCE, never by a claim:

    OPEN       a CRITICAL finding with no live incident (decision D7: opened on the FIRST occurrence; the policy is a parameter).
    CERTIFIED  a later re-audit shows it fixed: not INCOMPLETE, FULL scope or a sample that covers the affected ISINs, and none of those
               ISINs violates the rule any more (a rule absent from a complete re-audit counts as fixed); when the fix was a recompute,
               also proof that the recompute really ran (recompute_gate: the input_hash changed). A recompute alone certifies nothing.
    ACCEPTED   the defect is not fixed but every affected ISIN is covered by an active accepted residual (FND-0263: ticket + expiry).
    CLOSED     the owner's backlog ticket was closed on top of either of the two states above.

The auditor stays read-only: it detects and certifies; recomputes and corrections run in their owning pipelines. The database writes are in
persistence.py (the package's only writer); the closure gate is scripts/ops/close_release.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Mapping, Optional, Sequence

from shared.statistical_audit.criticality import is_critical

STATE_OPEN, STATE_CERTIFIED, STATE_ACCEPTED, STATE_CLOSED = "OPEN", "CERTIFIED", "ACCEPTED", "CLOSED"
STATES = (STATE_OPEN, STATE_CERTIFIED, STATE_ACCEPTED, STATE_CLOSED)
LIVE_STATES = (STATE_OPEN, STATE_CERTIFIED, STATE_ACCEPTED)

POLICY_FIRST, POLICY_RECURRENCE = "first", "recurrence"
POLICIES = (POLICY_FIRST, POLICY_RECURRENCE)

FIX_RECOMPUTE, FIX_CODE, FIX_DATA, FIX_ACCEPT = "RECOMPUTE", "CODE", "DATA", "ACCEPT"
FIX_KINDS = (FIX_RECOMPUTE, FIX_CODE, FIX_DATA, FIX_ACCEPT)

ACCEPTED_EVIDENCE_PREFIX = "ACCEPTED RESIDUAL"      # accepted_residuals.apply_accepted prefixes the evidence of a fully accepted finding


def finding_key(domain: str, rule_id: str, group_key: str) -> str:
    return f"{domain}|{rule_id}|{group_key}"


@dataclass(frozen=True)
class Candidate:
    """A would-be incident: what `--plan` prints (the D7 shadow log) and `--open` writes."""
    domain: str
    rule_id: str
    group_key: str
    isins: tuple
    evidence: str
    run_id: str

    @property
    def key(self) -> str:
        return finding_key(self.domain, self.rule_id, self.group_key)


def critical_findings(domain: str, findings: Iterable[Mapping[str, Any]]) -> list:
    """The CRITICAL findings of a run that still need a fix: defects under the criticality model, minus those fully covered by an
    accepted residual (persisted with INFO severity and an ACCEPTED RESIDUAL evidence, accepted_residuals.apply_accepted)."""
    return [f for f in findings
            if is_critical(domain, f.get("rule_class"), f.get("block"), f.get("rule_id"))
            and not str(f.get("evidence") or "").startswith(ACCEPTED_EVIDENCE_PREFIX)]


def incidents_to_open(domain: str, run_id: str, findings: Iterable[Mapping[str, Any]], isins_by_key: Mapping[str, Iterable[str]],
                      live_keys: Iterable[str], previous_keys: Iterable[str] = (), policy: str = POLICY_FIRST) -> list:
    """Candidates for one run. `live_keys` = keys with an OPEN / CERTIFIED / ACCEPTED incident (never opened twice); `previous_keys` = the
    CRITICAL keys of the previous full run of the domain (policy RECURRENCE opens only a key seen in both). Ordered by key. Pure."""
    if policy not in POLICIES:
        raise ValueError(f"policy must be one of {POLICIES}")
    live, previous = set(live_keys), set(previous_keys)
    out: dict = {}
    for f in critical_findings(domain, findings):
        group_key = f.get("group_key") or f.get("rule_id")
        key = finding_key(domain, f["rule_id"], group_key)
        if key in live or key in out:
            continue
        if policy == POLICY_RECURRENCE and key not in previous:
            continue
        isins = tuple(sorted(set(isins_by_key.get(key, ())) | ({f["isin"]} if f.get("isin") else set())))
        out[key] = Candidate(domain, f["rule_id"], group_key, isins, str(f.get("evidence") or ""), run_id)
    return [out[k] for k in sorted(out)]


# ---- certification ---------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Reaudit:
    """What certification needs to know about a re-audit run (control.audit_run header + its violators)."""
    run_id: str
    domain: str
    scope: str                                        # FULL | SAMPLE
    status: str                                       # PASS | PASS_WITH_WARNINGS | FAIL | INCOMPLETE
    finished_at: Optional[datetime]
    violators: Mapping[str, frozenset] = field(default_factory=dict)       # finding key -> ISINs still violating it
    scope_isins: Optional[frozenset] = None           # SAMPLE runs: the ISINs they covered (header manifest)


@dataclass(frozen=True)
class Certification:
    ok: bool
    reasons: tuple = ()


def certify(incident: Mapping[str, Any], reaudit: Reaudit, recompute_ok: Optional[bool] = None) -> Certification:
    """May `incident` be CERTIFIED by `reaudit`? Every failed condition is a reason. `recompute_ok` = recompute_gate verdict (True: every
    affected (isin, metric_version) input_hash changed), None = not provided. Pure."""
    why = []
    if incident["state"] != STATE_OPEN:
        why.append(f"the incident is {incident['state']}, not OPEN")
    if reaudit.domain != incident["domain"]:
        why.append(f"the re-audit is of {reaudit.domain}, the incident of {incident['domain']}")
    if reaudit.run_id == incident["opened_run_id"]:
        why.append("the re-audit is the run that opened the incident: a fix needs a LATER run")
    if reaudit.finished_at is not None and incident.get("opened_at") is not None and reaudit.finished_at <= incident["opened_at"]:
        why.append("the re-audit finished before the incident was opened")
    if reaudit.status == "INCOMPLETE":
        why.append("the re-audit is INCOMPLETE: it could not look, so it cannot certify")
    affected = frozenset(incident.get("isins") or ())
    if reaudit.scope != "FULL":
        if reaudit.scope_isins is None or not affected <= reaudit.scope_isins:
            missing = len(affected - (reaudit.scope_isins or frozenset()))
            why.append(f"a SAMPLE re-audit must cover every affected ISIN ({missing} not covered)")
    still = affected & frozenset(reaudit.violators.get(incident["finding_key"], ()))
    if still:
        why.append(f"{len(still)} affected ISIN(s) still violate the rule in the re-audit, e.g. {sorted(still)[:3]}")
    if incident.get("fix_kind") == FIX_RECOMPUTE and recompute_ok is not True:
        why.append("the fix was a recompute: prove it ran (recompute_gate --verify-recompute: every input_hash changed)"
                   if recompute_ok is None else "the recompute check failed: some input_hash is unchanged")
    if incident.get("fix_kind") == FIX_ACCEPT:
        why.append("an ACCEPT incident is not certified, it is accepted through an active residual")
    return Certification(not why, tuple(why))


def acceptance(incident: Mapping[str, Any], accepted_isins: Iterable[str]) -> Certification:
    """May `incident` be marked ACCEPTED? Every affected ISIN must carry an ACTIVE accepted residual for the rule (FND-0263). Pure."""
    why = []
    if incident["state"] != STATE_OPEN:
        why.append(f"the incident is {incident['state']}, not OPEN")
    affected = frozenset(incident.get("isins") or ())
    if not affected:
        why.append("the incident names no ISIN: nothing can be accepted for it")
    uncovered = affected - frozenset(accepted_isins)
    if uncovered:
        why.append(f"{len(uncovered)} affected ISIN(s) have no active accepted residual, e.g. {sorted(uncovered)[:3]}")
    return Certification(not why, tuple(why))


def closable(ap_id: str, incidents: Sequence[Mapping[str, Any]]) -> Certification:
    """May the backlog ticket `ap_id` be closed? Every incident linked to it must be CERTIFIED (with its certifying run) or ACCEPTED, or
    already CLOSED. An AP with no linked incident is not an audit ticket: closable. Pure."""
    why = []
    for inc in incidents:
        if inc.get("ap_id") != ap_id or inc["state"] in (STATE_CLOSED, STATE_ACCEPTED):
            continue
        if inc["state"] == STATE_CERTIFIED and inc.get("certified_run_id"):
            continue
        why.append(f"incident #{inc['incident_id']} ({inc['finding_key']}) is {inc['state']}: it needs a certifying re-audit run "
                   "(scripts/audit/audit_incident.py --certify) or an active accepted residual (--accept)")
    return Certification(not why, tuple(why))


# ---- read-only loaders -------------------------------------------------------------------------------------------------------------------
INCIDENT_COLUMNS = ("incident_id", "finding_key", "domain", "rule_id", "group_key", "state", "ap_id", "root_cause", "corrective_action",
                    "fix_kind", "affected_calc_version", "isins", "opened_run_id", "opened_at", "certified_run_id", "certified_at", "closed_at")
_INCIDENTS_SQL = ("SELECT incident_id, finding_key, domain, rule_id, group_key, state, ap_id, root_cause, corrective_action, fix_kind, "
                  "affected_calc_version, isins, opened_run_id, opened_at, certified_run_id, certified_at, closed_at "
                  "FROM audit_incident ORDER BY incident_id")


def load_incidents(conn: "psycopg.Connection") -> list:
    """Every incident as a dict (INCIDENT_COLUMNS). Read-only; raises when the table is missing or unreadable (the callers decide)."""
    return [dict(zip(INCIDENT_COLUMNS, tuple(r))) for r in conn.execute(_INCIDENTS_SQL).fetchall()]
