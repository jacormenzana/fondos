# -*- coding: utf-8 -*-
"""
Beta-shift audit: compare the macro betas of gold.fund_metrics before and after a CALC_VERSION recalculation.

Created for FND-0176 (m2_global_yoy rebuilt; every fund's macro OLS changes because the factor changed).

    # 1) BEFORE the full P2 run: freeze the current values
    python scripts/audit/beta_shift_audit.py --snapshot C:/data/fondos/audit/macro_betas_20260930.csv

    # 2) AFTER the full P2 run: diff live against the snapshot
    python scripts/audit/beta_shift_audit.py --compare C:/data/fondos/audit/macro_betas_20260930.csv \
        --version 20261002 [--metric-like beta_m2_global] [--out C:/data/fondos/audit/beta_shift_outliers.csv]

Read-only. Uses FONDOS_PG_DSN (fondos_app or the read-only role); never prints the DSN.
Reports per metric: funds compared, funds still on an older version, NULL/NaN after, mean / median / max |delta|,
and the funds whose |delta| exceeds 3 robust standard deviations (MAD-based) or sits in the top 1%.
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import re
import statistics
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

_METRIC_LIKE = ("beta\\_%", "macro\\_%")           # LIKE patterns for the snapshot (betas + r2/alpha/n_obs)
_HORIZON = "since_inception"


def _connect():
    dsn = os.environ.get("FONDOS_PG_DSN")
    if not dsn:                                      # fall back to the repo .env, same convention as the pipelines
        env = _ROOT / ".env"
        if env.exists():
            for line in env.read_text(encoding="utf8").splitlines():
                m = re.match(r"\s*FONDOS_PG_DSN\s*=\s*(.*)", line)
                if m:
                    dsn = m.group(1).strip().strip("\"'")
    if not dsn:
        sys.exit("FONDOS_PG_DSN is not set")
    import psycopg
    return psycopg.connect(dsn)


def _fetch(conn, metric_like=None):
    where = "metric LIKE ANY(%s)" if metric_like is None else "metric LIKE %s"
    arg = list(_METRIC_LIKE) if metric_like is None else metric_like
    return conn.execute(
        f"SELECT isin, metric, value, algorithm_version FROM gold.fund_metrics "
        f"WHERE horizon=%s AND {where}", (_HORIZON, arg)).fetchall()


def snapshot(path: str) -> None:
    conn = _connect()
    rows = _fetch(conn)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf8") as fh:
        w = csv.writer(fh)
        w.writerow(["isin", "metric", "value", "algorithm_version"])
        w.writerows(rows)
    print(f"snapshot: {len(rows)} rows ({len({r[0] for r in rows})} funds) -> {path}")


def _robust_sigma(xs):
    med = statistics.median(xs)
    return 1.4826 * statistics.median([abs(x - med) for x in xs]) or 0.0


def compare(path: str, version: str, metric_like: str | None, out: str | None) -> int:
    before = {}
    with open(path, encoding="utf8") as fh:
        for r in csv.DictReader(fh):
            before[(r["isin"], r["metric"])] = (float(r["value"]) if r["value"] not in ("", "None") else None)
    conn = _connect()
    now = {(i, m): (v, ver) for i, m, v, ver in _fetch(conn)}
    metrics = sorted({m for _, m in before if metric_like is None or m == metric_like})
    flagged = []
    problems = 0
    print(f"{'metric':22s} {'compared':>8s} {'old_ver':>7s} {'null/nan':>8s} {'mean|d|':>10s} {'med|d|':>10s} {'max|d|':>10s} {'>3sig':>6s} {'top1%':>6s}")
    for m in metrics:
        deltas, old_ver, bad = [], 0, 0
        for (isin, mm), b in before.items():
            if mm != m:
                continue
            cur = now.get((isin, mm))
            if cur is None:
                continue                                    # fund dropped out of the metric (e.g. now quarantined)
            v, ver = cur
            if ver != version:
                old_ver += 1
                continue
            if v is None or (isinstance(v, float) and math.isnan(v)):
                bad += 1
                continue
            if b is not None:
                deltas.append((isin, v - b, b, v))
        if not deltas:
            print(f"{m:22s} {0:8d} {old_ver:7d} {bad:8d}")
            problems += bad + old_ver
            continue
        ad = [abs(d) for _, d, _, _ in deltas]
        sig = _robust_sigma([d for _, d, _, _ in deltas])
        med = statistics.median([d for _, d, _, _ in deltas])
        cutoff = sorted(ad)[int(len(ad) * 0.99)] if len(ad) >= 100 else float("inf")
        n3 = [(i, d, b, v) for i, d, b, v in deltas if sig and abs(d - med) > 3 * sig]
        t1 = [(i, d, b, v) for i, d, b, v in deltas if cutoff > 0 and abs(d) >= cutoff]   # all-zero deltas flag nothing
        print(f"{m:22s} {len(deltas):8d} {old_ver:7d} {bad:8d} {statistics.mean(ad):10.5f} {statistics.median(ad):10.5f} {max(ad):10.5f} {len(n3):6d} {len(t1):6d}")
        flagged += [(m, i, b, v, d, "3sigma" if (i, d, b, v) in n3 else "top1pct") for i, d, b, v in {*n3, *t1}]
        problems += bad + old_ver
    if out:
        with open(out, "w", newline="", encoding="utf8") as fh:
            w = csv.writer(fh)
            w.writerow(["metric", "isin", "before", "after", "delta", "flag"])
            w.writerows(sorted(flagged, key=lambda r: -abs(r[4])))
        print(f"outliers: {len(flagged)} rows -> {out}")
    if problems:
        print(f"ATTENTION: {problems} fund-metric rows are still on an older version or NULL/NaN "
              f"(the P2 run is incomplete or a metric was dropped)")
    return 1 if problems else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--snapshot", metavar="CSV", help="write the current macro betas to CSV (run BEFORE the P2 cycle)")
    g.add_argument("--compare", metavar="CSV", help="diff live against a snapshot (run AFTER the P2 cycle)")
    ap.add_argument("--version", help="CALC_VERSION the live rows must carry (required with --compare)")
    ap.add_argument("--metric-like", help="restrict --compare to one metric, e.g. beta_m2_global")
    ap.add_argument("--out", help="CSV for the flagged outliers")
    a = ap.parse_args()
    if a.snapshot:
        snapshot(a.snapshot)
        return 0
    if not a.version:
        ap.error("--version is required with --compare")
    return compare(a.compare, a.version, a.metric_like, a.out)


if __name__ == "__main__":
    sys.exit(main())
