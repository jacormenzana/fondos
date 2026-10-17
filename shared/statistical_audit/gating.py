"""
shared/statistical_audit/gating.py -- which audit findings may stop a deployment, and what they touch (FND-0251).

One decision rule (P#11), used by the audit's own `--mode check` and by the P3 promotion gate (`scripts/launch/p3_promote_scenario.py`).
Two axes -- the NATURE of the finding and its SCOPE (does it touch a fund of the scenario being promoted):

                              | fund IN the scenario                        | universe only
    hard defect (is_blocking) | BLOCKS; reasoned override with an AP        | BLOCKS unless every violator has an ACTIVE accepted
                              |                                             | residual (AP + expiry)
    statistical anomaly       | listed per fund, never blocks               | recorded by the audit, nothing else

An ACTIVE accepted residual (`control.audit_accepted_finding`) never covers a fund of the scenario: a residual is a decision to live with a
defect in the universe, not in a portfolio. Scope is derived here at promotion time from `portfolio_weights`, never stored, so a fund that is
universe-only today is on-scenario the moment a later DRAFT selects it.

Evidence is read from the latest PERSISTED run of the domain. Two things keep that honest and are surfaced for the caller to gate on:
`run_ts` (is the run newer than the DRAFT?) and `coverage` (which scenario funds the audit never looked at). Pure functions + one read-only
loader; no pipeline imports (R-7).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable, Mapping, Optional

from shared.statistical_audit import criticality as _crit

# The defect classes. `rule_class` is what the detector asserts; BLOCK2 are the scalar-vs-timeseries / deflation identities
# (a wrong number that every consumer reads); TIMESERIES_DUPLICATE is a corrupt append-only series. FND-0175: BLOCK4 outliers
# (OUTLIER_MAD_Z / ALARM) are review items, not defects -- on the full population they number in the thousands.
# FND-0262: the sets below are VIEWS of the criticality model (shared/statistical_audit/criticality.py owns the data). BLOCK2 is a defect
# block in the P2 domain only (its cost-domain BLOCK2 are population-level coincidence detectors, MINOR): see criticality.py.
BLOCKING_RULE_CLASSES = _crit.CRITICAL_RULE_CLASSES
BLOCKING_BLOCKS = _crit.critical_blocks()
BLOCKING_RULE_IDS = _crit.CRITICAL_RULE_IDS

HELD_OUT_RULE_ID = "SNAPSHOT_HELD_OUT"          # run_statistical_audit._emit_snapshot_held_out_finding
# A run scoped with --isin always ends in this suffix (run_statistical_audit._resolve_run_id) and is never promotion evidence: a 40-ISIN
# sample persisted after the DRAFT would otherwise pass for the latest audit of the whole universe (two-pillar plan, G2 quick win).
SAMPLE_RUN_SUFFIX = "_sample"

COVERAGE_OK = "OK"
COVERAGE_UNKNOWN = "UNKNOWN"                    # the persisted detail disagrees with its own count: re-run the audit


def is_blocking(rule_class: Optional[str], block: Optional[str], rule_id: Optional[str], domain: Optional[str] = None) -> bool:
    """True for a finding that is a DEFECT, i.e. CRITICAL in the criticality model (blocks `--mode check`; blocks a promotion when it
    touches the scenario). `domain` (p2_metrics / cost_attributes) decides what a block means; None = the legacy domain-agnostic view."""
    return _crit.is_critical(domain, rule_class, block, rule_id)


@dataclass(frozen=True)
class DefectFinding:
    """One hard defect as the gate sees it. `isins` = the funds it is about FOR THE LIST IT SITS IN (the scenario funds for
    `on_scenario`, the funds outside it for `universe_only`); () = the detail is not attributable to funds, which the gate treats as
    touching the scenario (a defect nobody can place cannot be assumed to be elsewhere)."""
    rule_id: str
    group_key: str
    evidence: str
    isins: tuple = ()


@dataclass
class AuditScope:
    run_id: Optional[str] = None
    run_ts: Optional[datetime] = None
    on_scenario: list = field(default_factory=list)              # [DefectFinding] open hard defects touching the scenario
    universe_only: list = field(default_factory=list)            # [DefectFinding] open hard defects, none in the scenario
    residuals_on_scenario: list = field(default_factory=list)    # [(rule_id, isin, ap_id, expires_at)] active acceptances on scenario funds
    covered_by_residual: list = field(default_factory=list)      # [(rule_id, isin, ap_id, expires_at)] violators OUTSIDE the scenario that an
                                                                 # active acceptance covers (the sign-off line names them)
    anomalies_on_scenario: list = field(default_factory=list)    # [(isin, rule_id, group_key)] statistical ALARMs, informational
    held_out_on_scenario: tuple = ()                             # scenario funds the snapshot gate held out of the cross-sectional blocks
    outside_population: tuple = ()                               # scenario funds that are not In_Current_Universe = 1 now
    coverage: str = COVERAGE_OK
    coverage_detail: str = ""
    isin_detail_available: bool = True                           # audit_finding_isin readable (owner migration applied)
    selection_note: str = ""                                     # why no run qualified as evidence (completeness contract), "" otherwise

    @property
    def has_run(self) -> bool:
        return self.run_id is not None


def scope_findings(findings: Iterable[Mapping[str, Any]], finding_isins: Mapping[tuple, Iterable[str]], scenario_isins: Iterable[str],
                   accepted: Mapping[str, Mapping[str, tuple]], *, run_id: Optional[str] = None, run_ts: Optional[datetime] = None,
                   domain: Optional[str] = None) -> AuditScope:
    """Pure. `findings`: persisted audit_finding rows (rule_id, rule_class, block, group_key, isin, evidence); `finding_isins`:
    {(rule_id, group_key): {isin}} from audit_finding_isin; `accepted`: ACTIVE acceptances {rule_id: {isin: (ap_id, expires_at)}}."""
    scenario = frozenset(scenario_isins)
    scope = AuditScope(run_id=run_id, run_ts=run_ts)
    for rule_id, by_isin in accepted.items():                    # a residual on a fund of the scenario is a blocker whatever fired today
        for isin, (ap_id, expires_at) in by_isin.items():
            if isin in scenario:
                scope.residuals_on_scenario.append((rule_id, isin, ap_id, expires_at))
    scope.residuals_on_scenario.sort()

    for f in findings:
        rule_id, group_key = f.get("rule_id"), f.get("group_key") or f.get("rule_id")
        if not is_blocking(f.get("rule_class"), f.get("block"), rule_id, domain):
            continue
        isins = {f["isin"]} if f.get("isin") else set(finding_isins.get((rule_id, group_key), ()))
        evidence = str(f.get("evidence") or "")
        if not isins:                                            # not attributable: assume it touches the scenario
            scope.on_scenario.append(DefectFinding(rule_id, group_key, evidence, ()))
            continue
        by_isin = accepted.get(rule_id, {})
        open_isins = {i for i in isins if i not in by_isin}      # accepted ones are reported as residuals, not defects
        for i in sorted((isins & set(by_isin)) - scenario):
            scope.covered_by_residual.append((rule_id, i, *by_isin[i]))
        on, off = sorted(open_isins & scenario), sorted(open_isins - scenario)
        if on:
            scope.on_scenario.append(DefectFinding(rule_id, group_key, evidence, tuple(on)))
        if off:
            scope.universe_only.append(DefectFinding(rule_id, group_key, evidence, tuple(off)))
    return scope


def coverage_of(held_out_count: Optional[float], held_out_isins: Iterable[str], scenario_isins: Iterable[str]) -> tuple:
    """(status, held_out_on_scenario, detail). `held_out_count` = the SNAPSHOT_HELD_OUT finding's own count (None = no such finding: nothing
    was held out). The gate never INFERS 'held out' from missing rows: when the persisted ISIN detail does not add up to the count (a run
    persisted before the detail existed, or a table that is missing) coverage is UNKNOWN, not a per-fund verdict."""
    isins = set(held_out_isins)
    if held_out_count is None:
        return COVERAGE_OK, (), ""
    if len(isins) != int(round(held_out_count)):
        return (COVERAGE_UNKNOWN, (),
                f"the audit held out {int(round(held_out_count))} ISINs but only {len(isins)} are persisted; re-run "
                "run_statistical_audit.py --domain p2 --persist")
    return COVERAGE_OK, tuple(sorted(isins & set(scenario_isins))), ""


# ---- read-only loader -----------------------------------------------------------------------------------------------------------

def select_evidence_run(conn, domain: str, contract: bool) -> tuple:
    """((run_id, run_ts) or None, note). Contract OFF: the latest audit_finding run id that is not a sample run. Contract ON (FND-0261,
    decision D4): the latest control.audit_run header with scope FULL and status other than INCOMPLETE -- a FAIL run IS evidence (it is
    what blocks), and a run without a header (persisted before the table, or a crash: the header is in the rows' transaction) is not.
    run_ts = the run's started_at, when its data was read: the conservative reference against the DRAFT build time (an audit that read
    the data before the build does not describe it, however late it was persisted)."""
    if not contract:
        run = conn.execute(
            "SELECT run_id, MAX(detected_at) FROM audit_finding WHERE domain = %s AND right(run_id, 7) <> '_sample' GROUP BY run_id "
            "ORDER BY MAX(detected_at) DESC LIMIT 1", (domain,)).fetchone()
        return (None if run is None else (run[0], run[1])), ""
    if conn.execute("SELECT to_regclass('audit_run')").fetchone()[0] is None:
        return None, ("control.audit_run no existe (AUDIT_COMPLETENESS_CONTRACT_ENABLED exige la tabla): aplique "
                      "scripts/ops/migrate_audit_run.py --apply y persista una auditoria completa")
    run = conn.execute(
        "SELECT run_id, COALESCE(started_at, finished_at) AS run_ts FROM audit_run WHERE domain = %s AND scope = 'FULL' "
        "AND status <> 'INCOMPLETE' ORDER BY COALESCE(started_at, finished_at) DESC LIMIT 1", (domain,)).fetchone()
    if run is None:
        return None, "ninguna auditoria de ambito FULL con estado distinto de INCOMPLETE en control.audit_run"
    return (run[0], run[1]), ""


def load_scope(conn, domain: str, scenario_isins: Iterable[str], accepted: Optional[Mapping[str, Mapping[str, tuple]]] = None,
               contract: Optional[bool] = None) -> AuditScope:
    """AuditScope of the evidence run of `domain` (select_evidence_run: never a sample run; with the completeness contract never an
    INCOMPLETE one) for `scenario_isins`. Read-only. `accepted` = the ACTIVE acceptances (accepted_residuals.load_accepted; the caller
    decides what a missing table means). `contract` None = shared.config.AUDIT_COMPLETENESS_CONTRACT_ENABLED."""
    if contract is None:
        from shared.config import AUDIT_COMPLETENESS_CONTRACT_ENABLED as contract
    scenario = sorted(set(scenario_isins))
    accepted = accepted or {}
    run, note = select_evidence_run(conn, domain, contract)
    if run is None:
        scope = scope_findings((), {}, scenario, accepted)
        scope.selection_note = note
        return scope
    return scope_for_run(conn, domain, run[0], run[1], scenario, accepted)


def scope_for_run(conn, domain: str, run_id: str, run_ts, scenario_isins: Iterable[str],
                  accepted: Optional[Mapping[str, Mapping[str, tuple]]] = None) -> AuditScope:
    """AuditScope of ONE persisted run for `scenario_isins` (load_scope picks the evidence run; the cost-gate replay, FND-0265 / D8, walks
    the past runs through this same function). Read-only."""
    scenario = sorted(set(scenario_isins))
    accepted = accepted or {}
    cols = ("rule_id", "rule_class", "block", "group_key", "isin", "evidence", "distance")
    rows = conn.execute(
        "SELECT rule_id, rule_class, block, group_key, isin, evidence, distance FROM audit_finding "
        "WHERE run_id = %s AND domain = %s AND (rule_class = 'HARD_INVARIANT' OR block = 'BLOCK2' "
        "OR rule_id = 'TIMESERIES_DUPLICATE' OR rule_id = 'SNAPSHOT_HELD_OUT')", (run_id, domain)).fetchall()
    findings = [dict(zip(cols, r)) for r in rows]

    detail_ok = conn.execute("SELECT to_regclass('audit_finding_isin')").fetchone()[0] is not None
    finding_isins: dict = {}
    if detail_ok:
        for rule_id, group_key, isin in conn.execute(
                "SELECT rule_id, group_key, isin FROM audit_finding_isin WHERE run_id = %s AND domain = %s", (run_id, domain)).fetchall():
            finding_isins.setdefault((rule_id, group_key), set()).add(isin)

    scope = scope_findings(findings, finding_isins, scenario, accepted, run_id=run_id, run_ts=run_ts, domain=domain)
    scope.isin_detail_available = detail_ok

    held = next((f for f in findings if f["rule_id"] == HELD_OUT_RULE_ID), None)
    held_isins = {i for (rid, _gk), s in finding_isins.items() if rid == HELD_OUT_RULE_ID for i in s}
    scope.coverage, scope.held_out_on_scenario, scope.coverage_detail = coverage_of(
        None if held is None else held.get("distance"), held_isins, scenario)

    if scenario:
        scope.anomalies_on_scenario = [tuple(r) for r in conn.execute(
            "SELECT isin, rule_id, group_key FROM audit_finding WHERE run_id = %s AND domain = %s "
            "AND severity = 'ALARM' AND rule_class = 'STATISTICAL_ANOMALY' AND isin = ANY(%s) ORDER BY isin, rule_id, group_key",
            (run_id, domain, scenario)).fetchall()]
        in_universe = {r[0] for r in conn.execute(
            "SELECT isin FROM fund_master WHERE isin = ANY(%s) AND in_current_universe = 1", (scenario,)).fetchall()}
        scope.outside_population = tuple(i for i in scenario if i not in in_universe)
    return scope
