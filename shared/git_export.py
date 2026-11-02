# shared/git_export.py
# -*- coding: utf-8 -*-
"""git_export.py -- `git archive` of one commit extracted into a directory: the single place that exports a commit (P#11). Used by scripts/ops/owner_deploy.py (the migration runs from a clean
copy of master) and scripts/audit/delivery_attest.py (the tests run on the exact commit being delivered). The working tree, which may be on any branch or hold another session's edits, never enters."""
from __future__ import annotations

import io
import os
import subprocess
import tarfile
from pathlib import Path

_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def export_tree(ref: str, dest: Path, root: Path, timeout: int = 180) -> None:
    """`git archive` of `ref` (a commit or tree id of the repository at `root`) extracted into `dest`. Raises RuntimeError when git refuses; the archive is the repository's own, so it is extracted as is."""
    r = subprocess.run(["git", "archive", "--format=tar", ref], cwd=root, capture_output=True, timeout=timeout, creationflags=_NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError(f"git archive failed: {r.stderr.decode('utf-8', 'replace')[:200]}")
    with tarfile.open(fileobj=io.BytesIO(r.stdout)) as tar:
        tar.extractall(dest, filter="fully_trusted")
