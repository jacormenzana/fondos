# proyecto1/tests/test_statistical_audit_reconcile.py
# -*- coding: utf-8 -*-
"""Tests unitarios de shared/statistical_audit/reconcile.py
(reconcile_with_alerts — funcion #12, doc/reglas/AUDITORIA_ESTADISTICA.md
§4). Filtra hallazgos ya conocidos por el motor de alertas de produccion
(fund_metric_alerts) para devolver solo el conjunto incremental.

Nota (2026-09-15): esta funcion existe y esta testeada pero deliberadamente
NO esta cableada en scripts/audit/run_statistical_audit.py todavia --
fund_metric_alerts en produccion conserva filas de codigo pre-fix hasta el
proximo ciclo P2 real (ver docstring de reconcile.py y AUDITORIA_ESTADISTICA.md
§2.6/§2.7). Cablearla hoy reconciliaria contra datos obsoletos.

Cumple R-7: sin importar pipeline.py ni core.io.
"""

import os
import sys

import pandas as pd

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.normpath(os.path.join(_TESTS_DIR, '..', '..'))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from shared.statistical_audit.reconcile import isin_metric_key, reconcile_with_alerts


def _alerts_df(rows):
    return pd.DataFrame(rows, columns=["isin", "metric", "window", "level", "rule_code"])


def _finding(isin=None, group_key=None, block="BLOCK4"):
    return {
        "block": block, "rule_id": "OUTLIER_IQR", "rule_class": "STATISTICAL_ANOMALY",
        "severity": "WARN", "group_key": group_key, "isin": isin,
        "value": 1.0, "reference_value": None, "threshold": None,
        "distance": None, "evidence": "x", "root_cause_candidate": None,
    }


class TestIsinMetricKey:
    def test_extracts_metric_from_first_segment(self):
        f = _finding(isin="ES0001", group_key="sharpe|since_inception|0|v1")
        assert isin_metric_key(f) == ("ES0001", "sharpe")

    def test_none_when_no_isin(self):
        f = _finding(isin=None, group_key="sharpe|since_inception|0|v1")
        assert isin_metric_key(f) is None

    def test_none_when_no_group_key(self):
        f = _finding(isin="ES0001", group_key=None)
        assert isin_metric_key(f) is None


class TestReconcileWithAlerts:
    def test_known_finding_is_dropped(self):
        """A finding whose (isin, metric) is already alerted must NOT
        reappear as an incremental finding -- avoids double-reporting the
        same known issue via two independent mechanisms."""
        findings = [_finding(isin="ES0001", group_key="sharpe|since_inception|0|v1")]
        alerts = _alerts_df([("ES0001", "sharpe", "rolling_1y", "WARN", "VOL_CAT_P90")])
        result = reconcile_with_alerts(findings, alerts)
        assert result == []

    def test_unknown_finding_is_kept(self):
        findings = [_finding(isin="ES0002", group_key="sharpe|since_inception|0|v1")]
        alerts = _alerts_df([("ES0001", "sharpe", "rolling_1y", "WARN", "VOL_CAT_P90")])
        result = reconcile_with_alerts(findings, alerts)
        assert result == findings

    def test_different_metric_same_isin_is_kept(self):
        """Same fund, different metric -- alerts_df must not over-match."""
        findings = [_finding(isin="ES0001", group_key="sortino|since_inception|0|v1")]
        alerts = _alerts_df([("ES0001", "sharpe", "rolling_1y", "WARN", "VOL_CAT_P90")])
        result = reconcile_with_alerts(findings, alerts)
        assert result == findings

    def test_group_level_finding_without_isin_always_kept(self):
        """Group-level findings (BLOCK1/2/5/7 -- no per-fund isin) can never
        be corroborated by a per-fund alerts table, so they always pass
        through untouched regardless of alerts_df content."""
        findings = [_finding(isin=None, group_key="DOMINANT_VALUE_CONCENTRATION")]
        alerts = _alerts_df([("ES0001", "sharpe", "rolling_1y", "WARN", "VOL_CAT_P90")])
        result = reconcile_with_alerts(findings, alerts)
        assert result == findings

    def test_empty_alerts_df_keeps_everything(self):
        findings = [_finding(isin="ES0001", group_key="sharpe|since_inception|0|v1")]
        result = reconcile_with_alerts(findings, pd.DataFrame(columns=["isin", "metric"]))
        assert result == findings

    def test_none_alerts_df_keeps_everything(self):
        findings = [_finding(isin="ES0001", group_key="sharpe|since_inception|0|v1")]
        result = reconcile_with_alerts(findings, None)
        assert result == findings

    def test_custom_key_fn_is_respected(self):
        """A caller-supplied key_fn overrides the default group_key parsing
        -- lets a runner match on its own convention instead of coupling
        reconcile_with_alerts to one specific group_key format."""
        findings = [{"isin": "ES0001", "metric_name": "sharpe"}]
        alerts = _alerts_df([("ES0001", "sharpe", "rolling_1y", "WARN", "VOL_CAT_P90")])
        result = reconcile_with_alerts(
            findings, alerts, key_fn=lambda f: (f["isin"], f["metric_name"])
        )
        assert result == []

    def test_multiple_findings_mixed_known_and_incremental(self):
        findings = [
            _finding(isin="ES0001", group_key="sharpe|since_inception|0|v1"),
            _finding(isin="ES0002", group_key="sortino|since_inception|0|v1"),
        ]
        alerts = _alerts_df([("ES0001", "sharpe", "rolling_1y", "WARN", "VOL_CAT_P90")])
        result = reconcile_with_alerts(findings, alerts)
        assert len(result) == 1
        assert result[0]["isin"] == "ES0002"
