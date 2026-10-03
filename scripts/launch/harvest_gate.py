#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
harvest_gate.py -- quality gate of P1_harvestFunds.bat between the harvest and the destructive phases.

Why: p1_db_harvest.py / p1_kiid_sync.py exit 0 even when the catalogue they loaded is wrong, and
`p1_kiid_sync.py --retire-orphans` treats every local KIID absent from the LATEST harvest as retired.
A truncated or empty catalogo.xml would therefore retire the whole universe. The gate compares the
latest harvest_ts in db_document_catalogue with the previous one and refuses to continue when the
catalogue shrank by more than --max-drop-pct (rows or distinct ISINs) or is under --min-rows.

Exit codes: 0 pass | 5 gate failed (do not sync/retire) | 6 cannot evaluate (DB unreadable, < 1 harvest).
With a single harvest in the table there is nothing to compare: only --min-rows is applied.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

RC_PASS, RC_FAIL, RC_UNREADABLE = 0, 5, 6


def evaluate(latest: tuple, previous: tuple | None, max_drop_pct: float, min_rows: int):
    """latest/previous = (harvest_ts, rows, distinct_isins). Returns (ok, [messages])."""
    ts, rows, isins = latest
    msgs = [f"latest harvest_ts={ts} rows={rows} isins={isins}"]
    ok = True
    if rows < min_rows:
        ok = False
        msgs.append(f"FAIL rows {rows} < min-rows {min_rows}")
    if previous is not None:
        pts, prows, pisins = previous
        msgs.append(f"previous harvest_ts={pts} rows={prows} isins={pisins}")
        for label, new, old in (("rows", rows, prows), ("isins", isins, pisins)):
            if old and new < old * (1 - max_drop_pct / 100.0):
                ok = False
                msgs.append(f"FAIL {label} dropped {old} -> {new} "
                            f"({(old - new) * 100.0 / old:.1f}% > {max_drop_pct}%)")
    else:
        msgs.append("no previous harvest to compare against (min-rows check only)")
    return ok, msgs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max-drop-pct", type=float, default=10.0)
    ap.add_argument("--min-rows", type=int, default=1000)
    args = ap.parse_args(argv)

    sys.path.insert(0, str(ROOT))
    try:
        from shared.db import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT harvest_ts, COUNT(*), COUNT(DISTINCT isin) FROM db_document_catalogue "
                "GROUP BY harvest_ts ORDER BY harvest_ts DESC LIMIT 2"
            ).fetchall()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 -- fail closed, report why
        print(f"[GATE] cannot read db_document_catalogue: {exc}")
        return RC_UNREADABLE
    if not rows:
        print("[GATE] db_document_catalogue is empty -- run --harvest first")
        return RC_UNREADABLE

    latest = tuple(rows[0])
    previous = tuple(rows[1]) if len(rows) > 1 else None
    ok, msgs = evaluate(latest, previous, args.max_drop_pct, args.min_rows)
    for m in msgs:
        print(f"[GATE] {m}")
    print(f"[GATE] {'PASS' if ok else 'FAILED'}")
    return RC_PASS if ok else RC_FAIL


if __name__ == "__main__":
    sys.exit(main())
