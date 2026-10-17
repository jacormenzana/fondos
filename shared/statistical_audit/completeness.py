# shared/statistical_audit/completeness.py
# -*- coding: utf-8 -*-
"""
Audit completeness contract (FND-0261, GOV-02; controls G-COMPLETENESS / G-EVIDENCE-RUN in doc/reglas/GOBIERNO_CONTROLES.md). Pure.

Before this contract a check that could not run was a printed line (`AuditRun.skipped`) and the run still exited 0: a missing column,
reference series or table degraded the audit to a pass. Every skip now carries a check key and a kind:

    MISSING         a required input is absent or unreadable (a column the rule needs, the ES CPI reference, the residual store):
                    the check was supposed to run and could not.
    EMPTY           the population has no rows for the check. Legitimate in a SAMPLE run (40 ISINs may hold no betas); in a FULL
                    run of a REQUIRED check it means the input is missing, so it resolves to MISSING.
    NOT_APPLICABLE  skipped by design (a peer segment below MIN_PEERS, an outlier method without enough values, alerts already stale):
                    never counts.

A run with at least one resolved MISSING skip is INCOMPLETE: it is not evidence (the promotion gate does not read it, decision D4) and,
with shared.config.AUDIT_COMPLETENESS_CONTRACT_ENABLED, the runner exits RC_INCOMPLETE. The status of a complete run follows the defect
predicate (gating.is_blocking): FAIL with a defect, PASS_WITH_WARNINGS with a WARN/ALARM review item, PASS otherwise.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence

from shared.governance.controls import STATE_FAIL, STATE_INCOMPLETE, STATE_PASS, STATE_PASS_WITH_WARNINGS
from shared.statistical_audit.gating import is_blocking

RC_INCOMPLETE = 2

SCOPE_FULL = "FULL"            # the whole In_Current_Universe population
SCOPE_SAMPLE = "SAMPLE"        # scoped with --isin (run id ends in gating.SAMPLE_RUN_SUFFIX)
SCOPES = (SCOPE_FULL, SCOPE_SAMPLE)

KIND_MISSING = "MISSING"
KIND_EMPTY = "EMPTY"
KIND_NOT_APPLICABLE = "NOT_APPLICABLE"
KINDS = (KIND_MISSING, KIND_EMPTY, KIND_NOT_APPLICABLE)

RESOLVED_MISSING = "MISSING"
RESOLVED_LEGIT = "LEGIT_EMPTY"

# Every check key the runner may skip (scripts/audit/run_statistical_audit.py, `run.skip("<KEY>", ...)`); the contract test keeps the two
# in step. True = REQUIRED: an EMPTY skip of it in a FULL run is a missing input, not an empty population.
CHECKS = {
    "COST_SCHEDULE_INTEGRITY": True,       # BLOCK6 on fund_cost_schedule
    "COST_COLUMN": True,                   # BLOCK1/4/7 per cost column
    "TIMESERIES_INTEGRITY": True,          # BLOCK5/6 per metric/window of fund_metric_timeseries
    "CPI_COVERAGE": True,                  # BLOCK6 leading/trailing CPI gaps vs NAV lifespan
    "BETA_ORPHAN_BATCH": True,             # BLOCK6 batch lineage of the macro betas
    "P2_PAIR": True,                       # BLOCK2 same-group metric pairs
    "DEFLATION_ORDER": True,               # BLOCK5 real vs nominal ordering
    "REAL_EQUALS_NOMINAL": True,           # BLOCK2 real == nominal identity
    "SCALAR_EQUALS_TIMESERIES": True,      # BLOCK2 scalar vs latest timeseries snapshot
    "GROUP_INVARIANT": True,               # group-level invariants (columns the rule needs)
    "INVARIANT": True,                     # row-level invariants (columns the rule needs)
    "ACCEPTED_RESIDUALS": True,            # control.audit_accepted_finding readable
    "ALGORITHM_VERSION_UNIFORMITY": True,  # BLOCK6 share of active funds on one CALC_VERSION (FND-0262, D4)
    "ALERT_RECONCILIATION": False,         # suppression of findings the alert engine already raised: optional by design
    "PEER_SEGMENT": False,                 # peer segments below MIN_PEERS
    "OUTLIER": False,                      # an outlier method without enough values
}
REQUIRED_CHECKS = frozenset(k for k, required in CHECKS.items() if required)


@dataclass(frozen=True)
class Skip:
    check: str
    kind: str
    detail: str

    def __post_init__(self):
        if self.check not in CHECKS:
            raise ValueError(f"unknown audit check {self.check!r}: register it in completeness.CHECKS")
        if self.kind not in KINDS:
            raise ValueError(f"unknown skip kind {self.kind!r}")


def scope_of(isins: Optional[Sequence[str]]) -> str:
    return SCOPE_SAMPLE if isins else SCOPE_FULL


def resolve(skip: Skip, scope: str) -> Optional[str]:
    """RESOLVED_MISSING, RESOLVED_LEGIT, or None for a skip that never counts (NOT_APPLICABLE)."""
    if skip.kind == KIND_NOT_APPLICABLE:
        return None
    if skip.kind == KIND_MISSING:
        return RESOLVED_MISSING
    return RESOLVED_MISSING if (scope == SCOPE_FULL and skip.check in REQUIRED_CHECKS) else RESOLVED_LEGIT


def missing(skips: Iterable[Skip], scope: str) -> list:
    return [s for s in skips if resolve(s, scope) == RESOLVED_MISSING]


def run_status(skips: Iterable[Skip], findings: Iterable[Mapping[str, Any]], scope: str, domain: Optional[str] = None) -> str:
    """INCOMPLETE > FAIL > PASS_WITH_WARNINGS > PASS (shared.governance.controls.STATES order). FAIL = a CRITICAL finding in `domain`
    (shared/statistical_audit/criticality.py; None = the legacy domain-agnostic view)."""
    if missing(skips, scope):
        return STATE_INCOMPLETE
    findings = list(findings)
    if any(is_blocking(f.get("rule_class"), f.get("block"), f.get("rule_id"), domain) for f in findings):
        return STATE_FAIL
    if any(f.get("severity") in ("WARN", "ALARM") for f in findings):
        return STATE_PASS_WITH_WARNINGS
    return STATE_PASS


def skips_payload(skips: Iterable[Skip], scope: str) -> list:
    """JSON-ready list for control.audit_run.skips: one object per skip, with how it resolved in this scope."""
    return [{"check": s.check, "kind": s.kind, "resolved": resolve(s, scope), "detail": s.detail} for s in skips]


def exit_code(status: str, *, contract_enabled: bool, mode: str, blocking: bool) -> int:
    """RC of the runner. With the contract ON an INCOMPLETE run exits RC_INCOMPLETE in either mode (it outranks a defect: a run that
    could not look is not a run that looked and failed). Otherwise unchanged: 1 only for --mode check with a defect, 0 else."""
    if contract_enabled and status == STATE_INCOMPLETE:
        return RC_INCOMPLETE
    return 1 if (mode == "check" and blocking) else 0
