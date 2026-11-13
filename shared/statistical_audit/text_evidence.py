"""Independent text evidence for the cost audit (owner decision 2026-11-06, FND-0276).

The rule ACI_AMORTISATION_ORDER (ACI_RHP <= ACI_1Y + tolerance) assumes the annual cost impact falls with the horizon because an entry fee amortises. Real KIDs
print a RISING pair ("0.8 % 0.9 % each year", "1.4% 1.5% each year": no entry fee, costs that grow), and a stored pair that rises is then FAITHFUL to the document, not an
extraction defect. The audit may only tolerate such a pair when the document itself vouches for it.

This check is deliberately independent of the extractor (no import from proyecto1): an audit that vouched for the extractor with the extractor's own functions would be
circular. It asks one question of the stored KID text: does it print exactly `<ACI_1Y>% <ACI_RHP>%`, in that order, next to the wording of an annual cost impact?
Pure; the audit runner supplies the text (only for the funds whose pair rises, a few dozen).
"""
from __future__ import annotations

import re

_CONTEXT_AFTER = re.compile(r"(?:cada\s+a[ñn]o|each\s+year|per\s+year|par\s+an|pro\s+jahr|p\.a\.)", re.IGNORECASE)
_CONTEXT_BEFORE = re.compile(r"(?:incidencia|impacto|impact|reducci[óo]n|reduction|annual\s+cost|rendimiento|yield|costes?|costs?)", re.IGNORECASE)
_AFTER_WINDOW = 40
_BEFORE_WINDOW = 400


def _number_pattern(value: float) -> str:
    """Regex for `value` as a KID prints a percentage: 3.3 -> 3[.,](?:30|3); 4.0 -> 4(?:[.,](?:00|0))?. Decimals are required when the value has them."""
    whole, dec = f"{float(value):.2f}".split(".")
    trimmed = dec.rstrip("0")
    if trimmed:
        return re.escape(whole) + r"[.,](?:" + re.escape(dec) + "|" + re.escape(trimmed) + ")"
    return re.escape(whole) + r"(?:[.,](?:00|0))?"


def aci_pair_printed(text: str | None, aci_1y: float | None, aci_rhp: float | None) -> bool:
    """True when `text` prints `<aci_1y>% <aci_rhp>%` (two consecutive percentages, only separators between them) followed within 40 characters by an
    "each year" wording or preceded within 400 characters by an annual-impact wording. False for a missing text or value. Pure."""
    if not text or aci_1y is None or aci_rhp is None:
        return False
    pair = re.compile(r"(?<![\d.,])" + _number_pattern(aci_1y) + r"\s*%\s*[\s,;/|–-]{0,6}" + _number_pattern(aci_rhp) + r"\s*%(?!\d)")
    for m in pair.finditer(text):
        if _CONTEXT_AFTER.search(text[m.end():m.end() + _AFTER_WINDOW]) or _CONTEXT_BEFORE.search(text[max(0, m.start() - _BEFORE_WINDOW):m.start()]):
            return True
    return False


_HORIZON_ROW = re.compile(
    r"(?:exit\s+after|salida\s+despu[eé]s\s+de|baja\s+despu[eé]s\s+de|retira\s+la\s+inversi[oó]n\s+despu[eé]s\s+de)\s*(\d{1,2})\s*(?:years?|a[nñ]os?)\s+"
    r"(?:(?:EUR|USD|GBP|CHF)\s*)?[\d.,\s]+(?:EUR|USD|GBP|CHF|€)?\s*(\d{1,2}[.,]\d{1,2})\s*%\s*(?:each\s*year|cada\s*a[nñ]o)",
    re.IGNORECASE,
)


def aci_horizon_row_printed(text: str | None, aci_rhp: float | None) -> bool:
    """True when `text` prints the stored `aci_rhp` as a row of its own that names a horizon of 2 years or more: "If you exit after 5 years 2,089 EUR 1.66% each year" (package B, 2026-11-13:
    the layout of the funds whose 5-year column sits apart from the annual-impact label, so the pair is never adjacent). Independent of the extractor on purpose (its own pattern, no import
    from proyecto1). False for a missing text or value. Pure."""
    if not text or aci_rhp is None:
        return False
    value = re.compile(r"\A" + _number_pattern(aci_rhp) + r"\Z")
    return any(int(m.group(1)) >= 2 and value.match(m.group(2)) for m in _HORIZON_ROW.finditer(text))


_COMPOSITION_WORDING = re.compile(
    r"\s*(?:del?\s+valor\s+de\s+su\s+inversi|of\s+the\s+value\s+of\s+your\s+investment|de\s+la\s+valeur\s+de\s+votre\s+investissement|des\s+wertes\s+ihrer\s+anlage)",
    re.IGNORECASE,
)


def management_row_printed(text: str | None, management_pct: float | None) -> bool:
    """True when `text` prints `<management_pct>%` followed by the normative composition wording ("1,62% del valor de su inversion por ano"): the management row of a PRIIPs KID (package B,
    2026-11-13). Independent of the extractor on purpose (its own pattern, no import from proyecto1). An ongoing charge that equals a management row the document prints is that row, not the
    annual cost impact copied into it. False for a missing text or value. Pure."""
    if not text or management_pct is None:
        return False
    value = re.compile(r"(?<![\d.,])" + _number_pattern(management_pct) + r"\s*%(?!\d)")
    return any(_COMPOSITION_WORDING.match(text, m.end()) for m in value.finditer(text))
