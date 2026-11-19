# scripts/audit/p3/p3_cost_check.py
# -*- coding: utf-8 -*-
r"""
Cost check of roadmap v4 (FND-0430, doc/backlog/PKG_L_p3_alignment.md): how the value of turnover reduction scales with the cost of trading. READ-ONLY on a saved PIT run
(its inputs/ snapshot; the DB is read only for the stated entry / exit fees of fund_master).

    C:\Users\jacor\.conda\envs\des\python.exe -X utf8 scripts\audit\p3\p3_cost_check.py --run-dir out\reports\pit_backtest\20261119_202509

Variants: hysteresis bands (monthly) and rebalance cadences (band 0, phase-averaged). For every variant the GROSS monthly series and its turnover are charged at 25 / 50 / 100 / 200 bp per side
(and any --extra-bps, e.g. the cost the owner really pays), and with the funds' stated fees fund by fund (a sensitivity: fees are TODAY's attributes, no history). It states the break-even cost
at which each variant overtakes the baseline (band 0, monthly) on annual return and on Sharpe. Step 0 reproduces the 25 bp band table of the run (hysteresis_experiment.csv) and refuses to go on if it
does not match; on the sealed run (pit_seal) every artifact hash is verified first. Artifacts: <run-dir>/cost_check/ : cost_grid.csv, break_even.csv, fee_sensitivity.csv, REPORT.md.
"""
import argparse
import dataclasses
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto3.src.pit_backtest import DEFAULT_CONSTRAINTS, assemble_table, build_weights, prepare_pit_backtest, series_stats  # noqa: E402
from proyecto3.src.pit_cost_check import DEFAULT_GRID_BPS, break_even_bps, fee_cost_series, net_series  # noqa: E402
from proyecto3.src.pit_oos import load_saved_run  # noqa: E402
from proyecto3.src.pit_seal import RUN_STAMP, verify_seal  # noqa: E402

REPRODUCE_TOL = 5e-4          # annual return of the saved 25 bp band table (round(., 6) in the monthly series and a different chaining path)


def build_parser():
    p = argparse.ArgumentParser(description="Cost check on a saved PIT run (read-only).")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--bands", default="0,0.05,0.10,0.20,0.40")
    p.add_argument("--cadences", default="1,2,3,6,12", help="rebalance every k months (band 0, every phase, phase-averaged)")
    p.add_argument("--bps", default=",".join(str(int(b)) for b in DEFAULT_GRID_BPS))
    p.add_argument("--extra-bps", default="", help="more per-side costs, e.g. the owner's actual cost")
    p.add_argument("--no-fees", action="store_true", help="skip the fund-by-fund fee sensitivity (no DB read)")
    p.add_argument("--skip-seal", action="store_true", help="only for a run that is not the sealed baseline")
    return p


def _variants(pit_run, inputs, hist, at, bands, cadences):
    """{label: (gross_1m, turnover, cash_1m, W)}; the cadence variants are the phase average of the gross and turnover series (net is linear in them)."""
    base = prepare_pit_backtest(pit_run, inputs, hist, at, (1,))
    fx = getattr(inputs, "fx_exposed", None)
    out = {}
    for b in bands:
        if b == 0:
            W, cash_w = base.W, base.cash_w
        else:
            cons = dataclasses.replace(DEFAULT_CONSTRAINTS, hysteresis_band=b)
            W, cash_w = build_weights(pit_run.scores, inputs.attrs, hist, base.at, cons, use_incumbents=True, fx_exposed=fx)
        t = assemble_table(dataclasses.replace(base, W=W, cash_w=cash_w), 0.0)
        out[f"band_{b:g}"] = (t["gross_1m"], t["turnover"], t["cash_ret_1m"], W)
    for k in cadences:
        if k == 1:
            continue
        parts = []
        for ph in range(k):
            W, cash_w = build_weights(pit_run.scores, inputs.attrs, hist, base.at, DEFAULT_CONSTRAINTS, rebalance_every=k, phase=ph, fx_exposed=fx)
            t = assemble_table(dataclasses.replace(base, W=W, cash_w=cash_w), 0.0)
            parts.append((t["gross_1m"], t["turnover"], t["cash_ret_1m"], W))
        g = pd.concat([p[0] for p in parts], axis=1).mean(axis=1, skipna=True)
        tu = pd.concat([p[1] for p in parts], axis=1).mean(axis=1, skipna=True)
        out[f"cadence_{k}m"] = (g, tu, parts[0][2], None)           # the fee sensitivity needs one W: not defined for a phase average
    return out


def _load_fees(conn) -> "tuple[pd.Series, pd.Series]":
    rows = conn.execute("SELECT ISIN, Entry_Fee_Pct, Exit_Fee_Pct FROM fund_master").fetchall()
    df = pd.DataFrame([tuple(r) for r in rows], columns=["isin", "entry", "exit"]).set_index("isin")
    return pd.to_numeric(df["entry"], errors="coerce"), pd.to_numeric(df["exit"], errors="coerce")


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    run = Path(args.run_dir)
    sealed = None
    if run.name == RUN_STAMP and not args.skip_seal:
        sealed = verify_seal(run)
        print(f"[SEAL] {len(sealed)} artifacts verified against the baseline seal of {RUN_STAMP}")
    else:
        print("[SEAL] NOT sealed (another run or --skip-seal): hashes are not verified")
    from shared.db import get_connection
    conn = get_connection()
    pit_run, inputs, hist, at = load_saved_run(run, conn)
    bands = tuple(float(x) for x in args.bands.split(",") if x.strip())
    cadences = tuple(int(x) for x in args.cadences.split(",") if x.strip())
    grid_bps = tuple(float(x) for x in (args.bps + "," + args.extra_bps).split(",") if x.strip())
    var = _variants(pit_run, inputs, hist, at, bands, cadences)

    # step 0: reproduce the saved 25 bp band table before reading anything new
    saved = pd.read_csv(run / "hysteresis_experiment.csv").set_index("hysteresis_band")
    for b in bands:
        if b in saved.index:
            g, tu, cash, _ = var[f"band_{b:g}"]
            got = series_stats(net_series(g, tu, 25.0), cash)["ann_return"]
            want = float(saved.loc[b, "ann_return"])
            if abs(got - want) > REPRODUCE_TOL:
                print(f"[REPRODUCE] FAIL band {b:g}: {got:.5f} != saved {want:.5f}")
                return 2
    print("[REPRODUCE] OK: the 25 bp band table of the run is reproduced")

    rows = []
    for label, (g, tu, cash, _) in var.items():
        for b in grid_bps:
            st = series_stats(net_series(g, tu, b), cash)
            rows.append({"variant": label, "bps_per_side": b, "mean_turnover": float(tu.mean()), "annual_cost_drag": float(tu.mean() * 12 * b / 1e4),
                         "gross_ann_return": series_stats(g, cash)["ann_return"], **st})
    grid = pd.DataFrame(rows)

    g0, t0, cash0, _ = var["band_0"]
    be = []
    for label, (g, tu, cash, _) in var.items():
        if label == "band_0":
            continue
        be.append({"variant": label, "mean_turnover": float(tu.mean()), "baseline_turnover": float(t0.mean()),
                   "break_even_bps_ann_return": break_even_bps(g, tu, g0, t0, cash0, "ann_return"),
                   "break_even_bps_sharpe": break_even_bps(g, tu, g0, t0, cash0, "sharpe")})
    be = pd.DataFrame(be)

    fees = pd.DataFrame()
    if not args.no_fees:
        entry, exit_ = _load_fees(conn)
        eligible_attr = pd.Index(pit_run.scores.loc[pit_run.scores["eligible"], "isin"].unique())      # the funds the model could ever hold, not retired junk
        known = entry.reindex(eligible_attr).dropna()
        de = float(known.median()) if len(known) else 0.0
        dx = float(exit_.reindex(eligible_attr).dropna().median()) if exit_.reindex(eligible_attr).notna().any() else 0.0
        print(f"[FEES] entry fee known for {len(known)} of {len(eligible_attr)} ever-eligible funds; median {de:.4f}; default for unknown funds = the median of the ever-eligible funds (counted, not hidden)")
        frows = []
        for label, (g, tu, cash, W) in var.items():
            if W is None:
                continue
            cost, miss = fee_cost_series(W, entry, exit_, de, dx)
            st = series_stats(g - cost, cash)
            frows.append({"variant": label, "mean_fee_cost_month": float(cost.mean()), "annual_fee_drag": float(cost.mean() * 12), **st,
                          "funds_missing_entry": miss["missing_entry"], "default_entry": de, "default_exit": dx})
        fees = pd.DataFrame(frows)

    out = run / "cost_check"
    out.mkdir(exist_ok=True)
    grid.to_csv(out / "cost_grid.csv", index=False)
    be.to_csv(out / "break_even.csv", index=False)
    fees.to_csv(out / "fee_sensitivity.csv", index=False)
    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 30)
    piv = grid.pivot(index="variant", columns="bps_per_side", values="ann_return").reindex(list(var))
    print("\nAnnual return net of costs, by variant and cost per side (bp):\n", piv.round(4).to_string())
    print("\nBreak-even cost vs band_0 (None = never up to 400 bp):\n", be.round(3).to_string(index=False))
    if len(fees):
        print("\nFund-by-fund stated fees (sensitivity):\n", fees[["variant", "annual_fee_drag", "ann_return", "sharpe", "max_drawdown"]].round(4).to_string(index=False))
    lines = [f"# Cost check, run {run.name}", "", f"Seal: {'verified ' + str(len(sealed)) + ' artifacts' if sealed else 'NOT verified'}. Step 0 reproduced the saved 25 bp band table.", "",
             "## Annual return net of costs (rows: variant, columns: bp per side)", "", "```", piv.round(4).to_string(), "```", "", "## Break-even cost against band_0", "", "```", be.round(3).to_string(index=False), "```", ""]
    if len(fees):
        lines += ["## Stated fees fund by fund (today's attributes; sensitivity, not history)", "", "```", fees.round(4).to_string(index=False), "```", ""]
    (out / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[OUT] {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
