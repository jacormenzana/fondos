# proyecto3/src/pit_p37_snapshot.py
# -*- coding: utf-8 -*-
"""Package L, P3-7 (doc/decisiones/PREREG_P3_7.md section 8): the frozen snapshot of the DB-derived inputs of the trials.

Why. The runner of P3-5A read the IPC series, the entry / exit fees and the geography from the LIVE database at the start of every trial; another session's cycle changed two of them while the chain
ran (an IPC row, a bulk rewrite of `fund_master`). Both were shown immaterial after the fact, which is a proof about THAT chain and not a guarantee. The trials of P3-7 read these three inputs ONLY
from a snapshot written once, before the first run, and verified by SHA-256 on every load.

    freeze_inputs(folder, ipc, fees, geography)  -> manifest   (never overwrites a folder)
    load_snapshot(folder)                        -> (ipc, fees, geography), after verifying every file against the manifest
    read_live_inputs(conn)                       -> (ipc, fees, geography), a read-only read of the live database (the only function here that touches it)

The snapshot id is a function of the CONTENT hashes only, so two freezes of the same data share an id; the UTC time of the freeze is in the manifest, never in the id. PURE apart from the files it
writes in the folder it is given and `read_live_inputs`.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

IPC_FILE, FEES_FILE, GEO_FILE = "ipc.parquet", "fees.parquet", "geography.parquet"
MANIFEST = "manifest.json"
FEE_COLUMNS = ["Entry_Fee_Pct", "Exit_Fee_Pct"]


class SnapshotMismatch(RuntimeError):
    """A snapshot file is missing, changed after the freeze, or absent from its manifest."""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def freeze_frames(folder, frames: dict) -> dict:
    """Write named frames (`{file name: DataFrame}`, parquet, index kept) and a manifest (per file: SHA-256 and row count; the snapshot id; the UTC time). Refuses a folder that exists: a snapshot is written once."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=False)                                     # FileExistsError: never overwrite a frozen input
    files = {}
    for name, frame in frames.items():
        frame.to_parquet(folder / name)
        files[name] = {"sha256": _sha256(folder / name), "rows": int(len(frame))}
    snapshot_id = hashlib.sha256("\n".join(f"{n}:{files[n]['sha256']}" for n in sorted(files)).encode("utf-8")).hexdigest()
    manifest = {"snapshot_id": snapshot_id, "frozen_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "files": files}
    (folder / MANIFEST).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def load_frames(folder) -> dict:
    """Every frame of a frozen folder, after checking each file against the manifest (missing manifest, a file absent from disk or changed after the freeze: SnapshotMismatch naming it)."""
    folder = Path(folder)
    mf_path = folder / MANIFEST
    if not mf_path.exists():
        raise SnapshotMismatch(f"no {MANIFEST} in {folder}: not a frozen snapshot")
    manifest = json.loads(mf_path.read_text(encoding="utf-8"))
    out = {}
    for name, entry in manifest.get("files", {}).items():
        if not (folder / name).exists():
            raise SnapshotMismatch(f"{name} is missing from {folder}")
        if _sha256(folder / name) != entry["sha256"]:
            raise SnapshotMismatch(f"{name} changed after the freeze (SHA-256 differs from the manifest of {folder})")
        out[name] = pd.read_parquet(folder / name)
    return out


def freeze_inputs(folder, ipc: pd.DataFrame, fees: pd.DataFrame, geography: pd.Series) -> dict:
    """Write the three inputs and a manifest (per file: SHA-256 and row count; the snapshot id; the UTC time). Refuses a folder that exists: a snapshot is written once."""
    return freeze_frames(folder, {IPC_FILE: ipc.reset_index(drop=True), FEES_FILE: fees[FEE_COLUMNS], GEO_FILE: geography.rename("Geography").to_frame()})


def load_snapshot(folder) -> "tuple[pd.DataFrame, pd.DataFrame, pd.Series]":
    """(ipc, fees, geography) from a frozen folder, after checking every file against the manifest. Any difference raises SnapshotMismatch naming the file."""
    folder = Path(folder)
    mf_path = folder / MANIFEST
    if not mf_path.exists():
        raise SnapshotMismatch(f"no {MANIFEST} in {folder}: not a frozen snapshot")
    manifest = json.loads(mf_path.read_text(encoding="utf-8"))
    for name in (IPC_FILE, FEES_FILE, GEO_FILE):
        entry = manifest.get("files", {}).get(name)
        if entry is None:
            raise SnapshotMismatch(f"{name} is not in the manifest of {folder}")
        if not (folder / name).exists():
            raise SnapshotMismatch(f"{name} is missing from {folder}")
        if _sha256(folder / name) != entry["sha256"]:
            raise SnapshotMismatch(f"{name} changed after the freeze (SHA-256 differs from the manifest of {folder})")
    ipc = pd.read_parquet(folder / IPC_FILE)
    fees = pd.read_parquet(folder / FEES_FILE)
    geo = pd.read_parquet(folder / GEO_FILE)["Geography"]
    return ipc, fees, geo


def read_live_inputs(conn) -> "tuple[pd.DataFrame, pd.DataFrame, pd.Series]":
    """The three inputs as the live database holds them now, read-only (the same loaders the P3-5A runner used): the IPC index, the stated entry / exit fees of every fund, the geography."""
    from proyecto3.src.pit_inputs import load_ipc
    from proyecto3.src.pit_p35a_prep import load_d6_attributes
    ipc = load_ipc(conn)
    rows = conn.execute("SELECT ISIN, Entry_Fee_Pct, Exit_Fee_Pct FROM fund_master").fetchall()      # a literal: the EXPLAIN sweep must resolve it
    df = pd.DataFrame([tuple(r) for r in rows], columns=["isin", *FEE_COLUMNS]).set_index("isin")
    fees = df.apply(pd.to_numeric, errors="coerce")
    geo = load_d6_attributes(conn)["Geography"]
    return ipc, fees, geo
