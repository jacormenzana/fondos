import numpy as np
from src.calculations.returns import monthly_returns
from src.calculations.deflation import deflate_nav
from shared.config import SEVERE_LOSS_THRESHOLD


def consistency_metrics(nav_df, ipc_df=None):
    results = []

    # ---------- NOMINAL ----------
    r_nom = monthly_returns(nav_df["nav"])

    results.extend([
        ("pct_positive_months", (r_nom > 0).mean(), 0),
        ("pct_negative_months", (r_nom < 0).mean(), 0),
        ("pct_severe_loss_months", (r_nom <= SEVERE_LOSS_THRESHOLD).mean(), 0),
        ("worst_month", r_nom.min(), 0),
    ])

    # ---------- REAL ----------
    # Delegates to the canonical deflate_nav() (2026-09-18) -- this used to
    # reimplement its own inner-join-by-exact-date, the same bug class
    # root-caused and fixed in deflation.py: any NAV date without a
    # bit-for-bit matching IPC date was silently dropped from the real
    # series, not just misaligned.
    if ipc_df is not None and not ipc_df.empty:
        deflated = deflate_nav(nav_df, ipc_df)
        if not deflated.empty:
            r_real = monthly_returns(deflated["nav_real"])

            results.extend([
                ("pct_positive_months", (r_real > 0).mean(), 1),
                ("pct_negative_months", (r_real < 0).mean(), 1),
                ("pct_severe_loss_months", (r_real <= SEVERE_LOSS_THRESHOLD).mean(), 1),
                ("worst_month", r_real.min(), 1),
            ])

    return results
