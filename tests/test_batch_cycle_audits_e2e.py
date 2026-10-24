"""
tests/test_batch_cycle_audits_e2e.py -- audit ownership in the cycle launchers (FND-0308, doc/reglas/NORMAS_BATCH.md section 14).

Runs the REAL launchers against the simulated tool tree of tests/test_batch_launchers_e2e.py (no database, no network, never the real
repo). What these tests pin down:
  - the orchestrators own the audits: a frozen PRE (pre_<stamp>_p2 / pre_<stamp>_costs) before anything mutates, and a POST with
    --compare-to at the boundary of each domain's last writer (P1 after step 2, P2 once -- after the DRAFT when P3 is in the flow);
  - an invalid PRE (failed or INCOMPLETE) blocks every mutating step with RC 107, unless --allow-no-baseline;
  - the leaf step launchers do not audit unless asked (--audit);
  - the standalone orchestrators P1_runCycle / P2_runCycle / P3_runCycle and the subsets they stand for.
NOT covered (stated, not claimed): the real P1_audit / P2_audit against a database and the real control.audit_run writes (the
persistence is tested in the statistical-audit suites), and the promotion gate, which none of this changes.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_batch_launchers_e2e as e2e  # noqa: E402  (the stub tree and its conventions, one definition)

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the launchers are cmd.exe batch files")

# STUB_FAIL_AUDIT="<run-id prefix>:<domain>:<rc>" makes the matching audit exit with <rc> (and still leave its trace line).
_AUDIT_STUB = '''import os, sys
_args = sys.argv[1:]
_spec = os.environ.get("STUB_FAIL_AUDIT")
if _spec and "--run-id" in _args:
    _prefix, _domain, _rc = _spec.split(":")
    if _args[_args.index("--run-id") + 1].startswith(_prefix) and _args[_args.index("--domain") + 1] == _domain:
        with open(os.environ["STUB_TRACE"], "a") as f:
            f.write("run_statistical_audit %s\\n" % " ".join(_args))
        sys.exit(int(_rc))
''' + e2e._PY_STUB

# the real tool writes its findings to --out: the stub does too, so the launcher's `copy` of the stamped file has something to copy
_BENCH_STUB = '''import os, sys
_args = sys.argv[1:]
if "--out" in _args:
    _p = _args[_args.index("--out") + 1]
    os.makedirs(os.path.dirname(_p), exist_ok=True)
    open(_p, "w").write("{}")
''' + e2e._PY_STUB

CYCLE = e2e.CYCLE
P123 = e2e.P123


@pytest.fixture()
def tree(tmp_path):
    t = e2e.Tree(tmp_path)
    (t.root / "scripts" / "audit" / "run_statistical_audit.py").write_text(_AUDIT_STUB)
    (t.root / "proyecto1" / "tools" / "audit_benchmark_consistency.py").write_text(_BENCH_STUB)
    return t


# ---------------------------------------------------------------- helpers over the trace

def _audits(tree):
    return [ln for ln in tree.lines() if ln.startswith("run_statistical_audit")]


def _rid(line):
    return line.split("--run-id ")[1].split()[0]


def _dom(line):
    return line.split("--domain ")[1].split()[0]


def _cmp(line):
    return line.split("--compare-to ")[1].split()[0] if "--compare-to" in line else None


def _kind(tree, kind, domain):
    return [x for x in _audits(tree) if _rid(x).startswith(kind + "_") and _dom(x) == domain]


def _at(tree, prefix, after=0):
    return next(i for i, ln in enumerate(tree.lines()) if i >= after and ln.startswith(prefix))


def _pre_post(tree):
    """(pre p2, pre costs, post p2, post costs) lines of the trace; each a list."""
    return (_kind(tree, "pre", "p2"), _kind(tree, "pre", "costs"), _kind(tree, "post", "p2"), _kind(tree, "post", "costs"))


def _index_of(tree, line):
    return tree.lines().index(line)


def _compare_out_dirs(tree):
    """Parent folder names of the --out of every beta_shift_audit --compare in the trace."""
    outs = [ln.split("--out ")[1].strip().replace("\\", "/") for ln in tree.lines() if ln.startswith("beta_shift_audit --compare")]
    return sorted({o.rsplit("/", 2)[-2] for o in outs})


MUTATING = ("p1_db_harvest", "benchmark_loader", "run_block", "macro_discovery", "nav_discovery", "run_pipeline", "p3_build_portfolio")


# ================================================================ P1P2_runCycle

def test_the_p1p2_cycle_audits_each_domain_where_its_last_writer_ends(tree):
    r = tree.run("P1P2_runCycle", *CYCLE)
    assert r.returncode == 0, r.stdout + r.stderr
    pre_p2, pre_costs, post_p2, post_costs = _pre_post(tree)
    assert len(pre_p2) == len(pre_costs) == len(post_p2) == len(post_costs) == 1, _audits(tree)
    # PRE: frozen before anything mutates, in the existing order (p2, then costs)
    first_mut = min(tree.tools().index(t) for t in MUTATING if t in tree.tools())
    assert _index_of(tree, pre_p2[0]) < _index_of(tree, pre_costs[0]) < first_mut or \
        _index_of(tree, pre_costs[0]) < first_mut
    # POST P1: right after the P1 pipeline (step 2) and BEFORE P2 starts; POST P2: after the P2 indicators
    ln = tree.lines()
    p1_end = max(i for i, x in enumerate(ln) if x.startswith(("run_block", "fund_family_builder", "export_p1")))
    assert p1_end < _index_of(tree, post_costs[0]) < _at(tree, "nav_discovery")
    assert _index_of(tree, post_p2[0]) > max(i for i, x in enumerate(ln) if x.startswith(("run_pipeline", "export_metrics")))
    # drift is always measured against this cycle's own PRE
    assert _cmp(post_costs[0]) == _rid(pre_costs[0]) and _cmp(post_p2[0]) == _rid(pre_p2[0])
    assert all("--persist" in x and "--mode report" in x for x in _audits(tree))


def test_the_pre_and_post_benchmark_audit_json_are_kept_per_run(tree):
    assert tree.run("P1P2_runCycle", *CYCLE).returncode == 0
    bench = [ln for ln in tree.lines() if ln.startswith("audit_benchmark_consistency")]
    assert len(bench) == 2, bench                                              # PRE and POST (the PRE used to skip it)
    assert "benchmark_audit_pre_" in bench[0] and "benchmark_audit_post_" in bench[1], bench
    out = tree.audit / "P1"
    stamped = sorted(p.name for p in out.glob("benchmark_audit_*_costs.json"))
    assert len(stamped) == 2 and (out / "benchmark_audit_findings.json").exists(), list(out.glob("*"))


@pytest.mark.parametrize("rc", ["1", "2"])                                     # 2 = INCOMPLETE: a mandatory check could not run
@pytest.mark.parametrize("domain", ["p2", "costs"])
def test_an_invalid_pre_audit_blocks_every_mutating_step_with_rc_107(tree, domain, rc):
    r = tree.run("P1P2_runCycle", *CYCLE, STUB_FAIL_AUDIT=f"pre:{domain}:{rc}")
    assert r.returncode == 107, r.stdout + r.stderr
    assert not [t for t in MUTATING if t in tree.tools()], tree.tools()
    assert tree.state()["LAST_RESULT"] == "FAILED" and tree.state()["FAILED_STEP"] == "1"
    r2 = tree.run("P1P2_runCycle", *CYCLE, "--from", "1")                      # the failure is resumable: a new PRE is taken
    assert r2.returncode == 0, r2.stdout + r2.stderr


def test_allow_no_baseline_warns_and_goes_on(tree):
    r = tree.run("P1P2_runCycle", *CYCLE, "--allow-no-baseline", STUB_FAIL_AUDIT="pre:p2:2")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "--allow-no-baseline" in r.stdout and "run_pipeline" in tree.tools()


def test_defer_post_audit_leaves_the_p2_post_audit_to_whoever_builds_the_draft(tree):
    r = tree.run("P1P2_runCycle", *CYCLE, "--defer-post-audit")
    assert r.returncode == 0, r.stdout + r.stderr
    pre_p2, pre_costs, post_p2, post_costs = _pre_post(tree)
    assert len(post_costs) == 1 and post_p2 == [] and len(pre_p2) == 1, _audits(tree)


def test_a_resume_after_step_2_does_not_repeat_the_p1_post_audit(tree):
    assert tree.run("P1P2_runCycle", *CYCLE, STUB_RC_NAV_DISCOVERY="7").returncode == 7
    n_before = len(_kind(tree, "post", "costs"))
    assert n_before == 1                                                       # taken when step 2 finished, before step 3 failed
    assert tree.run("P1P2_runCycle", *CYCLE, "--from", "3").returncode == 0
    assert len(_kind(tree, "post", "costs")) == 1 and len(_kind(tree, "post", "p2")) == 1


# ================================================================ P1P2P3_runCycle

def test_the_master_flow_audits_p2_once_after_the_draft_against_the_cycles_pre(tree):
    r = tree.run("P1P2P3_runCycle", *P123)
    assert r.returncode == 0, r.stdout + r.stderr
    pre_p2, pre_costs, post_p2, post_costs = _pre_post(tree)
    assert len(post_p2) == 1 and len(post_costs) == 1, _audits(tree)
    b = _at(tree, "p3_build_portfolio")
    assert b < _index_of(tree, post_p2[0]) < _at(tree, "generate_report", after=b)       # after the DRAFT, before the report
    assert _index_of(tree, post_costs[0]) < _at(tree, "nav_discovery")                    # P1 post at the P1 boundary
    assert _cmp(post_p2[0]) == _rid(pre_p2[0])                                            # the same run is both drift and evidence
    assert "--audit-compare-to" not in " ".join(x for x in tree.lines() if x.startswith("p3_build_portfolio"))


def test_the_master_flow_still_audits_when_p3_dry_run_builds_nothing(tree):
    r = tree.run("P1P2P3_runCycle", *P123, "--p3-dry-run")
    assert r.returncode == 0, r.stdout + r.stderr
    pre_p2, _, post_p2, _ = _pre_post(tree)
    assert len(post_p2) == 1 and _cmp(post_p2[0]) == _rid(pre_p2[0])                      # the deferred POST still happens (backup)
    assert _index_of(tree, post_p2[0]) > _at(tree, "p3_build_portfolio")


def test_the_master_flow_audits_p2_even_if_the_draft_build_fails(tree):
    r = tree.run("P1P2P3_runCycle", *P123, STUB_RC_P3_BUILD_PORTFOLIO="2")
    assert r.returncode == 2, r.stdout + r.stderr
    assert len(_kind(tree, "post", "p2")) == 1 and "generate_report" not in tree.tools()


def test_the_master_flow_audits_nothing_more_when_the_cycle_itself_fails(tree):
    r = tree.run("P1P2P3_runCycle", *P123, STUB_RC_NAV_DISCOVERY="7")
    assert r.returncode == 7, r.stdout + r.stderr
    assert len(_kind(tree, "post", "costs")) == 1 and _kind(tree, "post", "p2") == []   # P1 finished; P2 never did
    assert "p3_build_portfolio" not in tree.tools()


def test_only_p3_compares_against_the_last_cycles_pre(tree):
    assert tree.run("P1P2P3_runCycle", *P123).returncode == 0
    pre_p2 = _rid(_kind(tree, "pre", "p2")[0])
    assert tree.run("P1P2P3_runCycle", *P123, "--only-p3").returncode == 0
    posts = _kind(tree, "post", "p2")
    assert len(posts) == 2 and _cmp(posts[1]) == pre_p2


def test_an_invalid_pre_blocks_the_master_flow_with_107(tree):
    r = tree.run("P1P2P3_runCycle", *P123, STUB_FAIL_AUDIT="pre:p2:2")
    assert r.returncode == 107, r.stdout + r.stderr
    assert not [t for t in MUTATING if t in tree.tools()]


# ================================================================ P2P3_runCycle

def test_p2p3_takes_a_pre_before_p2_and_one_post_after_the_draft(tree):
    r = tree.run("P2P3_runCycle", "--no-pause")
    assert r.returncode == 0, r.stdout + r.stderr
    pre_p2, pre_costs, post_p2, post_costs = _pre_post(tree)
    assert len(pre_p2) == 1 and len(post_p2) == 1 and pre_costs == [] and post_costs == [], _audits(tree)
    assert _index_of(tree, pre_p2[0]) < _at(tree, "run_pipeline") < _at(tree, "p3_build_portfolio") < _index_of(tree, post_p2[0])
    assert _cmp(post_p2[0]) == _rid(pre_p2[0])
    assert len(_audits(tree)) == 2                                                        # one PRE, one POST: nothing repeated


def test_p2p3_refuses_to_recompute_without_a_valid_pre(tree):
    r = tree.run("P2P3_runCycle", "--no-pause", STUB_FAIL_AUDIT="pre:p2:2")
    assert r.returncode == 107, r.stdout + r.stderr
    assert "run_pipeline" not in tree.tools() and "p3_build_portfolio" not in tree.tools()


def test_p2p3_allow_no_baseline_goes_on_and_the_post_has_nothing_to_compare_to(tree):
    r = tree.run("P2P3_runCycle", "--no-pause", "--allow-no-baseline", STUB_FAIL_AUDIT="pre:p2:2")
    assert r.returncode == 0, r.stdout + r.stderr
    post = _kind(tree, "post", "p2")
    assert len(post) == 1 and _cmp(post[0]) is None


# ================================================================ P3_buildPortfolio --audit-compare-to

def test_the_build_audit_compares_against_the_given_run_and_does_not_forward_the_option(tree):
    r = tree.run("P3_buildPortfolio", "--audit-compare-to", "pre_X_p2")
    assert r.returncode == 0, r.stdout + r.stderr
    post = _kind(tree, "post", "p2")
    assert len(post) == 1 and _cmp(post[0]) == "pre_X_p2"
    build = next(x for x in tree.lines() if x.startswith("p3_build_portfolio"))
    assert "audit-compare-to" not in build and "pre_X_p2" not in build


def test_the_build_audit_without_the_option_has_no_compare_to(tree):
    assert tree.run("P3_buildPortfolio").returncode == 0
    assert _cmp(_kind(tree, "post", "p2")[0]) is None


def test_audit_compare_to_without_a_value_is_a_usage_error_that_runs_nothing(tree):
    r = tree.run("P3_buildPortfolio", "--audit-compare-to")
    assert r.returncode == 100 and tree.tools() == []


# ================================================================ leaves do not audit unless asked

def test_the_leaf_launchers_do_not_audit_by_default(tree):
    assert tree.run("P2_calculateIndicators", "--no-export").returncode == 0
    assert tree.run("P1_discoverAllFunds", "--no-export").returncode == 0
    assert _audits(tree) == [] and "audit_benchmark_consistency" not in tree.tools()


def test_the_leaf_launchers_audit_on_request_in_report_mode_without_persisting(tree):
    assert tree.run("P2_calculateIndicators", "--no-export", "--audit").returncode == 0
    assert tree.run("P1_discoverAllFunds", "--no-export", "--audit").returncode == 0
    a = _audits(tree)
    assert sorted(_dom(x) for x in a) == ["costs", "p2"] and not any("--persist" in x for x in a), a


def test_no_audit_is_still_accepted_by_the_leaves(tree):
    assert tree.run("P2_calculateIndicators", "--no-audit", "--no-export").returncode == 0
    assert tree.run("P1_discoverAllFunds", "--no-audit", "--no-export").returncode == 0
    assert _audits(tree) == []


# ================================================================ P1_runCycle

def test_p1_runcycle_is_the_p1_subset_with_pre_and_post(tree):
    r = tree.run("P1_runCycle", *CYCLE)
    assert r.returncode == 0, r.stdout + r.stderr
    t = tree.tools()
    assert "nav_discovery" not in t and "run_pipeline" not in t and "p3_build_portfolio" not in t   # nothing of P2 or P3
    pre_p2, pre_costs, post_p2, post_costs = _pre_post(tree)
    assert pre_p2 == [] and post_p2 == [] and len(pre_costs) == len(post_costs) == 1, _audits(tree)
    assert _index_of(tree, pre_costs[0]) < _at(tree, "p1_db_harvest") < _at(tree, "benchmark_loader") < _at(tree, "run_block") \
        < _index_of(tree, post_costs[0])
    assert _cmp(post_costs[0]) == _rid(pre_costs[0])
    assert not (tree.logs / "P1P2_runCycle.state").exists()                   # a partial run is not a P1+P2 cycle


def test_p1_runcycle_invalid_pre_is_107_and_a_failed_step_keeps_its_rc_and_skips_the_post(tree):
    r = tree.run("P1_runCycle", *CYCLE, STUB_FAIL_AUDIT="pre:costs:1")
    assert r.returncode == 107 and not [t for t in MUTATING if t in tree.tools()], r.stdout + r.stderr
    r = tree.run("P1_runCycle", *CYCLE, STUB_RC_HARVEST_GATE="5")
    assert r.returncode == 5, r.stdout + r.stderr
    assert "benchmark_loader" not in tree.tools() and _kind(tree, "post", "costs") == []


def test_p1_runcycle_options_and_arguments(tree):
    r = tree.run("P1_runCycle", *CYCLE, "--no-harvest", "--benchmarks-load", "--no-export")
    assert r.returncode == 0, r.stdout + r.stderr
    ln = "\n".join(tree.lines())
    assert "p1_db_harvest" not in ln and "benchmark_loader --mode load" in ln and "export_p1" not in ln
    assert tree.run("P1_runCycle", "--frobnicate").returncode == 100
    assert tree.run("P1_runCycle", "--harvest-limit", "5", "--no-harvest").returncode == 100
    assert tree.run("P1_runCycle", "--harvest-limit", "5").returncode == 100              # needs --harvest-sync


# ================================================================ P2_runCycle

def test_p2_runcycle_is_the_p2_subset_with_pre_and_post_and_a_beta_check(tree):
    r = tree.run("P2_runCycle", *CYCLE)
    assert r.returncode == 0, r.stdout + r.stderr
    t = tree.tools()
    assert "p1_db_harvest" not in t and "run_block" not in t and "p3_build_portfolio" not in t   # nothing of P1 or P3
    pre_p2, pre_costs, post_p2, post_costs = _pre_post(tree)
    assert len(pre_p2) == len(post_p2) == 1 and pre_costs == [] and post_costs == [], _audits(tree)
    ln = tree.lines()
    assert _index_of(tree, pre_p2[0]) < _at(tree, "beta_shift_audit --snapshot") < _at(tree, "nav_discovery") \
        < _at(tree, "run_pipeline") < _at(tree, "beta_shift_audit --compare") < _index_of(tree, post_p2[0])
    assert _cmp(post_p2[0]) == _rid(pre_p2[0])
    assert not (tree.logs / "P1P2_runCycle.state").exists()
    assert ln and tree.power_calls() == ["0", "45"]                                        # saves and restores the real standby


def test_p2_runcycle_invalid_pre_is_107_and_a_failed_step_skips_the_post(tree):
    r = tree.run("P2_runCycle", *CYCLE, STUB_FAIL_AUDIT="pre:p2:2")
    assert r.returncode == 107 and "nav_discovery" not in tree.tools(), r.stdout + r.stderr
    r = tree.run("P2_runCycle", *CYCLE, STUB_RC_RUN_PIPELINE="4")
    assert r.returncode == 4, r.stdout + r.stderr
    assert _kind(tree, "post", "p2") == []


def test_p2_runcycle_forwards_its_options_to_the_steps(tree):
    r = tree.run("P2_runCycle", *CYCLE, "--skip-macro", "--workers", "3", "--force", "--no-export", "--", "--isin", '"A,B"')
    assert r.returncode == 0, r.stdout + r.stderr
    ln = "\n".join(tree.lines()).replace("  ", " ")
    assert "macro_discovery" not in ln and "--mode load --desde 2000-01-01 --workers 3" in ln
    assert "run_pipeline --force --isin A,B" in ln and "export_metrics" not in ln
    assert tree.run("P2_runCycle", "--workers", "0").returncode == 100


# ================================================================ P3_runCycle

def test_p3_runcycle_builds_audits_after_the_draft_and_reports_it(tree):
    r = tree.run("P3_runCycle")
    assert r.returncode == 0, r.stdout + r.stderr
    b = _at(tree, "p3_build_portfolio")
    post = _kind(tree, "post", "p2")
    assert len(post) == 1 and b < _index_of(tree, post[0]) < _at(tree, "generate_report", after=b)
    assert _kind(tree, "pre", "p2") == [] and _cmp(post[0]) is None                        # P3 does not change P2 data: no PRE, no drift
    assert "--draft" in r.stdout or "generate_report" in tree.tools()


def test_p3_runcycle_stale_inputs_stop_it_with_rc_2_and_dry_run_persists_nothing(tree):
    r = tree.run("P3_runCycle", STUB_RC_P3_BUILD_PORTFOLIO="2")
    assert r.returncode == 2 and "generate_report" not in tree.tools() and _audits(tree) == [], r.stdout + r.stderr
    r = tree.run("P3_runCycle", "--p3-dry-run")
    assert r.returncode == 0 and "generate_report" not in tree.tools() and _audits(tree) == [], r.stdout + r.stderr


def test_p3_runcycle_arguments(tree):
    assert tree.run("P3_runCycle", "--frobnicate").returncode == 100
    assert tree.run("P3_runCycle", "--scenario").returncode == 100
    assert tree.run("P3_runCycle", "--scenario", "a b").returncode == 100


# ================================================================ audit files: one folder per project, inside the repo

def test_audit_files_go_to_one_folder_per_project_under_the_audit_root(tree):
    assert tree.run("P1P2_runCycle", *CYCLE).returncode == 0
    p1 = sorted(p.name for p in (tree.audit / "P1").glob("*"))
    p2 = sorted(p.name for p in (tree.audit / "P2").glob("*"))
    assert any(n.startswith("benchmark_audit_pre_") for n in p1) and "benchmark_audit_findings.json" in p1, p1
    assert any(n.startswith("macro_betas_pre_") for n in p2), p2
    assert _compare_out_dirs(tree) == ["P2"], tree.lines()                                       # the outliers --out too
    assert not [p for p in tree.audit.iterdir() if p.is_file()], list(tree.audit.iterdir())   # nothing loose in the root
    assert not any(n.startswith("macro_betas") or n.startswith("beta_shift") for n in p1)       # and no cross-project files
    assert not any(n.startswith("benchmark_audit") for n in p2)


@pytest.mark.parametrize("name,args", [("P2_runCycle", CYCLE), ("P2P3_runCycle", ["--no-pause"])])
def test_the_p2_orchestrators_keep_their_beta_files_in_the_p2_folder(tree, name, args):
    assert tree.run(name, *args).returncode == 0
    p2 = sorted(p.name for p in (tree.audit / "P2").glob("*.csv"))
    assert any(n.startswith("macro_betas_") for n in p2), p2
    assert _compare_out_dirs(tree) == ["P2"], tree.lines()
    assert not (tree.audit / "P1").exists()


def test_no_launcher_writes_audit_files_outside_the_repo():
    bad = [p.name for p in (e2e.REAL / "scripts" / "launch").rglob("*.bat")
           if "data\\fondos\\audit" in "\n".join(ln for ln in p.read_text(errors="replace").splitlines() if not ln.lstrip().startswith("::"))]
    assert not bad, bad


def test_the_python_registry_and_the_launchers_agree_on_the_audit_folders():
    from shared import config
    assert config.AUDIT_DIRS["p1"] == config.AUDIT_DIRS["p1"].parent / "P1" and config.AUDIT_DIRS["p2"].name == "P2"
    assert config.AUDIT_DIRS["p1"].parent == config.AUDIT_DIRS["p2"].parent == config._ROOT / "out" / "audit"
    common = (e2e.REAL / "scripts" / "launch" / "lib" / "common.bat").read_text(errors="replace")
    assert 'set "AUDIT_P1_DIR=%AUDIT_ROOT%\\P1"' in common and 'set "AUDIT_P2_DIR=%AUDIT_ROOT%\\P2"' in common
    assert 'set "AUDIT_ROOT=%ROOT%\\out\\audit"' in common


# ================================================================ the family shares one lock

@pytest.mark.parametrize("name,args", [("P1_runCycle", CYCLE), ("P2_runCycle", CYCLE), ("P3_runCycle", [])])
def test_the_standalone_cyclers_share_the_single_instance_lock(tree, name, args):
    first = tree.popen("P1P2_runCycle", *CYCLE, STUB_SLEEP_BENCHMARK_LOADER="6")
    try:
        deadline = time.time() + 60
        while "benchmark_loader" not in tree.tools():
            assert first.poll() is None and time.time() < deadline, "the first cycle did not reach step 1"
            time.sleep(0.3)
        r = tree.run(name, *args)
        assert r.returncode == 105, r.stdout + r.stderr
        assert "Otro ciclo" in r.stdout or "Otra instancia" in r.stdout
    finally:
        first.communicate(timeout=240)
    assert first.returncode == 0
