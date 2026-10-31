# -*- coding: utf-8 -*-
"""
backlog_attest.py -- the signed attestation of the collision gate (Backlog v2 Phase C slice 4, FND-0334; PROPUESTA_BACKLOG_V2.md 6.4.6; NORMAS_BACKLOG.md 4.7).

Stdlib only, so that the CI job (scripts/audit/verify_attestation.py) can import it with no installation. Two halves:

* BUILD (the owner, at push time: `backlog_sync.py --attest`): from the commits of the range and the state of the collision pairs, a JSON document -- for each commit that cites a ticket: sha,
  tree hash, gate nonce and the pairs of its tickets with decided / undecided. **Ticket ids only: never a title, a description or a log** (the remote is GitHub). It is signed with the owner's
  SSH key (`ssh-keygen -Y sign -n backlog-gate`): the private key is never in the repository or in .env.
* VERIFY (CI): the signature is checked against `.github/backlog_gate_allowed_signers` as it is in the BASE of the range (a change cannot bring its own key), then `problems()` compares the
  commits of the range with the attestation. CI proves that the owner's key signed a statement; it cannot check the database.

The attestation commit itself is excluded from the range: a commit whose every changed path lies under `backlog_gate/`.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Optional

NAMESPACE = "backlog-gate"
PRINCIPAL = "backlog-gate-owner"                 # the identity written in allowed_signers (`backlog-gate-owner namespaces="backlog-gate" ssh-ed25519 AAAA...`)
ATTEST_DIR = "backlog_gate"
ATTEST_FILE = f"{ATTEST_DIR}/attestation.json"
SIG_FILE = f"{ATTEST_FILE}.sig"
ALLOWED_SIGNERS = ".github/backlog_gate_allowed_signers"
VERSION = 1
_TICKET = re.compile(r"^FND-\d{4}$")
_SHA = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")


# ── build (pure) ────────────────────────────────────────────────────────────────────────────────────

def is_attestation_commit(paths: list) -> bool:
    """True for the commit that carries the attestation: it changes files and every one of them is under backlog_gate/. Pure."""
    return bool(paths) and all(p.startswith(ATTEST_DIR + "/") for p in paths)


def build(base: str, head: str, commits: list, pairs: list, generated: str) -> dict:
    """The attestation document. `commits`: [{'sha', 'tree', 'gate', 'tickets', 'paths'}] of the range (oldest first); `pairs`: [{'a', 'b', 'decided'}] of the collision pairs that
    touch any ticket of the range. Commits that cite no ticket, and the attestation commit, are left out. Ticket ids only. Pure."""
    entries = []
    for c in commits:
        if not c["tickets"] or is_attestation_commit(c.get("paths") or []):
            continue
        mine = sorted({t for t in c["tickets"]})
        touching = [p for p in pairs if p["a"] in mine or p["b"] in mine]
        entries.append({"sha": c["sha"], "tree": c["tree"], "nonce": c.get("gate"), "tickets": mine,
                        "pairs": [{"a": p["a"], "b": p["b"], "decided": bool(p["decided"])} for p in sorted(touching, key=lambda p: (p["a"], p["b"]))]})
    return {"version": VERSION, "base": base, "head": head, "generated": generated, "commits": entries}


def dumps(doc: dict) -> str:
    """The exact bytes that are signed: sorted keys, two-space indent, a final newline. Pure."""
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


# ── verify (pure) ───────────────────────────────────────────────────────────────────────────────────

def problems(doc: dict, commits: list) -> list:
    """What is wrong when the attestation `doc` is compared with the commits of the range (CI). [] = the attestation covers them. `commits`: [{'sha', 'tree', 'gate', 'tickets', 'paths'}].
    Fails when: the document is not a version-1 attestation; a commit citing a ticket is absent from it or its tree hash differs; its nonce differs from the commit's trailer; any pair is undecided.
    A commit with no ticket and the attestation commit are not required. Pure."""
    out = []
    if not isinstance(doc, dict) or doc.get("version") != VERSION or not isinstance(doc.get("commits"), list):
        return ["the attestation is not a version-1 document"]
    by_sha = {}
    for e in doc["commits"]:
        sha = e.get("sha", "") if isinstance(e, dict) else ""
        if not _SHA.match(sha) or not isinstance(e.get("tickets"), list) or not all(_TICKET.match(str(t)) for t in e["tickets"]):
            out.append(f"malformed entry for {str(sha)[:12] or '?'}")
            continue
        by_sha[sha] = e
    for c in commits:
        if not c["tickets"] or is_attestation_commit(c.get("paths") or []):
            continue
        e = by_sha.get(c["sha"])
        if e is None:
            out.append(f"{c['sha'][:12]} cites {', '.join(c['tickets'])} and is not in the attestation")
            continue
        if e.get("tree") != c["tree"]:
            out.append(f"{c['sha'][:12]}: tree hash {str(e.get('tree'))[:12]} in the attestation, {c['tree'][:12]} in the commit")
        if (e.get("nonce") or None) != (c.get("gate") or None):
            out.append(f"{c['sha'][:12]}: gate nonce differs between the attestation and the commit trailer")
    for e in by_sha.values():
        for p in e.get("pairs", []):
            if not p.get("decided"):
                out.append(f"{e['sha'][:12]}: collision pair {p.get('a')} / {p.get('b')} is undecided")
    return out


def has_signer(allowed_signers_text: str) -> bool:
    """True when the allowed_signers file lists at least one key (a non-comment line). Pure."""
    return any(line.strip() and not line.lstrip().startswith("#") for line in (allowed_signers_text or "").splitlines())


# ── sign / verify with ssh-keygen (I/O) ─────────────────────────────────────────────────────────────

def sign(doc_path: Path, key_path: Path, timeout: int = 120) -> Path:
    """Sign the attestation file with the owner's private key: writes `<file>.sig` and returns its path. Raises RuntimeError with ssh-keygen's message on failure (a passphrase is asked on the
    terminal). The caller must make sure the key is not inside the repository."""
    sig = Path(str(doc_path) + ".sig")
    if sig.exists():
        sig.unlink()
    r = subprocess.run(["ssh-keygen", "-Y", "sign", "-f", str(key_path), "-n", NAMESPACE, str(doc_path)], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0 or not sig.exists():
        raise RuntimeError(f"ssh-keygen -Y sign failed: {(r.stderr or r.stdout).strip()[:300]}")
    return sig


def verify_signature(doc_path: Path, sig_path: Path, allowed_signers: Path, principal: str = PRINCIPAL, timeout: int = 60) -> tuple:
    """(ok, message): `ssh-keygen -Y verify` of the attestation against an allowed_signers file."""
    with open(doc_path, "rb") as fh:
        r = subprocess.run(["ssh-keygen", "-Y", "verify", "-f", str(allowed_signers), "-I", principal, "-n", NAMESPACE, "-s", str(sig_path)],
                           stdin=fh, capture_output=True, text=True, timeout=timeout)
    return r.returncode == 0, (r.stdout + r.stderr).strip()[:300]


def inside(path: Path, root: Path) -> bool:
    """True when `path` is `root` or lies below it (the signing key must NOT be in the repository). Pure."""
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


def ssh_keygen_available() -> Optional[str]:
    try:
        r = subprocess.run(["ssh-keygen", "-?"], capture_output=True, text=True, timeout=10)
        return None if (r.stderr or r.stdout) else "ssh-keygen gave no output"
    except Exception as exc:                                 # noqa: BLE001
        return f"ssh-keygen is not available ({type(exc).__name__})"
