"""
Every production entry point must at least start: import cleanly and parse `--help`, in a CLEAN subprocess.

Why this exists (2026-09-26, SQLite retirement): two regressions slipped past a green unit-test suite and were
only found by running things for real —
  * removing `DB_PATH` from shared/db.py silently dropped the `.env` autoload from every entry point
    (autoload is disabled under pytest on purpose, so in-process tests cannot see it);
  * a canonical launcher kept passing `--db` to scripts that had dropped the flag (argparse exit 2).
A subprocess per entry point exercises the real import chain and argument parser exactly as a launcher does.
`--help` never touches the database, so this is safe and needs no DSN.
"""
from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent

# (label, argv after the interpreter, cwd relative to the repo root)
_ENTRY_POINTS = [
    ("run_block",              ["run_block.py", "--help"], "proyecto1"),
    ("mark_stale",             ["scripts/launch/mark_stale.py", "--help"], "."),
    ("p1_db_harvest",          ["proyecto1/harvest/p1_db_harvest.py", "--help"], "."),
    ("p1_kiid_sync",           ["proyecto1/harvest/p1_kiid_sync.py", "--help"], "."),
    ("fund_family_builder",    ["proyecto1/core/fund_family_builder.py", "--help"], "."),
    ("audit_benchmark",        ["proyecto1/tools/audit_benchmark_consistency.py", "--help"], "."),
    ("statistical_audit",      ["scripts/audit/run_statistical_audit.py", "--help"], "."),
    ("diag_cost_extraction",   ["scripts/diag/diag_cost_extraction.py", "--help"], "."),
    ("archive_sqlite",         ["scripts/ops/archive_sqlite.py", "--help"], "."),
    ("export_p1",              ["-m", "proyecto1.src.analysis.export_p1", "--help"], "."),
    ("export_metrics",         ["-m", "proyecto2.src.analysis.export_metrics", "--help"], "."),
    ("nav_discovery",          ["-m", "proyecto2.src.discovery.nav_discovery", "--help"], "."),
    ("macro_discovery",        ["-m", "proyecto2.src.discovery.macro_discovery", "--help"], "."),
    ("run_pipeline",           ["-m", "proyecto2.src.pipeline.run_pipeline", "--help"], "."),
    ("rolling_dashboard",      ["-m", "proyecto2.src.reports.rolling_dashboard", "--help"], "."),
    ("benchmark_loader",       ["-m", "proyecto1.src.loaders.benchmark_loader", "--help"], "."),
]


def _run(spec):
    label, argv, cwd = spec
    env = {k: v for k, v in os.environ.items() if k != "PYTEST_CURRENT_TEST"}
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", *argv], cwd=str(_ROOT / cwd), env=env,
        capture_output=True, text=True, timeout=180, stdin=subprocess.DEVNULL,
    )
    return label, proc.returncode, (proc.stderr or proc.stdout)[-500:]


def test_every_entry_point_starts_and_parses_its_arguments():
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(_run, _ENTRY_POINTS))
    failures = [f"{label}: rc={rc}\n    {tail.strip()[-300:]}" for label, rc, tail in results if rc != 0]
    assert not failures, "entry points that do not start:\n  " + "\n  ".join(failures)


def test_the_smoke_list_covers_every_launcher_python_call():
    """A launcher that calls a Python entry point missing from _ENTRY_POINTS would escape the smoke test."""
    listed = " ".join(" ".join(argv) for _, argv, _ in _ENTRY_POINTS)
    missing = []
    for bat in sorted((_ROOT / "scripts" / "launch").glob("*.bat")):
        for line in bat.read_text(encoding="utf-8", errors="replace").splitlines():
            code = line.strip()
            if code.startswith(("::", "rem ", "REM ")) or "python" not in code.lower() and "%PYTHON%" not in code:
                continue
            for tok in code.replace('"', " ").split():
                tok = tok.replace("%ROOT%\\", "").replace("\\", "/")
                if tok.endswith(".py") and "/" in tok and tok not in listed and Path(tok).name not in listed:
                    if (_ROOT / tok).exists() or tok.startswith("scripts/"):
                        missing.append(f"{bat.name}: {tok}")
    # p1p2_state / p3_build_portfolio / mark_stale helpers have no --help contract of their own
    ignore = ("p1p2_state.py", "p3_build_portfolio.py", "run_pytest")
    missing = [m for m in missing if not any(i in m for i in ignore)]
    assert not missing, "launcher entry points not covered by the smoke test:\n  " + "\n  ".join(sorted(set(missing)))
