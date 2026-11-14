# shared/delivery_receipt.py
# -*- coding: utf-8 -*-
"""delivery_receipt.py -- the local test receipt (FND-0375; plan ACTION_PLAN_BACKLOG_REFACTORING, Revision 2, R2.3). GitHub is out of the loop and no CI is built: the deliverer runs the tests
locally (scripts/gov/delivery/delivery_attest.py) and the run leaves one receipt line. This module holds the rules, all PURE (no git, no files): which tests a change needs, how a run is read and judged, the
digest of a receipt, and the verdict of the `tests_pass` closing condition from the receipts.

The owner's three conditions: (a) the receipt names the commit unequivocally (the full 40-character sha, its tree sha and every commit it covers); (b) it counts as a pass only when tests really ran
and passed (exit code 0, at least one test passed, no failure, no error, a scope large enough for the change); (c) it is verifiable (a sha-256 digest over its content, recomputed by `evaluate`)."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Callable, Optional

from shared.backlog_autopilot import is_code_path

SCOPES = ("docs_only", "area", "impact", "full")                        # in increasing strength; `impact` (FND-0385) = the tests the dependency analysis of shared/test_impact.py selects, always with the policy set
RUNTIME_PREFIXES = ("shared/", "proyecto1/", "proyecto2/", "proyecto3/")  # a change here needs the full suite (rule R2)
RESULTS = ("pass", "fail", "inconclusive", "no_tests_cover", "insufficient_scope")
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
_SUMMARY_COUNT = re.compile(r"(\d+) (passed|failed|errors?|skipped|xfailed|xpassed|deselected)\b")
_SUMMARY_TIME = re.compile(r" in [\d.]+s\b")


def rank(scope: str) -> int:
    """Strength of a scope (docs_only 0, area 1, full 2); an unknown scope is below all. Pure."""
    return SCOPES.index(scope) if scope in SCOPES else -1


def is_test_path(path: str) -> bool:
    """True for a test file or a test-support file: under a `tests/` directory or named test_*.py / conftest.py. Pure."""
    p = str(path).replace("\\", "/")
    name = p.rsplit("/", 1)[-1]
    return bool(re.search(r"(^|/)tests?/", p)) or name.startswith("test_") or name == "conftest.py"


def required_scope(changed: list) -> str:
    """The weakest scope that may vouch for a change: `docs_only` when no changed file is code, `full` when runtime code (shared/, proyecto*/, not their tests) changed, else `area`. Pure."""
    code = [str(p).replace("\\", "/") for p in changed if is_code_path(p)]
    if not code:
        return "docs_only"
    if any(p.startswith(RUNTIME_PREFIXES) and not is_test_path(p) for p in code):
        return "full"
    return "area"


def select_tests(changed: list, available: list) -> tuple:
    """(tests to run, code files no test covers) for an `area` run. A changed test file runs itself; a changed module `x.py` (or script `x-y.sh` / `x.bat`, hyphens read as underscores) runs every available
    test named `test_x*.py`; any other changed code file (a .sql, a .yml, a script with no such test) is uncovered, and an uncovered file means the area run cannot vouch for the change: the deliverer runs `full`. Pure."""
    avail = [str(a).replace("\\", "/") for a in available]
    selected: set = set()
    uncovered: set = set()
    for raw in changed:
        p = str(raw).replace("\\", "/")
        if not is_code_path(p):
            continue
        name = p.rsplit("/", 1)[-1]
        if is_test_path(p) and p.endswith(".py") and name != "conftest.py":
            if p in avail:                                                  # a deleted test is nothing to run and nothing uncovered
                selected.add(p)
            continue
        stem = name.rsplit(".", 1)[0].replace("-", "_") if p.endswith((".py", ".sh", ".bat")) else ""         # `post-commit.sh` is covered by tests/test_post_commit*.py
        hits = [t for t in avail if stem and t.rsplit("/", 1)[-1].startswith(f"test_{stem}")]
        if hits:
            selected.update(hits)
        else:
            uncovered.add(p)
    return sorted(selected), sorted(uncovered)


# FND-0380 (2026-11-07): the tests that scan the WHOLE tree for a convention have no module of their own, so `select_tests` never picks them: scripts/gov/delivery/auto_publish.py (it reports
# incidents) passed its area run with its own 29 tests and failed the full suite on test_every_incident_reporter_has_a_fixture_case; a change confined to scripts/ or tests/ would have been
# published and its tickets closed with that defect. An `area` run therefore ALWAYS adds this fixed POLICY SET to the tests named after the changed modules. Measured 2026-11-07: about 37 s
# of plain pytest plus the SQL sweep on Postgres. A path may carry a `::node` to run ONE test of a file that also holds module tests. A stale entry (the file is gone) is a failing test
# (tests/test_delivery_receipt.py), so the set cannot rot.
POLICY_TESTS = (
    "tests/test_backlog_overlap.py::test_every_incident_reporter_has_a_fixture_case",   # every component that calls report_incident needs a calibration case (the FND-0380 incident)
    "tests/test_backlog_single_door.py",                                               # no raw INSERT / UPDATE on backlog.backlog outside the single door
    "tests/test_backlog_live_guard.py",                                                # every state-changing helper / command-line tool goes through the live-write gate
    "tests/test_backlog_writers.py",                                                   # every system:* identity in the code is registered (a new automatic writer cannot land unregistered)
    "tests/test_backlog_staging.py::test_every_script_that_calls_the_real_routines_on_a_snapshot_calls_the_guard",
    "tests/test_backlog_iam_client.py::test_no_code_outside_the_client_names_an_allow_listed_operation",   # the live-write exemption lives in ONE place
    "tests/test_backlog_gate_override_pg.py::test_no_repository_code_inserts_into_gate_switch_outside_the_ddl_the_scratch_replay_and_the_tests",   # sp_set_gate is the only door
    "tests/test_dialect_coverage.py",                                                  # no sqlite3 / dialect anti-patterns in production code
    "tests/test_log_paths.py",                                                         # every log goes to a registered directory (get_log_dir)
    "tests/test_maintenance_log_routing.py",
    "tests/test_report_dirs.py",
    "tests/test_silent_fill_scan.py",                                                  # no silent .fillna() / .ffill() imputation
    "tests/test_period_key_scan.py",                                                   # one period key (R-9)
    "tests/test_owner_vault.py",                                                       # owner-only strings never reach a session process
    "tests/test_batch_standards.py",                                                   # every launcher documents its usage and answers --help
    "tests/test_batch_helpers.py::test_every_real_launcher_has_a_printable_usage",
    "tests/test_entrypoints_smoke.py",                                                 # every entry point starts and parses its arguments
    "tests/test_sql_explain_sweep_pg.py",                                              # every SQL statement production code executes is valid on Postgres (runs in the PG container)
    "tests/test_fee_state.py::test_the_write_switch_has_exactly_one_name_and_is_on",   # Package H: ONE name for the pair-write switch across code and documents
)


def with_policy(selected: list, available: list) -> list:
    """`selected` plus the POLICY_TESTS whose file exists in the exported tree (`available`, repo-relative paths), sorted, without repeats. Pure."""
    avail = {str(a).replace("\\", "/") for a in available}
    return sorted(set(selected) | {t for t in POLICY_TESTS if t.split("::", 1)[0] in avail})


def parse_counts(text: str) -> tuple:
    """(counts, summaries) from the output of one or more pytest runs: the pytest `-q` final lines ("12 passed, 1 failed in 3.4s") are summed. `summaries` is how many such lines there were: zero means no
    pytest run reached its summary (the container did not start, the interpreter died), which is not a failed test. Pure."""
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    summaries = 0
    for line in str(text or "").splitlines():
        s = line.strip().strip("=").strip()
        if not _SUMMARY_TIME.search(s) and not s.startswith("no tests ran"):
            continue
        found = _SUMMARY_COUNT.findall(s)
        if not found and not s.startswith("no tests ran"):
            continue
        summaries += 1
        for n, what in found:
            key = {"passed": "passed", "failed": "failed", "skipped": "skipped"}.get(what, "errors" if what.startswith("error") else None)
            if key:
                counts[key] += int(n)
    return counts, summaries


_FAILED_LINE = re.compile(r"^(?:FAILED|ERROR) (\S+)")


def failed_tests(text: str, limit: Optional[int] = 5) -> list:
    """The ids of the tests pytest lists as FAILED or ERROR in its short summary (the first `limit`, in order, without repeats; `None` = all). Without them a failed receipt says only "1 failed". Pure."""
    seen: list = []
    for line in str(text or "").splitlines():
        m = _FAILED_LINE.match(line.strip())
        if m and m.group(1) not in seen:
            seen.append(m.group(1))
    return seen if limit is None else seen[:limit]


def judge(run_scope: str, required: str, counts: dict, summaries: int, rc: int, uncovered: list) -> tuple:
    """(result, detail) of a run. `insufficient_scope`: the scope run is weaker than the change needs. `no_tests_cover`: an area run with uncovered code files. `inconclusive`: no pytest summary was
    produced (infrastructure, never a failure of a test). `fail`: a non-zero exit code, a failure or error, or no test passed. `pass` only when tests really ran and passed. Pure."""
    if rank(run_scope) < rank(required):
        return "insufficient_scope", f"scope {run_scope} is weaker than the {required} this change needs"
    if run_scope == "docs_only":
        return "pass", "documents and records only: no code changed, no test needed"
    if run_scope == "area" and uncovered:
        return "no_tests_cover", "no test covers: " + ", ".join(uncovered[:8]) + (" ..." if len(uncovered) > 8 else "") + "; run --scope full"
    if summaries == 0:
        return "inconclusive", f"no pytest summary was produced (exit code {rc}): the run did not reach the tests"
    if rc != 0 or counts.get("failed", 0) or counts.get("errors", 0):
        return "fail", f"exit code {rc}, {counts.get('failed', 0)} failed, {counts.get('errors', 0)} errors"
    if counts.get("passed", 0) < 1:
        return "fail", "no test passed (nothing ran, or everything was skipped)"
    return "pass", f"{counts['passed']} passed, {counts.get('skipped', 0)} skipped"


def digest_of(receipt: dict) -> str:
    """sha-256 over the canonical JSON of the receipt without its own `digest` field. Pure."""
    body = {k: v for k, v in receipt.items() if k != "digest"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")).hexdigest()


def build_receipt(*, sha: str, tree: str, base: str, covers: list, changed: list, required: str, run_scope: str, files: list, counts: dict,
                  rc: int, seconds: float, python: str, command: list, result: str, detail: str, ts: str, selection: Optional[dict] = None) -> dict:
    """One receipt line, with its digest. `selection` (FND-0385) is the impact-selection block (algo version, mode, decision, escapes); a receipt that carries it is version 2, one without it is
    byte-for-byte the version 1 line it always was. Pure."""
    rec = {"v": 1, "ts": ts, "sha": sha, "tree": tree, "base": base, "covers": list(covers), "changed": list(changed)[:200], "required": required,
           "scope": run_scope, "files": list(files)[:200], "counts": dict(counts), "rc": rc, "seconds": round(float(seconds), 1), "python": python,
           "command": list(command), "result": result, "detail": detail}
    if selection is not None:
        rec["v"] = 2
        rec["selection"] = selection
    rec["digest"] = digest_of(rec)
    return rec


def evaluate(commit: str, receipts: list, tree_of: Callable) -> tuple:
    """The verdict of the `tests_pass` condition for the delivering `commit`: (True | False | None, text). `receipts` are the receipt lines, oldest first; `tree_of(sha)` returns the tree sha git has for
    a commit (None when unknown). True only when a receipt names this exact commit among those it covers, its digest is intact, git still gives the tree it recorded, the result is `pass` and its scope is at
    least what its own change required. A failed run is False; a run that could not start, an unreadable commit or a tampered receipt is None (unverifiable): neither is ever a pass. Pure given `tree_of`."""
    c = str(commit or "").strip()
    if not _FULL_SHA.match(c):
        return None, f"not verified: {c[:12]!r} is not a full 40-character commit sha"
    covering = [r for r in reversed(receipts) if c in (r.get("covers") or [])]
    if not covering:
        return False, f"no local test receipt covers {c[:12]} yet (run scripts/gov/delivery/delivery_attest.py)"
    last_problem: Optional[tuple] = None
    for r in covering:
        if r.get("digest") != digest_of(r):
            last_problem = last_problem or (None, f"not verified: the receipt for {c[:12]} does not match its digest")
            continue
        if tree_of(r.get("sha", "")) != r.get("tree"):
            last_problem = last_problem or (None, f"not verified: git no longer gives tree {str(r.get('tree'))[:12]} for {str(r.get('sha'))[:12]}")
            continue
        if r.get("result") == "pass" and rank(r.get("scope", "")) >= rank(r.get("required", "full")):
            n = (r.get("counts") or {}).get("passed", 0)
            return True, f"local receipt: {n} passed at {r['sha'][:12]} (tree {r['tree'][:12]}), scope {r['scope']}, digest {r['digest'][:12]}"
        verdict = None if r.get("result") == "inconclusive" else False
        last_problem = last_problem or (verdict, f"the newest receipt for {c[:12]} is {r.get('result')}: {r.get('detail')}")
    return last_problem or (False, f"no passing receipt for {c[:12]}")
