#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""impact_selection_evidence.py -- the evidence harness of the impact selector (FND-0385; plan sections 5 and 6). It measures; it never changes the repository, the receipts of a delivery or the live database.
Every run happens in a scratch clone of an exact commit (a real git repository) with the hermetic throwaway Postgres of scripts/ops/run_pg_tests.py.

    python scripts/audit/impact_selection_evidence.py baseline --work DIR [--sha HEAD] [--runs 1]   # E1: per-test verdicts and durations of the full suite (flakes when --runs > 1)
    python scripts/audit/impact_selection_evidence.py trace    --work DIR [--sha HEAD]              # E2: one full run with shared/testing/impact_trace.py, then the comparison below
    python scripts/audit/impact_selection_evidence.py compare  --work DIR [--sha HEAD]              # E2: every file a test ran must be in the selector's static closure of that test
    python scripts/audit/impact_selection_evidence.py replay   [--limit N]                          # E3: the selector at the commit of every recorded full receipt, against the tests that failed there

Read-only on the repository: `baseline` and `trace` write only under --work; `replay` reads git and out/audit/delivery_receipts.jsonl. The evidence line that lets the selector ever leave SHADOW
(`kind: impact_evidence`) is written by `finalize`, and only from results that all passed (see that command)."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "scripts" / "audit")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import delivery_attest as da  # noqa: E402
from shared import delivery_receipt as dr  # noqa: E402
from shared import test_impact as ti  # noqa: E402
from shared.git_export import checkout_tree, export_tree  # noqa: E402


# ── a full run in a scratch clone ───────────────────────────────────────────────

def parse_junit(junit_dir: Path, test_files: set) -> dict:
    """{repo-relative test id (`file::name`): (outcome, seconds)} from the JUnit XML files of one full run. The classname is a dotted module path (plus a class); it is resolved against the real test
    files, and the proyecto2 run (cwd proyecto2/) gets its prefix back. Pure on its inputs."""
    out: dict = {}
    for xml in sorted(Path(junit_dir).glob("run*.xml")):
        prefix = "proyecto2/" if xml.stem.endswith("_proyecto2") else ""
        for case in ET.parse(xml).getroot().iter("testcase"):
            parts = (case.get("classname") or "").split(".")
            file = None
            for i in range(len(parts), 0, -1):
                cand = prefix + "/".join(parts[:i]) + ".py"
                if cand in test_files:
                    file = cand
                    break
            if file is None:
                file = prefix + (case.get("classname") or "?").replace(".", "/") + ".py"
            outcome = "error" if case.find("error") is not None else "failed" if case.find("failure") is not None else "skipped" if case.find("skipped") is not None else "passed"
            out[f"{file}::{case.get('name')}"] = (outcome, float(case.get("time") or 0.0))
    return out


def per_file(tests: dict) -> dict:
    """{test file: total seconds}. Pure."""
    acc: dict = {}
    for tid, (_o, secs) in tests.items():
        acc[tid.split("::", 1)[0]] = acc.get(tid.split("::", 1)[0], 0.0) + secs
    return {k: round(v, 2) for k, v in sorted(acc.items())}


def run_full(sha: str, label_dir: Path, *, traced: bool) -> dict:
    """One full run of `sha` in a fresh clone under `label_dir`; returns {rc, seconds, tests, files, clone}. The clone is kept (the trace comparison reads it)."""
    label_dir.mkdir(parents=True, exist_ok=True)
    clone = label_dir / "clone"
    if clone.exists():
        shutil.rmtree(clone, ignore_errors=True)
    checkout_tree(sha, clone, ROOT)
    junit = label_dir / "junit"
    shutil.rmtree(junit, ignore_errors=True)
    env = da._clean_env()
    if traced:
        trace_dir = label_dir / "trace"
        shutil.rmtree(trace_dir, ignore_errors=True)
        env.update(IMPACT_TRACE_DIR=str(trace_dir), IMPACT_TRACE_ROOT=str(clone), PYTEST_ADDOPTS="-p shared.testing.impact_trace")
    da._wait_for_cycle(60)
    t0 = time.time()
    cmd = [sys.executable, "scripts/ops/run_pg_tests.py", "--image", "postgres:17", "--junit-dir", str(junit)]
    log = label_dir / "run.log"
    with open(log, "w", encoding="utf-8", errors="replace") as fh:
        r = subprocess.run(cmd, cwd=clone, env=env, stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, timeout=da.TIMEOUT_S["full"] * 2)
    files = set(da.available_tests(clone))
    return {"rc": r.returncode, "seconds": round(time.time() - t0, 1), "tests": parse_junit(junit, files), "clone": str(clone), "log": str(log)}


# ── E1: baseline ────────────────────────────────────────────────────────────────

def cmd_baseline(args) -> int:
    work = Path(args.work)
    sha = da._git("rev-parse", "--verify", f"{args.sha}^{{commit}}", cwd=ROOT).strip()
    runs = []
    for i in range(args.runs):
        res = run_full(sha, work / f"baseline_{i}", traced=False)
        print(f"[E1] run {i}: rc {res['rc']}, {res['seconds']} s, {len(res['tests'])} tests", flush=True)
        runs.append(res)
    verdicts: dict = {}
    for res in runs:
        for tid, (outcome, _s) in res["tests"].items():
            verdicts.setdefault(tid, set()).add(outcome)
    flaky = sorted(t for t, v in verdicts.items() if len(v) > 1)
    first = runs[0]["tests"]
    summary = {"sha": sha, "runs": [{"rc": r["rc"], "seconds": r["seconds"], "tests": len(r["tests"])} for r in runs], "flaky": flaky,
               "failed_in_first_run": sorted(t for t, (o, _s) in first.items() if o in ("failed", "error")),
               "median_seconds": statistics.median(r["seconds"] for r in runs), "per_file_seconds": per_file(first),
               "per_test": {t: [o, round(s, 3)] for t, (o, s) in first.items()}}
    out = work / "baseline.json"
    out.write_text(json.dumps(summary, indent=1, sort_keys=True), encoding="utf-8")
    top = sorted(summary["per_file_seconds"].items(), key=lambda kv: -kv[1])[:8]
    print(f"[E1] wrote {out}; flaky {len(flaky)}; failed {len(summary['failed_in_first_run'])}; slowest files: {top}")
    return 0 if all(r["rc"] == 0 for r in runs) else 1


# ── E2: dynamic ground truth against the static closure ─────────────────────────

def forward_closure(graph: "ti.Graph", start: str) -> set:
    seen, stack = set(), [start]
    while stack:
        x = stack.pop()
        if x in seen:
            continue
        seen.add(x)
        stack.extend(graph.deps.get(x, ()))
    return seen


def compare_trace(sources: dict, dynamic: dict) -> dict:
    """The holes of the static graph: for every test file, the repo files that ran while it ran and are NOT in its static closure (infrastructure and the test file itself excluded). Pure."""
    graph = ti.build_graph(sources)
    holes: dict = {}
    checked = 0
    for test, ran in sorted(dynamic.items()):
        if not ti.suite_of(test):
            continue
        checked += 1
        reach = forward_closure(graph, test)
        extra = sorted(f for f in ran if f != test and f not in reach and f.endswith(".py") and not ti.is_infra(f) and not ti.is_excluded(f))
        if extra:
            holes[test] = extra
    return {"tests_checked": checked, "tests_with_holes": len(holes), "holes": holes}


def load_trace(trace_dir: Path) -> dict:
    merged: dict = {}
    for f in sorted(Path(trace_dir).glob("*.json")):
        for test, files in json.loads(f.read_text(encoding="utf-8")).items():
            merged.setdefault(test, set()).update(files)
    return merged


def cmd_trace(args) -> int:
    work = Path(args.work)
    sha = da._git("rev-parse", "--verify", f"{args.sha}^{{commit}}", cwd=ROOT).strip()
    res = run_full(sha, work / "traced", traced=True)
    print(f"[E2] traced run: rc {res['rc']}, {res['seconds']} s, {len(res['tests'])} tests", flush=True)
    return max(cmd_compare(args, traced_result=res), 0)


def cmd_compare(args, traced_result: Optional[dict] = None) -> int:
    work = Path(args.work)
    clone = Path(traced_result["clone"]) if traced_result else work / "traced" / "clone"
    dynamic = load_trace(work / "traced" / "trace")
    if not dynamic:
        print("[E2] no trace files: the plugin did not run", file=sys.stderr)
        return 2
    result = compare_trace(da.read_sources(clone), dynamic)
    base = work / "baseline.json"
    same = None
    if traced_result and base.exists():                                                  # A10: the instrument must not change a verdict
        b = json.loads(base.read_text(encoding="utf-8"))["per_test"]
        diff = sorted(t for t, (o, _s) in traced_result["tests"].items() if t in b and b[t][0] != o)
        same = {"verdict_differences_vs_baseline": diff[:50], "count": len(diff)}
    result["a10"] = same
    out = work / "trace_vs_graph.json"
    out.write_text(json.dumps(result, indent=1, sort_keys=True), encoding="utf-8")
    print(f"[E2] {result['tests_checked']} test files checked, {result['tests_with_holes']} with holes; wrote {out}")
    for t, extra in list(result["holes"].items())[:10]:
        print(f"   {t}: {extra[:4]}")
    return 0 if not result["holes"] else 1


# ── E3: historical replay (static half) ─────────────────────────────────────────

def replay_rows(receipts: list, decide_at) -> list:
    """For every full receipt: the selector's decision at that commit and whether each test that failed there was in the selection. `decide_at(receipt) -> Decision | None`. Pure given decide_at."""
    rows = []
    for r in receipts:
        if r.get("scope") != "full" or not r.get("sha"):
            continue
        failed = [i.split("::", 1)[0] for i in (r.get("detail", "").split("| failed: ", 1)[1].split(" | ", 1)[0].split(", ") if "| failed: " in r.get("detail", "") else [])]
        d = decide_at(r)
        sel = set(d.files()) | {p.split("::", 1)[0] for p in d.files()} if d is not None and d.scope == "impact" else None
        rows.append({"sha": r["sha"][:8], "result": r.get("result"), "seconds": r.get("seconds"), "decision": None if d is None else d.scope,
                     "reason": None if d is None or d.scope == "impact" else d.reasons[0][:80], "selected": None if d is None or d.scope != "impact" else d.selected_count,
                     "failed": failed, "failed_missed": [] if sel is None else ti.find_escapes(failed, sel)})
    return rows


def cmd_replay(args) -> int:
    receipts = da.read_receipts(da.RECEIPTS)

    def decide_at(r):
        if len(r.get("changed") or []) >= 200:
            return None
        tmp = Path(__import__("tempfile").mkdtemp(prefix="impact_replay_"))
        try:
            export_tree(r["sha"], tmp, ROOT)
            base = None
            if any(not (tmp / c).exists() for c in r["changed"]) and r.get("base") and r["base"] != da.EMPTY_TREE:
                btmp = Path(__import__("tempfile").mkdtemp(prefix="impact_replay_base_"))
                try:
                    export_tree(r["base"], btmp, ROOT)
                    base = da.read_sources(btmp)
                finally:
                    shutil.rmtree(btmp, ignore_errors=True)
            return ti.decide(r["changed"], da.read_sources(tmp), base, policy_tests=dr.POLICY_TESTS)
        except Exception as exc:                                                         # noqa: BLE001
            print(f"[E3] {r['sha'][:8]}: {type(exc).__name__}: {str(exc)[:100]}", file=sys.stderr)
            return None
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    full = [r for r in receipts if r.get("scope") == "full"]
    rows = replay_rows(full[-args.limit:] if args.limit else full, decide_at)
    impact = [x for x in rows if x["decision"] == "impact"]
    tot = sum(x["seconds"] or 0 for x in rows)
    elig = sum(x["seconds"] or 0 for x in impact)
    for x in rows:
        print(f"[E3] {x['sha']} {x['result']:5} {str(x['decision']):6} sel={x['selected']} failed={x['failed']} missed={x['failed_missed']} {x['reason'] or ''}")
    print(f"[E3] {len(rows)} full receipts; {len(impact)} impact-eligible = {elig / tot:.0%} of their {tot:.0f} s; "
          f"failures the selection would have missed: {sum(len(x['failed_missed']) for x in rows)}" if tot else "[E3] no receipts")
    return 1 if any(x["failed_missed"] for x in rows) else 0


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("baseline", "trace", "compare"):
        p = sub.add_parser(name)
        p.add_argument("--work", required=True, help="scratch directory (clones, logs, results); never inside the repository")
        p.add_argument("--sha", default="HEAD")
        if name == "baseline":
            p.add_argument("--runs", type=int, default=1)
    p = sub.add_parser("replay")
    p.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)
    if getattr(args, "work", None) and Path(args.work).resolve().is_relative_to(ROOT):
        print("error: --work must be outside the repository", file=sys.stderr)
        return 2
    return {"baseline": cmd_baseline, "trace": cmd_trace, "compare": cmd_compare, "replay": cmd_replay}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
