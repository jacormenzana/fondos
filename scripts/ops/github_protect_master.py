#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
github_protect_master.py -- protection of the `master` branch of the repository (FND-0336, owner action O-5 of doc/especificacion/PROPUESTA_BACKLOG_V2.md).

    python scripts/ops/github_protect_master.py                 # DRY RUN: prints the settings it would apply and the current PUBLIC state; no token needed
    python scripts/ops/github_protect_master.py --apply         # applies them (needs GITHUB_ADMIN_TOKEN), then reads the result back
    python scripts/ops/github_protect_master.py --verify        # read-only check of the effective rules (public API; full detail too when GITHUB_ADMIN_TOKEN is set)
    python scripts/ops/github_protect_master.py --remove --apply  # EMERGENCY ONLY (owner): removes the protection; log V-GATE-EMERGENCY in FND-0336 and restore it within 48 h
    python scripts/ops/github_protect_master.py --with-backlog-gate --apply   # the owner, once the signing key is committed: ALSO require `backlog-gate` (FND-0334 slice 4)

The settings (owner decision of 2026-10-26): a pull request is required before merging (0 required approvals: the owner is the only reviewer); the four CI checks below
are required; branches need not be up to date (parallel pull requests are not serialised); ADMINISTRATORS ARE INCLUDED (every session publishes with the owner's own account,
an administrator, so a rule that exempted administrators would be advisory); no force pushes; no deletions.

GITHUB_ADMIN_TOKEN must be a fine-grained personal access token for THIS repository only with "Administration: Read and write" and a short expiry; revoke it after use.
It is read from the environment, never printed, never written anywhere. The checks are required by NAME: tests/test_github_protect_master.py fails if a workflow job is renamed,
because a required check that no job reports leaves every pull request waiting forever.

Exit codes: 0 ok, 1 the effective state does not match / the call was refused, 2 usage or no token.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

REPO = "jacormenzana/fondos"
BRANCH = "master"
API = "https://api.github.com"
REQUIRED_CHECKS = (
    "PG fixture smoke test",
    "Full test suite (environment-linux.yml, real Postgres 17)",
    "Verify all AUTO sentinels are in sync",
    "Verify statistical-audit catalog consistency",
)
GATE_CHECK = "backlog-gate"          # the signed-attestation job of .github/workflows/backlog-gate.yml: required only once the owner commits the signing key (FND-0334 slice 4, NORMAS_BACKLOG 4.7)
RC_OK, RC_FAIL, RC_USAGE = 0, 1, 2


def checks(with_gate: bool = False) -> tuple:
    """The required status checks: the four CI jobs, plus `backlog-gate` when the owner asks for it. Pure."""
    return REQUIRED_CHECKS + ((GATE_CHECK,) if with_gate else ())


def protection_payload(with_gate: bool = False) -> dict:
    """The body of PUT /repos/{repo}/branches/{branch}/protection. Pure."""
    return {
        "required_status_checks": {"strict": False, "contexts": list(checks(with_gate))},
        "enforce_admins": True,
        "required_pull_request_reviews": {"required_approving_review_count": 0, "dismiss_stale_reviews": False,
                                          "require_code_owner_reviews": False},
        "restrictions": None,
        "required_linear_history": False,
        "allow_force_pushes": False,
        "allow_deletions": False,
        "block_creations": False,
        "required_conversation_resolution": False,
    }


def _contexts_ok(contexts: list, with_gate: bool) -> bool:
    """Exactly the four CI jobs, or those plus `backlog-gate` once it is enabled; with `with_gate` (the owner asked for it) the fifth is REQUIRED. Pure."""
    return sorted(contexts) == sorted(checks(True)) if with_gate else sorted(contexts) in (sorted(checks(False)), sorted(checks(True)))


def evaluate_public(branch: dict, with_gate: bool = False) -> list:
    """[(check, passed, detail)] from the PUBLIC branch object (GET /branches/{branch}). Pure."""
    prot = branch.get("protection") or {}
    rsc = prot.get("required_status_checks") or {}
    contexts = sorted(rsc.get("contexts") or [c.get("context") for c in rsc.get("checks") or []])
    return [
        ("branch is protected", branch.get("protected") is True and prot.get("enabled") is True, f"protected={branch.get('protected')}"),
        ("the required checks are exactly the CI jobs (plus backlog-gate once enabled)", _contexts_ok(contexts, with_gate), f"contexts={contexts}"),
        ("the checks are enforced for everyone, administrators included", rsc.get("enforcement_level") == "everyone",
         f"enforcement_level={rsc.get('enforcement_level')}"),
    ]


def evaluate_admin(p: dict, with_gate: bool = False) -> list:
    """[(check, passed, detail)] from the full protection object (GET /branches/{branch}/protection, administrator token). Pure."""
    rsc = p.get("required_status_checks") or {}
    pr = p.get("required_pull_request_reviews")
    return [
        ("a pull request is required before merging", pr is not None, f"required_pull_request_reviews={'set' if pr is not None else 'absent'}"),
        ("administrators are included", (p.get("enforce_admins") or {}).get("enabled") is True, f"enforce_admins={p.get('enforce_admins')}"),
        ("force pushes are refused", (p.get("allow_force_pushes") or {}).get("enabled") is False, f"allow_force_pushes={p.get('allow_force_pushes')}"),
        ("deletions are refused", (p.get("allow_deletions") or {}).get("enabled") is False, f"allow_deletions={p.get('allow_deletions')}"),
        ("the checks are required", _contexts_ok(list(rsc.get("contexts") or []), with_gate), f"contexts={rsc.get('contexts')}"),
    ]


def _call(method: str, path: str, token: str | None = None, body: dict | None = None) -> tuple:
    req = urllib.request.Request(f"{API}{path}", method=method, data=None if body is None else json.dumps(body).encode("utf-8"))
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "fondos-github-protect-master")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode("utf-8")
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, {"message": raw[:200]}


def _report(results: list) -> bool:
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'} - {name}: {detail}")
    return all(ok for _, ok, _ in results)


def main(argv: "list | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="execute (default: dry run)")
    ap.add_argument("--verify", action="store_true", help="read-only check of the effective rules")
    ap.add_argument("--remove", action="store_true", help="EMERGENCY: remove the protection (needs --apply)")
    ap.add_argument("--with-backlog-gate", action="store_true", help="also require the `backlog-gate` check (the owner, once the signing key is committed)")
    args = ap.parse_args(argv)
    token = os.environ.get("GITHUB_ADMIN_TOKEN") or None
    path = f"/repos/{REPO}/branches/{BRANCH}"

    if args.apply and not token:
        print("error: GITHUB_ADMIN_TOKEN is not set (fine-grained token, this repository only, Administration: write)", file=sys.stderr)
        return RC_USAGE
    if args.remove and not args.apply:
        print("DRY RUN of --remove: nothing changed. Emergency use only; add --apply, then log V-GATE-EMERGENCY in FND-0336.")
        return RC_OK

    if args.remove:
        status, body = _call("DELETE", f"{path}/protection", token)
        print(f"remove protection: HTTP {status}" + ("" if status in (200, 204) else f" {body.get('message')}"))
        return RC_OK if status in (200, 204) else RC_FAIL

    if args.apply:
        status, body = _call("PUT", f"{path}/protection", token, protection_payload(args.with_backlog_gate))
        if status != 200:
            print(f"REFUSED: HTTP {status} {body.get('message')}", file=sys.stderr)
            return RC_FAIL
        print("applied: HTTP 200")
    elif not args.verify:
        print(f"DRY RUN: nothing changed. --apply would PUT this to {path}/protection:\n{json.dumps(protection_payload(args.with_backlog_gate), indent=2)}")

    status, branch = _call("GET", path)
    if status != 200:
        print(f"cannot read the branch: HTTP {status} {branch.get('message')}", file=sys.stderr)
        return RC_FAIL
    ok = _report(evaluate_public(branch, args.with_backlog_gate))
    if token:
        status, full = _call("GET", f"{path}/protection", token)
        if status == 200:
            ok = _report(evaluate_admin(full, args.with_backlog_gate)) and ok
        else:
            print(f"FAIL - full protection read: HTTP {status} {full.get('message')}")
            ok = False
    else:
        print("note: no GITHUB_ADMIN_TOKEN set, so the pull-request rule and the administrator flag are not readable; they are verified by the push and pull-request tests.")
    if args.apply or args.verify:
        print("RESULT:", "PROTECTION ACTIVE AS SPECIFIED" if ok else "DOES NOT MATCH")
        return RC_OK if ok else RC_FAIL
    return RC_OK


if __name__ == "__main__":
    sys.exit(main())
