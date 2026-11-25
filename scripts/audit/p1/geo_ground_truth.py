#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""FND-0441 Step 1: ground truth for the `Geography` extractor. READ-ONLY on the database; writes only the sample lists (ISIN, language, nature; no document text) under doc/audit/geo_ground_truth/.

  --stage sample              draw the sealed HOLDOUT first, then the development sample and the KIID-only sample; refuses to overwrite an existing draw.
  --stage read --set dev      print the objective-section excerpt of every fund of one sample, in batches, to be labelled by reading. `holdout` is refused until a commit whose message
                              contains GEO-PATTERNS-FROZEN exists: the holdout judges patterns it must not have shaped.

Populations (current universe, `Geography='Global'`, KIID status not WRONG_DOC):
  defaults  = the KIID extractor, the name detector and the equity-index signal all report nothing;
  kiid-only = the KIID extractor reports Global and the name reports nothing.
Samples are stratified by (document language, nature), proportional with largest remainder, ordered inside a stratum by md5(ISIN)."""
from __future__ import annotations

import argparse
import csv
import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "doc" / "audit" / "geo_ground_truth"
SIZES = {"holdout": 150, "dev": 100, "kiidonly": 80}
FROZEN_MARKER = "GEO-PATTERNS-FROZEN"
# documents whose text was already displayed while analysing the problem (2026-11-25): never in the holdout
ALREADY_SEEN = frozenset({"DE0008474149", "LU0164881194", "LU0552552704", "LU1295551144", "LU2008761137", "DE0009769760", "LU0249549436", "LU1022658667", "LU1700711317", "LU2066958385",
                          "LU0240540988", "LU0428745664", "LU1279334996", "LU1729940673", "LU1333148903"})


def md5_key(isin: str) -> str:
    return hashlib.md5(isin.encode("utf-8")).hexdigest()


def stratified_sample(items, n: int, exclude=frozenset()) -> list:
    """`items`: dicts with `isin` and `stratum`. Proportional allocation of `n` over the strata (largest remainder), taking inside each stratum the first ISINs in md5 order. Deterministic."""
    pool = [x for x in items if x["isin"] not in exclude]
    if n >= len(pool):
        return sorted(pool, key=lambda x: md5_key(x["isin"]))
    strata: dict = {}
    for x in pool:
        strata.setdefault(x["stratum"], []).append(x)
    quota = {k: n * len(v) / len(pool) for k, v in strata.items()}
    take = {k: int(q) for k, q in quota.items()}
    for k in sorted(quota, key=lambda k: (-(quota[k] - take[k]), str(k)))[: n - sum(take.values())]:
        take[k] += 1
    out = []
    for k in sorted(strata, key=str):
        out += sorted(strata[k], key=lambda x: md5_key(x["isin"]))[: take[k]]
    return sorted(out, key=lambda x: md5_key(x["isin"]))


_HEADING = re.compile(r"\bObjetivos?\b(?! de este documento)|\bInvestment [Oo]bjectives?\b|\bObjectives\b|\bPol[ií]tica de [Ii]nversi[oó]n\b|\bInvestment [Pp]olicy\b")


def objective_excerpt(text, width: int) -> str:
    """At most `width` characters from the first objective-section heading of the document (its start when there is none); whitespace collapsed."""
    flat = " ".join((text or "").split())
    m = _HEADING.search(flat)
    return flat[m.start() if m else 0:][:width]


def holdout_unlocked(messages) -> bool:
    return any(FROZEN_MARKER in msg for msg in messages)


def assert_readable(name: str, frozen: bool) -> None:
    if name == "holdout" and not frozen:
        raise PermissionError(f"the holdout is sealed: no commit message contains {FROZEN_MARKER} yet")


def _git_messages() -> list:
    try:
        return subprocess.run(["git", "log", "-200", "--format=%B%x00"], cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", timeout=60, check=True).stdout.split("\0")
    except Exception:                                                  # noqa: BLE001 -- unreadable git means sealed
        return []


def _load(isins=None) -> list:
    sys.path.insert(0, str(ROOT))
    import shared.config  # noqa: F401  loads .env
    from shared.db import get_connection
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT fm.ISIN, fm.Fund_Name, fm.Fund_Nature, fm.Benchmark_Declared, fm.Geography, k.Raw_KIID_Text, k.Language, k.KIID_Status
            FROM fund_master fm JOIN fund_kiid_metadata k ON k.ISIN = fm.ISIN AND k.KIID_Class = 1
            WHERE fm.In_Current_Universe = 1 ORDER BY fm.ISIN
        """).fetchall()
    finally:
        conn.close()
    keys = ("isin", "name", "nature", "benchmark", "geography", "text", "language", "status")
    out = [dict(zip(keys, r)) for r in rows]
    return out if isins is None else [x for x in out if x["isin"] in isins]


def populations(rows) -> "tuple[list, list]":
    from proyecto1.core import classify_utils as cu
    defaults, kiid_only = [], []
    for r in rows:
        if r["geography"] != "Global" or r["status"] == "WRONG_DOC":
            continue
        name_l = (r["name"] or "").lower()
        kiid, by_name = cu.detect_geography_from_kiid(r["text"] or ""), cu.detect_geography(name_l)
        item = {"isin": r["isin"], "stratum": f"{r['language'] or '??'}|{r['nature']}", "language": r["language"] or "??", "nature": r["nature"]}
        if not kiid and not by_name and not cu.detect_geography_from_equity_index(r["name"], r["benchmark"], r["nature"], cu.detect_theme(name_l)):
            defaults.append(item)
        elif not by_name and kiid and cu._derive_geography_en(kiid, name_l) == "Global":
            kiid_only.append(item)
    return defaults, kiid_only


def stage_sample() -> int:
    if OUT.exists() and any(OUT.glob("*.csv")):
        print(f"[REFUSED] {OUT} already holds a draw: a sealed sample is drawn once")
        return 2
    defaults, kiid_only = populations(_load())
    holdout = stratified_sample(defaults, SIZES["holdout"], exclude=ALREADY_SEEN)
    dev = stratified_sample(defaults, SIZES["dev"], exclude=ALREADY_SEEN | {x["isin"] for x in holdout})
    kio = stratified_sample(kiid_only, SIZES["kiidonly"])
    OUT.mkdir(parents=True, exist_ok=True)
    lines = [f"# Sealed samples of FND-0441 Step 1. Populations: defaults {len(defaults)}, kiid-only {len(kiid_only)}.", ""]
    for name, sample in (("holdout", holdout), ("dev", dev), ("kiidonly", kio)):
        path = OUT / f"{name}.csv"
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["isin", "language", "nature"])
            w.writerows([x["isin"], x["language"], x["nature"]] for x in sample)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"- `{name}.csv`: {len(sample)} funds, sha256 `{digest}`")
        print(f"[SAMPLE] {name}: {len(sample)} funds, sha256 {digest[:16]}")
    (OUT / "SAMPLES.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[POPULATION] defaults {len(defaults)} | kiid-only {len(kiid_only)}")
    return 0


def stage_read(name: str, batch: int, size: int, width: int) -> int:
    assert_readable(name, holdout_unlocked(_git_messages()))
    with open(OUT / f"{name}.csv", encoding="utf-8") as fh:
        listed = list(csv.DictReader(fh))
    part = listed[batch * size: (batch + 1) * size]
    docs = {x["isin"]: x for x in _load({r["isin"] for r in part})}
    print(f"[{name}] batch {batch}: funds {batch * size + 1}-{batch * size + len(part)} of {len(listed)}")
    for i, r in enumerate(part, batch * size + 1):
        print(f"{i} {r['isin']} {r['language']} {r['nature']}: {objective_excerpt(docs.get(r['isin'], {}).get('text'), width)}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stage", choices=["sample", "read"], required=True)
    ap.add_argument("--set", dest="name", choices=list(SIZES), default="dev")
    ap.add_argument("--batch", type=int, default=0)
    ap.add_argument("--size", type=int, default=25)
    ap.add_argument("--width", type=int, default=520)
    args = ap.parse_args(argv)
    try:
        return stage_sample() if args.stage == "sample" else stage_read(args.name, args.batch, args.size, args.width)
    except PermissionError as exc:
        print(f"[REFUSED] {exc}")
        return 3


if __name__ == "__main__":
    sys.exit(main())
