# proyecto3/src/pit_inputs.py
# -*- coding: utf-8 -*-
"""
DB readers for the point-in-time backtester -- FND-0159 d3.

Read-only, SQL-scoped by ISIN list (`isins=None` = whole universe) instead of the old unfiltered full-history
pivot (FND-0191). Literal SQL only (the EXPLAIN sweep verifies it). NAV dates are kept RAW (P2 aligns IPC on
the raw date; see pit_metrics). Daily NAV is streamed in ISIN chunks: it is ~14M rows for the full universe.
"""

import sys
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.config import REGION_IPC
from shared.eur_nav import apply_eur_view_frame   # FND-0243

ATTRIBUTE_FRAME_COLUMNS = ["ISIN", "Fund_Name", "Fund_Nature", "srri_kiid", "Investment_Focus", "Credit_Quality",
                           "Ongoing_Charge", "SRRI_Quality_Flag", "fund_family_id", "Management_Company",
                           "In_Current_Universe"]


def load_nav_panel(conn: "psycopg.Connection", isins: "list | None" = None) -> pd.DataFrame:
    """Wide monthly NAV panel: raw-date DatetimeIndex x ISIN (NaN where a fund has no observation)."""
    if isins is None:
        rows = conn.execute("SELECT isin, date, nav FROM fund_nav_monthly ORDER BY date").fetchall()
    else:
        rows = conn.execute(
            "SELECT isin, date, nav FROM fund_nav_monthly WHERE isin = ANY(%s) ORDER BY date", (list(isins),)
        ).fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([tuple(r) for r in rows], columns=["isin", "date", "nav"])
    df["date"] = pd.to_datetime(df["date"])
    df["nav"] = df["nav"].astype(float)
    df = apply_eur_view_frame(conn, df)   # FND-0243: EUR view, identity while the switch is off
    if df.empty:
        return pd.DataFrame()
    wide = df.pivot_table(index="date", columns="isin", values="nav", aggfunc="last").sort_index()
    wide.columns.name = None
    return wide


def load_attributes(conn: "psycopg.Connection", isins: "list | None" = None) -> pd.DataFrame:
    """fund_master attributes for scoring, ALL funds (In_Current_Universe kept as a column, not filtered):
    the PIT universe includes retired funds (FND-0198). Static = today's values (documented look-ahead)."""
    if isins is None:
        rows = conn.execute("""
            SELECT ISIN, Fund_Name, Fund_Nature, SRRI AS srri_kiid, Investment_Focus, Credit_Quality,
                   Ongoing_Charge_Recurrent AS Ongoing_Charge, SRRI_Quality_Flag, fund_family_id,
                   Management_Company, In_Current_Universe
            FROM fund_master
        """).fetchall()
    else:
        rows = conn.execute("""
            SELECT ISIN, Fund_Name, Fund_Nature, SRRI AS srri_kiid, Investment_Focus, Credit_Quality,
                   Ongoing_Charge_Recurrent AS Ongoing_Charge, SRRI_Quality_Flag, fund_family_id,
                   Management_Company, In_Current_Universe
            FROM fund_master WHERE ISIN = ANY(%s)
        """, (list(isins),)).fetchall()
    df = pd.DataFrame([tuple(r) for r in rows], columns=ATTRIBUTE_FRAME_COLUMNS).set_index("ISIN")
    df.index.name = "isin"
    return df


def load_ipc(conn: "psycopg.Connection", geography: str = REGION_IPC) -> pd.DataFrame:
    """IPC index (series_inflation), date + ipc_index, month-end dated -- same as P2's load_ipc."""
    rows = conn.execute(
        "SELECT date, ipc_index FROM series_inflation WHERE geography = %s ORDER BY date", (geography,)
    ).fetchall()
    df = pd.DataFrame([tuple(r) for r in rows], columns=["date", "ipc_index"])
    df["date"] = pd.to_datetime(df["date"]) + pd.offsets.MonthEnd(0)
    df["ipc_index"] = df["ipc_index"].astype(float)
    return df


def load_rate_deposit(conn: "psycopg.Connection") -> pd.DataFrame:
    """ECB deposit rate (series_macro rate_deposit/EU) as date + rate (DECIMAL, e.g. 0.04) -- the risk-free
    rate of the Sharpe (P2 load_rf_rate) AND the yield of the backtest's cash leg (FND-0197)."""
    rows = conn.execute(
        "SELECT date, value FROM series_macro WHERE indicator = 'rate_deposit' AND geography = 'EU' ORDER BY date"
    ).fetchall()
    df = pd.DataFrame([tuple(r) for r in rows], columns=["date", "rate"])
    df["date"] = pd.to_datetime(df["date"]) + pd.offsets.MonthEnd(0)
    df["rate"] = df["rate"].astype(float) / 100.0
    return df


def iter_daily_chunks(conn: "psycopg.Connection", isins: list, chunk_size: int = 300):
    """Yield long frames [isin, date, nav] of daily NAV for successive ISIN chunks (bounded memory)."""
    isins = sorted(isins)
    for i in range(0, len(isins), chunk_size):
        part = isins[i:i + chunk_size]
        rows = conn.execute(
            "SELECT isin, date, nav FROM fund_nav_daily WHERE isin = ANY(%s) ORDER BY isin, date", (part,)
        ).fetchall()
        df = pd.DataFrame([tuple(r) for r in rows], columns=["isin", "date", "nav"])
        df["date"] = pd.to_datetime(df["date"])
        df["nav"] = df["nav"].astype(float)
        yield apply_eur_view_frame(conn, df)   # FND-0243: EUR view, identity while the switch is off
