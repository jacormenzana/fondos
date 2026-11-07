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
    dynamic["tests/test_t.py"].add("config.py")                                                              # a file a test creates at run time is in no commit: never a hole
    assert ev.compare_trace(sources, dynamic)["holes"] == {"tests/test_t.py": ["shared/hidden.py"]}
    sources["tests/test_t.py"] += "import importlib\n\ndef test_t(n):\n    importlib.import_module(n)\n"      # it now loads by data: it runs in every impact receipt, so the graph need not cover it
    res = ev.compare_trace(sources, dynamic)
    assert res["holes"] == {} and res["tests_checked"] == 1 and res["always_run_exempt"] == 1


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


def test_a_raise_mutant_breaks_the_first_real_statement_and_keeps_the_docstring():
    src = 'def _private():\n    return 0\n\n\ndef public(x):\n    """doc"""\n    y = x + 1\n    return y\n'
    out, name = ev.raise_mutant(src)
    assert name == "public" and out.index('raise RuntimeError("IMPACT_MUTANT")') > out.index('"""doc"""') and out.index("y = x + 1") > out.index("IMPACT_MUTANT")
    compile(out, "m.py", "exec")
    assert ev.raise_mutant("def only_docstring():\n    '''doc'''\n") is None and ev.raise_mutant("def one(): return 1\n") is None and ev.raise_mutant("def broken(:\n") is None


def test_the_catalogue_is_deterministic_never_mutates_a_hub_and_keeps_mutants_in_a_batch_apart():
    files = {f"proyecto1/core/m{i}.py": f"def f{i}(x):\n    y = x\n    return y\n" for i in range(6)}
    files["shared/hub.py"] = "def h(x):\n    y = x\n    return y\n"
    files.update({f"tests/test_m{i}.py": f"from proyecto1.core import m{i}\n" for i in range(6)})
    files.update({f"tests/test_hub{i}.py": "from shared import hub\n" for i in range(30)})
    g = ti.build_graph(files)
    a, b = ev.gen_mutants(files, g, seed=1, per_area=3), ev.gen_mutants(files, g, seed=1, per_area=3)
    assert a == b and all("shared/hub.py" not in m["files"] for m in a)                          # same seed, same catalogue; the hub reaches over half the suite
    raises = [m for m in a if m["kind"] == "raise"]
    assert len(raises) == 3 and all(next(iter(m["files"])).startswith("proyecto1/core/") for m in raises)
    assert {m["kind"] for m in a} >= {"raise", "security"} and len([m for m in a if m["kind"] == "security"]) == 4
    batches = ev.mutant_batches(a, 2)
    assert sum(len(x) for x in batches) == len(a) and all(len(x) <= 2 for x in batches)
    for batch in batches:
        touched = [f for m in batch for f in m["files"]]
        assert len(touched) == len(set(touched))                                                  # no two mutants of a batch touch the same file


def test_attribution_separates_the_mutants_whose_selection_holds_a_failing_file_from_those_that_do_not():
    sel = {"M01": {"tests/test_a.py", "tests/test_b.py"}, "M02": {"tests/test_c.py"}, "M03": None}              # M03 was decided `full`: it can never miss a test
    plan = ev.plan_attribution(["tests/test_a.py", "tests/test_c.py", "tests/test_z.py"], sel)
    assert plan["tests/test_a.py"] == {"candidates": ["M01", "M03"], "others": ["M02"]}
    assert plan["tests/test_c.py"] == {"candidates": ["M02", "M03"], "others": ["M01"]}
    assert plan["tests/test_z.py"] == {"candidates": ["M03"], "others": ["M01", "M02"]}


def _row(i, decision, seconds, files=(), ts="2026-11-0{d}T10:00:00+00:00", commits=1):
    return {"seconds": seconds, "decision": decision, "files": list(files), "ts": ts.format(d=i), "commits": commits}


def test_the_saving_projection_charges_full_receipts_their_real_cost_and_forces_a_full_run_when_stale():
    secs = {"tests/test_a.py": 20.0, "tests/test_b.py": 10.0}
    rows = [_row(1, "full", 1000), _row(1, "impact", 1000, ["tests/test_a.py"], ts="2026-11-0{d}T11:00:00+00:00"), _row(1, "impact", 1000, ["tests/test_b.py"], ts="2026-11-0{d}T12:00:00+00:00"),
            _row(2, "impact", 1000, ["tests/test_a.py"], ts="2026-11-0{d}T13:00:00+00:00"), _row(5, "full", 1000)]
    res = ev.simulate_saving(rows, secs, overhead_s=60.0)
    # receipt 1 full (anchor); 2 and 3 impact (60+20, 60+10); 4 is 27 h after the anchor: forced full; 5 full
    assert res["ran_impact"] == 2 and res["impact_forced_full_by_freshness"] == 1 and res["seconds_full"] == 5000 and res["seconds_with_selector"] == 1000 + 80 + 70 + 1000 + 1000
    assert res["net_saving"] == round(1 - 3150 / 5000, 3)
    many = [_row(1, "full", 1000)] + [_row(1, "impact", 1000, ["tests/test_a.py"], ts="2026-11-01T1%d:00:00+00:00" % i) for i in range(7)]
    assert ev.simulate_saving(many, secs, 60.0)["impact_forced_full_by_freshness"] >= 1                 # more than 5 commits since the anchor
    assert ev.simulate_saving([_row(1, "impact", 1000, ["tests/test_a.py"])], secs, 60.0)["ran_impact"] == 0     # no full anchor yet: the first one is full


def test_the_evidence_line_needs_every_check_and_a_missing_file_fails_its_check(tmp_path):
    def write(name, obj):
        (tmp_path / name).write_text(json.dumps(obj), encoding="utf-8")

    assert ev.evaluate_evidence(tmp_path)["pass"] is False and not any(ev.evaluate_evidence(tmp_path)["checks"].values())      # nothing measured: nothing passes
    write("baseline.json", {"sha": "a" * 40, "runs": [{"rc": 0}, {"rc": 0}], "failed_in_first_run": [], "flaky": []})
    write("trace_vs_graph.json", {"tests_checked": 400, "tests_with_holes": 0, "a10": {"count": 0}})
    write("replay.json", {"rows": [{"sha": "x"}], "missed": 0})
    write("mutation.json", {"impact_decided_and_killed": 31, "escapes": 0, "escape_rate_upper_95": 0.097, "security_detected": [True] * 4, "batches_with_unexplained_failures": 0})
    write("saving.json", {"net_saving": 0.27})
    good = ev.evaluate_evidence(tmp_path)
    assert good["pass"] is True and good["sha"] == "a" * 40
    for name, change, check in [("baseline.json", {"flaky": ["tests/test_x.py::t"]}, "E1 no flaky test"), ("trace_vs_graph.json", {"tests_with_holes": 1}, "E2 no file ran outside the static closure"),
                                ("trace_vs_graph.json", {"a10": {"count": 2}}, "A10 the tracer changed no verdict"), ("replay.json", {"missed": 1}, "E3 no recorded failure falls outside its selection"),
                                ("mutation.json", {"escapes": 1}, "E4 zero escapes"),
                                ("mutation.json", {"batches_with_unexplained_failures": 1}, "E4 every failure has a cause (cascade victims only in batches with an explained failure)"), ("mutation.json", {"impact_decided_and_killed": 29}, "E4 at least 30 impact-decided mutants killed"),
                                ("mutation.json", {"escape_rate_upper_95": 0.2}, "E4 escape-rate 95% upper bound <= 0.1"), ("mutation.json", {"security_detected": [True, True, False, True]}, "E4 every security mutant trips its policy test"),
                                ("saving.json", {"net_saving": 0.19}, "A8 projected net saving >= 20%")]:
        original = json.loads((tmp_path / name).read_text(encoding="utf-8"))
        write(name, {**original, **change})
        res = ev.evaluate_evidence(tmp_path)
        assert res["pass"] is False and res["checks"][check] is False, check
        write(name, original)
    assert ev.evaluate_evidence(tmp_path)["pass"] is True


def test_the_tracer_attributes_code_imports_and_subprocesses_to_the_test_file_and_never_changes_the_verdict(tmp_path):
    proj = tmp_path / "proj"
    (proj / "pkg").mkdir(parents=True)
    (proj / "pkg" / "lib.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (proj / "pkg" / "other.py").write_text("def g():\n    return 2\n", encoding="utf-8")
    (proj / "child_script.py").write_text("import sys\nopen('child_state.txt', 'w').write(str('pytest' in sys.modules))\nimport pkg.other\npkg.other.g()\n", encoding="utf-8")
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
    assert (proj / "child_state.txt").read_text(encoding="utf-8") == "False"       # A10: the instrument must not make a child look like a pytest process (shared/config.py turns the .env autoload off then)


def test_the_tracer_is_off_without_its_environment(tmp_path):
    (tmp_path / "test_x.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("IMPACT_TRACE")}
    env.update(PYTEST_ADDOPTS="-p shared.testing.impact_trace", PYTHONPATH=str(_ROOT))
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_x.py"], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    assert "1 passed" in r.stdout and not list(tmp_path.glob("**/*.json"))
