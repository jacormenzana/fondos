# tests/test_owner_deploy_gate.py
# -*- coding: utf-8 -*-
"""FND-0377: the local DDL gate of the automatic deployment (scripts/ops/owner_deploy.py ddl_gate and its place in apply_pending). GitHub CI is out of the loop, so before the watcher touches the live
backlog it runs the backlog's hermetic migration, rollback and acceptance tests on a clean clone of the commit and applies nothing unless they really ran and passed. R-7: no database, no container, no
network: the clone and the test run are fakes."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "ops"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import owner_deploy as od  # noqa: E402
from test_owner_deploy import HEAD, NOW, SECRET, AutoRunner, FakeVault, keys  # noqa: E402

OK_TEXT = "..\n34 passed in 120.00s\n"


def fake_checkout(files=("tests/test_backlog_events_pg.py", "tests/test_backlog_gate_pg.py", "tests/test_owner_deploy_pg.py", "tests/test_other.py")):
    def checkout(ref, dest, root):
        for f in files:
            p = Path(dest) / f
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("def test_x():\n    pass\n", encoding="utf-8")
    return checkout


class GateRunner:
    def __init__(self, text=OK_TEXT, rc=0, boom=None):
        self.text, self.rc, self.boom, self.calls = text, rc, boom, []

    def __call__(self, cmd, env, cwd, timeout):
        self.calls.append((list(cmd), dict(env), Path(cwd)))
        if self.boom:
            raise self.boom
        return self.rc, self.text


def gate(runner, **kw):
    return od.ddl_gate(HEAD, python="py", checkout=kw.pop("checkout", fake_checkout()), runner=runner, **kw)


# ── ddl_gate ────────────────────────────────────────────────────────────────────

def test_the_gate_runs_the_backlog_ddl_tests_on_a_clean_clone_and_passes_only_on_tests_that_really_passed():
    r = GateRunner()
    kind, detail = gate(r)
    assert kind == "pass" and "34 hermetic DDL" in detail and "3 files" in detail and HEAD[:10] in detail
    cmd, env, cwd = r.calls[0]
    assert cmd[:5] == ["py", "scripts/ops/run_pg_tests.py", "--image", "postgres:17", "tests/test_backlog_events_pg.py"] and "tests/test_other.py" not in cmd          # only the DDL tests
    assert cmd[4:] == sorted(cmd[4:]) and "test_owner_deploy_pg.py" in " ".join(cmd) and cwd.name == "tree"                                                         # run inside the clone, never the working tree


def test_no_connection_string_reaches_the_gates_tests(monkeypatch):
    monkeypatch.setenv(od.OWNER_VAR, SECRET)
    monkeypatch.setenv(od.FACTS_VAR, "postgresql://x")
    monkeypatch.setenv("FONDOS_PG_DSN", "postgresql://live")
    monkeypatch.setenv("FONDOS_BACKLOG_PG_DSN_AGENT", "postgresql://agent")
    monkeypatch.setenv("FONDOS_AGENT_ACTOR", "agent:x")
    r = GateRunner()
    gate(r)
    env = r.calls[0][1]
    assert not [k for k in env if k.startswith("FONDOS_") and "DSN" in k] and od.OWNER_VAR not in env and od.FACTS_VAR not in env and env["FONDOS_AGENT_ACTOR"] == "agent:x"


def test_a_failed_test_is_a_failure_and_everything_skipped_or_nothing_collected_is_too():
    assert gate(GateRunner(text="1 failed, 33 passed in 9.0s\n", rc=1))[0] == "fail"
    assert gate(GateRunner(text="2 errors in 1.0s\n", rc=1))[0] == "fail"
    assert gate(GateRunner(text="34 skipped in 1.0s\n", rc=0))[0] == "fail"                                   # skipped is not "ran and passed"
    assert gate(GateRunner(text="no tests ran in 0.01s\n", rc=5))[0] == "fail"
    assert gate(GateRunner(text="34 passed in 1.0s\n", rc=1))[0] == "fail"                                    # a non-zero exit code beats a clean count


def test_a_failed_gate_names_the_failing_test():
    kind, detail = gate(GateRunner(text="FAILED tests/test_backlog_gate_pg.py::test_x - boom\n1 failed, 33 passed in 9.0s\n", rc=1))
    assert kind == "fail" and "failed: tests/test_backlog_gate_pg.py::test_x" in detail


def test_infrastructure_trouble_is_cannot_run_never_a_failed_test_and_never_a_pass():
    assert gate(GateRunner(text="[pg-test] 127.0.0.1:5440 is not reachable from the host\n", rc=1))[0] == "cannot_run"     # no pytest summary: the container never got to the tests
    assert gate(GateRunner(boom=subprocess.TimeoutExpired("x", 1)))[0] == "cannot_run"
    assert gate(GateRunner(boom=OSError("docker is not installed")))[0] == "cannot_run"
    assert gate(GateRunner(), checkout=lambda ref, dest, root: (_ for _ in ()).throw(RuntimeError("git clone failed")))[0] == "cannot_run"
    assert gate(GateRunner(), checkout=fake_checkout(files=("tests/test_other.py",)))[0] == "cannot_run"       # no DDL test file in the commit: nothing proves anything


def test_the_gate_judges_with_the_same_rules_as_the_receipts():
    from shared import delivery_receipt as dr
    assert dr.judge("area", "area", {"passed": 0, "failed": 0, "errors": 0, "skipped": 3}, 1, 0, [])[0] == "fail"
    src = (_ROOT / "scripts" / "ops" / "owner_deploy.py").read_text(encoding="utf-8")
    assert "dr.parse_counts(text)" in src and "dr.judge(" in src                                                # P#11: one rule for "tests really ran and passed"


# ── the gate inside apply_pending ───────────────────────────────────────────────

def gated_apply(tmp_path, runner, gate_fn, pending=("relations", "autonomy"), vault=None, diff=""):
    return od.apply_pending(HEAD, list(pending), vault=vault or FakeVault(), state_file=tmp_path / "s.json", run=runner, export=lambda ref, dest: None, python="py",
                            env_file=tmp_path / ".env", receipts_dir=tmp_path / "receipts", now=lambda: NOW, auto=True, gate=gate_fn, ddl_diff=diff)


def test_a_failing_gate_applies_nothing_opens_no_restore_point_and_is_reported_as_a_failure(tmp_path):
    r = AutoRunner()
    out = gated_apply(tmp_path, r, lambda head: ("fail", "3 failed"))
    assert not out.ok and out.stage == "local_gate" and not out.fallback and out.applied == [] and "local gate fail: 3 failed" in out.gate
    assert keys(r) == [] and od.read_state(tmp_path / "s.json")["deployed_ref"] is None                          # not even the restore point: the live backlog was never touched
    assert json.loads(next((tmp_path / "receipts").glob("*.json")).read_text(encoding="utf-8"))["ok"] is False


def test_a_gate_that_cannot_run_applies_nothing_and_asks_a_person(tmp_path):
    r = AutoRunner()
    out = gated_apply(tmp_path, r, lambda head: ("cannot_run", "the container did not start"))
    assert not out.ok and out.stage == "local_gate" and out.fallback and keys(r) == []


def test_a_passing_gate_lets_the_apply_run_and_its_verdict_and_the_change_are_in_the_receipt(tmp_path):
    r = AutoRunner()
    seen = []
    out = gated_apply(tmp_path, r, lambda head: seen.append(head) or ("pass", "34 passed"), diff="+CREATE TABLE backlog.new_thing (id int);")
    assert out.ok and out.gate == "local gate pass: 34 passed" and seen == [HEAD] and keys(r) == ["restore", "relations", "autonomy", "drift"]
    steps = json.loads(next((tmp_path / "receipts").glob("*.json")).read_text(encoding="utf-8"))["steps"]
    assert steps[0] == {"step": "local gate", "result": "local gate pass: 34 passed"} and steps[1]["step"] == "ddl diff" and "CREATE TABLE backlog.new_thing" in steps[1]["tail"]


def test_a_failure_after_a_passing_gate_still_carries_the_verdict_and_rolls_back(tmp_path):
    r = AutoRunner(rcs={"autonomy": 1})
    out = gated_apply(tmp_path, r, lambda head: ("pass", "34 passed"))
    assert not out.ok and out.stage == "autonomy" and out.rolled_back == ["relations"] and out.gate.startswith("local gate pass")


def test_the_gate_is_checked_after_the_vault_so_a_missing_string_does_not_cost_a_ten_minute_test_run(tmp_path):
    called = []
    out = gated_apply(tmp_path, AutoRunner(), lambda head: called.append(1) or ("pass", ""), vault=FakeVault(value=None))
    assert out.stage == "vault" and called == []


def test_a_non_auto_apply_never_runs_the_gate(tmp_path):
    called = []
    out = od.apply_pending(HEAD, ["relations"], vault=FakeVault(), state_file=tmp_path / "s.json", run=AutoRunner(), export=lambda ref, dest: None, python="py",
                           env_file=tmp_path / ".env", receipts_dir=tmp_path / "r", now=lambda: NOW, auto=False, gate=lambda h: called.append(h) or ("fail", "x"))
    assert out.ok and called == []                                                                              # the window apply is a person's decision on a shown diff


def test_both_automatic_paths_the_deploy_and_the_revoke_go_through_the_gate():
    src = (_ROOT / "scripts" / "ops" / "owner_deploy.py").read_text(encoding="utf-8")
    assert "gate=ddl_gate, ddl_diff=shown" in src and "auto=True, remember=False, gate=ddl_gate" in src
    assert "THE AUTOMATIC DEPLOYMENT DID NOT RUN" in src                                                           # a fallback (gate cannot run) is explained in the window
