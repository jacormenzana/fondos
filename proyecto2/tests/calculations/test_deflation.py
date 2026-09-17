# proyecto2/tests/calculations/test_deflation.py
# -*- coding: utf-8 -*-
"""
Tests para deflation.py::deflate_nav (root-cause fix, 2026-09-17).

Cumple R-7: sin importar pipeline.py ni core.io.

Root cause: deflate_nav() usaba un INNER JOIN exacto por fecha
(nav_df.merge(ipc_df, on='date', how='inner')) -- cualquier fecha NAV sin
una fecha IPC EXACTAMENTE coincidente se descartaba en silencio. En
produccion, la fecha NAV mas reciente suele ser una foto a mitad de mes
(ej. '2026-09-14') mientras que series_inflation esta normalizado a fin de
mes por load_ipc() (ej. '2026-09-30') -- esa fecha nunca cruzaba, asi que
el path escalar (_process_horizon -> compute_risk_metrics -> deflate_nav)
perdia sistematicamente su punto NAV mas reciente en TODO calculo
real_flag=1, en TODOS los horizontes. Root-caused via el motor de auditoria
estadistica: SCALAR_EQUALS_TIMESERIES mostraba real_flag=0 coincidiendo casi
exacto entre fund_metrics y fund_metric_timeseries, pero real_flag=1
divergiendo ~50% -- el path rolling (rolling_stats.compute_rolling_rows) ya
alineaba con reindex+ffill+bfill (nunca descarta una fecha), igual que
short_horizon.py::_deflate_nav().

Estos tests pinnean el comportamiento correcto (nunca descartar una fecha
NAV) ANTES del fix -- deben fallar contra la version vieja (inner join) y
pasar contra la nueva (reindex + ffill + bfill).
"""

import numpy as np
import pandas as pd
import pytest

from src.calculations.deflation import deflate_nav


def _df(dates, values, col):
    return pd.DataFrame({"date": pd.to_datetime(dates), col: values})


class TestNeverDropsANavDate:
    def test_mid_month_nav_date_without_exact_ipc_match_is_kept(self):
        """The exact production failure mode: NAV has a mid-month snapshot
        that IPC (month-end only) has no exact match for."""
        nav_df = _df(
            ["2026-06-30", "2026-07-31", "2026-08-31", "2026-09-14"],
            [100.0, 102.0, 101.0, 103.0],
            "nav",
        )
        ipc_df = _df(
            ["2026-06-30", "2026-07-31", "2026-08-31"],  # no September row at all
            [110.0, 110.5, 111.0],
            "ipc_index",
        )
        result = deflate_nav(nav_df, ipc_df)
        assert len(result) == len(nav_df), (
            "the 2026-09-14 row must be kept via ffill, not silently dropped"
        )
        assert pd.Timestamp("2026-09-14") in set(result["date"])

    def test_all_dates_kept_even_with_partial_ipc_gaps(self):
        nav_df = _df(
            ["2026-01-31", "2026-02-28", "2026-03-31", "2026-04-30"],
            [100.0, 101.0, 99.0, 102.0],
            "nav",
        )
        ipc_df = _df(
            ["2026-01-31", "2026-03-31"],  # February missing entirely
            [100.0, 101.0],
            "ipc_index",
        )
        result = deflate_nav(nav_df, ipc_df)
        assert len(result) == 4
        assert result["nav_real"].notna().all()


class TestDeflationCorrectness:
    def test_flat_ipc_leaves_nav_shape_unchanged(self):
        """Constant IPC -> deflator is always 1.0 -> nav_real == nav."""
        nav_df = _df(["2026-01-31", "2026-02-28", "2026-03-31"], [100.0, 105.0, 98.0], "nav")
        ipc_df = _df(["2026-01-31", "2026-02-28", "2026-03-31"], [100.0, 100.0, 100.0], "ipc_index")
        result = deflate_nav(nav_df, ipc_df)
        np.testing.assert_allclose(result["nav_real"].to_numpy(), nav_df["nav"].to_numpy())

    def test_rising_ipc_reduces_real_return(self):
        """10% nominal NAV growth against 10% IPC growth over the same
        period -> ~0% real return (classic deflation check)."""
        nav_df = _df(["2026-01-31", "2026-12-31"], [100.0, 110.0], "nav")
        ipc_df = _df(["2026-01-31", "2026-12-31"], [100.0, 110.0], "ipc_index")
        result = deflate_nav(nav_df, ipc_df)
        real_return = result["nav_real"].iloc[-1] / result["nav_real"].iloc[0] - 1.0
        assert real_return == pytest.approx(0.0, abs=1e-9)

    def test_return_ratio_is_independent_of_rebase_choice(self):
        """Regression guard: the specific rebase convention (dividing by
        ipc_aligned/ipc_base vs. raw ipc_index) must not matter for any
        RATIO-based downstream metric (return_ann, vol_ann, sharpe, sortino,
        max_dd are all ratio/return-based) -- only the absolute nav_real
        values differ, and those are never persisted directly. This is why
        switching deflate_nav()'s rebase convention (to match
        short_horizon.py / rolling_stats.py) was safe."""
        nav_df = _df(["2026-01-31", "2026-06-30", "2026-12-31"], [100.0, 108.0, 115.0], "nav")
        ipc_df = _df(["2026-01-31", "2026-06-30", "2026-12-31"], [102.3, 103.1, 104.0], "ipc_index")
        result = deflate_nav(nav_df, ipc_df)
        nav_real = result["nav_real"].to_numpy()

        # Manually deflate with the OLD (non-rebased) convention for comparison.
        raw_deflated = nav_df["nav"].to_numpy() / ipc_df["ipc_index"].to_numpy()

        ratio_new = nav_real[-1] / nav_real[0]
        ratio_old = raw_deflated[-1] / raw_deflated[0]
        assert ratio_new == pytest.approx(ratio_old, rel=1e-9)


class TestNoOverlap:
    def test_empty_ipc_df_returns_empty_frame(self):
        nav_df = _df(["2026-01-31", "2026-02-28"], [100.0, 101.0], "nav")
        result = deflate_nav(nav_df, pd.DataFrame(columns=["date", "ipc_index"]))
        assert result.empty

    def test_none_ipc_df_raises_or_empty(self):
        """Callers already guard with `if ipc_df is not None and not ipc_df.empty`
        before calling deflate_nav -- but the function itself should degrade
        gracefully (empty result) rather than raise, matching the empty-df case."""
        nav_df = _df(["2026-01-31", "2026-02-28"], [100.0, 101.0], "nav")
        result = deflate_nav(nav_df, None)
        assert result.empty
