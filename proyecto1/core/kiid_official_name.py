# proyecto1/core/kiid_official_name.py
# -*- coding: utf-8 -*-
"""FND-0246: the OFFICIAL sub-fund name printed in a KIID / PRIIPs KID, the class-invariant key of a fund family.

`fund_master.fund_name` is a catalogue string cut at ~30 characters whose abbreviations differ between the share classes of ONE fund (451 pairs of
stems measured by FND-0244), so the family key built from it splits funds. The document names the fund in full, but NOT at its top: the stored texts are
the Spanish-catalogue extraction and open with the PRIIPs boilerplate ("Este documento le proporciona información fundamental ..."), so the name is found by
structure, never by position alone. Four generic rules, in this order, inside the first `WINDOW` characters (R-6); no fund or manager names (P#5):

  LABEL           a capitalised label (Producto / Denominación del producto / Nombre del fondo / Product / Produit / Produkt ...) followed by the name, up to
                  the first terminator (the "(el «Fondo»)" wrapper, "Clase"/"Class", "ISIN", "un subfondo de", "Sociedad Gestora", "Productor", "¿Qué es")
  ISIN_TRAIL      "... ISIN <isin> <Name> El «Fondo» ..." (M&G layout; before SUBFUND_CLAUSE because its text later carries a sub-fund sentence of prose)
  SUBFUND_CLAUSE  "<Name>[ (the "fund")][, Class X], a sub-fund of ..." with no label, in the first `_SUBFUND_MAX` characters (Capital Group, Algebris, BNP)
  CODE_HEAD       "<Name> Security code: ... ISIN: ..." right after the boilerplate's last sentence (DWS money-market layout)
  CLASS_OF        "Clase de participaciones RT (Divisa: EUR) del <Name>, un fondo ..." (Flossbach von Storch layout)
  ISIN_HEAD       the fund's own ISIN within the first 150 characters: the name is what precedes it (JPM layout)

A candidate must look like a name (starts with a capital or a digit, no sentence punctuation, not a sentence opener): "Producto se gestiona de manera pasiva"
and "Producto: carácter fundamental ..." are prose, not labels. A share-class tail is cut only after an explicit marker ("A (acc) - EUR", "A-EURO", "- I");
CURRENCY WORDS INSIDE THE NAME ARE KEPT: "Pictet - Short-Term Money Market USD" and "... CHF" are different sub-funds, and `fund_family_builder._normalize_name`
(which strips trailing currency codes) must not be the key of this name.

Pure functions, no database and no pipeline import (R-7). Measured on the 2,952 active class-1 KIID texts: scripts/audit/official_name_coverage.py."""
from __future__ import annotations

import re
import unicodedata
from typing import Optional, Tuple

WINDOW = 2600                     # characters of the collapsed text that are read (R-6)
_ISIN_HEAD_MAX = 150              # the own ISIN must sit this close to the start for ISIN_HEAD
_SUBFUND_MAX = 1000               # a sub-fund clause further in is the prose of the objectives, not the title (measured: M&G prose at 1,073)
_NAME_MIN, _NAME_MAX, _NAME_MAX_TOKENS = 4, 120, 14

RULE_LABEL, RULE_SUBFUND, RULE_ISIN_HEAD, RULE_ISIN_TRAIL, RULE_NONE = "LABEL", "SUBFUND_CLAUSE", "ISIN_HEAD", "ISIN_TRAIL", "NONE"
RULE_CODE_HEAD, RULE_CLASS_OF = "CODE_HEAD", "CLASS_OF"

_ISIN = r"[A-Z]{2}[A-Z0-9]{9}\d"
# Labels are case-sensitive on purpose: "este producto" in running prose is not a label, "Producto" / "PRODUCTO" opening the field is.
_LABEL_RE = re.compile(r"(?<![A-Za-zÀ-ÿ])(?:Denominaci[oó]n del producto|Nombre del (?:producto|fondo)|Nom du (?:produit|fonds)|"
                       r"Produkt|PRODUKT|Produit|PRODUIT|Producto|PRODUCTO|Product|PRODUCT)\b\s*:?\s+")
_QUOTE = "[«\"“'‘]"
_WRAPPER_RE = re.compile(r"\s*\(\s*(?:el|the|la|le|il|der|die|das)\s+" + _QUOTE + r".*$", re.IGNORECASE)
# The first of these ends the name that follows a label.
_TERM_RE = re.compile(
    r"\(\s*(?:el|the|la|le|il|der|die|das)\s+" + _QUOTE
    + r"|,\s*(?:Class|Clase|Classe|Acciones|Share\s+Class)\b"
    + r"|\s[-–]\s+(?:Class|Clase|Classe)\b"
    + r"|\b(?:Class|Clase|Classe)\b"
    + r"|\bISIN\b|\(" + _ISIN + r"\)|\b" + _ISIN + r"\b"
    + r"|\bun\s+(?:subfondo|compartimento|comparto)\b|\ba\s+sub-?fund\b"
    + r"|\bSociedad\s+Gestora\b|\bProductor\b|\bProducer\b|\bManufacturer\b|\bPRIIP\b"
    + r"|¿Qu[eé]\b|\bWhat\s+is\b|\bNombre\s+de\s+la\s+Clase\b|\s(?:Tipo|Type)\s+(?:El|Este|Esta|The|This)\b|\bNombre\s+del\b",
    re.IGNORECASE)
_SUBFUND_RE = re.compile(r"(?:,\s*|\s)(?:un\s+subfondo\s+de|a\s+sub-?fund\s+of|un\s+compartimento\s+de|un\s+comparto\s+di)\b", re.IGNORECASE)
_ISIN_TRAIL_RE = re.compile(r"\bISIN\s*:?\s*" + _ISIN + r"\s+(?P<name>.{4,140}?)\s+(?:El|The|Le|La)\s+" + _QUOTE)
_CLASS_OF_RE = re.compile(r"\b(?:Clase|Class)\s+de\s+(?:participaciones|acciones)\s+[A-Z0-9][\w-]*(?:\s*\([^)]{0,40}\))?\s+(?:del|de\s+la|de)\s+"
                          r"(?P<name>[A-Z0-9][^,.()]{3,100}?)(?=,|\.|\s+un\s+(?:fondo|subfondo)|\s*\(|$)")              # FvS "Clase de participaciones RT (Divisa: EUR) del <name>, un fondo"
_CODE_HEAD_RE = re.compile(r"(?:^|[.:?!]\s+)(?P<name>[A-Z0-9][^.:?!]{3,100}?)\s+(?:Security code|WKN|Código de valor)\b")      # DWS "<name> Security code: 971122 ISIN: ..."
_SENTENCE_BREAK_RE =re.compile(r"(?<=[.:?!])\s+(?=[A-Z0-9¿«\"])")
_NOT_A_NAME_START_RE = re.compile(r"^(?:This|These|It|Its|Este|Esta|Estos|Estas|Se|Es|Para|For|Que|Class|Clase|Classe|Acciones|Share|Documento|Document|"
                                  r"(?:El|La|Los|Las)\s+(?:Producto|Fondo|Subfondo|Compartimento|Sociedad))\b")
_NOT_A_NAME_END_RE = re.compile(r"\b(?:es|is|son|are)$", re.IGNORECASE)
_PROSE_RE = re.compile(r"[.?!¿]\s|[?¿]|https?:|www\.|\bISIN\b")

# explicit share-class tails (cut from the class letter on); a currency word that is part of the fund name is never one of these
_TAIL_PAREN_RE = re.compile(r"\s+[A-Z]{1,3}\d?\s*\((?:acc|dist|div|inc|perf|mdis|hedged|hdg|cap)\b[^)]*\).*$", re.IGNORECASE)
_TAIL_DASH_CODE_RE = re.compile(r"\s+[-–]\s+[A-Z0-9]{1,4}(?:\s+(?:EUR|USD|GBP|CHF|JPY|SEK|NOK|DKK|CAD|AUD|SGD|HKD|CNH|PLN))?(?:\s+(?:Hgd|Hedged))?$")
_TAIL_DASH_POLICY_CODE_RE = re.compile(r"\s+[-–]\s+[A-Z]{1,2}\s+(?:dy|mdy|dm|d|m)(?:\s+H?(?:EUR|USD|GBP|CHF|JPY))?$")      # Pictet "- P dy EUR", "- I dy EUR", "- P dy"
_TAIL_HYPHEN_CLASS_RE = re.compile(r"\s+[A-Z]\d?(?:-[A-Za-z]{1,12}(?:\(\w\))?){1,4}(?:\s*\([A-Za-z/ ]+\))?$")   # Fidelity "A-EURO", "A-MINCOME(G)-EURO", "A-ACC-EURO (EURO/USD HEDGED)"
_TAIL_NAMED_POLICY_RE = re.compile(r"[,\s]+[A-Z0-9-]{1,4}\s+[-–]\s+(?:Capitalizaci[oó]n|Distribuci[oó]n|Capitalisation|Distribution)\b.*$")   # Candriam "Oncology, C - Capitalización"
_TAIL_DASH_POLICY_RE = re.compile(r"\s+[-–]\s+[A-Z0-9]{1,3}\s+(?:Cap|Dis|Acc|Inc)\w*(?:\s+H?(?:EUR|USD|GBP|CHF|JPY))?.*$")  # Goldman "- P Cap EUR <name repeated>"
_TAIL_COMMA_CLASS_RE = re.compile(r",\s*[A-Z]\d?(?:-\d)?\s+(?:Acc|Inc|Dist)\w*.*$")                    # Aberdeen "Fund, X Acc Hedged EUR Participaciones"
_CCYX = r"H?(?:EUR|USD|GBP|CHF|JPY|SEK|NOK|DKK|CAD|AUD|SGD|HKD|CNH|PLN)"
_CLASS_WORD = r"(?:Hedged|Hdg|Acc\w*|Inc\w*|Dist\w*|Share|Capitalisation|" + _CCYX + r")"
_TAIL_ISIN_RE = re.compile(r"\s*\b" + _ISIN + r"\b.*$")                                     # "... A2 USD Acc LU0011963245": the class's own ISIN and all after it
_TAIL_LETTER_DIGIT_RE = re.compile(r"\s+[A-Z]\d{1,2}[a-z]?(?:\s+" + _CLASS_WORD + r")*$")   # "A2 EUR", "A5m USD", "A2 Hedged EUR Acc" (a class code with a digit)
_TAIL_LETTER_WORDS_RE = re.compile(r"\s+[A-Z]{1,2}(?:\s+" + _CLASS_WORD + r")+$")           # "D USD", "F EUR", "BC EUR Share" (a 1-2 letter class code, then class words)
_TAIL_CCY_CODE_RE = re.compile(r"\s+" + _CCYX + r"\s+[A-Z]{1,3}\d?(?:\s+(?:Acc|Inc|Dis|Dist)[A-Za-z]{0,3})?$")   # "EUR D AccU", "EUR D"
_TAIL_CCY_POLICY_RE = re.compile(r"\s+" + _CCYX + r"\s+(?:Acc|Inc|Dist)\w*$")               # "USD Acc"
_TAIL_FI_RE = re.compile(r"(?:,|\s)\s*(?:CL\.?\s*)?[A-Z0-9]{1,2},\s*FI$|\s+CL\.?\s*[A-Z0-9]+$")   # Spanish "DB CRECIMIENTO A, FI" / "DB CORTO PLAZO CL.A, FI"
_TAILS = (_TAIL_ISIN_RE, _TAIL_PAREN_RE, _TAIL_NAMED_POLICY_RE, _TAIL_DASH_POLICY_RE, _TAIL_COMMA_CLASS_RE, _TAIL_DASH_POLICY_CODE_RE, _TAIL_DASH_CODE_RE, _TAIL_HYPHEN_CLASS_RE,
          _TAIL_CCY_CODE_RE, _TAIL_LETTER_DIGIT_RE, _TAIL_LETTER_WORDS_RE, _TAIL_CCY_POLICY_RE, _TAIL_FI_RE)       # CCY_CODE before the letter rules: "EUR D AccU" is ONE tail
_LEADING_NOISE_RE = re.compile(r"^(?:\d{1,2}\s+de\s+\w+\s+de\s+\d{4}\s+|" + _ISIN + r"\s+)")             # "29 de junio de 2026 <name>", "LU1251922891 <name>"
_LEADING_LABEL_RE = re.compile(r"^(?:(?:Denominaci[oó]n del producto|Nombre del (?:producto|fondo)|Nombre|Producto|Product|PRODUCTO)\s*:?\s*)+")


def _collapse(text: str) -> str:
    return " ".join(text.split())


def clean_official_name(raw: Optional[str]) -> str:
    """The name without leaked labels, the "¿Qué es este producto?" that follows it, the "(el «Fondo»)" wrapper and an explicit share-class tail."""
    if not raw:
        return ""
    n = _collapse(raw).replace("‑", "-").replace("®", "").replace("’", "'")
    n = _LEADING_NOISE_RE.sub("", _LEADING_LABEL_RE.sub("", n))
    n = re.split(r"¿Qu[eé]\b|\bWhat is\b", n)[0]
    n = _WRAPPER_RE.sub("", n)
    for _ in range(3):                                           # a name can carry stacked tails ("A2 Hedged EUR Acc")
        before = n
        for rx in _TAILS:
            cut = rx.sub("", n)
            if len(cut) >= _NAME_MIN:
                n = cut
        if n == before:
            break
    n = n.strip(" ,;:•·-–[]\"'«»").lstrip("(").rstrip("(").strip()
    return n.rstrip(")").strip() if n.count(")") > n.count("(") else n      # keep the ")" that closes "(LUX)"; drop an unmatched one


def official_name_key(name: Optional[str]) -> str:
    """Comparison key of an official name: accent-folded, lowercase, alphanumerics only. Keeps currency words (see the module docstring)."""
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", name.lower())
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


def _plausible(name: str) -> bool:
    if not (_NAME_MIN <= len(name) <= _NAME_MAX) or len(name.split()) > _NAME_MAX_TOKENS:
        return False
    if len(name.split()) < 2 and len(name) < 8:                  # "Retail": a class label, not a fund name
        return False
    first = name.split()[0]
    if not (first[0].isdigit() or any(ch.isupper() for ch in first)):    # "iShares" is a name, "se" / "carácter" are prose
        return False
    if re.fullmatch(_ISIN, name):
        return False
    return not (_NOT_A_NAME_START_RE.match(name) or _NOT_A_NAME_END_RE.search(name) or _PROSE_RE.search(name))


def _from_label(h: str) -> Optional[str]:
    for m in _LABEL_RE.finditer(h):
        rest = h[m.end(): m.end() + 220]
        t = _TERM_RE.search(rest)
        if not t:
            continue
        name = clean_official_name(rest[: t.start()])
        if _plausible(name):
            return name
    return None


def _from_subfund_clause(h: str) -> Optional[str]:
    m = _SUBFUND_RE.search(h[:_SUBFUND_MAX])
    if not m:
        return None
    before = h[max(0, m.start() - 160): m.start()]
    parts = _SENTENCE_BREAK_RE.split(before)
    seg = parts[-1]
    seg = re.split(r",?\s*\b(?:Class|Clase|Classe)\b", seg, maxsplit=1)[0]
    name = clean_official_name(seg)
    return name if _plausible(name) else None


def _from_code_head(h: str) -> Optional[str]:
    m = _CODE_HEAD_RE.search(h[:_SUBFUND_MAX])
    if not m:
        return None
    name = clean_official_name(m.group("name"))
    return name if _plausible(name) else None


def _from_class_of(h: str) -> Optional[str]:
    m = _CLASS_OF_RE.search(h[:_SUBFUND_MAX])
    if not m:
        return None
    name = clean_official_name(m.group("name"))
    return name if _plausible(name) else None


def _from_isin_head(h: str, isin: Optional[str]) -> Optional[str]:
    m = re.search(r"\b" + (re.escape(isin) if isin else _ISIN) + r"\b", h[:_ISIN_HEAD_MAX + 14])
    if not m or m.start() <= 8 or m.start() > _ISIN_HEAD_MAX:
        return None
    name = clean_official_name(h[: m.start()].rstrip(" (-–:•·"))
    return name if _plausible(name) else None


def _from_isin_trail(h: str) -> Optional[str]:
    m = _ISIN_TRAIL_RE.search(h)
    if not m:
        return None
    name = clean_official_name(m.group("name"))
    return name if _plausible(name) else None


def extract_official_fund_name(text: Optional[str], isin: Optional[str] = None) -> Tuple[Optional[str], str]:
    """(official sub-fund name, rule tag) read from the head of a KIID text, or (None, "NONE"). `isin` is the fund's own ISIN when known (it anchors
    ISIN_HEAD; without it any ISIN literal does). The name is returned as printed (case and punctuation kept) minus label, wrapper and explicit class tail."""
    if not text:
        return None, RULE_NONE
    h = _collapse(text[:WINDOW * 2])[:WINDOW]
    for tag, fn in ((RULE_LABEL, _from_label), (RULE_ISIN_TRAIL, _from_isin_trail), (RULE_SUBFUND, _from_subfund_clause),
                    (RULE_CODE_HEAD, _from_code_head), (RULE_CLASS_OF, _from_class_of), (RULE_ISIN_HEAD, lambda s: _from_isin_head(s, isin))):
        name = fn(h)
        if name:
            return name, tag
    return None, RULE_NONE
