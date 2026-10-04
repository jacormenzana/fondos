# scripts/launch/p3_pit_oos.py
# -*- coding: utf-8 -*-
r"""
Out-of-sample test of the rotation parameters on a SAVED PIT backtest run (FND-0193 / FND-0207). Read-only on the DB.

    C:\data\envs\des\python.exe -X utf8 scripts\launch\p3_pit_oos.py --run-dir c:\data\fondos\reports\pit_backtest\<STAMP>

Reuses the scores of that run (scores_long.parquet), so it needs no recomputation of the scoring (minutes, not an hour):
for the grid hysteresis band x weight no-trade band it builds the portfolios, chooses a cell ONLY on the design period
(dates <= --split, default 2016-12-31) and shows how it and the fixed alternatives did on the blind period, with a paired
bootstrap interval of the Sharpe difference against the reference (band 0, weights at target). Artifacts go to
<run-dir>/oos_<split>_<bps>bp/ : oos_grid.csv, oos_blind.csv, REPORT.md.
"""

import argparse
import sys
import warnings
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore")
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto3.src.pit_oos import HYSTERESIS_BANDS, WEIGHT_BANDS, band_grid_tables, load_saved_run, oos_report
from shared.db import get_connection


def build_parser():
    p = argparse.ArgumentParser(description="Out-of-sample test of hysteresis / weight bands on a saved PIT run (read-only).")
    p.add_argument("--run-dir", required=True, help="a p3_pit_backtest output folder (needs scores_long.parquet and pit_backtest_table.csv)")
    p.add_argument("--split", default="2016-12-31", help="last DESIGN month-end; the blind period starts the month after (default 2016-12-31)")
    p.add_argument("--bps", type=float, default=25.0, help="cost per side in bp (default 25)")
    p.add_argument("--bands", default=",".join(str(b) for b in HYSTERESIS_BANDS), help="hysteresis bands")
    p.add_argument("--weight-bands", default=",".join(str(b) for b in WEIGHT_BANDS), help="weight no-trade bands (master-weight units)")
    p.add_argument("--tolerance", type=float, default=0.02, help="design Sharpe tolerance for the parsimony rule (default 0.02)")
    p.add_argument("--n-boot", type=int, default=2000)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    run = Path(args.run_dir)
    conn = get_connection()
    pit_run, inputs, hist, at = load_saved_run(run, conn)
    bands = tuple(float(x) for x in args.bands.split(",") if x.strip())
    deltas = tuple(float(x) for x in args.weight_bands.split(",") if x.strip())
    tables = band_grid_tables(pit_run, inputs, hist, at, bands=bands, deltas=deltas, tx_cost_bps=args.bps)
    rep = oos_report(tables, args.split, tolerance=args.tolerance, n_boot=args.n_boot)
    out = run / f"oos_{pd.Timestamp(args.split):%Y%m%d}_{args.bps:g}bp"
    out.mkdir(exist_ok=True)
    rep["grid"].to_csv(out / "oos_grid.csv", index=False, float_format="%.6g")
    rep["blind"].to_csv(out / "oos_blind.csv", index=False, float_format="%.6g")
    pd.set_option("display.width", 220)
    g = rep["grid"].pivot_table(index=["hysteresis_band", "weight_band"], columns="period",
                                values=["sharpe", "max_drawdown", "mean_turnover", "ann_return"])
    lines = [f"# OOS test on {run.name}  (design <= {args.split}, blind after; {args.bps:g} bp per side)", "",
             f"Cell chosen on the DESIGN period only (best net Sharpe, parsimony tolerance {args.tolerance}): {rep['chosen']}", "",
             "Selection diagnostics (per-period units): " + ", ".join(f"{k}={v:.4g}" if isinstance(v, float) else f"{k}={v}" for k, v in rep["diagnostics"].items()), "",
             "## Per cell and period", "", g.round(3).to_string(), "",
             "## Blind period: Sharpe difference vs the reference cell (0, 0), paired stationary bootstrap (90% interval)", "",
             rep["blind"].round(3).to_string(index=False)]
    (out / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
