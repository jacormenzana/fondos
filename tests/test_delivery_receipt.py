# tests/test_delivery_receipt.py
# -*- coding: utf-8 -*-
"""FND-0375: the local test receipt. The pure rules of shared/delivery_receipt.py (which scope a change needs, which tests it runs, how a run is read and judged, the verdict of `tests_pass`), and
scripts/audit/delivery_attest.py on a throwaway git repository with a fake test runner (the owner's three conditions: the receipt names the exact commit, a pass means tests really ran and passed,
every receipt is verifiable). R-7: no database, no network, no pipeline imports."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "audit")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import delivery_attest as da  # noqa: E402
from shared import delivery_receipt as dr  # noqa: E402

SHA = "a" * 40
OK_TEXT = "..\n2 passed in 0.10s\n"


# ── pure rules ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("changed,expected", [
    ([], "docs_only"),
    (["doc/reglas/NORMAS_BACKLOG.md", "AGENTS.md", "out/audit/x.json", "deployments/r.md"], "docs_only"),
    (["scripts/audit/delivery_attest.py"], "area"),
    (["tests/test_delivery_receipt.py"], "area"),
    (["proyecto2/tests/calculations/test_drawdown.py"], "area"),             # a test under a runtime tree does not need the full suite
    (["shared/delivery_receipt.py"], "full"),
    (["proyecto3/src/fund_scorer.py", "doc/x.md"], "full"),
    (["scripts/ops/x.sh"], "area"),
])
def test_required_scope(changed, expected):
    assert dr.required_scope(changed) == expected


def test_scopes_are_ordered():
    assert dr.rank("docs_only") < dr.rank("area") < dr.rank("full")
    assert dr.rank("nonsense") < dr.rank("docs_only")


def test_select_tests_maps_modules_and_runs_changed_tests():
    avail = ["tests/test_backlog_autopilot.py", "tests/test_backlog_autopilot_pg.py", "tests/test_backlog_ci.py", "tests/test_other.py"]
    sel, unc = dr.select_tests(["shared/backlog_autopilot.py", "tests/test_other.py", "doc/x.md"], avail)
    assert sel == ["tests/test_backlog_autopilot.py", "tests/test_backlog_autopilot_pg.py", "tests/test_other.py"]
    assert unc == []


def test_select_tests_covers_shell_and_batch_scripts_by_name_with_hyphens_as_underscores():
    avail = ["tests/test_post_commit.py", "tests/test_launcher_x.py", "tests/test_a.py"]
    assert dr.select_tests(["scripts/audit/post-commit.sh"], avail) == (["tests/test_post_commit.py"], [])
    assert dr.select_tests(["scripts/launch/launcher_x.bat"], avail) == (["tests/test_launcher_x.py"], [])
    assert dr.select_tests(["db/pg/x.sql", "scripts/audit/pre-commit.sh"], avail) == ([], ["db/pg/x.sql", "scripts/audit/pre-commit.sh"])


def test_select_tests_reports_code_no_test_covers():
    sel, unc = dr.select_tests(["scripts/ops/new_tool.py", "scripts/audit/pre-commit.sh", "db/pg/x.sql", "conftest.py"], ["tests/test_a.py"])
    assert sel == []
    assert unc == ["conftest.py", "db/pg/x.sql", "scripts/audit/pre-commit.sh", "scripts/ops/new_tool.py"]


def test_a_deleted_test_is_nothing_to_run_and_nothing_uncovered():
    assert dr.select_tests(["tests/test_gone.py"], ["tests/test_a.py"]) == ([], [])


def test_parse_counts_reads_the_pytest_summary_lines_and_sums_runs():
    counts, n = dr.parse_counts("noise\n= 968 passed, 1 failed, 3 skipped in 600.23s (0:10:00) =\n3 passed in 0.5s\n")
    assert counts == {"passed": 971, "failed": 1, "errors": 0, "skipped": 3} and n == 2
    counts, n = dr.parse_counts("2 failed, 1 error in 3.2s")
    assert counts["failed"] == 2 and counts["errors"] == 1 and n == 1


def test_no_summary_is_not_a_summary():
    assert dr.parse_counts("Traceback (most recent call last):\n  boom\n") == ({"passed": 0, "failed": 0, "errors": 0, "skipped": 0}, 0)
    assert dr.parse_counts("")[1] == 0
    assert dr.parse_counts("no tests ran in 0.01s")[1] == 1


def counts(passed=2, failed=0, errors=0, skipped=0):
    return {"passed": passed, "failed": failed, "errors": errors, "skipped": skipped}


def test_judge_passes_only_when_tests_really_ran_and_passed():
    assert dr.judge("area", "area", counts(), 1, 0, [])[0] == "pass"
    assert dr.judge("full", "area", counts(), 1, 0, [])[0] == "pass"                       # a stronger scope vouches for a smaller change
    assert dr.judge("area", "area", counts(passed=0, skipped=5), 1, 0, [])[0] == "fail"     # everything skipped is not a pass
    assert dr.judge("area", "area", counts(failed=1), 1, 1, [])[0] == "fail"
    assert dr.judge("area", "area", counts(errors=1), 1, 1, [])[0] == "fail"
    assert dr.judge("area", "area", counts(), 1, 5, [])[0] == "fail"                        # a non-zero exit code beats a clean count
    assert dr.judge("area", "area", counts(passed=0), 1, 5, [])[0] == "fail"                # pytest exit code 5: nothing collected


def test_judge_infrastructure_is_inconclusive_never_a_failed_test():
    assert dr.judge("full", "full", counts(passed=0), 0, 1, [])[0] == "inconclusive"
    assert dr.judge("area", "area", counts(passed=0), 0, 99, [])[0] == "inconclusive"


def test_judge_scope_rules():
    assert dr.judge("area", "full", counts(), 1, 0, [])[0] == "insufficient_scope"
    assert dr.judge("docs_only", "area", counts(passed=0), 0, 0, [])[0] == "insufficient_scope"
    assert dr.judge("docs_only", "docs_only", counts(passed=0), 0, 0, [])[0] == "pass"
    assert dr.judge("area", "area", counts(), 1, 0, ["scripts/x.sh"])[0] == "no_tests_cover"


def receipt(sha=SHA, tree="t" * 40, covers=None, required="area", scope="area", result="pass", **kw):
    c = counts() if result == "pass" else counts(passed=0)
    return dr.build_receipt(sha=sha, tree=tree, base="b" * 40, covers=covers or [sha], changed=["scripts/x.py"], required=required, run_scope=scope, files=["tests/test_x.py"],
                            counts=kw.get("counts", c), rc=0 if result == "pass" else 1, seconds=1.5, python="py", command=["pytest"], result=result, detail=kw.get("detail", "d"),
                            ts="2026-11-02T20:00:00+00:00")


def tree_ok(sha):
    return "t" * 40


def test_evaluate_true_only_on_an_intact_passing_receipt_that_names_the_full_sha():
    ok, text = dr.evaluate(SHA, [receipt()], tree_ok)
    assert ok is True and SHA[:12] in text and "2 passed" in text
    assert dr.evaluate("a" * 12, [receipt()], tree_ok)[0] is None                           # a short sha is never accepted
    assert dr.evaluate("main", [receipt()], tree_ok)[0] is None
    assert dr.evaluate("", [receipt()], tree_ok)[0] is None


def test_evaluate_a_receipt_covers_the_earlier_commits_of_its_range():
    first, head = "b" * 40, "c" * 40
    r = receipt(sha=head, covers=[first, head])
    assert dr.evaluate(first, [r], lambda s: "t" * 40)[0] is True
    assert dr.evaluate("d" * 40, [r], lambda s: "t" * 40)[0] is False


def test_evaluate_without_a_receipt_is_false_not_a_pass():
    ok, text = dr.evaluate(SHA, [], tree_ok)
    assert ok is False and "no local test receipt" in text


def test_evaluate_refuses_a_tampered_receipt_and_a_changed_tree():
    r = receipt()
    r["counts"]["passed"] = 999                                                          # edited after the fact
    assert dr.evaluate(SHA, [r], tree_ok)[0] is None
    assert dr.evaluate(SHA, [receipt()], lambda s: "z" * 40)[0] is None                    # git no longer gives the recorded tree (history rewritten)
    assert dr.evaluate(SHA, [receipt()], lambda s: None)[0] is None


def test_evaluate_failed_inconclusive_and_too_weak():
    assert dr.evaluate(SHA, [receipt(result="fail")], tree_ok)[0] is False
    assert dr.evaluate(SHA, [receipt(result="inconclusive")], tree_ok)[0] is None
    assert dr.evaluate(SHA, [receipt(result="no_tests_cover")], tree_ok)[0] is False
    weak = receipt(required="full", scope="area")                                         # a receipt that vouches below its own need
    assert dr.evaluate(SHA, [weak], tree_ok)[0] is False


def test_evaluate_a_later_failure_does_not_veto_an_earlier_pass_of_the_same_commit():
    assert dr.evaluate(SHA, [receipt(), receipt(result="fail")], tree_ok)[0] is True


def test_digest_changes_with_any_field_and_ignores_itself():
    r = receipt()
    assert r["digest"] == dr.digest_of(r)
    assert dr.digest_of({**r, "detail": "other"}) != r["digest"]
    assert dr.digest_of({k: v for k, v in r.items() if k != "digest"}) == r["digest"]


# ── the command, on a throwaway repository ──────────────────────────────────────

def git(repo, *args):
    r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, capture_output=True, text=True, check=True)
    return r.stdout.strip()


def commit(repo, files: dict, msg="c"):
    for name, body in files.items():
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "r"
    r.mkdir()
    git(r, "init", "-q", "-b", "master")
    base = commit(r, {"README.md": "x\n", "tests/test_tool.py": "def test_a():\n    pass\n", "scripts/tool.py": "x = 1\n"}, "base")
    git(r, "update-ref", "refs/remotes/origin/master", base)
    return r


class Runner:
    def __init__(self, text=OK_TEXT, rc=0, boom=False):
        self.text, self.rc, self.boom, self.calls = text, rc, boom, []

    def __call__(self, scope, selected, export_dir):
        self.calls.append((scope, list(selected), sorted(p.name for p in Path(export_dir).iterdir())))
        self.checked_out = git(export_dir, "rev-parse", "HEAD")                            # the directory is a real repository at the commit being vouched for
        self.tracked = git(export_dir, "ls-files").splitlines()
        if self.boom:
            raise OSError("container did not start")
        return self.rc, self.text, 0.2, [["pytest", *selected]]


def run(repo, runner, scope=None, head="HEAD"):
    return da.attest(repo, head, scope, runner=runner, receipts_path=repo / "out" / "receipts.jsonl")


def test_a_code_change_runs_its_area_tests_and_leaves_a_verifiable_receipt(repo):
    sha = commit(repo, {"scripts/tool.py": "x = 2\n"})
    runner = Runner()
    rec = run(repo, runner)
    assert rec["result"] == "pass" and rec["scope"] == "area" and rec["required"] == "area"
    assert rec["sha"] == sha and len(rec["sha"]) == 40 and rec["tree"] == git(repo, "rev-parse", f"{sha}^{{tree}}")
    assert runner.calls[0][0:2] == ("area", ["tests/test_tool.py"])
    assert da.verify(repo, sha, repo / "out" / "receipts.jsonl")[0] is True
    assert da.verify(repo, sha[:12], repo / "out" / "receipts.jsonl")[0] is None


def test_the_tests_run_on_a_clean_clone_at_the_exact_commit_never_on_the_working_tree(repo):
    sha = commit(repo, {"scripts/tool.py": "x = 2\n"})
    (repo / "dirty_untracked.txt").write_text("another session's edit", encoding="utf-8")
    (repo / "scripts" / "tool.py").write_text("x = 999  # uncommitted\n", encoding="utf-8")
    runner = Runner()
    run(repo, runner)
    assert "dirty_untracked.txt" not in runner.calls[0][2] and "dirty_untracked.txt" not in runner.tracked
    assert runner.checked_out == sha and "scripts/tool.py" in runner.tracked                  # a real git repository (tests that call git need one), at the very commit
    assert runner.calls[0][2].count(".git") == 1


def test_a_receipt_covers_every_commit_not_yet_on_origin(repo):
    a = commit(repo, {"scripts/tool.py": "x = 2\n"}, "a")
    b = commit(repo, {"scripts/tool.py": "x = 3\n"}, "b")
    rec = run(repo, Runner())
    assert rec["covers"] == [a, b] and rec["sha"] == b
    assert da.verify(repo, a, repo / "out" / "receipts.jsonl")[0] is True


def test_a_documents_only_change_needs_no_run(repo):
    commit(repo, {"README.md": "changed\n", "doc/x.md": "new\n"})
    runner = Runner()
    rec = run(repo, runner)
    assert rec["result"] == "pass" and rec["scope"] == "docs_only" and runner.calls == []


def test_runtime_code_needs_the_full_suite(repo):
    commit(repo, {"shared/mod.py": "y = 1\n"})
    rec = run(repo, Runner())
    assert rec["required"] == "full" and rec["scope"] == "full" and rec["result"] == "pass"
    weak = run(repo, Runner(), scope="area")
    assert weak["result"] == "insufficient_scope"
    assert da.verify(repo, rec["sha"], repo / "out" / "receipts.jsonl")[0] is True       # the full receipt is still the newest passing one for the commit


def test_code_no_test_covers_is_not_a_pass_and_nothing_runs(repo):
    commit(repo, {"scripts/brand_new.py": "z = 1\n"})
    runner = Runner()
    rec = run(repo, runner)
    assert rec["result"] == "no_tests_cover" and runner.calls == []
    assert run(repo, Runner(), scope="full")["result"] == "pass"                          # a full run vouches for anything


def test_failed_tests_are_named_in_order_without_repeats_and_capped():
    text = "FAILED tests/a.py::test_x - assert 1 == 2\nERROR tests/b.py::test_y\nFAILED tests/a.py::test_x - again\nnoise FAILED not at the start\n"
    assert dr.failed_tests(text) == ["tests/a.py::test_x", "tests/b.py::test_y"]
    assert len(dr.failed_tests("\n".join(f"FAILED tests/t.py::test_{i}" for i in range(20)))) == 5
    assert dr.failed_tests("") == [] and dr.failed_tests("12 passed in 1s") == []


def test_a_failed_receipt_names_the_failing_test_and_keeps_the_output(repo):
    commit(repo, {"scripts/tool.py": "x = 2\n"})
    out = "..F\nFAILED tests/test_tool.py::test_a - assert 1 == 2\n1 failed, 2 passed in 0.1s\n"
    rec = run(repo, Runner(text=out, rc=1))
    assert rec["result"] == "fail" and "failed: tests/test_tool.py::test_a" in rec["detail"]
    assert "tests/test_tool.py::test_a" in (repo / "out" / "delivery_attest_last_failure.log").read_text(encoding="utf-8")


def test_a_failing_run_is_a_failure(repo):
    commit(repo, {"scripts/tool.py": "x = 2\n"})
    rec = run(repo, Runner(text="1 failed, 1 passed in 0.1s\n", rc=1))
    assert rec["result"] == "fail" and rec["counts"]["failed"] == 1
    assert da.verify(repo, rec["sha"], repo / "out" / "receipts.jsonl")[0] is False


def test_infrastructure_failure_is_inconclusive_retried_once_and_never_a_failed_test(repo):
    commit(repo, {"scripts/tool.py": "x = 2\n"})
    runner = Runner(boom=True)
    rec = run(repo, runner)
    assert rec["result"] == "inconclusive" and len(runner.calls) == 2 and rec["counts"]["failed"] == 0
    assert da.verify(repo, rec["sha"], repo / "out" / "receipts.jsonl")[0] is None


def test_a_flaky_start_is_retried_and_then_passes(repo):
    commit(repo, {"scripts/tool.py": "x = 2\n"})
    outputs = iter([(1, "Cannot connect to the Docker daemon", 0.1, []), (0, OK_TEXT, 0.2, [["pytest"]])])
    rec = run(repo, lambda s, sel, d: next(outputs))
    assert rec["result"] == "pass"


def test_receipt_lines_are_appended_and_a_torn_line_is_skipped(repo):
    commit(repo, {"scripts/tool.py": "x = 2\n"})
    path = repo / "out" / "receipts.jsonl"
    run(repo, Runner())
    with open(path, "a", encoding="utf-8") as f:
        f.write('{"torn": \n')
    run(repo, Runner())
    lines = da.read_receipts(path)
    assert len(lines) == 2 and all(json.dumps(x) for x in lines)


def test_a_root_commit_is_covered_by_itself(tmp_path):
    r = tmp_path / "root"
    r.mkdir()
    git(r, "init", "-q", "-b", "master")
    sha = commit(r, {"README.md": "x\n"})
    chg = da.collect_change(r)
    assert chg["covers"] == [sha] and chg["changed"] == ["README.md"]


def test_one_run_at_a_time_and_a_stale_lock_is_taken_over(tmp_path):
    lock = tmp_path / "x.lock"
    assert da.acquire(lock) is True
    assert da.acquire(lock) is False
    da.release(lock)
    assert da.acquire(lock) is True
    assert da.acquire(lock, stale_s=-1) is True                                          # older than "stale": belongs to a dead process
    da.release(lock)


# ── FND-0380: the whole-tree policy tests run in EVERY area receipt ─────────────

def test_with_policy_adds_only_the_policy_tests_the_tree_has_and_keeps_the_selection():
    avail = ["tests/test_log_paths.py", "tests/test_backlog_overlap.py", "tests/test_tool.py", "tests/test_other.py"]
    got = dr.with_policy(["tests/test_tool.py"], avail)
    assert got == sorted({"tests/test_tool.py", "tests/test_log_paths.py", "tests/test_backlog_overlap.py::test_every_incident_reporter_has_a_fixture_case"})
    assert "tests/test_other.py" not in got                                           # not a policy test: only selected when a module is changed
    assert dr.with_policy([], []) == [] and dr.with_policy(["tests/test_tool.py"], ["tests/test_tool.py"]) == ["tests/test_tool.py"]
    assert dr.with_policy(["tests/test_log_paths.py"], avail).count("tests/test_log_paths.py") == 1       # no repeats


def test_every_policy_test_exists_so_the_set_cannot_rot():
    root = Path(__file__).resolve().parents[1]
    assert len(set(dr.POLICY_TESTS)) == len(dr.POLICY_TESTS)
    for entry in dr.POLICY_TESTS:
        path, _, node = entry.partition("::")
        f = root / path
        assert f.is_file(), f"stale policy entry {entry}: the file is gone"
        if node:
            assert f"def {node}(" in f.read_text(encoding="utf-8"), f"stale policy entry {entry}: no such test"


def test_an_area_run_hands_the_policy_tests_to_the_runner_and_the_receipt_records_them(repo):
    commit(repo, {"tests/test_log_paths.py": "def test_p():\n    pass\n", "tests/test_sql_explain_sweep_pg.py": "def test_q():\n    pass\n"})
    sha = commit(repo, {"scripts/tool.py": "x = 2\n"})
    runner = Runner()
    rec = run(repo, runner)
    scope, selected, _ = runner.calls[0]
    assert scope == "area" and selected == ["tests/test_log_paths.py", "tests/test_sql_explain_sweep_pg.py", "tests/test_tool.py"]
    assert rec["files"] == selected and rec["result"] == "pass" and da.verify(repo, sha, repo / "out" / "receipts.jsonl")[0] is True


def test_the_policy_set_does_not_turn_uncovered_code_into_a_pass(repo):
    commit(repo, {"tests/test_log_paths.py": "def test_p():\n    pass\n"})
    commit(repo, {"scripts/brand_new.py": "z = 1\n"})                                  # no test is named after it
    runner = Runner()
    rec = run(repo, runner)
    assert rec["result"] == "no_tests_cover" and runner.calls == []


def test_the_postgres_policy_test_goes_to_the_postgres_runner_and_the_rest_to_plain_pytest():
    plain = [t for t in dr.POLICY_TESTS if "_pg" not in t.rsplit("/", 1)[-1]]
    pg = [t for t in dr.POLICY_TESTS if "_pg" in t.rsplit("/", 1)[-1]]
    assert sorted(p.split("::")[0] for p in pg) == ["tests/test_backlog_gate_override_pg.py", "tests/test_sql_explain_sweep_pg.py"] and len(plain) == len(dr.POLICY_TESTS) - 2


# The detector: a NEW test that scans the repository tree for a convention must join POLICY_TESTS (or be classified here), so the set is not a list somebody has to remember (Directive D-0).
NOT_POLICY = {
    "tests/test_batch_cycle_audits_e2e.py": "DESIGN: end to end on a stub tree; its globs read the logs and audit files the launchers it runs produced, not the repository sources.",
    "tests/test_batch_p3_post_draft_audit_e2e.py": "DESIGN: end to end on a stub tree; its globs read the logs the launcher it runs produced, not the repository sources.",
    "tests/test_launcher_state_guard.py": "DESIGN: end to end refusal paths of P1P2_runCycle.bat; its globs compare the log directory before and after a run, not the repository sources.",
    "tests/test_github_protect_master.py": "DESIGN: reads .github/workflows; GitHub is out of the loop (AGENTS.md, since 2026-11-02) and its checks come back with it, not with a code change.",
    "tests/test_test_impact.py": "DESIGN: self-test of the impact selector (FND-0385); it reads the tree to prove the selector's own claims (the 336daff8 regression, each NARROW_LOADERS entry), not a convention of the code. A change to the selector is a full receipt (F2).",
}


def _scans_the_repository(path) -> bool:
    """True when the test file walks (rglob / walk / glob / iglob) something named after a repository root (shared, scripts, proyecto1..3, ROOT / REPO). Static, no import."""
    import ast
    import re as _re
    roots = ("shared", "scripts", "proyecto1", "proyecto2", "proyecto3")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
        if name not in ("rglob", "walk", "glob", "iglob"):
            continue
        recv = ast.unparse(f.value) if isinstance(f, ast.Attribute) else ""
        text = ast.unparse(n)
        if any(f'"{r}"' in text or f"'{r}'" in text for r in roots) or _re.search(r"\b(ROOT|REPO|_ROOT)\b", recv):
            return True
    return False


def test_every_test_that_scans_the_repository_is_in_the_policy_set_or_classified_with_a_reason():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _allowlist import bad_reasons
    root = Path(__file__).resolve().parents[1]
    in_set = {e.split("::")[0] for e in dr.POLICY_TESTS}
    scanners = {p.relative_to(root).as_posix() for p in sorted((root / "tests").glob("test_*.py")) if _scans_the_repository(p)}
    unclassified = sorted(scanners - in_set - set(NOT_POLICY))
    assert not unclassified, ("a test that scans the repository tree must run in every area receipt: add it to POLICY_TESTS (shared/delivery_receipt.py, one `file::test` entry when "
                              f"the file also holds module tests) or to NOT_POLICY with a reason: {unclassified}")
    assert not bad_reasons(NOT_POLICY), bad_reasons(NOT_POLICY)
    stale = sorted(k for k in NOT_POLICY if k not in scanners)
    assert not stale, f"NOT_POLICY entries that no longer scan the repository (remove them): {stale}"
