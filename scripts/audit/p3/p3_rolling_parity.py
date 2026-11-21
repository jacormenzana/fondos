# scripts/audit/p3/p3_rolling_parity.py
# -*- coding: utf-8 -*-
r"""
R3 parity check of Package L step F (doc/decisiones/PREREG_P3_5A.md, lever L1 and L2): the PIT figure that feeds a lever must be the figure P2 stores, so that the live build and the PIT
backtest rank the same number. READ-ONLY (the database is only read, nothing is written; P2 and CALC_VERSION untouched).

    C:\Users\jacor\.conda\envs\des\python.exe -X utf8 scripts\audit\p3\p3_rolling_parity.py [--sample 400] [--seed 20261121]

Compares, on a seeded sample of funds that have both, at the `calculation_date` stored with each P2 row:
  * `pit_metrics.trailing_risk_metrics(window_months=60)["max_dd"]`      against `fund_metrics` metric max_dd, horizon rolling_5y, real_flag 0 (the L1 signal);
  * `pit_metrics.trailing_risk_metrics(window_months=36)["return_ann"]`  against `fund_metrics` metric return_ann, horizon rolling_3y, real_flag 0 (the L2 signal).
Prints counts, the share equal to 1e-9, the quantiles of the absolute difference and the worst few. Exit code 0 when every compared value agrees to 1e-6 (and at least 95 % of the sample was
comparable), 3 otherwise.
"""
import argparse
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import shared.config  # noqa: F401,E402  (loads the git-ignored .env first)
from proyecto3.src.pit_inputs import load_nav_panel  # noqa: E402
from proyecto3.src.pit_metrics import trailing_risk_metrics  # noqa: E402
from shared.db import get_connection  # noqa: E402

TOL = 1e-6
CASES = (("max_dd", 60, "max_dd", "rolling_5y"), ("return_ann", 36, "return_ann", "rolling_3y"))      # (PIT key, window months, P2 metric, P2 horizon)


def stored(conn, metric: str, horizon: str) -> pd.DataFrame:
    rows = conn.execute("SELECT isin, calculation_date, value FROM fund_metrics WHERE metric = %s AND horizon = %s AND real_flag = 0 AND value IS NOT NULL", (metric, horizon)).fetchall()
    df = pd.DataFrame([tuple(r) for r in rows], columns=["isin", "calc_date", "stored"])
    df["calc_date"] = pd.to_datetime(df["calc_date"])
    return df.set_index("isin")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="R3 parity of the PIT trailing metrics against P2's stored rolling metrics (read-only).")
    ap.add_argument("--sample", type=int, default=400)
    ap.add_argument("--seed", type=int, default=20261121)
    args = ap.parse_args(argv)
    conn = get_connection()
    try:
        st = {c[2] + "|" + c[3]: stored(conn, c[2], c[3]) for c in CASES}
        both = sorted(set.intersection(*[set(df.index) for df in st.values()]))
        rng = np.random.default_rng(args.seed)
        pick = sorted(rng.choice(both, size=min(args.sample, len(both)), replace=False).tolist())
        nav = load_nav_panel(conn, pick)
    finally:
        conn.close()
    print(f"[SAMPLE] {len(pick)} funds of {len(both)} that have both stored metrics (seed {args.seed}); NAV panel {nav.shape[0]} rows x {nav.shape[1]} funds")
    ok_all = True
    for key, window, metric, horizon in CASES:
        pit = trailing_risk_metrics(nav, None, None, window_months=window)[key]
        s = st[f"{metric}|{horizon}"].reindex(pick)
        vals, found, why = [], 0, {}
        for isin in pick:
            end = nav[isin].loc[:s.at[isin, "calc_date"]].last_valid_index()      # `calculation_date` is the day P2 ran: its window ends at the fund's last NAV on or before it
            if end is not None and pd.notna(pit.at[end, isin]):
                found += 1
                vals.append((isin, float(pit.at[end, isin]), float(s.at[isin, "stored"])))
            else:
                why[isin] = "no NAV before the calculation date" if end is None else "no full window in the PIT"
        t = pd.DataFrame(vals, columns=["isin", "pit", "stored"])
        t["abs_diff"] = (t["pit"] - t["stored"]).abs()
        comparable = found / len(pick)
        agree = float((t["abs_diff"] <= 1e-9).mean()) if len(t) else float("nan")
        within = float((t["abs_diff"] <= TOL).mean()) if len(t) else float("nan")
        q = t["abs_diff"].quantile([0.5, 0.9, 0.99, 1.0]).round(9).tolist() if len(t) else []
        passed = comparable >= 0.95 and within == 1.0
        ok_all &= passed
        print(f"[PARITY] {key} window {window}m vs P2 {metric} {horizon}: comparable {found}/{len(pick)} ({comparable:.1%}); equal to 1e-9: {agree:.1%}; within {TOL:g}: {within:.1%}; "
              f"abs diff quantiles 50/90/99/max: {q} -> {'PASS' if passed else 'FAIL'}")
        if len(t) and not passed:
            print(t.sort_values("abs_diff", ascending=False).head(6).round(6).to_string(index=False))
        if why:
            print(f"[PARITY]   not comparable: {pd.Series(why).value_counts().to_dict()}")
    return 0 if ok_all else 3


if __name__ == "__main__":
    sys.exit(main())
