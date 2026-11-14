#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
p2_perf_harness.py -- timing and bit-for-bit equivalence of the peer-based P2 families on a fixed sample (FND-0255).

    python scripts/audit/p2_perf_harness.py capture <out.pkl> --sample <isins.txt>           # run THIS tree's code, save every metric row + timings
    python scripts/audit/p2_perf_harness.py compare <ref.pkl>  --sample <isins.txt>          # run THIS tree's code, compare with a saved reference
    ... --no-cache                                                                           # force the legacy per-fund queries (PEER_PANEL_CACHE_ENABLED off)

Read-only: the compute_* functions only read. The families run in the pipeline's order for each ISIN; the per-run caches are reset and activated exactly as
run_pipeline.run() does (momentum's category cache, peer_panel.start_run). The sample is a plain text file of ISINs (one per line); keep it STABLE across
sessions so before/after numbers compare (the 2026-10-18 sample: 36 funds, 4 per nature by md5(isin) + the shadow ISINs + 3 non-EUR + 3 short-NAV).
`compare` exits 1 on any difference; capture rows are compared with exact equality (list of tuples of floats).
"""
from __future__ import annotations

import argparse
import pickle
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "proyecto2"))

import shared.config as cfg  # noqa: E402
from shared.db import get_connection  # noqa: E402
from src.calculations import capture_ratios, momentum, persistence  # noqa: E402
from src.readers.db_readers import load_nav  # noqa: E402

FAMILIES = (("momentum", momentum.compute_momentum), ("capture", capture_ratios.compute_capture_ratios),
            ("persistence", persistence.compute_persistence))


def run(sample: list, use_cache: bool) -> tuple:
    conn = get_connection()
    conn.read_only = True
    momentum.reset_category_returns_cache()
    panel = None
    if use_cache:
        try:
            from src.calculations import peer_panel as panel
        except ImportError:                                 # a tree from before FND-0255: nothing to activate
            panel = None
    if panel is not None:
        panel.start_run(conn)
    nat = dict(conn.execute("SELECT isin, fund_nature FROM fund_master WHERE isin = ANY(%s)", (sample,)).fetchall())
    results, timings = {}, {name: 0.0 for name, _ in FAMILIES}
    for isin in sample:
        nav = load_nav(conn, isin)
        fam = {}
        for name, fn in FAMILIES:
            t0 = time.perf_counter()
            fam[name] = fn(isin, nat.get(isin), nav, conn)
            timings[name] += time.perf_counter() - t0
        results[isin] = fam
    if panel is not None:
        panel.reset()
    conn.close()
    return results, timings


def _evaluate(conn, sample: list, panel) -> tuple:
    """One pass over the sample with the per-run caches reset (momentum) and the peer panel active (`panel` module) or inactive (None)."""
    momentum.reset_category_returns_cache()
    if panel is not None:
        panel.start_run(conn)
    nat = dict(conn.execute("SELECT isin, fund_nature FROM fund_master WHERE isin = ANY(%s)", (sample,)).fetchall())
    results, timings = {}, {name: 0.0 for name, _ in FAMILIES}
    for isin in sample:
        nav = load_nav(conn, isin)
        results[isin] = {}
        for name, fn in FAMILIES:
            t0 = time.perf_counter()
            results[isin][name] = fn(isin, nat.get(isin), nav, conn)
            timings[name] += time.perf_counter() - t0
    if panel is not None:
        panel.reset()
    return results, timings


def selfcheck(sample: list) -> int:
    """Legacy and cached code on the SAME connection and the SAME database snapshot (REPEATABLE READ): data cannot move between the two passes, so any
    difference is the code's. Exact equality is demanded of every metric row; differences are listed with their relative size."""
    import psycopg
    from src.calculations import peer_panel
    conn = get_connection()
    conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
    conn.read_only = True
    legacy, t_old = _evaluate(conn, sample, None)
    cached, t_new = _evaluate(conn, sample, peer_panel)
    conn.rollback()
    conn.close()
    n = len(sample)
    f = lambda t: " | ".join(f"{k} {v:6.1f}s ({v / n:5.2f}/fund)" for k, v in t.items()) + f" | total {sum(t.values()):.1f}s"
    print(f"{n} funds, one snapshot\n  legacy: {f(t_old)}\n  cached: {f(t_new)}")
    print(f"  speed-up: {sum(t_old.values()) / sum(t_new.values()):.1f}x total | " + " | ".join(f"{k} {t_old[k] / t_new[k]:.1f}x" for k in t_old if t_new[k] > 0))
    bad, rows, worst = [], 0, 0.0
    for isin in sample:
        for name, _ in FAMILIES:
            a, b = legacy[isin][name], cached[isin][name]
            rows += len(a)
            if a != b:
                bad.append((isin, name))
                if len(a) == len(b):
                    worst = max([worst] + [abs(x[1] - y[1]) / max(abs(x[1]), 1e-300) for x, y in zip(a, b)])
    print(f"  {rows} metric rows compared; " + ("BIT-IDENTICAL on every family and fund" if not bad else
          f"DIFFERENCES in {len(bad)} (isin, family), worst relative {worst:.3e}: {bad[:6]}"))
    return 0 if not bad else 1


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("mode", choices=("capture", "compare", "selfcheck"))
    ap.add_argument("pkl", nargs="?", default=None, help="reference file (capture/compare); not used by selfcheck")
    ap.add_argument("--sample", required=True, help="text file with one ISIN per line")
    ap.add_argument("--no-cache", action="store_true", help="do not activate peer_panel (legacy per-fund queries)")
    a = ap.parse_args(argv)
    sample = [l.strip() for l in Path(a.sample).read_text().splitlines() if l.strip()]
    if a.mode == "selfcheck":
        return selfcheck(sample)
    results, timings = run(sample, use_cache=not a.no_cache)
    n = len(sample)
    print(f"{n} funds | cache={'off' if a.no_cache else 'on'} | " + " | ".join(f"{k} {v:6.1f}s ({v / n:5.2f} s/fund)" for k, v in timings.items())
          + f" | total {sum(timings.values()):.1f}s")
    if a.mode == "capture":
        with open(a.pkl, "wb") as fh:
            pickle.dump({"results": results, "timings": timings}, fh)
        print(f"saved {sum(len(r) for f in results.values() for r in f.values())} metric rows to {a.pkl}")
        return 0
    ref = pickle.load(open(a.pkl, "rb"))
    bad = [(isin, name) for isin, fam in results.items() for name, rows in fam.items() if rows != ref["results"][isin][name]]
    rt = ref["timings"]
    print("reference: " + " | ".join(f"{k} {v:6.1f}s" for k, v in rt.items()) + f" | total {sum(rt.values()):.1f}s")
    print(f"speed-up: {sum(rt.values()) / sum(timings.values()):.2f}x total | " + " | ".join(f"{k} {rt[k] / timings[k]:.1f}x" for k in rt if timings[k] > 0))
    print("BIT-IDENTICAL on every family and fund" if not bad else f"DIFFERENCES in {len(bad)} (isin, family): {bad[:10]}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
