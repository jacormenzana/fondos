# -*- coding: utf-8 -*-
"""Remove the fund_metrics rows that the current minimum-sample rules would no longer publish (FND-0401).

    python scripts/mig/fondos/clean_stale_metric_rows.py            # DRY RUN (default): counts only, nothing is written
    python scripts/mig/fondos/clean_stale_metric_rows.py --apply    # deletes them, one transaction per rule

Why it exists: since FND-0401 the P2 pipeline deletes the owned statistics a run does not produce, but only for the funds it RECOMPUTES. A fund whose inputs did not change is a cache hit and keeps its old rows
until the next recompute, so the rows already in the table stay until then. This is the one-shot catch-up for them (R-2 step 3: a data migration after the code fix). Measured 2026-11-08: 1,870 of the 1,891
funds with fewer than REGIME_MIN_OBS expansion months still carried a return_ann_expansion computed when the regime held more months, and 146 slope rows rested on fewer than ROLLING_SLOPE_MIN_OBS points.

Rules (the same numbers as the pipeline, from shared.config, so the two cannot disagree):
  * regime: a per-regime statistic (return_ann / vol_ann / sharpe / sortino / max_dd of a regime) whose fund has `n_obs_<regime>` below REGIME_MIN_OBS; the two crisis_stress_score_* when
    n_obs_crisis_financiera is below it. Only funds that HAVE an n_obs row are judged (no n_obs, no verdict);
  * slope: `sharpe_slope` (nominal) and `return_ann_slope` (real) with source_rows below ROLLING_SLOPE_MIN_OBS, any window.
It deletes derived, recomputable rows only (P2 rebuilds any of them if the rule changes), with the pipeline's own DML role; no DDL. The deletion is NOT part of any automatic flow: the owner runs --apply.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
for _p in (_ROOT, _ROOT / "proyecto2"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from shared import config as _cfg                                                     # noqa: E402
from shared.db import get_connection                                                  # noqa: E402
from src.calculations.regime_returns import CRISIS_STAT_NAMES, REGIME_STAT_BASES, _REGIME_SUFFIX   # noqa: E402
from src.calculations.rolling_stats import slope_metric_keys                          # noqa: E402

METRIC_VERSION = "v1"


def regime_pairs() -> tuple:
    """(stat names, their regime suffix) as two parallel lists: every per-regime statistic and the two crisis scores (suffix crisis_financiera). Pure."""
    stats, suffixes = [], []
    for suffix in _REGIME_SUFFIX.values():
        for base in REGIME_STAT_BASES:
            stats.append(f"{base}_{suffix}"); suffixes.append(suffix)
    for name in CRISIS_STAT_NAMES:
        stats.append(name); suffixes.append("crisis_financiera")
    return stats, suffixes


_REGIME_SELECT = ("WITH owned(stat, suffix) AS (SELECT * FROM unnest(%s::text[], %s::text[])) "
                  "SELECT m.isin, f.in_current_universe FROM fund_metrics m JOIN owned o ON m.metric = o.stat "
                  "JOIN fund_metrics n ON n.isin = m.isin AND n.horizon = m.horizon AND n.real_flag = 0 AND n.metric_version = m.metric_version AND n.metric = 'n_obs_' || o.suffix "
                  "LEFT JOIN silver.fund_master f ON f.isin = m.isin "
                  "WHERE m.horizon = 'since_inception' AND m.real_flag = 0 AND m.metric_version = %s AND n.value IS NOT NULL AND n.value < %s")


def plan(conn) -> dict:
    """Counts of the rows each rule would delete: rows, funds, and the same restricted to the in-universe funds. Read-only."""
    stats, suffixes = regime_pairs()
    reg = conn.execute(_REGIME_SELECT, (stats, suffixes, METRIC_VERSION, _cfg.REGIME_MIN_OBS)).fetchall()
    keys = slope_metric_keys()
    slope = conn.execute("SELECT m.isin, f.in_current_universe FROM fund_metrics m LEFT JOIN silver.fund_master f ON f.isin = m.isin WHERE m.metric_version = %s "
                         "AND (m.metric, m.real_flag) IN (SELECT * FROM unnest(%s::text[], %s::smallint[])) AND m.source_rows < %s",
                         (METRIC_VERSION, [k[0] for k in keys], [k[1] for k in keys], _cfg.ROLLING_SLOPE_MIN_OBS)).fetchall()
    def summarise(rows):
        return {"rows": len(rows), "funds": len({r[0] for r in rows}), "rows_in_universe": sum(1 for r in rows if r[1] == 1), "funds_in_universe": len({r[0] for r in rows if r[1] == 1})}
    return {"regime_stats_below_floor": summarise(reg), "slope_rows_below_floor": summarise(slope), "floors": {"REGIME_MIN_OBS": _cfg.REGIME_MIN_OBS, "ROLLING_SLOPE_MIN_OBS": _cfg.ROLLING_SLOPE_MIN_OBS}}


def apply(conn) -> dict:
    """Delete them, one transaction per rule. Returns the rows deleted per rule."""
    stats, suffixes = regime_pairs()
    out = {}
    keys = slope_metric_keys()
    conn.execute("BEGIN")
    try:
        out["regime_stats_below_floor"] = conn.execute(
            "WITH owned(stat, suffix) AS (SELECT * FROM unnest(%s::text[], %s::text[])) "
            "DELETE FROM fund_metrics m USING owned o, fund_metrics n WHERE m.metric = o.stat AND m.horizon = 'since_inception' AND m.real_flag = 0 AND m.metric_version = %s "
            "AND n.isin = m.isin AND n.horizon = m.horizon AND n.real_flag = 0 AND n.metric_version = m.metric_version AND n.metric = 'n_obs_' || o.suffix AND n.value IS NOT NULL AND n.value < %s",
            (stats, suffixes, METRIC_VERSION, _cfg.REGIME_MIN_OBS)).rowcount
        conn.execute("COMMIT")
        conn.execute("BEGIN")
        out["slope_rows_below_floor"] = conn.execute(
            "DELETE FROM fund_metrics WHERE metric_version = %s AND (metric, real_flag) IN (SELECT * FROM unnest(%s::text[], %s::smallint[])) AND source_rows < %s",
            (METRIC_VERSION, [k[0] for k in keys], [k[1] for k in keys], _cfg.ROLLING_SLOPE_MIN_OBS)).rowcount
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--apply", action="store_true", help="delete the rows (default: dry run, counts only)")
    args = ap.parse_args(argv)
    conn = get_connection()
    if not args.apply:
        conn.read_only = True                                    # the server refuses any write on a dry run
    import json
    print(json.dumps(plan(conn), indent=1))
    if not args.apply:
        print("\nDRY RUN: nothing written. Add --apply (owner) to delete these rows.")
        return 0
    print("\nDELETED:", json.dumps(apply(conn), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
