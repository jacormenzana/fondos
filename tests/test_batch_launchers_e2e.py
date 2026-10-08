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

_LAUNCHERS = ["_template", "P1_P2_Complete", "P2_P3_complete", "P1_P2_P3", "P1_refreshBenchmarks", "P1_discoverAllFunds",
              "P2_discoverLoadMetrics", "P2_calculateIndicators", "P1_harvestFunds", "P1_diagCost",
              "AUDIT_P1", "AUDIT_P2", "P3_buildPortfolio", "P3_generateReport"]


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
        (self.root / "shared/db.py").write_text(
            "import os\n\nclass _Cur:\n    def fetchone(self): return (1,)\n    def fetchall(self): return []\n\n"
            "class _Conn:\n    def execute(self, *a, **k): return _Cur()\n    def close(self): pass\n\n"
            "def get_connection():\n    if os.environ.get('STUB_DB_DOWN'):\n        raise ConnectionError('db down')\n"
            "    return _Conn()\n")
        (self.root / "proyecto3/src").mkdir(parents=True)
        (self.root / "proyecto3/__init__.py").touch()
        (self.root / "proyecto3/src/__init__.py").touch()
        (self.root / "proyecto3/src/monthly_report.py").write_text("def generate_report(c, output_dir=None):\n    return 'report'\n")
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
        f = self.logs / "P1_P2_Complete.state"
        return dict(ln.split("=", 1) for ln in f.read_text().splitlines() if "=" in ln) if f.exists() else {}


@pytest.fixture()
def tree(tmp_path):
    return Tree(tmp_path)


CYCLE = ["--skip-preflight"]


# ─── the happy path ──────────────────────────────────────────────────────────────────────────────

def test_a_normal_cycle_runs_every_phase_in_order_and_exits_0(tree):
    r = tree.run("P1_P2_Complete", STUB_VERDICT_P3_FRESHNESS_CHECK="P3 aceptaria estos datos")   # preflight included
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
    r = tree.run("P1_P2_Complete", *CYCLE, "--benchmarks-load", "--no-export", "--skip-macro", "--workers", "3",
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
    r = tree.run("P1_P2_Complete", *CYCLE)
    assert r.returncode == 0, r.stdout + r.stderr
    assert str(tree.root) in r.stdout and " " in str(tree.root)
    after = {w: sorted(p.name for p in w.glob("*")) if w.exists() else None for w in watched}
    assert before == after


# ─── failures keep their own code and the resume guard works ────────────────────────────────────

def test_a_step_failure_propagates_its_rc_and_blocks_the_following_steps(tree):
    r = tree.run("P1_P2_Complete", *CYCLE, STUB_RC_NAV_DISCOVERY="7")
    assert r.returncode == 7, r.stdout + r.stderr
    assert "run_pipeline" not in tree.tools()
    assert tree.state()["LAST_RESULT"] == "FAILED" and tree.state()["FAILED_STEP"] == "3"


def test_resume_from_the_failed_step_skips_what_ran_and_reuses_baseline_and_snapshot(tree):
    assert tree.run("P1_P2_Complete", *CYCLE, STUB_RC_NAV_DISCOVERY="7").returncode == 7
    baseline = tree.state()["BASELINE_RUN_ID"]
    tree.trace.unlink()
    r = tree.run("P1_P2_Complete", *CYCLE, "--from", "3")
    assert r.returncode == 0, r.stdout + r.stderr
    t = tree.tools()
    assert "benchmark_loader" not in t and "p1_db_harvest" not in t and "mark_stale" not in t
    assert "nav_discovery" in t and "run_pipeline" in t
    assert any(baseline in ln for ln in tree.lines() if ln.startswith("beta_shift_audit --compare")) or \
        (tree.audit / f"macro_betas_{baseline}.csv").exists()
    assert tree.state()["LAST_RESULT"] == "OK"


def test_a_tool_code_is_never_confused_with_a_launcher_code(tree):
    """The harvest gate's RC 5 used to be indistinguishable from 'interpreter not found' (RC 5)."""
    r = tree.run("P1_P2_Complete", *CYCLE, STUB_RC_HARVEST_GATE="5")
    assert r.returncode == 5 and not 100 <= r.returncode <= 199
    assert tree.state()["FAILED_STEP"] == "1" and "benchmark_loader" not in tree.tools()


def test_the_launchers_own_codes(tree):
    assert tree.run("P1_P2_Complete", "--frobnicate").returncode == 100
    assert tree.run("P1_P2_Complete", "--workers", "0").returncode == 100
    assert tree.run("P1_P2_Complete", "--help").returncode == 0
    assert tree.run("P1_P2_Complete", "--from", "3").returncode == 102           # no state: refused, fails closed
    assert tree.run("P1_P2_Complete", *CYCLE, FONDOS_PYTHON=str(tree.root / "no_python.exe")).returncode == 101
    for sub in ("P1_discoverAllFunds", "P2_discoverLoadMetrics", "P1_harvestFunds"):   # P2_calculateIndicators forwards unknown args
        assert tree.run(sub, "--help").returncode == 0, sub
    assert tree.run("P1_discoverAllFunds", "--nope").returncode == 100
    assert tree.tools() == []                                                    # none of the above ran a tool


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
    r = tree.run("P1_P2_Complete", "--help", FONDOS_PYTHON=str(tree.root / "no_python.exe"), **BLOCKED)
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
    assert tree.run("P1_P2_Complete", *CYCLE, STUB_RC_NAV_DISCOVERY="7").returncode == 7
    before = tree.state()
    tree.trace.unlink()
    r = tree.run("P1_P2_Complete", "--from", "3", STUB_DB_DOWN="1")
    assert r.returncode == 104, r.stdout + r.stderr
    assert "database unreachable" in r.stdout and tree.tools() == []
    assert tree.state() == before                                                 # the failed step is still resumable


# ─── the suspension is saved and restored, never assumed ────────────────────────────────────────

def test_standby_is_restored_to_the_real_value_not_to_a_hard_coded_30(tree):
    r = tree.run("P1_P2_Complete", *CYCLE)                                       # the stub reports 45 minutes
    assert r.returncode == 0, r.stdout + r.stderr
    assert tree.power_calls() == ["0", "45"], tree.power_calls()
    assert not (tree.logs / "standby_ac.saved").exists()


def test_standby_is_restored_even_when_the_cycle_fails(tree):
    assert tree.run("P1_P2_Complete", *CYCLE, STUB_RC_NAV_DISCOVERY="7").returncode == 7
    assert tree.power_calls() == ["0", "45"]


def test_an_interrupted_run_does_not_make_the_next_one_forget_the_original_value(tree):
    """After Ctrl+C the plan stays at 0 and the saved value stays on disk: the next run must NOT save the 0."""
    (tree.logs / "standby_ac.saved").write_text("45\n")
    r = tree.run("P1_P2_Complete", *CYCLE, STUB_STANDBY_HEX="00000000")          # the plan currently reads 0
    assert r.returncode == 0, r.stdout + r.stderr
    assert tree.power_calls()[-1] == "45"


def test_sub_launchers_leave_the_suspension_alone_under_the_orchestrator_but_manage_it_alone(tree):
    assert tree.run("P1_refreshBenchmarks").returncode == 0
    assert tree.power_calls() == []                                              # this one never touched it
    tree.run("P2_calculateIndicators", "--no-audit", "--no-export")
    assert tree.power_calls() == ["0", "45"]                                     # standalone: saves and restores


# ─── single instance ─────────────────────────────────────────────────────────────────────────────

def test_a_second_cycle_is_refused_while_the_first_runs_and_the_lock_dies_with_the_process(tree):
    first = tree.popen("P1_P2_Complete", *CYCLE, STUB_SLEEP_BENCHMARK_LOADER="6")
    try:
        deadline = time.time() + 60
        while "benchmark_loader" not in tree.tools():
            assert first.poll() is None and time.time() < deadline, "the first cycle did not reach step 1"
            time.sleep(0.3)
        r = tree.run("P1_P2_Complete", *CYCLE)
        assert r.returncode == 105, r.stdout + r.stderr
        assert "Otra instancia" in r.stdout
        assert tree.run("P2_P3_complete", "--no-pause").returncode == 105       # shares the lock: both recompute P2
    finally:
        out, _ = first.communicate(timeout=240)
    assert first.returncode == 0, out
    assert tree.tools().count("run_pipeline") == 1                               # the refused ones ran nothing
    assert tree.run("P1_P2_Complete", *CYCLE).returncode == 0                    # nothing left behind to clean up


# ─── P2_P3_complete: nothing preassigned ────────────────────────────────────────────────────────

def test_p2_p3_reads_the_version_and_takes_its_own_baseline_before_recalculating(tree):
    r = tree.run("P2_P3_complete", "--no-pause")
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
    r = tree.run("P2_P3_complete", "--no-pause", "--baseline", str(base), STUB_RC_BETA_SHIFT_AUDIT="1")
    assert r.returncode == 1, r.stdout + r.stderr
    ln = tree.lines()
    assert not any(x.startswith("beta_shift_audit --snapshot") for x in ln)
    assert any(str(base) in x for x in ln if x.startswith("beta_shift_audit --compare"))
    assert "p3_build_portfolio" not in tree.tools()
    assert tree.run("P2_P3_complete", "--no-pause", "--baseline", str(tree.audit / "missing.csv")).returncode == 100


# ─── the template is a working launcher ─────────────────────────────────────────────────────────

def test_the_template_is_a_working_launcher_that_follows_its_own_rules(tree):
    r = tree.run("_template")
    assert r.returncode == 0, r.stdout + r.stderr
    assert tree.power_calls() == ["0", "45"]                       # saved and restored the real value
    assert list(tree.logs.glob("log_template_*.log")), "no standard log was written"
    assert tree.run("_template", "--n", "x").returncode == 100
    assert tree.run("_template", "--frobnicate").returncode == 100
    assert tree.run("_template", "--help").returncode == 0


# ─── P1_P2_P3: the two launchers integrated ─────────────────────────────────────────────────────

P123 = ["--no-pause", "--no-prompts"]


def test_p1_p2_p3_runs_the_cycle_then_the_beta_gate_then_p3_without_repeating_p2(tree):
    r = tree.run("P1_P2_P3", *P123, STUB_VERDICT_P3_FRESHNESS_CHECK="P3 aceptaria estos datos")
    assert r.returncode == 0, r.stdout + r.stderr
    t, ln = tree.tools(), tree.lines()
    assert t.count("run_pipeline") == 1, "P2 must run once (P2_P3_complete.bat used to repeat it)"
    compares = [x for x in ln if x.startswith("beta_shift_audit --compare")]
    assert len(compares) == 2                                       # the cycle's informational one + the blocking gate
    assert "--version 20991231" in compares[-1] and "outliers_gate_" in compares[-1]
    gate = max(i for i, x in enumerate(ln) if x.startswith("beta_shift_audit --compare"))
    assert ln.index(next(x for x in ln if x.startswith("run_pipeline"))) < gate < \
        ln.index(next(x for x in ln if x.startswith("p3_build_portfolio")))
    assert tree.state()["LAST_RESULT"] == "OK"
    assert tree.power_calls() == ["0", "45"], tree.power_calls()    # ONE disable/restore for the whole run
    assert list((tree.logs).glob("log_P1_P2_P3_*.log"))


def test_p1_p2_p3_forwards_the_cycle_options_and_its_own_p3_options(tree):
    r = tree.run("P1_P2_P3", *P123, "--no-export", "--skip-macro", "--workers", "2", "--no-harvest", "--shadow", "1",
                 "--scenario", "esc1", "--allow-stale", "--p3-dry-run", "--", "--isin", '"A,B"')
    assert r.returncode == 0, r.stdout + r.stderr
    ln = "\n".join(tree.lines()).replace("  ", " ")
    assert "export_p1" not in ln and "macro_discovery" not in ln and "p1_db_harvest" not in ln
    assert "--workers 2" in ln and "run_pipeline --isin A,B" in ln
    assert "shadow_reconciliation --stratified-sample 1" in ln
    assert "p3_build_portfolio esc1 --dry-run --allow-stale" in ln
    assert "Informe omitido" in r.stdout                                          # a dry run writes no report


def test_p1_p2_p3_stops_before_p3_when_the_cycle_fails_and_returns_its_code(tree):
    r = tree.run("P1_P2_P3", *P123, STUB_RC_NAV_DISCOVERY="7")
    assert r.returncode == 7, r.stdout + r.stderr
    assert "p3_build_portfolio" not in tree.tools() and "run_pipeline" not in tree.tools()
    assert tree.power_calls() == ["0", "45"]                                      # still restored


def test_p1_p2_p3_stops_before_p3_when_the_beta_gate_fails(tree):
    r = tree.run("P1_P2_P3", *P123, STUB_RC_BETA_SHIFT_AUDIT="1")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "run_pipeline" in tree.tools() and "p3_build_portfolio" not in tree.tools()
    assert "la puerta de betas fallo" in r.stdout


def test_p1_p2_p3_explicit_baseline_replaces_the_cycles_snapshot_in_the_gate(tree):
    base = tree.audit / "mine.csv"
    base.parent.mkdir(parents=True, exist_ok=True)
    base.write_text("x\n")
    assert tree.run("P1_P2_P3", *P123, "--baseline", str(base)).returncode == 0
    assert any(str(base) in x for x in tree.lines() if x.startswith("beta_shift_audit --compare"))


def test_only_p3_builds_on_the_last_ok_cycle_and_refuses_otherwise(tree):
    assert tree.run("P1_P2_P3", *P123, "--only-p3").returncode == 102             # no cycle yet: nothing proves it is OK
    assert tree.run("P1_P2_P3", *P123, STUB_RC_NAV_DISCOVERY="7").returncode == 7
    assert tree.run("P1_P2_P3", *P123, "--only-p3").returncode == 102             # the last cycle FAILED
    assert tree.run("P1_P2_Complete", "--from", "3", "--skip-preflight").returncode == 0     # resume -> OK
    tree.trace.unlink()
    r = tree.run("P1_P2_P3", *P123, "--only-p3")
    assert r.returncode == 0, r.stdout + r.stderr
    t = tree.tools()
    assert "p3_build_portfolio" in t and "run_pipeline" not in t and "benchmark_loader" not in t
    assert any(x.startswith("beta_shift_audit --compare") for x in tree.lines())   # the gate still runs


def test_p1_p2_p3_argument_errors_are_rc_100_and_run_nothing(tree):
    for args in (["--frobnicate"], ["--workers", "0"], ["--scenario"], ["--scenario", "--no-pause"],
                 ["--baseline", str(tree.audit / "nope.csv")], ["--only-p3", "--force"], ["--scenario", '"a b"']):
        assert tree.run("P1_P2_P3", *args).returncode == 100, args
    r = tree.run("P1_P2_P3", "--help")
    assert r.returncode == 0 and "--only-p3" in r.stdout and "--harvest-sync" in r.stdout    # own + the cycle's usage
    assert tree.tools() == []


def test_the_three_cyclers_exclude_each_other_and_the_nested_one_is_not_refused_by_its_parent(tree):
    first = tree.popen("P1_P2_P3", *P123, STUB_SLEEP_BENCHMARK_LOADER="6")
    try:
        deadline = time.time() + 60
        while "benchmark_loader" not in tree.tools():
            assert first.poll() is None and time.time() < deadline, "the first run did not reach step 1"
            time.sleep(0.3)
        assert tree.run("P1_P2_Complete", "--skip-preflight").returncode == 105
        assert tree.run("P2_P3_complete", "--no-pause").returncode == 105
        assert tree.run("P1_P2_P3", *P123).returncode == 105
    finally:
        out, _ = first.communicate(timeout=240)
    assert first.returncode == 0, out                  # P1_P2_Complete ran NESTED under it: it did not ask for the lock again
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
    r = tree.run("P1_P2_Complete", *CYCLE, FONDOS_CYCLE_FALLBACK=str(fb))             # FONDOS_TELEMETRY not set
    assert r.returncode == 0, r.stdout + r.stderr
    assert not fb.exists()


def test_telemetry_on_records_the_cycle_and_its_steps_in_order_and_the_rc_is_the_same(tree, tmp_path):
    fb, env = _with_telemetry(tree, tmp_path)
    r = tree.run("P1_P2_Complete", *CYCLE, **env)
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
    plain = tree.run("P1_P2_Complete", *CYCLE, STUB_RC_RUN_PIPELINE="7")
    assert plain.returncode != 0
    fb, env = _with_telemetry(tree, tmp_path)
    with_telemetry = tree.run("P1_P2_Complete", *CYCLE, STUB_RC_RUN_PIPELINE="7", **env)
    assert with_telemetry.returncode == plain.returncode                                # telemetry changes no return code
    ev = _events(fb)
    last = ev[-1]
    assert last["op"] == "cycle_end" and last["status"] == "FAILED" and last["failed_step"] == "P2_CALC" and last["rc"] == plain.returncode
    assert [e["rc"] for e in ev if e["op"] == "step_end" and e["step_code"] == "P2_CALC"] == [7]


def test_a_broken_telemetry_tool_cannot_fail_the_cycle(tree, tmp_path):
    fb, env = _with_telemetry(tree, tmp_path, tool_source="import sys\nsys.exit(9)\n")
    r = tree.run("P1_P2_Complete", *CYCLE, **env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert tree.state()["LAST_RESULT"] == "OK"
