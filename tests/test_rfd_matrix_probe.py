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
    def __init__(self, run=("post_run",), violating=(), hiding_rules=(), held_out=()):
        self.run, self.violating, self.hiding, self.held_out, self.sql = run, list(violating), list(hiding_rules), list(held_out), []

    def execute(self, sql, params=()):
        self.sql.append((sql, params))
        if "FROM audit_run" in sql:
            rows = [self.run] if self.run else []
        elif "FROM audit_finding_isin" in sql and "rule_id = ANY" in sql:                # the structural-marker query (FND-0423): the funds held out of the snapshot
            rows = [(i,) for i in self.held_out if i in params[2]]
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


# ---------------------------------------------------------------- held-out funds (FND-0423): not a violation, but not evaluated either

def test_the_structural_marker_is_excluded_from_the_violation_query_and_asked_for_on_its_own():
    conn = _FakeAuditConn(held_out=[])
    m.probe_db(conn, "audit_isins_clear", "p2_metrics", "LU0011850392")
    violations, markers = [(s, p) for s, p in conn.sql if "FROM audit_finding_isin" in s]
    assert "rule_id <> ALL(%s)" in violations[0] and violations[1][3] == list(m.STRUCTURAL_MARKER_RULES) == ["SNAPSHOT_HELD_OUT"]
    assert "rule_id = ANY(%s)" in markers[0] and markers[1][3] == ["SNAPSHOT_HELD_OUT"]


def test_a_held_out_fund_is_cannot_tell_not_the_permanent_fail_it_used_to_be_and_never_a_pass():
    frozen = ["LU0011850392", "LU0062756647"]
    ok, text = m.probe_db(_FakeAuditConn(held_out=frozen), "audit_isins_clear", "p2_metrics", *frozen)
    assert ok is None and "held out of the audit snapshot" in text and "2 of 2" in text and "audit_rule_clear" in text
    ok, text = m.probe_db(_FakeAuditConn(held_out=["LU0011850392"]), "audit_isins_clear", "p2_metrics", "LU0011850392", "LU0062756647")
    assert ok is None and "1 of 2" in text, "one held-out fund among clear ones still cannot be called clear"
    assert m.verdict([(ok, text)]) == "MANUAL"


def test_a_real_violation_beats_the_held_out_marker():
    ok, text = m.probe_db(_FakeAuditConn(violating=["LU0062756647"], held_out=["LU0011850392"]), "audit_isins_clear", "p2_metrics", "LU0011850392", "LU0062756647")
    assert ok is False and "LU0062756647" in text


# ---------------------------------------------------------------- db_check (FND-0423): a closed registry of named checks

class _FakeCheckConn:
    """Answers each db_check query with a canned value and records the SQL and parameters it was given."""
    def __init__(self, count=0, run=("post_run",), violating=(), hidden=False):
        self.count, self.run, self.violating, self.hidden, self.sql = count, run, list(violating), hidden, []

    def execute(self, sql, params=()):
        self.sql.append((sql, params))
        if "FROM audit_run" in sql:
            rows = [self.run] if self.run else []
        elif "FROM audit_finding_isin" in sql:
            rows = [(i,) for i in self.violating if i in params[3]]
        elif "FROM audit_accepted_finding" in sql:
            rows = [(1,)] if self.hidden else []
        else:
            rows = [(self.count,)]
        return type("R", (), {"fetchone": lambda s: rows[0] if rows else None, "fetchall": lambda s: rows})()


def test_db_check_is_a_known_kind_and_its_arguments_are_validated_before_anything_runs():
    assert "db_check" in m.PROBE_KINDS
    good = [["table_rows", "series_benchmark", "==", 0], ["ingestion_events", "BENCH_MS", "ERROR", "==", 0, 30], ["p2_run_summary", 1, 7],
            ["audit_rule_clear", "p2_metrics", "WINDOW_FISHER_IDENTITY", "LU0011850392", "LU0062756647"]]
    for args in good:
        assert m.validate_db_check(args) == [], args
        assert m.spec_problems({"FND-1": [("db_check", *args)]}) == []
    bad = [[], [5], ["nope"], ["table_rows", "fund_master; DROP TABLE x", "==", 0], ["table_rows", "series_benchmark", "=~", 0], ["table_rows", "series_benchmark", "==", -1],
           ["table_rows", "series_benchmark", "==", True], ["ingestion_events", "BENCH_MS", "OK", "==", 0, 0], ["ingestion_events", "BENCH MS", "OK", "==", 0, 5],
           ["p2_run_summary", 1], ["p2_run_summary", 1, 0], ["audit_rule_clear", "other_domain", "R", "LU0011850392"], ["audit_rule_clear", "p2_metrics", "R", "LU0011850392"],
           ["audit_rule_clear", "p2_metrics", "WINDOW_FISHER_IDENTITY"], ["audit_rule_clear", "p2_metrics", "WINDOW_FISHER_IDENTITY", "short"]]
    for args in bad:
        assert m.validate_db_check(args), args
    assert m.spec_problems({"FND-1": [("db_check", "table_rows", "x", "==", 0)]})[0].startswith("FND-1: table_rows needs")


def test_the_registry_holds_only_static_count_queries_and_the_operators_are_a_whitelist():
    for name, sql in m.TABLE_ROWS_SQL.items():
        assert sql.startswith("SELECT COUNT(*) FROM ") and not re.search(r"[%{;]", sql), name
    assert [m.compare(3, op, 3) for op in m.DB_CHECK_COMPARATORS] == [True, True, True, False, False]
    import pytest
    with pytest.raises(ValueError):
        m.compare(1, "; DROP", 1)


def test_table_rows_compares_the_count_of_a_named_table_and_a_bad_condition_is_a_value_error():
    ok, text = m.probe_db(_FakeCheckConn(count=0), "db_check", "table_rows", "series_benchmark", "==", 0)
    assert ok is True and "series_benchmark: 0 row" in text
    assert m.probe_db(_FakeCheckConn(count=4), "db_check", "table_rows", "series_benchmark", "==", 0)[0] is False
    assert m.probe_db(_FakeCheckConn(count=27), "db_check", "table_rows", "nav_sources_older_180d", "<=", 10)[0] is False
    import pytest
    with pytest.raises(ValueError):
        m.probe_db(_FakeCheckConn(), "db_check", "table_rows", "fund_master", "==", 0)


def test_the_log_checks_are_positive_and_bound_their_parameters():
    conn = _FakeCheckConn(count=0)
    ok, text = m.probe_db(conn, "db_check", "ingestion_events", "BENCH_MS", "ERROR", "==", 0, 30)
    assert ok is True and "step BENCH_MS status ERROR in the last 30 d" in text and conn.sql[0][1] == ("BENCH_MS", "ERROR", 30)
    ok, text = m.probe_db(_FakeCheckConn(count=0), "db_check", "p2_run_summary", 1, 7)
    assert ok is False and "processed >= 1" in text, "a positive probe: no RUN_SUMMARY with processed funds is not a pass"
    conn = _FakeCheckConn(count=2)
    assert m.probe_db(conn, "db_check", "p2_run_summary", 100, 7)[0] is True and conn.sql[0][1] == (7, 100)


def test_audit_rule_clear_is_about_one_rule_so_a_held_out_fund_is_not_an_obstacle():
    funds = ["LU0011850392", "LU0062756647"]
    ok, text = m.probe_db(_FakeCheckConn(violating=[]), "db_check", "audit_rule_clear", "p2_metrics", "WINDOW_FISHER_IDENTITY", *funds)
    assert ok is True and "post_run" in text and "WINDOW_FISHER_IDENTITY" in text
    ok, text = m.probe_db(_FakeCheckConn(violating=["LU0062756647"]), "db_check", "audit_rule_clear", "p2_metrics", "WINDOW_FISHER_IDENTITY", *funds)
    assert ok is False and "1 of 2" in text and "LU0062756647" in text
    assert m.probe_db(_FakeCheckConn(run=None), "db_check", "audit_rule_clear", "p2_metrics", "WINDOW_FISHER_IDENTITY", *funds)[0] is None
    ok, text = m.probe_db(_FakeCheckConn(hidden=True), "db_check", "audit_rule_clear", "p2_metrics", "WINDOW_FISHER_IDENTITY", *funds)
    assert ok is None and "hides the accepted ones" in text, "a partly accepted rule cannot be cleared by absence"
    conn = _FakeCheckConn()
    m.probe_db(conn, "db_check", "audit_rule_clear", "p2_metrics", "WINDOW_FISHER_IDENTITY", *funds)
    assert conn.sql[1][1] == ("post_run", "p2_metrics", "WINDOW_FISHER_IDENTITY", funds), "the rule id and the ISINs are bound, never interpolated"
