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
import re
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
CONFIG_HASH_VERSION = "v1"
RUN_PIPELINE = _ROOT / "proyecto2" / "src" / "pipeline" / "run_pipeline.py"
_CALC_VERSION_RE = re.compile(r"""^CALC_VERSION\s*(?::\s*str\s*)?=\s*["'](\d+)["']""", re.M)


def calc_version(path: Path | None = None) -> str | None:
    """P2's CALC_VERSION, read from the source text: importing run_pipeline would load the whole P2 stack just to read one constant.
    None when the file or the constant cannot be found. (Moved here from scripts/launch/p1p2_state.py, which delegates: one reader.)"""
    try:
        m = _CALC_VERSION_RE.search((path or RUN_PIPELINE).read_text(encoding="utf-8"))
    except OSError:
        return None
    return m.group(1) if m else None


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


TIP_REF = "origin/master"          # decision D1: the reviewed code is what is on origin/master


def code_integrity(root: Path = _ROOT, tip: str = TIP_REF) -> dict:
    """THE code-integrity predicate (decision D1; shared with the gated push script, FND-0273): {"head", "tip", "at_tip", "clean",
    "ok", "detail"}. at_tip = HEAD equals `tip` as of the LAST FETCH (no network here); clean = no modified tracked file (untracked files
    are ignored). ok is True only when both are known and true; any git failure gives ok False with the reason, never an exception."""
    def git(*args):
        return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=10)
    try:
        head, tip_sha = git("rev-parse", "HEAD"), git("rev-parse", "--verify", "--quiet", tip)
        dirty = git("status", "--porcelain", "--untracked-files=no")
    except Exception as exc:                                     # noqa: BLE001 -- reported, never raised
        return {"head": None, "tip": None, "at_tip": None, "clean": None, "ok": False, "detail": f"git unavailable ({type(exc).__name__})"}
    h = head.stdout.strip() or None
    t = tip_sha.stdout.strip() or None
    at_tip = (h == t) if (h and t) else None
    clean = (not dirty.stdout.strip()) if dirty.returncode == 0 else None
    problems = []
    if at_tip is None:
        problems.append(f"cannot compare HEAD with {tip}")
    elif not at_tip:
        problems.append(f"HEAD {h[:7]} != {tip} {t[:7]} (push first, or check out {tip})")
    if clean is None:
        problems.append("cannot read the working tree state")
    elif not clean:
        n = len(dirty.stdout.strip().splitlines())
        problems.append(f"{n} modified tracked file(s)")
    return {"head": h, "tip": t, "at_tip": at_tip, "clean": clean, "ok": not problems,
            "detail": "; ".join(problems) or f"HEAD {h[:7]} == {tip}, clean tracked tree"}


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
