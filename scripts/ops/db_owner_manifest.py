# scripts/ops/db_owner_manifest.py
# -*- coding: utf-8 -*-
"""MANIFEST.sha256 of the db_owner tool tree (FND-0354, Step 3): what the isolated account `fondos_db_owner` is allowed to execute.

Format (the PowerShell provisioning script writes it, this module reads and checks it):
    # commit <40 hex>
    <sha256 lowercase hex><two spaces><path relative to the root, forward slashes>
One line per file. Covered: every file under the root except the top-level entries in SKIP_TOP (the vault changes on rotation, receipts are written at every run, the manifest cannot contain itself).
Verification is exact: a changed file, a missing file and an EXTRA file (anything not listed, bytecode included) are all problems.
The ACL of the tree is what stops a change; this check makes a change impossible to miss and refuses to run the tool on it.

    python db_owner_manifest.py --verify <root>             exit 0 = identical, 1 = problems listed
    python db_owner_manifest.py --build <root> <commit>     writes <root>\\MANIFEST.sha256 (the provisioning script builds it in PowerShell; this exists for tests and rehearsals)
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

MANIFEST_NAME = "MANIFEST.sha256"
SKIP_TOP = frozenset({"vault", "receipts", MANIFEST_NAME})
_LINE = re.compile(r"^([0-9a-f]{64})  (\S.*)$")
_COMMIT = re.compile(r"^# commit ([0-9a-f]{40})$")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def covered_files(root: Path) -> dict:
    """{relative posix path: absolute Path} of every covered file under `root`."""
    out = {}
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if rel.split("/", 1)[0] in SKIP_TOP:
            continue
        out[rel] = p
    return out


def build(root: Path, commit: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("commit must be a full 40-character lowercase SHA")
    lines = [f"# commit {commit}"] + [f"{sha256_file(p)}  {rel}" for rel, p in sorted(covered_files(root).items())]
    return "\n".join(lines) + "\n"


def parse(text: str) -> tuple:
    """(commit, {path: sha256}). Any line that is not a comment, the commit line or a hash line is an error."""
    commit, entries = None, {}
    for n, line in enumerate(text.splitlines(), 1):
        m = _COMMIT.match(line)
        if m:
            commit = m.group(1)
            continue
        m = _LINE.match(line)
        if not m:
            raise ValueError(f"{MANIFEST_NAME} line {n} is malformed")
        if m.group(2) in entries:
            raise ValueError(f"{MANIFEST_NAME} lists {m.group(2)} twice")
        entries[m.group(2)] = m.group(1)
    if commit is None:
        raise ValueError(f"{MANIFEST_NAME} has no commit line")
    return commit, entries


def verify(root: Path) -> tuple:
    """(commit, problems): problems is empty when the tree is exactly what the manifest lists."""
    mf = root / MANIFEST_NAME
    if not mf.is_file():
        return None, [f"{MANIFEST_NAME} is missing"]
    try:
        commit, listed = parse(mf.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        return None, [str(exc)]
    actual = covered_files(root)
    problems = [f"CHANGED {rel}" for rel in sorted(listed) if rel in actual and sha256_file(actual[rel]) != listed[rel]]
    problems += [f"MISSING {rel}" for rel in sorted(listed) if rel not in actual]
    problems += [f"EXTRA   {rel}" for rel in sorted(actual) if rel not in listed]
    return commit, problems


def main(argv: list | None = None) -> int:
    a = sys.argv[1:] if argv is None else argv
    if len(a) == 2 and a[0] == "--verify":
        commit, problems = verify(Path(a[1]))
        for p in problems[:50]:
            print(p)
        print(f"manifest {'OK' if not problems else 'FAILED'} (commit {commit}); {len(problems)} problem(s)")
        return 0 if not problems else 1
    if len(a) == 3 and a[0] == "--build":
        root = Path(a[1])
        (root / MANIFEST_NAME).write_text(build(root, a[2]), encoding="utf-8", newline="\n")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
