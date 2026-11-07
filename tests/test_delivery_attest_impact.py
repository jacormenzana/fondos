"""FND-0385: the impact selection inside scripts/audit/delivery_attest.py, on a throwaway git repository with a fake test runner (no docker, no network, no database).
OFF is exactly the old behaviour; SHADOW runs the whole suite and records what the selector would have run and any escape; ENFORCE runs only the selection, and only when the analysis can bound the change
and a fresh full receipt stands behind it; `verify` recomputes an impact receipt. Plus the suite plan of run_pg_tests.py and the suite_roots filter of available_tests."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "audit"), str(_ROOT / "scripts" / "ops"), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import delivery_attest as da  # noqa: E402
import run_pg_tests as rpt  # noqa: E402
from shared import delivery_receipt as dr  # noqa: E402
from shared import test_impact as ti  # noqa: E402
from test_delivery_receipt import OK_TEXT, Runner, commit, git  # noqa: E402

BASE_FILES = {"README.md": "x\n", "shared/lib_a.py": "A = 1\n", "shared/lib_b.py": "B = 1\n",
              "tests/test_a.py": "from shared import lib_a\n\ndef test_a():\n    pass\n", "tests/test_b.py": "from shared import lib_b\n\ndef test_b():\n    pass\n",
              **{f"tests/test_n{i}.py": "def test_x():\n    pass\n" for i in range(10)}}


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "r"
    r.mkdir()
    git(r, "init", "-q", "-b", "master")
    base = commit(r, BASE_FILES, "base")
    git(r, "update-ref", "refs/remotes/origin/master", base)
    return r


def base_of(repo):
    return git(repo, "rev-parse", "refs/remotes/origin/master")


def receipts_file(repo):
    return repo / "out" / "receipts.jsonl"


def put_full_anchor(repo, sha):
    rec = dr.build_receipt(sha=sha, tree=git(repo, "rev-parse", f"{sha}^{{tree}}"), base=sha, covers=[sha], changed=[], required="full", run_scope="full", files=[],
                           counts={"passed": 5, "failed": 0, "errors": 0, "skipped": 0}, rc=0, seconds=1.0, python="py", command=[], result="pass", detail="ok", ts="2026-11-07T00:00:00+00:00")
    receipts_file(repo).parent.mkdir(parents=True, exist_ok=True)
    with open(receipts_file(repo), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")


def run(repo, runner, scope=None, mode="OFF", impact=None):
    return da.attest(repo, "HEAD", scope, runner=runner, receipts_path=receipts_file(repo), impact_mode=mode, impact=impact)


def failing(*ids):
    return "\n".join(f"FAILED {i} - assert False" for i in ids) + f"\n{len(ids)} failed, 2 passed in 0.10s\n"


# ── OFF is the old behaviour ─────────────────────────────────────────────────────

def test_off_is_byte_for_byte_the_old_receipt(repo):
    commit(repo, {"shared/lib_a.py": "A = 2\n"})
    runner = Runner()
    rec = run(repo, runner)
    assert rec["scope"] == "full" and rec["required"] == "full" and rec["v"] == 1 and "selection" not in rec
    assert runner.calls[0][0] == "full"
    assert dr.digest_of(rec) == rec["digest"]


# ── SHADOW: the full suite runs, the selection is recorded ───────────────────────

def test_shadow_runs_full_and_records_what_the_selector_would_have_run(repo):
    commit(repo, {"shared/lib_a.py": "A = 2\n"})
    runner = Runner()
    rec = run(repo, runner, mode="SHADOW")
    assert runner.calls[0][0] == "full" and rec["scope"] == "full" and rec["result"] == "pass" and rec["v"] == 2
    sel = rec["selection"]
    assert sel["mode"] == "SHADOW" and sel["algo"] == ti.ALGO_VERSION and sel["escapes"] == [] and sel["decision"]["scope"] == "impact"
    assert sel["decision"]["selected"] == {"tests": ["tests/test_a.py"]}
    assert dr.digest_of(rec) == rec["digest"]                                 # the selection is under the digest: it cannot be edited afterwards


def test_shadow_records_an_escape_when_a_failing_test_was_outside_the_selection(repo):
    commit(repo, {"shared/lib_a.py": "A = 2\n"})
    rec = run(repo, Runner(text=failing("tests/test_b.py::test_b"), rc=1), mode="SHADOW")
    assert rec["result"] == "fail" and rec["selection"]["escapes"] == ["tests/test_b.py"] and "ESCAPE" in rec["detail"]
    mode = ti.effective_mode([rec], ti.ALGO_VERSION)
    assert mode == "SHADOW"


def test_a_failure_inside_the_selection_is_not_an_escape(repo):
    commit(repo, {"shared/lib_a.py": "A = 2\n"})
    rec = run(repo, Runner(text=failing("tests/test_a.py::test_a"), rc=1), mode="SHADOW")
    assert rec["result"] == "fail" and rec["selection"]["escapes"] == []


def test_a_full_decision_is_recorded_with_its_reason_and_cannot_escape(repo):
    commit(repo, {"shared/testing/pg_fixtures.py": "x = 1\n"})
    rec = run(repo, Runner(text=failing("tests/test_b.py::test_b"), rc=1), mode="SHADOW")
    assert rec["selection"]["decision"]["scope"] == "full" and rec["selection"]["decision"]["reasons"][0].startswith("F2") and rec["selection"]["escapes"] == []


def test_an_analysis_that_raises_is_recorded_and_the_run_stays_full(repo):
    commit(repo, {"shared/lib_a.py": "A = 2\n"})

    def boom(chg, tree, receipts):
        raise RuntimeError("graph exploded")
    runner = Runner()
    rec = run(repo, runner, mode="ENFORCE", impact=boom)
    assert runner.calls[0][0] == "full" and rec["scope"] == "full" and "graph exploded" in rec["selection"]["error"] and "decision" not in rec["selection"]


# ── ENFORCE: only the selection runs, and only when it may ───────────────────────

def test_enforce_runs_only_the_selection_with_a_fresh_full_receipt_behind_it(repo):
    put_full_anchor(repo, base_of(repo))
    sha = commit(repo, {"shared/lib_a.py": "A = 2\n"})
    runner = Runner()
    rec = run(repo, runner, mode="ENFORCE")
    assert runner.calls[0][0] == "impact" and runner.calls[0][1] == ["tests/test_a.py"]
    assert rec["scope"] == "impact" and rec["required"] == "impact" and rec["result"] == "pass" and rec["files"] == ["tests/test_a.py"]
    assert da.verify(repo, sha, receipts_file(repo), recompute=lambda root, r: da.recompute_impact(root, r))[0] is True


def test_enforce_without_a_full_receipt_on_the_ancestry_stays_full_and_says_why(repo):
    commit(repo, {"shared/lib_a.py": "A = 2\n"})
    runner = Runner()
    rec = run(repo, runner, mode="ENFORCE")
    assert runner.calls[0][0] == "full" and rec["selection"]["freshness"].startswith("F7")


def test_enforce_with_a_full_receipt_that_is_too_far_back_stays_full(repo):
    put_full_anchor(repo, base_of(repo))
    for i in range(6):                                                         # more code-changing commits than the limit of 5
        commit(repo, {"shared/lib_b.py": f"B = {i + 2}\n"}, f"c{i}")
    commit(repo, {"shared/lib_a.py": "A = 2\n"})
    runner = Runner()
    rec = run(repo, runner, mode="ENFORCE")
    assert runner.calls[0][0] == "full" and "code-changing commits" in rec["selection"]["freshness"]


def test_enforce_still_runs_full_for_infrastructure_and_hub_changes(repo):
    put_full_anchor(repo, base_of(repo))
    commit(repo, {"shared/testing/pg_fixtures.py": "x = 1\n"})
    runner = Runner()
    run(repo, runner, mode="ENFORCE")
    assert runner.calls[0][0] == "full"


def test_an_explicit_impact_scope_is_refused_unless_the_selector_has_earned_it(repo):
    commit(repo, {"shared/lib_a.py": "A = 2\n"})
    runner = Runner()
    rec = run(repo, runner, scope="impact", mode="SHADOW")
    assert rec["result"] == "insufficient_scope" and runner.calls == []       # nothing was run: a weaker scope than the change needs
    assert da.verify(repo, rec["sha"], receipts_file(repo))[0] is False


def test_verify_refuses_an_impact_receipt_the_selector_no_longer_supports(repo):
    put_full_anchor(repo, base_of(repo))
    sha = commit(repo, {"shared/lib_a.py": "A = 2\n"})
    run(repo, Runner(), mode="ENFORCE")
    full = ti.Decision("full", ["F3 changed hub: ['shared/lib_a.py (9/12)']"], {}, 0, 12)
    ok, text = da.verify(repo, sha, receipts_file(repo), recompute=lambda root, r: full)
    assert ok is False and "requires a full receipt" in text
    short = ti.Decision("impact", [], {"tests": ["tests/test_a.py", "tests/test_b.py"]}, 2, 12)
    ok, text = da.verify(repo, sha, receipts_file(repo), recompute=lambda root, r: short)
    assert ok is False and "did not run" in text and "tests/test_b.py" in text

    def cannot(root, r):
        raise OSError("disk gone")
    assert da.verify(repo, sha, receipts_file(repo), recompute=cannot)[0] is None          # unverifiable, never a pass


def test_a_receipt_still_verifies_after_the_file_is_reread_and_a_tampered_impact_receipt_does_not(repo):
    put_full_anchor(repo, base_of(repo))
    sha = commit(repo, {"shared/lib_a.py": "A = 2\n"})
    run(repo, Runner(), mode="ENFORCE")
    lines = receipts_file(repo).read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[-1])
    tampered["required"] = "area"
    receipts_file(repo).write_text("\n".join(lines[:-1] + [json.dumps(tampered)]) + "\n", encoding="utf-8")
    assert da.verify(repo, sha, receipts_file(repo))[0] is None                # digest mismatch


# ── the pieces around it ─────────────────────────────────────────────────────────

def test_available_tests_are_only_those_the_full_run_collects(tmp_path):
    for rel in ("tests/test_a.py", "proyecto1/tests/test_b.py", "proyecto2/tests/calculations/test_c.py", "upload/code/test_d_upload.py", "proyecto1/core/test_cost_router.py",
                "proyecto2/tests/discovery/test_historia.py", "scripts/test_odd.py"):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("", encoding="utf-8")
    assert da.available_tests(tmp_path) == ["proyecto1/tests/test_b.py", "proyecto2/tests/calculations/test_c.py", "tests/test_a.py"]


def test_run_tests_for_impact_hands_run_pg_tests_one_suite_plan_and_removes_it(tmp_path, monkeypatch):
    seen = {}

    class R:
        returncode, stdout, stderr = 0, "3 passed in 1s\n", ""

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        seen["plan"] = json.loads(Path(cmd[cmd.index("--suite-files") + 1]).read_text(encoding="utf-8"))
        seen["file"] = Path(cmd[cmd.index("--suite-files") + 1])
        seen["timeout"] = kw["timeout"]
        return R()
    monkeypatch.setattr(da.subprocess, "run", fake_run)
    rc, text, _s, cmds = da.run_tests("impact", ["tests/test_a.py", "tests/test_x.py::test_one", "proyecto2/tests/calculations/test_c.py", "proyecto1/tests/test_b.py"], tmp_path, python="py")
    assert rc == 0 and "scripts/ops/run_pg_tests.py" in cmds[0] and "--suite-files" in cmds[0]
    assert seen["plan"] == {"tests": ["tests/test_a.py", "tests/test_x.py::test_one"], "proyecto2": ["proyecto2/tests/calculations/test_c.py"], "proyecto1": ["proyecto1/tests/test_b.py"]}
    assert not seen["file"].exists() and seen["timeout"] == da.TIMEOUT_S["impact"]


def test_the_suite_plan_keeps_the_four_runs_and_their_order_and_the_default_is_unchanged(tmp_path):
    assert rpt.default_runs(tmp_path) == [(tmp_path, ["proyecto1/tests"]), (tmp_path, ["proyecto3/tests"]), (tmp_path, ["tests"]),
                                          (tmp_path / "proyecto2", ["tests", "--ignore=tests/discovery/test_historia.py"])]
    plan = {"proyecto2": ["proyecto2/tests/calculations/test_c.py::test_x"], "tests": ["tests/test_a.py"], "proyecto1": ["proyecto1/tests/test_b.py"]}
    assert rpt.suite_runs(plan, tmp_path) == [(tmp_path, ["proyecto1/tests/test_b.py"]), (tmp_path, ["tests/test_a.py"]), (tmp_path / "proyecto2", ["tests/calculations/test_c.py::test_x"])]
    assert rpt.suite_runs({"tests": []}, tmp_path) == []
    with pytest.raises(ValueError):
        rpt.suite_runs({"tess": ["tests/test_a.py"]}, tmp_path)                 # a typo must never silently run less
