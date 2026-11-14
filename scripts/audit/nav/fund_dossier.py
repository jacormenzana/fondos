# -*- coding: utf-8 -*-
"""One-page dossier of a fund the statistical audit flags as extreme, for the human who has to decide (FND-0393, WS1; owner order 2026-11-08: "a specific report for the 3 funds that require manual review").

    python scripts/audit/nav/fund_dossier.py LU1663960000 ES0116848013 LU0922333322 [--min-z 20] [--out-dir DIR]

READ-ONLY (a read-only session). Generic: no fund is named in code (P#5). For every fund it shows what the reviewer needs and nothing the reviewer must go and find:
  1. identity        name, nature, family and its classes, currency, manager, universe flag, SRRI, NAV status;
  2. the NAV         rows, span, volatility, the three largest one-month moves against their peers, the longest flat run, the largest step ratio, and what the three NAV validators say;
  3. the anomalies   every stored metric of the fund with |z| >= --min-z against the universe: value, the universe median, z, z inside its nature, `source_rows`, and a FLAG that says why it may not be a real
                     signal: STALE_REGIME (the fund has fewer than REGIME_MIN_OBS months in that regime, so the row should not exist: FND-0401), SMALL_SLOPE (a slope on fewer than ROLLING_SLOPE_MIN_OBS points),
                     SMALL_SAMPLE (below its nature's p10 AND below half its median), or ADEQUATE (none of those: the anomaly stands on its own);
  4. a drill-down    for a ratio metric over a crisis window (`crisis_YYYY`): the monthly returns of the window, the number of negative months and the downside deviation, i.e. the denominator of the ratio;
  5. a reading       one line per finding, derived from the numbers above and stated as an inference, never as a verdict.
Output: Markdown (and JSON) under out/audit/P2/ unless --out-dir says otherwise.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[3]
for _p in (_ROOT, _ROOT / "proyecto2"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from shared import config as _cfg                                                          # noqa: E402
from shared.data_suspect import robust_z                                                   # noqa: E402
from shared.db import get_connection                                                       # noqa: E402
from shared.family_scope import load_family_map                                            # noqa: E402
from src.calculations.regime_returns import _REGIME_SUFFIX                                 # noqa: E402
from src.readers.db_readers import load_nav, load_peer_returns                             # noqa: E402
from src.utils.peer_returns import one_month_returns                                       # noqa: E402
from src.utils.validators import find_unshared_jumps, validate_nav, validate_nav_stale_flat   # noqa: E402

REGIME_STATS = ("return_ann", "vol_ann", "sharpe", "sortino", "max_dd")
STAT_SUFFIX = {f"{b}_{s}": s for s in _REGIME_SUFFIX.values() for b in REGIME_STATS}
STAT_SUFFIX.update({"crisis_stress_score_mdd": "crisis_financiera", "crisis_stress_score_ttr": "crisis_financiera"})


def flag_row(metric: str, horizon: str, real_flag: int, source_rows, nobs: dict, p10, p50) -> str:
    """Why a stored metric may not be a real signal. Pure. Order matters: the stale-row and slope floors are rules of the pipeline, the sample test is statistical."""
    if real_flag == 0 and horizon == "since_inception" and metric in STAT_SUFFIX:
        n = nobs.get("n_obs_" + STAT_SUFFIX[metric])
        if n is not None and n < _cfg.REGIME_MIN_OBS:
            return f"STALE_REGIME (only {int(n)} months in that regime, floor {_cfg.REGIME_MIN_OBS})"
    if metric.endswith("_slope") and source_rows is not None and source_rows < _cfg.ROLLING_SLOPE_MIN_OBS:
        return f"SMALL_SLOPE ({int(source_rows)} points, floor {_cfg.ROLLING_SLOPE_MIN_OBS})"
    if source_rows is not None and p10 is not None and p50 is not None and source_rows < p10 and source_rows < 0.5 * p50:
        return f"SMALL_SAMPLE ({int(source_rows)} rows, nature p10 {int(p10)}, median {int(p50)})"
    return "ADEQUATE"


def crisis_window(horizon: str):
    """(start, end) of a `crisis_*` horizon from shared.config.CRISIS_WINDOWS, or None."""
    win = _cfg.CRISIS_WINDOWS.get(horizon)
    return None if win is None else (pd.Timestamp(win[0]), pd.Timestamp(win[1]))


def calendar_returns(nav: pd.DataFrame) -> dict:
    """{year: return} of the last NAV of each calendar year against the last NAV of the year before (the first year it has no prior year, so it is left out). Pure."""
    s = nav.set_index("date")["nav"].astype(float)
    last = s.groupby(s.index.year).last()
    return {int(y): round(float(last[y] / last[y - 1] - 1.0), 4) for y in last.index if (y - 1) in last.index}


def downside_drilldown(nav: pd.DataFrame, start, end, min_negative=None) -> dict:
    """The monthly returns of a window and the denominator of a Sortino: how many months were negative and the downside deviation of the excess return over the risk-free rate. Pure."""
    rets = one_month_returns(nav).dropna()
    w = rets[(rets.index >= start) & (rets.index <= end)]
    rf_m = _cfg.RISK_FREE_RATE_ANN / 12.0
    excess = w - rf_m
    neg = excess[excess < 0]
    dd = float(np.sqrt((neg ** 2).sum() / len(excess))) if len(excess) else float("nan")
    return {"n_months": int(len(w)), "n_below_rf": int(len(neg)), "downside_dev_monthly": None if np.isnan(dd) else round(dd, 5),
            "mean_excess_monthly": None if w.empty else round(float(excess.mean()), 5), "returns": [round(float(x), 4) for x in w.tolist()],
            "months": [str(i)[:7] for i in w.index]}


def dossier(conn, peers, family_of: dict, isin: str, min_z: float) -> dict:
    m = conn.execute("SELECT fund_name, fund_nature, in_current_universe, management_company, fund_currency, srri, fund_family_id FROM silver.fund_master WHERE isin = %s", (isin,)).fetchone()
    name, nature, iu, mgr, ccy, srri, fam = (m if m else (None,) * 7)
    st = conn.execute("SELECT data_status, last_nav_date FROM control.nav_sources WHERE isin = %s", (isin,)).fetchone()
    members = [r[0] for r in conn.execute("SELECT isin FROM silver.fund_master WHERE fund_family_id = %s ORDER BY isin", (fam,)).fetchall()] if fam else [isin]
    nav = load_nav(conn, isin)
    rec = {"isin": isin, "name": name, "nature": nature, "in_universe": iu, "manager": mgr, "currency": ccy, "srri": srri, "family": fam, "family_members": members,
           "nav_status": st[0] if st else None, "last_nav": str(st[1]) if st else None, "n_rows": int(len(nav))}
    if len(nav) >= 2:
        rets = one_month_returns(nav).dropna()
        vals = nav["nav"].to_numpy(dtype=float)
        rec["nav"] = {"span": f"{str(nav['date'].iloc[0])[:7]}..{str(nav['date'].iloc[-1])[:7]}", "vol_ann": round(float(rets.std(ddof=1) * np.sqrt(12)), 4) if len(rets) > 2 else None,
                      "total_return": round(float(vals[-1] / vals[0] - 1), 4), "max_drawdown": round(float((vals / np.maximum.accumulate(vals) - 1).min()), 4)}
        top = rets.reindex(rets.abs().sort_values(ascending=False).index)[:3]
        rec["nav"]["largest_moves"] = []
        for month, r in top.items():
            arr = peers.peers_at(isin, month)
            rec["nav"]["largest_moves"].append({"month": str(month)[:7], "ret": round(float(r), 4), "peer_median": None if not len(arr) else round(float(np.median(arr)), 4), "n_peers": int(len(arr))})
        rec["nav"]["calendar_returns"] = calendar_returns(nav)
        jumps = find_unshared_jumps(nav, peers, isin)
        rec["validators"] = {"validate_nav": validate_nav(nav)[0], "validate_nav_stale_flat": validate_nav_stale_flat(nav, enabled=True)[0], "validate_nav_unshared_jump": not jumps}
    rows = conn.execute("SELECT m.metric, m.horizon, m.real_flag, m.metric_version, m.value, m.source_rows FROM fund_metrics m WHERE m.isin = %s AND m.value IS NOT NULL", (isin,)).fetchall()
    nobs = {r[0]: r[4] for r in rows if r[0].startswith("n_obs_") and r[1] == "since_inception" and r[2] == 0}
    anomalies = []
    for metric, horizon, rf, ver, value, sr in rows:
        pop = conn.execute("SELECT f.fund_nature, m.value, m.source_rows FROM fund_metrics m JOIN silver.fund_master f ON f.isin = m.isin WHERE f.in_current_universe = 1 AND m.metric = %s AND m.horizon = %s "
                           "AND m.real_flag = %s AND m.metric_version = %s AND m.value IS NOT NULL", (metric, horizon, rf, ver)).fetchall()
        z = robust_z(value, [p[1] for p in pop])
        if z is None or abs(z) < min_z:
            continue
        in_nat = [p for p in pop if p[0] == nature]
        z_nat = robust_z(value, [p[1] for p in in_nat])
        srs = [p[2] for p in in_nat if p[2] is not None]
        p10 = float(np.percentile(srs, 10)) if len(srs) >= 5 else None
        p50 = float(np.median(srs)) if len(srs) >= 5 else None
        anomalies.append({"metric": metric, "horizon": horizon, "real_flag": int(rf), "value": round(float(value), 4), "universe_median": round(float(np.median([p[1] for p in pop])), 4),
                          "z": round(float(z), 1), "z_in_nature": None if z_nat is None else round(float(z_nat), 1), "source_rows": None if sr is None else int(sr),
                          "flag": flag_row(metric, horizon, int(rf), sr, nobs, p10, p50)})
    rec["anomalies"] = sorted(anomalies, key=lambda a: -abs(a["z"]))
    rec["drilldown"] = {}
    for a in rec["anomalies"][:6]:
        w = crisis_window(a["horizon"])
        if w and a["metric"].split("_")[0] in ("sortino", "sharpe") and len(nav) >= 2:
            rec["drilldown"][f"{a['metric']}|{a['horizon']}"] = downside_drilldown(nav, *w)
    rec["reading"] = reading(rec)
    return rec


def reading(rec: dict) -> list:
    """Inferences from the numbers above, worded as such. Pure."""
    out = []
    an = rec.get("anomalies", [])
    if not an:
        return ["No stored metric reaches the z threshold."]
    stale = [a for a in an if a["flag"].startswith(("STALE_REGIME", "SMALL_SLOPE"))]
    small = [a for a in an if a["flag"].startswith("SMALL_SAMPLE")]
    ok = [a for a in an if a["flag"] == "ADEQUATE"]
    if stale:
        out.append(f"{len(stale)} of {len(an)} anomalous rows should not exist under today's floors (stale regime or slope rows, FND-0401); they disappear when the rows are removed.")
    if small:
        out.append(f"{len(small)} rest on a sample well below the fund's nature (small-sample artifact, not corruption).")
    if ok:
        out.append(f"{len(ok)} anomalous metric(s) rest on an adequate sample and stand on their own: " + ", ".join(f"{a['metric']}|{a['horizon']} (z {a['z']})" for a in ok[:4]) + ".")
    big = {y: r for y, r in rec.get("nav", {}).get("calendar_returns", {}).items() if abs(r) >= 0.5}
    if big:
        out.append("Calendar-year return of " + ", ".join(f"{r:+.1%} in {y}" for y, r in sorted(big.items())) + " in the stored NAV: check it against the publisher's own annual figure (the KIID annex), because a level shift inside the 8x test and below the unshared-jump cut would look like this.")
    v = rec.get("validators", {})
    if v and all(v.values()):
        out.append("All three NAV validators pass: nothing in the NAV itself points to a data defect.")
    elif v:
        out.append("A NAV validator rejects the series: " + ", ".join(k for k, ok_ in v.items() if not ok_) + ".")
    for key, d in rec.get("drilldown", {}).items():
        if d["n_months"] and d["n_below_rf"] <= 3:
            out.append(f"{key}: the ratio's denominator rests on {d['n_below_rf']} month(s) below the risk-free rate out of {d['n_months']} (downside deviation {d['downside_dev_monthly']} a month): a ratio with so few downside months explodes by construction.")
    return out


def render(recs: list) -> str:
    L = ["# Fund dossiers for human review", "", "Read-only. Flags: STALE_REGIME / SMALL_SLOPE = the row should not exist under today's floors; SMALL_SAMPLE = tiny sample for its nature; ADEQUATE = stands on its own.", ""]
    for r in recs:
        L += [f"## {r['isin']} - {r['name']}", "", f"- nature **{r['nature']}**, manager {r['manager']}, currency {r['currency']}, SRRI {r['srri']}, in universe {r['in_universe']}, NAV status {r['nav_status']} (last {r['last_nav']})",
              f"- family `{r['family']}`: {', '.join(r['family_members'])}"]
        if "nav" in r:
            n = r["nav"]
            L.append(f"- NAV {r['n_rows']} rows {n['span']}, vol {n['vol_ann']}, total return {n['total_return']:+.1%}, max drawdown {n['max_drawdown']:.1%}; validators: " + ", ".join(f"{k.replace('validate_nav_', '').replace('validate_nav', 'step8x')}={'pass' if v else 'REJECT'}" for k, v in r["validators"].items()))
            L.append("- calendar-year returns: " + ", ".join(f"{y} {r:+.1%}" for y, r in n["calendar_returns"].items()))
            L.append("- largest one-month moves (fund | peer median of n): " + "; ".join(f"{m['ret']:+.1%} in {m['month']} | {m['peer_median']} of {m['n_peers']}" for m in n["largest_moves"]))
        L += ["", "| metric | horizon | real | value | universe median | z | z in nature | rows | flag |", "|---|---|---|---|---|---|---|---|---|"]
        for a in r["anomalies"][:14]:
            L.append(f"| {a['metric']} | {a['horizon']} | {a['real_flag']} | {a['value']} | {a['universe_median']} | {a['z']} | {a['z_in_nature']} | {a['source_rows']} | {a['flag']} |")
        if len(r["anomalies"]) > 14:
            L.append(f"| ... {len(r['anomalies']) - 14} more | | | | | | | | |")
        for key, d in r["drilldown"].items():
            L += ["", f"**{key}** over its window: {d['n_months']} months, {d['n_below_rf']} below the risk-free rate, downside deviation {d['downside_dev_monthly']}, mean excess {d['mean_excess_monthly']} a month; returns: " + ", ".join(f"{m} {x:+.1%}" for m, x in zip(d["months"], d["returns"]))]
        L += ["", "**Reading (inference):**"] + [f"- {x}" for x in r["reading"]] + [""]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("isins", nargs="+"); ap.add_argument("--min-z", type=float, default=20.0); ap.add_argument("--out-dir", default=str(_cfg.AUDIT_DIRS["p2"]))
    args = ap.parse_args(argv)
    conn = get_connection(); conn.read_only = True
    peers = load_peer_returns(conn); fam = load_family_map(conn, args.isins)
    recs = [dossier(conn, peers, fam, i, args.min_z) for i in args.isins]
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    (out / "fund_dossier.json").write_text(json.dumps(recs, indent=1, default=str), encoding="utf-8")
    text = render(recs); (out / "fund_dossier.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
