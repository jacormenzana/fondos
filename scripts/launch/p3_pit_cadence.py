# scripts/launch/p3_pit_cadence.py
# -*- coding: utf-8 -*-
r"""
Rebalance-cadence experiment on a SAVED PIT backtest run (FND-0217). Read-only on the DB.

    C:\data\envs\des\python.exe -X utf8 scripts\launch\p3_pit_cadence.py --run-dir c:\data\fondos\reports\pit_backtest\<STAMP>

Rebalances every k months (default 1, 2, 3, 4, 6, 12) holding the portfolio in between. Every phase of each cadence is run
(which months you rebalance in is luck for k > 1) and the statistics are reported on the phase-averaged return series with the
Sharpe range across phases. The cadence is chosen on the design period only (dates <= --split) and compared on the blind
period against monthly rebalancing with a paired bootstrap interval. Artifacts go to
<run-dir>/cadence_<split>_<bps>bp_h<band>/ : cadence_grid.csv, cadence_blind.csv, REPORT.md.
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

from proyecto3.src.pit_oos import CADENCES, cadence_report, cadence_tables, load_saved_run
from shared.db import get_connection


def build_parser():
    p = argparse.ArgumentParser(description="Rebalance-cadence experiment on a saved PIT run (read-only).")
    p.add_argument("--run-dir", required=True, help="a p3_pit_backtest output folder (needs scores_long.parquet and pit_backtest_table.csv)")
    p.add_argument("--split", default="2016-12-31", help="last DESIGN month-end (default 2016-12-31)")
    p.add_argument("--bps", type=float, default=25.0, help="cost per side in bp (default 25)")
    p.add_argument("--cadences", default=",".join(str(k) for k in CADENCES), help="rebalance every k months")
    p.add_argument("--hysteresis-bands", default="0,0.10", help="hysteresis bands to run the cadences under (default 0 and 0.10)")
    p.add_argument("--regime-trigger", action="store_true",
                   help="also rebalance at once when the regime label changes (slow in calm periods, fast on a regime change)")
    p.add_argument("--trigger-regimes", default="",
                   help="comma-separated regimes: rebalance at once only on a change INTO or OUT OF one of them (e.g. Crisis_Financiera)")
    p.add_argument("--tolerance", type=float, default=0.02)
    p.add_argument("--n-boot", type=int, default=2000)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    run = Path(args.run_dir)
    conn = get_connection()
    pit_run, inputs, hist, at = load_saved_run(run, conn)
    cadences = tuple(int(x) for x in args.cadences.split(",") if x.strip())
    only = frozenset(x.strip() for x in args.trigger_regimes.split(",") if x.strip())
    trigger = only if only else args.regime_trigger
    tag = ("_" + "-".join(sorted(only))) if only else ("_regime" if args.regime_trigger else "")
    label = (f", trigger on {sorted(only)}" if only else (", regime-change trigger" if args.regime_trigger else ""))
    pd.set_option("display.width", 220)
    for band in (float(x) for x in args.hysteresis_bands.split(",") if x.strip()):
        tables = cadence_tables(pit_run, inputs, hist, at, cadences=cadences, hysteresis_band=band, tx_cost_bps=args.bps,
                                regime_trigger=trigger)
        rep = cadence_report(tables, args.split, tolerance=args.tolerance, n_boot=args.n_boot)
        out = run / f"cadence_{pd.Timestamp(args.split):%Y%m%d}_{args.bps:g}bp_h{band:g}{tag}"
        out.mkdir(exist_ok=True)
        rep["grid"].to_csv(out / "cadence_grid.csv", index=False, float_format="%.6g")
        rep["blind"].to_csv(out / "cadence_blind.csv", index=False, float_format="%.6g")
        g = rep["grid"].pivot_table(index="cadence_months", columns="period",
                                    values=["sharpe", "sharpe_phase_min", "sharpe_phase_max", "max_drawdown", "mean_turnover", "ann_return"])
        lines = [f"# Cadence test on {run.name}, hysteresis band {band:g}{label}  (design <= {args.split}, blind after; {args.bps:g} bp per side)", "",
                 f"Cadence chosen on the DESIGN period only (best phase-averaged Sharpe, parsimony tolerance {args.tolerance}): every {rep['chosen']} month(s)", "",
                 "Selection diagnostics (per-period units): " + ", ".join(f"{k}={v:.4g}" if isinstance(v, float) else f"{k}={v}" for k, v in rep["diagnostics"].items()), "",
                 "## Per cadence and period (phase-averaged series; Sharpe range across phases)", "", g.round(3).to_string(), "",
                 "## Blind period: Sharpe difference vs monthly rebalancing, paired stationary bootstrap (90% interval)", "",
                 rep["blind"].round(3).to_string(index=False)]
        (out / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
        print("\n".join(lines) + "\n")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
