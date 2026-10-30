#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
p3_pit_variants.py -- the FND-0303 orchestrator (PREREG_FND-0303 sections 3 and 7). Owner-run; read-only on the database.

    C:\\Users\\jacor\\.conda\\envs\\des\\python.exe scripts\\launch\\p3_pit_variants.py --dry-run
    C:\\Users\\jacor\\.conda\\envs\\des\\python.exe scripts\\launch\\p3_pit_variants.py --gate-only                  # V0 twice: the determinism gate
    C:\\Users\\jacor\\.conda\\envs\\des\\python.exe scripts\\launch\\p3_pit_variants.py --gate-only --expect-v0-hash <sha>   # proves V0 did not move
    C:\\Users\\jacor\\.conda\\envs\\des\\python.exe scripts\\launch\\p3_pit_variants.py --variants V1,V3                  # gate first, then the variants

What it does, and nothing else (it is an ORCHESTRATOR, not an analysis):
  * every run is its own fresh interpreter (`python -I`), with its own parameter overlay FILE (FONDOS_SCORER_PARAMS_OVERLAY = an absolute
    path; the file is written into the variant folder and hashed), its own cold parquet cache and its own artifact folder, so nothing
    can leak from one variant into the next;
  * no variant is computed before the determinism gate has passed: V0 run twice must give an identical series_stats.json AND an identical
    pit_backtest_table.csv (sha256). `--reuse-gate <gate.json>` accepts a previous PASS only for the same git commit with a clean tree;
  * each run is wrapped by the memory guard (out/audit/guarded_run.ps1; rc 107 = killed by the guard) and a timeout; a failed or timed-out
    run is recorded and the orchestrator carries on with the next variant, whose verdict is then "incomplete" (the owner reruns it);
  * every run writes a machine-readable `variant_status.json` (the report reads only those files, never the logs).
A gate failure aborts everything. Exit codes: 0 all runs succeeded, 1 a variant failed (incomplete), 2 gate failed / usage, 3 comparability.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "launch" / "p3_pit_backtest.py"
GUARD = ROOT / "out" / "audit" / "guarded_run.ps1"
OUT_ROOT = ROOT / "out" / "reports" / "pit_variants"
OVERLAY_ENV = "FONDOS_SCORER_PARAMS_OVERLAY"
TIMEOUT_S = 90 * 60
RC_GUARD_KILLED = 107
RC_OK, RC_VARIANT_FAILED, RC_GATE_FAILED, RC_INCOMPARABLE = 0, 1, 2, 3

# V0 = the live construction: hysteresis band 10 %, 25 bp (PREREG_FND-0303 section 3). Every variant shares these arguments.
BASE_ARGS = ["--bps-main", "25", "--hysteresis-band", "0.10", "--hysteresis-grid", "", "--no-scores"]

# PREREG_FND-0303 section 2. All four variants are parameter overlays; V2 / V123 set COMMON_WINDOW_MONTHS (amendment A2). The
# `needs_v2` / V2_AVAILABLE pair is kept as a guard: if the common-window code is ever withdrawn they report "not_available" and are
# never silently run as V0.
VARIANTS: dict = {
    "V0":   {"overlay": {}, "needs_v2": False},
    "V1":   {"overlay": {"MIN_REAL_RETURN_BY_SUB": {"Defensiva": 0.0, "Equilibrada": 0.0, "Dinamica": 0.0}}, "needs_v2": False},
    "V3":   {"overlay": {"MAX_DRAWDOWN_BY_SUB": {"Defensiva": -0.10, "Equilibrada": -0.15, "Dinamica": -0.20}}, "needs_v2": False},
    "V2":   {"overlay": {"COMMON_WINDOW_MONTHS": 60}, "needs_v2": True},
    "V123": {"overlay": {"MIN_REAL_RETURN_BY_SUB": {"Defensiva": 0.0, "Equilibrada": 0.0, "Dinamica": 0.0},
                         "MAX_DRAWDOWN_BY_SUB": {"Defensiva": -0.10, "Equilibrada": -0.15, "Dinamica": -0.20},
                         "COMMON_WINDOW_MONTHS": 60}, "needs_v2": True},
}
V2_AVAILABLE = True           # the common-window code (parts A, B, C) is merged; the guard stays for a withdrawal


def sha256_file(path) -> "str | None":
    p = Path(path)
    if not p.is_file():
        return None
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_dir(path) -> "str | None":
    """One digest over every file of a folder (sorted relative names + contents)."""
    p = Path(path)
    if not p.is_dir():
        return None
    h = hashlib.sha256()
    for f in sorted(x for x in p.rglob("*") if x.is_file()):
        h.update(str(f.relative_to(p)).replace("\\", "/").encode("utf-8"))
        h.update((sha256_file(f) or "").encode("ascii"))
    return h.hexdigest()


def git_state(root: Path = ROOT) -> dict:
    def run(*a):
        return subprocess.run(["git", *a], cwd=root, capture_output=True, text=True).stdout.strip()
    return {"commit": run("rev-parse", "--short=12", "HEAD"), "clean": run("status", "--porcelain", "--untracked-files=no") == ""}


def write_overlay(variant_dir: Path, overlay: dict) -> "Path | None":
    """The overlay file of a variant (absolute path, audit artifact); None for V0, which runs on the base parameters."""
    if not overlay:
        return None
    path = (variant_dir / "overlay.yaml").resolve()
    path.write_text(yaml.safe_dump(overlay, sort_keys=True), encoding="utf-8")
    return path


def build_command(variant_dir: Path, runner: Path, extra_args: "list | None" = None, guard: "Path | None" = GUARD) -> list:
    """argv of one run: the memory guard (when given) around a fresh `python -I` interpreter."""
    inner = [sys.executable, "-I", "-X", "utf8", str(runner), *BASE_ARGS, "--cache-dir", str(variant_dir / "cache"),
             "--out-dir", str(variant_dir / "run"), *(extra_args or [])]
    if guard is None:
        return inner
    line = " ".join(f'"{a}"' if (" " in a or a == "") else a for a in inner)
    return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(guard), "-Log", str(variant_dir / "guard.log"),
            "-CommandLine", line]


def kill_tree(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        proc.kill()


def run_variant(name: str, out_root: Path, label: str, runner: Path = RUNNER, guard: "Path | None" = GUARD,
                timeout_s: int = TIMEOUT_S, overlay: "dict | None" = None, extra_args: "list | None" = None) -> dict:
    """Run one variant in its own interpreter and write its variant_status.json. Never raises on a failed child."""
    variant_dir = out_root / label
    variant_dir.mkdir(parents=True, exist_ok=True)
    spec = VARIANTS[name]
    overlay = spec["overlay"] if overlay is None else overlay
    status = {"variant": name, "label": label, "status": "not_run", "exit_code": None, "started": None, "ended": None,
              "out_dir": str(variant_dir / "run"), "stderr_path": str(variant_dir / "stderr.log"),
              "stdout_path": str(variant_dir / "stdout.log"), "git": git_state()}
    if spec["needs_v2"] and not V2_AVAILABLE:
        status["status"] = "not_available"
        status["reason"] = "the common-window code (COMMON_WINDOW_MONTHS) is not available"
        return _finish(variant_dir, status)

    ov = write_overlay(variant_dir, overlay)
    env = {k: v for k, v in os.environ.items() if k != OVERLAY_ENV}
    if ov is not None:
        env[OVERLAY_ENV] = str(ov)
    status["overlay_path"] = str(ov) if ov else None
    status["overlay_sha256"] = sha256_file(ov) if ov else None
    cmd = build_command(variant_dir, runner, extra_args, guard)
    status["started"] = datetime.now().isoformat(timespec="seconds")
    with open(variant_dir / "stdout.log", "wb") as so, open(variant_dir / "stderr.log", "wb") as se:
        proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=so, stderr=se)
        try:
            rc = proc.wait(timeout=timeout_s)
            status["exit_code"] = rc
            status["status"] = "success" if rc == 0 else ("oom" if rc == RC_GUARD_KILLED else "failed")
        except subprocess.TimeoutExpired:
            kill_tree(proc)
            proc.wait()
            status["status"], status["exit_code"] = "timeout", None
    status["ended"] = datetime.now().isoformat(timespec="seconds")
    run_dir = variant_dir / "run"
    status["series_stats_sha256"] = sha256_file(run_dir / "series_stats.json")
    status["table_sha256"] = sha256_file(run_dir / "pit_backtest_table.csv")
    status["inputs_sha256"] = sha256_dir(run_dir / "inputs")
    if status["status"] == "success" and not (status["series_stats_sha256"] and status["table_sha256"]):
        status["status"], status["reason"] = "failed", "the run exited 0 but wrote no series_stats.json / pit_backtest_table.csv"
    return _finish(variant_dir, status)


def _finish(variant_dir: Path, status: dict) -> dict:
    (variant_dir / "variant_status.json").write_text(json.dumps(status, indent=2, default=str), encoding="utf-8")
    return status


def evaluate_gate(a: dict, b: dict, expect_v0_hash: "str | None" = None) -> dict:
    """The determinism gate on two V0 status records (pure). PASS needs both runs successful, identical series_stats and table
    digests, identical inputs, and (when given) the digest the owner recorded before the V2 code merged."""
    reasons = []
    for s in (a, b):
        if s["status"] != "success":
            reasons.append(f"{s['label']} did not succeed ({s['status']})")
    if not reasons:
        if a["series_stats_sha256"] != b["series_stats_sha256"]:
            reasons.append("series_stats.json differs between the two V0 runs")
        if a["table_sha256"] != b["table_sha256"]:
            reasons.append("pit_backtest_table.csv differs between the two V0 runs")
        if a["inputs_sha256"] != b["inputs_sha256"]:
            reasons.append("the saved inputs differ between the two V0 runs (the database moved between them)")
        if expect_v0_hash and a["series_stats_sha256"] != expect_v0_hash:
            reasons.append(f"V0 series_stats sha256 {a['series_stats_sha256']} != the expected {expect_v0_hash}")
    return {"status": "PASS" if not reasons else "FAIL", "reasons": reasons, "git": a.get("git"),
            "v0_series_stats_sha256": a.get("series_stats_sha256"), "v0_table_sha256": a.get("table_sha256"),
            "v0_inputs_sha256": a.get("inputs_sha256"), "v0_run_dir": a.get("out_dir"), "expect_v0_hash": expect_v0_hash,
            "decided": datetime.now().isoformat(timespec="seconds")}


def gate_reusable(gate: dict, current_git: dict) -> "str | None":
    """None when a previous gate may be reused, otherwise the reason it may not."""
    if gate.get("status") != "PASS":
        return "the earlier gate is not a PASS"
    if not current_git["clean"]:
        return "the working tree has tracked changes"
    if (gate.get("git") or {}).get("commit") != current_git["commit"]:
        return f"the gate was passed at {(gate.get('git') or {}).get('commit')}, the code is now at {current_git['commit']}"
    return None


def comparable(v0: dict, other: dict) -> "str | None":
    """A variant is comparable with V0 only on identical saved inputs (same database state)."""
    if other.get("status") != "success":
        return None
    if other.get("inputs_sha256") != v0.get("v0_inputs_sha256"):
        return f"{other['variant']}: saved inputs differ from V0's (the database moved): INCOMPARABLE, rerun"
    return None


def main(argv=None, runner: Path = RUNNER, guard: "Path | None" = GUARD, timeout_s: int = TIMEOUT_S) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--variants", default="", help="comma list of V1,V3,V2,V123 to run after the gate (default: none = gate only)")
    ap.add_argument("--gate-only", action="store_true", help="run only the V0 determinism gate")
    ap.add_argument("--expect-v0-hash", default=None, help="sha256 of the V0 series_stats.json recorded before the V2 code merged")
    ap.add_argument("--reuse-gate", default=None, help="gate.json of an earlier PASS (same commit, clean tree)")
    ap.add_argument("--out-root", default=None)
    ap.add_argument("--timeout-min", type=int, default=timeout_s // 60)
    ap.add_argument("--no-guard", action="store_true", help="do not wrap the runs in guarded_run.ps1 (tests only)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    wanted = [v.strip().upper() for v in args.variants.split(",") if v.strip()]
    unknown = [v for v in wanted if v not in VARIANTS or v == "V0"]
    if unknown:
        print(f"unknown or redundant variant(s): {unknown}; choose from V1,V3,V2,V123", file=sys.stderr)
        return RC_GATE_FAILED
    if not wanted and not args.gate_only and not args.dry_run:
        print("nothing to do: pass --gate-only or --variants", file=sys.stderr)
        return RC_GATE_FAILED
    out_root = Path(args.out_root) if args.out_root else OUT_ROOT / f"FND-0303_{datetime.now():%Y%m%d_%H%M%S}"
    g = None if args.no_guard else guard
    t = args.timeout_min * 60

    plan = ["V0 (gate run A, cold)", "V0 (gate run B, cold)"] if not args.reuse_gate else ["(gate reused)"]
    plan += [f"{v}" + ("" if (not VARIANTS[v]["needs_v2"] or V2_AVAILABLE) else " [not available: V2 code not merged]") for v in wanted]
    print(f"git {git_state()} | out {out_root}\nplan: " + " -> ".join(plan))
    if args.dry_run:
        return RC_OK
    out_root.mkdir(parents=True, exist_ok=True)

    if args.reuse_gate:
        gate = json.loads(Path(args.reuse_gate).read_text(encoding="utf-8"))
        why = gate_reusable(gate, git_state())
        if why:
            print(f"cannot reuse the gate: {why}", file=sys.stderr)
            return RC_GATE_FAILED
    else:
        a = run_variant("V0", out_root, "V0_gate_A", runner, g, t)
        b = run_variant("V0", out_root, "V0_gate_B", runner, g, t) if a["status"] == "success" else \
            {**a, "label": "V0_gate_B", "status": "not_run"}
        gate = evaluate_gate(a, b, args.expect_v0_hash)
    (out_root / "gate.json").write_text(json.dumps(gate, indent=2, default=str), encoding="utf-8")
    print(f"determinism gate: {gate['status']} {gate['reasons']}")
    if gate["status"] != "PASS":
        return RC_GATE_FAILED
    if args.gate_only or not wanted:
        print(f"V0 series_stats sha256 (record it on FND-0303): {gate['v0_series_stats_sha256']}")
        return RC_OK

    worst = RC_OK
    for name in wanted:
        s = run_variant(name, out_root, name, runner, g, t)
        print(f"{name}: {s['status']}")
        if s["status"] not in ("success", "not_available"):
            worst = max(worst, RC_VARIANT_FAILED)
        problem = comparable(gate, s)
        if problem:
            print(problem, file=sys.stderr)
            worst = RC_INCOMPARABLE
    return worst


if __name__ == "__main__":
    sys.exit(main())
