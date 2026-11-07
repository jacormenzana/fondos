#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
delivery_attest.py -- runs the tests LOCALLY on the exact commit being delivered and leaves one receipt (FND-0375; plan ACTION_PLAN_BACKLOG_REFACTORING, Revision 2, R2.3). No CI: nothing runs on
push and nothing refuses a push; the deliverer runs this after committing (rule R2), and the `tests_pass` closing condition reads the receipt.

    python scripts/audit/delivery_attest.py                  # HEAD, the scope its change needs (docs_only | area | impact | full)
    python scripts/audit/delivery_attest.py --scope full     # the whole suite (scripts/ops/run_pg_tests.py, hermetic throwaway Postgres); always allowed, covers anything
    python scripts/audit/delivery_attest.py --verify <sha>   # re-reads the receipts and says whether the full 40-character sha has a passing, intact receipt (exit 0 yes, 1 no, 3 unverifiable)

What it does. It makes a clone of the repository checked out DETACHED at the exact commit in a temporary directory (a real git repository, because some tests call git; the working tree, and another
session's edits in it, never enter: an archive without .git failed five tests in the baseline run of 2026-11-02) and runs pytest THERE; the live database and its connection
strings never reach the run (the suite uses a throwaway container on a random free port). The receipt line in out/audit/delivery_receipts.jsonl holds the full sha, the tree sha, every commit it covers
(origin/master..HEAD), the files and counts, the exit code and a sha-256 digest. Scope: `docs_only` when no code changed, `full` when runtime code changed (shared/, proyecto*/), `area` (the tests named
for each changed module) otherwise; an area run with a changed code file no test covers is `no_tests_cover` and the deliverer runs `--scope full`.
FND-0385 (impact selection, shared/test_impact.py): a runtime change is still `full` until the receipts themselves hold the evidence that a dependency analysis of the change selects every test that could
fail. Until then (SHADOW) the full suite runs as always and the receipt records what the selector WOULD have run and any failing test it would have missed (an escape); after that (ENFORCE, derived from the
receipts, demoted by one escape) a change the selector can bound, with a fresh full receipt on the ancestry of HEAD, runs only that selection plus the whole-tree policy tests (scope `impact`). Every
case the analysis cannot establish is `full`; `--verify` recomputes the decision of an impact receipt. Switches: DELIVERY_IMPACT_SELECTION_ENABLED / _MODE in shared/config.py.
One run at a time (a lock); a request that arrives while one runs is remembered and the running process runs once more for the newest HEAD. A `full` run waits while the monthly cycle holds its lock.
A run that never reaches pytest's summary (container down, interpreter died) is `inconclusive`, retried once, and never recorded as a failure of a test.
Exit codes: 0 pass, 1 fail / no_tests_cover / insufficient_scope, 2 usage, 3 inconclusive (or unverifiable), 4 coalesced into the running process.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from shared import delivery_receipt as dr  # noqa: E402
from shared import test_impact as ti  # noqa: E402
from shared.backlog_autopilot import is_code_path  # noqa: E402
from shared.git_export import checkout_tree, export_tree  # noqa: E402
from shared.out_dir import out_root  # noqa: E402

RC_PASS, RC_FAIL, RC_USAGE, RC_INCONCLUSIVE, RC_COALESCED = 0, 1, 2, 3, 4
RECEIPTS = out_root(ROOT) / "audit" / "delivery_receipts.jsonl"      # FND-0379: the autopilot runs from a clean clone and must read the MAIN tree's receipts
LOCK = out_root(ROOT) / "audit" / "delivery_attest.lock"
WANTED = out_root(ROOT) / "audit" / "delivery_attest.wanted"
DURATIONS = out_root(ROOT) / "audit" / "test_durations.json"        # FND-0385: measured seconds per test file, published by the evidence harness; the selector's time rule (F5) reads it
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
LOCK_STALE_S = 4 * 3600
TIMEOUT_S = {"area": 20 * 60, "impact": 45 * 60, "full": 90 * 60}
_RESULT_RC = {"pass": RC_PASS, "fail": RC_FAIL, "no_tests_cover": RC_FAIL, "insufficient_scope": RC_FAIL, "inconclusive": RC_INCONCLUSIVE}


# ── git ─────────────────────────────────────────────────────────────────────────

def _git(*args: str, cwd: Path) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {r.stderr.strip()[:200]}")
    return r.stdout


def _is_ancestor(a: str, b: str, cwd: Path) -> bool:
    return subprocess.run(["git", "merge-base", "--is-ancestor", a, b], cwd=cwd, capture_output=True).returncode == 0


def collect_change(root: Path, head: str = "HEAD") -> dict:
    """{'sha', 'tree', 'base', 'covers', 'changed'} for `head`: the full commit sha, its tree, the commits not yet on origin/master (oldest first; just `head` when it is already there or
    origin/master is not its ancestor) and the files they change. Reads git only."""
    sha = _git("rev-parse", "--verify", f"{head}^{{commit}}", cwd=root).strip()
    tree = _git("rev-parse", f"{sha}^{{tree}}", cwd=root).strip()
    base, covers = None, [sha]
    try:
        remote = _git("rev-parse", "--verify", "origin/master^{commit}", cwd=root).strip()
        if remote != sha and _is_ancestor(remote, sha, root):
            base = remote
            covers = [c for c in _git("rev-list", "--reverse", f"{remote}..{sha}", cwd=root).split() if c]
    except RuntimeError:
        pass
    if base is None:
        try:
            base = _git("rev-parse", "--verify", f"{sha}^", cwd=root).strip()
        except RuntimeError:
            base = EMPTY_TREE                                                # a root commit
    changed = [p for p in _git("diff", "--name-only", base, sha, cwd=root).splitlines() if p]
    return {"sha": sha, "tree": tree, "base": base, "covers": covers, "changed": changed}


def tree_of(root: Path) -> Callable:
    """A `tree_of(sha)` for delivery_receipt.evaluate: the tree git gives for a commit, None when it does not know it."""
    def _tree(sha: str) -> Optional[str]:
        try:
            return _git("rev-parse", f"{sha}^{{tree}}", cwd=root).strip()
        except RuntimeError:
            return None
    return _tree


# ── running the tests ───────────────────────────────────────────────────────────

def available_tests(export_dir: Path) -> list:
    """Repo-relative paths (forward slashes) of the test files of an exported tree that the FULL run collects (shared/test_impact.py::SUITE_ROOTS): a test the full suite never runs (upload/**,
    proyecto1/core/test_*.py) must not be able to vouch for a change in an area receipt (FND-0385)."""
    return sorted(r for r in (p.relative_to(export_dir).as_posix() for p in export_dir.rglob("test_*.py")) if ti.suite_of(r))


# ── impact selection (FND-0385) ─────────────────────────────────────────────────

def read_sources(directory: Path) -> dict:
    """{repo-relative path: text} of the files the dependency analysis reads (.py and the data / launcher files) of a checked-out tree. Never follows `.git`."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(directory):
        rel_dir = Path(dirpath).relative_to(directory).as_posix()
        dirnames[:] = [d for d in dirnames if d != ".git" and not ti.is_excluded(f"{'' if rel_dir == '.' else rel_dir + '/'}{d}/")]      # prune: never walk .git, out/ or upload/
        for name in filenames:
            rel = name if rel_dir == "." else f"{rel_dir}/{name}"
            if not (rel.endswith(".py") or rel.endswith(ti.DATA_EXTS)):
                continue
            try:
                out[rel] = (Path(dirpath) / name).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
    return out


def _code_commits_between(root: Path, a: str, b: str) -> Optional[int]:
    """How many commits in a..b change at least one code file; None when git cannot say."""
    try:
        text = _git("log", "--name-only", "--format=%x00%H", f"{a}..{b}", cwd=root)
    except RuntimeError:
        return None
    return sum(1 for block in text.split("\x00")[1:] if any(is_code_path(f) for f in block.splitlines()[1:] if f.strip()))


def _hours_between(root: Path, a: str, b: str) -> Optional[float]:
    try:
        ta, tb = (int(_git("show", "-s", "--format=%ct", c, cwd=root).strip()) for c in (a, b))
    except (RuntimeError, ValueError):
        return None
    return abs(tb - ta) / 3600.0


def _time_inputs(receipts: list) -> tuple:
    """(seconds per test file | None, median seconds of the last passing full receipts | None): the data of the selector's time rule. Missing or unreadable = None (the rule then does not apply, which
    can only keep a decision wider than it could be, never narrower)."""
    try:
        per_file = json.loads(DURATIONS.read_text(encoding="utf-8")).get("per_file_seconds") or None
    except (OSError, ValueError):
        per_file = None
    secs = [float(r["seconds"]) for r in receipts if r.get("scope") == "full" and r.get("result") == "pass" and float(r.get("seconds") or 0) > 0][-10:]
    return per_file, (sorted(secs)[len(secs) // 2] if secs else None)


def impact_decision(root: Path, chg: dict, tree: Path, receipts: list, *, use_durations: bool = True) -> tuple:
    """(Decision, F7 reason | None) for the change `chg` whose commit is checked out in `tree`. The base tree is exported only when a changed file was deleted or renamed away (the base graph is the only
    one that still knows who imported it). Reads git and files; decides nothing about the mode."""
    head_sources = read_sources(tree)
    base_sources = None
    if any(not (tree / c).exists() for c in chg["changed"]) and chg["base"] != EMPTY_TREE:
        btmp = Path(tempfile.mkdtemp(prefix="delivery_base_"))
        try:
            export_tree(chg["base"], btmp, root)
            base_sources = read_sources(btmp)
        finally:
            shutil.rmtree(btmp, ignore_errors=True)
    durations, median_full = _time_inputs(receipts) if use_durations else (None, None)
    decision = ti.decide(chg["changed"], head_sources, base_sources, policy_tests=dr.POLICY_TESTS, durations=durations, median_full_seconds=median_full)
    reason, _anchor = ti.freshness_reason(chg["sha"], receipts, is_ancestor=lambda a, b: _is_ancestor(a, b, root),
                                          code_commits_between=lambda a, b: _code_commits_between(root, a, b), hours_between=lambda a, b: _hours_between(root, a, b))
    return decision, reason


def confirm_escapes(outside: list, runner: Callable, tree: Path) -> tuple:
    """(escapes, cascade) from the failing test files a full run reported OUTSIDE the selection. A failure that does not reproduce when its file runs alone is a cascade (an earlier failing test left shared
    state broken, which a selective run of the same tests would not suffer: the mutation runs of 2026-11-07 showed ten such victims) and is no escape; one that fails alone is a real escape. If the isolated
    run cannot be made or says nothing (no pytest summary), every file stays an escape: a doubt demotes the mode, it never keeps it. Pure given `runner`."""
    if not outside:
        return [], []
    try:
        rc, text, _s, _c = runner("impact", sorted(outside), tree)
    except Exception:                                                        # noqa: BLE001
        return list(outside), []
    _counts, summaries = dr.parse_counts(text)
    if not summaries:
        return list(outside), []
    ids = [i.split("::", 1)[0] for i in dr.failed_tests(text, limit=None)]
    alone = [f for f in outside if f in ids or (f.startswith("proyecto2/") and f[len("proyecto2/"):] in ids)]
    return alone, [f for f in outside if f not in alone]


def selector_digest(path: Optional[Path] = None) -> str:
    """sha-256 of the selector's source (shared/test_impact.py, line endings normalised). The evidence and the shadow streak are bound to it, so any edit of the selector, with or without a version bump,
    leaves the old evidence behind and sends the mode back to SHADOW by itself. Pure on the file."""
    import hashlib
    return hashlib.sha256((path or ROOT / "shared" / "test_impact.py").read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def scoped_receipts(receipts: list, digest: str) -> list:
    """The receipts that count as evidence for the selector whose source digest is `digest`: shadow receipts recorded by that very code, and the LATEST evidence line written for it (a later `fail`
    line revokes an earlier `pass`). Receipts that carry no selection block are kept as they are; anything recorded by other selector code is dropped. Pure."""
    out, latest = [], None
    for r in receipts:
        if r.get("kind") == "impact_evidence":
            if r.get("selector_digest") == digest and r.get("algo") == ti.ALGO_VERSION:
                latest = r
            continue
        if r.get("selection") is not None and r["selection"].get("selector") != digest:
            continue
        out.append(r)
    return out + ([latest] if latest else [])


def current_impact_mode(receipts_path: Path) -> str:
    """OFF | SHADOW | ENFORCE for the next receipt: the switches of shared/config.py and the receipts file (shared/test_impact.py::effective_mode), counting only evidence and shadow receipts bound to
    the exact selector code that is running (selector_digest). Never raises: any doubt is SHADOW, and OFF when the configuration cannot be read."""
    try:
        from shared import config as cfg
        return ti.effective_mode(scoped_receipts(read_receipts(receipts_path), selector_digest()), ti.ALGO_VERSION, ceiling=cfg.DELIVERY_IMPACT_SELECTION_MODE, enabled=cfg.DELIVERY_IMPACT_SELECTION_ENABLED)
    except Exception:                                                        # noqa: BLE001 -- a selector that cannot be configured is off, never a reason to run less
        return "OFF"


def recompute_impact(root: Path, rec: dict) -> "ti.Decision":
    """The decision the selector gives today for the change a receipt vouched for (its own sha and base), used by `verify` so a receipt cannot claim a weaker scope than the analysis requires."""
    if len(rec.get("changed") or []) >= 200:
        raise RuntimeError("the receipt lists 200 changed files or more: its change list is truncated")
    tmp = Path(tempfile.mkdtemp(prefix="delivery_verify_"))
    try:
        checkout_tree(rec["sha"], tmp, root)
        chg = {"sha": rec["sha"], "base": rec["base"], "changed": [p for p in _git("diff", "--name-only", rec["base"], rec["sha"], cwd=root).splitlines() if p]}
        return impact_decision(root, chg, tmp, [], use_durations=False)[0]          # time can only widen a decision: a recomputation without it is the strictest check of "what must have run"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _clean_env() -> dict:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    for k in [k for k in env if k.startswith("FONDOS_") and "DSN" in k]:     # hermetic: no live connection string reaches a test
        env.pop(k)
    return env


def _wait_for_cycle(wait_min: int) -> None:
    """A `full` run waits while the monthly cycle holds its lock (the two would fight over CPU and disk). Raises when it is still held after `wait_min`."""
    sys.path.insert(0, str(ROOT / "scripts" / "audit"))
    from preflight_check import cycle_lock_held
    deadline = time.time() + wait_min * 60
    while cycle_lock_held():
        if time.time() > deadline:
            raise RuntimeError(f"the cycle lock is still held after {wait_min} minutes")
        time.sleep(30)


def run_tests(scope: str, selected: list, export_dir: Path, python: Optional[str] = None, wait_min: int = 60) -> tuple:
    """(exit code, combined output, seconds, commands) of the tests of `scope` run inside `export_dir`. `full` = scripts/ops/run_pg_tests.py with no arguments; `area` = plain pytest for the selected
    files and, when some are Postgres tests (`_pg` in the name), one run_pg_tests.py for those so they really run. Never reads the working tree."""
    py = python or sys.executable
    env = _clean_env()
    plan_file: Optional[Path] = None
    if scope == "full":
        _wait_for_cycle(wait_min)
        cmds = [[py, "scripts/ops/run_pg_tests.py", "--image", "postgres:17"]]
    elif scope == "impact":                                                  # the same container, DDL and four runs as `full`, each restricted to the selected entries of its suite
        plan: dict = {}
        for entry in selected:
            plan.setdefault(ti.suite_of(entry.split("::", 1)[0]) or "tests", []).append(entry)
        fd, name = tempfile.mkstemp(prefix="impact_suite_", suffix=".json")
        os.close(fd)
        plan_file = Path(name)
        plan_file.write_text(json.dumps(plan, sort_keys=True), encoding="utf-8")
        cmds = [[py, "scripts/ops/run_pg_tests.py", "--image", "postgres:17", "--suite-files", str(plan_file)]]
    else:
        pg = [f for f in selected if "_pg" in f.rsplit("/", 1)[-1]]
        plain = [f for f in selected if f not in pg]
        cmds = []
        if plain:
            cmds.append([py, "-m", "pytest", "-q", "--tb=short", "-p", "no:cacheprovider", *plain])
        if pg:
            cmds.append([py, "scripts/ops/run_pg_tests.py", "--image", "postgres:17", *pg])
    t0, rc, out = time.time(), 0, []
    try:
        for cmd in cmds:
            r = subprocess.run(cmd, cwd=export_dir, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, timeout=TIMEOUT_S.get(scope, TIMEOUT_S["area"]))
            out.append(r.stdout + "\n" + r.stderr)
            rc = rc or r.returncode
    finally:
        if plan_file is not None:
            plan_file.unlink(missing_ok=True)
    return rc, "\n".join(out), time.time() - t0, cmds


# ── the receipt ─────────────────────────────────────────────────────────────────

def read_receipts(path: Path) -> list:
    """The receipt lines, oldest first; a line that is not JSON is skipped (it can only be a torn write)."""
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def attest(root: Path, head: str, scope: Optional[str], *, runner: Callable, receipts_path: Path, now: Callable = lambda: datetime.now(timezone.utc), python: Optional[str] = None,
           impact_mode: str = "OFF", impact: Optional[Callable] = None) -> dict:
    """Run the tests for `head` as `scope` (None = the scope its change needs), append the receipt and return it. `runner(scope, selected, export_dir)` -> (rc, text, seconds, commands) is injectable for the tests.
    `impact_mode` (FND-0385; main() derives it from shared/config.py and the receipts): OFF = exactly the behaviour before the selector; SHADOW = a runtime change still runs the whole suite but the receipt records
    what the selector would have run and any failing test it would have missed (an escape); ENFORCE = a runtime change the selector can bound, with a fresh full receipt behind it, runs only that selection
    (scope `impact`). `impact(chg, tree, receipts) -> (Decision, F7 reason | None)` is injectable."""
    chg = collect_change(root, head)
    required = dr.required_scope(chg["changed"])
    run_scope = scope or required
    selected: list = []
    uncovered: list = []
    rc, text, seconds, commands = 0, "", 0.0, []
    counts, summaries = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}, 0
    decision, fresh_reason, impact_error, selection = None, None, None, None
    if run_scope != "docs_only":
        tmp = Path(tempfile.mkdtemp(prefix="delivery_attest_"))
        try:
            checkout_tree(chg["sha"], tmp, root)                             # a real repository at the exact commit: some tests call git (a plain archive failed 5 of them)
            if impact_mode != "OFF" and required == "full":
                analyze = impact or (lambda c, t, r: impact_decision(root, c, t, r))
                try:                                                         # any failure of the analysis is recorded and the run stays full: uncertainty never narrows
                    decision, fresh_reason = analyze(chg, tmp, read_receipts(receipts_path))
                except Exception as exc:                                     # noqa: BLE001
                    decision, impact_error = None, f"{type(exc).__name__}: {str(exc)[:200]}"
                if (impact_mode == "ENFORCE" and decision is not None and decision.scope == "impact" and fresh_reason is None and run_scope in ("full", "impact") and scope in (None, "impact")):
                    required, run_scope, selected = "impact", "impact", decision.files()
            if run_scope == "area":
                avail = available_tests(tmp)
                selected, uncovered = dr.select_tests(chg["changed"], avail)
                if not selected and not uncovered:
                    uncovered = ["(no test selected for this change)"]
                selected = dr.with_policy(selected, avail)                  # FND-0380: the whole-tree policy tests run in EVERY area receipt
            if not uncovered and not (run_scope == "impact" and required != "impact"):
                for _attempt in range(2):                                    # a run that never reaches pytest's summary is infrastructure: retried once
                    try:
                        rc, text, seconds, commands = runner(run_scope, selected, tmp)
                    except Exception as exc:                                 # noqa: BLE001 -- a runner that cannot start is inconclusive, not a failed test
                        rc, text, seconds, commands = 99, f"{type(exc).__name__}: {exc}", 0.0, []
                    counts, summaries = dr.parse_counts(text)
                    if summaries:
                        break
            if decision is not None or impact_error:                         # what the selector decided, and whether a failing test escaped it (only a full run can reveal that)
                selection = {"algo": ti.ALGO_VERSION, "selector": selector_digest(), "mode": impact_mode, "ran": run_scope, "freshness": fresh_reason, "error": impact_error, "escapes": []}
                if decision is not None:
                    selection["decision"] = decision.as_dict()
                    if run_scope == "full" and decision.scope == "impact" and text:
                        outside = ti.find_escapes(dr.failed_tests(text, limit=None), decision.files(), available_tests(tmp))
                        selection["escapes"], selection["cascade"] = confirm_escapes(outside, runner, tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    result, detail = dr.judge(run_scope, required, counts, summaries, rc, uncovered)
    if selection and selection["escapes"]:
        detail += " | ESCAPE: the impact selector would have missed " + ", ".join(selection["escapes"][:5])
    if result in ("fail", "inconclusive") and text.strip():
        ids = dr.failed_tests(text)
        detail += (" | failed: " + ", ".join(ids) if ids else "") + " | " + " / ".join(text.strip().splitlines()[-3:])[:300]
        try:                                                                 # the whole output of the run that did not pass, for whoever has to fix it (overwritten by the next failure)
            receipts_path.parent.mkdir(parents=True, exist_ok=True)
            (receipts_path.parent / "delivery_attest_last_failure.log").write_text(text[-60000:], encoding="utf-8")
        except OSError:
            pass
    rec = dr.build_receipt(sha=chg["sha"], tree=chg["tree"], base=chg["base"], covers=chg["covers"], changed=chg["changed"], required=required, run_scope=run_scope,
                           files=selected, counts=counts, rc=rc, seconds=seconds, python=python or sys.executable, command=[" ".join(c) for c in commands],
                           result=result, detail=detail, ts=now().isoformat(timespec="seconds"), selection=selection)
    receipts_path.parent.mkdir(parents=True, exist_ok=True)
    with open(receipts_path, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())                                                 # the receipt is the evidence of a delivery: it must be on disk before anything reads it
    return rec


def verify(root: Path, sha: str, receipts_path: Path, recompute: Optional[Callable] = None) -> tuple:
    """(True | False | None, text): the verdict of the `tests_pass` condition for the full `sha`, from the receipts file. A pass vouched for by an `impact` receipt is accepted only if the selector, run
    again on that receipt's own commit and base, still says `impact` and every test it selects is among those the receipt ran (FND-0385): a receipt cannot claim a weaker scope than the analysis requires.
    `recompute(root, receipt) -> Decision` is injectable; a recomputation that cannot run is unverifiable (None), never a pass."""
    receipts = read_receipts(receipts_path)
    ok, text = dr.evaluate(sha, receipts, tree_of(root))
    if ok is not True:
        return ok, text
    rec = next((r for r in reversed(receipts) if sha in (r.get("covers") or []) and r.get("result") == "pass" and r.get("digest") == dr.digest_of(r)), None)
    if not rec or rec.get("scope") != "impact":
        return ok, text
    try:
        again = (recompute or recompute_impact)(root, rec)
    except Exception as exc:                                                 # noqa: BLE001
        return None, f"not verified: the impact decision for {sha[:12]} could not be recomputed ({type(exc).__name__}: {str(exc)[:160]})"
    if again.scope != "impact":
        return False, f"the impact receipt for {sha[:12]} is refused: the selector now requires a full receipt ({'; '.join(again.reasons)[:200]})"
    ran = set(rec.get("files") or [])
    missing = sorted(set(again.files()) - ran)
    if missing:
        return False, f"the impact receipt for {sha[:12]} did not run {len(missing)} test(s) the selector requires, e.g. {missing[:3]}"
    return ok, text


# ── one run at a time ───────────────────────────────────────────────────────────

def acquire(lock: Path, stale_s: int = LOCK_STALE_S) -> bool:
    """Take the lock file (created exclusively); a lock older than `stale_s` belongs to a dead process and is taken over."""
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        if lock.exists() and time.time() - lock.stat().st_mtime > stale_s:
            lock.unlink()
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except OSError:
        return False
    os.write(fd, f"{os.getpid()}\n".encode())
    os.close(fd)
    return True


def release(lock: Path) -> None:
    try:
        lock.unlink()
    except OSError:
        pass


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--head", default="HEAD", help="the commit to vouch for (default HEAD)")
    ap.add_argument("--scope", choices=("auto", "docs_only", "area", "impact", "full"), default="auto",
                    help="auto = the scope the change needs (a runtime change is full, or impact once the selector has earned it); impact is refused unless the selector allows it")
    ap.add_argument("--verify", metavar="SHA", help="verify a full commit sha against the receipts and exit")
    ap.add_argument("--receipts", default=str(RECEIPTS), help=argparse.SUPPRESS)
    ap.add_argument("--wait-min", type=int, default=60, help="a full run waits this long for the monthly cycle to release its lock")
    args = ap.parse_args(argv)
    receipts = Path(args.receipts)
    if args.verify:
        ok, text = verify(ROOT, args.verify, receipts)
        print(("PASS: " if ok else "NOT PASS: " if ok is False else "UNVERIFIABLE: ") + text)
        return RC_PASS if ok else RC_FAIL if ok is False else RC_INCONCLUSIVE
    try:
        head = _git("rev-parse", "--verify", f"{args.head}^{{commit}}", cwd=ROOT).strip()
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return RC_USAGE
    if not acquire(LOCK):
        WANTED.write_text(head + "\n", encoding="utf-8")
        print(f"another run is in progress: {head[:12]} is remembered and that run will run once more for the newest HEAD")
        return RC_COALESCED
    code = RC_PASS
    try:
        for _round in range(5):
            rec = attest(ROOT, head, None if args.scope == "auto" else args.scope,
                         runner=lambda s, sel, d: run_tests(s, sel, d, wait_min=args.wait_min), receipts_path=receipts, impact_mode=current_impact_mode(receipts))
            sel = rec.get("selection") or {}
            note = ""
            if sel.get("decision"):
                dec = sel["decision"]
                note = f" | impact selector {sel.get('mode')}: would run {dec['selected_count'] + len(dec.get('policy') or [])} of {dec['suite_total']} test files" if dec["scope"] == "impact" \
                    else f" | impact selector {sel.get('mode')}: full ({dec['reasons'][0][:90]})"
            print(f"receipt {rec['result'].upper()}: {rec['sha'][:12]} scope {rec['scope']} (needs {rec['required']}), {rec['counts']['passed']} passed, {rec['counts']['failed']} failed, "
                  f"{rec['counts']['errors']} errors, {rec['seconds']}s; {rec['detail'][:200]}; digest {rec['digest'][:12]}{note}")
            code = _RESULT_RC.get(rec["result"], RC_FAIL)
            wanted = WANTED.read_text(encoding="utf-8").strip() if WANTED.exists() else ""
            if not wanted or wanted == head:
                WANTED.unlink(missing_ok=True)
                break
            WANTED.unlink(missing_ok=True)
            head = wanted
    finally:
        release(LOCK)
    if code == RC_PASS:                                                      # a delivery that waited for this receipt is published now (scripts/audit/auto_publish.py decides; it never forces)
        try:
            sys.path.insert(0, str(ROOT / "scripts" / "audit"))
            import auto_publish
            auto_publish.spawn_detached(ROOT)
        except Exception:                                                    # noqa: BLE001 -- the receipt stands; the watcher and the next commit publish too
            pass
    return code


if __name__ == "__main__":
    sys.exit(main())
