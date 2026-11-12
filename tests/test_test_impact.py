"""FND-0385: the impact selector (shared/test_impact.py). Pure tests on synthetic trees; the last block reads the real repository to prove each NARROW_LOADERS entry and the 336daff8 regression.
Every fallback rule F1..F8 has a test that forces `full`; every graph edge type has one that selects through it; the negative tests prove an indirect dependency, a deleted module and a
repository scanner cannot escape."""
import ast
import functools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared import delivery_receipt as dr  # noqa: E402
from shared import test_impact as ti  # noqa: E402

TP = lambda top: top in {"pytest", "pandas"}      # noqa: E731 -- the installed third-party packages of the synthetic world


def _noise(n=20):
    return {f"tests/test_noise_{i}.py": f"def test_{i}():\n    assert True\n" for i in range(n)}


def _decide(changed, files, base=None, **kw):
    kw.setdefault("third_party", TP)
    return ti.decide(changed, {**_noise(), **files}, base, **kw)


# ── selection through every kind of edge ─────────────────────────────────────────

def test_a_transitive_importer_is_selected():
    files = {"shared/b.py": "X = 1\n", "shared/a.py": "from shared import b\n", "proyecto1/core/c.py": "import shared.a\n",
             "tests/test_c.py": "from proyecto1.core import c\n"}
    d = _decide(["shared/b.py"], files)
    assert d.scope == "impact" and "tests/test_c.py" in d.selected["tests"]


def test_a_bare_module_name_and_a_relative_import_resolve():
    files = {"proyecto1/core/classify_utils.py": "X = 1\n", "proyecto1/core/mid.py": "from .classify_utils import X\n",
             "proyecto1/tests/test_mid.py": "import mid\n"}
    d = _decide(["proyecto1/core/classify_utils.py"], files)
    assert d.scope == "impact" and "proyecto1/tests/test_mid.py" in d.selected["proyecto1"]


def test_a_bare_literal_handed_to_a_loader_in_a_test_is_a_dependency_and_so_is_the_package_chain_of_a_test():
    files = {"proyecto1/blocks/monetarios.py": "X = 1\n", "proyecto1/tests/__init__.py": "",
             "proyecto1/tests/test_bl.py": "import importlib\n\ndef test_x():\n    importlib.import_module('monetarios')\n",
             "proyecto2/tests/sub/__init__.py": "", "proyecto2/tests/__init__.py": "", "proyecto2/tests/sub/test_deep.py": "def test_y():\n    pass\n"}
    assert "proyecto1/tests/test_bl.py" in _decide(["proyecto1/blocks/monetarios.py"], files).selected["proyecto1"]
    d = _decide(["proyecto2/tests/__init__.py"], files)
    assert d.scope == "impact" and "proyecto2/tests/sub/test_deep.py" in d.selected["proyecto2"]            # changing a package's __init__ selects the tests collected through it
    assert _decide(["proyecto1/tests/__init__.py"], files).scope == "impact"


def test_a_mock_patch_target_string_is_an_edge():
    files = {"shared/thing.py": "def f():\n    pass\n", "tests/test_patch.py": "def test_x(mocker):\n    mocker.patch('shared.thing.f')\n"}
    assert "tests/test_patch.py" in _decide(["shared/thing.py"], files).selected["tests"]


def test_a_launcher_script_edge_reaches_the_python_it_runs():
    files = {"scripts/tool.py": "print(1)\n", "scripts/launch/run.bat": "python scripts\\tool.py\n", "tests/test_launch.py": "NAME = 'run.bat'\n"}
    assert "tests/test_launch.py" in _decide(["scripts/tool.py"], files).selected["tests"]


def test_a_data_file_named_by_a_test_is_an_edge_and_an_unreferenced_data_file_is_full():
    files = {"tests/fixtures/cases.json": "[]\n", "tests/test_cases.py": "P = 'cases.json'\n"}
    assert "tests/test_cases.py" in _decide(["tests/fixtures/cases.json"], files).selected["tests"]
    d = _decide(["scripts/orphan.sh"], {"scripts/orphan.sh": "echo hi\n"})
    assert d.scope == "full" and d.reasons[0].startswith("F4")


def test_the_standard_library_wins_over_a_same_named_repo_module():
    files = {"proyecto1/core/io.py": "X = 1\n", "tests/test_uses_stdlib_io.py": "import io\n"}
    d = _decide(["proyecto1/core/io.py"], files)
    assert d.scope == "full" and d.reasons[0].startswith("F4")          # nothing reaches it: the stdlib import did not create a false edge


def test_an_fstring_loader_is_bounded_by_its_prefix():
    files = {"proyecto1/blocks/alpha.py": "X = 1\n", "proyecto1/core/pipe.py": "import importlib\n\ndef f(n):\n    return importlib.import_module(f'blocks.{n}')\n",
             "tests/test_pipe.py": "from proyecto1.core import pipe\n"}
    d = _decide(["proyecto1/blocks/alpha.py"], files)
    assert d.scope == "impact" and "tests/test_pipe.py" in d.selected["tests"]


def test_a_deleted_module_still_selects_the_tests_that_imported_it():
    base = {**_noise(), "shared/old.py": "X = 1\n", "tests/test_old.py": "from shared import old\n"}
    head = {**_noise(), "tests/test_old.py": "from shared import old\n"}                # old.py is gone, its importer stays (and would now fail)
    d = ti.decide(["shared/old.py"], head, base, third_party=TP)
    assert d.scope == "impact" and "tests/test_old.py" in d.selected["tests"]


def test_a_changed_test_runs_itself_and_a_deleted_test_is_nothing_to_run():
    d = _decide(["tests/test_noise_3.py", "tests/test_gone.py"], {})
    assert d.scope == "impact" and d.selected["tests"] == ["tests/test_noise_3.py"]


def test_the_policy_tests_are_in_every_impact_decision():
    files = {"shared/b.py": "X = 1\n", "tests/test_b.py": "from shared import b\n", "tests/test_policy_scan.py": "def test_p():\n    pass\n"}
    d = _decide(["shared/b.py"], files, policy_tests=["tests/test_policy_scan.py::test_p", "tests/test_missing.py"])
    assert d.scope == "impact" and d.policy == ["tests/test_policy_scan.py::test_p"]               # one node of the file, never the whole file; a policy file the tree lacks is dropped
    assert "tests/test_policy_scan.py::test_p" in d.files() and "tests/test_b.py" in d.files() and "tests/test_policy_scan.py" not in d.selected["tests"]


def test_selection_is_grouped_by_the_suite_the_full_run_uses():
    files = {"proyecto2/src/m.py": "X = 1\n", "proyecto2/tests/test_m.py": "from src import m\n", "proyecto3/tests/test_m3.py": "from src import m\n"}
    d = _decide(["proyecto2/src/m.py"], files)
    assert d.selected["proyecto2"] == ["proyecto2/tests/test_m.py"] and d.selected["proyecto3"] == ["proyecto3/tests/test_m3.py"]
    assert ti.suite_of("proyecto2/tests/discovery/test_historia.py") is None and ti.suite_of("proyecto1/core/test_cost_router.py") is None and ti.suite_of("upload/code/test_x.py") is None


# ── always-run tests ─────────────────────────────────────────────────────────────

def test_a_repository_scanner_runs_in_every_impact_decision():
    files = {"shared/b.py": "X = 1\n", "tests/test_b.py": "from shared import b\n",
             "tests/test_scan.py": "from pathlib import Path\n\ndef test_s():\n    list(Path('.').joinpath('shared').rglob('*.py'))\n    for p in ROOT.rglob('*.py'):\n        pass\n"}
    assert "tests/test_scan.py" in _decide(["shared/b.py"], files).selected["tests"]


def test_a_test_that_starts_a_blind_subprocess_runs_always_and_one_with_a_derived_env_does_not():
    blind = "import subprocess\n\ndef test_x():\n    subprocess.run(['python', 'x.py'], env={'A': '1'})\n"
    derived = "import os, subprocess\n\ndef test_y():\n    env = dict(os.environ, A='1')\n    subprocess.run(['python', 'x.py'], env=env)\n"
    dynamic = "import subprocess\n\ndef test_z(s):\n    subprocess.run(f'python {s}')\n"
    files = {"shared/b.py": "X = 1\n", "tests/test_b.py": "from shared import b\n", "tests/test_blind.py": blind, "tests/test_derived.py": derived, "tests/test_dynamic.py": dynamic}
    sel = _decide(["shared/b.py"], files).selected["tests"]
    assert "tests/test_blind.py" in sel and "tests/test_dynamic.py" in sel and "tests/test_derived.py" not in sel


def test_a_test_that_loads_a_module_whose_name_comes_from_data_runs_always_and_one_with_a_literal_does_not():
    dyn = "import importlib\n\ndef test_x(names):\n    for n in names:\n        importlib.import_module(n)\n"
    lit = "import importlib\n\ndef test_y():\n    importlib.import_module('shared.b')\n"
    files = {"shared/b.py": "X = 1\n", "shared/c.py": "Y = 1\n", "tests/test_c.py": "from shared import c\n", "tests/test_dyn.py": dyn, "tests/test_lit.py": lit}
    sel = _decide(["shared/c.py"], files).selected["tests"]
    assert "tests/test_dyn.py" in sel and "tests/test_lit.py" not in sel and "tests/test_c.py" in sel


STUB_E2E = ("import shutil\nfrom pathlib import Path\nREAL = Path(__file__).resolve().parents[1]\n\n_TOOLS = ['scripts/launch/tool_a.py', 'proyecto1/tools/tool_b.py']\n\n"
            "class Tree:\n    def __init__(self, root):\n        self.root = root\n        shutil.copy(REAL / 'scripts' / 'launch' / 'run.bat', root)\n"
            "        shutil.copy(REAL / 'scripts' / 'launch' / 'helper.py', root)\n        for rel in _TOOLS:\n            p = self.root / rel\n            p.write_text('stub')\n\n"
            "def test_it(tmp_path):\n    Tree(tmp_path)\n")
STUB_FILES = {"scripts/launch/run.bat": "python scripts\\launch\\tool_a.py\npython proyecto1\\tools\\tool_b.py\npython scripts\\launch\\helper.py\n",
              "scripts/launch/tool_a.py": "X = 1\n", "proyecto1/tools/tool_b.py": "Y = 1\n", "scripts/launch/helper.py": "Z = 1\n", "tests/test_e2e_stub.py": STUB_E2E}


def test_the_names_of_stubs_a_test_creates_are_not_dependencies_but_the_real_files_it_copies_are():
    assert _decide(["proyecto1/tools/tool_b.py"], STUB_FILES).reasons[0].startswith("F4")                 # only a stub of it exists in the test: no test reaches the real file
    assert _decide(["scripts/launch/tool_a.py"], STUB_FILES).reasons[0].startswith("F4")
    assert "tests/test_e2e_stub.py" in _decide(["scripts/launch/helper.py"], STUB_FILES).selected["tests"]   # copied from the real tree (REAL / ...): a real dependency
    assert "tests/test_e2e_stub.py" in _decide(["scripts/launch/run.bat"], STUB_FILES).selected["tests"]     # so is the launcher it copies and runs


def test_a_stub_name_stays_a_dependency_when_the_test_also_reaches_the_real_file_another_way():
    for extra in ("\nfrom proyecto1.tools import tool_b\n",                                               # a real import
                  "\nOTHER = REAL / 'proyecto1' / 'tools' / 'tool_b.py'\n",                               # a real, repo-rooted path to the same file
                  "\nprint(_TOOLS)\n"):                                                                    # the list is used for something other than building stubs
        files = {**STUB_FILES, "tests/test_e2e_stub.py": STUB_E2E + extra}
        assert "tests/test_e2e_stub.py" in _decide(["proyecto1/tools/tool_b.py"], files).selected["tests"], extra
    loop_uses_it_for_real = STUB_E2E.replace("p = self.root / rel", "p = REAL / rel")
    assert "tests/test_e2e_stub.py" in _decide(["proyecto1/tools/tool_b.py"], {**STUB_FILES, "tests/test_e2e_stub.py": loop_uses_it_for_real}).selected["tests"]


def test_a_path_the_test_writes_to_is_a_new_file_not_a_dependency_and_a_read_is_not_cut():
    direct = ("import shutil\nfrom pathlib import Path\nREAL = Path(__file__).resolve().parents[1]\n\ndef test_it(t):\n"
              "    shutil.copy(REAL / 'scripts' / 'launch' / 'run.bat', t.root)\n    (t.root / 'proyecto1' / 'tools' / 'tool_b.py').write_text('stub')\n")
    files = {**STUB_FILES, "tests/test_e2e_stub.py": direct}
    assert _decide(["proyecto1/tools/tool_b.py"], files).reasons[0].startswith("F4")                       # created in the test's own tree: no test reaches the real file
    assert "tests/test_e2e_stub.py" in _decide(["scripts/launch/run.bat"], files).selected["tests"]
    read_it = direct.replace(".write_text('stub')", ".read_text()")                                           # the same path READ: it may be the repository, so it stays a dependency
    assert "tests/test_e2e_stub.py" in _decide(["proyecto1/tools/tool_b.py"], {**STUB_FILES, "tests/test_e2e_stub.py": read_it}).selected["tests"]
    into_repo = direct.replace("(t.root /", "(REAL /")                                                       # a write rooted at the repository is not a stub tree: never cut
    assert "tests/test_e2e_stub.py" in _decide(["proyecto1/tools/tool_b.py"], {**STUB_FILES, "tests/test_e2e_stub.py": into_repo}).selected["tests"]
    no_write = STUB_E2E.replace("            p.write_text('stub')\n", "            print(p)\n")                                      # a loop that never writes: not proven to create anything
    assert "tests/test_e2e_stub.py" in _decide(["proyecto1/tools/tool_b.py"], {**STUB_FILES, "tests/test_e2e_stub.py": no_write}).selected["tests"]


def test_a_test_whose_import_resolves_to_nothing_runs_always():
    files = {"shared/b.py": "X = 1\n", "tests/test_b.py": "from shared import b\n", "tests/test_ghost.py": "import no_such_package_anywhere\n"}
    assert "tests/test_ghost.py" in _decide(["shared/b.py"], files).selected["tests"]


def test_a_glob_over_a_stub_tree_is_not_a_repository_scan_but_one_rooted_at_the_repository_is():
    stub = "def test_a(tree, tmp_path):\n    list((tree.root / 'proyecto3' / 'log').glob('*.log'))\n    list(tmp_path.rglob('*.py'))\n    names = [n for n in ast.walk(ast.parse('x'))]\n"
    assert ti.scans_repository(stub) is False                     # the launcher e2e shape: a tree the test built itself, and ast.walk is not os.walk
    real = "from pathlib import Path\n_ROOT = Path(__file__).resolve().parents[1]\n_LAUNCH = _ROOT / 'scripts' / 'launch'\n\ndef test_b():\n    list(_LAUNCH.glob('*.bat'))\n"
    assert ti.scans_repository(real) is True                      # a name derived from __file__ through another derived name
    assert ti.scans_repository("import os\n\ndef test_c():\n    list(os.walk('shared'))\n") is True
    assert ti.scans_repository("from pathlib import Path\n\ndef test_d():\n    list(Path('.').joinpath('shared').rglob('*.py'))\n") is True
    assert ti.scans_repository("def broken(:\n") is True          # cannot tell: it runs always


def test_a_subprocess_env_built_from_os_environ_in_a_helper_is_not_blind_but_a_hand_built_one_is():
    helper = ("import os, subprocess\n\nclass Tree:\n    def __init__(self):\n        self.env = {**os.environ, 'A': '1'}\n\n    def env2(self, **x):\n        return {k: v for k, v in os.environ.items()}\n\n"
              "def test_a(tree):\n    subprocess.run(['x'], env=tree.env)\n    subprocess.run(['x'], env=tree.env2(B='2'))\n")
    hand = "import subprocess\n\ndef test_b():\n    subprocess.run(['x'], env={'PATH': '/bin'})\n"
    files = {"shared/b.py": "X = 1\n", "tests/test_b.py": "from shared import b\n", "tests/test_helper_env.py": helper, "tests/test_hand_env.py": hand}
    g = ti.build_graph({**_noise(), **files}, third_party=TP)
    assert "tests/test_hand_env.py" in g.blind_tests and "tests/test_helper_env.py" not in g.blind_tests


# ── every fallback rule forces full ──────────────────────────────────────────────

def test_F1_a_changed_file_that_does_not_parse_is_full_but_an_unrelated_broken_file_is_not():
    d = _decide(["shared/broken.py"], {"shared/broken.py": "def (:\n"})
    assert d.scope == "full" and d.reasons[0].startswith("F1")
    files = {"proyecto2/logs/funds.py": "2026-01-01 INFO started\n", "shared/b.py": "X = 1\n", "tests/test_b.py": "from shared import b\n"}
    assert _decide(["shared/b.py"], files).scope == "impact"          # an unimportable log file misnamed .py cannot affect a test
    assert _decide(["shared/b.py"], {**files, "tests/test_broken.py": "def (:\n"}).scope == "full"


def test_F2_infrastructure_and_the_selector_itself_are_full():
    for path in ("conftest.py", "proyecto1/conftest.py", "shared/testing/pg_fixtures.py", "db/pg/00_roles_schemas.sql", "scripts/ops/run_pg_tests.py",
                 "shared/delivery_receipt.py", "shared/test_impact.py", "scripts/audit/delivery_attest.py", "shared/git_export.py", "pytest.ini"):
        d = _decide([path], {path: "X = 1\n"})
        assert d.scope == "full" and d.reasons[0].startswith("F2"), path


def test_F3_a_hub_is_full():
    files = {"shared/hub.py": "X = 1\n", **{f"tests/test_hub_{i}.py": "from shared import hub\n" for i in range(30)}}
    d = _decide(["shared/hub.py"], files)
    assert d.scope == "full" and d.reasons[0].startswith("F3")


def test_F4_a_changed_code_file_no_test_reaches_is_full():
    d = _decide(["scripts/lonely.py"], {"scripts/lonely.py": "print(1)\n"})
    assert d.scope == "full" and d.reasons[0].startswith("F4")


def test_F5_a_selection_that_is_half_the_suite_is_full_and_so_is_a_slow_one():
    files = {"shared/b.py": "X = 1\n", "shared/c.py": "Y = 1\n", **{f"tests/test_big_{i}.py": "from shared import b\n" for i in range(4)},
             **{f"tests/test_huge_{i}.py": "from shared import c\n" for i in range(4)}}
    d = ti.decide(["shared/b.py", "shared/c.py"], {**{f"tests/test_n{i}.py": "def test_x():\n    pass\n" for i in range(7)}, **files}, third_party=TP)     # each reaches 4 of 15, together 8
    assert d.scope == "full" and d.reasons[0].startswith("F5")
    ok = {"shared/b.py": "X = 1\n", "tests/test_b.py": "from shared import b\n"}
    slow = _decide(["shared/b.py"], ok, durations={"tests/test_b.py": 900.0}, median_full_seconds=1000.0)
    assert slow.scope == "full" and "60%" in slow.reasons[0]
    assert _decide(["shared/b.py"], ok, durations={"tests/test_b.py": 30.0}, median_full_seconds=1000.0).scope == "impact"


def test_F6_too_many_runtime_files_changed_is_full():
    files = {f"shared/m{i}.py": "X = 1\n" for i in range(ti.MAX_RUNTIME_FILES + 1)}
    d = _decide(sorted(files), files)
    assert d.scope == "full" and d.reasons[0].startswith("F6")


def test_F8_a_switched_off_selector_is_full_and_a_docs_only_change_is_not_decided_here():
    files = {"shared/b.py": "X = 1\n", "tests/test_b.py": "from shared import b\n"}
    assert _decide(["shared/b.py"], files, enabled=False).reasons[0].startswith("F8")
    assert _decide(["doc/reglas/X.md"], files).scope == "full"


def test_an_analysis_that_raises_is_full(monkeypatch):
    monkeypatch.setattr(ti, "build_graph", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    d = ti.decide(["shared/b.py"], {"shared/b.py": "X = 1\n"})
    assert d.scope == "full" and d.reasons[0].startswith("F1")


# ── unbounded loaders ────────────────────────────────────────────────────────────

def test_an_unbounded_loader_depends_conservatively_on_its_top_level_directory_and_a_narrowed_one_does_not():
    loader = "import importlib\n\ndef f(x):\n    return importlib.import_module(x)\n"
    files = {"scripts/audit/load.py": loader, "scripts/audit/target.py": "X = 1\n", "tests/test_load.py": "from scripts.audit import load\n"}
    g = ti.build_graph({**_noise(), **files}, third_party=TP)
    assert ("scripts/audit/load.py", 4, "import_module") in g.unbounded
    assert "tests/test_load.py" in ti.decide(["scripts/audit/target.py"], {**_noise(), **files}, third_party=TP).selected["tests"]
    narrowed = {**files, "shared/env_guard.py": loader, "shared/other.py": "X = 1\n", "tests/test_guard.py": "from shared import env_guard\n"}
    d = ti.decide(["shared/other.py"], {**_noise(), **narrowed}, third_party=TP)
    assert d.scope == "full" and d.reasons[0].startswith("F4")           # env_guard is narrowed to nothing: shared/other.py is reached by no test


# ── F7 freshness ─────────────────────────────────────────────────────────────────

def _receipt(sha, scope="full", result="pass", ts="2026-11-07T10:00:00+00:00"):
    r = {"v": 1, "sha": sha, "scope": scope, "result": result, "ts": ts, "covers": [sha]}
    r["digest"] = dr.digest_of(r)
    return r


def _fresh(receipts, commits=2, hours=3.0, ancestors=("a" * 40,)):
    return ti.freshness_reason("h" * 40, receipts, is_ancestor=lambda a, b: a in ancestors, code_commits_between=lambda a, b: commits, hours_between=lambda a, b: hours)


def test_F7_freshness_is_decided_by_ancestry_distance_and_age():
    ok = _receipt("a" * 40)
    assert _fresh([ok])[0] is None
    assert _fresh([])[0].startswith("F7 no passing full receipt")
    assert _fresh([_receipt("b" * 40)])[0].startswith("F7 no passing full receipt")            # a full receipt that is not an ancestor does not count
    assert _fresh([_receipt("a" * 40, scope="area")])[0].startswith("F7 no passing")
    assert _fresh([_receipt("a" * 40, result="fail")])[0].startswith("F7 no passing")
    assert "code-changing commits" in _fresh([ok], commits=6)[0]
    assert "older" in _fresh([ok], hours=30.0)[0]
    assert _fresh([ok], commits=None)[0].startswith("F7") and _fresh([ok], hours=None)[0].startswith("F7")
    tampered = dict(ok, ts="2026-01-01T00:00:00+00:00")
    assert _fresh([tampered])[0].startswith("F7 no passing")


# ── the derived mode ─────────────────────────────────────────────────────────────

def _shadow(i, escapes=(), algo=ti.ALGO_VERSION, day=None, decided="impact"):
    day = day if day is not None else i
    r = {"v": 2, "sha": f"{i:040x}", "scope": "full", "result": "pass", "ts": f"2026-11-{1 + day:02d}T10:00:00+00:00", "covers": [f"{i:040x}"],
         "selection": {"algo": algo, "mode": "SHADOW", "escapes": list(escapes), "decision": {"scope": decided}}}
    r["digest"] = dr.digest_of(r)
    return r


def _evidence(algo=ti.ALGO_VERSION, result="pass"):
    r = {"kind": "impact_evidence", "algo": algo, "result": result, "ts": "2026-11-08T00:00:00+00:00"}
    r["digest"] = dr.digest_of(r)
    return r


def test_the_mode_is_enforce_only_on_evidence_a_long_enough_clean_streak_and_a_week():
    streak = [_shadow(i, day=i) for i in range(10)]                                  # days 0..9
    assert ti.effective_mode([_evidence(), *streak]) == "ENFORCE"
    assert ti.effective_mode(streak) == "SHADOW"                                      # no evidence line
    assert ti.effective_mode([_evidence(), *streak[:9]]) == "SHADOW"                  # 9 receipts
    assert ti.effective_mode([_evidence(), *[_shadow(i, day=i // 5) for i in range(10)]]) == "SHADOW"   # 10 receipts inside two days
    assert ti.effective_mode([_evidence(result="fail"), *streak]) == "SHADOW"
    assert ti.effective_mode([_evidence(algo=0), *streak]) == "SHADOW"                # evidence of another algorithm version
    assert ti.effective_mode([_evidence(), *[_shadow(i, day=i, algo=0) for i in range(10)]]) == "SHADOW"
    assert ti.effective_mode([_evidence(), *[_shadow(i, day=i, decided="full") for i in range(10)]]) == "SHADOW"     # full decisions prove nothing about the selector


def test_one_escape_demotes_and_the_streak_starts_again():
    streak = [_shadow(i, day=i) for i in range(10)]
    assert ti.effective_mode([_evidence(), *streak, _shadow(10, escapes=["tests/test_x.py"], day=10)]) == "SHADOW"
    escape = _shadow(10, escapes=["tests/test_x.py"], day=10)
    assert ti.effective_mode([_evidence(), *streak, escape, *[_shadow(11 + i, day=11 + i) for i in range(5)]]) == "SHADOW"       # only 5 clean receipts since the escape
    assert ti.effective_mode([_evidence(), *streak, escape, *[_shadow(11 + i, day=11 + i) for i in range(10)]]) == "ENFORCE"    # a new clean streak of 10 over 9 days earns it back
    assert ti.effective_mode([_evidence(), *streak, escape, *[_shadow(11 + i, day=11 + i // 5) for i in range(10)]]) == "SHADOW"   # 10 receipts but inside two days


def test_a_tampered_receipt_does_not_count_and_the_ceiling_and_kill_switch_win():
    streak = [_shadow(i, day=i) for i in range(10)]
    streak[3]["selection"]["escapes"] = []
    streak[4] = dict(streak[4], ts="2026-12-01T00:00:00+00:00")                      # digest no longer matches: ignored, so only 9 intact receipts
    assert ti.effective_mode([_evidence(), *streak]) == "SHADOW"
    good = [_evidence(), *[_shadow(i, day=i) for i in range(10)]]
    assert ti.effective_mode(good, ceiling="SHADOW") == "SHADOW" and ti.effective_mode(good, ceiling="OFF") == "OFF" and ti.effective_mode(good, enabled=False) == "OFF"


def test_an_escape_is_a_failing_test_file_the_selection_did_not_contain():
    failed = ["tests/test_a.py::test_x", "tests/test_b.py::test_y", "tests/test_a.py::test_z", "some warning"]
    assert ti.find_escapes(failed, ["tests/test_a.py"]) == ["tests/test_b.py"]
    assert ti.find_escapes([], ["tests/test_a.py"]) == []


def test_a_proyecto2_failure_is_matched_under_its_cwd_relative_id_and_an_unknown_id_is_an_escape():
    all_tests = ["tests/test_a.py", "proyecto2/tests/calculations/test_drawdown.py"]
    assert ti.find_escapes(["tests/calculations/test_drawdown.py::test_x"], ["proyecto2/tests/calculations/test_drawdown.py"], all_tests) == []
    assert ti.find_escapes(["tests/calculations/test_drawdown.py::test_x"], ["tests/test_a.py"], all_tests) == ["tests/calculations/test_drawdown.py"]
    assert ti.find_escapes(["tests/test_mystery.py::test_x"], ["tests/test_a.py"], all_tests) == ["tests/test_mystery.py"]


# ── FND-0387: a hub bounded by the symbols it changes ────────────────────────────

_HUB = "shared/config.py"
_HUB_BASE = "A_ENABLED = False\nB_LIMIT = 3\nFLAGS = ('A_ENABLED',)\n\ndef limit():\n    return B_LIMIT\n"


def _hub_world(readers=None, n_readers=30):
    """A hub imported by 30 tests (so F3 applies) plus a few specific readers."""
    files = {_HUB: _HUB_BASE, **{f"tests/test_hub_{i}.py": "from shared import config\n\ndef test_x():\n    assert config is not None\n" for i in range(n_readers)}}
    files.update(readers or {})
    return files


def _hub_decide(head, files=None, **kw):
    files = dict(files or _hub_world())
    files[_HUB] = head
    return _decide([_HUB], files, base_texts={_HUB: _HUB_BASE}, **kw)


def test_a_hub_without_its_base_text_stays_a_hub_and_with_it_a_new_constant_selects_only_what_names_or_enumerates():
    files = _hub_world(readers={"tests/test_reader.py": "from shared import config\n\ndef test_r():\n    assert config.B_LIMIT == 3\n"})
    full = _decide([_HUB], {**files, _HUB: _HUB_BASE + "NEW_ENABLED = False\n"})
    assert full.scope == "full" and full.reasons[0].startswith("F3")                                  # no base text: unchanged behaviour
    d = _hub_decide(_HUB_BASE + "NEW_ENABLED = False\n", files)
    assert d.scope == "impact" and "tests/test_reader.py" not in d.files()                           # nothing names NEW_ENABLED and nothing enumerates the module
    assert any("bounded by its changed symbols" in n for n in d.reasons)


def test_an_edited_constant_selects_the_files_that_name_it_and_those_that_use_what_uses_it():
    files = _hub_world(readers={"shared/user.py": "from shared import config\n\ndef lim():\n    return config.B_LIMIT\n",
                                "tests/test_user.py": "from shared import user\n\ndef test_u():\n    assert user.lim()\n",
                                "tests/test_other.py": "from shared import user\n\ndef test_o():\n    assert True\n",
                                "tests/test_unrelated.py": "from shared import config\n\ndef test_z():\n    assert config.A_ENABLED is False\n"})
    d = _hub_decide(_HUB_BASE.replace("B_LIMIT = 3", "B_LIMIT = 4"), files)
    assert d.scope == "impact"
    got = set(d.files())
    assert "tests/test_user.py" in got and "tests/test_unrelated.py" not in got
    assert "tests/test_other.py" not in got                                                           # imports user.py but never names `lim`


def test_a_function_of_the_hub_that_uses_a_changed_constant_is_changed_too():
    files = _hub_world(readers={"tests/test_lim.py": "from shared import config\n\ndef test_l():\n    assert config.limit() == 3\n"})
    d = _hub_decide(_HUB_BASE.replace("B_LIMIT = 3", "B_LIMIT = 4"), files)
    assert "tests/test_lim.py" in d.files()


def test_a_name_inside_a_literal_list_of_the_hub_counts_as_a_reference():
    base = "FLAGS = ('LATER_ENABLED',)\n"
    files = {_HUB: base, **{f"tests/test_hub_{i}.py": "from shared import config\n" for i in range(30)},
             "tests/test_flags.py": "from shared import config\n\ndef test_f():\n    assert config.FLAGS\n"}
    d = _decide([_HUB], {**files, _HUB: base + "LATER_ENABLED = True\n"}, base_texts={_HUB: base})
    assert d.scope == "impact" and "tests/test_flags.py" in d.files()                                 # FLAGS lists the name that now exists


def test_a_reader_that_enumerates_or_probes_by_a_computed_name_or_reads_the_file_is_affected_by_any_change():
    readers = {"tests/test_enum.py": "from shared import config\n\ndef test_e():\n    assert [n for n in dir(config) if n.isupper()]\n",
               "tests/test_computed.py": "from shared import config\n\ndef test_c(kind):\n    assert getattr(config, kind + '_ENABLED', None) is None\n",
               "tests/test_text.py": "from pathlib import Path\n\ndef test_t():\n    assert 'ENABLED' in (Path('shared') / 'config.py').read_text()\n",
               "tests/test_passed.py": "from shared import config\nfrom tests import helper\n\ndef test_p():\n    helper.run(config)\n",
               "tests/helper.py": "def run(m):\n    return m\n",
               "tests/test_star.py": "from shared.config import *\n\ndef test_s():\n    assert True\n"}
    d = _hub_decide(_HUB_BASE + "NEW_ENABLED = False\n", _hub_world(readers=readers))
    assert d.scope == "impact", d.reasons
    assert {"tests/test_enum.py", "tests/test_computed.py", "tests/test_text.py", "tests/test_passed.py", "tests/test_star.py"} <= set(d.files())


def test_a_probe_by_a_name_from_a_literal_list_or_from_a_hub_list_and_a_literal_getattr_are_not_hazards():
    readers = {"tests/test_lit.py": "from shared import config\n\ndef test_l():\n    assert [n for n in ('A_ENABLED',) if getattr(config, n, False)] == []\n",
               "tests/test_hublist.py": "from shared import config\n\ndef test_h():\n    assert [n for n in config.FLAGS if getattr(config, n, False)] == []\n",
               "tests/test_literal.py": "from shared import config\n\ndef test_g():\n    assert getattr(config, 'B_LIMIT', 0) == 3\n"}
    d = _hub_decide(_HUB_BASE + "NEW_ENABLED = False\n", _hub_world(readers=readers))
    assert d.scope == "impact" and not ({"tests/test_lit.py", "tests/test_hublist.py", "tests/test_literal.py"} & set(d.files()))


def test_module_level_code_that_uses_a_changed_name_makes_the_hub_unbounded_and_so_does_a_changed_anonymous_statement():
    anon_base = _HUB_BASE + "if A_ENABLED:\n    pass\n"
    files = _hub_world()
    changed_ref = _decide([_HUB], {**files, _HUB: anon_base.replace("A_ENABLED = False", "A_ENABLED = True")}, base_texts={_HUB: anon_base})
    assert changed_ref.scope == "full" and "F3" in changed_ref.reasons[0]
    changed_stmt = _decide([_HUB], {**files, _HUB: anon_base.replace("pass", "print(1)")}, base_texts={_HUB: anon_base})
    assert changed_stmt.scope == "full"
    assert _decide([_HUB], {**files, _HUB: anon_base + "# a comment\n"}, base_texts={_HUB: anon_base}).scope == "impact"      # formatting and comments change no symbol


def test_a_module_getattr_a_new_hub_and_an_unparsable_hub_are_unbounded():
    for head, base in (("def __getattr__(n):\n    return 1\n", _HUB_BASE), (_HUB_BASE, None), ("def broken(:\n", _HUB_BASE)):
        files = {**_hub_world(), _HUB: head}
        assert _decide([_HUB], files, base_texts={_HUB: base}).scope == "full"


def test_an_affected_conftest_or_test_infrastructure_makes_the_hub_unbounded():
    files = _hub_world(readers={"conftest.py": "from shared import config\n\ndef pytest_configure():\n    config.B_LIMIT\n"})
    d = _hub_decide(_HUB_BASE.replace("B_LIMIT = 3", "B_LIMIT = 4"), files)
    assert d.scope == "full" and "infrastructure" in d.reasons[0]


def test_a_script_main_block_is_reached_only_by_a_run_of_the_script_not_by_importing_it():
    readers = {"scripts/tool.py": "from shared import config\n\ndef main():\n    return config.B_LIMIT\n\nif __name__ == '__main__':\n    main()\n",
               "tests/test_runs_tool.py": "import subprocess\n\ndef test_r():\n    subprocess.run(['python', 'scripts/tool.py'], env={})\n",
               "tests/test_imports_tool.py": "from scripts import tool\n\ndef test_i():\n    assert tool\n"}
    d = _hub_decide(_HUB_BASE.replace("B_LIMIT = 3", "B_LIMIT = 4"), _hub_world(readers=readers))
    assert d.scope == "impact" and "tests/test_runs_tool.py" in d.files()
