"""
tests/test_cycle_sequence_e2e.py -- every *_runCycle orchestrator is a SUBSET of the master sequence (FND-0309).

The master sequence is defined once in scripts/launch/cycle_sequence.py. Each orchestrator is run in the SIMULATED tool tree of
tests/test_batch_launchers_e2e.py (no database, no network, never the real repo), its tool trace is mapped to master phases and compared with the
phases its projects require, in order. This is the check that would have caught P2P3_runCycle.bat never refreshing macro + NAV.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "scripts" / "launch"))
import cycle_sequence as seq  # noqa: E402
import test_batch_launchers_e2e as e2e  # noqa: E402

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the launchers are cmd.exe batch files")

ARGS = {"P2P3_runCycle": ["--no-pause", "--no-prompts"], "P1P2P3_runCycle": ["--no-pause", "--no-prompts"]}
LAUNCH = _HERE.parent / "scripts" / "launch"


@pytest.fixture()
def tree(tmp_path):
    return e2e.Tree(tmp_path)


# ---------------------------------------------------------------- the definition itself (pure)

def test_the_master_orders_every_phase_once_and_knows_every_orchestrator():
    assert len(seq.ORDER) == len(set(seq.ORDER))
    assert set(seq.ORCHESTRATORS) == {"P1_runCycle", "P2_runCycle", "P3_runCycle", "P1P2_runCycle", "P2P3_runCycle", "P1P2P3_runCycle"}


def test_expected_sequences_are_the_master_filtered_by_project():
    full = seq.expected({"P1", "P2", "P3"})
    assert full == ["PRE_P2", "PRE_P1", "BETA_SNAPSHOT", "HARVEST", "BENCH", "P1_PIPELINE", "POST_P1", "DISCOVER", "P2_CALC", "BETA_COMPARE",
                    "P3_FRESHNESS", "CYCLE_REPORT", "P3_BUILD", "POST_P2", "P3_REPORT"]
    assert seq.expected({"P2", "P3"}) == ["PRE_P2", "BETA_SNAPSHOT", "DISCOVER", "P2_CALC", "BETA_COMPARE", "P3_FRESHNESS", "CYCLE_REPORT",
                                          "P3_BUILD", "POST_P2", "P3_REPORT"]
    assert seq.expected({"P2"})[-4:] == ["POST_P2", "BETA_COMPARE", "P3_FRESHNESS", "CYCLE_REPORT"]      # no P3: POST follows the last P2 writer
    assert seq.expected({"P3"}) == ["P3_BUILD", "POST_P2", "P3_REPORT"]
    assert seq.expected({"P1"}) == ["PRE_P1", "HARVEST", "BENCH", "P1_PIPELINE", "POST_P1", "CYCLE_REPORT"]
    master = [p for p in seq.expected({"P1", "P2", "P3"}) if p != "POST_P2"]      # POST_P2 moves with P3 (see expected): checked above
    for projects in seq.ORCHESTRATORS.values():                                  # a subset keeps the master order
        got = [p for p in seq.expected(projects) if p != "POST_P2"]
        assert [p for p in master if p in got] == got


def test_an_unknown_tool_is_reported_not_ignored():
    assert seq.observed(["something_new --x"]) == ["UNKNOWN:something_new"]


# ---------------------------------------------------------------- every orchestrator, executed in the simulated tree

@pytest.mark.parametrize("name", sorted(seq.ORCHESTRATORS))
def test_the_orchestrator_runs_exactly_the_master_phases_of_its_projects(tree, name):
    r = tree.run(name, *ARGS.get(name, []))
    assert r.returncode == 0, r.stdout[-600:] + r.stderr[-300:]
    got = seq.observed(tree.lines())
    want = seq.expected(seq.ORCHESTRATORS[name])
    assert got == want, f"{name}: phases it ran {got}; the master requires {want}; missing {[p for p in want if p not in got]}"


@pytest.mark.parametrize("name", sorted(seq.ORCHESTRATORS))
def test_every_orchestrator_starts_with_the_preflight_or_delegates_to_one_that_does(name):
    src = (LAUNCH / f"{name}.bat").read_text(encoding="ascii")
    delegates = name == "P1P2P3_runCycle" and "P1P2_runCycle.bat" in src         # the master calls the P1+P2 cycle, which runs the preflight
    own = "call :preflight" in src                                                # P1P2_runCycle keeps its own (resume-aware) preflight
    assert delegates or own or '"%PHASES%" :preflight' in src, f"{name} does not run the preflight phase"


# ---------------------------------------------------------------- the documented opt-outs of P2P3_runCycle

def test_p2p3_no_discover_is_the_explicit_recalculation_only_mode(tree):
    r = tree.run("P2P3_runCycle", "--no-pause", "--no-prompts", "--no-discover")
    assert r.returncode == 0, r.stdout[-600:]
    got = seq.observed(tree.lines())
    assert "DISCOVER" not in got and got == [p for p in seq.expected({"P2", "P3"}) if p != "DISCOVER"]


def test_p2p3_skip_macro_and_workers_reach_the_nav_loader_only(tree):
    r = tree.run("P2P3_runCycle", "--no-pause", "--no-prompts", "--skip-macro", "--workers", "4")
    assert r.returncode == 0, r.stdout[-600:]
    assert "macro_discovery" not in tree.tools() and "nav_discovery" in tree.tools()
    assert any(x.startswith("nav_discovery") and "--workers 4" in x for x in tree.lines())


def test_p2p3_stops_before_p2_when_the_macro_nav_refresh_fails(tree):
    r = tree.run("P2P3_runCycle", "--no-pause", "--no-prompts", STUB_RC_NAV_DISCOVERY="3")
    assert r.returncode == 3, r.stdout[-600:]
    assert "run_pipeline" not in tree.tools() and "p3_build_portfolio" not in tree.tools()


# ---------------------------------------------------------------- option parity: a subset accepts the options of every project it covers

@pytest.mark.parametrize("name", sorted(seq.ORCHESTRATORS))
def test_every_orchestrator_accepts_the_options_of_the_projects_it_covers(name):
    text = lambda f: (LAUNCH / f).read_text(encoding="ascii")                      # noqa: E731
    haystack = text(f"{name}.bat")
    if name in ("P1P2_runCycle", "P1P2P3_runCycle"):                               # the P1+P2 option table is shared (lib/p1p2_options.bat)
        haystack += text("lib/p1p2_options.bat")
    missing = [o for o in seq.required_options(seq.ORCHESTRATORS[name]) if o not in haystack]
    assert not missing, f"{name} does not accept {missing} (options of the projects it covers)"


def test_p2p3_forwards_the_p2_options_to_the_p2_step(tree):
    r = tree.run("P2P3_runCycle", "--no-pause", "--no-prompts", "--force", "--no-export", "--", "--max-new-per-run", "7")
    assert r.returncode == 0, r.stdout[-600:]
    pipe = next(x for x in tree.lines() if x.startswith("run_pipeline"))
    assert "--force" in pipe and "--max-new-per-run 7" in pipe, pipe
    assert "export_metrics" not in tree.tools()                                    # --no-export reached P2_calculateIndicators


def test_p2p3_forwards_the_p3_options_to_the_p3_steps(tree, tmp_path):
    out = tmp_path / "rep"
    r = tree.run("P2P3_runCycle", "--no-pause", "--no-prompts", "--scenario", "mi_cartera", "--allow-stale", "--report-dir", str(out))
    assert r.returncode == 0, r.stdout[-600:]
    build = next(x for x in tree.lines() if x.startswith("p3_build_portfolio"))
    assert "mi_cartera" in build and "--allow-stale" in build, build
    post = next(x for x in tree.lines() if x.startswith("run_statistical_audit") and "--run-id post_" in x)
    assert "--compare-to pre_" in post, post                                       # P3_buildPortfolio consumed --audit-compare-to: the POST audit used it
    assert any(x.startswith("generate_report") and str(out) in x for x in tree.lines())


def test_p2p3_p3_dry_run_builds_nothing_reports_nothing_and_still_audits_once(tree):
    r = tree.run("P2P3_runCycle", "--no-pause", "--no-prompts", "--p3-dry-run")
    assert r.returncode == 0, r.stdout[-600:]
    assert "--dry-run" in next(x for x in tree.lines() if x.startswith("p3_build_portfolio"))
    got = seq.observed(tree.lines())
    assert "P3_REPORT" not in got and got[-2:] == ["P3_BUILD", "POST_P2"], got      # the backup POST audit: P3 did not make it
    assert sum(1 for x in tree.lines() if x.startswith("run_statistical_audit") and "--run-id post_" in x) == 1


def test_p2p3_rejects_a_scenario_with_spaces_and_a_missing_value_before_running_anything(tree):
    for args in (["--scenario", "a b"], ["--scenario"], ["--report-dir", "--force"], ["--workers", "x"]):
        r = tree.run("P2P3_runCycle", "--no-pause", "--no-prompts", *args)
        assert r.returncode == 100 and tree.tools() == [], (args, r.stdout[-300:])


# ---------------------------------------------------------------- the non-default paths of every orchestrator (probed in the simulated tree)

NP = ["--no-pause", "--no-prompts"]
AUD2 = {"STUB_RC_RUN_STATISTICAL_AUDIT": "2"}                                       # every audit INCOMPLETE (RC 2)
FULL = {n: seq.expected(p) for n, p in seq.ORCHESTRATORS.items()}
WITHOUT = lambda name, *drop: [p for p in FULL[name] if p not in drop]               # noqa: E731
# (orchestrator, args, env, rc, phases)
VARIANTS = [
    ("P1_runCycle", ["--no-harvest"], {}, 0, WITHOUT("P1_runCycle", "HARVEST")),
    ("P1_runCycle", [], AUD2, 107, ["PRE_P1"]),
    ("P1_runCycle", ["--allow-no-baseline"], AUD2, 0, FULL["P1_runCycle"]),
    ("P2_runCycle", ["--skip-macro"], {}, 0, FULL["P2_runCycle"]),                    # NAV is still refreshed
    ("P2_runCycle", [], AUD2, 107, ["PRE_P2"]),
    ("P2_runCycle", ["--allow-no-baseline"], AUD2, 0, FULL["P2_runCycle"]),
    ("P2_runCycle", [], {"STUB_RC_RUN_PIPELINE": "4"}, 4, ["PRE_P2", "BETA_SNAPSHOT", "DISCOVER", "P2_CALC"]),
    ("P3_runCycle", ["--p3-dry-run"], {}, 0, ["P3_BUILD"]),
    ("P3_runCycle", [], {"STUB_RC_P3_BUILD_PORTFOLIO": "2"}, 2, ["P3_BUILD"]),
    ("P1P2_runCycle", ["--no-harvest"], {}, 0, WITHOUT("P1P2_runCycle", "HARVEST")),
    ("P1P2_runCycle", [], AUD2, 107, ["PRE_P2", "PRE_P1"]),
    ("P1P2_runCycle", ["--allow-no-baseline"], AUD2, 0, FULL["P1P2_runCycle"]),
    ("P2P3_runCycle", NP, AUD2, 107, ["PRE_P2"]),
    ("P2P3_runCycle", NP + ["--allow-no-baseline"], AUD2, 0, FULL["P2P3_runCycle"]),
    ("P2P3_runCycle", NP, {"STUB_RC_RUN_PIPELINE": "4"}, 4, ["PRE_P2", "BETA_SNAPSHOT", "DISCOVER", "P2_CALC"]),
    ("P1P2P3_runCycle", NP + ["--p3-dry-run"], {}, 0, WITHOUT("P1P2P3_runCycle", "P3_REPORT")),   # the backup POST audit still runs
    ("P1P2P3_runCycle", NP, AUD2, 107, ["PRE_P2", "PRE_P1"]),
    ("P1P2P3_runCycle", NP + ["--allow-no-baseline"], AUD2, 0, FULL["P1P2P3_runCycle"]),
    ("P1P2P3_runCycle", NP + ["--no-harvest"], {}, 0, WITHOUT("P1P2P3_runCycle", "HARVEST")),
]


@pytest.mark.parametrize("name,args,env,rc,phases", VARIANTS, ids=[f"{v[0]} {' '.join(v[1])} {','.join(v[2])}".strip() for v in VARIANTS])
def test_the_non_default_paths_run_the_documented_subset_of_phases(tree, name, args, env, rc, phases):
    r = tree.run(name, *args, **env)
    assert r.returncode == rc, r.stdout[-500:]
    assert seq.observed(tree.lines()) == phases


def test_p1p2_resume_from_step_4_runs_only_the_tail_of_the_cycle(tree):
    assert tree.run("P1P2_runCycle", STUB_RC_RUN_PIPELINE="4").returncode == 4
    tree.trace.unlink()
    r = tree.run("P1P2_runCycle", "--from", "4")
    assert r.returncode == 0, r.stdout[-500:]
    assert seq.observed(tree.lines()) == ["P2_CALC", "POST_P2", "BETA_COMPARE", "P3_FRESHNESS", "CYCLE_REPORT"]    # no new PRE, no repeated P1


def test_p1p2p3_only_p3_runs_the_gate_and_p3_on_the_last_ok_cycle(tree):
    assert tree.run("P1P2P3_runCycle", *NP).returncode == 0
    tree.trace.unlink()
    r = tree.run("P1P2P3_runCycle", *NP, "--only-p3")
    assert r.returncode == 0, r.stdout[-500:]
    assert seq.observed(tree.lines()) == ["BETA_COMPARE", "P3_BUILD", "POST_P2", "P3_REPORT"]


def test_p1p2p3_a_failing_beta_gate_stops_before_p3_and_still_makes_the_backup_post_audit(tree):
    r = tree.run("P1P2P3_runCycle", *NP, STUB_RC_BETA_SHIFT_AUDIT="1")
    assert r.returncode == 1, r.stdout[-500:]
    got = seq.observed(tree.lines())
    assert "P3_BUILD" not in got and got[-1] == "POST_P2", got
