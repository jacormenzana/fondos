"""
tests/test_governance_controls.py -- the two-pillar control catalogue is complete and in step with the code (FND-0260, GOV-01).

`shared/governance/controls.py` is a mirror of `doc/reglas/GOBIERNO_CONTROLES.md`; the gates it names live in
`proyecto3/src/scenario_lifecycle.py` (GATE_* codes) and `shared/statistical_audit/gating.py` (blocking defect classes). Nothing
here runs a check: each test is a statement about how the register relates to the code that enforces it, so adding a gate, a
blocking class or a rule class without its control fails here before it can be merged.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from proyecto3.src import scenario_lifecycle as lc
from shared.governance import controls as gc
from shared.statistical_audit import accepted_residuals, gating

_ROOT = Path(__file__).resolve().parent.parent
_DOC = _ROOT / "doc" / "reglas" / "GOBIERNO_CONTROLES.md"
_CONTROL_SQL = _ROOT / "db" / "pg" / "35_control.sql"
_ID_RE = re.compile(r"\b[TDG]-[A-Z][A-Z-]*[A-Z]\b")


def _sources(prefix: str) -> dict:
    return {c.source[len(prefix):]: c for c in gc.CONTROLS if c.source.startswith(prefix)}


def _gate_codes() -> set:
    return {v for k, v in vars(lc).items() if k.startswith("GATE_") and isinstance(v, str)}


def _db_rule_classes() -> set:
    text = _CONTROL_SQL.read_text(encoding="utf-8")
    m = re.search(r"rule_class\s+text\s+NOT NULL\s+CHECK \(rule_class IN \(([^)]*)\)\)", text)
    assert m, "the rule_class CHECK in db/pg/35_control.sql was not found"
    return set(re.findall(r"'([A-Z_]+)'", m.group(1)))


# --- shape ------------------------------------------------------------------------------------------------------------------
def test_ids_are_unique_and_follow_their_pillar_prefix():
    ids = [c.id for c in gc.CONTROLS]
    assert len(ids) == len(set(ids))
    prefix = {gc.PILLAR_TECHNICAL: "T-", gc.PILLAR_DATA: "D-", gc.PILLAR_ORCHESTRATION: "G-"}
    for c in gc.CONTROLS:
        assert c.pillar in gc.PILLARS, c.id
        assert c.id.startswith(prefix[c.pillar]), f"{c.id} is in pillar {c.pillar}"
        assert _ID_RE.fullmatch(c.id), c.id


def test_every_control_has_owner_criterion_consequence_and_a_known_state():
    for c in gc.CONTROLS:
        assert c.title.strip() and c.owner.strip() and c.criterion.strip(), c.id
        assert c.consequence in gc.CONSEQUENCES, c.id
        assert c.deploy_consequence is None or c.deploy_consequence in gc.CONSEQUENCES, c.id
        assert c.enforced or re.fullmatch(r"planned:FND-\d{4}", c.status), f"{c.id}: status {c.status!r}"


def test_enforced_controls_name_a_source_and_planned_ones_name_none():
    prefixes = (gc.SOURCE_GATE, gc.SOURCE_RULE_CLASS, gc.SOURCE_BLOCK, gc.SOURCE_RULE_ID)
    for c in gc.CONTROLS:
        if c.enforced:
            assert c.source.startswith(prefixes) and c.source.split(":", 1)[1], f"{c.id}: source {c.source!r}"
        else:
            assert c.source == "", f"{c.id} is planned and must not claim a source"


def test_every_source_is_claimed_by_exactly_one_control():
    sources = [c.source for c in gc.CONTROLS if c.source]
    assert len(sources) == len(set(sources))


# --- the mapping to the code (the phase's exit criterion) ---------------------------------------------------------------------
def test_every_gate_code_maps_to_a_control_and_back():
    mapped = set(_sources(gc.SOURCE_GATE))
    codes = _gate_codes()
    assert codes, "no GATE_* constants found in scenario_lifecycle"
    assert codes - mapped == set(), f"GATE codes without a control: {sorted(codes - mapped)}"
    assert mapped - codes == set(), f"controls naming a GATE code that does not exist: {sorted(mapped - codes)}"


def test_every_blocking_defect_class_maps_to_a_control():
    by_class, by_block, by_id = (_sources(gc.SOURCE_RULE_CLASS), _sources(gc.SOURCE_BLOCK), _sources(gc.SOURCE_RULE_ID))
    for rc in gating.BLOCKING_RULE_CLASSES:
        assert rc in by_class and by_class[rc].consequence == gc.BLOCK, rc
    for b in gating.BLOCKING_BLOCKS:
        assert b in by_block and by_block[b].consequence == gc.BLOCK, b
    for rid in gating.BLOCKING_RULE_IDS:
        assert rid in by_id and by_id[rid].consequence == gc.BLOCK, rid
    # and no control claims to block on a defect class the predicate does not block on
    assert {k for k, c in by_class.items() if c.consequence == gc.BLOCK} == set(gating.BLOCKING_RULE_CLASSES)
    assert {k for k, c in by_block.items() if c.consequence == gc.BLOCK} == set(gating.BLOCKING_BLOCKS)
    assert {k for k, c in by_id.items() if c.consequence == gc.BLOCK} == set(gating.BLOCKING_RULE_IDS)


def test_every_rule_class_the_audit_can_persist_maps_to_a_control():
    classes = _db_rule_classes() | {accepted_residuals.ACCEPTED_CLASS}
    mapped = set(_sources(gc.SOURCE_RULE_CLASS))
    assert classes - mapped == set(), f"rule classes without a control: {sorted(classes - mapped)}"
    assert mapped - classes == set(), f"controls naming a rule class that does not exist: {sorted(mapped - classes)}"


def test_is_blocking_agrees_with_the_catalogue_for_every_mapped_rule_class():
    for rc, control in _sources(gc.SOURCE_RULE_CLASS).items():
        assert gating.is_blocking(rc, None, None) == (control.consequence == gc.BLOCK), rc


# --- override policy ------------------------------------------------------------------------------------------------------------
def test_overridable_controls_are_exactly_the_overridable_gates():
    by_gate = _sources(gc.SOURCE_GATE)
    overridable = {code for code, c in by_gate.items() if c.overridable}
    assert overridable == set(lc.OVERRIDABLE_GATES)


def test_only_pillar_data_controls_are_overridable():
    for c in gc.CONTROLS:
        if c.overridable:
            assert c.pillar == gc.PILLAR_DATA, f"{c.id} is overridable but belongs to {c.pillar}"


# --- states and decision matrix -----------------------------------------------------------------------------------------------
def test_decision_matrix_is_total_over_operations_and_states():
    assert set(gc.DECISION_MATRIX) == {(o, s) for o in gc.OPERATIONS for s in gc.STATES}
    assert set(gc.DECISION_MATRIX.values()) <= set(gc.DECISIONS)


@pytest.mark.parametrize("state", [gc.STATE_FAIL, gc.STATE_INCOMPLETE])
def test_a_failed_or_incomplete_state_never_authorises_a_promotion(state):
    assert gc.decide(gc.OP_PORTFOLIO_PROMOTION, state) == gc.REFUSED


@pytest.mark.parametrize("state", gc.STATES)
def test_a_deploy_is_never_refused_by_the_governance_state(state):
    # decision D3: the cycle launchers record and warn; only a promotion blocks
    assert gc.decide(gc.OP_CODE_DEPLOY, state) != gc.REFUSED


def test_incomplete_outranks_fail_and_fail_outranks_warnings():
    assert gc.combine_states([]) == gc.STATE_PASS
    assert gc.combine_states([gc.STATE_PASS, gc.STATE_PASS_WITH_WARNINGS]) == gc.STATE_PASS_WITH_WARNINGS
    assert gc.combine_states([gc.STATE_FAIL, gc.STATE_PASS_WITH_WARNINGS]) == gc.STATE_FAIL
    assert gc.combine_states([gc.STATE_FAIL, gc.STATE_INCOMPLETE, gc.STATE_PASS]) == gc.STATE_INCOMPLETE


def test_code_integrity_warns_at_deploy_and_blocks_at_promotion():
    t_code = gc.CONTROLS_BY_ID["T-CODE"]
    assert (t_code.deploy_consequence, t_code.consequence) == (gc.WARN, gc.BLOCK)


# --- document <-> code ----------------------------------------------------------------------------------------------------------
def _doc_ids() -> set:
    return set(_ID_RE.findall(_DOC.read_text(encoding="utf-8")))


def test_every_control_is_documented_and_the_document_names_no_unknown_id():
    doc_ids, code_ids = _doc_ids(), {c.id for c in gc.CONTROLS}
    assert code_ids - doc_ids == set(), f"controls missing from GOBIERNO_CONTROLES.md: {sorted(code_ids - doc_ids)}"
    assert doc_ids - code_ids == set(), f"ids in GOBIERNO_CONTROLES.md with no control: {sorted(doc_ids - code_ids)}"


def test_the_document_table_agrees_with_the_catalogue_on_state_and_source():
    rows = {}
    for line in _DOC.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 5 and re.fullmatch(r"`[TDG]-[A-Z-]+`", cells[0]):
            rows[cells[0].strip("`")] = cells
    assert set(rows) == {c.id for c in gc.CONTROLS}
    for c in gc.CONTROLS:
        _id, _criterion, consequence, source, status = rows[c.id]
        assert consequence.strip("`") == c.consequence, c.id
        assert status == c.status, c.id
        assert source.strip("`") == (c.source or "—"), c.id


def test_the_agents_index_lists_the_document():
    assert "`GOBIERNO_CONTROLES.md`" in (_ROOT / "AGENTS.md").read_text(encoding="utf-8")
