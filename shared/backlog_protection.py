# -*- coding: utf-8 -*-
"""
backlog_protection.py -- is `backlog-gate` a REQUIRED status check of master? (Backlog v2 Phase C slice 4, FND-0334; PROPUESTA_BACKLOG_V2.md 6.4.6; NORMAS_BACKLOG.md 4.7).

The local git configuration cannot see the protection of the remote branch, so the only source is the GitHub API. With a fine-grained token that has READ access to the repository
administration -- HELD OUTSIDE THE REPOSITORY, the environment variable BACKLOG_GATE_GITHUB_TOKEN, typed in the owner's terminal, never committed, never needed by a session -- the administration
endpoint is read; without one the PUBLIC branch object is read, which a public repository serves with no secret. The answer is one of three kinds, and an unreadable one never passes silently:
    PROTECTED    backlog-gate is among the required status checks of the branch;
    UNPROTECTED  the branch is not protected, or protected without required status checks, or without backlog-gate among them (violation V-GATE-UNPROTECTED, an [ALERTA]);
    UNVERIFIED   no token, the API unreachable, a refusal, or any answer that cannot tell (violation V-GATE-UNVERIFIED, cleared by an owner-written verification event for 30 days).
`classify` is pure; `read_protection` does the one HTTPS call (stdlib urllib, 10 s timeout) and never raises: any error is UNVERIFIED.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

CHECK = "backlog-gate"
PROTECTED, UNPROTECTED, UNVERIFIED = "PROTECTED", "UNPROTECTED", "UNVERIFIED"
API = "https://api.github.com"
_NOT_PROTECTED_TEXT = ("branch not protected", "required status checks not enabled")


def classify(status: int, body) -> tuple:
    """(kind, detail) of the GitHub answer for `GET /repos/{repo}/branches/{branch}/protection/required_status_checks`. `body`: the parsed JSON (or None). Pure.
    200 lists the required contexts; 404 with the messages GitHub uses for "not protected" / "no required status checks" is UNPROTECTED, while a bare "Not Found" (what a token without
    enough permission also gets) cannot tell, so it is UNVERIFIED; anything else is UNVERIFIED."""
    if status == 200 and isinstance(body, dict):
        contexts = list(body.get("contexts") or []) + [c.get("context") for c in (body.get("checks") or []) if isinstance(c, dict)]
        if CHECK in contexts:
            return PROTECTED, f"{CHECK} is a required status check"
        return UNPROTECTED, f"master requires status checks but {CHECK} is not among them ({len(set(contexts))} required)"
    if status == 404 and isinstance(body, dict):
        msg = str(body.get("message", "")).strip().lower()
        if any(t in msg for t in _NOT_PROTECTED_TEXT):
            return UNPROTECTED, f"GitHub says: {body.get('message')}"
        return UNVERIFIED, "GitHub answered Not Found: the token may lack read access to the repository administration"
    if status in (401, 403):
        return UNVERIFIED, f"GitHub refused the token (HTTP {status}): it needs read access to the repository administration"
    return UNVERIFIED, f"GitHub answered HTTP {status} with a body that cannot tell"


def classify_public(status: int, body) -> tuple:
    """(kind, detail) of the PUBLIC branch object (`GET /repos/{repo}/branches/{branch}`, which needs no token for a public repository): `protected` and the required status checks with their
    `enforcement_level`. PROTECTED only when backlog-gate is among the required checks and the enforcement is not `off`; any other readable answer is UNPROTECTED; an unreadable one is UNVERIFIED. Pure."""
    if status == 200 and isinstance(body, dict) and "protected" in body:
        if body.get("protected") is not True:
            return UNPROTECTED, "the branch is not protected (public branch object)"
        rsc = (body.get("protection") or {}).get("required_status_checks") or {}
        contexts = list(rsc.get("contexts") or []) + [c.get("context") for c in (rsc.get("checks") or []) if isinstance(c, dict)]
        if CHECK in contexts and rsc.get("enforcement_level") != "off":
            return PROTECTED, f"{CHECK} is a required status check (public branch object)"
        return UNPROTECTED, f"{CHECK} is not among the required status checks (public branch object)"
    return UNVERIFIED, f"the public branch object cannot tell (HTTP {status})"


def _get(url: str, token, opener, timeout: float):
    """(status, parsed JSON or None), or None when the call itself failed (network, timeout). The token, when there is one, only goes in the Authorization header."""
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "fondos-backlog-gate"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with (opener or urllib.request.urlopen)(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except Exception:                                    # noqa: BLE001
            return exc.code, None
    except Exception:                                         # noqa: BLE001  (network, timeout, bad JSON)
        return None


def read_protection(repo: str, branch: str, token, opener=None, timeout: float = 10.0) -> tuple:
    """(kind, detail): the protection of `repo`'s `branch`. Never raises; the token is never part of the detail.
    With a token the administration endpoint is read first (it says exactly which checks are required). Without one -- or when that answer cannot tell -- the PUBLIC branch object is read, which
    needs no token for a public repository: the answer is as authoritative, so a public repository needs no secret. Only when both fail is it UNVERIFIED (no token and an unreadable
    public object: a missing token never passes silently)."""
    detail = "no read-only GitHub token (BACKLOG_GATE_GITHUB_TOKEN is not set)"
    if token:
        got = _get(f"{API}/repos/{repo}/branches/{branch}/protection/required_status_checks", token, opener, timeout)
        if got is None:
            detail = "the GitHub API could not be read (network)"
        else:
            kind, detail = classify(*got)
            if kind != UNVERIFIED:
                return kind, detail
    got = _get(f"{API}/repos/{repo}/branches/{branch}", token, opener, timeout)
    if got is None:
        return UNVERIFIED, f"{detail}; the public branch object could not be read either (network)"
    kind, public_detail = classify_public(*got)
    if kind != UNVERIFIED:
        return kind, public_detail
    return UNVERIFIED, f"{detail}; {public_detail}"
