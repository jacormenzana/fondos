# -*- coding: utf-8 -*-
"""
audit_investment_base.py — confirm the PRIIPS investment base is parseable
corpus-wide from the stored DLA2 grid (and Raw_KIID_Text as fallback).

Decides whether EUR-derivation (mgmt_eur / base * 100) is a safe fix for the
mgmt%<->oper% collision: it is only safe if `base` is reliably recoverable for
the affected funds, and is consistently the standard 10 000 (any currency).

Strategy: for every Class-1 fund, search BOTH DLA2_Table_Text and
Raw_KIID_Text for the investment-base statement using several known phrasings:
    "Se invierten 10 000 EUR" / "Se invierten 10.000 EUR"
    "Inversión: 10 000 EUR"   / "Ejemplo de inversión: USD 10 000"
    "Para una inversión de: 10 000 EUR"
    "invest 10 000"  (EN)
Report: parse rate, distinct base values, currency spread, and the specific
affected swap ISINs' base recovery.

Run (Windows, conda 'des'):
  set PYTHONPATH=C:\\desarrollo\\fondos\\proyecto1;C:\\desarrollo\\fondos\\proyecto1\\core;C:\\desarrollo\\fondos\\shared
  python -X utf8 audit_investment_base.py
"""
import re
import sqlite3
import collections

DB = r'C:\desarrollo\fondos\db\fondos.sqlite'

# Number forms: "10 000", "10.000", "10,000", "10000"
_NUM = r'(\d{1,3}(?:[ .,]\d{3})+|\d{4,6})'
_CUR = r'(?:EUR|USD|GBP|CHF|€|\$|£)'

# Several base-statement phrasings, base number captured in group 1, optional cur in 2.
_BASE_PATTERNS = [
    re.compile(r'se\s+invierten\s+' + _NUM + r'\s*(' + _CUR + r')?', re.I),
    re.compile(r'(?:ejemplo\s+de\s+)?inversi[oó]n(?:\s+de)?\s*:?\s*(?:' + _CUR + r'\s*)?' + _NUM, re.I),
    re.compile(r'para\s+una\s+inversi[oó]n\s+de\s*:?\s*' + _NUM, re.I),
    re.compile(r'invest(?:ment)?\s+(?:of\s+)?(?:' + _CUR + r'\s*)?' + _NUM, re.I),
]


def parse_base(text):
    """Return (base_int, raw_match) or (None, None)."""
    if not text:
        return None, None
    for pat in _BASE_PATTERNS:
        m = pat.search(text)
        if m:
            num = m.group(1)
            digits = re.sub(r'[ .,]', '', num)
            try:
                val = int(digits)
                if 1000 <= val <= 1000000:
                    return val, m.group(0)[:50]
            except ValueError:
                pass
    return None, None


def main():
    conn = sqlite3.connect(DB)
    rows = conn.execute(
        "SELECT ISIN, DLA2_Table_Text, Raw_KIID_Text FROM fund_kiid_metadata "
        "WHERE KIID_Class = 1"
    ).fetchall()
    conn.close()

    total = len(rows)
    from_grid = 0
    from_raw = 0
    none_found = 0
    base_values = collections.Counter()
    none_isins = []

    base_by_isin = {}
    for isin, grid, raw in rows:
        b, _ = parse_base(grid)
        if b is not None:
            from_grid += 1
        else:
            b, _ = parse_base(raw)
            if b is not None:
                from_raw += 1
        if b is None:
            none_found += 1
            none_isins.append(isin)
        else:
            base_values[b] += 1
            base_by_isin[isin] = b

    print('=== Investment base parse audit (Class-1 funds) ===')
    print(f'Total funds:            {total}')
    print(f'Base from DLA2 grid:    {from_grid}')
    print(f'Base from Raw_KIID:     {from_raw}')
    print(f'Base NOT found:         {none_found}')
    print()
    print('Distinct base values (top 10):')
    for val, cnt in base_values.most_common(10):
        print(f'   {val:>10,}  x {cnt}')
    print()
    print('Sample of NOT-found ISINs:', none_isins[:15])

    # Targeted: the known mgmt%<->oper% swap ISINs
    swap_isins = ['ES0125756017', 'ES0125756009', 'ES0122762000',
                  'ES0125757007', 'ES0125757015', 'ES0125756025']
    print()
    print('=== Base recovery for known swap funds ===')
    for isin in swap_isins:
        print(f'   {isin}: base = {base_by_isin.get(isin, "NOT FOUND")}')


if __name__ == '__main__':
    main()
