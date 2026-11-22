# scripts/audit/p3/p3_l1_feasibility.py
# -*- coding: utf-8 -*-
r"""
Package L, layer 1 (selection value): the FEASIBILITY of a historical or forward test that the P3 ranking adds gross return over a matched random pool, at the owner's materiality floor.
READ-ONLY (files of one saved P3-7 run) and BLIND: it computes standard errors, counts and minimum detectable effects only, never the mean or any other point estimate of the champion
minus the pool (`pit_p35a_prep.paired_se_annual_pp` never returns one; a test pins that no mean of the difference is taken here).

    py -X utf8 scripts\audit\p3\p3_l1_feasibility.py [--run-dir out\reports\p3_p37_trials\20261121_p37] [--floor-pp 0.5] [--alpha 0.05]

Series: the T0 baseline's monthly GROSS return (`<run>/T0_baseline/series.csv`) minus the mean of the 20 draws of each matched random pool (`<run>/pools/*.parquet`, gross), paired by month.
Gross, because layer 1 is the value of the SELECTION; the cost of trading it is layer 0 (P3-7). Result: the SE of the mean paired difference (pp a year, stationary block bootstrap, 12 x the
monthly mean), the MDE (one-sided, power 0.80) at the stated level, the SE and the months the floor would need, and the forward MDE at 12 / 24 / 36 / 60 / 120 months (SE x sqrt(n / T)).
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto3.src import pit_p35a_prep as P  # noqa: E402

POOLS = ("persistent_b0", "persistent_b10", "persistent_b20", "redraw")
FORWARD_MONTHS = (12, 24, 36, 60, 120)


def layer1_se(champion_gross: pd.Series, pool: pd.DataFrame) -> dict:
    """Standard errors (pp a year) of the paired monthly difference champion minus the mean pool draw, and of the difference against ONE draw (median over the draws). A month missing in the pool is dropped and counted.
    `pool` has columns date, draw, gross. No point estimate is computed or returned."""
    pool = pool.assign(date=pd.to_datetime(pool["date"]))
    mean_draw = pool.groupby("date")["gross"].mean()
    r = P.paired_se_annual_pp((champion_gross - mean_draw.reindex(champion_gross.index)).to_numpy(), return_n=True)
    single = [P.paired_se_annual_pp((champion_gross - g.set_index("date")["gross"].reindex(champion_gross.index)).to_numpy()) for _, g in pool.groupby("draw")]
    return {"se_pp": r["se_pp"], "se_single_draw_pp": float(np.median(single)), "n": r["n"], "dropped": r["dropped"], "draws": int(pool["draw"].nunique())}


def feasibility(se_pp: float, n: int, floor_pp: float, alpha: float) -> dict:
    """The MDE at `alpha` (one-sided, power 0.80), the SE the floor would need and the months of the same quality that would give it."""
    mde = P.mde_pp(se_pp, alpha)
    need = floor_pp / P.mde_pp(1.0, alpha)
    return {"mde_pp": mde, "se_needed_pp": need, "months_needed": n * (se_pp / need) ** 2, "testable_at_floor": bool(mde <= floor_pp)}


def forward_mde(se_pp: float, n: int, months=FORWARD_MONTHS, alpha: float = 0.05) -> dict:
    """{months: MDE (pp a year)} of a forward stream of that length, assuming the same monthly variance as the history (SE x sqrt(n / T))."""
    return {int(t): P.mde_pp(se_pp * np.sqrt(n / t), alpha) for t in months}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="p3_l1_feasibility")
    ap.add_argument("--run-dir", default=str(_ROOT / "out" / "reports" / "p3_p37_trials" / "20261121_p37"))
    ap.add_argument("--floor-pp", type=float, default=0.5)
    ap.add_argument("--alpha", type=float, default=0.05)
    args = ap.parse_args(argv)
    run = Path(args.run_dir)
    champ = pd.read_csv(run / "T0_baseline" / "series.csv", parse_dates=["date"]).set_index("date")["gross"]
    rows = {}
    for name in POOLS:
        rows[name] = layer1_se(champ, pd.read_parquet(run / "pools" / f"{name}.parquet"))
    print(f"champion = T0 baseline gross; pools = mean of the draws; one-sided alpha {args.alpha}, power 0.80; floor {args.floor_pp} pp a year")
    print(f"{'pool':15s} {'n':>4s} {'dropped':>7s} {'draws':>5s} {'SE pp':>7s} {'SE 1 draw':>9s} {'MDE pp':>7s} {'MDE/floor':>9s}")
    for name, r in rows.items():
        f = feasibility(r["se_pp"], r["n"], args.floor_pp, args.alpha)
        print(f"{name:15s} {r['n']:4d} {r['dropped']:7d} {r['draws']:5d} {r['se_pp']:7.3f} {r['se_single_draw_pp']:9.3f} {f['mde_pp']:7.2f} {f['mde_pp'] / args.floor_pp:9.2f}")
    worst = max(rows.values(), key=lambda r: r["se_pp"])
    f = feasibility(worst["se_pp"], worst["n"], args.floor_pp, args.alpha)
    print(f"worst SE {worst['se_pp']:.3f} pp over {worst['n']} months; the floor needs SE <= {f['se_needed_pp']:.3f} pp = {f['months_needed']:.0f} months ({f['months_needed'] / 12:.0f} years) of the same quality")
    print("forward MDE (pp a year): " + "; ".join(f"{t} m {v:.2f}" for t, v in forward_mde(worst["se_pp"], worst["n"], alpha=args.alpha).items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
