# proyecto2/tests/calculations/test_alert_engine_fix_20260913.py
# -*- coding: utf-8 -*-
"""
Regresion de los 3 defectos de fund_metric_alerts documentados en
doc/reglas/AUDITORIA_ESTADISTICA.md §2.5, descubiertos por el motor de
auditoria estadistica (shared/statistical_audit/) al reconciliar Block 4
contra la tabla de alertas en produccion:

  D1: fund_metric_alerts.value era NULL en el 100% de las filas
      (compute_alerts lee row.get("value") pero compute_category_snapshot
      nunca emitia esa columna).
  D2: reference_value era NULL en las 612 filas ALARM de DD_CAT_P03 y
      RET_CAT_P05 (compute_alerts construye cat_p03/cat_p05 pero
      compute_category_snapshot solo emitia cat_p10/p50/p90/p97).
  D3: VOL_CAT_P90/VOL_CAT_P97 nunca disparaban — apuntaban a
      window='rolling_6m', que tiene 0 filas en fund_metric_timeseries
      (solo existen rolling_1y/2y/3y/5y/10y en el modelo hibrido v29).

Cumple R-7: sin importar pipeline.py ni core.io.
"""

import os
import sys

import pandas as pd
import pytest

from src.calculations.rolling_stats import compute_alerts, compute_category_snapshot

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.normpath(os.path.join(_TESTS_DIR, '..', '..', '..'))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)


def _snapshot_input(n: int = 20) -> pd.DataFrame:
    rows = []
    for i in range(n):
        rows.append({
            "isin": f"ISIN{i:04d}",
            "metric": "vol_ann",
            "window": "rolling_1y",
            "date": "2026-01-31",
            "value": 0.02 + i * 0.002,
            "real_flag": 0,
            "Fund_Nature": "Renta Fija Flexible",
        })
    return pd.DataFrame(rows)


class TestFindingD1ValueColumn:
    def test_snapshot_emits_value_column(self):
        cat_df = compute_category_snapshot(_snapshot_input(), min_peers=5)
        assert "value" in cat_df.columns
        row = cat_df[cat_df["isin"] == "ISIN0005"].iloc[0]
        assert row["value"] == pytest.approx(0.02 + 5 * 0.002)

    def test_alert_value_populated_end_to_end(self):
        """The exact production defect: value ended up NULL in every
        fund_metric_alerts row because compute_alerts had nothing to read.
        """
        cat_df = compute_category_snapshot(_snapshot_input(), min_peers=5)
        rules = [{
            "rule_code": "VOL_CAT_P90", "metric": "vol_ann", "window": "rolling_1y",
            "ref_type": "category", "level": "WARN", "direction": "above",
            "threshold_pctile": 0.90,
        }]
        alerts = compute_alerts(cat_df, rules, min_peers=5)
        assert alerts
        assert all(a["value"] is not None for a in alerts)


class TestFindingD2ReferenceValueColumn:
    def test_snapshot_emits_p03_and_p05(self):
        cat_df = compute_category_snapshot(_snapshot_input(), min_peers=5)
        assert {"cat_p03", "cat_p05"} <= set(cat_df.columns)
        row = cat_df.iloc[0]
        assert row["cat_p03"] <= row["cat_p05"] <= row["cat_p10"] <= row["cat_p50"]

    def test_alarm_tier_reference_value_populated(self):
        """RET_CAT_P05 / DD_CAT_P03-style rule: before the fix, reference_value
        was always NULL because cat_p03/cat_p05 did not exist to look up.
        """
        cat_df = compute_category_snapshot(_snapshot_input(), min_peers=5)
        rules = [{
            "rule_code": "RET_CAT_P05", "metric": "vol_ann", "window": "rolling_1y",
            "ref_type": "category", "level": "ALARM", "direction": "below",
            "threshold_pctile": 0.05,
        }]
        cat_df.loc[cat_df["isin"] == "ISIN0000", "pctile_cat"] = 0.01
        alerts = compute_alerts(cat_df, rules, min_peers=5)
        triggered = next(a for a in alerts if a["isin"] == "ISIN0000")
        assert triggered["level"] == "ALARM"
        assert triggered["reference_value"] is not None


class TestFindingD3WindowMismatch:
    def test_vol_alert_rules_target_a_window_that_exists_in_timeseries(self):
        from shared.config import ALERT_RULES, ROLLING_WINDOWS

        vol_rules = [r for r in ALERT_RULES if r["metric"] == "vol_ann"]
        assert vol_rules, "expected at least one vol_ann rule in ALERT_RULES"
        for rule in vol_rules:
            assert rule["window"] != "rolling_6m", (
                "rolling_6m has 0 rows in fund_metric_timeseries (only "
                "rolling_1y/2y/3y/5y/10y are curated there) — this rule "
                "would never fire, reproducing finding D3"
            )
            assert rule["window"] in ROLLING_WINDOWS
