"""
shared/fx_exposure.py -- the currency exposure of a portfolio as a EUR investor bears it.

Owner decision 2026-10-11 (post-release analysis of the FND-0243 release): the P3 build concentrated 79% of the master weight in USD
classes with nothing measuring it. This module is the single definition of "net non-EUR exposure" (P#11) used by the build log, the
report and the promotion gate. Pure (R-7): rows in, numbers out; `load_attrs` is the only function that reads a database.

Rule per fund (what the EUR investor is exposed to):
  * class currency not EUR -> that currency, WHATEVER the fund hedges inside: a "USD hedged" class has removed the JPY risk of its
    assets but its NAV is quoted in USD, so the EUR investor still holds USD (shared/eur_nav.py converts it for the same reason);
  * EUR class, Hedged                          -> no exposure;
  * EUR class, not Hedged, non-EUR asset ccy   -> the asset currency ('MCY' = multi-currency, reported apart: composition unknown);
  * anything else                              -> none.
`Partially Hedged` counts as exposed (conservative: the hedged share is not stored).
"""
from __future__ import annotations

from typing import Iterable, Mapping, Optional

MCY = "MCY"


def fund_exposure_currency(fund_currency: Optional[str], hedging_policy: Optional[str],
                           asset_currency: Optional[str]) -> Optional[str]:
    """The currency the EUR investor is exposed to through one fund, or None."""
    fc = (fund_currency or "").upper().strip()
    if fc and fc != "EUR":
        return fc
    if (hedging_policy or "").strip().lower() == "hedged":
        return None
    ac = (asset_currency or "").upper().strip()
    if ac and ac != "EUR":
        return ac
    return None


def is_budgeted(ccy: Optional[str]) -> bool:
    """True when an exposure currency counts against the budget: any non-EUR currency except 'MCY' (summarize() reports MCY apart)."""
    return ccy is not None and ccy != MCY


def exposed_flags(attrs: Mapping[str, tuple], isins: Iterable[str]) -> dict:
    """{isin: bool} -- does the fund count against the budget. `attrs` is load_attrs()'s {isin: (fund_currency, hedging_policy, asset_currency)};
    an ISIN without a row is NOT exposed, exactly as summarize() sees it (the construction constraint and the promotion gate must agree)."""
    return {i: is_budgeted(fund_exposure_currency(*attrs.get(i, (None, None, None)))) for i in isins}


def exposure_by_currency(holdings: Iterable[tuple]) -> dict:
    """{currency: weight} from (weight, fund_currency, hedging_policy, asset_currency); weights in 0..1."""
    out: dict = {}
    for weight, fc, hp, ac in holdings:
        ccy = fund_exposure_currency(fc, hp, ac)
        if ccy is not None and weight:
            out[ccy] = out.get(ccy, 0.0) + float(weight)
    return out


def summarize(by_ccy: Mapping[str, float], budget: float) -> dict:
    """The KPI of a portfolio: `non_eur` EXCLUDES 'MCY' (unknown composition, shown apart); over_budget compares `non_eur` to `budget`."""
    non_eur = sum(w for c, w in by_ccy.items() if c != MCY)
    return {
        "by_currency": dict(sorted(by_ccy.items(), key=lambda kv: -kv[1])),
        "non_eur": non_eur,
        "mcy": by_ccy.get(MCY, 0.0),
        "budget": budget,
        "over_budget": non_eur > budget + 1e-9,
    }


def format_line(summary: Mapping) -> str:
    """One line for logs and the report cover."""
    parts = ", ".join(f"{c} {w:.1%}" for c, w in summary["by_currency"].items() if c != MCY) or "ninguna"
    mcy = f"; MCY {summary['mcy']:.1%}" if summary.get("mcy") else ""
    verdict = "EXCEDE" if summary["over_budget"] else "dentro de"
    return (f"Exposicion divisa no-EUR (neta, sin cobertura a EUR): {summary['non_eur']:.1%} "
            f"[{parts}{mcy}] -- {verdict} presupuesto {summary['budget']:.0%}")


def load_attrs(conn, isins: list) -> dict:
    """{isin: (fund_currency, hedging_policy, asset_currency)} from fund_master (read-only)."""
    if not isins:
        return {}
    rows = conn.execute(
        "SELECT isin, fund_currency, hedging_policy, asset_currency FROM fund_master WHERE isin = ANY(%s)",
        (list(isins),)).fetchall()
    return {r[0]: (r[1], r[2], r[3]) for r in rows}


def portfolio_summary(conn, weights: Mapping[str, float], budget: float) -> dict:
    """KPI of a {isin: master weight} portfolio read from fund_master."""
    attrs = load_attrs(conn, list(weights))
    holdings = [(w, *attrs.get(i, (None, None, None))) for i, w in weights.items()]
    return summarize(exposure_by_currency(holdings), budget)
