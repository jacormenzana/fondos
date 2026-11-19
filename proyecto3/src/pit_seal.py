# proyecto3/src/pit_seal.py
# -*- coding: utf-8 -*-
"""Baseline seal of the PIT evidence run 20261119_202509 (roadmap v4, doc/backlog/PKG_L_p3_alignment.md, FND-0430).

The run's manifest records `git_commit: f365c11 (+uncommitted changes)`, so the working tree of that moment cannot be recovered. What CAN be proved is which bytes were analysed: the SHA-256 of
every artifact the cost check and the turning-point diagnostic read. `verify_seal` refuses to go on when a file differs, and every report quotes the hashes it verified. Stdlib only (R-7)."""
from __future__ import annotations

import hashlib
from pathlib import Path

RUN_STAMP = "20261119_202509"

SEAL = {
    "manifest.json": "f67569236346997895f9fab74b24a16ce9ac46cdec2ce06683e323cc63f2db09",
    "scores_long.parquet": "7a7d5b344907c8be616cb8f4d47781ca464a5c4b825f8aab9d1298ffdfa846eb",
    "pit_backtest_table.csv": "56f0a5a03d2fd3fc75d00966c33e431837adbe90d8f719cf00cecce5b18d4b56",
    "inputs/attrs.parquet": "12680a379803a0e56064a3a1a33012b5cac6b966fda14d2c957103ef061dbacb",
    "inputs/fx_exposed.parquet": "af6e2bfa929cf5ebc822e05fa0db686842a0a41def292b71bf1041884b700921",
    "inputs/nav.parquet": "83fd237e2ac009292cad4b265223433e1b71f16e98470532734d5394a5660818",
    "inputs/rate.parquet": "b7192041f952d0232fa11f38486a24e127ed14df4ed57d6a1e77d0212f2df752",
    "inputs/regime_hist.parquet": "f540bbf80b0e5aa7e906dc4be17e665d76d3f11e4f5d39dfc11def4a18cc6c34",
    "inputs/target.parquet": "04cb111f6f9272d59ce68b919e2cfa58d0bef14d7b55d77dba69e8b8c8a4a93b",
}


class SealMismatch(Exception):
    """A sealed artifact is missing or its SHA-256 differs from the seal."""


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def verify_seal(run_dir, seal: "dict | None" = None) -> dict:
    """Verify every sealed file under `run_dir` and return {relative path: hash} of what was verified. Raises SealMismatch listing every missing or changed file."""
    seal = SEAL if seal is None else seal
    run = Path(run_dir)
    bad, ok = [], {}
    for rel, want in seal.items():
        p = run / rel
        if not p.exists():
            bad.append(f"{rel}: missing")
            continue
        got = sha256_of(p)
        if got != want:
            bad.append(f"{rel}: {got[:12]}.. != sealed {want[:12]}..")
        else:
            ok[rel] = got
    if bad:
        raise SealMismatch(f"{run}: " + "; ".join(bad))
    return ok
