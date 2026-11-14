#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
sovereign_stress_backtest.py -- would simple, pre-declared sovereign-stress rules have flagged the known episodes? (FND-0294)

    python scripts/audit/sovereign_stress_backtest.py            # per-rule table on the euro era + the state of the latest month
    python scripts/audit/sovereign_stress_backtest.py --json     # machine-readable

Read-only research: public data only (ECB IRS 10y government yields for DE FR IT ES PT GR; FRED/OECD IRLTLT01 for US GB JP, through the repo's keyed
FRED fetch), nothing is written anywhere. The rules, thresholds and episodes live in shared/sovereign_stress.py (thresholds are pinned by a test:
tuning one is a deliberate edit, never a side effect). MONITOR-ONLY evidence: no portfolio weight may depend on it before FND-0193.
Exit codes: 0 ok, 2 data could not be fetched.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from shared import sovereign_stress as ss  # noqa: E402

EURO_COUNTRIES = ("DE", "FR", "IT", "ES", "PT", "GR")
FRED_COUNTRIES = ("US", "GB", "JP")


def fetch_ecb(country: str) -> dict:
    """{'YYYY-MM': 10y government yield %} from the ECB IRS convergence-criterion series."""
    url = f"https://data-api.ecb.europa.eu/service/data/IRS/M.{country}.L.L40.CI.0000.EUR.N.Z?format=csvdata"
    text = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=60).read().decode()
    return {r["TIME_PERIOD"]: float(r["OBS_VALUE"]) for r in csv.DictReader(io.StringIO(text)) if r["OBS_VALUE"]}


def fetch_fred(country: str) -> dict:
    """{'YYYY-MM': 10y yield %} from FRED (OECD long-term interest rate, monthly) with the repo's keyed fetch."""
    import shared.config  # noqa: F401  (autoloads the repo .env: FRED_API_KEY)
    from proyecto2.src.discovery import macro_discovery as md
    df = md._fred_fetch(f"IRLTLT01{country}M156N", "1990-01", os.environ.get("FRED_API_KEY") or None)
    return {d.strftime("%Y-%m"): float(v) for d, v in zip(df["date"], df["value"])}


def render(result: dict) -> str:
    months = result["months"]
    out = [f"euro era: {len(months)} months ({months[0]}..{months[-1]}); episodes labelled from history, windows +-3 months",
           f"{'rule':5} {'kind':6} " + " ".join(f"{n[:9]:>9}" for n in ss.EPISODES) + "  detected  false-alarm months   now"]
    for rid, r in result["rules"].items():
        sc = r["score"]
        out.append(f"{rid:5} {r['kind']:6} " + " ".join(f"{v:9d}" for v in sc["per_episode"].values())
                   + f"  {sc['detected']}/{sc['episodes']}      {len(sc['false_months']):4d} ({sc['false_pct']:.0f}%)        {'ON' if r['now'] else 'off'}")
    out += ["", *(f"{rid}: {r['label']}" for rid, r in result["rules"].items())]
    return "\n".join(out)


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--json", action="store_true", help="print JSON instead of the table")
    args = ap.parse_args(argv)
    try:
        y = {c: fetch_ecb(c) for c in EURO_COUNTRIES}
        y.update({c: fetch_fred(c) for c in FRED_COUNTRIES})
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] cannot fetch the yield series: {type(exc).__name__}: {exc}")
        return 2
    result = ss.evaluate(y)
    if not result["months"]:
        print("[ERROR] no month has every needed series")
        return 2
    print(json.dumps(result, indent=1) if args.json else render(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
