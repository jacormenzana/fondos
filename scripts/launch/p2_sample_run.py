# scripts/launch/p2_sample_run.py
# -*- coding: utf-8 -*-
"""
FND-0426 / FND-0427: rehearsal of the two P2 window switches on a 40-ISIN SAMPLE, never on the universe.

  pick       choose the sample (deterministic) and write it to out/pkg_i/sample_40.txt: IE00008TZ2E8, funds whose stored rolling_5y / rolling_10y would be purged, a fund launched this
             year, and random funds of the current universe. Read-only.
  snapshot   save the sample's stored fund_metrics rows to out/pkg_i/sample_snapshot_<stamp>.csv.gz (the "before"). Read-only.
  run        run P2 (run_pipeline.run, force) on the sample WITH ROLLING_WINDOW_COVERAGE_ENABLED and YTD_METRIC_ENABLED set to True IN THIS PROCESS ONLY: shared/config.py is not edited.
             `--dry-run` computes and writes nothing. Without it the sample's rows in fund_metrics / fund_metric_timeseries are rewritten (the live write: owner order).
  diff       compare the latest snapshot with the stored rows now: rows removed, added, changed, per horizon and metric. Read-only.

Rollback of a write run: `run --switches-off` on the same sample (the old code path, force; it also deletes the sample's ytd rows), then `diff` against the snapshot shows only the drift that has nothing to do with the switches.
Never a universe run: the sample file is required and capped at 40 ISINs.
"""
from __future__ import annotations

import argparse
import gzip
import random
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "proyecto2"))

OUT = ROOT / "out" / "pkg_i"
SAMPLE = OUT / "sample_40.txt"
INCIDENT_ISIN = "IE00008TZ2E8"
MAX_SAMPLE = 40
KEY = ["isin", "metric", "horizon", "real_flag", "metric_version"]


def _conn():
    import shared.config  # noqa: F401  (loads .env first)
    from shared.db import get_connection
    return get_connection()


def _load_sample() -> list:
    if not SAMPLE.exists():
        sys.exit(f"no sample file: run `pick` first ({SAMPLE})")
    isins = [x.strip().upper() for x in SAMPLE.read_text(encoding="utf-8").split() if x.strip()]
    if not 1 <= len(isins) <= MAX_SAMPLE:
        sys.exit(f"the sample must have 1..{MAX_SAMPLE} ISINs, it has {len(isins)}")
    return isins


def cmd_pick(_args) -> int:
    from shared import config
    conn = _conn()
    universe = [r[0] for r in conn.execute("SELECT isin FROM fund_master WHERE in_current_universe = 1 ORDER BY isin").fetchall()]
    span = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT isin, MIN(date), MAX(date) FROM fund_nav_monthly GROUP BY isin").fetchall()}
    stored = {(r[0], r[1]) for r in conn.execute(
        "SELECT isin, horizon FROM fund_metrics WHERE metric = 'return_ann' AND real_flag = 0 AND metric_version = 'v1' AND horizon IN ('rolling_5y', 'rolling_10y')").fetchall()}
    months = {"rolling_5y": 60, "rolling_10y": 120}
    purged = {"rolling_5y": [], "rolling_10y": []}
    for (isin, horizon) in sorted(stored):
        if isin not in span or isin not in universe:
            continue
        first, last = span[isin]
        n = (last.year - first.year) * 12 + last.month - first.month
        if n < months[horizon] - 1:                                   # one monthly close of tolerance
            purged[horizon].append(isin)
    this_year = [i for i in universe if i in span and span[i][0].year == span[i][1].year]
    rng = random.Random(11)
    chosen = [INCIDENT_ISIN]
    for pool, k in ((purged["rolling_10y"], 4), (purged["rolling_5y"], 4), (this_year, 2)):
        pool = [i for i in pool if i not in chosen]
        chosen += rng.sample(pool, min(k, len(pool)))
    rest = [i for i in universe if i not in chosen and i in span]
    chosen += rng.sample(rest, MAX_SAMPLE - len(chosen))
    OUT.mkdir(parents=True, exist_ok=True)
    SAMPLE.write_text("\n".join(chosen) + "\n", encoding="utf-8")
    print(f"sample written: {SAMPLE} ({len(chosen)} ISINs); would lose rolling_10y: {len(purged['rolling_10y'])} funds in the universe, rolling_5y: {len(purged['rolling_5y'])}; "
          f"switches now: coverage={config.ROLLING_WINDOW_COVERAGE_ENABLED} ytd={config.YTD_METRIC_ENABLED}")
    return 0


def _stored(conn, isins) -> list:
    return conn.execute(
        "SELECT isin, metric, horizon, real_flag, metric_version, value FROM fund_metrics WHERE isin = ANY(%s) ORDER BY isin, metric, horizon, real_flag, metric_version", (isins,)).fetchall()


def cmd_snapshot(_args) -> int:
    isins = _load_sample()
    rows = _stored(_conn(), isins)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"sample_snapshot_{datetime.now():%Y%m%d_%H%M%S}.csv.gz"
    with gzip.open(path, "wt", encoding="utf-8", newline="") as fh:
        fh.write(",".join(KEY + ["value"]) + "\n")
        for r in rows:
            fh.write(",".join("" if v is None else str(v) for v in r) + "\n")
    print(f"snapshot: {path} ({len(rows)} fund_metrics rows of {len(isins)} funds)")
    return 0


def cmd_run(args) -> int:
    isins = _load_sample()
    import shared.config as config
    on = not args.switches_off
    config.ROLLING_WINDOW_COVERAGE_ENABLED = on                      # in this process only: shared/config.py is not edited
    config.YTD_METRIC_ENABLED = on
    print(f"[p2_sample_run] {len(isins)} ISINs | coverage={on} ytd={on} | {'DRY RUN (nothing written)' if args.dry_run else 'WRITING the sample rows (live)'}")
    from proyecto2.src.pipeline import run_pipeline
    rc = int(run_pipeline.run(isins=isins, force=True, dry_run=args.dry_run) or 0)
    if args.switches_off and not args.dry_run and rc == 0:
        # With YTD_METRIC_ENABLED OFF nothing rewrites the sample's `ytd` rows, so a rollback must remove the ones the ON run added (nothing reads them).
        conn = _conn()
        n = conn.execute("DELETE FROM fund_metrics WHERE isin = ANY(%s) AND horizon = 'ytd'", (isins,)).rowcount
        conn.commit()
        print(f"[p2_sample_run] rollback: {n} ytd rows of the sample removed")
    return rc


def cmd_diff(_args) -> int:
    import pandas as pd
    isins = _load_sample()
    snaps = sorted(OUT.glob("sample_snapshot_*.csv.gz"))
    if not snaps:
        sys.exit("no snapshot: run `snapshot` before the run")
    before = pd.read_csv(snaps[-1], dtype={"metric_version": str})
    after = pd.DataFrame(_stored(_conn(), isins), columns=KEY + ["value"])
    for df in (before, after):
        df["real_flag"] = df["real_flag"].astype(int)
        df["metric_version"] = df["metric_version"].astype(str)
    m = before.merge(after, on=KEY, how="outer", suffixes=("_before", "_after"), indicator=True)
    removed, added = m[m["_merge"] == "left_only"], m[m["_merge"] == "right_only"]
    both = m[m["_merge"] == "both"]
    changed = both[(both["value_before"].astype(float) - both["value_after"].astype(float)).abs() > 1e-12]
    print(f"snapshot {snaps[-1].name}: before {len(before)} rows, after {len(after)} rows | removed {len(removed)}, added {len(added)}, changed {len(changed)}, unchanged {len(both) - len(changed)}")
    for label, df in (("REMOVED", removed), ("ADDED", added), ("CHANGED", changed)):
        if df.empty:
            continue
        print(f"\n{label} by horizon / metric (rows):")
        print(df.groupby(["horizon", "metric"]).size().sort_values(ascending=False).head(15).to_string())
        print(f"{label} funds: {df['isin'].nunique()}")
    inc = removed[removed["isin"] == INCIDENT_ISIN]
    if not inc.empty:
        print(f"\n{INCIDENT_ISIN} loses: {sorted(set(inc['horizon']))}")
    ytd = after[(after["horizon"] == "ytd") & (after["metric"] == "return_cum")]
    print(f"\nytd rows now: {len(ytd)} ({ytd['isin'].nunique()} funds); {INCIDENT_ISIN}: {ytd[ytd['isin'] == INCIDENT_ISIN]['value'].tolist()}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("pick", help="choose the 40-ISIN sample (read-only)").set_defaults(fn=cmd_pick)
    sub.add_parser("snapshot", help="save the sample's stored fund_metrics rows (read-only)").set_defaults(fn=cmd_snapshot)
    r = sub.add_parser("run", help="run P2 on the sample with the two switches ON in this process")
    r.add_argument("--dry-run", action="store_true", help="compute, write nothing")
    r.add_argument("--switches-off", action="store_true", help="rollback: the same run with both switches OFF (the old code path)")
    r.set_defaults(fn=cmd_run)
    sub.add_parser("diff", help="snapshot versus the rows now (read-only)").set_defaults(fn=cmd_diff)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
