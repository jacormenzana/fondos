#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
delivery_attest.py -- runs the tests LOCALLY on the exact commit being delivered and leaves one receipt (FND-0375; plan ACTION_PLAN_BACKLOG_REFACTORING, Revision 2, R2.3). No CI: nothing runs on
push and nothing refuses a push; the deliverer runs this after committing (rule R2), and the `tests_pass` closing condition reads the receipt.

    python scripts/audit/delivery_attest.py                  # HEAD, the scope its change needs (docs_only | area | full)
    python scripts/audit/delivery_attest.py --scope full     # the whole suite (scripts/ops/run_pg_tests.py, hermetic throwaway Postgres); always allowed, covers anything
    python scripts/audit/delivery_attest.py --verify <sha>   # re-reads the receipts and says whether the full 40-character sha has a passing, intact receipt (exit 0 yes, 1 no, 3 unverifiable)

What it does. It makes a clone of the repository checked out DETACHED at the exact commit in a temporary directory (a real git repository, because some tests call git; the working tree, and another
session's edits in it, never enter: an archive without .git failed five tests in the baseline run of 2026-11-02) and runs pytest THERE; the live database and its connection
strings never reach the run (the suite uses a throwaway container on a random free port). The receipt line in out/audit/delivery_receipts.jsonl holds the full sha, the tree sha, every commit it covers
(origin/master..HEAD), the files and counts, the exit code and a sha-256 digest. Scope: `docs_only` when no code changed, `full` when runtime code changed (shared/, proyecto*/), `area` (the tests named
for each changed module) otherwise; an area run with a changed code file no test covers is `no_tests_cover` and the deliverer runs `--scope full`.
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
from shared.git_export import checkout_tree  # noqa: E402
from shared.out_dir import out_root  # noqa: E402

RC_PASS, RC_FAIL, RC_USAGE, RC_INCONCLUSIVE, RC_COALESCED = 0, 1, 2, 3, 4
RECEIPTS = out_root(ROOT) / "audit" / "delivery_receipts.jsonl"      # FND-0379: the autopilot runs from a clean clone and must read the MAIN tree's receipts
LOCK = out_root(ROOT) / "audit" / "delivery_attest.lock"
WANTED = out_root(ROOT) / "audit" / "delivery_attest.wanted"
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
LOCK_STALE_S = 4 * 3600
TIMEOUT_S = {"area": 20 * 60, "full": 90 * 60}
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
    """Repo-relative paths (forward slashes) of the test files of an exported tree."""
    return sorted(p.relative_to(export_dir).as_posix() for p in export_dir.rglob("test_*.py"))


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
    if scope == "full":
        _wait_for_cycle(wait_min)
        cmds = [[py, "scripts/ops/run_pg_tests.py", "--image", "postgres:17"]]
    else:
        pg = [f for f in selected if "_pg" in f.rsplit("/", 1)[-1]]
        plain = [f for f in selected if f not in pg]
        cmds = []
        if plain:
            cmds.append([py, "-m", "pytest", "-q", "--tb=short", "-p", "no:cacheprovider", *plain])
        if pg:
            cmds.append([py, "scripts/ops/run_pg_tests.py", "--image", "postgres:17", *pg])
    t0, rc, out = time.time(), 0, []
    for cmd in cmds:
        r = subprocess.run(cmd, cwd=export_dir, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, timeout=TIMEOUT_S["full" if scope == "full" else "area"])
        out.append(r.stdout + "\n" + r.stderr)
        rc = rc or r.returncode
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


def attest(root: Path, head: str, scope: Optional[str], *, runner: Callable, receipts_path: Path, now: Callable = lambda: datetime.now(timezone.utc), python: Optional[str] = None) -> dict:
    """Run the tests for `head` as `scope` (None = the scope its change needs), append the receipt and return it. `runner(scope, selected, export_dir)` -> (rc, text, seconds, commands) is injectable for the tests."""
    chg = collect_change(root, head)
    required = dr.required_scope(chg["changed"])
    run_scope = scope or required
    selected: list = []
    uncovered: list = []
    rc, text, seconds, commands = 0, "", 0.0, []
    counts, summaries = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}, 0
    if run_scope != "docs_only":
        tmp = Path(tempfile.mkdtemp(prefix="delivery_attest_"))
        try:
            checkout_tree(chg["sha"], tmp, root)                             # a real repository at the exact commit: some tests call git (a plain archive failed 5 of them)
            if run_scope == "area":
                selected, uncovered = dr.select_tests(chg["changed"], available_tests(tmp))
                if not selected and not uncovered:
                    uncovered = ["(no test selected for this change)"]
            if not uncovered:
                for _attempt in range(2):                                    # a run that never reaches pytest's summary is infrastructure: retried once
                    try:
                        rc, text, seconds, commands = runner(run_scope, selected, tmp)
                    except Exception as exc:                                 # noqa: BLE001 -- a runner that cannot start is inconclusive, not a failed test
                        rc, text, seconds, commands = 99, f"{type(exc).__name__}: {exc}", 0.0, []
                    counts, summaries = dr.parse_counts(text)
                    if summaries:
                        break
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    result, detail = dr.judge(run_scope, required, counts, summaries, rc, uncovered)
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
                           result=result, detail=detail, ts=now().isoformat(timespec="seconds"))
    receipts_path.parent.mkdir(parents=True, exist_ok=True)
    with open(receipts_path, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")
    return rec


def verify(root: Path, sha: str, receipts_path: Path) -> tuple:
    """(True | False | None, text): the verdict of the `tests_pass` condition for the full `sha`, from the receipts file."""
    return dr.evaluate(sha, read_receipts(receipts_path), tree_of(root))


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
    ap.add_argument("--scope", choices=("auto", "docs_only", "area", "full"), default="auto", help="auto = the scope the change needs")
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
                         runner=lambda s, sel, d: run_tests(s, sel, d, wait_min=args.wait_min), receipts_path=receipts)
            print(f"receipt {rec['result'].upper()}: {rec['sha'][:12]} scope {rec['scope']} (needs {rec['required']}), {rec['counts']['passed']} passed, {rec['counts']['failed']} failed, "
                  f"{rec['counts']['errors']} errors, {rec['seconds']}s; {rec['detail'][:200]}; digest {rec['digest'][:12]}")
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
