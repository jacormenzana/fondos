#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
fee_state_accuracy.py -- read-only accuracy sample of the derived entry / exit fee state (Package H; plan R7.1 #3, R9).

    python scripts/audit/p1/fee_state_accuracy.py                 # n = 30 per stratum, prints the verdict and the disagreements
    python scripts/audit/p1/fee_state_accuracy.py --n 30 --out out/pkg_h/accuracy.json

"Consistent" is not "correct": a stored 0.0 would read ZERO_CONFIRMED. So for a sample of funds per derived state this reads the fee row of the stored KID text
(raw text plus the DLA2 table text) WITH ITS OWN logic, independent of the parser and the cost extractor, and compares what the document shows with the state derived
from the stored numbers (shared.fee_state.derive_fee_state):

    document shows            -> expected derived state
    an explicit zero          -> ZERO_CONFIRMED   ('no cobramos ...', '0 EUR', '0,00 %': an explicit zero wins over a 'may apply up to' sentence)
    a maximum ('hasta', 'maximo', 'up to') with a positive number -> ENTRY_CONDITIONAL / EXIT_CONDITIONAL
    a positive percentage or amount without a maximum marker    -> EXTRACTED
    nothing on the row        -> NOT_FOUND
A row the reader cannot find is 'unreadable' (reported, never counted as an error). The decision per stratum is the Wilson rule of shared.fee_state.accuracy_decision.
Exit codes: 0 PASS/REVIEW | 1 a stratum is STOP | 3 cannot read the database. Read-only: SELECTs only; the only file written is the JSON.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from shared.fee_state import accuracy_decision, derive_fee_state  # noqa: E402

RC_OK, RC_STOP, RC_NO_DB = 0, 1, 3
SAMPLE = 30
ROW_START = {"entry": re.compile(r"costes?\s+de\s+entrada|entry\s+costs?", re.I), "exit": re.compile(r"costes?\s+de\s+salida|exit\s+costs?", re.I)}
ROW_END = re.compile(r"costes?\s+de\s+(?:entrada|salida)|costes?\s+corrientes|comisiones?\s+de\s+gesti|costes?\s+de\s+operaci|costes?\s+recurrentes|ongoing\s+costs?|entry\s+costs?|exit\s+costs?", re.I)
NEGATION = re.compile(r"no\s+cobramos|no\s+se\s+cobra|no\s+aplica|no\s+se\s+aplica|nocobramos|we\s+do\s+not\s+charge|no\s+(?:entry|exit)\s+(?:fee|charge)", re.I)
MAX_MARK = re.compile(r"\bhasta\b|m[aá]xim[oa]|up\s+to|as\s+much\s+as", re.I)
PERCENT = re.compile(r"(\d{1,3}(?:[.,]\d{1,3})?)\s*%")
AMOUNT = re.compile(r"(?:[$€£]\s?(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d+)?)|(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d+)?)\s*(?:EUR|USD|GBP|CHF|JPY|SEK|NOK|DKK)\b)", re.I)
WINDOW_MAX = 420


def _num(text: str) -> float:
    t = text.strip()
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".") else t.replace(",", "")
    elif "," in t:
        t = t.replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", t):
        t = t.replace(".", "")
    return float(t)


def fee_row(text: str, side: str) -> str:
    """The fee row of one side: from its label to the next cost label (bounded), whitespace and table separators collapsed. '' when the label is absent."""
    m = ROW_START[side].search(text or "")
    if not m:
        return ""
    rest = text[m.end(): m.end() + WINDOW_MAX]
    end = ROW_END.search(rest)
    row = rest[: end.start()] if end else rest
    return re.sub(r"[|\s]+", " ", row).strip()


def read_row(row: str) -> str:
    """'zero' | 'conditional' | 'positive' | 'none' from the printed row. An explicit zero (negation, 0 %, 0 EUR) wins over a sentence about a possible fee."""
    if not row:
        return "none"
    percents = [_num(x) for x in PERCENT.findall(row)]
    amounts = [_num(a or b) for a, b in AMOUNT.findall(row)]
    if NEGATION.search(row) or (percents and percents[0] == 0) or (not percents and amounts and amounts[0] == 0):
        return "zero"
    positive = [v for v in percents + amounts if v > 0]
    if not positive:
        return "none"
    return "conditional" if MAX_MARK.search(row) else "positive"


def expected_kind(state: str) -> str:
    return {"ZERO_CONFIRMED": "zero", "EXTRACTED": "positive", "ENTRY_CONDITIONAL": "conditional", "EXIT_CONDITIONAL": "conditional", "NOT_FOUND": "none"}[state]


def judge(state: str, text: str, side: str) -> dict:
    row = fee_row(text, side)
    if not row:
        return {"verdict": "unreadable", "row": "", "read": None}
    seen = read_row(row)
    return {"verdict": "agree" if seen == expected_kind(state) else "disagree", "row": row[:240], "read": seen}


def stratum_of(point, ceiling, side: str) -> str:
    state = derive_fee_state(point, ceiling, side)
    if side == "exit" and state == "ZERO_CONFIRMED":
        return "ZERO_CONFIRMED:point0_no_ceiling" if (point is not None and ceiling is None) else "ZERO_CONFIRMED:other"
    return state


def summarise(results: list) -> dict:
    """Per stratum: n read (agree + disagree), errors, decision. Unreadable rows are listed apart and never count as errors."""
    out = {}
    for item in results:
        s = out.setdefault(item["stratum"], {"agree": 0, "disagree": 0, "unreadable": 0, "disagreements": []})
        s[item["verdict"]] += 1
        if item["verdict"] == "disagree":
            s["disagreements"].append({k: item[k] for k in ("isin", "state", "read", "row")})
    for s in out.values():
        n = s["agree"] + s["disagree"]
        s["n_read"] = n
        s["decision"] = accuracy_decision(s["disagree"], n) if n else "NO_DATA"
    return out


def load_sample(conn, n: int) -> list:
    """n funds per stratum, deterministic by md5(isin), over the funds with a readable KID text (universe only). SELECTs only."""
    cur = conn.execute(
        "SELECT f.isin, f.entry_fee_pct, f.entry_fee_pct_max, f.exit_fee_pct, f.exit_fee_pct_max, "
        "coalesce(k.raw_kiid_text, '') || ' ' || coalesce(k.dla2_table_text, '') AS txt "
        "FROM fund_master f JOIN fund_kiid_metadata k ON k.isin = f.isin AND k.kiid_class = 1 "
        "WHERE f.in_current_universe = 1 AND k.kiid_status = 'OK' ORDER BY md5(f.isin)")
    cols = [d.name for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    picked, count = [], {}
    for r in rows:
        for side, p, c in (("entry", "entry_fee_pct", "entry_fee_pct_max"), ("exit", "exit_fee_pct", "exit_fee_pct_max")):
            key = (side, stratum_of(r[p], r[c], side))
            if count.get(key, 0) < n:
                count[key] = count.get(key, 0) + 1
                picked.append({"isin": r["isin"], "side": side, "stratum": f"{side}:{key[1]}", "state": derive_fee_state(r[p], r[c], side), "txt": r["txt"]})
    return picked


def run(conn, n: int) -> dict:
    results = []
    for item in load_sample(conn, n):
        j = judge(item["state"], item["txt"], item["side"])
        results.append({"isin": item["isin"], "stratum": item["stratum"], "state": item["state"], **j})
    return {"generated": date.today().isoformat(), "n_per_stratum": n, "strata": summarise(results)}


def render(result: dict) -> str:
    out = [f"FEE STATE ACCURACY (read-only; independent reader of the stored KID text; n = {result['n_per_stratum']} per stratum)"]
    for name, s in sorted(result["strata"].items()):
        out.append(f"  [{s['decision']:<6}] {name:<48} agree {s['agree']:>3} disagree {s['disagree']:>2} unreadable {s['unreadable']:>2}")
        for d in s["disagreements"]:
            out.append(f"           {d['isin']} derived {d['state']}, document reads {d['read']}: {d['row'][:150]}")
    return "\n".join(out)


def main(argv=None, conn=None) -> int:
    parser = argparse.ArgumentParser(description="Read-only accuracy sample of the derived fee state (Package H).")
    parser.add_argument("--n", type=int, default=SAMPLE, help="funds per stratum (default 30)")
    parser.add_argument("--out", type=Path, default=ROOT / "out" / "pkg_h" / "accuracy.json")
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):          # KID text carries characters a Windows console cannot encode: never let printing abort the run
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if conn is None:
        from shared.env_guard import require_db_driver
        require_db_driver("fee_state_accuracy.py")
        try:
            from shared.db import get_connection
            conn = get_connection()
        except Exception as exc:
            print(f"cannot read the database: {type(exc).__name__}: {str(exc)[:160]}", file=sys.stderr)
            return RC_NO_DB
    result = run(conn, args.n)
    print(render(result))
    if not args.no_write:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return RC_STOP if any(s["decision"] == "STOP" for s in result["strata"].values()) else RC_OK


if __name__ == "__main__":
    sys.exit(main())
