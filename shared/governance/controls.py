# shared/governance/controls.py
# -*- coding: utf-8 -*-
"""
Control catalogue of the two-pillar data-control system (FND-0260, GOV-01) -- DATA ONLY.

One register of every control with its pillar, owner, criterion and consequence; the normative text is
`doc/reglas/GOBIERNO_CONTROLES.md` and `tests/test_governance_controls.py` keeps the two and the code in step.

    Pillar TECHNICAL (T-*)  the artefact being promoted is the one that was reviewed: code, version, mandate limits, evidence.
    Pillar DATA      (D-*)  the data the artefact stands on is fit for use: statistical audit, freshness, exceptions.
    ORCHESTRATION    (G-*)  how the two combine: completeness of the audit itself, evidence selection, override and remediation.

Nothing here runs a check. A control with `status == "enforced"` names the existing code that enforces it in `source`
(a promotion gate code, an audit defect class / block / rule id, or a function); `status == "dormant:<SWITCH>"` names code that
exists behind a shared.config switch still OFF (the test fails once the switch is ON, so the register is updated with the flip);
`status == "planned:FND-nnnn"` is owned by that ticket and is not mapped to any code yet. The consumers are the orchestrator
(GOV-06) and the tests.

Importing this module must stay free of pipeline imports (R-7): the tests read the gate constants, not the other way round.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# --- pillars ------------------------------------------------------------------------------------------------------------------
PILLAR_TECHNICAL = "TECHNICAL"
PILLAR_DATA = "DATA"
PILLAR_ORCHESTRATION = "ORCHESTRATION"
PILLARS = (PILLAR_TECHNICAL, PILLAR_DATA, PILLAR_ORCHESTRATION)

# --- operations (what is being authorised) ------------------------------------------------------------------------------------
# CODE_DEPLOY: a launcher runs production code from the working tree (a cycle, a build).
# PORTFOLIO_PROMOTION: `scripts/launch/p3_promote_scenario.py` turns a DRAFT into the PRODUCTION portfolio.
OP_CODE_DEPLOY = "CODE_DEPLOY"
OP_PORTFOLIO_PROMOTION = "PORTFOLIO_PROMOTION"
OPERATIONS = (OP_CODE_DEPLOY, OP_PORTFOLIO_PROMOTION)

# --- states of a pillar (and of the combined verdict) --------------------------------------------------------------------------
STATE_PASS = "PASS"
STATE_PASS_WITH_WARNINGS = "PASS_WITH_WARNINGS"
STATE_FAIL = "FAIL"
STATE_INCOMPLETE = "INCOMPLETE"
# Increasing severity: the combined state of several is the last one present. INCOMPLETE outranks FAIL because it says "could not
# be established", which is worse than "established and failed": neither authorises a promotion.
STATES = (STATE_PASS, STATE_PASS_WITH_WARNINGS, STATE_FAIL, STATE_INCOMPLETE)

# --- decisions ----------------------------------------------------------------------------------------------------------------
AUTHORISED = "AUTHORISED"
AUTHORISED_WITH_WARNING = "AUTHORISED_WITH_WARNING"      # proceeds; the state is recorded and shown to the operator
REFUSED = "REFUSED"
DECISIONS = (AUTHORISED, AUTHORISED_WITH_WARNING, REFUSED)

# The dual-pillar decision matrix, one row per (operation, combined state). Owner decision D3 (2026-10-11): code state blocks only at
# PORTFOLIO_PROMOTION; the cycle launchers record it and warn. A statistical-audit verdict never stops a cycle (the audits inside a
# launcher are diagnostics), so at CODE_DEPLOY every state proceeds.
DECISION_MATRIX = {
    (OP_CODE_DEPLOY, STATE_PASS): AUTHORISED,
    (OP_CODE_DEPLOY, STATE_PASS_WITH_WARNINGS): AUTHORISED,
    (OP_CODE_DEPLOY, STATE_FAIL): AUTHORISED_WITH_WARNING,
    (OP_CODE_DEPLOY, STATE_INCOMPLETE): AUTHORISED_WITH_WARNING,
    (OP_PORTFOLIO_PROMOTION, STATE_PASS): AUTHORISED,
    (OP_PORTFOLIO_PROMOTION, STATE_PASS_WITH_WARNINGS): AUTHORISED,
    (OP_PORTFOLIO_PROMOTION, STATE_FAIL): REFUSED,
    (OP_PORTFOLIO_PROMOTION, STATE_INCOMPLETE): REFUSED,
}

# --- consequences of a control that does not pass ----------------------------------------------------------------------------
BLOCK = "BLOCK"                                      # refuses the operation; no override exists
BLOCK_UNLESS_OVERRIDE = "BLOCK_UNLESS_OVERRIDE"      # refuses, unless a written reason AND the owning ticket accompany the promotion
WARN = "WARN"                                        # shown, never refuses
RECORD = "RECORD"                                    # written to the run record, nothing shown
CONSEQUENCES = (BLOCK, BLOCK_UNLESS_OVERRIDE, WARN, RECORD)

STATUS_ENFORCED = "enforced"
PLANNED_PREFIX = "planned:"
DORMANT_PREFIX = "dormant:"     # "dormant:<SWITCH>": the code exists behind a shared.config switch that is still OFF

# `source` of an enforced or dormant control: "GATE:<code>" is a `GATE_*` constant of proyecto3/src/scenario_lifecycle.py; "RULE_CLASS:",
# "BLOCK:" and "RULE_ID:" are audit finding attributes (shared/statistical_audit/gating.py for the three that block); "CODE:<module.attr>"
# names the function that applies a control with no gate code of its own.
SOURCE_GATE = "GATE:"
SOURCE_RULE_CLASS = "RULE_CLASS:"
SOURCE_BLOCK = "BLOCK:"
SOURCE_RULE_ID = "RULE_ID:"
SOURCE_CODE = "CODE:"


@dataclass(frozen=True)
class Control:
    id: str
    pillar: str
    title: str
    owner: str
    criterion: str
    consequence: str                         # at PORTFOLIO_PROMOTION
    source: str                              # "GATE:X" / "RULE_CLASS:X" / "BLOCK:X" / "RULE_ID:X" / "CODE:m.f" (enforced, dormant); "" (planned)
    status: str                              # STATUS_ENFORCED, "planned:FND-nnnn" or "dormant:<SWITCH>"
    deploy_consequence: Optional[str] = None  # at CODE_DEPLOY; None = not evaluated there

    @property
    def overridable(self) -> bool:
        return self.consequence == BLOCK_UNLESS_OVERRIDE

    @property
    def enforced(self) -> bool:
        return self.status == STATUS_ENFORCED

    @property
    def planned_ticket(self) -> Optional[str]:
        return self.status[len(PLANNED_PREFIX):] if self.status.startswith(PLANNED_PREFIX) else None

    @property
    def dormant_switch(self) -> Optional[str]:
        return self.status[len(DORMANT_PREFIX):] if self.status.startswith(DORMANT_PREFIX) else None


_OWNER_PROMOTION = "owner (promotion command)"
_OWNER_AUDIT = "owner (statistical audit)"

CONTROLS = (
    # ---- Pillar TECHNICAL ------------------------------------------------------------------------------------------------------
    Control("T-STATUS", PILLAR_TECHNICAL, "Scenario exists and is a DRAFT", _OWNER_PROMOTION,
            "the scenario exists, the lifecycle is installed, and its status is DRAFT (only a DRAFT is promoted)",
            BLOCK, SOURCE_GATE + "STATUS", STATUS_ENFORCED),
    Control("T-WEIGHTS", PILLAR_TECHNICAL, "Weights add up to 1.0000", _OWNER_PROMOTION,
            "the scenario has weights and sum(weights) + declared structural cash = 1.0000 (weights are 4 dp, built by largest remainder; "
            "tolerance 5e-5 per decision D2 when the two-pillar orchestrator decides, 1e-3 in the legacy gate)",
            BLOCK, SOURCE_GATE + "WEIGHTS", STATUS_ENFORCED),
    Control("T-CASH", PILLAR_TECHNICAL, "Structural cash within the mandate", _OWNER_PROMOTION,
            "declared cash <= MAX_PORTFOLIO_CASH_WEIGHT (shared/config.py, 10%); the mandate changes by a reviewed commit, not at promotion",
            BLOCK, SOURCE_GATE + "CASH", STATUS_ENFORCED),
    Control("T-FX", PILLAR_TECHNICAL, "Net non-EUR exposure within the mandate", _OWNER_PROMOTION,
            "net non-EUR unhedged exposure <= MAX_NON_EUR_UNHEDGED_WEIGHT (shared/config.py, 30%, shared/fx_exposure.py)",
            BLOCK, SOURCE_GATE + "FX", STATUS_ENFORCED),
    Control("T-CODE", PILLAR_TECHNICAL, "Code integrity", "owner (release)",
            "HEAD == origin/master as of the last fetch and no modified tracked file (untracked files ignored; decision D1; "
            "run_identity.code_integrity, the predicate the gated push of FND-0273 shares); the cycle preflight only warns (D3)",
            BLOCK, SOURCE_GATE + "CODE", DORMANT_PREFIX + "CODE_INTEGRITY_GATE_ENABLED", deploy_consequence=WARN),
    Control("T-VERSION", PILLAR_TECHNICAL, "Version and configuration identified", "owner (release)",
            "the evidence audit run's control.audit_run header carries git sha, config hash (*_ENABLED + MAX_*), catalogue version and the "
            "data manifest (CALC_VERSION, cut-off, algorithm versions); a run without them is not promotion evidence",
            BLOCK, SOURCE_GATE + "MANIFEST", DORMANT_PREFIX + "GOVERNANCE_ORCHESTRATOR_ENABLED", deploy_consequence=RECORD),
    Control("T-EVIDENCE", PILLAR_TECHNICAL, "Execution evidence", "owner (release)",
            "the promotion record (ingestion_log, same transaction as the promotion) cites the audit run, config hash, exception grants "
            "and the two-pillar verdict line (each pillar's state and the decision)",
            BLOCK, SOURCE_CODE + "shared.governance.orchestrator.verdict_line", STATUS_ENFORCED, deploy_consequence=RECORD),
    # ---- Pillar DATA -----------------------------------------------------------------------------------------------------------
    Control("D-FRESHNESS", PILLAR_DATA, "Inputs are fresh", _OWNER_PROMOTION,
            "the P3 data-freshness gate (shared.config.P3_FRESHNESS_MAX_AGE_DAYS) reports no stale input",
            BLOCK, SOURCE_GATE + "FRESHNESS", STATUS_ENFORCED),
    Control("D-AUDIT-STALE", PILLAR_DATA, "Audit evidence describes this scenario", _OWNER_AUDIT,
            "a persisted p2_metrics audit exists and is newer than the DRAFT build",
            BLOCK, SOURCE_GATE + "AUDIT_STALE", STATUS_ENFORCED),
    Control("D-DATA-SCENARIO", PILLAR_DATA, "No open hard defect on a fund of the scenario", _OWNER_AUDIT,
            "no hard defect (D-INVARIANT / D-IDENTITY / D-TS-DUP) is open on a fund of the scenario, or one that cannot be placed",
            BLOCK_UNLESS_OVERRIDE, SOURCE_GATE + "DATA_SCENARIO", STATUS_ENFORCED),
    Control("D-RESIDUAL-SCENARIO", PILLAR_DATA, "No accepted residual on a fund of the scenario", _OWNER_AUDIT,
            "an accepted residual is tolerated in the universe, never inside a portfolio",
            BLOCK_UNLESS_OVERRIDE, SOURCE_GATE + "RESIDUAL_SCENARIO", STATUS_ENFORCED),
    Control("D-COVERAGE", PILLAR_DATA, "Every fund of the scenario was audited", _OWNER_AUDIT,
            "no fund of the scenario is held out of the audit's cross-sectional blocks or outside the audited population; coverage is known",
            BLOCK_UNLESS_OVERRIDE, SOURCE_GATE + "DATA_COVERAGE", STATUS_ENFORCED),
    Control("D-DATA-UNIVERSE", PILLAR_DATA, "No unaccepted hard defect in the universe", _OWNER_AUDIT,
            "an open hard defect outside the scenario needs an active accepted residual (accept_audit_residual.py: ticket + expiry); "
            "there is no promotion-time override",
            BLOCK, SOURCE_GATE + "DATA_UNIVERSE", STATUS_ENFORCED),
    Control("D-INVARIANT", PILLAR_DATA, "Methodological invariants hold", _OWNER_AUDIT,
            "findings of rule class HARD_INVARIANT (a value outside a hard bound, a violated cross-column identity) are defects",
            BLOCK, SOURCE_RULE_CLASS + "HARD_INVARIANT", STATUS_ENFORCED),
    Control("D-IDENTITY", PILLAR_DATA, "Scalar and time-series values agree", _OWNER_AUDIT,
            "findings of block BLOCK2 in the P2 domain (scalar-vs-timeseries, real-vs-nominal, Sharpe-vs-Sortino, capture identities) are "
            "defects: every consumer reads a wrong number; the cost-domain BLOCK2 'X__EQUALS__Y' coincidence pairs are MINOR "
            "(shared/statistical_audit/criticality.py, FND-0262)",
            BLOCK, SOURCE_BLOCK + "BLOCK2", STATUS_ENFORCED),
    Control("D-TS-DUP", PILLAR_DATA, "Append-only series are not duplicated", _OWNER_AUDIT,
            "TIMESERIES_DUPLICATE findings are defects: a corrupt append-only series",
            BLOCK, SOURCE_RULE_ID + "TIMESERIES_DUPLICATE", STATUS_ENFORCED),
    Control("D-PLAUSIBILITY", PILLAR_DATA, "Plausibility bounds", _OWNER_AUDIT,
            "findings of rule class PLAUSIBILITY (a value outside its plausible, not impossible, range) are listed for review",
            WARN, SOURCE_RULE_CLASS + "PLAUSIBILITY", STATUS_ENFORCED),
    Control("D-OUTLIER", PILLAR_DATA, "Statistical anomalies", _OWNER_AUDIT,
            "findings of rule class STATISTICAL_ANOMALY (MAD-Z, IQR) are review items: on the full population they number in the thousands",
            WARN, SOURCE_RULE_CLASS + "STATISTICAL_ANOMALY", STATUS_ENFORCED),
    Control("D-RESIDUAL", PILLAR_DATA, "Accepted residual", _OWNER_AUDIT,
            "a defect whose violators are all covered by an active accepted residual (ticket + expiry) is reported, downgraded, never hidden",
            WARN, SOURCE_RULE_CLASS + "ACCEPTED_RESIDUAL", STATUS_ENFORCED),
    Control("D-EXCEPTIONS", PILLAR_DATA, "Accepted residuals are governed", _OWNER_AUDIT,
            "every accepted residual is a GRANT / RENEW plus a separate APPROVE in control.audit_exception_event (decision D6), revoked "
            "with a cause; the gate refuses when that history is unreadable or control.audit_accepted_finding disagrees with it",
            BLOCK, SOURCE_GATE + "EXCEPTIONS", STATUS_ENFORCED),
    # ---- ORCHESTRATION ---------------------------------------------------------------------------------------------------------
    Control("G-OVERRIDE-AP", PILLAR_ORCHESTRATION, "An override names its ticket", _OWNER_PROMOTION,
            "--accept-alarms needs a reason and --ap FND-nnnn that exists in gestion.backlog; without it the override does not apply",
            BLOCK, SOURCE_GATE + "OVERRIDE_AP", STATUS_ENFORCED),
    Control("G-COMPLETENESS", PILLAR_ORCHESTRATION, "The audit ran everything it was required to", "owner (statistical audit)",
            "every required check produced a result; a MISSING input (or an EMPTY required check in a FULL run) makes the run INCOMPLETE, "
            "exit code 2, never a pass; the status is written to control.audit_run with the run's rows, in one transaction",
            BLOCK, SOURCE_CODE + "shared.statistical_audit.completeness.run_status", STATUS_ENFORCED),
    Control("G-EVIDENCE-RUN", PILLAR_ORCHESTRATION, "Evidence-grade audit run", "owner (statistical audit)",
            "the run the gate reads is the latest control.audit_run header with scope FULL and status not INCOMPLETE, newer than the DRAFT; "
            "a FAIL run counts (decision D4); a _sample run never does (always on: gating.SAMPLE_RUN_SUFFIX)",
            BLOCK, SOURCE_CODE + "shared.statistical_audit.gating.select_evidence_run", STATUS_ENFORCED),
    Control("G-COST", PILLAR_ORCHESTRATION, "Cost domain at promotion", "owner (statistical audit)",
            "open hard defects of the cost_attributes evidence run on a fund of the scenario refuse like p2_metrics ones (decision D8: "
            "scripts/audit/cost_gate_replay.py over the past runs, one shadow cycle, then the switch)",
            BLOCK, SOURCE_GATE + "COST", DORMANT_PREFIX + "COST_DOMAIN_GATE_ENABLED"),
    Control("G-REMEDIATION", PILLAR_ORCHESTRATION, "Remediation is closed by evidence", "owner (backlog)",
            "a CRITICAL finding becomes an incident (control.audit_incident); a ticket linked to it closes only when the incident is "
            "CERTIFIED by a later, complete re-audit run (not INCOMPLETE, FULL or covering the affected ISINs, none still violating; a "
            "recompute alone never certifies) or ACCEPTED through active residuals; enforced by scripts/ops/close_release.py",
            BLOCK, SOURCE_CODE + "shared.statistical_audit.incidents.closable", STATUS_ENFORCED),
)

CONTROLS_BY_ID = {c.id: c for c in CONTROLS}


def combine_states(states) -> str:
    """The most severe of `states` (STATES order); PASS for none. Pure."""
    worst = 0
    for s in states:
        worst = max(worst, STATES.index(s))
    return STATES[worst]


def decide(operation: str, state: str) -> str:
    """Decision of the matrix for the combined state of both pillars at `operation`. Pure."""
    return DECISION_MATRIX[(operation, state)]
