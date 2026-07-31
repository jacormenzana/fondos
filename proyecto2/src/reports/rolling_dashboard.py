# proyecto2/src/reports/rolling_dashboard.py
# -*- coding: utf-8 -*-
"""
Generador de dashboard HTML auto-contenido — indicadores P2 (v27).

CAMBIOS ARQUITECTÓNICOS vs v26  (RC-1 FIX):
  fund_metric_timeseries (13.7 M filas) NO se carga nunca completo.
  Dos niveles de consulta:
    Tier 1 — snapshot (última fecha, todo el universo):
        JOIN a MAX(date) GROUP BY → índice idx_fmts_metric_window_real_date.
        Alimenta KPIs, tabla por nature y percentiles de referencia.
    Tier 2 — series completas SOLO para los ISINs seleccionados (--isin, max 8):
        WHERE isin IN (…) → índice idx_fmts_isin_metric.
        Si --isin no se indica, se auto-seleccionan hasta 8 fondos con más ALARMs.
        La tabla completa nunca se lee → pico RAM ~ decenas de MB, no GB.

CHART.JS AUTO-CONTENIDO:
  Lee el fichero vendorizado _vendor/chartjs_4.4.0.min.js si existe.
  Si falta, fallback al CDN (las gráficas requieren conexión) + WARNING.
  One-time vendoring:
    Invoke-WebRequest https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js \
        -OutFile proyecto2/src/reports/_vendor/chartjs_4.4.0.min.js

Uso:
    python -m proyecto2.src.reports.rolling_dashboard
    python -m proyecto2.src.reports.rolling_dashboard --isin LU1050470613,LU0841607921
    python -m proyecto2.src.reports.rolling_dashboard --nature "Mixtos"
    python -m proyecto2.src.reports.rolling_dashboard --output out/reports/informe.html
    python -m proyecto2.src.reports.rolling_dashboard --nature "Renta Variable" --isin LU0823415285
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import warnings
from datetime import date
from pathlib import Path
from statistics import quantiles
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent.parent.parent   # c:\desarrollo\fondos
sys.path.insert(0, str(_ROOT))

from shared.config import DB_PATH

# ── Fichero vendorizado ────────────────────────────────────────────────────────
_VENDOR_CHARTJS = Path(__file__).parent / "_vendor" / "chartjs_4.4.0.min.js"


# ── Constantes de visualización ────────────────────────────────────────────────
_METRIC_LABELS = {
    "roll_vol_ann":    "Volatilidad Anualizada",
    "roll_max_dd":     "Máximo Drawdown",
    "roll_return_ann": "Retorno Anualizado",
}
_WINDOW_ORDER = [
    "rolling_1m", "rolling_3m", "rolling_6m",
    "rolling_1y", "rolling_2y", "rolling_3y", "rolling_5y", "rolling_10y",
]
_WINDOW_LABELS = {
    "rolling_1m": "1m", "rolling_3m": "3m", "rolling_6m": "6m",
    "rolling_1y": "1a", "rolling_2y": "2a", "rolling_3y": "3a",
    "rolling_5y": "5a", "rolling_10y": "10a",
}
_LEVEL_COLOR  = {"ALARM": "#C0392B", "WARN": "#D4943A", "OK": "#27AE60"}
_LEVEL_WEIGHT = {"ALARM": 3, "WARN": 2, "OK": 1}

# Paleta categórica: colores fijos, nunca reciclados, sin solapar con status colors.
# Orden fijo (dataviz: categorical hues in fixed order, never cycled).
_SERIES_COLORS = [
    "#2980B9",   # azul acero
    "#8E44AD",   # violeta
    "#16A085",   # teal
    "#D35400",   # naranja tostado
    "#1A5276",   # azul navy
    "#6C3483",   # violeta oscuro
    "#117A65",   # verde oscuro (distinto del OK #27AE60)
    "#A04000",   # siena  (distinto del ALARM #C0392B)
]

# Métricas escalares a mostrar en la tabla de métricas
_SCALAR_METRICS = ("return_ann", "volatility_ann", "sharpe", "max_drawdown",
                   "alpha_persistence", "capture_ratio", "srri_nav")
_PCTILE_METRICS = (
    "roll_vol_ann_pctile_self",    "roll_max_dd_pctile_self",    "roll_return_ann_pctile_self",
    "roll_vol_ann_pctile_cat",     "roll_max_dd_pctile_cat",     "roll_return_ann_pctile_cat",
)
_ALL_METRICS = _SCALAR_METRICS + _PCTILE_METRICS
_METRIC_IN = ",".join(f"'{m}'" for m in _ALL_METRICS)

# Columnas para la tabla de métricas: (key, label, is_pct, decimals)
_TABLE_COLS: list[tuple] = [
    ("srri_nav",            "SRRI P2",       False, 1),
    ("return_ann",          "Retorno anu.",   True,  2),
    ("volatility_ann",      "Volatilidad",    True,  2),
    ("sharpe",              "Sharpe",         False, 2),
    ("max_drawdown",        "Max DD",         True,  2),
    ("alpha_persistence",   "Alpha Pers.",    False, 2),
    ("capture_ratio",       "Capture",        False, 2),
]
_PCTILE_TABLE_COLS: list[tuple] = [  # (metric_key, label)
    ("roll_return_ann_pctile_self", "Pctile Ret."),
    ("roll_vol_ann_pctile_self",    "Pctile Vol."),
    ("roll_max_dd_pctile_self",     "Pctile DD"),
    ("roll_return_ann_pctile_cat",  "Cat Ret."),
]


# ── SQL: Tier 1 — snapshot de última fecha, ventana rolling_1y (universo) ─────
# Solo rolling_1y: es la ventana usada por nature_summary y peer_refs.
# Filtra por (metric, window='rolling_1y', real_flag) → ~3 200 fondos × 3 métricas
# = ~9 600 filas; índice idx_fmts_metric_window_real_date sirve el GROUP BY.
_SQL_SNAPSHOT = """
    SELECT t.isin, t.metric, t.window, t.date, t.value,
           m.Fund_Nature, m.Fund_Name
    FROM fund_metric_timeseries t
    JOIN (
        SELECT metric, window, real_flag, MAX(date) AS mx
        FROM fund_metric_timeseries
        WHERE metric IN ('roll_vol_ann','roll_max_dd','roll_return_ann')
          AND window = 'rolling_1y'
          AND real_flag = 0
        GROUP BY metric, window, real_flag
    ) latest ON t.metric    = latest.metric
             AND t.window    = latest.window
             AND t.real_flag = latest.real_flag
             AND t.date      = latest.mx
    LEFT JOIN fund_master m ON t.isin = m.ISIN
    WHERE t.real_flag = 0
      AND t.metric IN ('roll_vol_ann','roll_max_dd','roll_return_ann')
      AND t.window = 'rolling_1y'
      AND t.value IS NOT NULL
    {nature_clause}
"""

# ── SQL: Tier 2 — series completas para ISINs seleccionados ───────────────────
_SQL_SERIES = """
    SELECT t.isin, t.metric, t.window, t.date, t.value,
           m.Fund_Nature, m.Fund_Name
    FROM fund_metric_timeseries t
    LEFT JOIN fund_master m ON t.isin = m.ISIN
    WHERE t.isin IN ({ph})
      AND t.real_flag = 0
      AND t.metric IN ('roll_vol_ann','roll_max_dd','roll_return_ann')
      AND t.value IS NOT NULL
    ORDER BY t.isin, t.metric, t.window, t.date
"""

# ── SQL: métricas escalares desde fund_metrics (solo ISINs seleccionados) ─────
# Siempre se filtra por ISIN (los auto-seleccionados o los pasados por --isin)
# para evitar cargar toda la tabla fund_metrics (puede ser > 200 K filas).
_SQL_METRICS = """
    SELECT fm.isin, fm.metric, fm.horizon, fm.value,
           m.Fund_Name, m.Fund_Nature, m.SRRI
    FROM fund_metrics fm
    LEFT JOIN fund_master m ON fm.isin = m.ISIN
    WHERE fm.real_flag = 0
      AND fm.metric IN ({metric_in})
      AND fm.isin IN ({isin_in})
      AND fm.value IS NOT NULL
"""

# ── SQL: alertas (original conservado) ────────────────────────────────────────
_SQL_ALERTS = """
    SELECT a.isin, a.metric, a.window, a.level, a.rule_code,
           a.value, a.reference_value, m.Fund_Nature, m.Fund_Name
    FROM fund_metric_alerts a
    LEFT JOIN fund_master m ON a.isin = m.ISIN
    {nature_clause}
    ORDER BY a.level DESC, a.isin
"""

_SQL_NATURES = """
    SELECT DISTINCT m.Fund_Nature
    FROM fund_metric_timeseries t
    JOIN fund_master m ON t.isin = m.ISIN
    WHERE m.Fund_Nature IS NOT NULL
    ORDER BY m.Fund_Nature
"""


# ── Carga de datos ─────────────────────────────────────────────────────────────

def _nclause(nature: str | None, alias: str = "m") -> str:
    return f"AND {alias}.Fund_Nature = '{nature}'" if nature else ""


def _load_snapshot(conn: sqlite3.Connection, nature: str | None = None) -> list:
    return conn.execute(_SQL_SNAPSHOT.format(nature_clause=_nclause(nature))).fetchall()


def _load_series(conn: sqlite3.Connection, isins: list[str]) -> list:
    if not isins:
        return []
    ph = ",".join("?" * len(isins))
    return conn.execute(_SQL_SERIES.format(ph=ph), isins).fetchall()


def _load_scalar_metrics(conn: sqlite3.Connection, isins: list[str]) -> list:
    """Carga métricas escalares para los ISINs seleccionados (max 8)."""
    if not isins:
        return []
    isin_in = ",".join(f"'{i}'" for i in isins)
    return conn.execute(
        _SQL_METRICS.format(metric_in=_METRIC_IN, isin_in=isin_in)
    ).fetchall()


def _load_alerts(conn: sqlite3.Connection, nature: str | None = None) -> list:
    nc = f"AND m.Fund_Nature = '{nature}'" if nature else ""
    return conn.execute(_SQL_ALERTS.format(nature_clause=nc)).fetchall()


def _auto_select_isins(al_rows: list, n: int = 8) -> list[str]:
    """Devuelve hasta n ISINs con más puntuación de alertas (ALARM=3, WARN=2, OK=1)."""
    score: dict[str, int] = {}
    for row in al_rows:
        isin, metric, window, level, *_ = row
        score[isin] = score.get(isin, 0) + _LEVEL_WEIGHT.get(level, 0)
    return [isin for isin, _ in sorted(score.items(), key=lambda x: -x[1])[:n]]


# ── Transformaciones de datos ─────────────────────────────────────────────────

def pivot_metrics(rows: list) -> dict[str, dict[str, Any]]:
    """
    Pivota filas long-format de fund_metrics a {isin: {campo: valor, ...}}.
    Prioridad para multi-horizon: rolling_1y > since_inception > resto.
    Usable desde tests sin dependencias de pipeline (R-7).
    """
    PRIO = {"rolling_1y": 2, "since_inception": 1}
    result: dict[str, dict] = {}
    best_prio: dict[tuple, int] = {}   # (isin, metric) → mejor prioridad vista

    for isin, metric, horizon, value, fund_name, fund_nature, srri in rows:
        entry = result.setdefault(isin, {
            "fund_name":   fund_name or isin,
            "fund_nature": fund_nature or "",
            "srri_p1":     srri,
        })
        prio = PRIO.get(horizon, 0)
        key  = (isin, metric)
        if prio > best_prio.get(key, -1):
            entry[metric] = value
            best_prio[key] = prio

    return result


def nature_summary(snapshot_rows: list) -> dict[str, dict]:
    """
    Medias por Fund_Nature a partir del snapshot (rolling_1y).
    Usable desde tests (R-7).
    """
    buckets: dict[str, dict] = {}
    for isin, metric, window, dt, value, nature, name in snapshot_rows:
        if window != "rolling_1y" or not nature or value is None:
            continue
        b = buckets.setdefault(nature, {"isins": set(), "ret": [], "vol": [], "dd": []})
        b["isins"].add(isin)
        if metric == "roll_return_ann":
            b["ret"].append(value)
        elif metric == "roll_vol_ann":
            b["vol"].append(value)
        elif metric == "roll_max_dd":
            b["dd"].append(value)

    def avg(lst: list) -> float | None:
        return sum(lst) / len(lst) if lst else None

    return {
        n: {
            "count":      len(d["isins"]),
            "avg_return": avg(d["ret"]),
            "avg_vol":    avg(d["vol"]),
            "avg_dd":     avg(d["dd"]),
        }
        for n, d in sorted(buckets.items())
    }


def peer_refs(snapshot_rows: list, window: str = "rolling_1y") -> dict:
    """
    Cuartiles (p25/p50/p75) por (Fund_Nature, metric) desde el snapshot.
    Devuelve {nature: {metric: {p25, p50, p75}}}.
    Usable desde tests (R-7).
    """
    buckets: dict[tuple, list] = {}
    for isin, metric, w, dt, value, nature, name in snapshot_rows:
        if w != window or not nature or value is None:
            continue
        buckets.setdefault((nature, metric), []).append(value)

    result: dict = {}
    for (nature, metric), vals in buckets.items():
        if len(vals) < 4:
            continue
        qs = quantiles(vals, n=4)
        result.setdefault(nature, {})[metric] = {
            "p25": qs[0], "p50": qs[1], "p75": qs[2],
        }
    return result


# ── Utilidades HTML ───────────────────────────────────────────────────────────

def _fmt_pct(v: float | None, decimals: int = 2) -> str:
    return f"{v * 100:.{decimals}f}%" if v is not None else "—"


def _fmt_num(v: float | None, decimals: int = 2) -> str:
    return f"{v:.{decimals}f}" if v is not None else "—"


def _pctile_bar(p: float | None) -> str:
    """Barra de calor 0–100 para pctile_self/pctile_cat (valor 0.0–1.0)."""
    if p is None:
        return "<span style='color:var(--sub)'>—</span>"
    p = max(0.0, min(1.0, p))
    pct = p * 100
    # Azul bajo → rojo alto (sin solapar colores de status)
    r = int(41  + (200 - 41)  * p)
    g = int(128 - (128 - 41)  * p)
    b = int(185 - (185 - 41)  * p)
    color = f"rgb({r},{g},{b})"
    return (
        f"<div style='display:flex;align-items:center;gap:5px'>"
        f"<div style='flex:1;height:7px;border-radius:3px;background:var(--border)'>"
        f"<div style='width:{pct:.0f}%;height:100%;border-radius:3px;background:{color}'></div>"
        f"</div>"
        f"<code style='font-size:10px;color:var(--sub);min-width:26px;text-align:right'>{pct:.0f}</code>"
        f"</div>"
    )


def _load_chartjs() -> str:
    """Devuelve el bloque <script> de Chart.js — fichero vendorizado o CDN (fallback)."""
    if _VENDOR_CHARTJS.exists():
        js = _VENDOR_CHARTJS.read_text(encoding="utf-8")
        return f"<script>\n{js}\n</script>"
    warnings.warn(
        f"Fichero vendorizado Chart.js no encontrado: {_VENDOR_CHARTJS}\n"
        "Fallback al CDN — las gráficas requieren conexión a internet.\n"
        "Para usar sin conexión descarga:\n"
        "  Invoke-WebRequest https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"
        f" -OutFile \"{_VENDOR_CHARTJS}\"",
        UserWarning,
        stacklevel=4,
    )
    return (
        '<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"'
        ' crossorigin="anonymous" referrerpolicy="no-referrer"></script>'
    )


# ── Construcción del HTML ──────────────────────────────────────────────────────

def _build_html(
    snapshot_rows: list,
    series_rows: list,
    metric_rows: list,
    al_rows: list,
    natures: list[str],
    filter_nature: str | None,
    auto_isins: list[str],
    generated_at: str,
) -> str:

    # ── KPIs de alertas
    n_alarm = sum(1 for r in al_rows if r[3] == "ALARM")
    n_warn  = sum(1 for r in al_rows if r[3] == "WARN")
    n_ok    = sum(1 for r in al_rows if r[3] == "OK")
    n_funds = len({r[0] for r in snapshot_rows})

    # ── Tabla por nature
    nat_sum  = nature_summary(snapshot_rows)
    ref_data = peer_refs(snapshot_rows)

    nature_rows_html = ""
    for nat, d in nat_sum.items():
        ret_s = _fmt_pct(d["avg_return"])
        vol_s = _fmt_pct(d["avg_vol"])
        dd_s  = _fmt_pct(d["avg_dd"])
        nature_rows_html += (
            f"<tr><td>{nat}</td><td>{d['count']}</td>"
            f"<td>{ret_s}</td><td>{vol_s}</td><td>{dd_s}</td></tr>\n"
        )
    if not nature_rows_html:
        nature_rows_html = "<tr><td colspan='5' style='color:var(--sub);text-align:center'>Sin datos en snapshot</td></tr>"

    # ── Tabla de alertas
    alert_rows_html = ""
    for isin, metric, window, level, rule_code, value, ref_value, fund_nature, fund_name in al_rows:
        color  = _LEVEL_COLOR.get(level, "#888")
        v_str  = _fmt_pct(value)
        rv_str = _fmt_pct(ref_value)
        alert_rows_html += (
            f"<tr data-level='{level}'>"
            f"<td><span class='badge' style='background:{color}'>{level}</span></td>"
            f"<td title='{isin}'>{(fund_name or isin)[:32]}</td>"
            f"<td>{fund_nature or '—'}</td>"
            f"<td>{_METRIC_LABELS.get(metric, metric)}</td>"
            f"<td>{_WINDOW_LABELS.get(window, window)}</td>"
            f"<td class='num'>{v_str}</td>"
            f"<td class='num'>{rv_str}</td>"
            f"<td style='font-size:11px;color:var(--sub)'>{rule_code}</td>"
            f"</tr>\n"
        )
    if not alert_rows_html:
        alert_rows_html = "<tr><td colspan='8' style='text-align:center;color:var(--sub)'>Sin alertas activas</td></tr>"

    # ── Gráficas por ISIN seleccionado
    ts_data: dict = {}
    isin_name: dict = {}
    isin_nature: dict = {}
    for isin, metric, window, dt, value, nature, name in series_rows:
        if value is None:
            continue
        isin_name[isin] = name or isin
        isin_nature[isin] = nature or ""
        ts_data.setdefault(metric, {}).setdefault(window, {}).setdefault(isin, []).append(
            (dt, round(value * 100, 4))
        )

    chart_blocks = ""
    if ts_data:
        # Determine nature for peer bands (majority nature of selected ISINs)
        natures_sel = [n for n in isin_nature.values() if n]
        peer_nature = max(set(natures_sel), key=natures_sel.count) if natures_sel else None

        for metric in ["roll_vol_ann", "roll_max_dd", "roll_return_ann"]:
            metric_data = ts_data.get(metric, {})
            for window in _WINDOW_ORDER:
                if window not in metric_data:
                    continue
                isins_data = metric_data[window]
                if not isins_data:
                    continue
                all_dates = sorted({d for pts in isins_data.values() for d, _ in pts})
                if len(all_dates) < 3:
                    continue

                datasets = []
                for ci, (isin, pts) in enumerate(isins_data.items()):
                    pts_dict = dict(pts)
                    data_pts = [pts_dict.get(d) for d in all_dates]
                    color = _SERIES_COLORS[ci % len(_SERIES_COLORS)]
                    datasets.append({
                        "label": (isin_name.get(isin, isin))[:24],
                        "data":  data_pts,
                        "borderColor": color,
                        "backgroundColor": color + "18",
                        "pointRadius": 0,
                        "borderWidth": 1.5,
                        "tension": 0.3,
                        "spanGaps": True,
                    })

                # Líneas de referencia de percentiles del peer group
                if peer_nature and peer_nature in ref_data:
                    peer_m = ref_data[peer_nature].get(metric, {})
                    for pct_key, pct_label, pct_color, dash in [
                        ("p50", "Mediana",  "#9E9E9E", [5, 5]),
                        ("p25", "P25 cat.", "#BDBDBD", [3, 3]),
                        ("p75", "P75 cat.", "#BDBDBD", [3, 3]),
                    ]:
                        if pct_key in peer_m:
                            pv = round(peer_m[pct_key] * 100, 4)
                            datasets.append({
                                "label": pct_label,
                                "data":  [pv] * len(all_dates),
                                "borderColor": pct_color,
                                "backgroundColor": "transparent",
                                "borderDash": dash,
                                "pointRadius": 0,
                                "borderWidth": 1,
                                "tension": 0,
                                "spanGaps": True,
                            })

                chart_id  = f"chart_{metric}_{window}"
                w_label   = _WINDOW_LABELS.get(window, window)
                dates_str = json.dumps(all_dates)
                ds_str    = json.dumps(datasets)
                chart_blocks += f"""
<div class="chart-card">
  <h3>{_METRIC_LABELS.get(metric, metric)} — {w_label}</h3>
  <canvas id="{chart_id}" height="220"></canvas>
</div>
<script>
(function() {{
  var ctx = document.getElementById('{chart_id}').getContext('2d');
  new Chart(ctx, {{
    type: 'line',
    data: {{ labels: {dates_str}, datasets: {ds_str} }},
    options: {{
      responsive: true,
      interaction: {{ mode: 'index', intersect: false }},
      plugins: {{
        legend: {{ position: 'right', labels: {{ boxWidth: 8, font: {{ size: 10 }} }} }},
        tooltip: {{
          callbacks: {{
            label: function(ctx) {{
              var v = ctx.parsed.y;
              return ctx.dataset.label + ': ' + (v != null ? v.toFixed(2) + '%' : '\u2014');
            }}
          }}
        }}
      }},
      scales: {{
        x: {{ ticks: {{ maxTicksLimit: 8, font: {{ size: 9 }} }},
              grid: {{ color: 'rgba(128,128,128,0.1)' }} }},
        y: {{ ticks: {{ callback: function(v) {{ return v + '%'; }}, font: {{ size: 9 }} }},
              grid: {{ color: 'rgba(128,128,128,0.1)' }} }}
      }}
    }}
  }});
}})();
</script>
"""
    charts_empty_msg = ""
    if not chart_blocks:
        if auto_isins:
            charts_empty_msg = (
                "<p style='color:var(--sub);padding:16px'>"
                "Los ISINs seleccionados no tienen series rolling disponibles. "
                "Ejecuta P2 con <code>ROLLING_STATS_ENABLED=True</code> para generarlas.</p>"
            )
        else:
            charts_empty_msg = (
                "<p style='color:var(--sub);padding:16px'>"
                "Pasa <code>--isin ISIN1,ISIN2</code> o asegúrate de que existen alertas "
                "para auto-seleccionar fondos. "
                "Sin series que graficar.</p>"
            )

    # ── Tabla de métricas (pivot)
    metrics_dict = pivot_metrics(metric_rows)

    # Solo los ISINs auto-seleccionados o todos si no hay selección
    isins_for_table = auto_isins if auto_isins else sorted(metrics_dict.keys())[:50]

    hdr_scalar  = "".join(f"<th>{label}</th>" for _, label, _, _ in _TABLE_COLS)
    hdr_pctile  = "".join(f"<th>{label}</th>" for _, label in _PCTILE_TABLE_COLS)
    metrics_body = ""
    for isin in isins_for_table:
        m = metrics_dict.get(isin)
        if not m:
            continue
        scalar_cells = ""
        for key, label, is_pct, decs in _TABLE_COLS:
            v = m.get(key)
            if is_pct:
                scalar_cells += f"<td class='num'>{_fmt_pct(v, decs)}</td>"
            else:
                scalar_cells += f"<td class='num'>{_fmt_num(v, decs)}</td>"
        pctile_cells = "".join(
            f"<td>{_pctile_bar(m.get(key))}</td>"
            for key, _ in _PCTILE_TABLE_COLS
        )
        metrics_body += (
            f"<tr>"
            f"<td title='{isin}' style='max-width:220px;overflow:hidden;text-overflow:ellipsis'>"
            f"{m.get('fund_name','')[:32]}</td>"
            f"<td style='font-size:11px;color:var(--sub)'>{m.get('fund_nature','')}</td>"
            f"{scalar_cells}{pctile_cells}"
            f"</tr>\n"
        )
    if not metrics_body:
        metrics_body = "<tr><td colspan='20' style='text-align:center;color:var(--sub)'>Sin datos en fund_metrics para los ISINs seleccionados</td></tr>"

    # ── Chip de filtro por nature
    nature_chip = (
        f" <span style='background:var(--accent);color:#fff;padding:2px 10px;"
        f"border-radius:10px;font-size:11px;font-weight:600'>{filter_nature}</span>"
        if filter_nature else ""
    )
    # ── ISINs seleccionados info
    isin_info = ""
    if auto_isins and isin_name:
        items = [f"<li>{isin_name.get(i,i)[:40]} <code style='font-size:10px'>{i}</code></li>"
                 for i in auto_isins[:8]]
        isin_info = f"<ul style='padding:0 0 0 16px;margin:6px 0;font-size:12px'>" + "".join(items) + "</ul>"

    chartjs_script = _load_chartjs()

    # ── Ensamblaje final
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>P2 Dashboard{(' · ' + filter_nature) if filter_nature else ''}</title>
{chartjs_script}
<style>
:root {{
  --bg: #F4F6FA; --card: #FFFFFF; --text: #1A2235; --sub: #5A6B8A;
  --accent: #B97D28; --alarm: #C0392B; --warn: #D4943A; --ok: #27AE60;
  --border: rgba(0,0,0,0.08); --font: 'Segoe UI', system-ui, sans-serif;
}}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg: #0C1222; --card: #151F33; --text: #D4DBE8; --sub: #7A8BA8;
           --border: rgba(255,255,255,0.06); }}
}}
:root[data-theme="light"] {{ --bg: #F4F6FA; --card: #FFFFFF; --text: #1A2235;
  --sub: #5A6B8A; --border: rgba(0,0,0,0.08); }}
:root[data-theme="dark"]  {{ --bg: #0C1222; --card: #151F33; --text: #D4DBE8;
  --sub: #7A8BA8; --border: rgba(255,255,255,0.06); }}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ background: var(--bg); color: var(--text); font-family: var(--font); font-size: 14px; }}
/* Header */
header {{ background: var(--card); border-bottom: 1px solid var(--border);
         padding: 14px 24px; display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }}
header h1 {{ font-size: 17px; font-weight: 700; flex: 1; }}
header time {{ color: var(--sub); font-size: 12px; }}
.theme-btn {{ background: none; border: 1px solid var(--border); border-radius: 6px;
              padding: 4px 10px; font-size: 11px; cursor: pointer; color: var(--sub); }}
/* KPIs */
.kpis {{ display: flex; gap: 10px; padding: 14px 24px; flex-wrap: wrap; }}
.kpi {{ background: var(--card); border: 1px solid var(--border); border-radius: 8px;
        padding: 10px 16px; min-width: 110px; }}
.kpi .num {{ font-size: 24px; font-weight: 800; font-variant-numeric: tabular-nums; }}
.kpi .lbl {{ font-size: 10px; color: var(--sub); text-transform: uppercase;
              letter-spacing: .06em; margin-top: 2px; }}
.kpi.alarm .num {{ color: var(--alarm); }}
.kpi.warn  .num {{ color: var(--warn);  }}
.kpi.ok    .num {{ color: var(--ok);    }}
/* Sections */
section {{ padding: 0 24px 24px; }}
section h2 {{ font-size: 11px; font-weight: 700; text-transform: uppercase;
              letter-spacing: .07em; color: var(--sub); padding: 18px 0 10px; }}
/* Charts */
.charts {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(460px, 1fr)); gap: 14px; }}
.chart-card {{ background: var(--card); border: 1px solid var(--border);
               border-radius: 8px; padding: 14px; }}
.chart-card h3 {{ font-size: 11px; font-weight: 600; color: var(--sub); margin-bottom: 8px; }}
/* Tables */
.tbl-wrap {{ overflow-x: auto; border-radius: 8px; border: 1px solid var(--border); }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
thead {{ position: sticky; top: 0; background: var(--bg); z-index: 1; }}
th, td {{ padding: 7px 12px; text-align: left; border-bottom: 1px solid var(--border);
           white-space: nowrap; }}
th {{ font-weight: 700; font-size: 10px; text-transform: uppercase;
      letter-spacing: .05em; color: var(--sub); cursor: pointer; user-select: none; }}
th:hover {{ color: var(--text); }}
td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
tr:last-child td {{ border-bottom: none; }}
tr:hover {{ background: rgba(128,128,128,0.04); }}
/* Alert filters */
.filters {{ display: flex; gap: 8px; padding: 0 24px 12px; flex-wrap: wrap; }}
.filter-btn {{ background: var(--card); border: 1px solid var(--border); border-radius: 6px;
               padding: 4px 12px; font-size: 12px; cursor: pointer; color: var(--sub); }}
.filter-btn.active {{ background: var(--accent); color: #fff; border-color: var(--accent); }}
/* Badges */
.badge {{ display: inline-block; padding: 2px 8px; border-radius: 10px;
          color: #fff; font-size: 10px; font-weight: 700; }}
/* Info box */
.info-box {{ background: var(--card); border: 1px solid var(--border); border-radius: 8px;
              padding: 12px 16px; margin-bottom: 12px; font-size: 12px; color: var(--sub); }}
.info-box strong {{ color: var(--text); }}
</style>
</head>
<body>

<header>
  <h1>P2 Dashboard — Indicadores Rolling{nature_chip}</h1>
  <time>{generated_at}</time>
  <button class="theme-btn" data-theme-toggle onclick="toggleTheme()">☽ Tema</button>
</header>

<!-- 1. KPIs -->
<div class="kpis">
  <div class="kpi"><div class="num">{n_funds}</div><div class="lbl">Fondos (snapshot)</div></div>
  <div class="kpi alarm"><div class="num">{n_alarm}</div><div class="lbl">Alarmas</div></div>
  <div class="kpi warn"><div class="num">{n_warn}</div><div class="lbl">Avisos</div></div>
  <div class="kpi ok"><div class="num">{n_ok}</div><div class="lbl">OK</div></div>
</div>

<!-- 2. Tabla por nature -->
<section>
  <h2>Resumen por categoría (rolling 1a — snapshot)</h2>
  <div class="tbl-wrap">
  <table>
    <thead><tr>
      <th>Fund Nature</th><th>Fondos</th>
      <th>Retorno med.</th><th>Volatilidad med.</th><th>Max DD med.</th>
    </tr></thead>
    <tbody>{nature_rows_html}</tbody>
  </table>
  </div>
</section>

<!-- 3. Gráficas — ISINs seleccionados -->
<section>
  <h2>Series rolling — fondos seleccionados</h2>
  {'<div class="info-box"><strong>Fondos graficados:</strong>' + isin_info + '</div>' if isin_info else ''}
  <div class="charts">
    {chart_blocks or charts_empty_msg}
  </div>
</section>

<!-- 4. Tabla de métricas -->
<section>
  <h2>Métricas escalares — fondos seleccionados</h2>
  <div class="tbl-wrap">
  <table id="tbl-metrics">
    <thead><tr>
      <th onclick="sortTable('tbl-metrics',0)">Fondo</th>
      <th onclick="sortTable('tbl-metrics',1)">Categoría</th>
      {hdr_scalar}
      {hdr_pctile}
    </tr></thead>
    <tbody>{metrics_body}</tbody>
  </table>
  </div>
</section>

<!-- 5. Alertas activas -->
<div class="filters">
  <span style="font-size:11px;color:var(--sub);line-height:28px">Filtrar:</span>
  <button class="filter-btn active" onclick="filterAlerts('ALL')">Todos</button>
  <button class="filter-btn" onclick="filterAlerts('ALARM')">Alarmas</button>
  <button class="filter-btn" onclick="filterAlerts('WARN')">Avisos</button>
  <button class="filter-btn" onclick="filterAlerts('OK')">OK</button>
</div>
<section>
  <h2>Alertas activas</h2>
  <div class="tbl-wrap">
  <table id="tbl-alerts">
    <thead><tr>
      <th onclick="sortTable('tbl-alerts',0)">Nivel</th>
      <th onclick="sortTable('tbl-alerts',1)">Fondo</th>
      <th onclick="sortTable('tbl-alerts',2)">Categoría</th>
      <th onclick="sortTable('tbl-alerts',3)">Métrica</th>
      <th onclick="sortTable('tbl-alerts',4)">Ventana</th>
      <th onclick="sortTable('tbl-alerts',5)">Valor</th>
      <th onclick="sortTable('tbl-alerts',6)">Ref. cat.</th>
      <th>Regla</th>
    </tr></thead>
    <tbody>{alert_rows_html}</tbody>
  </table>
  </div>
</section>

<script>
// Theme toggle
function toggleTheme() {{
  var root = document.documentElement;
  root.dataset.theme = (root.dataset.theme === 'dark') ? 'light' : 'dark';
}}

// Alert level filter
function filterAlerts(level) {{
  document.querySelectorAll('.filter-btn').forEach(function(b) {{
    b.classList.toggle('active', b.textContent.trim() === (
      level === 'ALL' ? 'Todos' : level === 'ALARM' ? 'Alarmas' :
      level === 'WARN' ? 'Avisos' : 'OK'));
  }});
  document.querySelectorAll('#tbl-alerts tbody tr').forEach(function(tr) {{
    tr.style.display = (level === 'ALL' || tr.dataset.level === level) ? '' : 'none';
  }});
}}

// Table sort
var _sortState = {{}};
function sortTable(tableId, col) {{
  var tbl = document.getElementById(tableId);
  var rows = Array.from(tbl.querySelectorAll('tbody tr'));
  var asc  = !_sortState[tableId + '_' + col];
  _sortState[tableId + '_' + col] = asc;
  rows.sort(function(a, b) {{
    var av = a.cells[col] ? a.cells[col].textContent.trim() : '';
    var bv = b.cells[col] ? b.cells[col].textContent.trim() : '';
    var an = parseFloat(av); var bn = parseFloat(bv);
    if (!isNaN(an) && !isNaN(bn)) return asc ? an - bn : bn - an;
    return asc ? av.localeCompare(bv) : bv.localeCompare(av);
  }});
  var tbody = tbl.querySelector('tbody');
  rows.forEach(function(r) {{ tbody.appendChild(r); }});
}}
</script>
</body>
</html>
"""


# ── Punto de entrada ──────────────────────────────────────────────────────────

def generate_dashboard(
    db_path: Path = DB_PATH,
    output_path: Path | None = None,
    nature: str | None = None,
    isins: list[str] | None = None,
) -> Path:
    """
    Genera el dashboard HTML P2.

    Args:
        db_path:     Ruta a fondos.sqlite.
        output_path: Ruta de salida del HTML (por defecto out/reports/).
        nature:      Filtrar todo el informe a un Fund_Nature concreto.
        isins:       Lista de ISINs cuyos series rolling se grafican (max 8).
                     Si None, se auto-seleccionan hasta 8 fondos con más ALARMs.
    """
    conn = sqlite3.connect(str(db_path), timeout=60)
    try:
        print("[1/5] Cargando snapshot (tier-1, rolling_1y)…", flush=True)
        snap_rows = _load_snapshot(conn, nature)

        print(f"[2/5] Cargando alertas…", flush=True)
        al_rows   = _load_alerts(conn, nature)

        # Determinar ISINs a graficar
        auto_isins = isins if isins else _auto_select_isins(al_rows)
        auto_isins = auto_isins[:8]  # hard cap

        print(f"[3/5] Cargando series para {len(auto_isins)} ISINs (tier-2)…", flush=True)
        series_rows = _load_series(conn, auto_isins)

        print(f"[4/5] Cargando métricas escalares para {len(auto_isins)} ISINs…", flush=True)
        metric_rows = _load_scalar_metrics(conn, auto_isins)

        natures = [r[0] for r in conn.execute(_SQL_NATURES).fetchall()]
    finally:
        conn.close()

    generated_at = date.today().isoformat()
    html = _build_html(
        snap_rows, series_rows, metric_rows, al_rows,
        natures, nature, auto_isins, generated_at,
    )

    if output_path is None:
        out_dir = _ROOT / "out" / "reports"
        out_dir.mkdir(parents=True, exist_ok=True)
        suffix = f"_{nature.replace(' ', '_')}" if nature else ""
        output_path = out_dir / f"rolling_dashboard{suffix}_{generated_at}.html"

    Path(output_path).write_text(html, encoding="utf-8")
    print(f"[5/5] Dashboard generado: {output_path}", flush=True)
    print(
        f"      Snapshot: {len(snap_rows)} filas | "
        f"Series: {len(series_rows)} filas | "
        f"Métricas: {len(metric_rows)} filas | "
        f"Alertas: Alarma={sum(1 for r in al_rows if r[3]=='ALARM')} "
        f"Aviso={sum(1 for r in al_rows if r[3]=='WARN')}"
    )
    return output_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Genera dashboard rolling HTML P2 (v27 — bounded)")
    parser.add_argument("--output", default=None,
                        help="Ruta de salida del HTML")
    parser.add_argument("--nature", default=None,
                        help="Filtrar por Fund_Nature (ej. 'Mixtos')")
    parser.add_argument("--isin",   default=None,
                        help="ISINs a graficar, separados por comas (max 8). "
                             "Si se omite, auto-selecciona los fondos con más ALARMs.")
    parser.add_argument("--db",     default=None,
                        help="Ruta alternativa a fondos.sqlite")
    args = parser.parse_args()

    db_path    = Path(args.db) if args.db else DB_PATH
    out_path   = Path(args.output) if args.output else None
    isin_list  = [i.strip() for i in args.isin.split(",") if i.strip()] if args.isin else None

    generate_dashboard(db_path=db_path, output_path=out_path,
                       nature=args.nature, isins=isin_list)
