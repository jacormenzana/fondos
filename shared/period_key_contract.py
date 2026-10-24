"""The data contract R-9 rests on: which stamp convention each stored series actually uses (FND-0300).

R-9 says a calculation converts every stamp to the canonical period key at its boundary. That rule is only as good as the
assumption underneath it -- that we KNOW what convention each table holds. Measured on the live database, 2026-10-18:

  * `fund_nav_monthly`     28.7% of 672,554 rows are NOT on the calendar month-end (by year 2021-2026: 32.6 / 25.7 / 24.6 /
                           32.7 / 24.5 / 39.6%). 121 months carry two distinct raw dates ACROSS funds, and zero
                           `(isin, month)` pairs carry two rows -- so a per-fund series is clean and a PANEL index is not,
                           which is exactly how the PIT lookup came to see duplicate labels (b586ba2 / FND-0298).
  * `series_inflation`     963 of 963 rows on the month START.
  * `series_macro`         MIXED, and that is the finding this module exists to keep visible: the whole OLS factor set is
                           month-start, while `m2_global_yoy`, `m2_yoy` CN/JP/US and `fx_eom_eur` are month-END and
                           `fx_d_eur` is daily. `m2_yoy` itself is month-start for EU and month-end for CN/JP/US -- one
                           indicator, two conventions. A table-wide "series_macro is month-start" check would average that
                           away, so the contract is per indicator.

Pure functions only (R-7: testable with no connection). The caller runs the SQL and hands the counts in; the cycle report
(`scripts/launch/p1p2_cycle_report.py`) and the standing audit (`scripts/audit/period_key_invariance.py`) both do that, so
neither can drift from the other's idea of the contract. Same shape as `shared/macro_freshness.py`.

Nothing here BLOCKS: a convention change is a finding for a person to read, never a reason to stop a cycle.
"""
from __future__ import annotations

from typing import Iterable, Mapping

MONTH_START, MONTH_END, DAILY, MIXED = "month_start", "month_end", "daily", "mixed"

#: Baselines measured on the live database on 2026-10-18 (FND-0300). Drift against these is the signal; the numbers are a
#: snapshot of reality, not a target -- a NAV load legitimately moves the share a little every month.
BASELINE = {
    "nav_off_month_end_share": 0.287,
    "nav_duplicate_months": 121,
    "nav_isin_month_duplicates": 0,
}

#: How far the off-month-end share may drift before it is worth a person's attention. Wide on purpose: the share moved
#: between 24.5% and 39.6% across 2021-2026 purely because of which weekday each month ended on.
OFF_MONTH_END_DRIFT_TOLERANCE = 0.08

#: Expected stamp per `series_macro` indicator. An indicator absent from this map is reported as unknown rather than
#: assumed: a new macro series is a decision someone made, and R-9 wants it stated.
EXPECTED_STAMPS: dict[str, str] = {
    # the OLS factor set and its inputs -- month START
    "rate_deposit": MONTH_START, "rate_policy": MONTH_START, "m3_level": MONTH_START, "m3_yoy": MONTH_START,
    "ipc_index": MONTH_START, "oil_wti": MONTH_START, "copper": MONTH_START, "cli": MONTH_START,
    "dxy": MONTH_START, "gold": MONTH_START, "spread_hy": MONTH_START, "spread_ig": MONTH_START,
    "vix": MONTH_START, "term_spread": MONTH_START, "unemployment": MONTH_START,
    "fx_usd_eur": MONTH_START, "fx_jpy_usd": MONTH_START, "fx_usd_gbp": MONTH_START, "fx_cny_usd": MONTH_START,
    "m2_level": MONTH_START, "gdp_nom_eur": MONTH_START, "debt_gdp_pct": MONTH_START, "deficit_gdp_pct": MONTH_START,
    "yield_10y_gov": MONTH_START,                    # FND-0294: sovereign-stress monitor (monitor-only, not an OLS factor)
    # month END
    "m2_global_yoy": MONTH_END, "fx_eom_eur": MONTH_END,
    # FND-0293 left this one split: EU month-start, CN/JP/US month-end. Recorded as MIXED so the check reports the split
    # as the KNOWN state it is, instead of crying wolf every cycle -- and so that cleaning it up removes an entry here.
    "m2_yoy": MIXED,
    # daily
    "fx_d_eur": DAILY,
}


def classify_stamp(n_rows: int, n_month_start: int, n_month_end: int) -> str:
    """The convention a series actually follows, from the counts of its rows. `mixed` when neither label covers it."""
    if n_rows <= 0:
        return MIXED
    if n_month_start == n_rows:
        return MONTH_START
    if n_month_end == n_rows:
        return MONTH_END
    # A daily series lands on a month boundary about 2/30 of the time; anything that leaves most rows off both labels is
    # daily (or sub-monthly), not a monthly series with a few stragglers.
    return DAILY if (n_month_start + n_month_end) / n_rows < 0.25 else MIXED


def assess_macro_stamps(rows: Iterable[Mapping]) -> list[dict]:
    """One verdict per `series_macro` (indicator, geography).

    `rows`: mappings with `indicator`, `geography`, `n_rows`, `n_month_start`, `n_month_end`.
    """
    out = []
    for r in rows:
        observed = classify_stamp(int(r["n_rows"]), int(r["n_month_start"]), int(r["n_month_end"]))
        expected = EXPECTED_STAMPS.get(r["indicator"])
        if expected is None:
            state = "unknown"
        elif expected == MIXED or observed == expected:
            state = "ok"
        else:
            state = "unexpected"
        out.append({"indicator": r["indicator"], "geography": r["geography"], "n_rows": int(r["n_rows"]),
                    "observed": observed, "expected": expected, "state": state})
    return sorted(out, key=lambda d: (d["state"] != "unexpected", d["indicator"], d["geography"]))


def assess_nav(off_month_end_share: float, duplicate_months: int, isin_month_duplicates: int,
               baseline: Mapping | None = None) -> dict:
    """The `fund_nav_monthly` side of the contract."""
    base = dict(BASELINE, **(baseline or {}))
    drift = float(off_month_end_share) - float(base["nav_off_month_end_share"])
    return {
        "off_month_end_share": float(off_month_end_share),
        "off_month_end_drift": drift,
        "off_month_end_drifted": abs(drift) > OFF_MONTH_END_DRIFT_TOLERANCE,
        "duplicate_months": int(duplicate_months),
        "isin_month_duplicates": int(isin_month_duplicates),
        "isin_month_duplicates_unexpected": int(isin_month_duplicates) > int(base["nav_isin_month_duplicates"]),
        "baseline": base,
    }


def attention_lines(nav: Mapping, macro: Iterable[Mapping]) -> list[str]:
    """`[ATENCION]` texts for the cycle report (Spanish, like the rest of that report). Empty list = nothing to say."""
    lines: list[str] = []
    if nav.get("isin_month_duplicates_unexpected"):
        lines.append(
            f"{nav['isin_month_duplicates']} pares (isin, mes) con mas de una fila en fund_nav_monthly "
            f"(esperado {nav['baseline']['nav_isin_month_duplicates']}): una serie por fondo deja de tener una sola "
            f"observacion por mes y cualquier join por clave de periodo colapsa filas (R-9 regla 5)")
    if nav.get("off_month_end_drifted"):
        lines.append(
            f"la proporcion de filas de fund_nav_monthly fuera del fin de mes natural es {100 * nav['off_month_end_share']:.1f}% "
            f"({100 * nav['off_month_end_drift']:+.1f} pp frente a la referencia "
            f"{100 * nav['baseline']['nav_off_month_end_share']:.1f}%): revisar si la fuente cambio de convencion (R-9)")
    unexpected = [m for m in macro if m["state"] == "unexpected"]
    for m in unexpected[:6]:
        lines.append(
            f"series_macro {m['indicator']}/{m['geography']} esta sellada como {m['observed']} y el contrato dice "
            f"{m['expected']} (R-9): corregir la serie o actualizar EXPECTED_STAMPS con el motivo")
    if len(unexpected) > 6:
        lines.append(f"... y {len(unexpected) - 6} serie(s) mas de series_macro con una convencion de sello inesperada")
    unknown = [m for m in macro if m["state"] == "unknown"]
    if unknown:
        names = ", ".join(sorted({m["indicator"] for m in unknown})[:8])
        lines.append(f"{len(unknown)} serie(s) de series_macro sin convencion declarada en EXPECTED_STAMPS ({names}): "
                     f"declarar su sello antes de usarlas en un calculo (R-9)")
    return lines
