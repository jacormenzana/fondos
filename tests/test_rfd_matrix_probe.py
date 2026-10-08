"""
tests/test_rfd_matrix_probe.py -- the READY_FOR_DEPLOY evidence matrix (scripts/audit/rfd_matrix_probe.py). R-7: the decision logic and the spec are pure;
the probes that read git / the database / Windows tasks are exercised by running the script, never from the test suite.
"""
import importlib.util
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("rfd_matrix_probe", _ROOT / "scripts" / "audit" / "rfd_matrix_probe.py")
m = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(m)


def test_a_failed_probe_blocks_a_manual_one_holds_and_only_all_green_is_ready():
    assert m.verdict([(True, "a"), (True, "b")]) == "READY"
    assert m.verdict([(True, "a"), (None, "owner action")]) == "MANUAL"
    assert m.verdict([(True, "a"), (False, "flag off"), (None, "owner action")]) == "BLOCKED"       # BLOCKED beats MANUAL
    assert m.verdict([]) == "READY"                                                                   # nothing to prove is vacuous: the spec test below forbids empty specs


def test_every_spec_entry_has_probes_of_known_kinds_and_none_is_empty():
    assert m.spec_problems() == []
    assert all(probes for probes in m.SPEC.values())
    assert m.spec_problems({"FND-1": [("nope", 1)]}) == ["FND-1: unknown probe 'nope'"]


def test_every_ticket_is_pinned_to_a_commit_or_a_dependency():
    for tid, probes in m.SPEC.items():
        assert any(p[0] in ("pushed", "ticket_closed", "task_ok") for p in probes), f"{tid} has no objective anchor"
        assert re.fullmatch(r"FND-\d{4}", tid)


def test_the_dormant_flag_tickets_need_their_flag_on_and_a_recompute_by_the_owner():
    for tid in ("FND-0240", "FND-0241", "FND-0242"):
        kinds = [p[0] for p in m.SPEC[tid]]
        assert "flag" in kinds and "manual" in kinds, tid


def test_the_markdown_marks_every_state():
    rows = [("FND-1", "READY", [(True, "ok thing")]), ("FND-2", "BLOCKED", [(False, "flag off")]), ("FND-3", "MANUAL", [(None, "owner")])]
    md = m.render_markdown(rows)
    assert "| FND-1 | **READY** | OK: ok thing |" in md and "FAIL: flag off" in md and "MANUAL: owner" in md


def test_the_code_reference_probe_ignores_docstrings_and_catches_real_reads(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "ROOT", tmp_path)
    (tmp_path / "scorer.py").write_text('"""history: beta_rate_eu < -0.10 was a multiplier"""\nx = 1\n', encoding="utf-8")
    pattern = r"""["']beta_(oil|rate_eu)["']"""
    assert m.probe_absent_re_in("scorer.py", pattern)[0] is True
    (tmp_path / "scorer.py").write_text('beta = row["beta_rate_eu"]\n', encoding="utf-8")
    ok, detail = m.probe_absent_re_in("scorer.py", pattern)
    assert ok is False and "STILL references" in detail
