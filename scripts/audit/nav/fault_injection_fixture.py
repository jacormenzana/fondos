# -*- coding: utf-8 -*-
"""Build the fixture of the fault-injection harness from the live store, READ-ONLY (FND-0392, WS0.5 of FND-0391).

    python scripts/audit/nav/fault_injection_fixture.py --real-positive <ISIN> [--out tests/fixtures/fault_injection]

Writes `nav_sample.csv.gz` (isin, nature, role, date, nav) and `nav_sample.manifest.json`. Roles: `sample` = the 40-ISIN sample whose healthy series get corrupted; `real_positive` = funds whose
real series is the defect itself (counted in the 40: the sample takes the quota minus them); `peer` = the other funds of each Fund_Nature the unshared-jump test compares against.

Selection is deterministic and generic (P#5, no fund is named in code): in-universe funds with at least MIN_ROWS monthly NAV rows, ordered by md5(isin) inside their Fund_Nature; the first
`quota` become the sample, the next PEERS_PER_NATURE the peers. The same live data gives the same fixture. NAV is the stored class-currency NAV (the unshared-jump test compares one-month moves,
where FX is a few percent against a 50 % cut). The harness itself (scripts/audit/nav/fault_injection_run.py) never touches the database.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

MIN_ROWS = 48
MIN_ROWS_POSITIVE = 24          # a real positive is never corrupted: it only needs enough history to be judged
PEERS_PER_NATURE = 40
SAMPLE_SIZE = 40
# Quota per Fund_Nature (sums to SAMPLE_SIZE with the real positives taken from the Renta Variable quota); proportional to the universe, every nature present.
QUOTAS = {"Renta Variable": 11, "Renta Fija Flexible": 7, "Mixtos": 6, "Renta Fija Corto Plazo": 6, "Monetario": 3, "Alternativo": 4, "Estructurado": 3}
assert sum(QUOTAS.values()) == SAMPLE_SIZE


def select(rows: list, real_positives: list) -> dict:
    """rows = [(isin, nature, md5, n_rows)] of the in-universe funds. Returns {isin: role}. A real positive needs MIN_ROWS_POSITIVE rows and uses one place of its nature's quota;
    a sample or peer fund needs MIN_ROWS."""
    by_nature: dict = {}
    for isin, nature, h, n in sorted(rows, key=lambda r: (r[1], r[2], r[0])):
        if n >= MIN_ROWS:
            by_nature.setdefault(nature, []).append(isin)
    roles: dict = {}
    nature_of = {r[0]: r[1] for r in rows}
    rows_of = {r[0]: r[3] for r in rows}
    missing = [p for p in real_positives if rows_of.get(p, 0) < MIN_ROWS_POSITIVE]
    if missing:
        raise SystemExit(f"error: real positive(s) not eligible (in universe, nature set, >= {MIN_ROWS_POSITIVE} monthly rows): {missing}")
    for p in real_positives:
        roles[p] = "real_positive"
    for nature, quota in QUOTAS.items():
        pool = [i for i in by_nature.get(nature, []) if i not in roles]
        take = quota - sum(1 for p in real_positives if nature_of[p] == nature)
        for i in pool[:max(take, 0)]:
            roles[i] = "sample"
        for i in pool[max(take, 0):max(take, 0) + PEERS_PER_NATURE]:
            roles[i] = "peer"
    return roles


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--out", default=str(_ROOT / "tests" / "fixtures" / "fault_injection"))
    ap.add_argument("--real-positive", action="append", default=[], help="ISIN whose real series is itself the defect (repeatable)")
    args = ap.parse_args(argv)
    from shared.db import get_connection
    conn = get_connection()
    conn.read_only = True                                                 # the server refuses any write on this session
    rows = [tuple(r) for r in conn.execute(
        "SELECT n.isin, f.fund_nature, md5(n.isin), count(*) FROM fund_nav_monthly n JOIN fund_master f ON f.isin = n.isin "
        "WHERE f.in_current_universe = 1 AND f.fund_nature IS NOT NULL AND n.nav > 0 GROUP BY n.isin, f.fund_nature HAVING count(*) >= %s", (MIN_ROWS_POSITIVE,)).fetchall()]
    roles = select(rows, args.real_positive)
    nature_of = {r[0]: r[1] for r in rows}
    nav = conn.execute("SELECT isin, date, nav FROM fund_nav_monthly WHERE isin = ANY(%s) AND nav > 0 ORDER BY isin, date", (sorted(roles),)).fetchall()
    df = pd.DataFrame([tuple(r) for r in nav], columns=["isin", "date", "nav"])
    df["nav"] = df["nav"].astype(float)
    df.insert(1, "nature", df["isin"].map(nature_of))
    df.insert(2, "role", df["isin"].map(roles))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "nav_sample.csv.gz", index=False, compression="gzip")
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=_ROOT, capture_output=True, text=True).stdout.strip()
    manifest = {"built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "code_commit": sha, "min_rows": MIN_ROWS, "peers_per_nature": PEERS_PER_NATURE,
                "quotas": QUOTAS, "real_positives": args.real_positive, "rule": "in-universe, nature set, >= min_rows monthly NAV rows; ordered by md5(isin) inside nature; first quota = sample, next peers_per_nature = peers",
                "funds": {r: int((pd.Series(roles) == r).sum()) for r in ("sample", "real_positive", "peer")}, "rows": int(len(df)),
                "natures": {n: int(c) for n, c in df.drop_duplicates("isin")["nature"].value_counts().items()}}
    (out / "nav_sample.manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    size = (out / "nav_sample.csv.gz").stat().st_size
    print(f"fixture written to {out}: {manifest['funds']} | {len(df)} NAV rows | {size / 1024:.0f} KiB gz")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
