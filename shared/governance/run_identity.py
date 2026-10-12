# shared/governance/run_identity.py
# -*- coding: utf-8 -*-
"""
Which code and which configuration produced a run (FND-0261 header; extended by the FND-0263 manifest; T-CODE / T-VERSION in
doc/reglas/GOBIERNO_CONTROLES.md). One definition for every recorder, so a promotion note and an audit header never disagree (P#11).

    git_state()    -> (short sha or None, clean or None). "Clean" = no modified TRACKED file; untracked files do not count (decision D1).
                      Information for the record: failures return (None, None), never raise.
    config_hash()  -> "v1:<12 hex>" over every *_ENABLED switch and every MAX_* mandate limit of shared.config, by name. A flipped switch or
                      a changed limit changes the hash; the "v1:" prefix changes if the definition of the set does.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
CONFIG_HASH_VERSION = "v1"


def git_state(root: Path = _ROOT) -> tuple:
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root, capture_output=True, text=True,
                             timeout=10).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=root, capture_output=True, text=True,
                               timeout=10)
        if not sha or dirty.returncode != 0:
            return (sha or None, None)
        return (sha, not dirty.stdout.strip())
    except Exception:                                            # noqa: BLE001 -- information only
        return (None, None)


def describe_git_state(state: tuple) -> str:
    """`git <sha> clean|dirty-tracked` (the promotion-note wording); `git n/a` when unknown."""
    sha, clean = state
    if not sha:
        return "git n/a"
    return f"git {sha} {'clean' if clean else 'dirty-tracked' if clean is False else 'unknown'}"


def config_items(module=None) -> list:
    """[(name, value)] sorted by name: every *_ENABLED switch and every MAX_* limit of shared.config."""
    if module is None:
        import shared.config as module
    return sorted((name, getattr(module, name)) for name in dir(module)
                  if name.isupper() and (name.endswith("_ENABLED") or name.startswith("MAX_"))
                  and isinstance(getattr(module, name), (bool, int, float, str)))


def config_hash(module=None) -> str:
    digest = hashlib.sha256()
    for name, value in config_items(module):
        digest.update(f"{name}={value!r}\n".encode("utf-8"))
    return f"{CONFIG_HASH_VERSION}:{digest.hexdigest()[:12]}"
