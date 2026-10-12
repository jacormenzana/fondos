"""
tests/test_governance_orchestrator.py -- the two-pillar orchestrator (FND-0265, GOV-06): the plan's exit matrix as pure tests, the code
integrity predicate (D1) on a throwaway git repository, the weights tolerance (D2), the launcher warning (D3) and the cost replay (D8).
No database; the end-to-end promotion runs are in proyecto3/tests/test_promotion_gate_negative_pg.py.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from proyecto3.src import scenario_lifecycle as lc  # noqa: E402
from shared.governance import controls as gc  # noqa: E402
from shared.governance import orchestrator as orch  # noqa: E402
from shared.governance import run_identity as ri  # noqa: E402

OK_CODE = {"ok": True, "detail": "HEAD abc == origin/master, clean tracked tree"}
DIRTY_CODE = {"ok": False, "detail": "2 modified tracked file(s)"}
HEADER = {"git_commit": "abc1234", "config_hash": "v1:000000000000", "catalog_version": "cv", "manifest": {"calc_version": "20261011"}}


def verdict(blockers=(), **kw):
    base = dict(code_state=OK_CODE, evidence_header=HEADER, cost_defects=(), cost_has_run=True)
    base.update(kw)
    return orch.authorise_promotion(list(blockers), **base)


def states(v):
    return {p: v.pillars[p].state for p in gc.PILLARS}


# --- the exit matrix (plan §3, phase 06) ---------------------------------------------------------------------------------------------
def test_success_authorises_with_every_pillar_passing():
    v = verdict()
    assert v.decision == gc.AUTHORISED and set(states(v).values()) == {gc.STATE_PASS} and v.blockers == []


@pytest.mark.parametrize("line", [lc.gate_line(lc.GATE_FX, "exposure 35% > 30%"), lc.gate_line(lc.GATE_CASH, "cash 12%"),
                                  lc.gate_line(lc.GATE_WEIGHTS, "weights 0.9990"), lc.gate_line(lc.GATE_STATUS, "not a DRAFT")])
def test_a_pillar_1_failure_refuses(line):
    v = verdict([line])
    assert states(v)[gc.PILLAR_TECHNICAL] == gc.STATE_FAIL and v.decision == gc.REFUSED and v.blockers == [line]


def test_a_critical_defect_on_scope_refuses_through_the_data_pillar():
    v = verdict([lc.gate_line(lc.GATE_DATA_SCENARIO, "WINDOW_FISHER_IDENTITY on LU1")])
    assert states(v)[gc.PILLAR_DATA] == gc.STATE_FAIL and v.decision == gc.REFUSED


def test_a_non_blocking_anomaly_authorises():
    # statistical anomalies never produce a gate line (scenario_lifecycle lists them as information): nothing to refuse
    assert verdict().decision == gc.AUTHORISED


@pytest.mark.parametrize("code", ["AUDIT_STALE", "EXCEPTIONS"])
def test_evidence_that_cannot_be_established_is_incomplete_and_refuses(code):
    v = verdict([f"[GATE:{code}] no evidence"])
    assert states(v)[gc.PILLAR_DATA] == gc.STATE_INCOMPLETE and v.state == gc.STATE_INCOMPLETE and v.decision == gc.REFUSED


@pytest.mark.parametrize("header,fragment", [
    (None, "no tiene cabecera"),
    ({**HEADER, "manifest": {}}, "manifiesto"),
    ({**HEADER, "git_commit": None}, "git_commit"),
    ({**HEADER, "config_hash": ""}, "config_hash"),
])
def test_an_evidence_run_without_its_identity_is_incomplete(header, fragment):
    v = verdict(evidence_header=header)
    assert states(v)[gc.PILLAR_TECHNICAL] == gc.STATE_INCOMPLETE and v.decision == gc.REFUSED
    assert any(b.startswith("[GATE:MANIFEST]") and fragment in b for b in v.blockers)


def test_code_integrity_warns_in_shadow_and_refuses_when_its_switch_is_on():
    shadow = verdict(code_state=DIRTY_CODE, code_gate_enabled=False)
    assert shadow.decision == gc.AUTHORISED and states(shadow)[gc.PILLAR_TECHNICAL] == gc.STATE_PASS_WITH_WARNINGS
    assert shadow.pillars[gc.PILLAR_TECHNICAL].warnings[0].endswith(orch.SHADOW_SUFFIX)
    on = verdict(code_state=DIRTY_CODE, code_gate_enabled=True)
    assert on.decision == gc.REFUSED and on.blockers[0].startswith("[GATE:CODE]")


def test_the_cost_domain_warns_in_shadow_and_refuses_when_its_switch_is_on():
    defect = SimpleNamespace(rule_id="OC_NOT_CONTAMINATED", isins=("LU1",))
    shadow = verdict(cost_defects=[defect])
    assert shadow.decision == gc.AUTHORISED and states(shadow)[gc.PILLAR_ORCHESTRATION] == gc.STATE_PASS_WITH_WARNINGS
    on = verdict(cost_defects=[defect], cost_gate_enabled=True)
    assert on.decision == gc.REFUSED and on.blockers[0].startswith("[GATE:COST]")
    no_run = verdict(cost_has_run=False, cost_gate_enabled=True)
    assert no_run.decision == gc.REFUSED and "no hay auditoria de costes" in no_run.blockers[0]


def test_hint_lines_are_notes_and_overrides_pass_through():
    hint = "(--accept-alarms \"motivo\" --ap FND-nnnn cubre los defectos y huecos de cobertura de la CARTERA)"
    v = verdict([hint], overridden=[("DATA_SCENARIO", "x")])
    assert v.blockers == [] and v.notes == (hint,) and v.overridden == (("DATA_SCENARIO", "x"),)


def test_every_lifecycle_gate_code_has_a_pillar_in_the_catalogue():
    codes = {v for k, v in vars(lc).items() if k.startswith("GATE_") and isinstance(v, str)}
    assert all(orch.pillar_of(c) in gc.PILLARS for c in codes)
    assert orch.pillar_of("CODE") == gc.PILLAR_TECHNICAL and orch.pillar_of("COST") == gc.PILLAR_ORCHESTRATION
    assert orch.INCOMPLETE_GATES <= codes


def test_the_verdict_line_names_every_pillar_and_the_decision():
    line = orch.verdict_line(verdict([lc.gate_line(lc.GATE_FX, "x")]))
    assert line == "two-pillar TECHNICAL=FAIL DATA=PASS ORCHESTRATION=PASS -> FAIL REFUSED"


# --- D2: weights tolerance --------------------------------------------------------------------------------------------------------
def test_the_d2_tolerance_is_tighter_than_the_legacy_gate():
    from shared.statistical_audit.gating import AuditScope
    from datetime import datetime, timezone
    kw = dict(status="DRAFT", n_funds=10, weight_sum=0.9997, stale_checks=[], exposure_non_eur=0.1, budget=0.3,
              audit=AuditScope(run_id="r", run_ts=datetime(2026, 10, 12, tzinfo=timezone.utc)))
    assert lc.evaluate_promotion(**kw).blockers == []                                                  # legacy 1e-3 lets 3e-4 pass
    assert lc.evaluate_promotion(**kw, weight_tolerance=lc.WEIGHT_TOLERANCE).blockers[0].startswith("[GATE:WEIGHTS]")
    assert lc.evaluate_promotion(**{**kw, "weight_sum": 1.0 - 1e-12}, weight_tolerance=lc.WEIGHT_TOLERANCE).blockers == []


# --- D1: the code-integrity predicate on a real throwaway repository ---------------------------------------------------------------
def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


@pytest.fixture()
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "a.txt").write_text("one\n")
    _git(tmp_path, "add", "a.txt")
    _git(tmp_path, "commit", "-q", "-m", "one")
    _git(tmp_path, "update-ref", "refs/remotes/origin/master", "HEAD")                 # as if fetched
    return tmp_path


def test_code_integrity_ok_at_the_tip_with_a_clean_tree(repo):
    s = ri.code_integrity(repo)
    assert s["ok"] and s["at_tip"] and s["clean"]


def test_untracked_files_do_not_count(repo):
    (repo / "scratch.tmp").write_text("x")
    assert ri.code_integrity(repo)["ok"]


def test_a_modified_tracked_file_fails(repo):
    (repo / "a.txt").write_text("two\n")
    s = ri.code_integrity(repo)
    assert not s["ok"] and s["clean"] is False and "modified tracked" in s["detail"]


def test_a_local_commit_ahead_of_the_tip_fails(repo):
    (repo / "b.txt").write_text("b\n")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-q", "-m", "local")
    s = ri.code_integrity(repo)
    assert not s["ok"] and s["at_tip"] is False and "push first" in s["detail"]


def test_outside_a_repository_it_reports_instead_of_raising(tmp_path):
    s = ri.code_integrity(tmp_path)
    assert s["ok"] is False and s["at_tip"] is None


# --- D3: the launcher preflight only ever warns ------------------------------------------------------------------------------------
def test_the_preflight_reports_the_code_state_as_ok_or_warn_never_fail():
    sys.path.insert(0, str(_ROOT / "scripts" / "launch"))
    import p1p2_state as st

    class _Ok:
        def execute(self, *_a):
            return SimpleNamespace(fetchone=lambda: (1,))

        def close(self):
            pass

    env = {"FONDOS_PG_DSN_OWNER": "x", "FRED_API_KEY": "k"}
    ok = st.preflight_checks(env, lambda: _Ok(), 100.0, OK_CODE)
    bad = st.preflight_checks(env, lambda: _Ok(), 100.0, DIRTY_CODE)
    assert ("OK", f"code: {OK_CODE['detail']}") in ok
    assert [lv for lv, msg in bad if msg.startswith("code:")] == ["WARN"]
    assert not any(lv == "FAIL" for lv, _ in bad)


# --- D8: the cost replay ----------------------------------------------------------------------------------------------------------
def test_the_cost_replay_lists_what_the_gate_would_have_refused():
    sys.path.insert(0, str(_ROOT / "scripts" / "audit"))
    import cost_gate_replay as rp
    from datetime import datetime, timezone
    t = datetime(2026, 10, 1, tzinfo=timezone.utc)

    def scope_of(run_id, run_ts, isins):
        hits = [SimpleNamespace(rule_id="OC_NOT_CONTAMINATED", isins=("LU1",))] if run_id == "r_bad" and "LU1" in isins else []
        return SimpleNamespace(on_scenario=hits)

    out = rp.replay_rows([("r_bad", t), ("r_ok", t)], {"s1": ("PRODUCTION", {"LU1", "LU2"}), "s2": ("ARCHIVED", {"LU3"})}, scope_of)
    blocked = [(r[0], r[2]) for r in out if r[5]]
    assert blocked == [("r_bad", "s1")] and len(out) == 4
    lines = rp.summary_lines(out)
    assert lines[0].startswith("  r_bad (2026-10-01): 1/2 scenario(s) refused; 1 rule(s) on scenario funds, 0 rule(s) without ISIN detail")
    assert "0/2 scenario(s) refused" in lines[1]
