#!/usr/bin/env python
"""scripts/audit/eur_view_rehearsal.py — FND-0243 step 6: what the EUR view changes, on a SAMPLE, before the release.

Read-only. For each ISIN it loads the monthly NAV twice through the production reader (db_readers.load_nav) -- once with
EUR_NAV_CONVERSION_ENABLED off (the class-currency NAV P2 uses today) and once on (the EUR view) -- and computes the risk
metrics of both with the production functions (compute_risk_metrics, same ES CPI and the same risk-free rate on both sides),
so every difference is the currency conversion alone. Nothing is written to the database; the switch is flipped only
inside this process.

Sample (owner HARD RULE: never the full population): --isin A,B,... or --sample, which picks 40 ISINs deterministically
(md5 order) weighted to what the conversion touches: non-EUR classes of every currency, the funds whose Fund_Currency the
next P1 pass corrects (the FND-0243 step-2 list must be passed with --isin), funds with unknown class currency (excluded)
and EUR funds as the control group (their metrics must not move).

Prerequisite: the ECB rates in series_macro (`python -X utf8 -m proyecto2.src.discovery.macro_discovery --source bce`,
owner-run). Without them every non-EUR fund is excluded and the script says so and exits 3.

    C:\\data\\envs\\des\\python.exe -X utf8 scripts\\audit\\eur_view_rehearsal.py --sample [--csv out.csv]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_ROOT), str(_ROOT / "proyecto2")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pandas as pd

from shared import config
from shared import eur_nav
from shared.db import get_connection
from src.readers.db_readers import load_ipc, load_nav
from src.calculations.risk_metrics import compute_risk_metrics

SAMPLE_SIZE = 40
METRICS = ("return_ann", "vol_ann", "max_dd", "sharpe", "sortino")
RC_OK, RC_NO_RATES = 0, 3

# Deterministic sample, weighted to what the conversion touches. Literal SQL (EXPLAIN sweep).
Q_SAMPLE = """
    WITH a AS (
        SELECT fm.isin, upper(fm.fund_currency) AS ccy, md5(fm.isin) AS h
        FROM fund_master fm
        WHERE fm.in_current_universe = 1 AND EXISTS (SELECT 1 FROM fund_nav_monthly n WHERE n.isin = fm.isin)
    ), r AS (
        SELECT isin, ccy, row_number() OVER (PARTITION BY COALESCE(ccy, 'NULL') ORDER BY h) AS rn FROM a
    )
    SELECT isin, COALESCE(ccy, 'NULL') FROM r
    WHERE (ccy = 'USD' AND rn <= 14) OR (ccy = 'GBP' AND rn <= 5) OR (ccy = 'JPY' AND rn <= 3) OR (ccy = 'CHF' AND rn <= 3)
       OR (ccy IS NULL AND rn <= 5) OR (ccy = 'EUR' AND rn <= 10)
    ORDER BY 2, 1
"""
Q_RATES = "SELECT COUNT(*) FROM series_macro WHERE indicator = %s"


def _metrics(nav_df: pd.DataFrame, ipc_df: pd.DataFrame) -> dict:
    if nav_df.empty:
        return {}
    out = compute_risk_metrics(nav_df, ipc_df)
    out = out[out["metric"].isin(METRICS)]
    return {(r.metric, int(r.real_flag)): float(r.value) for r in out.itertuples() if pd.notna(r.value)}


def compare(conn, isins: list[str], ccy_of: dict) -> pd.DataFrame:
    """One row per (isin, metric, real_flag): value off, value on, difference. Pure apart from the reads."""
    ipc = load_ipc(conn)
    rows = []
    shipped = config.EUR_NAV_CONVERSION_ENABLED
    try:                                   # the switch is flipped in THIS process only, and always put back
        for isin in isins:
            config.EUR_NAV_CONVERSION_ENABLED = False
            off = _metrics(load_nav(conn, isin), ipc)
            config.EUR_NAV_CONVERSION_ENABLED = True
            nav_on = load_nav(conn, isin)
            status = nav_on.attrs.get(eur_nav.ATTR)
            on = _metrics(nav_on, ipc)
            for key in sorted(set(off) | set(on)):
                v0, v1 = off.get(key), on.get(key)
                rows.append({"isin": isin, "ccy": ccy_of.get(isin, "?"), "status": status, "metric": key[0],
                             "real_flag": key[1], "off": v0, "on": v1, "diff": None if v0 is None or v1 is None else v1 - v0})
    finally:
        config.EUR_NAV_CONVERSION_ENABLED = shipped
    return pd.DataFrame(rows)


def summarize(df: pd.DataFrame) -> str:
    if df.empty:
        return "no rows"
    L = []
    st = df.drop_duplicates("isin").groupby(["ccy", "status"]).size()
    L.append("funds by class currency / EUR-view status:")
    L += [f"    {c:<5} {s:<18} {n}" for (c, s), n in st.items()]
    eur = df[(df["ccy"] == "EUR") & df["diff"].notna()]
    L.append(f"EUR control group: max |diff| = {eur['diff'].abs().max() if not eur.empty else 0:.3g} (must be 0)")
    nz = df[(df["ccy"] != "EUR") & df["diff"].notna()]
    if not nz.empty:
        L.append("non-EUR classes, median / max |diff| per metric (nominal):")
        g = nz[nz["real_flag"] == 0].groupby("metric")["diff"]
        for m in METRICS:
            if m in g.groups:
                s = g.get_group(m).abs()
                L.append(f"    {m:<11} median {s.median():.4f}   max {s.max():.4f}")
        top = nz[(nz["metric"] == "sharpe") & (nz["real_flag"] == 0)].reindex(
            nz[(nz["metric"] == "sharpe") & (nz["real_flag"] == 0)]["diff"].abs().sort_values(ascending=False).index).head(5)
        L.append("largest Sharpe moves:")
        L += [f"    {r.isin} {r.ccy}  {r.off:.2f} -> {r.on:.2f}" for r in top.itertuples()]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--isin", help="comma-separated ISINs (max %d)" % SAMPLE_SIZE)
    g.add_argument("--sample", action="store_true", help="the deterministic %d-ISIN sample" % SAMPLE_SIZE)
    ap.add_argument("--csv", help="write the per-fund comparison here")
    args = ap.parse_args(argv)

    conn = get_connection()
    try:
        if not conn.execute(Q_RATES, (config.EUR_FX_DAILY_INDICATOR,)).fetchone()[0]:
            print("[STOP] no ECB daily rates (series_macro fx_d_eur): run `macro_discovery --source bce` first (owner).")
            return RC_NO_RATES
        sample = conn.execute(Q_SAMPLE).fetchall()
        ccy_of = {r[0]: r[1] for r in sample}
        if args.sample:
            isins = [r[0] for r in sample]
        else:
            isins = [i.strip() for i in args.isin.split(",") if i.strip()]
            if len(isins) > SAMPLE_SIZE:
                ap.error(f"at most {SAMPLE_SIZE} ISINs (sample rule)")
            for isin, ccy in conn.execute("SELECT isin, COALESCE(upper(fund_currency), 'NULL') FROM fund_master WHERE isin = ANY(%s)",
                                          (isins,)).fetchall():
                ccy_of[isin] = ccy
        df = compare(conn, isins, ccy_of)
    finally:
        conn.rollback()
        conn.close()
    print(summarize(df))
    if args.csv:
        df.to_csv(args.csv, index=False)
        print(f"\nper-fund comparison: {args.csv}")
    return RC_OK


if __name__ == "__main__":
    sys.exit(main())
