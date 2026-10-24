"""
tests/test_batch_launchers_e2e.py -- the REAL launchers, end to end, against simulated tools (doc/reglas/NORMAS_BATCH.md §12).

A throwaway tree is built under tmp_path (its name contains a space on purpose): the real scripts/launch/*.bat,
lib/, and p1p2_state.py are COPIED in, and every tool they call (python scripts and modules, git-free) is a stub that
records its argv to a trace file and exits with a code taken from the environment. There is no database, no network and
no real `powercfg` (FONDOS_POWERCFG points to a recording stub). Because every path is derived from the launcher's own
location (NORMAS_BATCH.md §4), the copy can only ever run the copied tree: the incident of 2026-10-04, where running a
copy of a launcher with hard-coded paths started the REAL pipeline, cannot happen to these tests -- and
test_the_real_repo_is_never_touched proves it.

Windows only (cmd.exe).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

REAL = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the launchers are cmd.exe batch files")

_PY_STUB = '''import os, sys, time
name = os.path.splitext(os.path.basename(sys.argv[0]))[0]
with open(os.environ["STUB_TRACE"], "a") as f:
    f.write("%s %s\\n" % (name, " ".join(sys.argv[1:])))
args = sys.argv[1:]
if "--snapshot" in args:
    open(args[args.index("--snapshot") + 1], "w").write("isin,metric,value\\n")
secs = os.environ.get("STUB_SLEEP_" + name.upper())
if secs:
    time.sleep(float(secs))
verdict = os.environ.get("STUB_VERDICT_" + name.upper())
if verdict:
    print(verdict)
sys.exit(int(os.environ.get("STUB_RC_" + name.upper(), "0")))
'''

# every python tool the launchers run (script path or module path under the tree)
_PY_TOOLS = [
    "scripts/launch/mark_stale.py", "scripts/launch/harvest_gate.py", "scripts/launch/p3_build_portfolio.py",
    "scripts/launch/p3_freshness_check.py", "scripts/launch/p1p2_cycle_report.py",
    "scripts/audit/run_statistical_audit.py", "scripts/audit/beta_shift_audit.py",
    "scripts/audit/shadow_reconciliation.py", "proyecto1/tools/audit_benchmark_consistency.py",
    "proyecto1/run_block.py", "proyecto1/harvest/p1_db_harvest.py", "proyecto1/harvest/p1_kiid_sync.py",
    "proyecto1/core/fund_family_builder.py", "proyecto1/src/analysis/export_p1.py",
    "proyecto1/src/loaders/benchmark_loader.py", "proyecto2/src/discovery/macro_discovery.py",
    "proyecto2/src/discovery/nav_discovery.py", "proyecto2/src/analysis/export_metrics.py",
    "proyecto2/src/reports/rolling_dashboard.py", "scripts/diag/diag_cost_extraction.py",
    "scripts/launch/release_check.py", "scripts/audit/verify_fund_currency.py",
]

_POWERCFG_STUB = """@echo off
if /i "%~1"=="/query" (
    echo   Maximum: 0xffffffff
    echo   Current AC Power Setting Index: 0x%STUB_STANDBY_HEX%
    echo   Current DC Power Setting Index: 0x00000258
    exit /b 0
)
echo %* >> "%STUB_POWERCFG_LOG%"
exit /b 0
"""

_LAUNCHERS = ["_template", "P1P2_runCycle", "P2P3_runCycle", "P1P2P3_runCycle", "P1_refreshBenchmarks", "P1_discoverAllFunds",
              "P2_discoverLoadMetrics", "P2_calculateIndicators", "P1_harvestFunds", "P1_diagCost",
              "P1_audit", "P2_audit", "P1P2_auditStatistical", "P3_buildPortfolio", "P3_generateReport",
              "P1_runCycle", "P2_runCycle", "P3_runCycle",
              "P1_discoverAllFundsPlusCostDiag", "FND-0246", "FND-0246_V2"]


class Tree:
    def __init__(self, base: Path):
        self.root = base / "repo with space"
        self.launch = self.root / "scripts" / "launch"
        self.trace = base / "trace.txt"
        self.power = base / "powercfg.txt"
        self.audit = base / "audit"
        self.logs = self.root / "proyecto1" / "log"
        (self.launch / "lib").mkdir(parents=True)
        self.logs.mkdir(parents=True)
        for n in _LAUNCHERS:
            shutil.copy(REAL / "scripts" / "launch" / f"{n}.bat", self.launch)
        for f in (REAL / "scripts" / "launch" / "lib").iterdir():
            if f.is_file():                                   # not __pycache__
                shutil.copy(f, self.launch / "lib")
        shutil.copy(REAL / "scripts" / "launch" / "p1p2_state.py", self.launch)
        shutil.copy(REAL / "scripts" / "launch" / "p3_generate_report.py", self.launch)      # the real CLI over the stubbed report
        for rel in _PY_TOOLS:
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(_PY_STUB)
            q = p.parent
            while q != self.root:                                # make every parent an importable package
                (q / "__init__.py").touch()
                q = q.parent
        (self.root / "proyecto2/src/pipeline").mkdir(parents=True)
        (self.root / "proyecto2/src/pipeline/run_pipeline.py").write_text(
            _PY_STUB + '\nCALC_VERSION: str = "20991231"  # stub\n')
        (self.root / "proyecto2/src/pipeline/__init__.py").touch()
        (self.root / "shared").mkdir()
        (self.root / "shared/__init__.py").touch()
        (self.root / "shared/config.py").write_text("")
        shutil.copy(REAL / "shared" / "env_guard.py", self.root / "shared")        # the environment guard (RC 106)
        (self.root / "shared/governance").mkdir()                                     # pure stdlib: the real one (FND-0263, P#11):
        (self.root / "shared/governance/__init__.py").touch()                         # p1p2_state calc-version reads CALC_VERSION
        shutil.copy(REAL / "shared" / "governance" / "run_identity.py", self.root / "shared/governance")   # through it
        (self.root / "shared/db.py").write_text(
            "import os\n\nclass _Cur:\n    def __init__(self, sql=''): self.sql = sql\n"
            "    def fetchone(self): return (1,)\n"
            "    def fetchall(self):\n"        # the newest-DRAFT query (scenario_lifecycle.latest_drafts) finds one unless STUB_NO_DRAFT
            "        if \"status = 'DRAFT'\" in self.sql and not os.environ.get('STUB_NO_DRAFT'): return [('cartera_stub_b1',)]\n"
            "        return []\n\n"
            "class _Conn:\n    def execute(self, sql='', *a, **k): return _Cur(sql)\n    def close(self): pass\n\n"
            "def get_connection():\n    if os.environ.get('STUB_DB_DOWN'):\n        raise ConnectionError('db down')\n"
            "    return _Conn()\n")
        (self.root / "proyecto3/src").mkdir(parents=True)
        (self.root / "proyecto3/__init__.py").touch()
        (self.root / "proyecto3/src/__init__.py").touch()
        shutil.copy(REAL / "proyecto3" / "src" / "scenario_lifecycle.py", self.root / "proyecto3/src")   # pure stdlib: the real one
        (self.root / "proyecto3/src/monthly_report.py").write_text(       # records itself in the trace: a launcher that ran it ran a real report
            "import os\n\ndef generate_report(c, output_dir=None, scenario_id=None):\n"
            "    with open(os.environ['STUB_TRACE'], 'a') as f:\n"
            "        f.write('generate_report %s %s\\n' % (output_dir, scenario_id))\n"
            "    if os.environ.get('STUB_NO_SCENARIO'):\n"
            "        from proyecto3.src.scenario_lifecycle import NoProductionScenario\n"
            "        raise NoProductionScenario('sin escenario en PRODUCCION')\n"
            "    return 'report'\n")
        (base / "bin").mkdir()
        (base / "bin" / "powercfg.bat").write_text(_POWERCFG_STUB)

    def bat(self, name: str) -> Path:
        return self.launch / f"{name}.bat"

    def env(self, **extra) -> dict:
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("STUB_", "FONDOS_", "FLAG_", "OPT_", "P1P2_"))}
        env.update(FONDOS_PYTHON=sys.executable, FONDOS_POWERCFG=str(self.trace.parent / "bin" / "powercfg.bat"),
                   STUB_TRACE=str(self.trace), STUB_POWERCFG_LOG=str(self.power), STUB_STANDBY_HEX="00000a8c",   # 45 min
                   P1P2_AUDIT_DIR=str(self.audit), PYTHONIOENCODING="utf-8")
        env.update({k: str(v) for k, v in extra.items()})
        return env

    def _cmd(self, name: str, args) -> str:
        """`cmd /s /c "<command>"`: with the launcher path AND a quoted argument on the line, a plain `cmd /c`
        strips the wrong quotes (the same trap as NORMAS_BATCH.md section 11, for `for /f`)."""
        quoted = [a if a.startswith('"') or " " not in a else f'"{a}"' for a in args]
        return 'cmd /s /c "' + " ".join([f'"{self.bat(name)}"', *quoted]) + '"'

    def run(self, name: str, *args: str, timeout=240, **env):
        return subprocess.run(self._cmd(name, args), env=self.env(**env), cwd=str(self.root),
                              capture_output=True, text=True, errors="replace", timeout=timeout,
                              stdin=subprocess.DEVNULL)

    def popen(self, name: str, *args: str, **env):
        return subprocess.Popen(self._cmd(name, args), env=self.env(**env), cwd=str(self.root),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace",
                                stdin=subprocess.DEVNULL)

    def tools(self) -> list:
        """Names of the tools that ran, in order (the first word of each trace line)."""
        return [ln.split()[0] for ln in self.trace.read_text().splitlines() if ln.strip()] if self.trace.exists() else []

    def lines(self) -> list:
        return self.trace.read_text().splitlines() if self.trace.exists() else []

    def power_calls(self) -> list:
        return [ln.split()[-1] for ln in self.power.read_text().splitlines() if "standby-timeout-ac" in ln] \
            if self.power.exists() else []

    def state(self) -> dict:
        f = self.logs / "P1P2_runCycle.state"
        return dict(ln.split("=", 1) for ln in f.read_text().splitlines() if "=" in ln) if f.exists() else {}


@pytest.fixture()
def tree(tmp_path):
    return Tree(tmp_path)


CYCLE = ["--skip-preflight"]


# ─── the happy path ──────────────────────────────────────────────────────────────────────────────

def test_a_normal_cycle_runs_every_phase_in_order_and_exits_0(tree):
    r = tree.run("P1P2_runCycle", STUB_VERDICT_P3_FRESHNESS_CHECK="P3 aceptaria estos datos")   # preflight included
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[preflight] OK   database reachable" in r.stdout
    t = tree.tools()
    order = ["run_statistical_audit", "beta_shift_audit", "p1_db_harvest", "harvest_gate", "p1_kiid_sync",
             "benchmark_loader"]
    pos = [t.index(x) for x in order]
    assert pos == sorted(pos), t                                       # baseline < snapshot < harvest < step 1
    assert t.index("benchmark_loader") < t.index("mark_stale") < t.index("run_pipeline") < t.index("p3_freshness_check")
    assert tree.state()["LAST_RESULT"] == "OK" and tree.state()["FAILED_STEP"] == "0"
    assert "P3 aceptaria" in r.stdout


def test_option_forwarding_reaches_the_right_tool(tree):
    r = tree.run("P1P2_runCycle", *CYCLE, "--benchmarks-load", "--no-export", "--skip-macro", "--workers", "3",
                 "--force", "--harvest-sync", "--harvest-limit", "5", "--shadow", "2", "--benchmark-gaps",
                 "--dashboard", "--diag-cost", "--", "--isin", '"A,B"')
    assert r.returncode == 0, r.stdout + r.stderr
    ln = "\n".join(tree.lines())
    assert "benchmark_loader --mode load" in ln                         # PASO 1 recarga completa
    assert "export_p1" not in ln and "export_metrics" not in ln          # --no-export
    assert "macro_discovery" not in ln                                   # --skip-macro
    assert "--mode load --desde 2000-01-01 --workers 3" in ln            # PASO 3
    assert "run_pipeline --force --isin A,B" in ln.replace("  ", " ")      # PASO 4 (argv has no quotes)
    assert "p1_kiid_sync --sync --limit 5" in ln                         # harvest
    assert "shadow_reconciliation --stratified-sample 2" in ln
    assert "benchmark_loader --mode gaps" in ln and "rolling_dashboard" in ln and "diag_cost_extraction" in ln


def test_a_directory_with_a_space_in_its_name_is_fine_and_the_real_repo_is_never_touched(tree):
    """Every path is derived from the launcher's own location: only the copied tree can run."""
    watched = [REAL / "proyecto1" / "log", REAL / "proyecto2" / "log", REAL / "out"]
    before = {w: sorted(p.name for p in w.glob("*")) if w.exists() else None for w in watched}
    r = tree.run("P1P2_runCycle", *CYCLE)
    assert r.returncode == 0, r.stdout + r.stderr
    assert str(tree.root) in r.stdout and " " in str(tree.root)
    after = {w: sorted(p.name for p in w.glob("*")) if w.exists() else None for w in watched}
    assert before == after


# ─── failures keep their own code and the resume guard works ────────────────────────────────────

def test_a_step_failure_propagates_its_rc_and_blocks_the_following_steps(tree):
    r = tree.run("P1P2_runCycle", *CYCLE, STUB_RC_NAV_DISCOVERY="7")
    assert r.returncode == 7, r.stdout + r.stderr
    assert "run_pipeline" not in tree.tools()
    assert tree.state()["LAST_RESULT"] == "FAILED" and tree.state()["FAILED_STEP"] == "3"


def test_resume_from_the_failed_step_skips_what_ran_and_reuses_baseline_and_snapshot(tree):
    assert tree.run("P1P2_runCycle", *CYCLE, STUB_RC_NAV_DISCOVERY="7").returncode == 7
    baseline = tree.state()["BASELINE_RUN_ID"]
    tree.trace.unlink()
    r = tree.run("P1P2_runCycle", *CYCLE, "--from", "3")
    assert r.returncode == 0, r.stdout + r.stderr
    t = tree.tools()
    assert "benchmark_loader" not in t and "p1_db_harvest" not in t and "mark_stale" not in t
    assert "nav_discovery" in t and "run_pipeline" in t
    assert any(baseline in ln for ln in tree.lines() if ln.startswith("beta_shift_audit --compare")) or \
        (tree.audit / "P2" / f"macro_betas_{baseline}.csv").exists()
    assert tree.state()["LAST_RESULT"] == "OK"


def test_a_tool_code_is_never_confused_with_a_launcher_code(tree):
    """The harvest gate's RC 5 used to be indistinguishable from 'interpreter not found' (RC 5)."""
    r = tree.run("P1P2_runCycle", *CYCLE, STUB_RC_HARVEST_GATE="5")
    assert r.returncode == 5 and not 100 <= r.returncode <= 199
    assert tree.state()["FAILED_STEP"] == "1" and "benchmark_loader" not in tree.tools()


def test_the_launchers_own_codes(tree):
    assert tree.run("P1P2_runCycle", "--frobnicate").returncode == 100
    assert tree.run("P1P2_runCycle", "--workers", "0").returncode == 100
    assert tree.run("P1P2_runCycle", "--help").returncode == 0
    assert tree.run("P1P2_runCycle", "--from", "3").returncode == 102           # no state: refused, fails closed
    assert tree.run("P1P2_runCycle", *CYCLE, FONDOS_PYTHON=str(tree.root / "no_python.exe")).returncode == 101
    for sub in ("P1_discoverAllFunds", "P2_discoverLoadMetrics", "P1_harvestFunds"):   # P2_calculateIndicators forwards unknown args
        assert tree.run(sub, "--help").returncode == 0, sub
    assert tree.run("P1_discoverAllFunds", "--nope").returncode == 100
    assert tree.tools() == []                                                    # none of the above ran a tool


# ─── FND-0244: the family-nature refresh runs after the family builder, with no ISIN passed ─────────

def test_p1_discover_all_funds_refreshes_the_family_corrected_funds_after_the_builder(tree):
    r = tree.run("P1_discoverAllFunds", "--no-audit", "--no-export")
    assert r.returncode == 0, r.stdout + r.stderr
    ln = tree.lines()
    first = next(i for i, x in enumerate(ln) if x.startswith("run_block --nature-first --master-db"))
    builder = next(i for i, x in enumerate(ln) if x.startswith("fund_family_builder"))
    refresh = next(i for i, x in enumerate(ln) if x.startswith("run_block --family-nature-refresh --master-db"))
    assert first < builder < refresh, ln
    assert "--list-isin" not in ln[refresh] and "--sample" not in ln[refresh]            # the set is selected, never passed


def test_a_failing_refresh_step_makes_the_launcher_fail(tree):
    r = tree.run("P1_discoverAllFunds", "--no-audit", "--no-export", STUB_RC_RUN_BLOCK="7")
    assert r.returncode == 7, r.stdout + r.stderr
    log = max(tree.logs.glob("log_pipeline_*.log"), key=lambda f: f.stat().st_mtime).read_text(errors="replace")
    assert "RC1=7" in log and "RC2R=7" in log                  # both run_block steps report their own code in the footer


# ─── --help / -h must never reach a tool (2026-10-08 incident: `--help` over the real launchers started real runs) ───

@pytest.mark.parametrize("flag", ["--help", "-h"])
@pytest.mark.parametrize("name", _LAUNCHERS)
def test_help_prints_usage_and_exits_0_without_running_any_tool(tree, name, flag):
    r = tree.run(name, flag)
    assert r.returncode == 0, (name, flag, r.stdout + r.stderr)
    assert tree.tools() == [], (name, flag, tree.tools())                    # no business logic, no orchestrated tool (generate_report included)
    assert not list(tree.logs.glob("log_*")), (name, flag, [f.name for f in tree.logs.glob("log_*")])    # and no run log was opened
    for sub in ("proyecto2/log", "proyecto3/log", "out", flag):              # nor any directory a real run would have created
        assert not (tree.root / sub).exists(), (name, flag, sub)
    assert r.stdout.strip(), (name, flag, "empty usage")


# ─── the legacy block path is gone: P1 + cost diag is an orchestrator of two existing launchers ───

def test_p1_plus_cost_diag_runs_the_nature_first_launcher_then_the_diagnostic_and_never_the_block_path(tree):
    r = tree.run("P1_discoverAllFundsPlusCostDiag", "--no-audit", "--no-export")
    assert r.returncode == 0, r.stdout + r.stderr
    ln = tree.lines()
    run_block = [x for x in ln if x.startswith("run_block")]
    assert run_block and all("--nature-first --master-db" in x or "--family-nature-refresh --master-db" in x for x in run_block), run_block
    assert not any("--block" in x or "--master " in x or "--master\t" in x for x in ln), ln          # no legacy block, no Excel master
    t = tree.tools()
    assert t.index("mark_stale") < t.index("fund_family_builder") < t.index("diag_cost_extraction"), t     # P1 first, the diagnostic last
    assert "--no-audit" not in " ".join(ln) and "export_p1" not in t                                   # the options reached P1_discoverAllFunds


def test_p1_plus_cost_diag_returns_the_first_failing_rc_and_still_runs_the_diagnostic(tree):
    r = tree.run("P1_discoverAllFundsPlusCostDiag", "--no-audit", "--no-export", STUB_RC_FUND_FAMILY_BUILDER="7")
    assert r.returncode == 7, r.stdout + r.stderr
    assert "diag_cost_extraction" in tree.tools()                                                      # every phase runs, as before
    r = tree.run("P1_discoverAllFundsPlusCostDiag", "--no-audit", "--no-export", STUB_RC_DIAG_COST_EXTRACTION="9")
    assert r.returncode == 9                                                                           # P1 fine: the diagnostic's code


def test_p1_plus_cost_diag_rejects_an_unknown_option_before_running_anything(tree):
    r = tree.run("P1_discoverAllFundsPlusCostDiag", "--frobnicate")
    assert r.returncode == 100 and "run_block" not in tree.tools() and "mark_stale" not in tree.tools()


# ─── environment guard: a blocked PostgreSQL driver is RC 106 everywhere, before any tool runs ──────

BLOCKED = {"FONDOS_DB_DRIVER_MODULE": "no_such_driver_module_xyz"}      # same ImportError path as a blocked DLL


@pytest.mark.parametrize("name", _LAUNCHERS)
def test_a_blocked_driver_stops_every_launcher_with_106_before_any_tool(tree, name):
    r = tree.run(name, "--help", **BLOCKED)
    assert r.returncode == 106, (name, r.stdout, r.stderr)
    assert "RC 106" in r.stderr and tree.tools() == []
    assert not (tree.logs / "env_driver_ok").exists()                       # a blocked environment is never cached
    assert "launcher=" + f"{name}.bat" in (tree.logs / "env_blocked.log").read_text()


def test_the_interpreter_missing_is_still_101_not_106(tree):
    r = tree.run("P1P2_runCycle", "--help", FONDOS_PYTHON=str(tree.root / "no_python.exe"), **BLOCKED)
    assert r.returncode == 101


def test_the_guard_is_checked_once_per_process_tree_and_cached_for_ten_minutes(tree):
    assert tree.run("P1_discoverAllFunds", "--help").returncode == 0
    marker = tree.logs / "env_driver_ok"
    assert marker.exists()
    # inside the window the marker is honoured (cost optimisation; the python layer still does the real import)
    assert tree.run("P1_discoverAllFunds", "--help", **BLOCKED).returncode == 0
    # expired marker -> the real check runs again and reports the block
    interp, mtime, _ = marker.read_text().rsplit("|", 2)
    marker.write_text(f"{interp}|{mtime}|1")
    assert tree.run("P1_discoverAllFunds", "--help", **BLOCKED).returncode == 106
    assert not marker.exists()
    # a launcher nested under an owner skips the check altogether
    assert tree.run("P1_discoverAllFunds", "--help", FONDOS_DB_DRIVER_OK="1", **BLOCKED).returncode == 0


def test_the_preflight_reports_106_when_the_driver_is_blocked(tree):
    r = subprocess.run([sys.executable, str(tree.launch / "p1p2_state.py"), "preflight"],
                       env=tree.env(**BLOCKED), cwd=str(tree.root), capture_output=True, text=True, errors="replace")
    assert r.returncode == 106 and "PostgreSQL driver cannot be loaded" in r.stdout


def test_a_dead_database_aborts_in_preflight_without_touching_the_resume_state(tree):
    assert tree.run("P1P2_runCycle", *CYCLE, STUB_RC_NAV_DISCOVERY="7").returncode == 7
    before = tree.state()
    tree.trace.unlink()
    r = tree.run("P1P2_runCycle", "--from", "3", STUB_DB_DOWN="1")
    assert r.returncode == 104, r.stdout + r.stderr
    assert "database unreachable" in r.stdout and tree.tools() == []
    assert tree.state() == before                                                 # the failed step is still resumable


# ─── the suspension is saved and restored, never assumed ────────────────────────────────────────

def test_standby_is_restored_to_the_real_value_not_to_a_hard_coded_30(tree):
    r = tree.run("P1P2_runCycle", *CYCLE)                                       # the stub reports 45 minutes
    assert r.returncode == 0, r.stdout + r.stderr
    assert tree.power_calls() == ["0", "45"], tree.power_calls()
    assert not (tree.logs / "standby_ac.saved").exists()


def test_standby_is_restored_even_when_the_cycle_fails(tree):
    assert tree.run("P1P2_runCycle", *CYCLE, STUB_RC_NAV_DISCOVERY="7").returncode == 7
    assert tree.power_calls() == ["0", "45"]


def test_an_interrupted_run_does_not_make_the_next_one_forget_the_original_value(tree):
    """After Ctrl+C the plan stays at 0 and the saved value stays on disk: the next run must NOT save the 0."""
    (tree.logs / "standby_ac.saved").write_text("45\n")
    r = tree.run("P1P2_runCycle", *CYCLE, STUB_STANDBY_HEX="00000000")          # the plan currently reads 0
    assert r.returncode == 0, r.stdout + r.stderr
    assert tree.power_calls()[-1] == "45"


def test_sub_launchers_leave_the_suspension_alone_under_the_orchestrator_but_manage_it_alone(tree):
    assert tree.run("P1_refreshBenchmarks").returncode == 0
    assert tree.power_calls() == []                                              # this one never touched it
    tree.run("P2_calculateIndicators", "--no-audit", "--no-export")
    assert tree.power_calls() == ["0", "45"]                                     # standalone: saves and restores


# ─── single instance ─────────────────────────────────────────────────────────────────────────────

def test_a_second_cycle_is_refused_while_the_first_runs_and_the_lock_dies_with_the_process(tree):
    first = tree.popen("P1P2_runCycle", *CYCLE, STUB_SLEEP_BENCHMARK_LOADER="6")
    try:
        deadline = time.time() + 60
        while "benchmark_loader" not in tree.tools():
            assert first.poll() is None and time.time() < deadline, "the first cycle did not reach step 1"
            time.sleep(0.3)
        r = tree.run("P1P2_runCycle", *CYCLE)
        assert r.returncode == 105, r.stdout + r.stderr
        assert "Otra instancia" in r.stdout
        assert tree.run("P2P3_runCycle", "--no-pause").returncode == 105       # shares the lock: both recompute P2
    finally:
        out, _ = first.communicate(timeout=240)
    assert first.returncode == 0, out
    assert tree.tools().count("run_pipeline") == 1                               # the refused ones ran nothing
    assert tree.run("P1P2_runCycle", *CYCLE).returncode == 0                    # nothing left behind to clean up


# ─── P2P3_runCycle: nothing preassigned ────────────────────────────────────────────────────────

def test_p2_p3_reads_the_version_and_takes_its_own_baseline_before_recalculating(tree):
    r = tree.run("P2P3_runCycle", "--no-pause")
    assert r.returncode == 0, r.stdout + r.stderr
    ln = tree.lines()
    snap = next(i for i, x in enumerate(ln) if x.startswith("beta_shift_audit --snapshot"))
    p2 = next(i for i, x in enumerate(ln) if x.startswith("run_pipeline"))
    cmp_ = next(i for i, x in enumerate(ln) if x.startswith("beta_shift_audit --compare"))
    assert snap < p2 < cmp_ < next(i for i, x in enumerate(ln) if x.startswith("p3_build_portfolio"))
    assert "--version 20991231" in ln[cmp_]                                      # the stub's CALC_VERSION, read at run time
    assert "20991231" in r.stdout
    assert power_ok(tree)


def power_ok(tree) -> bool:
    return tree.power_calls() == ["0", "45"]


def test_p2_p3_uses_an_explicit_baseline_and_stops_before_p3_when_the_beta_audit_fails(tree):
    base = tree.audit / "mine.csv"
    base.parent.mkdir(parents=True, exist_ok=True)
    base.write_text("x\n")
    r = tree.run("P2P3_runCycle", "--no-pause", "--baseline", str(base), STUB_RC_BETA_SHIFT_AUDIT="1")
    assert r.returncode == 1, r.stdout + r.stderr
    ln = tree.lines()
    assert not any(x.startswith("beta_shift_audit --snapshot") for x in ln)
    assert any(str(base) in x for x in ln if x.startswith("beta_shift_audit --compare"))
    assert "p3_build_portfolio" not in tree.tools()
    assert tree.run("P2P3_runCycle", "--no-pause", "--baseline", str(tree.audit / "missing.csv")).returncode == 100


# ─── the template is a working launcher ─────────────────────────────────────────────────────────

def test_the_template_is_a_working_launcher_that_follows_its_own_rules(tree):
    r = tree.run("_template")
    assert r.returncode == 0, r.stdout + r.stderr
    assert tree.power_calls() == ["0", "45"]                       # saved and restored the real value
    assert list(tree.logs.glob("log_template_*.log")), "no standard log was written"
    assert tree.run("_template", "--n", "x").returncode == 100
    assert tree.run("_template", "--frobnicate").returncode == 100
    assert tree.run("_template", "--help").returncode == 0


# ─── P1P2P3_runCycle: the two launchers integrated ─────────────────────────────────────────────────────

P123 = ["--no-pause", "--no-prompts"]


def test_p1_p2_p3_runs_the_cycle_then_the_beta_gate_then_p3_without_repeating_p2(tree):
    r = tree.run("P1P2P3_runCycle", *P123, STUB_VERDICT_P3_FRESHNESS_CHECK="P3 aceptaria estos datos")
    assert r.returncode == 0, r.stdout + r.stderr
    t, ln = tree.tools(), tree.lines()
    assert t.count("run_pipeline") == 1, "P2 must run once (P2P3_runCycle.bat used to repeat it)"
    compares = [x for x in ln if x.startswith("beta_shift_audit --compare")]
    assert len(compares) == 2                                       # the cycle's informational one + the blocking gate
    assert "--version 20991231" in compares[-1] and "outliers_gate_" in compares[-1]
    gate = max(i for i, x in enumerate(ln) if x.startswith("beta_shift_audit --compare"))
    assert ln.index(next(x for x in ln if x.startswith("run_pipeline"))) < gate < \
        ln.index(next(x for x in ln if x.startswith("p3_build_portfolio")))
    assert tree.state()["LAST_RESULT"] == "OK"
    assert tree.power_calls() == ["0", "45"], tree.power_calls()    # ONE disable/restore for the whole run
    assert list((tree.logs).glob("log_P1P2P3_runCycle_*.log"))


def test_p1_p2_p3_forwards_the_cycle_options_and_its_own_p3_options(tree):
    r = tree.run("P1P2P3_runCycle", *P123, "--no-export", "--skip-macro", "--workers", "2", "--no-harvest", "--shadow", "1",
                 "--scenario", "esc1", "--allow-stale", "--p3-dry-run", "--", "--isin", '"A,B"')
    assert r.returncode == 0, r.stdout + r.stderr
    ln = "\n".join(tree.lines()).replace("  ", " ")
    assert "export_p1" not in ln and "macro_discovery" not in ln and "p1_db_harvest" not in ln
    assert "--workers 2" in ln and "run_pipeline --isin A,B" in ln
    assert "shadow_reconciliation --stratified-sample 1" in ln
    assert "p3_build_portfolio esc1 --dry-run --allow-stale" in ln
    assert "Informe omitido" in r.stdout                                          # a dry run writes no report


def test_p1_p2_p3_stops_before_p3_when_the_cycle_fails_and_returns_its_code(tree):
    r = tree.run("P1P2P3_runCycle", *P123, STUB_RC_NAV_DISCOVERY="7")
    assert r.returncode == 7, r.stdout + r.stderr
    assert "p3_build_portfolio" not in tree.tools() and "run_pipeline" not in tree.tools()
    assert tree.power_calls() == ["0", "45"]                                      # still restored


def test_p1_p2_p3_stops_before_p3_when_the_beta_gate_fails(tree):
    r = tree.run("P1P2P3_runCycle", *P123, STUB_RC_BETA_SHIFT_AUDIT="1")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "run_pipeline" in tree.tools() and "p3_build_portfolio" not in tree.tools()
    assert "la puerta de betas fallo" in r.stdout


def test_p1_p2_p3_explicit_baseline_replaces_the_cycles_snapshot_in_the_gate(tree):
    base = tree.audit / "mine.csv"
    base.parent.mkdir(parents=True, exist_ok=True)
    base.write_text("x\n")
    assert tree.run("P1P2P3_runCycle", *P123, "--baseline", str(base)).returncode == 0
    assert any(str(base) in x for x in tree.lines() if x.startswith("beta_shift_audit --compare"))


def test_only_p3_builds_on_the_last_ok_cycle_and_refuses_otherwise(tree):
    assert tree.run("P1P2P3_runCycle", *P123, "--only-p3").returncode == 102             # no cycle yet: nothing proves it is OK
    assert tree.run("P1P2P3_runCycle", *P123, STUB_RC_NAV_DISCOVERY="7").returncode == 7
    assert tree.run("P1P2P3_runCycle", *P123, "--only-p3").returncode == 102             # the last cycle FAILED
    assert tree.run("P1P2_runCycle", "--from", "3", "--skip-preflight").returncode == 0     # resume -> OK
    tree.trace.unlink()
    r = tree.run("P1P2P3_runCycle", *P123, "--only-p3")
    assert r.returncode == 0, r.stdout + r.stderr
    t = tree.tools()
    assert "p3_build_portfolio" in t and "run_pipeline" not in t and "benchmark_loader" not in t
    assert any(x.startswith("beta_shift_audit --compare") for x in tree.lines())   # the gate still runs


def test_p1_p2_p3_argument_errors_are_rc_100_and_run_nothing(tree):
    for args in (["--frobnicate"], ["--workers", "0"], ["--scenario"], ["--scenario", "--no-pause"],
                 ["--baseline", str(tree.audit / "nope.csv")], ["--only-p3", "--force"], ["--scenario", '"a b"']):
        assert tree.run("P1P2P3_runCycle", *args).returncode == 100, args
    r = tree.run("P1P2P3_runCycle", "--help")
    assert r.returncode == 0 and "--only-p3" in r.stdout and "--harvest-sync" in r.stdout    # own + the cycle's usage
    assert tree.tools() == []


def test_the_three_cyclers_exclude_each_other_and_the_nested_one_is_not_refused_by_its_parent(tree):
    first = tree.popen("P1P2P3_runCycle", *P123, STUB_SLEEP_BENCHMARK_LOADER="6")
    try:
        deadline = time.time() + 60
        while "benchmark_loader" not in tree.tools():
            assert first.poll() is None and time.time() < deadline, "the first run did not reach step 1"
            time.sleep(0.3)
        assert tree.run("P1P2_runCycle", "--skip-preflight").returncode == 105
        assert tree.run("P2P3_runCycle", "--no-pause").returncode == 105
        assert tree.run("P1P2P3_runCycle", *P123).returncode == 105
    finally:
        out, _ = first.communicate(timeout=240)
    assert first.returncode == 0, out                  # P1P2_runCycle ran NESTED under it: it did not ask for the lock again
    assert tree.tools().count("run_pipeline") == 1


# ─── cycle telemetry (FND-0239): opt-in, best effort, never changes a return code ────────────────

def _with_telemetry(tree, tmp_path, tool_source=None):
    """Copy the REAL shared/cycle_telemetry.py into the throwaway tree (or a replacement) and return the env that turns it on with NO database."""
    dst = tree.root / "shared" / "cycle_telemetry.py"
    if tool_source is None:
        shutil.copy(REAL / "shared" / "cycle_telemetry.py", dst)
    else:
        dst.write_text(tool_source)
    fb = tmp_path / "telemetry_fallback.jsonl"
    return fb, dict(FONDOS_TELEMETRY="1", FONDOS_PG_DSN="", FONDOS_CYCLE_FALLBACK=str(fb))


def _events(fb):
    import json
    return [json.loads(ln) for ln in fb.read_text(encoding="utf-8").splitlines() if ln.strip()] if fb.exists() else []


def test_telemetry_off_by_default_writes_nothing(tree, tmp_path):
    fb, _ = _with_telemetry(tree, tmp_path)
    r = tree.run("P1P2_runCycle", *CYCLE, FONDOS_CYCLE_FALLBACK=str(fb))             # FONDOS_TELEMETRY not set
    assert r.returncode == 0, r.stdout + r.stderr
    assert not fb.exists()


def test_telemetry_on_records_the_cycle_and_its_steps_in_order_and_the_rc_is_the_same(tree, tmp_path):
    fb, env = _with_telemetry(tree, tmp_path)
    r = tree.run("P1P2_runCycle", *CYCLE, **env)
    assert r.returncode == 0, r.stdout + r.stderr
    ev = _events(fb)
    ops = [e["op"] for e in ev]
    assert ops[0] == "cycle_begin" and ops[-1] == "cycle_end"
    steps = [e["step_code"] for e in ev if e["op"] == "step_end"]
    main = [s for s in steps if s in ("P1_BENCH", "P1_CLASSIFY", "P2_DISCOVER", "P2_CALC")]
    assert main == ["P1_BENCH", "P1_CLASSIFY", "P2_DISCOVER", "P2_CALC"]                # the four steps, in order
    assert all(e["rc"] == 0 for e in ev if e["op"] == "step_end")
    assert ev[-1]["status"] == "OK" and ev[-1]["rc"] == 0 and len({e["cycle_id"] for e in ev}) == 1
    begin = {e["step_code"]: e for e in ev if e["op"] == "step_begin"}
    end = {e["step_code"]: e for e in ev if e["op"] == "step_end"}
    assert all(end[s]["started_at"] == begin[s]["ts"] for s in begin)                    # the start travelled between two Python processes


def test_a_failing_step_is_recorded_as_failed_and_its_rc_is_propagated_untouched(tree, tmp_path):
    plain = tree.run("P1P2_runCycle", *CYCLE, STUB_RC_RUN_PIPELINE="7")
    assert plain.returncode != 0
    fb, env = _with_telemetry(tree, tmp_path)
    with_telemetry = tree.run("P1P2_runCycle", *CYCLE, STUB_RC_RUN_PIPELINE="7", **env)
    assert with_telemetry.returncode == plain.returncode                                # telemetry changes no return code
    ev = _events(fb)
    last = ev[-1]
    assert last["op"] == "cycle_end" and last["status"] == "FAILED" and last["failed_step"] == "P2_CALC" and last["rc"] == plain.returncode
    assert [e["rc"] for e in ev if e["op"] == "step_end" and e["step_code"] == "P2_CALC"] == [7]


def test_a_broken_telemetry_tool_cannot_fail_the_cycle(tree, tmp_path):
    fb, env = _with_telemetry(tree, tmp_path, tool_source="import sys\nsys.exit(9)\n")
    r = tree.run("P1P2_runCycle", *CYCLE, **env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert tree.state()["LAST_RESULT"] == "OK"


# ─── cycle telemetry, stage 3: the whole chain is instrumented (still opt-in, still RC-neutral) ───

def _step_order(ev, op="step_end"):
    return [e["step_code"] for e in ev if e["op"] == op]


def test_p1_p2_p3_owns_one_cycle_and_records_audits_beta_gate_and_p3_in_order(tree, tmp_path):
    fb, env = _with_telemetry(tree, tmp_path)
    r = tree.run("P1P2P3_runCycle", *P123, STUB_VERDICT_P3_FRESHNESS_CHECK="P3 aceptaria estos datos", **env)
    assert r.returncode == 0, r.stdout + r.stderr
    ev = _events(fb)
    assert [e["op"] for e in ev].count("cycle_begin") == 1 and [e["op"] for e in ev].count("cycle_end") == 1      # ONE cycle, owned by P1P2P3_runCycle
    assert ev[0]["op"] == "cycle_begin" and ev[0]["launcher"] == "P1P2P3_runCycle" and ev[-1]["status"] == "OK"
    assert len({e["cycle_id"] for e in ev}) == 1
    order = _step_order(ev)
    for a, b in (("AUDIT_BASE", "P1_BENCH"), ("P2_CALC", "BETA_GATE"), ("BETA_GATE", "P3_BUILD"), ("P3_BUILD", "P3_REPORT")):
        assert order.index(a) < order.index(b), (a, b, order)
    assert all(e["rc"] == 0 for e in ev if e["op"] == "step_end")
    assert _step_order(ev, "step_begin").count("P3_BUILD") == 1                                       # the nested P3 launcher did not add its own


def test_a_failing_p3_or_beta_gate_is_the_failed_step_and_the_rc_is_unchanged(tree, tmp_path):
    for stub, step in (("STUB_RC_P3_BUILD_PORTFOLIO", "P3_BUILD"), ("STUB_RC_BETA_SHIFT_AUDIT", "BETA_GATE")):
        plain = tree.run("P1P2P3_runCycle", *P123, **{stub: "5"})
        assert plain.returncode == 5, plain.stdout + plain.stderr
        fb = tmp_path / f"fb_{step}.jsonl"
        _, env = _with_telemetry(tree, tmp_path)
        env["FONDOS_CYCLE_FALLBACK"] = str(fb)
        with_t = tree.run("P1P2P3_runCycle", *P123, **{stub: "5"}, **env)
        assert with_t.returncode == plain.returncode
        end = _events(fb)[-1]
        assert end["op"] == "cycle_end" and end["status"] == "FAILED" and end["failed_step"] == step and end["rc"] == 5


def test_p2_p3_complete_owns_its_cycle_with_every_step(tree, tmp_path):
    fb, env = _with_telemetry(tree, tmp_path)
    r = tree.run("P2P3_runCycle", "--no-pause", **env)
    assert r.returncode == 0, r.stdout + r.stderr
    ev = _events(fb)
    assert ev[0]["op"] == "cycle_begin" and ev[0]["launcher"] == "P2P3_runCycle" and ev[-1]["status"] == "OK"
    assert _step_order(ev) == ["AUDIT_BASE", "BETA_SNAPSHOT", "P2_DISCOVER", "P2_CALC", "BETA_COMPARE", "P3_BUILD", "P3_REPORT"]   # FND-0309: master order


def test_p2_p3_complete_failure_keeps_its_rc_and_names_the_step(tree, tmp_path):
    plain = tree.run("P2P3_runCycle", "--no-pause", STUB_RC_RUN_PIPELINE="4")
    fb, env = _with_telemetry(tree, tmp_path)
    with_t = tree.run("P2P3_runCycle", "--no-pause", STUB_RC_RUN_PIPELINE="4", **env)
    assert with_t.returncode == plain.returncode != 0
    end = _events(fb)[-1]
    assert end["status"] == "FAILED" and end["failed_step"] == "P2_CALC"


def test_a_standalone_p3_launcher_opens_and_closes_its_own_cycle(tree, tmp_path):
    fb, env = _with_telemetry(tree, tmp_path)
    r = tree.run("P3_buildPortfolio", **env)
    assert r.returncode == 0, r.stdout + r.stderr
    ev = _events(fb)
    assert [e["op"] for e in ev] == ["cycle_begin", "step_begin", "step_end", "cycle_end"]
    assert ev[0]["launcher"] == "P3_buildPortfolio" and ev[2]["step_code"] == "P3_BUILD" and ev[3]["status"] == "OK"
    fb2 = tmp_path / "fb2.jsonl"
    env["FONDOS_CYCLE_FALLBACK"] = str(fb2)
    r = tree.run("P3_generateReport", **env)
    assert r.returncode == 0, r.stdout + r.stderr
    ev = _events(fb2)
    assert ev[0]["launcher"] == "P3_generateReport" and _step_order(ev) == ["P3_REPORT"] and ev[-1]["status"] == "OK"


def test_a_standalone_p3_failure_keeps_the_stale_inputs_rc(tree, tmp_path):
    plain = tree.run("P3_buildPortfolio", STUB_RC_P3_BUILD_PORTFOLIO="2")
    assert plain.returncode == 2
    fb, env = _with_telemetry(tree, tmp_path)
    with_t = tree.run("P3_buildPortfolio", STUB_RC_P3_BUILD_PORTFOLIO="2", **env)
    assert with_t.returncode == 2
    end = _events(fb)[-1]
    assert end["status"] == "FAILED" and end["failed_step"] == "P3_BUILD" and end["rc"] == 2


def test_telemetry_off_leaves_every_hooked_launcher_silent(tree, tmp_path):
    fb, _ = _with_telemetry(tree, tmp_path)
    for name, args in (("P1P2P3_runCycle", P123), ("P2P3_runCycle", ["--no-pause"]), ("P3_buildPortfolio", []), ("P3_generateReport", [])):
        r = tree.run(name, *args, FONDOS_CYCLE_FALLBACK=str(fb))
        assert r.returncode == 0, (name, r.stdout + r.stderr)
    assert not fb.exists()


# ─── FND-0246 / FND-0246_V2: the 2026-10-10 release launchers ───────────────────────────────────

def _first(ln, prefix):
    return next(i for i, x in enumerate(ln) if x.startswith(prefix))


def test_v2_checks_the_release_configuration_then_runs_p2_p3_without_prompts(tree):
    r = tree.run("FND-0246_V2", "--no-pause")
    assert r.returncode == 0, r.stdout + r.stderr
    ln = tree.lines()
    assert ln[0].startswith("release_check")
    assert _first(ln, "release_check") < _first(ln, "beta_shift_audit --snapshot") < _first(ln, "run_pipeline")         < _first(ln, "beta_shift_audit --compare") < _first(ln, "p3_build_portfolio")
    assert "--force" not in " ".join(x for x in ln if x.startswith("run_pipeline"))      # the release forces the recompute itself
    assert "benchmark_loader" not in tree.tools() and "--no-prompts" in r.stdout          # no question asked, nothing extra run
    assert any(x.startswith("generate_report") for x in ln)                              # P3 report included


def test_v2_runs_nothing_when_the_release_configuration_is_not_in_the_code(tree):
    r = tree.run("FND-0246_V2", "--no-pause", STUB_RC_RELEASE_CHECK="1")
    assert r.returncode == 1, r.stdout + r.stderr
    assert tree.tools() == ["release_check"]


def test_full_release_runs_p1_then_the_currency_gate_then_nav_then_p2_p3(tree):
    r = tree.run("FND-0246", "--no-pause")
    assert r.returncode == 0, r.stdout + r.stderr
    ln = tree.lines()
    assert ln[0].startswith("release_check")                                             # fails fast, before P1
    assert _first(ln, "run_block") < _first(ln, "verify_fund_currency") < _first(ln, "nav_discovery")         < _first(ln, "run_pipeline") < _first(ln, "p3_build_portfolio")
    assert tree.tools().count("run_pipeline") == 1
    assert power_ok(tree)                                                                # one owner saves and restores


def test_full_release_stops_before_p2_when_a_class_currency_is_pending(tree):
    r = tree.run("FND-0246", "--no-pause", STUB_RC_VERIFY_FUND_CURRENCY="1")
    assert r.returncode == 1, r.stdout + r.stderr
    t = tree.tools()
    assert "run_block" in t and "nav_discovery" not in t and "run_pipeline" not in t


def test_full_release_stops_on_a_failed_nav_load(tree):
    r = tree.run("FND-0246", "--no-pause", STUB_RC_NAV_DISCOVERY="2")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "run_pipeline" not in tree.tools()


def test_the_release_launchers_work_when_started_from_scripts_launch(tree):
    for name in ("FND-0246_V2", "FND-0246"):
        r = subprocess.run(tree._cmd(name, ["--no-pause"]), env=tree.env(), cwd=str(tree.launch),
                           capture_output=True, text=True, errors="replace", timeout=240, stdin=subprocess.DEVNULL)
        assert r.returncode == 0, (name, r.stdout + r.stderr)


def test_the_full_release_shares_the_cycle_lock(tree):
    first = tree.popen("P1P2_runCycle", *CYCLE, STUB_SLEEP_BENCHMARK_LOADER="6")
    try:
        deadline = time.time() + 60
        while "benchmark_loader" not in tree.tools():
            assert first.poll() is None and time.time() < deadline, "the first cycle did not reach step 1"
            time.sleep(0.3)
        assert tree.run("FND-0246", "--no-pause").returncode == 105
        assert tree.run("FND-0246_V2", "--no-pause").returncode == 105                   # via P2P3_runCycle
    finally:
        out, _ = first.communicate(timeout=240)
    assert first.returncode == 0, out


def test_p2_p3_no_prompts_skips_the_push_and_the_loader_questions(tree):
    r = tree.run("P2P3_runCycle", "--no-pause", "--no-prompts")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "--no-prompts" in r.stdout and "Estas satisfecho" not in r.stdout and "benchmark_loader" not in tree.tools()


# ─── P3_generateReport: the report follows the scenario lifecycle (2026-10-11) ────────────────────

def test_report_launcher_reports_the_production_scenario_by_default(tree):
    r = tree.run("P3_generateReport")
    assert r.returncode == 0, r.stdout + r.stderr
    ln = [x for x in tree.lines() if x.startswith("generate_report")]
    assert len(ln) == 1 and ln[0].endswith(" None"), ln                     # no scenario forced: the library resolves PRODUCTION


def test_report_launcher_forwards_an_explicit_scenario_and_output_dir(tree, tmp_path):
    out = tmp_path / "otra ruta"
    r = tree.run("P3_generateReport", str(out), "--scenario", "cartera_x_b1")
    assert r.returncode == 0, r.stdout + r.stderr
    ln = [x for x in tree.lines() if x.startswith("generate_report")]
    assert len(ln) == 1 and ln[0].endswith(" cartera_x_b1") and str(out) in ln[0], ln


def test_report_launcher_exits_3_when_there_is_no_scenario_to_report(tree):
    r = tree.run("P3_generateReport", STUB_NO_SCENARIO="1")
    assert r.returncode == 3, r.stdout + r.stderr                           # the tool's code passes through untouched
    assert "sin escenario en PRODUCCION" in r.stdout + (list(tree.logs.parent.parent.glob("proyecto3/log/log_P3_report_*.log")) and
                                                          "".join(f.read_text(errors="replace")
                                                                  for f in tree.logs.parent.parent.glob("proyecto3/log/log_P3_report_*.log")) or "")


def test_report_launcher_draft_reports_the_newest_draft_explicitly(tree):
    r = tree.run("P3_generateReport", "--draft")
    assert r.returncode == 0, r.stdout + r.stderr
    ln = [x for x in tree.lines() if x.startswith("generate_report")]
    assert len(ln) == 1 and ln[0].endswith(" cartera_stub_b1"), ln          # resolved by the tool, passed to the library as an explicit id


def test_report_launcher_draft_without_any_draft_exits_3(tree):
    r = tree.run("P3_generateReport", "--draft", STUB_NO_DRAFT="1")
    assert r.returncode == 3, r.stdout + r.stderr
    assert not [x for x in tree.lines() if x.startswith("generate_report")]


def test_the_cycle_launcher_reports_the_draft_it_just_built(tree):
    r = tree.run("P2P3_runCycle", "--no-pause", "--no-prompts")
    assert r.returncode == 0, r.stdout + r.stderr
    ln = [x for x in tree.lines() if x.startswith("generate_report")]
    assert len(ln) == 1 and ln[0].endswith(" cartera_stub_b1"), ln          # never the implicit PRODUCTION: nothing is promoted yet


def test_report_launcher_rejects_scenario_without_an_id(tree):
    assert tree.run("P3_generateReport", "--scenario").returncode == 100
    assert not [x for x in tree.lines() if x.startswith("generate_report")]
