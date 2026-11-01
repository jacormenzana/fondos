# -*- coding: utf-8 -*-
"""
backlog_ci.py -- is the CI of a change green? The evidence behind the closing condition `tests_pass` (Backlog v3 W4, FND-0359; scripts/audit/rfd_matrix_probe.py).

`verdict` and `evidence_commit` are pure. `read_ci` makes the HTTPS calls (the same stdlib helper as shared/backlog_protection.py) and NEVER raises: whatever it cannot read is "unverifiable" (None), which the
autopilot treats as "not yet" and asks again on its next run, never as a pass. The repository is public, so GitHub's check runs and the protection of the branch need NO secret; the read-only token
BACKLOG_GATE_GITHUB_TOKEN, when the environment has one, only raises the rate limit (60 -> 5000 requests an hour) and never appears in a detail.

Where the evidence is: a pull request merged with "Create a merge commit" leaves the merge commit on the branch and the commit the checks ran on as its SECOND PARENT; the merge commit itself has
no check runs. So the checks of a delivery are read on the second parent (`evidence_commit`), and the checks that count are the ones the branch REQUIRES (read from the branch's own protection,
not hard-coded here) plus any other run that exists: one failing, cancelled or still running makes it "not green".
"""
from __future__ import annotations

from typing import Optional

from shared import backlog_protection as prot

FAILED = frozenset({"failure", "cancelled", "timed_out", "action_required", "stale", "startup_failure"})
PASSED = frozenset({"success", "neutral", "skipped"})


def evidence_commit(commit: str, parents: list) -> str:
    """The commit whose check runs prove a delivery: the second parent of a merge commit (the pull request's head), else the commit itself. `parents` = the parents of `commit`. Pure."""
    return parents[1] if len(parents) >= 2 else commit


def verdict(check_runs: list, required: list) -> tuple:
    """(True | False, why) for the check runs of one commit. Green only when there is at least one run, every REQUIRED check has run and passed, and no run at all failed or is still going.
    A run is passed when completed with success, neutral or skipped. Pure."""
    runs = [r for r in check_runs if isinstance(r, dict)]
    if not runs:
        return False, "no check runs on the commit (CI has not run, or the commit is not the one the checks ran on)"
    by_name: dict = {}
    for r in runs:
        by_name.setdefault(r.get("name"), []).append(r)
    bad = sorted({r.get("name") or "?" for r in runs if r.get("status") == "completed" and r.get("conclusion") in FAILED})
    if bad:
        return False, f"check(s) failed: {', '.join(bad)}"
    pending = sorted({r.get("name") or "?" for r in runs if r.get("status") != "completed"})
    if pending:
        return False, f"check(s) not finished yet: {', '.join(pending)}"
    unknown = sorted({r.get("name") or "?" for r in runs if r.get("conclusion") not in PASSED})
    if unknown:
        return False, f"check(s) without a passing conclusion: {', '.join(unknown)}"
    missing = sorted(n for n in required if n not in by_name)
    if missing:
        return False, f"required check(s) missing on the commit: {', '.join(missing)}"
    return True, f"{len(runs)} check run(s) all green" + (f", the {len(required)} required among them" if required else "")


def required_checks(status: int, body) -> list:
    """The names of the checks the branch requires, from the PUBLIC branch object (`GET /repos/{repo}/branches/{branch}`); empty when it does not say. Pure."""
    if status == 200 and isinstance(body, dict):
        rsc = (body.get("protection") or {}).get("required_status_checks") or {}
        names = list(rsc.get("contexts") or []) + [c.get("context") for c in (rsc.get("checks") or []) if isinstance(c, dict)]
        return sorted({n for n in names if n})
    return []


def read_ci(repo: str, branch: str, sha: str, token=None, opener=None, timeout: float = 10.0) -> tuple:
    """(True | False | None, detail): is the CI of `sha` green? None = could not be read (network, rate limit, an unknown answer): unverifiable, not a pass. Never raises; the token is never in a detail."""
    got = prot._get(f"{prot.API}/repos/{repo}/commits/{sha}/check-runs?per_page=100", token, opener, timeout)
    if got is None:
        return None, f"GitHub could not be reached to read the check runs of {sha[:12]}"
    status, body = got
    if status == 403:
        return None, "GitHub refused the read (HTTP 403: most likely the unauthenticated rate limit; BACKLOG_GATE_GITHUB_TOKEN raises it)"
    if status != 200 or not isinstance(body, dict):
        return None, f"GitHub answered HTTP {status} for the check runs of {sha[:12]}"
    runs = body.get("check_runs") or []
    if int(body.get("total_count") or 0) > len(runs):
        return None, f"{body.get('total_count')} check runs but only {len(runs)} were returned: the read is incomplete"
    branch_got = prot._get(f"{prot.API}/repos/{repo}/branches/{branch}", token, opener, timeout)
    required = required_checks(*branch_got) if branch_got else []
    if not required:                                                          # "nothing failed" is not evidence: without the list of what must have run, the read cannot say
        return None, f"the checks that {branch} requires could not be read, so the check runs of {sha[:12]} cannot be judged"
    ok, why = verdict(runs, required)
    return ok, f"{sha[:12]}: {why}"
