# proyecto1/core/cost_currency.py
# -*- coding: utf-8 -*-
"""Single source of the CURRENCY VOCABULARY of the cost parsers (FND-0279, P#11).

Before this module the same alternation lived, shortened differently, in `cost_table_parser` (four places: the amount pattern, the two cell patterns,
`_CURRENCY`, `_BASE_CUR`) and in `cost_format_router` (the two investment-base patterns): JPY was in none of them and SEK / NOK / DKK / PLN / CZK only in two.
A cost row such as "Costes totales 808 JPY 1874 JPY" was therefore not read as amounts at all (LU0512093542 kept a wrong 5-year total), and the KID currency
(`fund_master.KID_Currency`, the currency the KID's own cost table is expressed in) was detected for only half of the funds because the investment base was only
accepted as "10.000 EUR" / "10,000 USD".

Pure (no I/O, no project imports; R-7). Not shared on purpose: `dla_table_serializer._AMT` decides which candidate table rows WIN (a scoring role, measured
sensitive to any change of that pattern on 408 real PDFs), so it keeps its own pattern.
"""
from __future__ import annotations

import re
from typing import Optional

# Currencies a PRIIPs / UCITS cost table is printed in. Measured over the 3,763 stored KIIDs next to the example base: EUR 2,812, USD 797, GBP 26, CHF 11, JPY 3;
# SEK / NOK / DKK / PLN / CZK do not occur today but are real KID currencies (they were already in two of the old lists).
CURRENCY_CODES = ("EUR", "USD", "GBP", "CHF", "JPY", "SEK", "NOK", "DKK", "PLN", "CZK")
# Symbols with ONE reading. The yen sign is deliberately absent: it is also the yuan sign (JPY / CNY), the ISO code is the reliable form.
SYMBOL_TO_CODE = {"€": "EUR", "$": "USD", "£": "GBP"}

CODES_ALT = "|".join(CURRENCY_CODES)
CURRENCY = r"(?:" + CODES_ALT + r"|€|\$|£)"

# The PRIIPs example amount, 10 000 in any grouping a KID uses (10.000, 10,000, 10 000, 10'000, 10000), with an optional ,00 / .00.
BASE_AMOUNT = r"10[\s.,'’  ]?000(?:[.,]00)?"

_BASE_PREFIX = re.compile(r"(" + CURRENCY + r")\s?" + BASE_AMOUNT + r"(?!\d)", re.IGNORECASE)
_BASE_SUFFIX = re.compile(r"(?<!\d)" + BASE_AMOUNT + r"\s?(" + CURRENCY + r")", re.IGNORECASE)


def currency_code(token: str) -> str:
    """ISO code of a matched currency token ('EUR', 'eur' or the euro sign -> 'EUR')."""
    return SYMBOL_TO_CODE.get(token, token.upper())


_TOKEN = re.compile(r"(?<![A-Za-z])(" + CODES_ALT + r"|€|\$|£)(?![A-Za-z])")


def _base_hits(text: str) -> list:
    return sorted((m.start(), currency_code(m.group(1))) for pat in (_BASE_PREFIX, _BASE_SUFFIX) for m in pat.finditer(text))


def detect_base_currency(text: Optional[str]) -> Optional[str]:
    """ISO code of the currency the example investment base is written in ('EUR 10,000', '10 000 EUR', '10.000 USD', "CHF 10'000"), the EARLIEST one in the text
    when a document names more than one; None when the text has none."""
    hits = _base_hits(text) if text else []
    return hits[0][1] if hits else None


def _scan_totals_row(text: str, total_label: "re.Pattern") -> tuple:
    """(currency of the totals row or None, position of the FIRST totals label or None). The row is the label's line plus the next two; a row naming two currencies
    decides nothing and ends the scan (later labels are not looked at)."""
    first = None
    for m in total_label.finditer(text):
        block = "\n".join(text[text.rfind("\n", 0, m.start()) + 1:].splitlines()[:3])
        tokens = {currency_code(t) for t in _TOKEN.findall(block)}
        if first is None:
            first = m.start()
        if len(tokens) == 1:
            return next(iter(tokens)), first
        if tokens:
            break
    return None, first


def detect_totals_row_currency(text: Optional[str], total_label: "re.Pattern") -> Optional[str]:
    """ISO code of the currency written on the KID's totals row itself ("Total costs 61 EUR 194 EUR"; also the cell layout where the amounts sit two lines below
    the label, after the "Annual cost impact" label), or None. Only the row: no example-base fallback (that is `detect_cost_currency`)."""
    return _scan_totals_row(text, total_label)[0] if text else None


def detect_cost_currency(text: Optional[str], total_label: "re.Pattern") -> Optional[str]:
    """ISO code of the currency the KID's COST table is written in (`fund_master.KID_Currency`), by evidence order:
    1. the currency of the totals row itself (the label line and the next two): "Total costs 61 EUR 194 EUR" -> EUR; a row naming two currencies decides nothing;
    2. else the example-base mention nearest BEFORE the totals label (within 1,500 characters): the scenarios table can be written in another currency than the
       costs table (LU0368229703: "USD 10,000" earlier in the text, costs in EUR);
    3. else the earliest example-base mention.
    Measured against `Fund_Currency` over the 3,763 stored KIIDs: set for 3,654 funds, agreeing in 3,643 of 3,648 comparable ones (rule 3 alone: 3,648 / 3,636 of
    3,642). `total_label` is cost_table_parser._TOTAL_COSTS_LABEL, passed in so this module stays free of project imports."""
    if not text:
        return None
    row, label_pos = _scan_totals_row(text, total_label)
    if row:
        return row
    hits = _base_hits(text)
    if label_pos is not None:
        before = [h for h in hits if h[0] <= label_pos and label_pos - h[0] <= 1500]
        if before:
            return before[-1][1]
    return hits[0][1] if hits else None
