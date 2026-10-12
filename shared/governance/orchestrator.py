# shared/governance/orchestrator.py
# -*- coding: utf-8 -*-
"""
Two-pillar orchestrator (FND-0265, GOV-06): one verdict per operation, built from the controls of doc/reglas/GOBIERNO_CONTROLES.md.

    authorise_promotion(...) -> Verdict      pure: composes the promotion gate (proyecto3/src/scenario_lifecycle.evaluate_promotion, whose
                                             blockers are kept verbatim) with the Pillar-1 checks that need no scenario logic (code
                                             integrity D1, evidence manifest) and the cost domain (decision D8), then states each pillar
                                             (PASS / PASS_WITH_WARNINGS / FAIL / INCOMPLETE) and decides with controls.DECISION_MATRIX.

No gate logic is duplicated: every refusal is a `[GATE:<code>]` line, and the pillar of a line is the pillar of the catalogue control whose
source is that gate code. A check behind a switch that is still OFF is reported as a warning (shadow), never as a blocker.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Mapping, Optional

from shared.governance import controls as gc

INCOMPLETE_GATES = frozenset({"AUDIT_STALE", "EXCEPTIONS", "MANIFEST"})   # "could not be established", not "established and failed"
_GATE_RE = re.compile(r"^\[GATE:([A-Z_]+)\]")
SHADOW_SUFFIX = " [sombra: interruptor apagado, no bloquea]"


def gate_code(line: str) -> Optional[str]:
    m = _GATE_RE.match(line or "")
    return m.group(1) if m else None


def pillar_of(code: Optional[str]) -> str:
    """Pillar of the catalogue control whose source is `GATE:<code>`; ORCHESTRATION for a line without a known code."""
    for c in gc.CONTROLS:
        if c.source == gc.SOURCE_GATE + (code or ""):
            return c.pillar
    return gc.PILLAR_ORCHESTRATION


@dataclass(frozen=True)
class PillarResult:
    pillar: str
    state: str
    blockers: tuple = ()
    warnings: tuple = ()


@dataclass(frozen=True)
class Verdict:
    operation: str
    pillars: Mapping[str, PillarResult]
    state: str
    decision: str
    overridden: tuple = ()
    notes: tuple = ()                                            # lines without a gate code (hints), printed, never decisive

    @property
    def blockers(self) -> list:
        return [b for p in gc.PILLARS for b in self.pillars[p].blockers]

    @property
    def authorised(self) -> bool:
        return self.decision != gc.REFUSED


def _pillar_state(blockers: Iterable[str], warnings: Iterable[str]) -> str:
    codes = {gate_code(b) for b in blockers}
    if codes & INCOMPLETE_GATES:
        return gc.STATE_INCOMPLETE
    if codes:
        return gc.STATE_FAIL
    return gc.STATE_PASS_WITH_WARNINGS if list(warnings) else gc.STATE_PASS


def manifest_problems(header: Optional[Mapping]) -> list:
    """Why the evidence run's control.audit_run header does not identify its code, configuration and data (T-VERSION). Pure."""
    if header is None:
        return ["la auditoria de evidencia no tiene cabecera en control.audit_run"]
    out = [f"falta {k}" for k in ("git_commit", "config_hash", "catalog_version") if not header.get(k)]
    if not header.get("manifest"):
        out.append("falta el manifiesto de datos (persista una auditoria nueva: la cabecera es anterior a FND-0263)")
    return out


def authorise_promotion(lifecycle_blockers: Iterable[str], *, overridden: Iterable = (), code_state: Optional[Mapping] = None,
                        code_gate_enabled: bool = False, evidence_header: Optional[Mapping] = None, cost_defects: Iterable = (),
                        cost_has_run: bool = True, cost_gate_enabled: bool = False) -> Verdict:
    """The PORTFOLIO_PROMOTION verdict. `lifecycle_blockers` / `overridden` = scenario_lifecycle.evaluate_promotion's (computed with
    WEIGHT_TOLERANCE, decision D2); `code_state` = run_identity.code_integrity(); `evidence_header` = the audit_run row of the p2 evidence
    run (None = none); `cost_defects` = gating DefectFinding list ON the scenario from the cost_attributes evidence run. Pure."""
    # the gate codes and their line format belong to the promotion gate (P3); imported here, never redefined (P#11)
    from proyecto3.src.scenario_lifecycle import GATE_CODE, GATE_COST, GATE_MANIFEST, gate_line
    blockers = [b for b in lifecycle_blockers if gate_code(b)]
    notes = tuple(b for b in lifecycle_blockers if not gate_code(b))
    warnings: list = []

    def place(line: str, enforced: bool) -> None:
        if enforced:
            blockers.append(line)
        else:
            warnings.append(line + SHADOW_SUFFIX)

    if code_state is not None and not code_state.get("ok"):
        place(gate_line(GATE_CODE, f"integridad del codigo (D1): {code_state.get('detail')}"), code_gate_enabled)

    problems = manifest_problems(evidence_header)
    if problems:
        blockers.append(gate_line(GATE_MANIFEST, "; ".join(problems)))

    cost_defects = list(cost_defects)
    if cost_defects:
        place(gate_line(GATE_COST, "defectos duros del dominio de costes sobre fondos de la cartera: "
                        + ", ".join(f"{d.rule_id} ({len(d.isins) or 'sin ISIN'})" for d in cost_defects)), cost_gate_enabled)
    elif not cost_has_run:
        place(gate_line(GATE_COST, "no hay auditoria de costes que sirva de evidencia (FULL, no INCOMPLETE)"), cost_gate_enabled)

    by_pillar: dict = {p: ([], []) for p in gc.PILLARS}
    for b in blockers:
        by_pillar[pillar_of(gate_code(b))][0].append(b)
    for w in warnings:
        by_pillar[pillar_of(gate_code(w))][1].append(w)
    pillars = {p: PillarResult(p, _pillar_state(bl, wa), tuple(bl), tuple(wa)) for p, (bl, wa) in by_pillar.items()}
    state = gc.combine_states(r.state for r in pillars.values())
    return Verdict(gc.OP_PORTFOLIO_PROMOTION, pillars, state, gc.decide(gc.OP_PORTFOLIO_PROMOTION, state), tuple(overridden), notes)


def verdict_line(v: Verdict) -> str:
    """One machine-readable line for the promotion note (T-EVIDENCE): each pillar's state and the decision."""
    parts = " ".join(f"{p}={v.pillars[p].state}" for p in gc.PILLARS)
    return f"two-pillar {parts} -> {v.state} {v.decision}"
