# -*- coding: utf-8 -*-
"""
cost_pct_anchored.py  —  BL-COST-OPER-FIX

Layout-independent extraction of the operación / transaction-cost percentage
(Transaction_Cost_Pct) from KID "Composición de los costes" text.

WHY THIS EXISTS
---------------
The operación cost % lives inside the free-text DESCRIPTION cell of the cost
table ("0,02% del valor de su inversión al año. Se trata de una estimación de
los costes en que incurrimos al comprar y vender las inversiones subyacentes…").
pdfplumber's borderless-table extraction (vertical/horizontal_strategy='text')
fragments or drops that middle cell, so the serialized grid row ends up as
`|||Costes de operacion|||0 EUR|||` — the % is gone and Transaction_Cost_Pct
becomes NULL. Positional / column heuristics fail because issuer layouts differ
(BNP weaves values before labels; Pictet centres the label inside the wrapped
description so the % lands BEFORE its label).

THE INVARIANT WE ANCHOR ON
--------------------------
Across every PRIIPS KID issuer observed (PIMCO, UBS, Pictet, Morgan Stanley,
JPMorgan, T. Rowe Price, BNP, Fidelity[EN], DWS) the transaction-cost sentence
is the ONLY place that combines an "incurr*" token with a buy/sell verb:
    ES: "…costes en que incurrimos al comprar y vender…"
        "…costes incurridos al comprar y vender…"
        "…incurrimos cuando compramos y vendemos…"
    EN: "…costs incurred when we buy and sell…"
The cost % is the nearest percentage PRECEDING that signature (same sentence),
e.g. "0,76% del valor… Se trata de una estimación de los costes en que
incurrimos al comprar y vender". This holds regardless of column geometry or
reading-order scatter, and regardless of "al año" / "por año" / English phrasing.

VALIDATION (2026-06-13, pdfplumber-extracted text)
--------------------------------------------------
10/10 text PDFs correct, spanning the dominant corpus issuers, incl. the
English Fidelity KID and the correct NULL for a fund that states no operación %:
    PIMCO 0.19 · UBS-Bal None · Pictet 0.10 · MSIM 0.36 · JPM 0.22 ·
    T.Rowe 0.40 · BNP 0.76 · UBS-WF 0.10 · Fidelity(EN) 0.56 · DWS 0.04
Fully-scanned KIDs (e.g. Allianz LU1548496022, 0 text chars) yield nothing from
pdfplumber; they must be fed OCR'd text — the same anchor then applies unchanged.

INTEGRATION
-----------
Call on the SPACED text the serializer already prefers (pdfplumber self-extract,
or OCR fallback for scanned PDFs — see BL-COST-SER-FIX in dla_table_serializer).
When the operación composition row carries an EUR amount but no %, inject the
value returned here so the parser's strong (|||) path yields Transaction_Cost_Pct.
This module is pure/text-only and has no side effects.
"""

import re

_PCT = re.compile(r'(\d{1,3}(?:[.,]\d{1,3})?)\s*%')
# BL-COST-OPER-FIX-2: extended anchor vocabulary.
# "incurr*" alone missed two confirmed corpus issuers:
#   - Candriam: "costes soportados cuando compramos y vendemos"  → soport\w* added to Pass 1
#   - EdR:      "tendrán lugar al efectuar compras y ventas"     → no incurr/soport token;
#     covered by Pass 2 (_BUYSELL_PAIR direct anchor, see function below).
_INCURR_SOPORT = re.compile(r'incurr\w*|soport\w*', re.I)  # incurrimos/incurridos/incurred/soportados
_BUYSELL = re.compile(r'compr|vend|buy|sell', re.I)          # comprar/compramos · vender/vendemos · buy · sell
# Direct buy/sell PAIR: the ONLY sentence in a PRIIPS KID where both verbs co-occur.
# Used as fallback (Pass 2) when no incurr*/soport* token is present.
_BUYSELL_PAIR = re.compile(
    r'compr[a-z]+\s+y\s+vend[a-z]+'   # comprar y vender, compramos y vendemos, compra y venta
    r'|vend[a-z]+\s+y\s+compr[a-z]+'  # reverse order
    r'|compras?\s+y\s+ventas?'         # compra y venta, compras y ventas
    r'|buy\s+and\s+sell'
    r'|sell\s+and\s+buy',
    re.I,
)

# Window sizes are bounded (R-6): never scan the whole document.
_CTX_AFTER = 90      # chars after "incurr*" allowed to contain the buy/sell verb (tolerates an
                     # inserted "Costes de operación" label split mid-sentence by the table layout)
_BACK = 170          # chars before the signature to search for the nearest preceding %
_FWD = 120           # fallback: chars after the signature if no % precedes it


def extract_transaction_cost_pct(text):
    """
    Return the operación / transaction-cost percentage as a float fraction-of-100
    (e.g. 0.02 for "0,02%"), or None if the KID states no operación % (or text is
    unreadable). Pure function; bounded windows only.
    """
    if not text:
        return None
    # Pass 1: incurr*/soport* + buy/sell within window (highest specificity).
    # Covers: incurrimos/incurridos/incurred (original 10 issuers) +
    #         soportados (Candriam: "costes soportados cuando compramos y vendemos").
    anchor = None
    for m in _INCURR_SOPORT.finditer(text):
        if _BUYSELL.search(text[m.start():m.start() + _CTX_AFTER]):
            anchor = m
            break
    # Pass 2: direct buy/sell PAIR (EdR and similar: "compra y venta" without incurr/soport).
    # Only reached when Pass 1 finds nothing.
    if anchor is None:
        anchor = _BUYSELL_PAIR.search(text)
    if anchor is None:
        return None
    back = text[max(0, anchor.start() - _BACK):anchor.start()]
    pcts = _PCT.findall(back)
    if pcts:
        return float(pcts[-1].replace(',', '.'))         # nearest preceding %
    fwd = text[anchor.end():anchor.end() + _FWD]
    pf = _PCT.search(fwd)
    return float(pf.group(1).replace(',', '.')) if pf else None
