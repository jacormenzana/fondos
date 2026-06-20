# proyecto1/scripts/diag/diag_cost_extraction.py
# -*- coding: utf-8 -*-
"""
diag_cost_extraction.py — corpus diagnostic harness for the cost-extraction path.

PURPOSE
-------
Quantify, at corpus scale (~3,205 funds), the residual cost-extraction defects
R1..R4 identified on FR0010664052 / LU0326422689 / FR0010760694, and provide a
repeatable gate to validate any serializer/parser fix BEFORE corpus deploy
(no "100% solved" on small samples).

GROUND TRUTH
------------
The DLA2 dual-extractor arbitration columns in fund_kiid_metadata
(Cost_Mgmt_BandsX / Cost_Oper_BandsX, where Cost_*_Arbitration='AGREE') are the
trustworthy reference: they read the PDF via the column-preserving grid and were
validated correct. This harness re-runs the VALUES path (serializer grid -> parser)
the way io.py L557 feeds it (Raw_KIID_Text + '\\n' + grid) and compares the result
against (a) arbitration truth and (b) the currently-stored fund_master values.

WHAT IT MEASURES (per fund, written to CSV)
-------------------------------------------
  - has_grid            : serializer produced '|||' rows for this PDF
  - mgmt_truth/oper_truth: arbitration BandsX (when Arbitration='AGREE')
  - mgmt_regrid/oper_regrid : what the current grid+parser path WOULD extract
  - mgmt_stored/oper_stored : what is currently in fund_master
  - swap_mgmt_oper     : stored mgmt/oper mismatch vs truth (the R-class swaps)
  - aci1_regrid/acirhp_regrid vs stored : ACI recovery (R1/R2)
  - oper_bleed         : regrid oper differs from truth by > tol (R3)
  - perf_missing       : truth has no perf but a perf value is present, or vice-versa
  - quality_stored     : Cost_Extraction_Quality in DB (LOW / MEDIUM_EUR inflation)
  - verdict            : OK | STALE_SWAP | REGRID_FIXES | RESIDUAL_<R>

EXECUTION (Windows, conda env `des`)
------------------------------------
  cd C:\\desarrollo\\fondos
  set PYTHONPATH=proyecto1;proyecto1\\core;shared
  python -X utf8 proyecto1\\scripts\\diag\\diag_cost_extraction.py ^
      --db C:\\desarrollo\\fondos\\db\\fondos.sqlite ^
      --kiid-dir C:\\data\\fondos\\kiid ^
      --out C:\\desarrollo\\fondos\\out\\diag\\cost_diag_YYYYMMDD.csv

  Optional: --limit N (smoke test), --only-priips, --isins FR..,LU.. (subset).

NO side effects on the DB (read-only). Single connection. python -X utf8 safe.
"""

import argparse
import csv
import math
import os
import sqlite3
import sys
from datetime import date
from io import BytesIO
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Module imports — defensive (proyecto1/core layout). Project must put
# proyecto1 and proyecto1/core on PYTHONPATH (see header).
# ---------------------------------------------------------------------------
def _import_modules():
    errs = []
    mods = {}
    for name in ("dla_table_serializer", "cost_table_parser"):
        m = None
        for prefix in ("", "core."):
            try:
                m = __import__(prefix + name, fromlist=["*"])
                break
            except Exception as e:  # noqa
                errs.append(f"{prefix}{name}: {e}")
        mods[name] = m
    return mods, errs


# ---------------------------------------------------------------------------
# Comparison tolerance — mirror cost arbitration hybrid isclose (% scale).
# ---------------------------------------------------------------------------
_ABS_TOL_PCT = 0.02   # 0.02 percentage points
_REL_TOL     = 0.01


def _close(a: Optional[float], b: Optional[float]) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return math.isclose(a, b, abs_tol=_ABS_TOL_PCT, rel_tol=_REL_TOL)


def _r2p(x: Optional[float]) -> Optional[float]:
    """Parser output is ratio (0.0249); convert to percent. None-safe."""
    return None if x is None else round(x * 100.0, 4)


# ---------------------------------------------------------------------------
# Core per-fund evaluation
# ---------------------------------------------------------------------------
def evaluate_fund(row: dict, kiid_dir: Path, mods: dict) -> dict:
    """row carries the SELECT columns; returns a flat diagnostic dict."""
    isin = row["ISIN"]
    serialize_tables = getattr(mods["dla_table_serializer"], "serialize_tables")
    parse_comp       = getattr(mods["cost_table_parser"], "parse_costs_composition")
    parse_ot         = getattr(mods["cost_table_parser"], "parse_costs_over_time")
    SEP              = getattr(mods["cost_table_parser"], "DLA2_SEPARATOR", "|||")

    out = {
        "ISIN": isin,
        "KID_Format": row.get("KID_Format"),
        "quality_stored": row.get("Cost_Extraction_Quality"),
        "has_pdf": False, "has_grid": False,
        "mgmt_truth": None, "oper_truth": None,
        "mgmt_arb_verdict": row.get("Cost_Mgmt_Arbitration"),
        "oper_arb_verdict": row.get("Cost_Oper_Arbitration"),
        "mgmt_regrid": None, "oper_regrid": None,
        "perf_regrid": None,
        "aci1_regrid": None, "acirhp_regrid": None,
        "mgmt_stored": row.get("Management_Fee_Pct"),
        "oper_stored": row.get("Transaction_Cost_Pct"),
        "perf_stored": row.get("Performance_Fee_Pct"),
        "aci1_stored": row.get("ACI_1Y"),
        "acirhp_stored": row.get("ACI_RHP"),
        "swap_mgmt_oper": "", "oper_bleed": "", "aci_recovered": "",
        "verdict": "",
    }

    # Ground truth from arbitration (only when AGREE — trustworthy)
    if row.get("Cost_Mgmt_Arbitration") == "AGREE":
        out["mgmt_truth"] = row.get("Cost_Mgmt_BandsX")
    if row.get("Cost_Oper_Arbitration") == "AGREE":
        out["oper_truth"] = row.get("Cost_Oper_BandsX")

    pdf_path = kiid_dir / f"{isin}.pdf"
    raw = row.get("Raw_KIID_Text") or ""
    if not pdf_path.exists():
        out["verdict"] = "NO_LOCAL_PDF"
        return out
    out["has_pdf"] = True

    try:
        pdf_bytes = pdf_path.read_bytes()
        grid, _meta = serialize_tables(pdf_bytes, text=raw)
    except Exception as e:  # noqa
        out["verdict"] = f"SERIALIZE_ERR:{e}"
        return out

    fed = (raw + "\n" + grid) if grid else raw
    out["has_grid"] = SEP in fed

    try:
        comp = parse_comp(fed)
        ot = parse_ot(fed)
    except Exception as e:  # noqa
        out["verdict"] = f"PARSE_ERR:{e}"
        return out

    out["mgmt_regrid"] = _r2p(comp.get("management_fee_pct"))
    out["oper_regrid"] = _r2p(comp.get("transaction_cost_pct"))
    out["perf_regrid"] = _r2p(comp.get("performance_fee_pct"))
    # ACI: pick 1Y and RHP rows
    for e in ot:
        hy = e.get("horizon_years")
        if hy is not None and abs(hy - 1.0) <= 0.01:
            out["aci1_regrid"] = _r2p(e.get("aci_pct"))
        if e.get("is_rhp") or (hy is not None and hy > 1.0):
            out["acirhp_regrid"] = _r2p(e.get("aci_pct"))
    # FIX-HARNESS-2: any single-column OT table is, by PRIIPS construction,
    # reporting the RHP horizon -- regardless of its specific value (1Y, 3M,
    # whatever the fund's recommended holding period is). Production already
    # resolves this correctly via priips_cost_extractor._pick_aci_for_horizon's
    # rhp_years-prose fallback (abs(hy - rhp_years) <= 0.01; _RHP_VALUE_PATTERN
    # explicitly converts "X meses" -> years). The harness's simplified
    # is_rhp/hy>1.0 check doesn't mirror that, so it under-reports ACI_RHP
    # recovery for short-RHP funds (e.g. 3-month money-market-style products)
    # purely as a MEASUREMENT gap, not a production defect. Supersedes the
    # narrower FIX-HARNESS (hy≈1.0-only) below.
    if len(ot) == 1 and out["acirhp_regrid"] is None:
        out["acirhp_regrid"] = _r2p(ot[0].get("aci_pct"))
    # FIX-HARNESS: RHP=1Y single-column OT tables emit one entry with hy=1.0,
    # is_rhp=False. Production _pick_aci_for_horizon maps it to ACI_RHP via
    # target_years fallback (abs(hy - rhp_years) <= 0.01). Mirror that here:
    # if no entry with is_rhp=True or hy>1.0 exists, the 1Y ACI IS the RHP ACI.
    if out["acirhp_regrid"] is None and out["aci1_regrid"] is not None:
        _has_longer = any(e.get("is_rhp") or (e.get("horizon_years") or 0) > 1.0
                          for e in ot)
        if not _has_longer:
            out["acirhp_regrid"] = out["aci1_regrid"]

    # ---- classifications ----
    # swap: stored mgmt/oper disagree with truth, but truth exists
    if out["mgmt_truth"] is not None or out["oper_truth"] is not None:
        sm = (out["mgmt_truth"] is not None
              and not _close(out["mgmt_stored"], out["mgmt_truth"]))
        so = (out["oper_truth"] is not None
              and not _close(out["oper_stored"], out["oper_truth"]))
        out["swap_mgmt_oper"] = "Y" if (sm or so) else ""

        # does regrid fix it vs truth?
        rm = (out["mgmt_truth"] is None or _close(out["mgmt_regrid"], out["mgmt_truth"]))
        ro = (out["oper_truth"] is None or _close(out["oper_regrid"], out["oper_truth"]))
        regrid_ok = rm and ro

        # oper bleed: regrid oper present but wrong vs truth (R3)
        if (out["oper_truth"] is not None and out["oper_regrid"] is not None
                and not _close(out["oper_regrid"], out["oper_truth"])):
            out["oper_bleed"] = "Y"

        if not (sm or so):
            out["verdict"] = "OK"
        elif regrid_ok:
            out["verdict"] = "REGRID_FIXES"        # re-run FORCE_REFRESH resolves
        elif out["oper_bleed"] == "Y":
            out["verdict"] = "RESIDUAL_R3"          # serializer oper bleed
        else:
            out["verdict"] = "RESIDUAL_OTHER"
    else:
        out["verdict"] = "NO_TRUTH"                 # arbitration not AGREE -> can't judge

    # ACI recovery flag (R1/R2): regrid yields ACI the stored value lacks
    if (out["acirhp_stored"] is None and out["acirhp_regrid"] is not None):
        out["aci_recovered"] = "RHP"
    if (out["aci1_stored"] is None and out["aci1_regrid"] is not None):
        out["aci_recovered"] = (out["aci_recovered"] + "+1Y").strip("+")
    # ACI still missing after regrid -> residual R1/R2
    if (out["acirhp_regrid"] is None and out["mgmt_truth"] is not None):
        out["aci_recovered"] = (out["aci_recovered"] + "|RHP_STILL_MISSING").strip("|")

    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
SELECT_SQL = """
SELECT m.ISIN, m.KID_Format, m.Cost_Extraction_Quality,
       m.Management_Fee_Pct, m.Transaction_Cost_Pct, m.Performance_Fee_Pct,
       m.ACI_1Y, m.ACI_RHP,
       k.Raw_KIID_Text,
       k.Cost_Mgmt_BandsX, k.Cost_Mgmt_Arbitration,
       k.Cost_Oper_BandsX, k.Cost_Oper_Arbitration
FROM fund_master m
LEFT JOIN fund_kiid_metadata k ON k.ISIN = m.ISIN
{where}
ORDER BY m.ISIN
{limit}
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="Cost-extraction corpus diagnostic")
    ap.add_argument("--db", required=True)
    ap.add_argument("--kiid-dir", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only-priips", action="store_true")
    ap.add_argument("--isins", default=None, help="comma-separated subset")
    args = ap.parse_args()

    mods, errs = _import_modules()
    missing = [n for n, m in mods.items() if m is None]
    if missing:
        print("FATAL: modules not importable:", missing)
        for e in errs:
            print("  ", e)
        print("Set PYTHONPATH=proyecto1;proyecto1\\core;shared")
        return 2

    db = Path(args.db)
    kiid_dir = Path(args.kiid_dir)
    if not db.exists():
        print("FATAL: DB not found:", db); return 2
    if not kiid_dir.exists():
        print("FATAL: kiid dir not found:", kiid_dir); return 2

    where = []
    if args.only_priips:
        where.append("m.KID_Format = 'PRIIPS_KID'")
    if args.isins:
        ins = ",".join(f"'{x.strip()}'" for x in args.isins.split(",") if x.strip())
        where.append(f"m.ISIN IN ({ins})")
    where_clause = ("WHERE " + " AND ".join(where)) if where else ""
    limit_clause = f"LIMIT {args.limit}" if args.limit > 0 else ""
    sql = SELECT_SQL.format(where=where_clause, limit=limit_clause)

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute(sql).fetchall()]
    finally:
        conn.close()

    print(f"Funds to evaluate: {len(rows)}")
    results = []
    for i, r in enumerate(rows, 1):
        results.append(evaluate_fund(r, kiid_dir, mods))
        if i % 200 == 0:
            print(f"  ...{i}/{len(rows)}")

    # summary
    from collections import Counter
    verdicts = Counter(x["verdict"] for x in results)
    swaps    = sum(1 for x in results if x["swap_mgmt_oper"] == "Y")
    bleeds   = sum(1 for x in results if x["oper_bleed"] == "Y")
    rhp_miss = sum(1 for x in results if "RHP_STILL_MISSING" in (x["aci_recovered"] or ""))
    print("\n==== SUMMARY ====")
    print("verdicts:", dict(verdicts))
    print(f"swap_mgmt_oper (stored wrong vs truth): {swaps}")
    print(f"oper_bleed (R3, regrid oper wrong):     {bleeds}")
    print(f"ACI_RHP still missing after regrid (R1/R2): {rhp_miss}")

    out_path = args.out or str(
        Path("out/diag") / f"cost_diag_{date.today():%Y%m%d}.csv")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    cols = list(results[0].keys()) if results else []
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(results)
    print(f"\nCSV: {out_path}  ({len(results)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
