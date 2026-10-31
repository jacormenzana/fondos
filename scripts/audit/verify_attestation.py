#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
verify_attestation.py -- the CI half of the collision gate's signed attestation (Backlog v2 Phase C slice 4, FND-0334; PROPUESTA_BACKLOG_V2.md 6.4.6; NORMAS_BACKLOG.md 4.7).
Run by .github/workflows/backlog-gate.yml; stdlib only (plus git and ssh-keygen), so the job installs nothing.

    python scripts/audit/verify_attestation.py --base <rev> [--head HEAD]

It takes the commits of `<base>..<head>` and fails (exit 1) when:
  * a commit of the range cites a ticket (`FND-nnnn` in its subject or a Refs: trailer) and the signed attestation `backlog_gate/attestation.json` of <head> is missing or its signature
    (`backlog_gate/attestation.json.sig`) does not verify;
  * such a commit is absent from the attestation, or its tree hash or gate nonce differs;
  * any collision pair in the attestation is undecided.
The signature is checked against `.github/backlog_gate_allowed_signers` AS IT IS IN <base>: a change cannot bring its own key. While that file lists no key the gate is NOT ENFORCED: the job
passes with a loud warning, so nothing is silently "green" (owner action: generate the key and commit its public part). The attestation commit itself (every path under backlog_gate/) and the
commits that cite no ticket are not required. A range with no commit that cites a ticket passes with no attestation.
Stated limit: CI proves that the owner's key signed a statement; it cannot check the database.

Exit codes: 0 pass (or not enforced), 1 fail, 2 usage or git error.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from shared import backlog_attest as att  # noqa: E402
from shared import backlog_gate as gate  # noqa: E402

_FMT = "%H%x1f%T%x1f%B%x1e"


def git(*args, check: bool = True) -> str:
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    if check and r.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {r.stderr.strip()[:200]}")
    return r.stdout if r.returncode == 0 else ""


def parse_range(text: str, paths_of) -> list:
    """The commits of `git log --format=_FMT` output (oldest first, as the caller orders them): [{'sha', 'tree', 'gate', 'tickets', 'paths'}]. `paths_of(sha)` lists a commit's changed paths. Pure
    apart from that callable."""
    out = []
    for rec in text.split("\x1e"):
        rec = rec.strip("\r\n")
        if not rec:
            continue
        sha, tree, message = (rec.split("\x1f") + ["", "", ""])[:3]
        msg = gate.parse_message(message)
        out.append({"sha": sha.strip(), "tree": tree.strip(), "gate": msg.gate, "tickets": msg.mentions, "paths": paths_of(sha.strip())})
    return out


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", required=True, help="the base of the range (the commit the push or the pull request starts from)")
    ap.add_argument("--head", default="HEAD")
    args = ap.parse_args(argv)
    try:
        base = git("rev-parse", "--verify", f"{args.base}^{{commit}}").strip()
        head = git("rev-parse", "--verify", f"{args.head}^{{commit}}").strip()
        log = git("log", "--reverse", f"--format={_FMT}", f"{base}..{head}")
        commits = parse_range(log, lambda sha: [p for p in git("diff-tree", "--root", "--no-commit-id", "--name-only", "-r", "-m", "--first-parent", sha).splitlines() if p])
    except RuntimeError as exc:
        print(f"::error::{exc}")
        return 2
    citing = [c for c in commits if c["tickets"] and not att.is_attestation_commit(c["paths"])]
    print(f"range {base[:12]}..{head[:12]}: {len(commits)} commit(s), {len(citing)} cite a ticket")
    signers = git("show", f"{base}:{att.ALLOWED_SIGNERS}", check=False)
    if not att.has_signer(signers):
        print("::warning::backlog-gate is NOT ENFORCED: .github/backlog_gate_allowed_signers lists no key in the base (owner: generate the signing key and commit its public part).")
        return 0
    if not citing:
        print("no commit of the range cites a ticket: nothing to attest")
        return 0
    doc_text = git("show", f"{head}:{att.ATTEST_FILE}", check=False)
    sig_text = git("show", f"{head}:{att.SIG_FILE}", check=False)
    if not doc_text or not sig_text:
        print(f"::error::the range has {len(citing)} commit(s) that cite a ticket and {head[:12]} carries no signed attestation ({att.ATTEST_FILE} and {att.SIG_FILE}): run `backlog_sync.py --attest`.")
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        d, s, a = Path(tmp) / "attestation.json", Path(tmp) / "attestation.json.sig", Path(tmp) / "allowed_signers"
        d.write_text(doc_text, encoding="utf-8", newline="")
        s.write_text(sig_text, encoding="utf-8", newline="")
        a.write_text(signers, encoding="utf-8", newline="")
        ok, msg = att.verify_signature(d, s, a)
    if not ok:
        print(f"::error::the signature of the attestation does not verify against the allowed signers of the base: {msg}")
        return 1
    try:
        doc = json.loads(doc_text)
    except ValueError:
        print("::error::the attestation is not valid JSON")
        return 1
    bad = att.problems(doc, commits)
    for p in bad:
        print(f"::error::{p}")
    if bad:
        return 1
    print(f"attestation OK: {len(citing)} commit(s) covered, every collision pair decided, signature verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
