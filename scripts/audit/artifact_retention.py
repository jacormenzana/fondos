# scripts/audit/artifact_retention.py
# -*- coding: utf-8 -*-
"""Retention report for the artifacts that live outside the log registry (FND-0268; rule text: doc/reglas/NORMAS_IMPLEMENTACION.md section 4.10).

READ-ONLY: it lists what each rule would let the owner remove and never deletes, moves or edits anything. The numbers below are the single source of
the rule (the doc points here).

  harvest_raw_*.jsonl   raw harvest evidence; the data lives in db_document_catalogue, nothing reads these files back.
                        Keep every file younger than `keep_all_days`; beyond that keep the NEWEST file of each calendar month; always keep the newest overall.
  out/incident_*        incident evidence folders: removable `keep_days` after the folder date (YYYYMMDD in its name), unless a file in it is newer.
  out/diag              diagnostic CSVs: `_tmp_*` removable after `tmp_days`, the rest after `keep_days`, by modification time.

PDFs and Raw_KIID_Text are never deleted (separate rule). Usage:
    C:\\data\\envs\\des\\python.exe scripts\\audit\\artifact_retention.py [--root DIR] [--today YYYY-MM-DD] [--list]
Exit code is always 0 (it is a report, not a gate).
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

RETENTION: dict = {
    "harvest_raw": {"globs": ("proyecto1/harvest/harvest_raw_*.jsonl", "proyecto1/harvest_raw_*.jsonl"), "keep_all_days": 90},
    "incident": {"globs": ("out/incident_*",), "keep_days": 180},
    "diag": {"globs": ("out/diag/*.csv",), "tmp_prefix": "_tmp_", "tmp_days": 30, "keep_days": 365},
}


def _mtime(path: Path) -> date:
    return datetime.fromtimestamp(path.stat().st_mtime).date()


def _tree_newest(path: Path) -> date:
    files = [p for p in path.rglob("*") if p.is_file()]
    return max((_mtime(p) for p in files), default=_mtime(path))


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def _stamp_of(name: str) -> date | None:
    m = re.search(r"(20\d{2})(\d{2})(\d{2})", name)
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None
    except ValueError:
        return None


def _collect(root: Path, globs: tuple) -> list:
    seen, out = set(), []
    for g in globs:
        for p in sorted(root.glob(g)):
            if p not in seen:
                seen.add(p)
                out.append(p)
    return out


def harvest_raw_candidates(files: list, today: date, keep_all_days: int) -> list:
    """Files removable under the harvest rule: older than keep_all_days AND not the newest file of their calendar month; the newest overall stays."""
    stamped = sorted(((_stamp_of(p.name) or _mtime(p), p) for p in files), key=lambda t: (t[0], t[1].name))
    if not stamped:
        return []
    newest_of_month: dict = {}
    for d, p in stamped:
        newest_of_month[(d.year, d.month)] = p
    keep = set(newest_of_month.values()) | {stamped[-1][1]}
    cutoff = today - timedelta(days=keep_all_days)
    return [p for d, p in stamped if d < cutoff and p not in keep]


def incident_candidates(dirs: list, today: date, keep_days: int) -> list:
    cutoff = today - timedelta(days=keep_days)
    out = []
    for p in dirs:
        stamp = _stamp_of(p.name) or _mtime(p)
        newest = _tree_newest(p) if p.is_dir() else _mtime(p)
        if max(stamp, newest) < cutoff:
            out.append(p)
    return out


def diag_candidates(files: list, today: date, prefix: str, tmp_days: int, keep_days: int) -> list:
    out = []
    for p in files:
        limit = tmp_days if p.name.startswith(prefix) else keep_days
        if _mtime(p) < today - timedelta(days=limit):
            out.append(p)
    return out


def report(root: Path = ROOT, today: date | None = None) -> dict:
    """{rule: {'total': n, 'candidates': [Path], 'bytes': candidate bytes}}."""
    today = today or date.today()
    r = RETENTION
    harvest = _collect(root, r["harvest_raw"]["globs"])
    incident = _collect(root, r["incident"]["globs"])
    diag = _collect(root, r["diag"]["globs"])
    cands = {
        "harvest_raw": (harvest, harvest_raw_candidates(harvest, today, r["harvest_raw"]["keep_all_days"])),
        "incident": (incident, incident_candidates(incident, today, r["incident"]["keep_days"])),
        "diag": (diag, diag_candidates(diag, today, r["diag"]["tmp_prefix"], r["diag"]["tmp_days"], r["diag"]["keep_days"])),
    }
    return {k: {"total": len(all_), "candidates": c, "bytes": sum(_size(p) for p in c)} for k, (all_, c) in cands.items()}


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--today", type=date.fromisoformat, default=None)
    ap.add_argument("--list", action="store_true", help="print every candidate path, not just the counts")
    args = ap.parse_args(argv)
    res = report(args.root, args.today)
    for rule, v in res.items():
        print(f"{rule:12s} files/dirs: {v['total']:4d} | removable under the rule: {len(v['candidates']):4d} | {v['bytes'] / 1e6:8.1f} MB")
        if args.list:
            for p in v["candidates"]:
                print(f"    {p.relative_to(args.root).as_posix()}")
    print("report only: nothing was deleted or moved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
