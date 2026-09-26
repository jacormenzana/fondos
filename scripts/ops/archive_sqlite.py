#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
archive_sqlite.py — archive the retired SQLite database (FND-0103).

    python scripts/ops/archive_sqlite.py                   # DRY RUN: checks + plan, changes nothing
    python scripts/ops/archive_sqlite.py --apply           # move the file into the archive (never deletes)
    python scripts/ops/archive_sqlite.py --apply --include-backups   # also the db/fondos.sqlite.bak_* copies

Owner directive 2026-09-26: keep the sealed file for one week after the retirement decision, then
archive it. Refuses before --due (default 2026-10-03) unless --force.

What it does, in order:
  1. refuses if the file is missing or a SQLite process still has it open (a non-empty -wal),
  2. computes the file's sha256 and compares it with the checksum the reconcile gate recorded at the
     cutover (control.migration_state.source_sqlite_sha256) — a mismatch is REPORTED, not fatal: the
     file was found to differ from the gate record on 2026-09-26 (modified between the 23:06 gate and
     the 23:54 seal on cutover night); the manifest keeps both values,
  3. MOVES (never deletes) the file to <dest>/fondos_sealed_<date>.sqlite, writes a manifest JSON
     next to it (path, size, sha256, gate sha256, timestamps) and removes only empty -wal/-shm files.

The default destination is on the same disk as the live data, so this is NOT a backup — copy the
archive (and the Postgres dumps, FND-0070) off-box. Exit codes: 0 ok / dry run, 1 refused, 2 error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

DEFAULT_DEST = Path(r"C:\data\fondos\archive\sqlite")
DEFAULT_DUE = "2026-10-03"


def sha256_of(path: Path, chunk: int = 16 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def gate_record() -> dict:
    """The checksum/size the reconcile gate recorded at the cutover, or {} if Postgres is unreachable."""
    try:
        from shared.db import get_connection
        conn = get_connection(backend="postgres")
        try:
            row = conn.execute(
                "SELECT source_sqlite_sha256, source_sqlite_size_bytes, migrated_at "
                "FROM control.migration_state ORDER BY id DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        return {"sha256": row[0], "size": row[1], "migrated_at": str(row[2])} if row else {}
    except Exception as exc:  # noqa: BLE001 — informational only
        print(f"  (gate record unavailable: {type(exc).__name__}: {exc})")
        return {}


def main() -> int:
    ap = argparse.ArgumentParser(description="Archive the retired SQLite database (FND-0103)")
    ap.add_argument("--db", default=None, help="SQLite file (default: shared.config.DB_PATH)")
    ap.add_argument("--dest", default=str(DEFAULT_DEST))
    ap.add_argument("--due", default=DEFAULT_DUE, help="earliest archive date, YYYY-MM-DD")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--force", action="store_true", help="ignore the --due date")
    ap.add_argument("--include-backups", action="store_true", help="also move db/fondos.sqlite.bak_*")
    args = ap.parse_args()

    from shared.config import DB_PATH
    db = Path(args.db) if args.db else Path(DB_PATH)
    dest = Path(args.dest)
    due = date.fromisoformat(args.due)
    today = date.today()

    print(f"source: {db}\ndest:   {dest}\ndue:    {due}  (today {today})")
    if not db.exists():
        print("ABORT: SQLite file not found (already archived?)"); return 1
    if today < due and not args.force:
        print(f"REFUSED: not before {due} (one-week retention, FND-0103). Use --force to override."); return 1
    wal = Path(str(db) + "-wal")
    if wal.exists() and wal.stat().st_size > 0:
        print(f"ABORT: {wal.name} is not empty — a process still has the database open"); return 1

    size = db.stat().st_size
    print(f"size:   {size:,} bytes — hashing (a few minutes)...")
    digest = sha256_of(db)
    gate = gate_record()
    same = bool(gate) and gate.get("sha256") == digest
    print(f"sha256: {digest}")
    print(f"gate:   {gate.get('sha256', 'n/a')}  -> {'IDENTICAL' if same else 'DIFFERENT (recorded in the manifest, not fatal)'}")

    moves = [(db, dest / f"fondos_sealed_{today:%Y%m%d}.sqlite")]
    if args.include_backups:
        moves += [(p, dest / p.name) for p in sorted(db.parent.glob(db.name + ".bak_*")) if p.is_file()]
    for src, dst in moves:
        print(f"  {'MOVE' if args.apply else 'would move'}: {src} -> {dst}")
    if not args.apply:
        print("DRY RUN: nothing changed. Re-run with --apply."); return 0

    dest.mkdir(parents=True, exist_ok=True)
    for src, dst in moves:
        if dst.exists():
            print(f"ABORT: {dst} already exists"); return 2
    manifest = {
        "archived_at": datetime.now().isoformat(timespec="seconds"),
        "source_path": str(db), "size_bytes": size, "sha256": digest,
        "gate_record": gate, "matches_gate_record": same,
        "moved": [{"from": str(s), "to": str(d)} for s, d in moves],
        "note": "Retired 2026-09-26 (FND-0102). Not a backup: same disk unless copied off-box (FND-0070).",
    }
    for src, dst in moves:
        shutil.move(str(src), str(dst))
    for extra in (Path(str(db) + "-wal"), Path(str(db) + "-shm")):
        if extra.exists() and extra.stat().st_size in (0, 32768):   # empty WAL / index-only shm
            extra.unlink()
    (dest / f"fondos_sealed_{today:%Y%m%d}.manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"ARCHIVED. Manifest: {dest / f'fondos_sealed_{today:%Y%m%d}.manifest.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
