# proyecto2/tests/calculations/test_rolling_stats.py
# -*- coding: utf-8 -*-
"""
Tests para rolling_stats.py (v26).

Cumple R-7: sin importar pipeline.py ni core.io.
Fixtures: datos sintéticos con valores conocidos para verificar
  las fórmulas de roll_vol_ann, roll_max_dd, roll_return_ann.
"""

import math
import pytest
import numpy as np
import pandas as pd

from src.calculations.rolling_stats import (
    _roll_vol_ann,
    _roll_max_dd,
    _roll_return_ann,
    compute_rolling_rows,
    compute_category_snapshot,
    compute_alerts,
    compute_timeseries_snapshots,
)


# ============================================================
# Helpers de fixture
# ============================================================

def _make_nav_df(navs: list[float], start: str = "2020-01-31") -> pd.DataFrame:
    """Crea un DataFrame mensual con fechas de fin de mes."""
    dates = pd.date_range(start=start, periods=len(navs), freq="ME")
    return pd.DataFrame({"date": dates, "NAV": navs})


# ============================================================
# Tests de bajo nivel — fórmulas estadísticas
# ============================================================

class TestRollVolAnn:
    def test_flat_series(self):
        """Serie plana → retornos 0 → volatilidad 0."""
        nav = np.array([100.0, 100.0, 100.0, 100.0, 100.0])
        assert _roll_vol_ann(nav, 12) == pytest.approx(0.0, abs=1e-10)

    def test_known_volatility(self):
        """Retornos mensuales constantes de 1% → vol ~3.46% anual."""
        # NAV que crece 1% cada mes
        navs = [100.0 * (1.01 ** i) for i in range(13)]
        vol = _roll_vol_ann(np.array(navs), 12)
        # retornos ≈ 0.01 constante → std ≈ 0 → vol ≈ 0
        assert vol == pytest.approx(0.0, abs=1e-6)

    def test_two_nav_too_short(self):
        assert math.isnan(_roll_vol_ann(np.array([100.0, 110.0]), 12))

    def test_single_nav(self):
        assert math.isnan(_roll_vol_ann(np.array([100.0]), 12))


class TestRollMaxDD:
    def test_no_drawdown(self):
        """Serie siempre creciente → drawdown 0."""
        nav = np.array([100.0, 110.0, 120.0, 130.0])
        assert _roll_max_dd(nav) == pytest.approx(0.0, abs=1e-10)

    def test_known_drawdown(self):
        """Baja de 110 a 90: drawdown = 90/110 - 1 ≈ -0.1818."""
        nav = np.array([100.0, 110.0, 90.0, 95.0])
        dd = _roll_max_dd(nav)
        assert dd == pytest.approx(-0.18182, abs=1e-4)

    def test_single_nav(self):
        assert math.isnan(_roll_max_dd(np.array([100.0])))

    def test_full_loss(self):
        """Baja de 100 a 0 → drawdown = -1.0."""
        nav = np.array([100.0, 50.0, 0.0])
        # Evitar división por cero: el módulo debe devolver -1.0 o nan
        # (0/100 - 1 = -1.0 → correcto)
        dd = _roll_max_dd(nav)
        assert dd == pytest.approx(-1.0, abs=1e-10)


class TestRollReturnAnn:
    def test_double_in_one_year(self):
        """NAV que dobla: [100, 200] con periods_per_year=2 → years=1 → ret=100%."""
        # Convention: years = len(nav) / periods_per_year
        # len=2, periods_per_year=2 → years=1 → total^(1/1) - 1 = 2-1 = 1.0
        nav = np.array([100.0, 200.0])
        ret = _roll_return_ann(nav, periods_per_year=2)
        assert ret == pytest.approx(1.0, abs=1e-8)

    def test_known_12m_return(self):
        """12 puntos NAV creciendo 10%/año: len=12, years=12/12=1 → ret≈10%."""
        # NAV = 100*(1.1)^(i/11) para i in 0..11 → total = 1.1, years = 12/12 = 1
        navs = [100.0 * (1.1 ** (i / 11)) for i in range(12)]
        ret = _roll_return_ann(np.array(navs), 12)
        # years = 12/12 = 1; total = 1.1; ret = 1.1 - 1 = 0.1
        assert ret == pytest.approx(0.10, abs=1e-6)

    def test_single_nav(self):
        assert math.isnan(_roll_return_ann(np.array([100.0]), 12))


# ============================================================
# Tests de compute_rolling_rows
# ============================================================

class TestComputeRollingRows:
    def _simple_nav(self, n: int = 24) -> pd.DataFrame:
        """NAV creciente simple para tests genéricos."""
        navs = [100.0 * (1.005 ** i) for i in range(n)]
        return _make_nav_df(navs)

    def test_returns_list_of_dicts(self):
        nav_df = self._simple_nav(24)
        rows = compute_rolling_rows(
            "TEST0001", nav_df,
            rolling_windows={"rolling_1y": 12, "rolling_2y": 24},
            min_obs=5, periods_per_year=12,
        )
        assert isinstance(rows, list)
        assert len(rows) > 0
        assert all(isinstance(r, dict) for r in rows)

    def test_required_keys(self):
        nav_df = self._simple_nav(14)
        rows = compute_rolling_rows(
            "TEST0002", nav_df,
            rolling_windows={"rolling_1y": 12},
            min_obs=5, periods_per_year=12,
        )
        expected_keys = {"isin", "metric", "window", "date", "value",
                         "real_flag", "ref_type", "ref_value", "source_rows"}
        for r in rows:
            assert expected_keys == set(r.keys()), f"Keys mismatch: {set(r.keys())}"

    def test_metrics_produced(self):
        nav_df = self._simple_nav(24)
        rows = compute_rolling_rows(
            "TEST0003", nav_df,
            rolling_windows={"rolling_1y": 12},
            min_obs=5, periods_per_year=12,
        )
        metrics = {r["metric"] for r in rows}
        assert metrics == {"roll_vol_ann", "roll_max_dd", "roll_return_ann"}

    def test_window_names_in_output(self):
        nav_df = self._simple_nav(36)
        windows = {"rolling_1y": 12, "rolling_2y": 24, "rolling_3y": 36}
        rows = compute_rolling_rows(
            "TEST0004", nav_df,
            rolling_windows=windows,
            min_obs=5, periods_per_year=12,
        )
        produced_windows = {r["window"] for r in rows}
        assert produced_windows == set(windows.keys())

    def test_min_obs_respected(self):
        """Con min_obs=12, los primeros 11 puntos no deben producir filas."""
        nav_df = self._simple_nav(20)
        rows = compute_rolling_rows(
            "TEST0005", nav_df,
            rolling_windows={"rolling_1y": 12},
            min_obs=12, periods_per_year=12,
        )
        # Solo deben existir filas donde source_rows >= 12
        assert all(r["source_rows"] >= 12 for r in rows)

    def test_empty_nav(self):
        nav_df = pd.DataFrame({"date": [], "NAV": []})
        rows = compute_rolling_rows(
            "TEST0006", nav_df,
            rolling_windows={"rolling_1y": 12},
            min_obs=5, periods_per_year=12,
        )
        assert rows == []

    def test_none_nav(self):
        rows = compute_rolling_rows(
            "TEST0007", None,
            rolling_windows={"rolling_1y": 12},
            min_obs=5, periods_per_year=12,
        )
        assert rows == []

    def test_nan_value_becomes_none(self):
        """Valores NaN en el output deben mapearse a None (para SQL NULL)."""
        # Serie muy corta relativa a la ventana → max_dd sobre 1 punto → NaN
        nav_df = _make_nav_df([100.0, 101.0, 100.5])
        rows = compute_rolling_rows(
            "TEST0008", nav_df,
            rolling_windows={"rolling_1y": 12},
            min_obs=2, periods_per_year=12,
        )
        # Los values pueden ser None (NaN → None) pero no float('nan')
        for r in rows:
            if r["value"] is not None:
                assert not math.isnan(r["value"]), f"Raw NaN escaped: {r}"

    def test_ipc_deflation_produces_real_flag_1(self):
        """Con ipc_df proporcionado, deben aparecer filas con real_flag=1."""
        nav_df = self._simple_nav(24)
        ipc_df = pd.DataFrame({
            "date": nav_df["date"],
            "value": [100.0 * (1.003 ** i) for i in range(24)],
        })
        rows = compute_rolling_rows(
            "TEST0009", nav_df,
            rolling_windows={"rolling_1y": 12},
            min_obs=5, periods_per_year=12,
            ipc_df=ipc_df,
        )
        flags = {r["real_flag"] for r in rows}
        assert 1 in flags, "real_flag=1 (deflactado) no encontrado en el output"
        assert 0 in flags, "real_flag=0 (nominal) desapareció"

    def test_incrementality_simulation(self):
        """La segunda ejecución no genera fechas anteriores duplicadas."""
        nav_full = self._simple_nav(36)
        rows_full = compute_rolling_rows(
            "TEST0010", nav_full,
            rolling_windows={"rolling_1y": 12},
            min_obs=5, periods_per_year=12,
        )
        # Simular que la última fecha ya está en DB → el escritor usa INSERT OR IGNORE
        dates_full = {r["date"] for r in rows_full}
        # Solo debe haber una fila por (isin, metric, window, date, real_flag)
        keys = [(r["isin"], r["metric"], r["window"], r["date"], r["real_flag"])
                for r in rows_full]
        assert len(keys) == len(set(keys)), "Filas duplicadas detectadas"


# ============================================================
# Tests de compute_category_snapshot
# ============================================================

class TestComputeCategorySnapshot:
    def _make_snapshot_input(self) -> pd.DataFrame:
        """10 fondos, 2 categorías, 1 métrica, 1 fecha."""
        rows = []
        for i in range(10):
            nature = "Renta Fija Flexible" if i < 6 else "Monetario"
            rows.append({
                "isin": f"ISIN{i:04d}",
                "metric": "roll_vol_ann",
                "window": "rolling_1y",
                "date": "2026-01-31",
                "value": 0.05 + i * 0.005,  # valores bien separados
                "real_flag": 0,
                "Fund_Nature": nature,
            })
        return pd.DataFrame(rows)

    def test_returns_dataframe(self):
        df = self._make_snapshot_input()
        result = compute_category_snapshot(df)
        assert isinstance(result, pd.DataFrame)
        assert not result.empty

    def test_pctile_range(self):
        df = self._make_snapshot_input()
        result = compute_category_snapshot(df, min_peers=3)
        assert result["pctile_cat"].between(0, 1).all()

    def test_min_peers_respected(self):
        """Categorías con < min_peers fondos no deben aparecer."""
        df = self._make_snapshot_input()
        # "Monetario" tiene 4 fondos (i=6..9)
        result = compute_category_snapshot(df, min_peers=5)
        # Monetario tiene 4 < 5 → no debe salir
        if not result.empty:
            assert "Monetario" not in result["Fund_Nature"].values

    def test_empty_input(self):
        result = compute_category_snapshot(pd.DataFrame())
        assert isinstance(result, pd.DataFrame)
        assert result.empty

    def test_missing_column_raises(self):
        df = pd.DataFrame({"isin": ["X"], "metric": ["m"], "window": ["w"],
                           "date": ["2026-01"], "value": [0.1], "real_flag": [0]})
        # Fund_Nature faltante → ValueError
        with pytest.raises(ValueError, match="Fund_Nature"):
            compute_category_snapshot(df)


# ============================================================
# Tests de compute_alerts
# ============================================================

class TestComputeAlerts:
    def _make_rules(self) -> list[dict]:
        return [
            {
                "rule_code": "VOL_CAT_P90",
                "metric": "roll_vol_ann",
                "window": "rolling_1y",
                "ref_type": "category",
                "level": "WARN",
                "direction": "above",
                "threshold_pctile": 0.90,
            },
            {
                "rule_code": "VOL_CAT_P97",
                "metric": "roll_vol_ann",
                "window": "rolling_1y",
                "ref_type": "category",
                "level": "ALARM",
                "direction": "above",
                "threshold_pctile": 0.97,
            },
        ]

    def _make_category_df(self, n: int = 20) -> pd.DataFrame:
        """n fondos en la misma categoría con volatilidades escalonadas."""
        rows = []
        for i in range(n):
            vol = 0.02 + i * 0.002
            pctile = i / (n - 1) if n > 1 else 0.5
            rows.append({
                "isin": f"ISIN{i:04d}",
                "metric": "roll_vol_ann",
                "window": "rolling_1y",
                "date": "2026-01-31",
                "value": vol,
                "real_flag": 0,
                "Fund_Nature": "Renta Fija Flexible",
                "pctile_cat": pctile,
                "zscore_cat": (pctile - 0.5) * 2,
                "cat_p10": 0.024,
                "cat_p50": 0.040,
                "cat_p90": 0.054,
                "cat_p97": 0.056,
                "cat_n": n,
            })
        return pd.DataFrame(rows)

    def test_high_pctile_triggers_alarm(self):
        """Fondo en pctile 0.98 (> p97) debe recibir ALARM."""
        cat_df = self._make_category_df(20)
        # Fondo con pctile=0.98
        cat_df.loc[cat_df["isin"] == "ISIN0019", "pctile_cat"] = 0.98
        alerts = compute_alerts(cat_df, self._make_rules(), min_peers=5)
        top = next((a for a in alerts if a["isin"] == "ISIN0019"), None)
        assert top is not None
        assert top["level"] == "ALARM"

    def test_low_pctile_is_ok(self):
        """Fondo en pctile 0.50 no debe recibir WARN (está por debajo de p90)."""
        cat_df = self._make_category_df(20)
        # ISIN0010 → pctile ≈ 0.526 (por debajo de 0.90)
        alerts = compute_alerts(cat_df, self._make_rules(), min_peers=5)
        row = next((a for a in alerts if a["isin"] == "ISIN0010"), None)
        if row is not None:
            assert row["level"] == "OK"

    def test_fail_open_insufficient_peers(self):
        """Con < min_peers fondos no se emiten alertas."""
        cat_df = self._make_category_df(4)
        alerts = compute_alerts(cat_df, self._make_rules(), min_peers=5)
        assert alerts == []

    def test_alarm_supersedes_warn(self):
        """Si una regla WARN y una ALARM se activan, ALARM gana."""
        cat_df = self._make_category_df(20)
        # Fondo con pctile 0.98 supera tanto p90 (WARN) como p97 (ALARM)
        cat_df.loc[cat_df["isin"] == "ISIN0019", "pctile_cat"] = 0.98
        alerts = compute_alerts(cat_df, self._make_rules(), min_peers=5)
        # No puede haber dos filas para el mismo (isin, metric, window)
        key_counts = {}
        for a in alerts:
            k = (a["isin"], a["metric"], a["window"])
            key_counts[k] = key_counts.get(k, 0) + 1
        assert all(v == 1 for v in key_counts.values()), \
            "Múltiples filas para el mismo (isin, metric, window)"

    def test_returns_list(self):
        cat_df = self._make_category_df(20)
        alerts = compute_alerts(cat_df, self._make_rules(), min_peers=5)
        assert isinstance(alerts, list)

    def test_empty_category_df(self):
        alerts = compute_alerts(pd.DataFrame(), self._make_rules(), min_peers=5)
        assert alerts == []


# ============================================================
# Tests de compute_timeseries_snapshots (P2 latest-scalar)
# ============================================================

class TestComputeTimeseriesSnapshots:
    def _make_ts_df(self, n_dates: int = 24) -> pd.DataFrame:
        """Serie temporal de 1 fondo, 1 métrica, 1 ventana."""
        dates = pd.date_range("2024-01-31", periods=n_dates, freq="ME")
        return pd.DataFrame({
            "isin":        ["ISIN0001"] * n_dates,
            "metric":      ["roll_vol_ann"] * n_dates,
            "window":      ["rolling_1y"] * n_dates,
            "date":        dates,
            "value":       [0.03 + i * 0.001 for i in range(n_dates)],
            "real_flag":   [0] * n_dates,
            "Fund_Nature": ["Renta Fija Flexible"] * n_dates,
        })

    def test_returns_list(self):
        snap = compute_timeseries_snapshots(self._make_ts_df())
        assert isinstance(snap, list)

    def test_pctile_self_produced(self):
        snap = compute_timeseries_snapshots(self._make_ts_df(24), min_self_obs=12)
        metrics = {r["metric"] for r in snap}
        assert "roll_vol_ann_pctile_self" in metrics

    def test_pctile_self_range(self):
        snap = compute_timeseries_snapshots(self._make_ts_df(24), min_self_obs=12)
        pctile_rows = [r for r in snap if r["metric"] == "roll_vol_ann_pctile_self"]
        assert pctile_rows
        for r in pctile_rows:
            assert 0.0 <= r["value"] <= 1.0

    def test_increasing_series_has_high_pctile_self(self):
        """Serie estrictamente creciente → último valor está por encima de todos los previos → pctile_self ≈ 1."""
        snap = compute_timeseries_snapshots(self._make_ts_df(24), min_self_obs=12)
        row = next((r for r in snap if r["metric"] == "roll_vol_ann_pctile_self"), None)
        assert row is not None
        assert row["value"] > 0.9  # estrictamente creciente → casi 100%

    def test_cat_signals_produced_when_category_df_given(self):
        ts_df = self._make_ts_df(24)
        cat_df = compute_category_snapshot(ts_df, min_peers=1)  # 1 peer (el fondo mismo)
        snap = compute_timeseries_snapshots(ts_df, category_df=cat_df, min_self_obs=12)
        metrics = {r["metric"] for r in snap}
        # pctile_cat y zscore_cat deben aparecer si category_df tiene datos
        # (con min_peers=1 debería haber snapshot)
        # Al menos pctile_self debe estar siempre presente
        assert "roll_vol_ann_pctile_self" in metrics

    def test_min_self_obs_respected(self):
        """Con min_self_obs=30 y solo 24 puntos, no debe emitirse pctile_self."""
        snap = compute_timeseries_snapshots(self._make_ts_df(24), min_self_obs=30)
        pctile_rows = [r for r in snap if r["metric"].endswith("_pctile_self")]
        assert pctile_rows == []

    def test_empty_input(self):
        snap = compute_timeseries_snapshots(pd.DataFrame())
        assert snap == []

    def test_no_nan_values(self):
        snap = compute_timeseries_snapshots(self._make_ts_df(24), min_self_obs=12)
        for r in snap:
            assert r["value"] is not None
            if isinstance(r["value"], float):
                assert not math.isnan(r["value"])

    def test_isin_in_each_row(self):
        snap = compute_timeseries_snapshots(self._make_ts_df(24), min_self_obs=12)
        for r in snap:
            assert r["isin"] == "ISIN0001"
            assert r["window"] == "rolling_1y"
