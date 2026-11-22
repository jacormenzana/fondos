# scripts/audit/p3/p3_cpi_tables.py
# -*- coding: utf-8 -*-
r"""
Summary tables of the baseline model (PIT backtest at the owner's stated fees) against inflation and money growth, owner request 2026-11-22. READ-ONLY on the database; writes CSV files under out\reports\p3_cpi_tables\.

    C:\Users\jacor\.conda\envs\des\python.exe -X utf8 scripts\audit\p3\p3_cpi_tables.py --series out\reports\p3_p37_trials\20261121_p37\T0_baseline\series.csv --run-dir out\reports\pit_backtest\20261119_202509

Every benchmark is UNLAGGED and measured over the SAME months as the returns: the return of the formation date t is earned in month t+1 and is compared with the readings OF month t+1 (an after-the-fact comparison; the
publication lag belongs to the model's decisions, not to this display). CPI is shown for Spain alone and for the plain average of Spain and the euro area; M2 and M3 are the euro-area year-on-year rates. A reading that
does not exist yet (the last month of M2 / M3) is averaged over the months it has, never filled. Display only: no model and no production input reads these tables.
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

PERIODS = 12


def return_months(formation_index) -> pd.DatetimeIndex:
    """The month in which the return of each formation date is earned: the following month-end."""
    return pd.DatetimeIndex(formation_index) + pd.offsets.MonthEnd(1)


def cpi_yoy(levels: pd.DataFrame) -> pd.DataFrame:
    """Year-on-year change of each index against the SAME MONTH a year earlier (a date, never a row offset), per region, and their plain average. `levels`: month-end index, columns ES and EU."""
    prev = levels.copy()
    prev.index = prev.index + pd.offsets.MonthEnd(12)
    y = levels / prev.reindex(levels.index) - 1.0
    y["AVG"] = y[["ES", "EU"]].mean(axis=1, skipna=False)
    return y[["ES", "EU", "AVG"]]


def _ann(x) -> float:
    a = np.asarray(x, dtype=float)
    a = a[np.isfinite(a)]
    return float(np.expm1(np.log1p(a).mean() * PERIODS)) if len(a) else float("nan")


def period_block(s: pd.DataFrame, macro: pd.DataFrame) -> dict:
    """One row: the model's annualised gross and net return over the months of `s` (formation-date index), the fee cost = gross - net, the benchmarks as the mean of their monthly readings over the same months,
    and the excesses in percentage points. `macro` is indexed by RETURN month with columns ES, EU, AVG, M2, M3 (fractions)."""
    s = s[s["gross"].notna() & s["netfee"].notna()]
    m = macro.reindex(return_months(s.index))
    m.index = s.index
    gross, net = _ann(s["gross"]), _ann(s["netfee"])
    out = {"months": int(len(s)), "Gross": 100 * gross, "Fees": 100 * (gross - net), "Net": 100 * net,
           "CPI_ES": 100 * m["ES"].mean(), "CPI_AVG": 100 * m["AVG"].mean(), "M2": 100 * m["M2"].mean(), "M3": 100 * m["M3"].mean(),
           "CPI_months": int(m["ES"].notna().sum()), "M2_months": int(m["M2"].notna().sum()), "M3_months": int(m["M3"].notna().sum())}
    for tag in ("ES", "AVG"):
        out[f"Net-CPI_{tag}"] = out["Net"] - out[f"CPI_{tag}"]
        out[f"Net-(CPI+M2)_{tag}"] = out["Net"] - out[f"CPI_{tag}"] - out["M2"]
        out[f"Net-(CPI+M3)_{tag}"] = out["Net"] - out[f"CPI_{tag}"] - out["M3"]
    return out


def yearly(s: pd.DataFrame, macro: pd.DataFrame) -> pd.DataFrame:
    s = s[s["gross"].notna() & s["netfee"].notna()]
    year = return_months(s.index).year
    return pd.DataFrame({int(y): period_block(g, macro) for y, g in s.groupby(year)}).T.rename_axis("year")


def cycles(s: pd.DataFrame, macro: pd.DataFrame, regime: pd.Series) -> pd.DataFrame:
    """Runs of consecutive months under the same regime label (the label of the formation date), with the first and last month in which their returns were earned (YYYYMM)."""
    ok = s["gross"].notna() & s["netfee"].notna()
    s, reg = s[ok], regime.reindex(s.index[ok])
    run = (reg != reg.shift()).cumsum()
    rows = []
    for _k, g in s.groupby(run):
        b = period_block(g, macro)
        rm = return_months(g.index)
        b.update({"regime": reg.loc[g.index[0]], "start": rm.min().strftime("%Y%m"), "end": rm.max().strftime("%Y%m")})
        rows.append(b)
    return pd.DataFrame(rows)


def by_regime(s: pd.DataFrame, macro: pd.DataFrame, regime: pd.Series) -> pd.DataFrame:
    ok = s["gross"].notna() & s["netfee"].notna()
    s, reg = s[ok], regime.reindex(s.index[ok])
    out = pd.DataFrame({k: period_block(g, macro) for k, g in s.groupby(reg)}).T
    out["cycles"] = cycles(s, macro, regime).groupby("regime").size()
    return out.rename_axis("regime")


def load_macro(conn) -> pd.DataFrame:
    """CPI (ES, EU, average), M2 and M3 (euro area) as fractions by month, from series_macro, no lag. M2 / M3 end one month before the CPI."""
    rows = conn.execute("SELECT date, indicator, geography, value FROM series_macro WHERE (indicator = 'ipc_index' AND geography IN ('ES','EU')) OR (indicator = 'm3_yoy' AND geography = 'EU') "
                        "OR (indicator = 'm2_yoy' AND geography = 'EU') ORDER BY date").fetchall()
    df = pd.DataFrame(rows, columns=["date", "indicator", "geography", "value"])
    df["date"] = pd.to_datetime(df["date"]) + pd.offsets.MonthEnd(0)
    df["value"] = df["value"].astype(float)
    df["key"] = df["indicator"] + "_" + df["geography"]
    wide = df.pivot_table(index="date", columns="key", values="value", aggfunc="last")
    y = cpi_yoy(wide[["ipc_index_ES", "ipc_index_EU"]].rename(columns={"ipc_index_ES": "ES", "ipc_index_EU": "EU"}))
    y["M2"] = wide["m2_yoy_EU"] / 100.0
    y["M3"] = wide["m3_yoy_EU"] / 100.0
    return y


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Baseline vs inflation and money growth tables (read-only).")
    p.add_argument("--series", required=True, help="series.csv of the baseline arm (gross, netfee by formation date)")
    p.add_argument("--run-dir", required=True, help="the sealed PIT run (its monthly table carries the regime of each formation date)")
    p.add_argument("--out-root", default=str(_ROOT / "out" / "reports" / "p3_cpi_tables"))
    args = p.parse_args(argv)
    from shared.db import get_connection
    conn = get_connection()
    try:
        macro = load_macro(conn)
    finally:
        conn.close()
    s = pd.read_csv(args.series, index_col=0, parse_dates=True)[["gross", "netfee"]]
    regime = pd.read_csv(Path(args.run_dir) / "pit_backtest_table.csv", parse_dates=["date"]).set_index("date")["regime"]
    s = s[s["netfee"].notna()]
    out = Path(args.out_root) / datetime.now().strftime("%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    glob = pd.Series(period_block(s, macro))
    glob.to_csv(out / "global.csv", header=["value"])
    yearly(s, macro).to_csv(out / "yearly.csv")
    cyc = cycles(s, macro, regime)
    cyc.to_csv(out / "cycles.csv", index=False)
    by_regime(s, macro, regime).to_csv(out / "by_regime.csv")
    pd.set_option("display.width", 260)
    print(f"[tables] written to {out}")
    print(glob.round(2).to_string())
    print(yearly(s, macro).round(1).to_string())
    print(cyc[["regime", "start", "end", "months", "Gross", "Fees", "Net", "CPI_ES", "CPI_AVG", "M2", "M3"]].round(1).to_string(index=False))
    print(by_regime(s, macro, regime).round(1).to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
