"""
tests/test_rfd_matrix_probe.py -- the READY_FOR_DEPLOY evidence matrix (scripts/gov/backlog/rfd_matrix_probe.py). R-7: the decision logic and the spec are pure;
the probes that read git / the database / Windows tasks are exercised by running the script, never from the test suite.
"""
import importlib.util
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("rfd_matrix_probe", _ROOT / "scripts" / "gov" / "backlog" / "rfd_matrix_probe.py")
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


def test_ticket_closed_probe_reads_through_the_backlog_client_and_never_passes_on_a_failed_lookup():
    assert m.probe_ticket_closed("FND-1", lookup=lambda t: "CLOSED") == (True, "FND-1 is CLOSED")
    ok, detail = m.probe_ticket_closed("FND-1", lookup=lambda t: "IN_PROGRESS")
    assert ok is False and "IN_PROGRESS" in detail
    ok, detail = m.probe_ticket_closed("FND-1", lookup=lambda t: "LOOKUP_FAILED")
    assert ok is None and "check by hand" in detail                      # unverifiable is MANUAL, never READY
    assert m.probe_ticket_closed("FND-1", lookup=lambda t: "NOT_FOUND")[0] is False


def test_the_module_holds_no_sql_on_the_backlog_database():
    src = (_ROOT / "scripts" / "gov" / "backlog" / "rfd_matrix_probe.py").read_text(encoding="utf-8")
    assert "backlog.backlog" not in src and "psycopg" not in src


# ---------------------------------------------------------------- audit_isins_clear (FND-0384): a standing-accepted fund is cleared only when the audit no longer finds it

class _FakeAuditConn:
    """Just enough of a connection for the three queries of the probe: the latest finished run, the funds with a persisted violation in it, and the rules that hold an acceptance for the funds
    and carry a non-INFO finding in the run (the ones that can hide an accepted fund)."""
    def __init__(self, run=("post_run",), violating=(), hiding_rules=()):
        self.run, self.violating, self.hiding, self.sql = run, list(violating), list(hiding_rules), []

    def execute(self, sql, params=()):
        self.sql.append((sql, params))
        if "FROM audit_run" in sql:
            rows = [self.run] if self.run else []
        elif "FROM audit_finding_isin" in sql:
            rows = [(i,) for i in self.violating if i in params[2]]
        elif "FROM audit_accepted_finding" in sql:
            rows = [(r,) for r in self.hiding]
        else:
            rows = []
        return type("R", (), {"fetchone": lambda s: rows[0] if rows else None, "fetchall": lambda s: rows})()


def test_the_audit_clear_probe_fails_while_any_fund_is_still_violating_and_names_them():
    conn = _FakeAuditConn(violating=["LU1665237613", "ES0143559005"])
    ok, text = m.probe_db(conn, "audit_isins_clear", "cost_attributes", "LU1665237613", "LU1665237704", "ES0143559005")
    assert ok is False and "2 of 3" in text and "ES0143559005" in text and "post_run" in text


def test_the_audit_clear_probe_passes_when_the_latest_finished_run_persists_none_of_them():
    conn = _FakeAuditConn(violating=["LU9999999999"])                       # another fund violating is not this ticket's business
    ok, text = m.probe_db(conn, "audit_isins_clear", "cost_attributes", "LU1665237613", "LU1665237704")
    assert ok is True and "all 2" in text
    assert conn.sql[0][1][1] == list(m.AUDIT_FINISHED) and "finished_at IS NOT NULL" in conn.sql[0][0]       # an unfinished or INCOMPLETE run proves nothing


def test_the_audit_clear_probe_cannot_tell_without_a_finished_run_and_refuses_an_empty_list():
    assert m.probe_db(_FakeAuditConn(run=None), "audit_isins_clear", "cost_attributes", "LU1665237613")[0] is None      # unverifiable, never a pass
    import pytest
    with pytest.raises(ValueError):
        m.probe_db(_FakeAuditConn(), "audit_isins_clear", "cost_attributes")
    assert "audit_isins_clear" in m.PROBE_KINDS and m.spec_problems({"FND-1": [("audit_isins_clear", "cost_attributes", "X")]}) == []


def test_a_partly_accepted_rule_hides_the_accepted_funds_so_the_probe_cannot_pass_on_their_absence():
    """The 2026-11-07 case: OC_NOT_CONTAMINATED had 1 new violator and 69 accepted; the finding kept only the new one, so an accepted fund was missing from the persisted rows without being clear."""
    conn = _FakeAuditConn(violating=[], hiding_rules=["ANNUAL_LE_ACCUMULATED"])
    ok, text = m.probe_db(conn, "audit_isins_clear", "cost_attributes", "LU1665237613", "LU1665237704")
    assert ok is None and "cannot tell" in text and "ANNUAL_LE_ACCUMULATED" in text
    assert "severity <> 'INFO'" in conn.sql[-1][0] and "expires_at >= CURRENT_DATE" in conn.sql[-1][0]       # only an active acceptance next to a non-INFO finding hides anything
    assert m.probe_db(_FakeAuditConn(violating=["LU1665237613"], hiding_rules=["X"]), "audit_isins_clear", "cost_attributes", "LU1665237613")[0] is False       # a persisted violation stays a definite False
