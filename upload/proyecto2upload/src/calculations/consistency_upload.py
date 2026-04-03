import numpy as np
from src.calculations.returns import monthly_returns

SEVERE_LOSS_THRESHOLD = -0.02


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
    if ipc_df is not None:
        df = nav_df.merge(ipc_df, on="date", how="inner")
        nav_real = df["nav"] / df["ipc_index"]
        r_real = monthly_returns(nav_real)

        results.extend([
            ("pct_positive_months", (r_real > 0).mean(), 1),
            ("pct_negative_months", (r_real < 0).mean(), 1),
            ("pct_severe_loss_months", (r_real <= SEVERE_LOSS_THRESHOLD).mean(), 1),
            ("worst_month", r_real.min(), 1),
        ])

    return results
