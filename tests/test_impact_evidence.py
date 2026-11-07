"""FND-0385: the evidence harness (scripts/audit/impact_selection_evidence.py) and the dynamic tracer (shared/testing/impact_trace.py). Pure parts on synthetic data; the tracer on a tiny throwaway
pytest project in a subprocess (a test file's own code, a module it imports, and a python subprocess it starts must all be attributed to it). No docker, no database, no network."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "scripts" / "audit")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import impact_selection_evidence as ev  # noqa: E402
from shared import test_impact as ti  # noqa: E402


def _junit(path, cases):
    body = "".join(f'<testcase classname="{c}" name="{n}" time="{t}">{extra}</testcase>' for c, n, t, extra in cases)
    path.write_text(f'<?xml version="1.0"?><testsuites><testsuite name="pytest">{body}</testsuite></testsuites>', encoding="utf-8")


def test_junit_is_resolved_to_repo_paths_with_the_proyecto2_prefix_and_outcomes(tmp_path):
    _junit(tmp_path / "run0_fondos.xml", [("tests.test_a", "test_x", "0.5", ""), ("tests.test_a.TestK", "test_y", "1.5", "<failure/>"), ("proyecto1.tests.test_b", "test_z", "2", "<skipped/>")])
    _junit(tmp_path / "run1_proyecto2.xml", [("tests.calculations.test_c", "test_w", "3", "<error/>")])
    files = {"tests/test_a.py", "proyecto1/tests/test_b.py", "proyecto2/tests/calculations/test_c.py"}
    got = ev.parse_junit(tmp_path, files)
    assert got == {"tests/test_a.py::test_x": ("passed", 0.5), "tests/test_a.py::test_y": ("failed", 1.5), "proyecto1/tests/test_b.py::test_z": ("skipped", 2.0),
                   "proyecto2/tests/calculations/test_c.py::test_w": ("error", 3.0)}
    assert ev.per_file(got) == {"proyecto1/tests/test_b.py": 2.0, "proyecto2/tests/calculations/test_c.py": 3.0, "tests/test_a.py": 2.0}


def test_the_comparison_reports_only_files_that_ran_and_are_outside_the_static_closure():
    sources = {"shared/a.py": "X = 1\n", "shared/b.py": "from shared import a\n", "shared/hidden.py": "Y = 1\n", "tests/test_t.py": "from shared import b\n", "tests/test_u.py": "def test_u():\n    pass\n",
               "shared/testing/pg_fixtures.py": "Z = 1\n"}
    dynamic = {"tests/test_t.py": {"tests/test_t.py", "shared/b.py", "shared/a.py", "shared/hidden.py", "shared/testing/pg_fixtures.py"},      # hidden.py ran but nothing links it
               "tests/test_u.py": {"tests/test_u.py"}, "(collection)": {"shared/hidden.py"}}                                              # not a test file: ignored
    res = ev.compare_trace(sources, dynamic)
    assert res["tests_checked"] == 2 and res["holes"] == {"tests/test_t.py": ["shared/hidden.py"]}          # infrastructure files never count as holes


def test_the_replay_flags_a_failure_the_selection_would_have_missed_and_nothing_else():
    def dec(selected):
        return ti.Decision("impact", [], {"tests": selected}, len(selected), 10)

    full = ti.Decision("full", ["F3 changed hub: x"], {}, 0, 10)
    receipts = [{"scope": "full", "sha": "a" * 40, "result": "fail", "seconds": 100, "detail": "exit code 1 | failed: tests/test_a.py::t1, tests/test_b.py::t2 | tail"},
                {"scope": "full", "sha": "b" * 40, "result": "fail", "seconds": 50, "detail": "exit code 1 | failed: tests/test_b.py::t2 | tail"},
                {"scope": "full", "sha": "c" * 40, "result": "pass", "seconds": 10, "detail": "ok"}, {"scope": "area", "sha": "d" * 40}]
    table = {"a" * 40: dec(["tests/test_a.py"]), "b" * 40: full, "c" * 40: dec(["tests/test_a.py"])}
    rows = ev.replay_rows(receipts, lambda r: table[r["sha"]])
    assert [r["sha"] for r in rows] == ["aaaaaaaa", "bbbbbbbb", "cccccccc"]
    assert rows[0]["failed_missed"] == ["tests/test_b.py"] and rows[1]["failed_missed"] == [] and rows[1]["decision"] == "full" and rows[2]["selected"] == 1


def test_the_tracer_attributes_code_imports_and_subprocesses_to_the_test_file_and_never_changes_the_verdict(tmp_path):
    proj = tmp_path / "proj"
    (proj / "pkg").mkdir(parents=True)
    (proj / "pkg" / "lib.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (proj / "pkg" / "other.py").write_text("def g():\n    return 2\n", encoding="utf-8")
    (proj / "child_script.py").write_text("import pkg.other\npkg.other.g()\n", encoding="utf-8")
    (proj / "test_one.py").write_text("from pkg import lib\n\ndef test_one():\n    assert lib.f() == 1\n", encoding="utf-8")
    (proj / "test_two.py").write_text("import subprocess, sys\n\ndef test_two():\n    subprocess.run([sys.executable, 'child_script.py'], check=True)\n\ndef test_fails():\n    assert False\n", encoding="utf-8")
    out = tmp_path / "trace"
    env = dict(os.environ, IMPACT_TRACE_DIR=str(out), IMPACT_TRACE_ROOT=str(proj), PYTEST_ADDOPTS="-p shared.testing.impact_trace", PYTHONPATH=str(_ROOT))
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_one.py", "test_two.py"], cwd=proj, env=env, capture_output=True, text=True, timeout=120)
    assert "1 failed, 2 passed" in r.stdout, r.stdout + r.stderr                    # the deliberate failure is still a failure, the others still pass
    traced = ev.load_trace(out)
    assert "pkg/lib.py" in traced["test_one.py"] and "pkg/other.py" not in traced["test_one.py"]
    assert "child_script.py" in traced["test_two.py"] and "pkg/other.py" in traced["test_two.py"]      # the python subprocess it started is followed
    assert "pkg/lib.py" not in traced["test_two.py"]


def test_the_tracer_is_off_without_its_environment(tmp_path):
    (tmp_path / "test_x.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("IMPACT_TRACE")}
    env.update(PYTEST_ADDOPTS="-p shared.testing.impact_trace", PYTHONPATH=str(_ROOT))
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_x.py"], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    assert "1 passed" in r.stdout and not list(tmp_path.glob("**/*.json"))
