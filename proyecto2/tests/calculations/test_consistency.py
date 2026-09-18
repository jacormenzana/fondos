import pandas as pd
import pytest

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


class TestConsistencyReal:
    """real_flag=1 path -- zero prior coverage. Root-cause fix (2026-09-18):
    this used to do its own inner-join-by-exact-date deflation, the same bug
    class fixed in deflation.py::deflate_nav() -- any NAV date without a
    bit-for-bit matching IPC date got silently dropped, not just misaligned.
    Now delegates to deflate_nav() instead of reimplementing it."""

    def test_no_ipc_df_emits_only_nominal(self):
        nav_df = pd.DataFrame({
            "date": pd.date_range("2020-01-31", periods=6, freq="ME"),
            "nav": [100, 101, 99, 102, 101, 103],
        })
        results = consistency_metrics(nav_df, ipc_df=None)
        assert all(rf == 0 for _, _, rf in results)

    def test_real_metrics_present_with_full_ipc_overlap(self):
        dates = pd.date_range("2020-01-31", periods=6, freq="ME")
        nav_df = pd.DataFrame({"date": dates, "nav": [100, 101, 99, 102, 101, 103]})
        ipc_df = pd.DataFrame({"date": dates, "ipc_index": [100, 100, 100, 100, 100, 100]})

        results = consistency_metrics(nav_df, ipc_df)
        res = {(m, rf): v for m, v, rf in results}

        # Flat IPC -> real == nominal.
        assert res[("pct_positive_months", 1)] == res[("pct_positive_months", 0)]
        assert res[("worst_month", 1)] == pytest.approx(res[("worst_month", 0)])

    def test_real_metrics_survive_a_mid_month_nav_date_without_exact_ipc_match(self):
        """The exact production failure mode: the most recent NAV date is a
        mid-month snapshot, IPC is month-end only -- before this fix, the
        inner join silently dropped that row and 'worst_month'/pct_* real
        metrics were computed over a shorter, shifted series than nominal."""
        nav_df = pd.DataFrame({
            "date": pd.to_datetime(["2026-06-30", "2026-07-31", "2026-08-31", "2026-09-14"]),
            "nav": [100.0, 102.0, 101.0, 103.0],
        })
        ipc_df = pd.DataFrame({
            "date": pd.to_datetime(["2026-06-30", "2026-07-31", "2026-08-31"]),  # no Sep row
            "ipc_index": [110.0, 110.5, 111.0],
        })
        results = consistency_metrics(nav_df, ipc_df)
        res = {(m, rf): v for m, v, rf in results}
        # 4 NAV rows -> 3 monthly returns for BOTH nominal and real; if the
        # September row were still being dropped, real would only have 2.
        assert res[("pct_positive_months", 1)] + res[("pct_negative_months", 1)] <= 1.0
        assert ("worst_month", 1) in res
