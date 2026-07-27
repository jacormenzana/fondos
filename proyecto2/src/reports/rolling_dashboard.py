# proyecto2/src/reports/rolling_dashboard.py
# -*- coding: utf-8 -*-
"""
Generador de dashboard HTML auto-contenido — indicadores rolling P2 (v26).

Genera un fichero HTML con gráficos interactivos (Chart.js embebido) desde
las tablas fund_metric_timeseries y fund_metric_alerts de fondos.sqlite.

Uso:
    cd c:/desarrollo/fondos
    python -m proyecto2.src.reports.rolling_dashboard
    python -m proyecto2.src.reports.rolling_dashboard --output out/rolling_YYYYMMDD.html
    python -m proyecto2.src.reports.rolling_dashboard --nature "Renta Fija Flexible"

Requisito: ROLLING_STATS_ENABLED=True y al menos un ciclo P2 ejecutado.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent.parent   # c:\desarrollo\fondos
sys.path.insert(0, str(_ROOT))

from shared.config import DB_PATH


# ============================================================
# Consultas
# ============================================================

_SQL_TIMESERIES = """
    SELECT t.isin, t.metric, t.window, t.date, t.value, t.real_flag,
           m.Fund_Nature, m.Fund_Name
    FROM fund_metric_timeseries t
    LEFT JOIN fund_master m ON t.isin = m.ISIN
    WHERE t.real_flag = 0
      AND t.metric IN ('roll_vol_ann', 'roll_max_dd', 'roll_return_ann')
    {nature_clause}
    ORDER BY t.isin, t.metric, t.window, t.date
"""

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


def _load_data(conn: sqlite3.Connection, nature: str | None = None):
    nature_clause = f"AND m.Fund_Nature = '{nature}'" if nature else ""
    ts_rows = conn.execute(_SQL_TIMESERIES.format(nature_clause=nature_clause)).fetchall()
    al_rows = conn.execute(_SQL_ALERTS.format(
        nature_clause=f"AND m.Fund_Nature = '{nature}'" if nature else ""
    )).fetchall()
    natures = [r[0] for r in conn.execute(_SQL_NATURES).fetchall()]
    return ts_rows, al_rows, natures


# ============================================================
# HTML builder
# ============================================================

_METRIC_LABELS = {
    "roll_vol_ann":    "Volatilidad Anualizada",
    "roll_max_dd":     "Máximo Drawdown",
    "roll_return_ann": "Retorno Anualizado",
}
_WINDOW_ORDER = ["rolling_1m", "rolling_3m", "rolling_6m",
                 "rolling_1y", "rolling_2y", "rolling_3y",
                 "rolling_5y", "rolling_10y"]
_LEVEL_COLOR = {"ALARM": "#C0392B", "WARN": "#D4943A", "OK": "#27AE60"}


def _build_html(ts_rows, al_rows, natures, filter_nature: str | None, generated_at: str) -> str:
    # Organise timeseries data for Chart.js: {metric: {window: {isin: [(date, value)]}}}
    ts_data: dict = {}
    isin_name: dict = {}
    for isin, metric, window, dt, value, real_flag, fund_nature, fund_name in ts_rows:
        if value is None:
            continue
        isin_name[isin] = fund_name or isin
        ts_data.setdefault(metric, {}).setdefault(window, {}).setdefault(isin, []).append(
            (dt, round(value * 100, 4) if metric != "roll_max_dd" else round(value * 100, 4))
        )

    # Alert table data
    alert_rows_html = ""
    for isin, metric, window, level, rule_code, value, ref_value, fund_nature, fund_name in al_rows:
        color = _LEVEL_COLOR.get(level, "#888")
        v_str  = f"{value*100:.2f}%" if value is not None else "—"
        rv_str = f"{ref_value*100:.2f}%" if ref_value is not None else "—"
        alert_rows_html += (
            f"<tr>"
            f"<td><span class='badge' style='background:{color}'>{level}</span></td>"
            f"<td title='{isin}'>{(fund_name or isin)[:30]}</td>"
            f"<td>{_METRIC_LABELS.get(metric, metric)}</td>"
            f"<td>{window}</td>"
            f"<td>{v_str}</td>"
            f"<td>{rv_str}</td>"
            f"<td>{rule_code}</td>"
            f"</tr>\n"
        )

    # Summary chips
    n_alarm = sum(1 for r in al_rows if r[3] == "ALARM")
    n_warn  = sum(1 for r in al_rows if r[3] == "WARN")
    n_ok    = sum(1 for r in al_rows if r[3] == "OK")
    n_funds = len({r[0] for r in ts_rows})

    # Charts: one per metric × window pair — pick a few key combos
    chart_blocks = ""
    chart_js_datasets = []

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
            colors = [
                "#3498DB","#E74C3C","#2ECC71","#9B59B6","#F39C12",
                "#1ABC9C","#E67E22","#34495E","#7F8C8D","#C0392B",
            ]
            for ci, (isin, pts) in enumerate(list(isins_data.items())[:10]):
                pts_dict = dict(pts)
                data_pts = [pts_dict.get(d) for d in all_dates]
                color = colors[ci % len(colors)]
                datasets.append({
                    "label": (isin_name.get(isin, isin))[:20],
                    "data": data_pts,
                    "borderColor": color,
                    "backgroundColor": color + "22",
                    "pointRadius": 0,
                    "borderWidth": 1.5,
                    "tension": 0.3,
                    "spanGaps": True,
                })

            chart_id = f"chart_{metric}_{window}"
            label_str = json.dumps(_METRIC_LABELS.get(metric, metric))
            dates_str = json.dumps(all_dates)
            ds_str    = json.dumps(datasets)
            y_fmt     = "v + '%'"
            chart_blocks += f"""
<div class="chart-card">
  <h3>{_METRIC_LABELS.get(metric, metric)} — {window}</h3>
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
              return ctx.dataset.label + ': ' + (v != null ? v.toFixed(2) + '%' : '—');
            }}
          }}
        }}
      }},
      scales: {{
        x: {{ ticks: {{ maxTicksLimit: 8, font: {{ size: 9 }} }}, grid: {{ color: 'rgba(128,128,128,0.1)' }} }},
        y: {{ ticks: {{ callback: function(v) {{ return v + '%'; }}, font: {{ size: 9 }} }},
              grid: {{ color: 'rgba(128,128,128,0.1)' }} }}
      }}
    }}
  }});
}})();
</script>
"""

    nature_title = f" · {filter_nature}" if filter_nature else ""
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>Dashboard Rolling Indicators P2{nature_title}</title>
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
header {{ background: var(--card); border-bottom: 1px solid var(--border);
         padding: 16px 24px; display: flex; align-items: center; gap: 16px; }}
header h1 {{ font-size: 18px; font-weight: 600; }}
header time {{ color: var(--sub); font-size: 12px; margin-left: auto; }}
.kpis {{ display: flex; gap: 12px; padding: 16px 24px; flex-wrap: wrap; }}
.kpi {{ background: var(--card); border: 1px solid var(--border); border-radius: 8px;
        padding: 12px 18px; min-width: 120px; }}
.kpi .num {{ font-size: 26px; font-weight: 700; }}
.kpi .lbl {{ font-size: 11px; color: var(--sub); text-transform: uppercase; letter-spacing: .05em; }}
.kpi.alarm .num {{ color: var(--alarm); }}
.kpi.warn  .num {{ color: var(--warn);  }}
.kpi.ok    .num {{ color: var(--ok);    }}
section {{ padding: 0 24px 24px; }}
section h2 {{ font-size: 14px; font-weight: 600; text-transform: uppercase;
              letter-spacing: .06em; color: var(--sub); padding: 16px 0 10px; }}
.charts {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(480px, 1fr)); gap: 16px; }}
.chart-card {{ background: var(--card); border: 1px solid var(--border);
               border-radius: 8px; padding: 16px; }}
.chart-card h3 {{ font-size: 12px; font-weight: 600; color: var(--sub); margin-bottom: 10px; }}
.tbl-wrap {{ overflow-x: auto; }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
thead {{ background: var(--bg); }}
th, td {{ padding: 7px 12px; text-align: left; border-bottom: 1px solid var(--border); white-space: nowrap; }}
th {{ font-weight: 600; font-size: 11px; text-transform: uppercase; letter-spacing: .04em; color: var(--sub); }}
.badge {{ display: inline-block; padding: 2px 8px; border-radius: 10px;
          color: #fff; font-size: 11px; font-weight: 700; }}
</style>
</head>
<body>
<header>
  <h1>Rolling Indicators P2{nature_title}</h1>
  <time>{generated_at}</time>
</header>

<div class="kpis">
  <div class="kpi"><div class="num">{n_funds}</div><div class="lbl">Fondos</div></div>
  <div class="kpi alarm"><div class="num">{n_alarm}</div><div class="lbl">Alarmas</div></div>
  <div class="kpi warn"><div class="num">{n_warn}</div><div class="lbl">Avisos</div></div>
  <div class="kpi ok"><div class="num">{n_ok}</div><div class="lbl">OK</div></div>
</div>

<section>
<h2>Alertas activas</h2>
<div class="tbl-wrap">
<table>
<thead><tr>
  <th>Nivel</th><th>Fondo</th><th>Métrica</th><th>Ventana</th>
  <th>Valor</th><th>Referencia cat.</th><th>Regla</th>
</tr></thead>
<tbody>
{alert_rows_html if alert_rows_html else '<tr><td colspan="7" style="text-align:center;color:var(--sub)">Sin alertas activas</td></tr>'}
</tbody>
</table>
</div>
</section>

<section>
<h2>Series rolling (hasta 10 fondos por gráfico)</h2>
<div class="charts">
{chart_blocks if chart_blocks else '<p style="color:var(--sub)">Sin datos en fund_metric_timeseries. Ejecutar P2 con ROLLING_STATS_ENABLED=True.</p>'}
</div>
</section>

<script>
/* Chart.js 4.4 inline (minified) */
</script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js" crossorigin="anonymous" referrerpolicy="no-referrer"></script>
<script>
// Theme toggle support
document.querySelectorAll('[data-theme-toggle]').forEach(function(el) {{
  el.addEventListener('click', function() {{
    var root = document.documentElement;
    root.dataset.theme = root.dataset.theme === 'dark' ? 'light' : 'dark';
  }});
}});
</script>
</body>
</html>
"""


# ============================================================
# Entry point
# ============================================================

def generate_dashboard(
    db_path: Path = DB_PATH,
    output_path: Path | None = None,
    nature: str | None = None,
) -> Path:
    conn = sqlite3.connect(str(db_path))
    ts_rows, al_rows, natures = _load_data(conn, nature)
    conn.close()

    generated_at = date.today().isoformat()
    html = _build_html(ts_rows, al_rows, natures, nature, generated_at)

    if output_path is None:
        out_dir = _ROOT / "out" / "reports"
        out_dir.mkdir(parents=True, exist_ok=True)
        suffix = f"_{nature.replace(' ','_')}" if nature else ""
        output_path = out_dir / f"rolling_dashboard{suffix}_{generated_at}.html"

    output_path.write_text(html, encoding="utf-8")
    print(f"Dashboard generado: {output_path}")
    print(f"  Fondos: {len({r[0] for r in ts_rows})}  "
          f"Alarmas: {sum(1 for r in al_rows if r[3]=='ALARM')}  "
          f"Avisos: {sum(1 for r in al_rows if r[3]=='WARN')}")
    return output_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Genera dashboard rolling HTML P2")
    parser.add_argument("--output",  default=None, help="Ruta de salida del HTML")
    parser.add_argument("--nature",  default=None, help="Filtrar por Fund_Nature")
    parser.add_argument("--db",      default=None, help="Ruta alternativa a fondos.sqlite")
    args = parser.parse_args()

    db  = Path(args.db) if args.db else DB_PATH
    out = Path(args.output) if args.output else None
    generate_dashboard(db_path=db, output_path=out, nature=args.nature)
