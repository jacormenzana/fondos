# proyecto2/tests/reports/test_rolling_dashboard.py
# -*- coding: utf-8 -*-
"""
Tests para rolling_dashboard.py (v27 — bounded queries).

Cumple R-7: sin importar pipeline.py ni core.io.
Usa fixtures sintéticos con valores conocidos; no depende de la DB de producción.
"""

import sys
from pathlib import Path

import pytest

# Añadir la raíz del repo al path para que shared.config sea importable
_ROOT = Path(__file__).resolve().parents[3]   # c:\desarrollo\fondos
sys.path.insert(0, str(_ROOT))

from src.reports.rolling_dashboard import (
    _VENDOR_CHARTJS,
    _auto_select_isins,
    _build_html,
    _fmt_pct,
    _pctile_bar,
    nature_summary,
    peer_refs,
    pivot_metrics,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures sintéticas
# ═══════════════════════════════════════════════════════════════════════════════

def _snapshot_rows() -> list:
    """
    Simula rows de _load_snapshot:
    (isin, metric, window, date, value, Fund_Nature, Fund_Name)
    """
    return [
        # RFF — 3 fondos, rolling_1y
        ("LU0001", "return_ann", "rolling_1y", "2026-06-30", 0.10, "Renta Fija Flexible", "FONDO A"),
        ("LU0001", "vol_ann",    "rolling_1y", "2026-06-30", 0.05, "Renta Fija Flexible", "FONDO A"),
        ("LU0001", "max_dd",     "rolling_1y", "2026-06-30",-0.04, "Renta Fija Flexible", "FONDO A"),
        ("LU0002", "return_ann", "rolling_1y", "2026-06-30", 0.04, "Renta Fija Flexible", "FONDO B"),
        ("LU0002", "vol_ann",    "rolling_1y", "2026-06-30", 0.03, "Renta Fija Flexible", "FONDO B"),
        ("LU0002", "max_dd",     "rolling_1y", "2026-06-30",-0.02, "Renta Fija Flexible", "FONDO B"),
        ("LU0003", "return_ann", "rolling_1y", "2026-06-30", 0.07, "Renta Fija Flexible", "FONDO C"),
        ("LU0003", "vol_ann",    "rolling_1y", "2026-06-30", 0.04, "Renta Fija Flexible", "FONDO C"),
        ("LU0003", "max_dd",     "rolling_1y", "2026-06-30",-0.03, "Renta Fija Flexible", "FONDO C"),
        ("LU0004", "return_ann", "rolling_1y", "2026-06-30", 0.08, "Renta Fija Flexible", "FONDO D"),
        ("LU0004", "vol_ann",    "rolling_1y", "2026-06-30", 0.06, "Renta Fija Flexible", "FONDO D"),
        ("LU0004", "max_dd",     "rolling_1y", "2026-06-30",-0.05, "Renta Fija Flexible", "FONDO D"),
        # Mixtos — 2 fondos, rolling_1y
        ("LU0010", "return_ann", "rolling_1y", "2026-06-30", 0.15, "Mixtos", "MIXTO X"),
        ("LU0010", "vol_ann",    "rolling_1y", "2026-06-30", 0.10, "Mixtos", "MIXTO X"),
        ("LU0010", "max_dd",     "rolling_1y", "2026-06-30",-0.08, "Mixtos", "MIXTO X"),
        ("LU0011", "return_ann", "rolling_1y", "2026-06-30", 0.12, "Mixtos", "MIXTO Y"),
        ("LU0011", "vol_ann",    "rolling_1y", "2026-06-30", 0.09, "Mixtos", "MIXTO Y"),
        ("LU0011", "max_dd",     "rolling_1y", "2026-06-30",-0.07, "Mixtos", "MIXTO Y"),
        ("LU0012", "return_ann", "rolling_1y", "2026-06-30", 0.18, "Mixtos", "MIXTO Z"),
        ("LU0012", "vol_ann",    "rolling_1y", "2026-06-30", 0.11, "Mixtos", "MIXTO Z"),
        ("LU0012", "max_dd",     "rolling_1y", "2026-06-30",-0.10, "Mixtos", "MIXTO Z"),
        ("LU0013", "return_ann", "rolling_1y", "2026-06-30", 0.16, "Mixtos", "MIXTO W"),
        ("LU0013", "vol_ann",    "rolling_1y", "2026-06-30", 0.09, "Mixtos", "MIXTO W"),
        ("LU0013", "max_dd",     "rolling_1y", "2026-06-30",-0.06, "Mixtos", "MIXTO W"),
        # Otras ventanas (no deben afectar al resumen que usa rolling_1y)
        ("LU0001", "return_ann", "rolling_3y", "2026-06-30", 0.08, "Renta Fija Flexible", "FONDO A"),
    ]


def _alert_rows() -> list:
    """
    Simula rows de _load_alerts:
    (isin, metric, window, level, rule_code, value, reference_value, Fund_Nature, Fund_Name)
    """
    return [
        ("LU0001", "vol_ann", "rolling_1y", "ALARM", "VOL_CAT_P90", 0.08, 0.07, "RFF", "FONDO A"),
        ("LU0001", "max_dd",  "rolling_1y", "WARN",  "DD_CAT_P10",  -0.10, -0.05, "RFF", "FONDO A"),
        ("LU0002", "vol_ann", "rolling_1y", "ALARM", "VOL_CAT_P90", 0.09, 0.07, "RFF", "FONDO B"),
        ("LU0002", "max_dd",  "rolling_1y", "ALARM", "DD_CAT_P10",  -0.12, -0.05, "RFF", "FONDO B"),
        ("LU0003", "max_dd",  "rolling_1y", "OK",    "DD_CAT_P10",  -0.02, -0.05, "RFF", "FONDO C"),
    ]


def _metric_rows() -> list:
    """
    Simula rows de _load_scalar_metrics:
    (isin, metric, horizon, value, Fund_Name, Fund_Nature, SRRI)
    """
    return [
        # Horizons múltiples — rolling_1y debe ganar sobre since_inception
        ("LU0001", "return_ann",    "since_inception", 0.09, "FONDO A", "RFF", 3),
        ("LU0001", "return_ann",    "rolling_1y",      0.10, "FONDO A", "RFF", 3),
        ("LU0001", "vol_ann","rolling_1y",      0.05, "FONDO A", "RFF", 3),
        ("LU0001", "sharpe",        "rolling_1y",      1.20, "FONDO A", "RFF", 3),
        ("LU0001", "max_dd",  "rolling_1y",     -0.04, "FONDO A", "RFF", 3),
        ("LU0001", "alpha_persistence","since_inception", 0.62, "FONDO A", "RFF", 3),
        ("LU0001", "return_ann_pctile_self", "rolling_1y", 0.72, "FONDO A", "RFF", 3),
        ("LU0001", "vol_ann_pctile_self",    "rolling_1y", 0.81, "FONDO A", "RFF", 3),
        ("LU0001", "max_dd_pctile_self",     "rolling_1y", 0.55, "FONDO A", "RFF", 3),
        ("LU0001", "return_ann_pctile_cat",  "rolling_1y", 0.68, "FONDO A", "RFF", 3),
        # Fondo B — solo since_inception para return_ann (no hay rolling_1y)
        ("LU0002", "return_ann",    "since_inception", 0.04, "FONDO B", "RFF", 2),
        ("LU0002", "vol_ann","rolling_1y",      0.03, "FONDO B", "RFF", 2),
        ("LU0002", "sharpe",        "since_inception", 0.85, "FONDO B", "RFF", 2),
    ]


# ═══════════════════════════════════════════════════════════════════════════════
# Tests de pivot_metrics
# ═══════════════════════════════════════════════════════════════════════════════

class TestPivotMetrics:
    def test_rolling_1y_preferred_over_since_inception(self):
        """return_ann rolling_1y=0.10 debe ganar sobre since_inception=0.09."""
        result = pivot_metrics(_metric_rows())
        assert result["LU0001"]["return_ann"] == pytest.approx(0.10)

    def test_since_inception_used_when_only_option(self):
        """Fondo B: return_ann solo existe en since_inception → debe usar ese."""
        result = pivot_metrics(_metric_rows())
        assert result["LU0002"]["return_ann"] == pytest.approx(0.04)

    def test_fund_name_populated(self):
        result = pivot_metrics(_metric_rows())
        assert result["LU0001"]["fund_name"] == "FONDO A"
        assert result["LU0002"]["fund_name"] == "FONDO B"

    def test_pctile_captured(self):
        result = pivot_metrics(_metric_rows())
        assert result["LU0001"]["return_ann_pctile_self"] == pytest.approx(0.72)
        assert result["LU0001"]["return_ann_pctile_cat"]  == pytest.approx(0.68)

    def test_missing_metric_not_present(self):
        """alpha_persistence falta para LU0002 — no debe estar en el dict."""
        result = pivot_metrics(_metric_rows())
        assert "alpha_persistence" not in result.get("LU0002", {})

    def test_empty_input(self):
        assert pivot_metrics([]) == {}


# ═══════════════════════════════════════════════════════════════════════════════
# Tests de nature_summary
# ═══════════════════════════════════════════════════════════════════════════════

class TestNatureSummary:
    def test_correct_natures_present(self):
        result = nature_summary(_snapshot_rows())
        assert "Renta Fija Flexible" in result
        assert "Mixtos" in result

    def test_rff_count(self):
        result = nature_summary(_snapshot_rows())
        assert result["Renta Fija Flexible"]["count"] == 4  # LU0001..LU0004

    def test_rff_avg_return(self):
        """Media de (0.10, 0.04, 0.07, 0.08) = 0.0725."""
        result = nature_summary(_snapshot_rows())
        assert result["Renta Fija Flexible"]["avg_return"] == pytest.approx(0.0725)

    def test_rolling_3y_not_counted(self):
        """LU0001 rolling_3y no debe incrementar el count de RFF (solo rolling_1y)."""
        # LU0001 aparece también con rolling_3y, pero count sigue siendo 4 ISINs únicos
        result = nature_summary(_snapshot_rows())
        assert result["Renta Fija Flexible"]["count"] == 4

    def test_empty_input(self):
        assert nature_summary([]) == {}


# ═══════════════════════════════════════════════════════════════════════════════
# Tests de peer_refs
# ═══════════════════════════════════════════════════════════════════════════════

class TestPeerRefs:
    def test_rff_has_quartiles(self):
        """4 fondos RFF → suficientes para cuartiles."""
        result = peer_refs(_snapshot_rows())
        assert "Renta Fija Flexible" in result
        rff = result["Renta Fija Flexible"]
        assert "return_ann" in rff
        assert "p25" in rff["return_ann"]
        assert "p50" in rff["return_ann"]
        assert "p75" in rff["return_ann"]

    def test_quartile_ordering(self):
        """p25 ≤ p50 ≤ p75 para roll_return_ann."""
        result = peer_refs(_snapshot_rows())
        rq = result["Renta Fija Flexible"]["return_ann"]
        assert rq["p25"] <= rq["p50"] <= rq["p75"]

    def test_too_few_peers_no_entry(self):
        """Con menos de 4 valores no se calculan cuartiles (se omite la entrada)."""
        # Quitamos 2 filas de Mixtos para que queden solo 2 ISINs
        rows = [r for r in _snapshot_rows() if r[0] not in ("LU0012", "LU0013")]
        # Mixtos solo quedaría con LU0010 y LU0011 → 2 fondos < 4 → omitir
        result = peer_refs(rows)
        # Puede que Mixtos esté ausente o presente con otro métrico; en ningún caso
        # roll_return_ann de Mixtos debe aparecer si son solo 2 fondos.
        mixto_ret = result.get("Mixtos", {}).get("return_ann")
        assert mixto_ret is None

    def test_empty_input(self):
        assert peer_refs([]) == {}


# ═══════════════════════════════════════════════════════════════════════════════
# Tests de _auto_select_isins
# ═══════════════════════════════════════════════════════════════════════════════

class TestAutoSelectIsins:
    def test_highest_severity_first(self):
        """LU0002 tiene 2×ALARM → debe aparecer primero."""
        result = _auto_select_isins(_alert_rows())
        assert result[0] == "LU0002"

    def test_respects_cap(self):
        """Cap de n=2 devuelve máximo 2 ISINs."""
        result = _auto_select_isins(_alert_rows(), n=2)
        assert len(result) <= 2

    def test_no_duplicates(self):
        result = _auto_select_isins(_alert_rows())
        assert len(result) == len(set(result))

    def test_empty_alerts(self):
        assert _auto_select_isins([]) == []


# ═══════════════════════════════════════════════════════════════════════════════
# Tests de _pctile_bar
# ═══════════════════════════════════════════════════════════════════════════════

class TestPctileBar:
    def test_none_returns_dash(self):
        html = _pctile_bar(None)
        assert "—" in html

    def test_zero_renders(self):
        html = _pctile_bar(0.0)
        assert "0" in html
        assert "rgb(" in html

    def test_one_renders(self):
        html = _pctile_bar(1.0)
        assert "100" in html

    def test_clamps_above_one(self):
        html_one  = _pctile_bar(1.0)
        html_over = _pctile_bar(1.5)
        assert html_one == html_over   # clamped to same result

    def test_clamps_below_zero(self):
        html_zero = _pctile_bar(0.0)
        html_neg  = _pctile_bar(-0.5)
        assert html_zero == html_neg


# ═══════════════════════════════════════════════════════════════════════════════
# Tests de _fmt_pct
# ═══════════════════════════════════════════════════════════════════════════════

class TestFmtPct:
    def test_none(self):
        assert _fmt_pct(None) == "—"

    def test_positive(self):
        assert _fmt_pct(0.1234) == "12.34%"

    def test_negative(self):
        assert _fmt_pct(-0.05) == "-5.00%"

    def test_zero(self):
        assert _fmt_pct(0.0) == "0.00%"


# ═══════════════════════════════════════════════════════════════════════════════
# Test de _build_html — sin CDN cuando el vendor existe
# ═══════════════════════════════════════════════════════════════════════════════

class TestBuildHtml:
    def test_no_cdn_when_vendor_exists(self):
        """Si el fichero vendor está presente, el HTML no debe contener cdn.jsdelivr."""
        if not _VENDOR_CHARTJS.exists():
            pytest.skip("Fichero vendor Chart.js no descargado; skip de test de CDN")

        html = _build_html(
            snapshot_rows=_snapshot_rows(),
            series_rows=[],
            metric_rows=_metric_rows(),
            al_rows=_alert_rows(),
            natures=["Renta Fija Flexible", "Mixtos"],
            filter_nature=None,
            auto_isins=["LU0001", "LU0002"],
            generated_at="2026-07-31",
        )
        assert "cdn.jsdelivr" not in html, (
            "El HTML no debe referenciar CDN cuando el vendor está disponible"
        )

    def test_html_has_required_sections(self):
        """El HTML generado incluye los marcadores de las 5 secciones."""
        html = _build_html(
            snapshot_rows=_snapshot_rows(),
            series_rows=[],
            metric_rows=_metric_rows(),
            al_rows=_alert_rows(),
            natures=["Renta Fija Flexible"],
            filter_nature=None,
            auto_isins=["LU0001"],
            generated_at="2026-07-31",
        )
        assert "Resumen por categor" in html      # Section 2
        assert "Series rolling"      in html      # Section 3
        assert "tricas escalares"    in html      # Section 4
        assert "Alertas activas"     in html      # Section 5

    def test_nature_filter_chip_present(self):
        """Cuando se pasa filter_nature, el chip aparece en el header."""
        html = _build_html(
            snapshot_rows=[], series_rows=[], metric_rows=[],
            al_rows=[], natures=[], filter_nature="Mixtos",
            auto_isins=[], generated_at="2026-07-31",
        )
        assert "Mixtos" in html

    def test_alarm_kpi_shown(self):
        """El conteo de alarmas del fixture aparece en los KPIs."""
        html = _build_html(
            snapshot_rows=_snapshot_rows(),
            series_rows=[],
            metric_rows=[],
            al_rows=_alert_rows(),
            natures=[],
            filter_nature=None,
            auto_isins=[],
            generated_at="2026-07-31",
        )
        n_alarm = sum(1 for r in _alert_rows() if r[3] == "ALARM")
        assert f"<div class=\"num\">{n_alarm}</div>" in html
