"""
tests/test_maintenance_log_routing.py — guards for the host-level maintenance-log routing
(NORMAS_IMPLEMENTACION.md §4.9 rule 5; ticket FND-0269).

The preventive-maintenance scripts (scripts/ops/maintenance) used to mirror every run into one directory,
C:\\data\\logs\\maintenance. Each script family now has its own host-level directory under c:\\data\\wsl\\log:

    wsl_*  ->  /mnt/c/data/wsl/log/os                 (WSL_LOG_OS)
    pg_*   ->  /mnt/c/data/wsl/log/docker/postgres    (WSL_LOG_PG)

What each test is for:
  1. No tracked text file still names the old directory.
  2. The defaults in lib/common.sh are the two registered directories and nothing else is registered.
  3. pg_04 writes pg_capacity_history.csv through the resolved directory (no second default).
  4. Behaviour, with real bash in a tmp dir: a wsl_ and a pg_ run land in their own directory, an unregistered
     family exits 3 without writing, MAINT_LOG_DIR still forces one directory for both. This test does not skip:
     it fails with a message when no bash is available (Git Bash / WSL are part of this environment).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_MAINT = _ROOT / "scripts" / "ops" / "maintenance"
_COMMON = _MAINT / "lib" / "common.sh"
_OLD = re.compile(r"data[/\\]logs[/\\]maintenance", re.I)
_TEXT_SUFFIXES = {".py", ".sh", ".ps1", ".bat", ".md", ".yml", ".yaml", ".txt", ".sql", ".conf", ".env", ".example"}
_SKIP_PARTS = {".git", "__pycache__", "log", "upload", "prev", ".pytest_cache"}


def _tracked_text_files():
    out = subprocess.run(["git", "ls-files"], cwd=_ROOT, capture_output=True, text=True, encoding="utf-8")
    if out.returncode != 0:
        pytest.skip("git ls-files unavailable")
    for rel in out.stdout.splitlines():
        p = _ROOT / rel
        parts = set(Path(rel).parts)
        if p.suffix.lower() in _TEXT_SUFFIXES and not (_SKIP_PARTS & parts) and p.is_file():
            yield rel, p


def test_no_tracked_file_names_the_old_maintenance_log_dir():
    offenders = []
    for rel, p in _tracked_text_files():
        if rel == "tests/test_maintenance_log_routing.py" or rel.startswith("doc/backlog/"):
            continue                                  # this guard; dated planning docs are history, not instructions
        for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if _OLD.search(line):
                offenders.append(f"{rel}:{n}: {line.strip()[:110]}")
    assert not offenders, "old maintenance log dir still referenced:\n" + "\n".join(offenders)


def test_common_sh_registers_exactly_the_two_families():
    src = _COMMON.read_text(encoding="utf-8")
    assert "WSL_LOG_OS=${WSL_LOG_OS:-/mnt/c/data/wsl/log/os}" in src
    assert "WSL_LOG_PG=${WSL_LOG_PG:-/mnt/c/data/wsl/log/docker/postgres}" in src
    body = re.search(r"maint_log_dir\(\)\s*\{.*?\n\}", src, re.S).group(0)
    assert re.findall(r"^\s*(\w+\*)\)", body, re.M) == ["wsl_*", "pg_*"]
    assert "/mnt/c/data/logs" not in src


def test_pg_capacity_csv_follows_the_resolved_dir():
    src = (_MAINT / "pg" / "pg_04_capacity_report.sh").read_text(encoding="utf-8")
    assert 'H="$MAINT_LOG_DIR/pg_capacity_history.csv"' in src
    assert "/mnt/c/data" not in src
    # the file is only meaningful after maint_start resolved the directory
    assert src.index("maint_start pg_04_capacity_report") < src.index("pg_capacity_history.csv\"")


def _bash() -> str:
    for cand in (shutil.which("bash"), r"C:\Program Files\Git\bin\bash.exe"):
        if cand and Path(cand).exists():
            return cand
    pytest.fail("bash not found: the routing behaviour test must run (install Git Bash or enable WSL)")


def _posix(p: Path) -> str:
    s = str(p).replace("\\", "/")
    return re.sub(r"^([A-Za-z]):", lambda m: "/" + m.group(1).lower(), s)


def _run(script: str, env: dict):
    bash = _bash()
    common = _posix(_COMMON)
    code = f'. "{common}"; maint_args; maint_start {script}; echo body; maint_end'
    e = {k: v for k, v in os.environ.items() if k != "MAINT_LOG_DIR"}     # never inherit a forced dir
    e.update(env)
    return subprocess.run([bash, "-c", code], capture_output=True, text=True, env=e, timeout=60)


def test_families_land_in_their_own_dirs_and_unknown_family_is_refused(tmp_path):
    os_dir, pg_dir = tmp_path / "os", tmp_path / "docker" / "postgres"
    env = {"WSL_LOG_OS": _posix(os_dir), "WSL_LOG_PG": _posix(pg_dir)}

    r = _run("wsl_99_probe", env)
    assert r.returncode == 0, r.stderr
    r = _run("pg_99_probe", env)
    assert r.returncode == 0, r.stderr
    assert [p.name.split("_2")[0] for p in os_dir.glob("*.log")] == ["wsl_99_probe"]
    assert [p.name.split("_2")[0] for p in pg_dir.glob("*.log")] == ["pg_99_probe"]

    r = _run("zzz_unknown", env)
    assert r.returncode == 3 and "no log directory registered" in r.stderr
    assert not list(tmp_path.rglob("zzz_unknown*"))


def test_maint_log_dir_env_still_forces_one_dir(tmp_path):
    one = tmp_path / "one"
    r = _run("pg_98_probe", {"MAINT_LOG_DIR": _posix(one)})
    assert r.returncode == 0, r.stderr
    r = _run("wsl_98_probe", {"MAINT_LOG_DIR": _posix(one)})
    assert r.returncode == 0, r.stderr
    assert sorted(p.name.split("_2")[0] for p in one.glob("*.log")) == ["pg_98_probe", "wsl_98_probe"]
