# shared/eur_nav.py
# -*- coding: utf-8 -*-
"""
EUR view of a share-class NAV (FND-0243): the platform's investor is EUR-domiciled, so every EUR-based metric
(real return vs ES CPI, Sharpe vs the EUR risk-free rate, volatility, drawdown, the P3 filters) must be computed
on the NAV a EUR investor experiences, not on the class-currency NAV stored in bronze.

Convert-on-read (owner decision D1): the stored NAV is never rewritten. Every NAV reader passes its frame through
`apply_eur_view` (one fund) or `convert_frame` (many funds); both are identities while
`shared.config.EUR_NAV_CONVERSION_ENABLED` is False.

Rules
- Class currency = fund_master.fund_currency (nav_currency is only a copy of it, verified 2026-10-09). Hedging
  policy does not enter: a EUR-hedged class already quotes in EUR; a USD-hedged class quotes in USD and the EUR
  investor bears EUR/USD.
- Rate = ECB reference rate, units of currency per 1 EUR, so NAV_EUR = NAV / rate. For every NAV date the rate is
  the latest ECB daily rate ON OR BEFORE that date (series_macro fx_d_eur): for a month-end NAV this is the rate of
  the month's last business day, i.e. the end-of-period monthly rate (FND-0241 convention), and it also covers
  last-business-day stamps, weekend daily rows and the open intra-month row. A currency without daily rates falls
  back to the end-of-period monthly series (fx_eom_eur, stamped on the calendar month-end). A NAV row whose
  nearest earlier rate is more than FX_MAX_GAP_DAYS old is dropped (no extrapolation).
- Unknown class currency (NULL, or a currency without rates) = the fund is EXCLUDED from EUR scoring (owner
  decision D3), never assumed EUR: the one-fund view returns an empty frame and says why in `df.attrs`.
"""
from __future__ import annotations

import pandas as pd

from shared import config

STATUS_EUR = "EUR"                     # class quotes in EUR, frame unchanged
STATUS_CONVERTED = "CONVERTED"         # every row converted
STATUS_PARTIAL = "CONVERTED_PARTIAL"   # some rows dropped: no rate within FX_MAX_GAP_DAYS
STATUS_UNKNOWN_CCY = "UNKNOWN_CCY"     # fund_currency NULL
STATUS_NO_FX = "NO_FX"                 # currency known but no ECB rates loaded for it
STATUS_DISABLED = "DISABLED"           # switch off: class-currency NAV as stored
EXCLUDED_STATUSES = (STATUS_UNKNOWN_CCY, STATUS_NO_FX)
ATTR = "eur_status"

FX_MAX_GAP_DAYS = 7

_SQL_FX = (
    "SELECT geography, date, value FROM series_macro "
    "WHERE indicator = %s AND geography = ANY(%s) AND value > 0 ORDER BY geography, date"
)
_SQL_CCY = "SELECT isin, fund_currency FROM fund_master"

_cache: dict = {}


def reset_cache() -> None:
    """Forget the rates and currencies read so far (tests; a long-lived process after a reload)."""
    _cache.clear()


# ----------------------------------------------------------------------------------------------------------------
# DB access (read-only, cached per process: ~30k rate rows, ~3.3k funds)
# ----------------------------------------------------------------------------------------------------------------

def load_rates(conn) -> dict[str, pd.DataFrame]:
    """{currency: DataFrame[date datetime64, rate float]} sorted by date; daily series, else end-of-period monthly."""
    if "rates" not in _cache:
        ccys = list(config.EUR_FX_CURRENCIES)
        out: dict[str, pd.DataFrame] = {}
        for indicator in (config.EUR_FX_DAILY_INDICATOR, config.EUR_FX_MONTHLY_INDICATOR):
            rows = conn.execute(_SQL_FX, (indicator, ccys)).fetchall()
            df = pd.DataFrame(rows, columns=["ccy", "date", "rate"])
            for ccy, g in df.groupby("ccy"):
                if ccy not in out:
                    out[ccy] = _norm_rates(g[["date", "rate"]])
        _cache["rates"] = out
    return _cache["rates"]


def load_fund_currencies(conn) -> dict[str, str | None]:
    if "ccy" not in _cache:
        _cache["ccy"] = {r[0]: r[1] for r in conn.execute(_SQL_CCY).fetchall()}
    return _cache["ccy"]


def _norm_rates(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df["rate"] = df["rate"].astype(float)
    return df.sort_values("date").reset_index(drop=True)


# ----------------------------------------------------------------------------------------------------------------
# Pure conversion
# ----------------------------------------------------------------------------------------------------------------

def to_eur(nav_df: pd.DataFrame, ccy: str | None, rates: dict[str, pd.DataFrame],
           max_gap_days: int = FX_MAX_GAP_DAYS) -> tuple[pd.DataFrame, str, int]:
    """
    One fund: (frame[date, nav] in EUR, status, rows dropped). Pure; `rates` as returned by load_rates.
    An excluded fund (unknown currency / no rates) returns an EMPTY frame with the same columns.
    """
    cols = list(nav_df.columns)
    if ccy is None:
        return nav_df.iloc[0:0].copy(), STATUS_UNKNOWN_CCY, len(nav_df)
    ccy = ccy.upper()
    if ccy == "EUR":
        return nav_df, STATUS_EUR, 0
    fx = rates.get(ccy)
    if fx is None or fx.empty:
        return nav_df.iloc[0:0].copy(), STATUS_NO_FX, len(nav_df)
    if nav_df.empty:
        return nav_df, STATUS_CONVERTED, 0
    left = nav_df.copy()
    left["_d"] = pd.to_datetime(left["date"])
    left = left.sort_values("_d")
    m = pd.merge_asof(left, fx.rename(columns={"date": "_d"}), on="_d", direction="backward",
                      tolerance=pd.Timedelta(days=max_gap_days))
    dropped = int(m["rate"].isna().sum())
    m = m[m["rate"].notna()].copy()
    m["nav"] = m["nav"].astype(float) / m["rate"]
    out = m[cols].reset_index(drop=True)
    return out, (STATUS_PARTIAL if dropped else STATUS_CONVERTED), dropped


def convert_frame(df: pd.DataFrame, ccy_map: dict[str, str | None], rates: dict[str, pd.DataFrame],
                  isin_col: str = "isin", max_gap_days: int = FX_MAX_GAP_DAYS) -> pd.DataFrame:
    """
    Many funds: frame[isin, date, nav, ...] -> the same frame in EUR. Funds whose class currency is unknown or has
    no rates are DROPPED (excluded, D3); rows without a rate within max_gap_days are dropped. Pure.
    """
    if df.empty:
        return df
    cols = list(df.columns)
    d = df.copy()
    d["_ccy"] = d[isin_col].map(ccy_map).str.upper()
    eur = d[d["_ccy"] == "EUR"]
    other = d[d["_ccy"].isin([c for c, r in rates.items() if r is not None and not r.empty])]
    parts = [eur[cols]]
    if not other.empty:
        # ONE merge for every fund (by currency): peer readers call this per fund over ~300k rows, so a per-fund
        # loop would multiply the cost by the size of the peer group.
        fx = pd.concat([r.assign(_ccy=c) for c, r in rates.items() if r is not None and not r.empty],
                       ignore_index=True).rename(columns={"date": "_d"}).sort_values("_d")
        left = other.assign(_d=pd.to_datetime(other["date"]), _row=range(len(other))).sort_values("_d")
        m = pd.merge_asof(left, fx, on="_d", by="_ccy", direction="backward",
                          tolerance=pd.Timedelta(days=max_gap_days))
        m = m[m["rate"].notna()].copy()
        m["nav"] = m["nav"].astype(float) / m["rate"]
        parts.append(m.sort_values("_row")[cols])
    out = pd.concat(parts, ignore_index=True)
    return out.sort_values([isin_col, "date"], kind="stable").reset_index(drop=True) if "date" in cols else out


# ----------------------------------------------------------------------------------------------------------------
# Reader entry points (identity while the switch is off)
# ----------------------------------------------------------------------------------------------------------------

def apply_eur_view(conn, isin: str, nav_df: pd.DataFrame) -> pd.DataFrame:
    """One fund's frame[date, nav] as a EUR investor sees it; `df.attrs[ATTR]` carries the status."""
    if not config.EUR_NAV_CONVERSION_ENABLED:
        nav_df.attrs[ATTR] = STATUS_DISABLED
        return nav_df
    out, status, _dropped = to_eur(nav_df, load_fund_currencies(conn).get(isin), load_rates(conn))
    out.attrs[ATTR] = status
    return out


def apply_eur_view_frame(conn, df: pd.DataFrame, isin_col: str = "isin") -> pd.DataFrame:
    """Many funds' frame[isin, date, nav, ...] as a EUR investor sees it (excluded funds dropped)."""
    if not config.EUR_NAV_CONVERSION_ENABLED:
        return df
    return convert_frame(df, load_fund_currencies(conn), load_rates(conn), isin_col=isin_col)


class ReleaseCouplingError(RuntimeError):
    """The EUR view and the switches it depends on are in an inconsistent state (owner decision D4)."""


def release_coupling_errors(cfg=config) -> list[str]:
    """Pure: why the current switch combination may not run (empty = consistent).

    - EUR_NAV_CONVERSION_ENABLED == FX_CONTRIBUTION_EUR_VIEW_ENABLED (owner directive 2026-10-07, "neither may be enabled
      alone"): with converted NAV the FND-0235 view must not convert the class currency again, and without it the FX view
      would be computed on class-currency returns while everything else is in EUR.
    - EUR view ON => PERSISTENCE_FIRST_LAST_NAV_ENABLED: the legacy MIN/MAX peer return has no date to convert at."""
    errors = []
    if cfg.EUR_NAV_CONVERSION_ENABLED != cfg.FX_CONTRIBUTION_EUR_VIEW_ENABLED:
        errors.append(f"EUR_NAV_CONVERSION_ENABLED={cfg.EUR_NAV_CONVERSION_ENABLED} but "
                      f"FX_CONTRIBUTION_EUR_VIEW_ENABLED={cfg.FX_CONTRIBUTION_EUR_VIEW_ENABLED}: FND-0243 and FND-0235 are "
                      f"released together, neither may be enabled alone")
    if cfg.EUR_NAV_CONVERSION_ENABLED and not cfg.PERSISTENCE_FIRST_LAST_NAV_ENABLED:
        errors.append("EUR_NAV_CONVERSION_ENABLED requires PERSISTENCE_FIRST_LAST_NAV_ENABLED (MIN/MAX peer NAVs carry no "
                      "date to convert at)")
    return errors


def assert_release_coupling() -> None:
    """Called first by every P2/P3 entry point, before any connection or write: refuse an inconsistent switch state."""
    errors = release_coupling_errors()
    if errors:
        raise ReleaseCouplingError("Refusing to run (FND-0243 D4): " + "; ".join(errors))


def is_excluded(nav_df: pd.DataFrame) -> bool:
    """True when apply_eur_view excluded the fund (class currency unknown or without rates)."""
    return nav_df.attrs.get(ATTR) in EXCLUDED_STATUSES
