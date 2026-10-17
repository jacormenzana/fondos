#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
macro_freshness_check.py -- is every macro series as fresh as its own publication cadence allows? (FND-0256)

    python scripts/audit/macro_freshness_check.py            # table of every series, the stale first; exit 1 if any is stale or an input is degraded
    python scripts/audit/macro_freshness_check.py --stale    # only the series that need a look
    python scripts/audit/macro_freshness_check.py --json     # machine-readable

Read-only. The limits live in shared/macro_freshness.py (one explicit limit per series: a series is dated by its stamp, and annual / quarterly
data are stamped at the START of the period). A STALE series usually means the PROVIDER changed or discontinued it (ECB ICP -> HICP, OECD MEI,
FRED mirrors): look there before blaming the loader. Exit codes: 0 all fresh, 1 something stale or degraded, 2 the database could not be read.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from shared import macro_freshness as mf  # noqa: E402


def read_latest(conn) -> dict:
    """{(indicator, geography): newest stamped date} for every series of series_macro."""
    rows = conn.execute("SELECT indicator, geography, MAX(date) FROM series_macro GROUP BY indicator, geography").fetchall()
    return {(r[0], r[1]): r[2] for r in rows}


def run(latest: dict, today: date) -> tuple:
    statuses = mf.assess(latest, today)
    return statuses, mf.composite_legs(statuses)


def render(statuses: list, composites: dict, only_stale: bool) -> str:
    rows = [s for s in statuses if s.stale] if only_stale else statuses
    out = [f"{'indicator':16} {'geo':7} {'newest':8} {'age_d':>6} {'limit':>6}  state"]
    for s in rows:
        out.append(f"{s.indicator[:16]:16} {str(s.geography)[:7]:7} {s.newest:%Y-%m}  {s.age_days:6d} {s.limit_days:6d}  "
                   f"{'STALE' if s.stale else 'ok'}{'  (default limit)' if s.limit_source == 'default' else ''}")
    for name, c in composites.items():
        out.append(f"composite input {name}: {c['state']}  ({', '.join(f'{g}={v}' for g, v in c['legs'].items())})")
    n_stale = sum(1 for s in statuses if s.stale)
    out.append(f"{len(statuses)} series, {n_stale} stale")
    return "\n".join(out)


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--stale", action="store_true", help="show only the stale series")
    ap.add_argument("--json", action="store_true", help="print JSON instead of the table")
    args = ap.parse_args(argv)
    try:
        import shared.config  # noqa: F401  (autoloads the repo .env)
        from shared.db import get_connection
        conn = get_connection()
        try:
            latest = read_latest(conn)
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] cannot read series_macro: {type(exc).__name__}: {exc}")
        return 2
    statuses, composites = run(latest, date.today())
    if args.json:
        print(json.dumps({"series": [{"indicator": s.indicator, "geography": s.geography, "newest": s.newest.isoformat(), "age_days": s.age_days,
                                      "limit_days": s.limit_days, "stale": s.stale, "limit_source": s.limit_source} for s in statuses],
                          "composites": composites}, indent=1))
    else:
        print(render(statuses, composites, args.stale))
    return 1 if any(s.stale for s in statuses) or any(c["state"] != "OK" for c in composites.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
