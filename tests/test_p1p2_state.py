"""
tests/test_p1p2_state.py — the resume guard and run telemetry of P1_P2_Complete.bat.

The logic lives in scripts/launch/p1p2_state.py precisely so it can be tested here without running
any real step: pure pytest, no database, runs on any OS. Telemetry is best-effort; the tests prove
it never changes an exit code.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "p1p2_state", Path(__file__).resolve().parent.parent / "scripts" / "launch" / "p1p2_state.py")
st = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(st)


@pytest.fixture()
def path(tmp_path, monkeypatch):
    p = tmp_path / "sub" / "P1_P2_Complete.state"
    monkeypatch.setenv("P1P2_STATE_FILE", str(p))
    return p


def _seed(path, result="FAILED", step=3, from_any=0, stamp="20260926_101500"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"LAST_RESULT={result}\nFAILED_STEP={step}\nSTAMP={stamp}\nLAST_FROM_ANY={from_any}\n")


# ─── write: the success path, the keys, atomicity ────────────────────────────────────────────────

def test_write_ok_produces_the_expected_keys(path):
    st.write_state("OK", 0, "20260926_120000", False)
    assert path.read_text() == ("LAST_RESULT=OK\nFAILED_STEP=0\nSTAMP=20260926_120000\nLAST_FROM_ANY=0\n"
                                "BASELINE_RUN_ID=\n")
    assert st.read_state(path) == {"LAST_RESULT": "OK", "FAILED_STEP": 0,
                                   "STAMP": "20260926_120000", "LAST_FROM_ANY": 0, "BASELINE_RUN_ID": ""}


def test_write_failed_records_the_failed_step_and_from_any(path):
    st.write_state("FAILED", 2, "20260926_120000", True)
    s = st.read_state(path)
    assert s["LAST_RESULT"] == "FAILED" and s["FAILED_STEP"] == 2 and s["LAST_FROM_ANY"] == 1


def test_write_replaces_atomically_and_leaves_no_temp_files(path):
    _seed(path, step=1)
    st.write_state("OK", 0, "S", False)
    assert st.read_state(path)["LAST_RESULT"] == "OK"
    assert [p.name for p in path.parent.iterdir()] == [path.name]


def test_a_failed_write_leaves_the_previous_state_untouched(path, monkeypatch):
    _seed(path, step=2)
    before = path.read_text()
    monkeypatch.setattr(st.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        st.write_state("OK", 0, "S", False)
    assert path.read_text() == before
    assert [p.name for p in path.parent.iterdir()] == [path.name]      # temp file cleaned up


# ─── read: fail closed ───────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("content", [
    "", "garbage", "LAST_RESULT=MAYBE\nFAILED_STEP=1\n", "LAST_RESULT=FAILED\nFAILED_STEP=x\n",
    "LAST_RESULT=FAILED\n",
])
def test_unreadable_or_malformed_state_reads_as_none(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    assert st.read_state(path) is None


def test_missing_state_reads_as_none(path):
    assert st.read_state(path) is None


# ─── check: plain runs ───────────────────────────────────────────────────────────────────────────

def test_plain_run_is_allowed_with_no_state_and_creates_no_content(path):
    rc, msg = st.check(None, False)
    assert rc == st.RC_OK and msg == ""
    assert path.exists() and path.read_text() == ""          # writability probe only


def test_plain_run_warns_once_about_a_previous_from_any(path):
    _seed(path, result="OK", step=0, from_any=1)
    rc, msg = st.check(None, False)
    assert rc == st.RC_OK and "--from-any" in msg
    st.write_state("OK", 0, "S2", False)                        # the run writes its own flag: cleared
    assert st.check(None, False) == (st.RC_OK, "")


def test_unwritable_state_path_aborts_before_the_run(tmp_path, monkeypatch):
    blocker = tmp_path / "a_file"
    blocker.write_text("x")
    monkeypatch.setenv("P1P2_STATE_FILE", str(blocker / "child" / "state"))   # parent is a file
    rc, msg = st.check(None, False)
    assert rc == st.RC_NOT_WRITABLE and "cannot write the state file" in msg


# ─── check: --from N ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("failed, n, allowed", [
    (3, 3, True), (3, 2, True), (3, 1, True),      # the failed step or earlier
    (3, 4, False),                                  # later than the failed step: refused
    (1, 3, False), (1, 1, True), (4, 4, True), (4, 3, True),
])
def test_from_n_is_allowed_only_at_or_before_the_failed_step(path, failed, n, allowed):
    _seed(path, step=failed)
    rc, msg = st.check(n, False)
    assert (rc == st.RC_OK) is allowed
    assert allowed or (rc == st.RC_REFUSED and "REFUSED" in msg)


def test_from_n_is_refused_when_the_last_run_was_ok(path):
    _seed(path, result="OK", step=0)
    assert st.check(2, False)[0] == st.RC_REFUSED


def test_from_n_is_refused_fail_closed_when_the_state_file_is_missing(path):
    assert st.check(2, False)[0] == st.RC_REFUSED


def test_from_n_is_refused_when_the_state_file_is_corrupt(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("LAST_RESULT=FAILED\nFAILED_STEP=")
    assert st.check(2, False)[0] == st.RC_REFUSED


@pytest.mark.parametrize("n", [0, 5, 9, -1])
def test_from_n_outside_1_to_4_is_a_usage_error(path, n):
    _seed(path)
    assert st.check(n, False)[0] == st.RC_BAD_ARGS


def test_from_any_bypasses_the_guard_and_says_so(path):
    _seed(path, step=1)
    rc, msg = st.check(4, True)
    assert rc == st.RC_OK and "bypassed" in msg
    assert st.check(4, True, path)[0] == st.RC_OK             # also with no state at all


# ─── CLI wiring and exit codes ───────────────────────────────────────────────────────────────────

def test_cli_check_and_write_round_trip(path):
    assert st.main(["write", "--result", "FAILED", "--failed-step", "2", "--stamp", "S"]) == 0
    assert st.main(["check", "--from", "2"]) == st.RC_OK
    assert st.main(["check", "--from", "3"]) == st.RC_REFUSED
    assert st.main(["write", "--result", "OK", "--stamp", "S2"]) == 0
    assert st.main(["check", "--from", "1"]) == st.RC_REFUSED       # last run OK: nothing to resume


def test_cli_bad_arguments_return_4_not_a_traceback(path):
    assert st.main(["check", "--from", "not-a-number"]) == st.RC_BAD_ARGS
    assert st.main(["frobnicate"]) == st.RC_BAD_ARGS


def test_from_any_writes_a_durable_row_but_never_fails_the_run(path, monkeypatch):
    rows = []
    monkeypatch.setattr(st, "log_event", lambda status, message: rows.append((status, message)) or True)
    assert st.main(["check", "--from", "4", "--from-any"]) == st.RC_OK
    assert rows and rows[0][0] == "FROM_ANY" and "--from 4" in rows[0][1]
    rows.clear()
    assert st.main(["check"]) == st.RC_OK and rows == []          # a plain run writes no such row


def test_step_telemetry_is_best_effort_and_reports_failures(path, monkeypatch, capsys):
    rows, tickets = [], []
    monkeypatch.setattr(st, "log_event", lambda status, message: rows.append((status, message)) or True)
    monkeypatch.setattr(st, "report_failure", lambda step, rc, secs: tickets.append((step, rc, secs)))
    assert st.main(["step", "--step", "1", "--rc", "0", "--start", "093240", "--end", "095217"]) == 0
    assert rows == [("STEP_OK", "PASO 1 rc=0 secs=1177")] and tickets == []
    assert st.main(["step", "--step", "2", "--rc", "3", "--start", "100000", "--end", "100005"]) == 0
    assert rows[-1][0] == "STEP_FAILED" and tickets == [(2, 3, 5)]


def _fake_db_modules(monkeypatch, *, connect_raises: bool):
    """Replace shared.db / core.sqlite_writer / shared.backlog_client with fakes, so these tests can
    never reach a real database whatever FONDOS_DB_BACKEND / .env say."""
    import sys
    import types
    calls = []

    class FakeConn:
        def commit(self): calls.append("commit")
        def close(self): calls.append("close")

    def get_connection():
        if connect_raises:
            raise RuntimeError("database is down")
        return FakeConn()

    shared_db = types.ModuleType("shared.db"); shared_db.get_connection = get_connection
    writer = types.ModuleType("core.sqlite_writer")
    writer.log_ingestion = lambda conn, isin, step, status, msg: calls.append((step, status, msg))
    backlog = types.ModuleType("shared.backlog_client")
    backlog.report_incident = lambda **kw: (_ for _ in ()).throw(RuntimeError("backlog down"))
    for name, mod in (("shared.db", shared_db), ("core.sqlite_writer", writer),
                      ("shared.backlog_client", backlog)):
        monkeypatch.setitem(sys.modules, name, mod)
    return calls


def test_log_event_writes_one_row_through_the_existing_helper(monkeypatch):
    calls = _fake_db_modules(monkeypatch, connect_raises=False)
    assert st.log_event("STEP_OK", "PASO 1 rc=0") is True
    assert ("P1P2_ORCH", "STEP_OK", "PASO 1 rc=0") in calls and "commit" in calls and "close" in calls


def test_telemetry_survives_a_broken_database_and_never_changes_the_exit_code(path, monkeypatch, capsys):
    _fake_db_modules(monkeypatch, connect_raises=True)
    assert st.log_event("STEP_OK", "x") is False
    assert "telemetry row skipped" in capsys.readouterr().err
    # a failed step: the row fails AND the backlog client fails; the launcher still gets exit 0
    assert st.main(["step", "--step", "1", "--rc", "1", "--start", "1", "--end", "2"]) == 0
    assert "backlog incident skipped" in capsys.readouterr().err


def test_seconds_handles_crossing_midnight_and_bad_input():
    assert st._seconds("235900", "000100") == 120
    assert st._seconds("100000", "100230") == 150
    assert st._seconds("", "100230") is None and st._seconds("xx", "yy") is None


# ─── baseline id (statistical-audit baseline shared with a resumed run) ─────────────────────────

def test_write_stores_the_baseline_id_and_baseline_prints_it(path, capsys):
    st.write_state("FAILED", 2, "S", False, baseline_id="pre_20260926_130000")
    assert st.read_state(path)["BASELINE_RUN_ID"] == "pre_20260926_130000"
    assert st.main(["baseline"]) == st.RC_OK
    assert capsys.readouterr().out.strip() == "pre_20260926_130000"


def test_baseline_is_empty_without_state_or_without_an_id(path, capsys):
    assert st.main(["baseline"]) == st.RC_OK and capsys.readouterr().out.strip() == ""
    st.write_state("OK", 0, "S", False)                       # no baseline id given
    assert st.main(["baseline"]) == st.RC_OK and capsys.readouterr().out.strip() == ""


def test_old_state_files_without_a_baseline_key_still_read(path):
    _seed(path, step=2)                                       # the format written before this key existed
    s = st.read_state(path)
    assert s is not None and s["BASELINE_RUN_ID"] == ""


def test_cli_write_accepts_the_baseline_flag(path):
    assert st.main(["write", "--result", "FAILED", "--failed-step", "3", "--stamp", "S",
                    "--baseline-id", "pre_X"]) == 0
    assert st.read_state(path)["BASELINE_RUN_ID"] == "pre_X"


# ─── ongoing-charge repair scope: only what THIS chain's P1 pass contaminated ───────────────────

def _fake_oc_db(monkeypatch, rows_by_call, *, raises=False):
    """shared.db faked so no real database can be reached; each get_connection() returns the next
    result set (list of ISINs) from `rows_by_call`."""
    import sys
    import types
    calls = iter(rows_by_call)

    class Conn:
        def __init__(self, isins): self._isins = isins
        def execute(self, sql):
            assert "Ongoing_Charge_Recurrent" in sql and "ACI_RHP" in sql
            return types.SimpleNamespace(fetchall=lambda: [(i,) for i in self._isins])
        def close(self): pass

    def get_connection():
        if raises:
            raise RuntimeError("database is down")
        return Conn(next(calls))

    mod = types.ModuleType("shared.db"); mod.get_connection = get_connection
    monkeypatch.setitem(sys.modules, "shared.db", mod)


@pytest.fixture()
def ocfile(tmp_path, monkeypatch):
    p = tmp_path / "oc_before"
    monkeypatch.setenv("P1P2_OC_FILE", str(p))
    return p


def test_oc_newly_returns_only_funds_contaminated_since_the_snapshot(ocfile, monkeypatch):
    _fake_oc_db(monkeypatch, [["A", "B"], ["A", "B", "C", "D"]])     # before: 2 known; after: +C, +D
    assert st.oc_before() is True
    assert st.oc_newly() == ["C", "D"]                                 # the 2 pre-existing are untouched


def test_oc_newly_is_empty_when_nothing_new(ocfile, monkeypatch):
    _fake_oc_db(monkeypatch, [["A"], ["A"]])
    st.oc_before()
    assert st.oc_newly() == []


def test_oc_newly_without_a_snapshot_repairs_nothing(ocfile, monkeypatch):
    _fake_oc_db(monkeypatch, [["A", "B"]])
    assert st.oc_newly() == []                                         # never guess


def test_a_failed_snapshot_removes_a_stale_one_so_it_cannot_be_reused(ocfile, monkeypatch):
    ocfile.write_text("STALE\n")
    _fake_oc_db(monkeypatch, [], raises=True)
    assert st.oc_before() is False
    assert not ocfile.exists()


def test_oc_newly_is_empty_when_the_database_cannot_be_read(ocfile, monkeypatch):
    ocfile.write_text("A\n")
    _fake_oc_db(monkeypatch, [], raises=True)
    assert st.oc_newly() == []


def test_cli_oc_commands_print_the_list_and_never_fail_the_launcher(ocfile, monkeypatch, capsys):
    _fake_oc_db(monkeypatch, [["A"], ["A", "X", "Y"]])
    assert st.main(["oc-before"]) == st.RC_OK
    assert st.main(["oc-newly"]) == st.RC_OK
    assert capsys.readouterr().out.strip() == "X,Y"
    _fake_oc_db(monkeypatch, [], raises=True)
    assert st.main(["oc-before"]) == st.RC_OK and st.main(["oc-newly"]) == st.RC_OK
