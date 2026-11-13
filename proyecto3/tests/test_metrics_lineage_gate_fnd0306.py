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
    assert 'lineage=lineage_results["audit"] if lineage_results else ("UNKNOWN"' in src
