"""
FND-0243 release gate: is fund_master.fund_currency what a P1 pass would persist NOW?

The EUR view (shared/eur_nav.py) converts each share class from its stored Fund_Currency. The step-2 detectors change that
value for ~93 active funds (83 NULL filled, 10 corrected -- 9 EUR-hedged classes stored as their USD base currency) only when
P1 runs; enabling the EUR view before that would divide a EUR NAV by the USD rate. `pending_fund_currency` re-derives, with
the production rule (kiid_parser.resolve_fund_currency, cached KIID text, no HTTP), the value each active fund would get and
lists the ones whose stored value differs. COALESCE semantics: a fund whose rule yields None is not pending (P1 would keep the
stored value), it is reported separately as unknown. WRONG_DOC funds are skipped (P1 does not classify them).

Read-only. Streams the KIID texts through a server-side cursor (~40 MB over the active universe).
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

Q_ACTIVE_TEXTS = """
    SELECT fm.isin, fm.fund_name, fm.fund_currency, k.language, k.raw_kiid_text
    FROM fund_master fm
    JOIN fund_kiid_metadata k ON k.isin = fm.isin AND k.kiid_class = 1
    WHERE fm.in_current_universe = 1
      AND COALESCE(k.kiid_status, '') <> 'WRONG_DOC'
    ORDER BY fm.isin
"""


def _resolver():
    for p in (str(_ROOT), str(_ROOT / "proyecto1")):          # P1 modules import each other as `core.*`
        if p not in sys.path:
            sys.path.insert(0, p)
    from core.kiid_parser import resolve_fund_currency
    return resolve_fund_currency


def diff_rows(rows, resolve) -> tuple[list, list]:
    """Pure. rows = (isin, fund_name, stored, language, text). Returns (pending [(isin, name, stored, resolved)],
    unknown [isin]) where pending = the resolved value is known and differs from the stored one (upper-case compare)."""
    pending, unknown = [], []
    for isin, name, stored, lang, text in rows:
        resolved = resolve(text, lang, name)
        if resolved is None:
            if stored is None:
                unknown.append(isin)
            continue
        if (stored or "").upper() != resolved.upper():
            pending.append((isin, name, stored, resolved))
    return pending, unknown


def pending_fund_currency(conn, batch: int = 200) -> tuple[list, list]:
    resolve = _resolver()

    def _rows():
        with conn.cursor(name="fund_currency_gate") as cur:
            cur.execute(Q_ACTIVE_TEXTS)
            while True:
                chunk = cur.fetchmany(batch)
                if not chunk:
                    return
                yield from chunk
    return diff_rows(_rows(), resolve)


def summarize(pending: list, unknown: list, limit: int = 20) -> str:
    """One line for a gate detail."""
    from collections import Counter
    moves = Counter(f"{s or 'NULL'}->{r}" for _, _, s, r in pending)
    head = ", ".join(f"{m}:{n}" for m, n in moves.most_common()) or "none"
    ex = ", ".join(i for i, *_ in pending[:limit])
    return (f"{len(pending)} active funds whose Fund_Currency the next P1 pass changes ({head})"
            + (f" e.g. {ex}" if ex else "") + f"; {len(unknown)} stay unknown (excluded from EUR scoring)")
