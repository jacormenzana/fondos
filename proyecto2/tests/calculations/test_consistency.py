import pandas as pd

from src.calculations.consistency import consistency_metrics


def test_consistency_nominal():
    nav_df = pd.DataFrame({
        "date": pd.date_range("2020-01-31", periods=6, freq="ME"),
        "nav": [100, 101, 99, 102, 101, 103],
    })

    results = consistency_metrics(nav_df)

    res = {(m, rf): v for m, v, rf in results}

    assert res[("pct_positive_months", 0)] == 0.6
    assert res[("pct_negative_months", 0)] == 0.4
    assert round(res[("worst_month", 0)], 4) == -0.0198
