# proyecto3/tests/test_metrics_lineage_gate_fnd0306.py
# -*- coding: utf-8 -*-
"""
FND-0306 slice 1c: the promotion gate compares the stored P2 metrics the DRAFT was built on with those of the evidence audit (shared/governance/lineage.py),
SHADOW first. Pure: no DB, no pipeline imports (R-7).

    python -m pytest proyecto3/tests/test_metrics_lineage_gate_fnd0306.py -v
"""
import json
import os
import sys
from datetime import datetime, timezone

import pytest

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import shared.config as cfg  # noqa: E402
from proyecto3.src import scenario_lifecycle as lc  # noqa: E402
from shared.governance import controls as gc  # noqa: E402
from shared.governance import lineage as ln  # noqa: E402
from shared.statistical_audit.gating import AuditScope  # noqa: E402

BUILT_AT = datetime(2026, 11, 12, 22, 5, tzinfo=timezone.utc)
AUDIT_AT = datetime(2026, 11, 12, 22, 10, tzinfo=timezone.utc)
RUN_1, RUN_2 = "P2-20261112_194930-2984d0", "P2-20261113_090000-bbbbbb"
ROWS = [("IE0001", "20261112", RUN_1, 40), ("LU0002", "20261112", RUN_1, 38), ("ES0003", "20261112", RUN_1, 30)]
SCOPE = ("IE0001", "LU0002")


def _verdict(**over):
    base = dict(status="DRAFT", n_funds=30, weight_sum=1.0, stale_checks=[], exposure_non_eur=0.2, budget=0.3,
                audit=AuditScope(run_id="run1", run_ts=AUDIT_AT), built_at=BUILT_AT)
    base.update(over)
    return lc.evaluate_promotion(**base)


def _fp(rows):
    return ln.fingerprint(rows)


def _draft_stamp(rows=ROWS):
    return {"version": "v1", "universe": _fp(rows).as_dict(), "scenario": _fp([r for r in rows if r[0] in SCOPE]).as_dict()}


def _manifest(rows=ROWS):
    return {"calc_version": "20261112", "metrics_lineage": _fp(rows).as_dict()}


# ---------------- the pure gate ----------------

def test_with_the_switch_off_nothing_changes():
    assert _verdict().blockers == [] and _verdict(lineage=None).blockers == []


def test_a_match_adds_nothing():
    v = _verdict(lineage=(ln.MATCH, "30 funds identical"))
    assert v.blockers == [] and v.overridden == []


@pytest.mark.parametrize("state", [ln.MISMATCH, ln.UNKNOWN, ln.AMBIGUOUS])
def test_anything_but_a_match_refuses_under_its_own_stable_code(state):
    v = _verdict(lineage=(state, "detail of the comparison"))
    lines = [b for b in v.blockers if b.startswith("[GATE:METRICS_LINEAGE]")]
    assert len(lines) == 1 and state in lines[0] and "detail of the comparison" in lines[0]


def test_a_reasoned_override_with_its_ticket_covers_it_and_is_logged_as_an_override():
    v = _verdict(lineage=(ln.UNKNOWN, "no lineage stamp"), accept_alarms="DRAFT built before the feature", accept_ap="FND-0306", ap_exists=True)
    assert v.blockers == [] and [code for code, _ in v.overridden] == ["METRICS_LINEAGE"]


def test_the_override_without_its_ticket_is_refused():
    v = _verdict(lineage=(ln.MISMATCH, "x"), accept_alarms="because I say so")
    assert any(b.startswith("[GATE:OVERRIDE_AP]") for b in v.blockers) and any("[GATE:METRICS_LINEAGE]" in b for b in v.blockers)


def test_the_gate_is_a_catalogued_dormant_data_control_behind_a_switch_that_ships_off():
    assert "METRICS_LINEAGE" in lc.OVERRIDABLE_GATES and lc.GATE_METRICS_LINEAGE == "METRICS_LINEAGE"
    control = next(c for c in gc.CONTROLS if c.source == gc.SOURCE_GATE + "METRICS_LINEAGE")
    assert control.id == "D-LINEAGE" and control.pillar == gc.PILLAR_DATA and control.dormant_switch == "PROMOTION_AUDIT_IDENTITY_GATE_ENABLED"
    assert cfg.PROMOTION_AUDIT_IDENTITY_GATE_ENABLED is False


# ---------------- the comparisons ----------------

def test_a_draft_and_an_audit_on_the_same_data_match_in_the_three_comparisons():
    got = ln.compare_draft(_draft_stamp(), _manifest(), _fp(ROWS), _fp([r for r in ROWS if r[0] in SCOPE]))
    assert {k: v[0] for k, v in got.items()} == {"audit": ln.MATCH, "universe_now": ln.MATCH, "scenario_now": ln.MATCH}


def test_a_p2_run_after_the_draft_and_the_audit_is_seen_by_the_now_comparisons_not_by_the_audit_one():
    later = [("IE0001", "20261112", RUN_2, 40)] + ROWS[1:]                      # a fund OF the scenario was recomputed afterwards
    got = ln.compare_draft(_draft_stamp(), _manifest(), _fp(later), _fp([r for r in later if r[0] in SCOPE]))
    assert got["audit"][0] == ln.MATCH and got["universe_now"][0] == ln.MISMATCH and got["scenario_now"][0] == ln.MISMATCH


def test_a_recompute_of_a_fund_outside_the_scenario_moves_only_the_universe_comparison():
    later = ROWS[:2] + [("ES0003", "20261112", RUN_2, 30)]
    got = ln.compare_draft(_draft_stamp(), _manifest(), _fp(later), _fp([r for r in later if r[0] in SCOPE]))
    assert got["universe_now"][0] == ln.MISMATCH and got["scenario_now"][0] == ln.MATCH


def test_an_audit_that_judged_other_data_than_the_draft_is_a_mismatch():
    audited_later = [("IE0001", "20261112", RUN_2, 40)] + ROWS[1:]
    assert ln.compare_draft(_draft_stamp(), _manifest(audited_later), _fp(ROWS), _fp(ROWS))["audit"][0] == ln.MISMATCH


def test_a_draft_built_before_the_feature_is_unknown_never_a_pass():
    got = ln.compare_draft(None, _manifest(), _fp(ROWS), _fp(ROWS))
    assert {v[0] for v in got.values()} == {ln.UNKNOWN}
    assert ln.compare_draft({"other": 1}, _manifest(), _fp(ROWS), _fp(ROWS))["audit"][0] == ln.UNKNOWN


@pytest.mark.parametrize("manifest", [None, {}, {"calc_version": "20261112"}, {"metrics_lineage": {"version": "v1"}}, "not json", 7])
def test_an_audit_header_without_a_usable_fingerprint_is_unknown(manifest):
    assert ln.compare_draft(_draft_stamp(), manifest, _fp(ROWS), _fp(ROWS))["audit"][0] == ln.UNKNOWN


def test_a_manifest_read_as_json_text_is_accepted():
    assert ln.compare_draft(_draft_stamp(), json.dumps(_manifest()), _fp(ROWS), _fp(ROWS))["audit"][0] == ln.MATCH


def test_an_unreadable_current_lineage_is_unknown():
    assert ln.compare_draft(_draft_stamp(), _manifest(), None, None)["universe_now"][0] == ln.UNKNOWN


# ---------------- wiring of the launcher ----------------

def _promote_source():
    with open(os.path.join(_ROOT, "scripts", "launch", "p3_promote_scenario.py"), encoding="utf-8") as fh:
        return fh.read()


def test_the_launcher_always_prints_the_verdicts_and_traces_only_a_real_shadow_attempt():
    src = _promote_source()
    assert "_print_lineage(lineage_results, lineage_problem, PROMOTION_AUDIT_IDENTITY_GATE_ENABLED)" in src
    assert "if not PROMOTION_AUDIT_IDENTITY_GATE_ENABLED and lineage_results and not args.dry_run:" in src
    assert "lc.record_lineage_shadow(conn, args.scenario_id, getpass.getuser(), lineage_results)" in src


def test_with_the_switch_on_the_launcher_feeds_the_audit_verdict_and_fails_closed_on_an_unreadable_lineage():
    src = _promote_source()
    assert 'gate_kw.update(lineage=lineage_results["audit"] if lineage_results else unreadable,' in src
    assert 'identity=lineage_results["identity"] if lineage_results else unreadable)' in src
    assert 'unreadable = ("UNKNOWN", f"no se pudo leer el linaje ({lineage_problem})")' in src


# ---------------- build identity: the git sha and the configuration of the build against the audit header ----------------

from shared.governance import run_identity as ri  # noqa: E402

DRAFT_ID = {"git_commit": "2a91f2d", "git_clean": True, "config_hash": "v1:aaaaaaaaaaaa"}
HEADER = {"git_commit": "2a91f2d6d53c", "git_clean": True, "config_hash": "v1:aaaaaaaaaaaa", "catalog_version": "x", "manifest": {}}


def test_the_same_code_and_configuration_match_even_when_the_short_shas_have_different_lengths():
    state, detail = ri.identity_verdict(DRAFT_ID, HEADER)
    assert state == "MATCH" and "2a91f2d" in detail


def test_a_changed_configuration_is_a_mismatch_even_with_the_same_sha():
    state, detail = ri.identity_verdict(DRAFT_ID, {**HEADER, "config_hash": "v1:bbbbbbbbbbbb"})
    assert state == "MISMATCH" and "configuration differs" in detail and "aaaaaaaaaaaa" in detail and "bbbbbbbbbbbb" in detail


def test_another_commit_between_the_build_and_the_audit_is_a_mismatch():
    state, detail = ri.identity_verdict(DRAFT_ID, {**HEADER, "git_commit": "b29bba9"})
    assert state == "MISMATCH" and "code differs" in detail and "b29bba9" in detail


def test_equal_shas_on_a_tree_with_modified_files_do_not_identify_the_code():
    for who, side in (("DRAFT", ({**DRAFT_ID, "git_clean": False}, HEADER)), ("audit", (DRAFT_ID, {**HEADER, "git_clean": False})),
                      ("unknown", ({**DRAFT_ID, "git_clean": None}, HEADER))):
        state, detail = ri.identity_verdict(*side)
        assert state == "UNKNOWN" and "does not identify the code" in detail, who


def test_a_difference_outranks_an_unclean_tree():
    assert ri.identity_verdict({**DRAFT_ID, "git_clean": False}, {**HEADER, "config_hash": "v1:cccccccccccc"})[0] == "MISMATCH"


@pytest.mark.parametrize("draft", [None, {}, {"git_commit": "2a91f2d"}, {"config_hash": "v1:aaaaaaaaaaaa"}, "x"])
def test_a_draft_without_a_build_identity_is_unknown(draft):
    assert ri.identity_verdict(draft, HEADER)[0] == "UNKNOWN"


@pytest.mark.parametrize("header", [None, {}, {"git_commit": "2a91f2d"}, {"config_hash": "v1:aaaaaaaaaaaa"}])
def test_an_audit_header_without_an_identity_is_unknown(header):
    assert ri.identity_verdict(DRAFT_ID, header)[0] == "UNKNOWN"


def test_the_states_use_the_same_words_as_the_lineage_verdicts():
    assert {ri.identity_verdict(DRAFT_ID, HEADER)[0], ri.identity_verdict(None, HEADER)[0],
            ri.identity_verdict(DRAFT_ID, {**HEADER, "git_commit": "zzzzzzz"})[0]} == {ln.MATCH, ln.UNKNOWN, ln.MISMATCH}


def test_build_identity_has_the_three_audit_header_fields_and_survives_a_git_failure(monkeypatch):
    got = ri.build_identity()
    assert set(got) == {"git_commit", "git_clean", "config_hash"} and got["config_hash"].startswith("v1:")
    monkeypatch.setattr(ri, "git_state", lambda *a, **k: (None, None))
    assert ri.build_identity()["git_commit"] is None
    assert ri.identity_verdict(ri.build_identity(), HEADER)[0] == "UNKNOWN"


def test_the_identity_gate_refuses_only_when_it_is_given_and_is_not_a_match():
    assert _verdict().blockers == [] and _verdict(identity=None).blockers == [] and _verdict(identity=("MATCH", "same")).blockers == []
    lines = [b for b in _verdict(identity=("MISMATCH", "code differs")).blockers if b.startswith("[GATE:AUDIT_IDENTITY]")]
    assert len(lines) == 1 and "MISMATCH" in lines[0] and "code differs" in lines[0]
    v = _verdict(identity=("UNKNOWN", "no identity"), accept_alarms="DRAFT built before the feature", accept_ap="FND-0306", ap_exists=True)
    assert v.blockers == [] and [code for code, _ in v.overridden] == ["AUDIT_IDENTITY"]


def test_both_gates_can_fire_together_and_one_override_covers_both():
    v = _verdict(lineage=("MISMATCH", "a"), identity=("MISMATCH", "b"), accept_alarms="rebuild is scheduled", accept_ap="FND-0306", ap_exists=True)
    assert v.blockers == [] and sorted(code for code, _ in v.overridden) == ["AUDIT_IDENTITY", "METRICS_LINEAGE"]


def test_the_identity_control_is_a_catalogued_dormant_data_control_on_the_same_switch():
    assert "AUDIT_IDENTITY" in lc.OVERRIDABLE_GATES
    control = next(c for c in gc.CONTROLS if c.source == gc.SOURCE_GATE + "AUDIT_IDENTITY")
    assert control.id == "D-AUDIT-IDENTITY" and control.pillar == gc.PILLAR_DATA and control.dormant_switch == "PROMOTION_AUDIT_IDENTITY_GATE_ENABLED"


def test_the_draft_build_stamps_its_identity_and_the_launcher_reads_the_clean_flag_apart():
    with open(os.path.join(_ROOT, "proyecto3", "src", "portfolio_builder.py"), encoding="utf-8") as fh:
        assert 'portfolio.macro_context["build_identity"] = _build_identity()' in fh.read()
    src = _promote_source()
    assert 'results["identity"] = identity_verdict(build_id, header)' in src
    assert 'SELECT git_clean FROM audit_run WHERE run_id = %s AND domain = %s' in src
