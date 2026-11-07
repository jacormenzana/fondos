#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""impact_selection_evidence.py -- the evidence harness of the impact selector (FND-0385; plan sections 5 and 6). It measures; it never changes the repository, the receipts of a delivery or the live database.
Every run happens in a scratch clone of an exact commit (a real git repository) with the hermetic throwaway Postgres of scripts/ops/run_pg_tests.py.

    python scripts/audit/impact_selection_evidence.py baseline --work DIR [--sha HEAD] [--runs 1]   # E1: per-test verdicts and durations of the full suite (flakes when --runs > 1)
    python scripts/audit/impact_selection_evidence.py trace    --work DIR [--sha HEAD]              # E2: one full run with shared/testing/impact_trace.py, then the comparison below
    python scripts/audit/impact_selection_evidence.py compare  --work DIR [--sha HEAD]              # E2: every file a test ran must be in the selector's static closure of that test
    python scripts/audit/impact_selection_evidence.py replay   [--limit N]                          # E3: the selector at the commit of every recorded full receipt, against the tests that failed there
    python scripts/audit/impact_selection_evidence.py mutate   --work DIR [--only M01,M02] [--batch 6]  # E4: mutants of the channels the selector must see; a failing test outside the selection is an ESCAPE

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


def run_clone(clone: Path, label_dir: Path, *, traced: bool = False, plan: Optional[dict] = None) -> dict:
    """One run of the tests in an existing scratch `clone` (hermetic Postgres, clean environment): the whole suite, or only `plan` ({suite: [path or path::test]}) through run_pg_tests.py --suite-files.
    Returns {rc, seconds, tests, clone, log}. Never touches the repository tree."""
    label_dir.mkdir(parents=True, exist_ok=True)
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
    if plan is not None:
        plan_file = label_dir / "plan.json"
        plan_file.write_text(json.dumps(plan, sort_keys=True), encoding="utf-8")
        cmd += ["--suite-files", str(plan_file)]
    log = label_dir / "run.log"
    with open(log, "w", encoding="utf-8", errors="replace") as fh:
        r = subprocess.run(cmd, cwd=clone, env=env, stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, timeout=da.TIMEOUT_S["full"] * 2)
    files = set(da.available_tests(clone))
    return {"rc": r.returncode, "seconds": round(time.time() - t0, 1), "tests": parse_junit(junit, files), "clone": str(clone), "log": str(log)}


def run_full(sha: str, label_dir: Path, *, traced: bool) -> dict:
    """One full run of `sha` in a fresh clone under `label_dir`. The clone is kept (the trace comparison reads it)."""
    label_dir.mkdir(parents=True, exist_ok=True)
    clone = label_dir / "clone"
    if clone.exists():
        shutil.rmtree(clone, ignore_errors=True)
    checkout_tree(sha, clone, ROOT)
    return run_clone(clone, label_dir, traced=traced)


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
    always = ti.always_run(graph, [t for t in sources if ti.suite_of(t)])                  # these run in EVERY impact receipt: whatever they execute is covered without the graph
    holes: dict = {}
    checked = 0
    for test, ran in sorted(dynamic.items()):
        if not ti.suite_of(test) or test in always:
            continue
        checked += 1
        reach = ti.fwd(graph, test)                                                          # the same per-test closure the selector uses (a stub tree cuts launcher edges)
        extra = sorted(f for f in ran if f != test and f not in reach and f in sources and f.endswith(".py") and not ti.is_infra(f) and not ti.is_excluded(f))      # `f in sources`: a file a test creates and deletes at run time is in no commit, so it can never be a changed file
        if extra:
            holes[test] = extra
    return {"tests_checked": checked, "tests_with_holes": len(holes), "holes": holes, "always_run_exempt": len(always & {t for t in dynamic})}


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
                     "failed": failed, "failed_missed": [] if sel is None else ti.find_escapes(failed, sel),
                     "files": [] if d is None or d.scope != "impact" else d.files(), "ts": r.get("ts"), "commits": max(1, len(r.get("covers") or []))})
    return rows


# ── E5: what the selector would have saved, from measured durations ─────────────

def simulate_saving(rows: list, file_seconds: dict, overhead_s: float, *, max_commits: int = 5, max_hours: float = 24.0) -> dict:
    """The net time over a chronological list of replayed full receipts if the selector had been in ENFORCE: an impact-eligible receipt costs `overhead_s` (container + DDL) plus the measured seconds of
    its selected files, unless the freshness rule F7 forces a full run (more than `max_commits` commits or `max_hours` since the last full run, or none yet), in which case it costs what it really cost and
    becomes the new anchor. A receipt the selector sends to full costs what it really cost. Pure."""
    from datetime import datetime
    anchor_ts, since = None, 0
    full_s = impact_s = 0.0
    n_impact = n_forced = 0
    for x in rows:
        actual = float(x.get("seconds") or 0)
        ts = datetime.fromisoformat(x["ts"]) if x.get("ts") else None
        full_s += actual
        stale = anchor_ts is None or since + x.get("commits", 1) > max_commits or (ts is not None and (ts - anchor_ts).total_seconds() > max_hours * 3600)
        if x.get("decision") == "impact" and not stale:
            n_impact += 1
            impact_s += overhead_s + sum(float(file_seconds.get(f.split("::", 1)[0], 0.0)) for f in x.get("files", []))
            since += x.get("commits", 1)
        else:
            if x.get("decision") == "impact":
                n_forced += 1
            impact_s += actual
            anchor_ts, since = ts or anchor_ts, 0
    return {"receipts": len(rows), "ran_impact": n_impact, "impact_forced_full_by_freshness": n_forced, "seconds_full": round(full_s), "seconds_with_selector": round(impact_s),
            "net_saving": round(1 - impact_s / full_s, 3) if full_s else 0.0}


def cmd_saving(args) -> int:
    work = Path(args.work)
    base = json.loads((work / "baseline.json").read_text(encoding="utf-8"))
    rows = json.loads((work / "replay.json").read_text(encoding="utf-8"))["rows"]
    res = simulate_saving(rows, base["per_file_seconds"], args.overhead)
    res["overhead_s"] = args.overhead
    (work / "saving.json").write_text(json.dumps(res, indent=1, sort_keys=True), encoding="utf-8")
    print(f"[E5] {res['ran_impact']} of {res['receipts']} receipts would run impact ({res['impact_forced_full_by_freshness']} forced full by freshness): "
          f"{res['seconds_full']} s -> {res['seconds_with_selector']} s = net saving {res['net_saving']:.0%} (overhead {args.overhead:.0f} s per impact run; PROJECTION from measured durations)")
    return 0


# ── finalize: the only writer of the evidence line ──────────────────────────────

def evaluate_evidence(work: Path, *, min_killed: int = 30, max_escape_bound: float = 0.10, min_saving: float = 0.20) -> dict:
    """Every acceptance check of the plan, read from the result files in `work`. A missing file is a failed check. Pure on its files."""
    def load(name):
        p = work / name
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    base, trace, replay, mut, sav = load("baseline.json"), load("trace_vs_graph.json"), load("replay.json"), load("mutation.json"), load("saving.json")
    checks = {
        "E1 baseline ran and is green": bool(base) and all(r["rc"] == 0 for r in base["runs"]) and not base["failed_in_first_run"],
        "E1 no flaky test": bool(base) and not base["flaky"],
        "E2 no file ran outside the static closure": bool(trace) and trace["tests_checked"] > 300 and trace["tests_with_holes"] == 0,
        "A10 the tracer changed no verdict": bool(trace) and bool(trace.get("a10")) and trace["a10"]["count"] == 0,
        "E3 no recorded failure falls outside its selection": bool(replay) and replay["missed"] == 0 and replay["rows"] != [],
        f"E4 at least {min_killed} impact-decided mutants killed": bool(mut) and mut["impact_decided_and_killed"] >= min_killed,
        "E4 zero escapes": bool(mut) and mut["escapes"] == 0,
        f"E4 escape-rate 95% upper bound <= {max_escape_bound}": bool(mut) and mut["escape_rate_upper_95"] is not None and mut["escape_rate_upper_95"] <= max_escape_bound,
        "E4 every security mutant trips its policy test": bool(mut) and bool(mut["security_detected"]) and all(mut["security_detected"]),
        f"A8 projected net saving >= {min_saving:.0%}": bool(sav) and sav["net_saving"] >= min_saving,
    }
    return {"checks": checks, "pass": all(checks.values()), "sha": (base or {}).get("sha")}


def cmd_finalize(args) -> int:
    ev = evaluate_evidence(Path(args.work), min_killed=args.min_killed)
    for name, ok in ev["checks"].items():
        print(f"[final] {'PASS' if ok else 'FAIL'}  {name}")
    if not ev["pass"]:
        print("[final] NOT all checks pass: no evidence line is written")
        return 1
    if not args.write:
        print("[final] all checks pass; run again with --write to append the evidence line (it lets the receipts promote the selector once their own streak is clean)")
        return 0
    if not da.acquire(da.LOCK):
        print("[final] a delivery attest is running; try again afterwards", file=sys.stderr)
        return 3
    try:
        from datetime import datetime, timezone
        line = {"kind": "impact_evidence", "algo": ti.ALGO_VERSION, "result": "pass", "sha": ev["sha"], "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "checks": ev["checks"]}
        line["digest"] = dr.digest_of(line)
        da.RECEIPTS.parent.mkdir(parents=True, exist_ok=True)
        with open(da.RECEIPTS, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(line, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())
    finally:
        da.release(da.LOCK)
    print(f"[final] evidence line for algorithm {ti.ALGO_VERSION} appended to {da.RECEIPTS}")
    base = json.loads((Path(args.work) / "baseline.json").read_text(encoding="utf-8"))               # the measured seconds per test file: the selector's time rule (F5) reads them
    da.DURATIONS.write_text(json.dumps({"sha": base["sha"], "per_file_seconds": base["per_file_seconds"]}, sort_keys=True), encoding="utf-8")
    return 0


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
    missed = sum(len(x["failed_missed"]) for x in rows)
    print(f"[E3] {len(rows)} full receipts; {len(impact)} impact-eligible = {elig / tot:.0%} of their {tot:.0f} s; failures the selection would have missed: {missed}" if tot else "[E3] no receipts")
    if getattr(args, "work", None):
        Path(args.work).mkdir(parents=True, exist_ok=True)
        (Path(args.work) / "replay.json").write_text(json.dumps({"rows": rows, "missed": missed}, indent=1, sort_keys=True), encoding="utf-8")
    return 1 if missed else 0


# ── E4: mutation of the channels the selector must see ──────────────────────────
# A mutant makes ONE thing break for sure (a function raises on entry, an SQL keyword is misspelt, an environment variable is renamed at its read site, a launcher exits, a data file is corrupted, a
# function a test patches by string is renamed). Every test that really breaks under it is a test the selection MUST contain: a failing test file outside the selection is an ESCAPE.
# To stay affordable several mutants in different files are applied at once and run as ONE full suite; each failing file is then attributed by cheap reruns (plan_attribution).

import ast  # noqa: E402
import random  # noqa: E402
import re  # noqa: E402

# Security mutants: new files that break a convention the whole-tree policy tests guard. Their content is ASSEMBLED from fragments, and their notes avoid the trigger words: written out contiguously in this
# file (a production path) they would be exactly what those policy tests forbid, and they failed this very commit's receipt once (found by the first traced full run, 2026-11-07).
_SECURITY = [
    ("shared/impact_mut_sqlite.py", ("import ", "sqlite", "3\n"), "a module imports the retired SQLite driver", "tests/test_dialect_coverage.py"),
    ("scripts/audit/impact_mut_insert.py", ("Q = \"INSERT INTO ", "backlog", ".backlog (action_point_id) VALUES ('X')\"\n"), "a raw write to the ticket table outside the single door", "tests/test_backlog_single_door.py"),
    ("shared/impact_mut_writer.py", ("ACTOR = 'sys", "tem:impact-mut-writer'\n"), "an automatic writer identity that is not registered", "tests/test_backlog_writers.py"),
    ("proyecto2/src/impact_mut_log.py", ("P = 'lo", "gs/impact_mut.log'\n"), "a module writes under a log directory at the repository root", "tests/test_log_paths.py"),
]
AREAS = {"p1-core": "proyecto1/core/", "p1-blocks": "proyecto1/blocks/", "p2": "proyecto2/src/", "p3": "proyecto3/src/", "shared": "shared/", "audit": "scripts/audit/"}
MUTANT_RAISE = 'raise RuntimeError("IMPACT_MUTANT")'


def _first_real_stmt(fn: ast.AST):
    body = list(fn.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) and isinstance(body[0].value.value, str):
        body = body[1:]                                                                # keep the docstring a docstring
    return body[0] if body else None


def raise_mutant(source: str) -> Optional[tuple]:
    """(mutated source, function name) with `raise RuntimeError` as the first real statement of the first public top-level function that has a body worth breaking; None when there is none. Pure."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    lines = source.splitlines(keepends=True)
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name.startswith("_"):
            continue
        stmt = _first_real_stmt(node)
        if stmt is None or lines[stmt.lineno - 1][: stmt.col_offset].strip():           # a one-line `def f(): return x` cannot take a statement before its body
            continue
        lines.insert(stmt.lineno - 1, " " * stmt.col_offset + MUTANT_RAISE + "\n")
        out = "".join(lines)
        try:
            ast.parse(out)
        except SyntaxError:
            return None
        return out, node.name
    return None


def gen_mutants(sources: dict, graph: "ti.Graph", *, seed: int = 20261107, per_area: int = 4) -> list:
    """The mutant catalogue for a tree: deterministic for a seed. Each mutant = {id, kind, channel, files: {path: new text}, note}. Pure."""
    rng = random.Random(seed)
    tests = set(ti.suite_tests(p for p in sources if p.endswith(".py")))
    n_tests = len(tests)
    mutants: list = []

    def add(kind, channel, files, note, **extra):
        mutants.append({"id": f"M{len(mutants) + 1:02d}", "kind": kind, "channel": channel, "files": files, "note": note, **extra})

    for area, prefix in AREAS.items():                                                   # 1. a function raises: in-process, loader-bound, indirect
        pool = []
        for p in sorted(sources):
            if not (p.startswith(prefix) and ti.is_runtime_code(p)) or ti.is_infra(p) or p.endswith("__init__.py") or p in graph.parse_errors:
                continue
            reach = ti.closure(graph, [p]) & tests
            if reach and len(reach) <= 0.5 * n_tests and raise_mutant(sources[p]):
                pool.append((p, len(reach), bool(graph.rdeps.get(p, set()) & tests)))
        indirect = [x for x in pool if not x[2]]                                          # no test imports it directly: the selection reaches it through at least one other module
        direct = [x for x in pool if x[2]]
        picks = rng.sample(direct, min(per_area - (1 if indirect else 0), len(direct))) + (rng.sample(indirect, 1) if indirect else [])
        for p, n, is_direct in picks:
            new, fn = raise_mutant(sources[p])
            add("raise", "in-process" if is_direct else "indirect", {p: new}, f"{p}::{fn} raises; {n} test files reach it")
    test_text = {t: sources[t] for t in tests}

    def mutable(p: str) -> bool:
        """A runtime file the selector can actually narrow for: not test infrastructure, not a hub (a hub is always `full`, so mutating it tests nothing about the selection)."""
        return ti.is_runtime_code(p) and not ti.is_infra(p) and p not in graph.parse_errors and 0 < len(ti.closure(graph, [p]) & tests) <= 0.5 * n_tests

    # 2. SQL text: a keyword misspelt inside an SQL string constant of a runtime module
    sql = []
    for p in sorted(sources):
        if mutable(p) and p.startswith(("shared/", "proyecto1/", "proyecto2/src/", "proyecto3/src/")) and " FROM " in sources[p] and "SELECT" in sources[p]:
            try:
                for n in ast.walk(ast.parse(sources[p])):
                    if isinstance(n, ast.Constant) and isinstance(n.value, str) and "SELECT" in n.value and " FROM " in n.value:
                        sql.append((p, n.lineno, n.end_lineno))
                        break
            except SyntaxError:
                continue
    for p, a, b in rng.sample(sql, min(3, len(sql))):
        ls = sources[p].splitlines(keepends=True)
        for i in range(a - 1, b):
            if " FROM " in ls[i]:
                ls[i] = ls[i].replace(" FROM ", " FORM ", 1)
                add("sql", "sql-text", {p: "".join(ls)}, f"{p}:{i + 1} FROM -> FORM")
                break
    # 3. an environment variable renamed at its read site, where some test sets it
    env_re = re.compile(r'''(?:environ\.get\(|environ\[|getenv\()\s*["'](FONDOS_[A-Z0-9_]+)["']''')
    envs = []
    for p in sorted(sources):
        if mutable(p) and p.startswith(("shared/", "proyecto1/", "proyecto2/src/", "proyecto3/src/")):
            for m in env_re.finditer(sources[p]):
                if any(m.group(1) in txt for txt in test_text.values()) and "DSN" not in m.group(1):
                    envs.append((p, m.group(1)))
                    break
    for p, var in rng.sample(envs, min(3, len(envs))):
        add("env", "env-var", {p: sources[p].replace(f'"{var}"', f'"{var}_IMPACTMUT"').replace(f"'{var}'", f"'{var}_IMPACTMUT'")}, f"{p}: {var} renamed at its read sites")
    # 4. a function a test patches by string is renamed
    patch_re = re.compile(r'''patch\(\s*["']([\w.]+)\.(\w+)["']''')
    patched = []
    for t, txt in sorted(test_text.items()):
        for m in patch_re.finditer(txt):
            mod = m.group(1).replace(".", "/") + ".py"
            for cand in (mod, *[k for k in sources if k.endswith("/" + mod)]):
                if cand in sources and ti.is_runtime_code(cand) and re.search(rf"^def {m.group(2)}\(", sources[cand], re.M):
                    patched.append((cand, m.group(2), t))
                    break
    for p, fn, t in rng.sample(patched, min(3, len(patched))):
        add("patch-string", "patch-string", {p: re.sub(rf"^def {fn}\(", f"def {fn}_impactmut(", sources[p], count=1, flags=re.M)}, f"{p}: {fn} renamed; patched by string in {t}")
    # 5. a launcher exits at once
    for p in sorted(sources):
        if p.startswith("scripts/launch/") and p.endswith(".bat") and any(p.rsplit("/", 1)[-1] in txt for txt in test_text.values()):
            add("launcher", "launcher", {p: "exit /b 77\n" + sources[p]}, f"{p} exits with 77")
            break
    # 6. a data file a test names is corrupted
    data = [p for p in sorted(sources) if p.endswith((".json", ".yaml", ".yml")) and da.is_code_path(p) and any(p.rsplit("/", 1)[-1] in txt for txt in test_text.values())]
    for p in rng.sample(data, min(2, len(data))):
        add("data-file", "data-file", {p: "{{{ not valid\n"}, f"{p} corrupted")
    # 7. security: code that breaks a convention of the whole tree (new files: the policy tests must fail, and they are in every selection)
    for path, parts, note, expects in _SECURITY:
        add("security", "policy", {path: "".join(parts)}, note, expects=expects)
    return mutants


def mutant_batches(mutants: list, size: int) -> list:
    """Groups of at most `size` mutants that touch pairwise different files (so they can be applied together). Pure."""
    batches: list = []
    for m in mutants:
        for b in batches:
            if len(b) < size and not (set(m["files"]) & {f for x in b for f in x["files"]}):
                b.append(m)
                break
        else:
            batches.append([m])
    return batches


def plan_attribution(failed_files: list, selections: dict) -> dict:
    """Which mutants could be the culprit of each failing test file, by selection. `selections` = {mutant id: set of test files its selection contains, or None when its decision was `full`}. Returns
    {test file: {"candidates": [ids whose selection contains it], "others": [ids whose selection does not]}}: the file is explained when a rerun with the candidates alone still fails; if it passes there but
    fails with the others, the others' selector missed it (an escape). Pure."""
    out = {}
    for t in failed_files:
        cands = sorted(m for m, sel in selections.items() if sel is None or t in sel)
        out[t] = {"candidates": cands, "others": sorted(set(selections) - set(cands))}
    return out


def _reset(clone: Path) -> None:
    """Put the scratch clone back to the pristine commit (it is OUR clone; never the repository)."""
    subprocess.run(["git", "-C", str(clone), "checkout", "-q", "--", "."], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(clone), "clean", "-fdq"], capture_output=True, check=True)


def _apply(clone: Path, mutants: list) -> None:
    for m in mutants:
        for rel, text in m["files"].items():
            p = clone / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8", newline="")


def _failed_files(tests: dict) -> set:
    return {t.split("::", 1)[0] for t, (o, _s) in tests.items() if o in ("failed", "error")}


def _plan_for(files) -> dict:
    """{suite: [entries]} for run_pg_tests.py --suite-files; an entry may carry `::test` (a policy entry)."""
    plan: dict = {}
    for f in sorted(files):
        plan.setdefault(ti.suite_of(f.split("::", 1)[0]) or "tests", []).append(f)
    return plan


def cmd_selective(args) -> int:
    """E3 second half and the calibration of E5: run the SELECTED tests of a historical full receipt's commit in a scratch clone, and compare with what the full run said. The verdict must match
    (the same failing test files), and the seconds are the real cost of an impact run, container and all."""
    work = Path(args.work) / f"selective_{args.sha[:8]}"
    receipts = da.read_receipts(da.RECEIPTS)
    rec = next((r for r in reversed(receipts) if r.get("scope") == "full" and r.get("sha", "").startswith(args.sha)), None)
    if not rec:
        print(f"[E3s] no full receipt for {args.sha}", file=sys.stderr)
        return 2
    clone = work / "clone"
    shutil.rmtree(clone, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    checkout_tree(rec["sha"], clone, ROOT)
    shutil.copy(ROOT / "scripts" / "ops" / "run_pg_tests.py", clone / "scripts" / "ops" / "run_pg_tests.py")      # the ORCHESTRATOR (container, DDL, --suite-files, --junit-dir) of today; the code under test is the commit's
    base = None
    if any(not (clone / c).exists() for c in rec["changed"]) and rec.get("base") and rec["base"] != da.EMPTY_TREE:
        btmp = work / "base"
        shutil.rmtree(btmp, ignore_errors=True)
        export_tree(rec["base"], btmp, ROOT)
        base = da.read_sources(btmp)
    d = ti.decide(rec["changed"], da.read_sources(clone), base, policy_tests=dr.POLICY_TESTS)
    if d.scope != "impact":
        print(f"[E3s] {args.sha[:8]}: the selector says full ({d.reasons[0][:100]}); nothing to compare")
        return 2
    res = run_clone(clone, work / "run", plan=_plan_for(d.files()))
    failed = sorted(_failed_files(res["tests"]))
    recorded = sorted({i.split("::", 1)[0] for i in (rec.get("detail", "").split("| failed: ", 1)[1].split(" | ", 1)[0].split(", ") if "| failed: " in rec.get("detail", "") else [])})
    same = (rec["result"] == "pass") == (not failed) and (not recorded or set(recorded) <= set(failed))
    out = {"sha": rec["sha"], "recorded_result": rec["result"], "recorded_failed_files": recorded, "selected_files": d.selected_count, "policy_entries": len(d.policy),
           "selective_rc": res["rc"], "selective_failed_files": failed, "selective_seconds": res["seconds"], "full_seconds_recorded": rec["seconds"], "same_verdict": same}
    (Path(args.work) / f"selective_{args.sha[:8]}.json").write_text(json.dumps(out, indent=1, sort_keys=True), encoding="utf-8")
    print(f"[E3s] {args.sha[:8]}: recorded {rec['result']}, selective {'fail' if failed else 'pass'} {failed}; {res['seconds']} s vs {rec['seconds']} s full; same verdict: {same}")
    return 0 if same else 1


def cmd_mutate(args) -> int:
    work = Path(args.work) / "mutate"
    sha = da._git("rev-parse", "--verify", f"{args.sha}^{{commit}}", cwd=ROOT).strip()
    clone = work / "clone"
    shutil.rmtree(clone, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    checkout_tree(sha, clone, ROOT)
    sources = da.read_sources(clone)
    graph = ti.build_graph(sources)
    all_tests = da.available_tests(clone)
    mutants = gen_mutants(sources, graph, seed=args.seed, per_area=args.per_area)
    if args.only:
        mutants = [m for m in mutants if m["id"] in set(args.only.split(","))]
    if args.limit:
        mutants = mutants[: args.limit]
    baseline_failed: set = set()
    base = Path(args.work) / "baseline.json"
    if base.exists():
        baseline_failed = {t.split("::", 1)[0] for t in json.loads(base.read_text(encoding="utf-8")).get("failed_in_first_run", [])}
    selections: dict = {}
    for m in mutants:                                                                     # what the selector says for each mutant, on the mutated tree
        d = ti.decide(list(m["files"]), {**sources, **m["files"]}, None, policy_tests=dr.POLICY_TESTS)
        m["decision"] = d.scope
        m["reasons"] = d.reasons[:1]
        m["selection"] = d.files() if d.scope == "impact" else None
        selections[m["id"]] = None if d.scope == "full" else {f.split("::", 1)[0] for f in d.files()}
    print(f"[E4] {len(mutants)} mutants; {sum(1 for m in mutants if m['decision'] == 'impact')} decided impact", flush=True)
    results = {m["id"]: {"kind": m["kind"], "channel": m["channel"], "note": m["note"], "decision": m["decision"], "failing": [], "escapes": [], "unattributed": []} for m in mutants}
    by_id = {m["id"]: m for m in mutants}
    for bi, batch in enumerate(mutant_batches(mutants, args.batch)):
        ids = [m["id"] for m in batch]
        _reset(clone)
        _apply(clone, batch)
        res = run_clone(clone, work / f"batch{bi}")
        failed = _failed_files(res["tests"]) - baseline_failed
        print(f"[E4] batch {bi} {ids}: {res['seconds']} s, {len(failed)} failing test files", flush=True)
        plan = plan_attribution(sorted(failed), {i: selections[i] for i in ids})
        groups: dict = {}
        for t, p in plan.items():
            groups.setdefault((tuple(p["candidates"]), tuple(p["others"])), set()).add(t)
        for gi, ((cands, others), files) in enumerate(sorted(groups.items())):
            explained: set = set()
            if cands:                                                                     # rerun the group's files with only the mutants whose selection contains them
                _reset(clone)
                _apply(clone, [by_id[i] for i in cands])
                rerun = run_clone(clone, work / f"batch{bi}_g{gi}c", plan=_plan_for(files))
                explained = _failed_files(rerun["tests"]) & files
                for i in cands:
                    results[i]["failing"] += sorted(explained)
            rest = files - explained
            if rest and others:                                                           # still unexplained: do the mutants whose selection lacks them break them? then it is an escape
                _reset(clone)
                _apply(clone, [by_id[i] for i in others])
                rerun = run_clone(clone, work / f"batch{bi}_g{gi}o", plan=_plan_for(rest))
                escaped = _failed_files(rerun["tests"]) & rest
                for i in others:
                    results[i]["escapes"] += sorted(escaped)
                rest = rest - escaped
            for i in cands + others:
                results[i]["unattributed"] += sorted(rest)
    for m in mutants:                                                                     # a security mutant must trip its policy test
        if m["kind"] == "security":
            want = m.get("expects")
            results[m["id"]]["expected_policy_test"] = want
            results[m["id"]]["detected"] = want in results[m["id"]]["failing"]
    n_i = sum(1 for m in mutants if m["decision"] == "impact" and results[m["id"]]["failing"])
    esc = sum(len(r["escapes"]) for r in results.values())
    summary = {"sha": sha, "seed": args.seed, "mutants": len(mutants), "killed": sum(1 for r in results.values() if r["failing"]), "impact_decided_and_killed": n_i,
               "escapes": esc, "escape_rate_upper_95": None if not n_i else round(min(1.0, 3 / n_i), 3),
               "security_detected": [r.get("detected") for r in results.values() if r["kind"] == "security"], "results": results}
    out = Path(args.work) / "mutation.json"
    out.write_text(json.dumps(summary, indent=1, sort_keys=True), encoding="utf-8")
    print(f"[E4] killed {summary['killed']}/{summary['mutants']}; impact-decided and killed {n_i}; ESCAPES {esc}; 95% upper bound on the escape rate {summary['escape_rate_upper_95']}; wrote {out}")
    return 1 if esc else 0


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("baseline", "trace", "compare"):
        p = sub.add_parser(name)
        p.add_argument("--work", required=True, help="scratch directory (clones, logs, results); never inside the repository")
        p.add_argument("--sha", default="HEAD")
        if name == "baseline":
            p.add_argument("--runs", type=int, default=1)
    p = sub.add_parser("mutate")
    p.add_argument("--work", required=True)
    p.add_argument("--sha", default="HEAD")
    p.add_argument("--seed", type=int, default=20261107)
    p.add_argument("--per-area", type=int, default=4, help="raise-mutants per code area")
    p.add_argument("--batch", type=int, default=6, help="mutants applied together in one full run")
    p.add_argument("--only", help="comma-separated mutant ids")
    p.add_argument("--limit", type=int, default=0)
    p = sub.add_parser("replay")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--work", help="also save replay.json here (input of saving / finalize)")
    p = sub.add_parser("selective")
    p.add_argument("--work", required=True)
    p.add_argument("--sha", required=True, help="a commit with a recorded full receipt (at least 8 characters)")
    p = sub.add_parser("saving")
    p.add_argument("--work", required=True)
    p.add_argument("--overhead", type=float, default=90.0, help="seconds an impact run costs before its first test (container, DDL); calibrate it with a real selective run")
    p = sub.add_parser("finalize")
    p.add_argument("--work", required=True)
    p.add_argument("--min-killed", type=int, default=30)
    p.add_argument("--write", action="store_true", help="append the evidence line to the receipts file (only when every check passes)")
    args = ap.parse_args(argv)
    if getattr(args, "work", None) and Path(args.work).resolve().is_relative_to(ROOT):
        print("error: --work must be outside the repository", file=sys.stderr)
        return 2
    return {"baseline": cmd_baseline, "trace": cmd_trace, "compare": cmd_compare, "replay": cmd_replay, "mutate": cmd_mutate, "selective": cmd_selective, "saving": cmd_saving, "finalize": cmd_finalize}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
