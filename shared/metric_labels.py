# shared/metric_labels.py
# -*- coding: utf-8 -*-
"""
FND-0428: the ONE source of the header and the definition of every metric column the monthly report (sheet 1_Cartera) shows (P#11).

Why it exists: the report showed "Retorno real %" for a figure that is an ANNUALIZED, IPC-deflated, SINCE-INCEPTION return, and "Exceso obj %" subtracted a
NOMINAL objective from it. A reader took 8.08% for a year-to-date or twelve-month return. Every displayed column is now declared here with the
(metric, horizon, real_flag) it reads, so a header can never hide its horizon or its basis, and a test fails when a displayed column has no entry.

Language: one language per column (P#8): Spanish, like the rest of the report. Pure (no database, no pandas): the labels are data.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MetricLabel:
    metric: str
    horizon: str
    real_flag: int
    label: str          # the column header
    definition: str     # the legend line printed under the sheet


_LABELS = (
    MetricLabel("return_cum", "ytd", 0, "Rent. YTD % (nominal)",
                "Rentabilidad acumulada del año natural hasta el último VL del fondo; nominal; no anualizada. Un fondo lanzado este año muestra la rentabilidad desde su primer VL."),
    MetricLabel("return_ann", "rolling_1y", 0, "Rent. 1A % (nominal)",
                "Rentabilidad de los últimos 12 meses sobre VL mensuales; nominal. El plazo real de la ventana figura en la columna «Ventana 1A (meses)»."),
    MetricLabel("return_ann", "since_inception", 1, "Rent. desde inicio % anualiz. (real IPC)",
                "Rentabilidad geométrica ANUALIZADA desde el primer VL del fondo, deflactada por el IPC de España. No es un dato del último año."),
    MetricLabel("return_ann", "since_inception", 0, "Rent. desde inicio % anualiz. (nominal)",
                "Rentabilidad geométrica anualizada desde el primer VL del fondo; nominal."),
    MetricLabel("max_dd", "since_inception", 0, "Max DD % (desde inicio)",
                "Máxima caída desde un máximo, sobre todo el historial del fondo; nominal."),
    MetricLabel("fx_contribution_ann", "since_inception", 0, "Divisa contrib. % anualiz. (desde inicio)",
                "Contribución anualizada de la divisa a la rentabilidad en EUR, sobre todo el historial; nominal."),
    MetricLabel("beta_oil", "since_inception", 0, "Beta petróleo (desde inicio)", "Sensibilidad al petróleo (WTI), regresión sobre todo el historial."),
    MetricLabel("beta_rate_eu", "since_inception", 0, "Beta tipos UE (desde inicio)", "Sensibilidad a los tipos de la zona euro, regresión sobre todo el historial."),
    MetricLabel("beta_rate_us", "since_inception", 0, "Beta tipos EE.UU. (desde inicio)", "Sensibilidad a los tipos de EE.UU., regresión sobre todo el historial."),
    MetricLabel("macro_r2", "since_inception", 0, "R² macro (desde inicio)", "Poder explicativo del modelo macro, sobre todo el historial."),
    MetricLabel("alpha_persistence", "since_inception", 0, "Persistencia alfa (desde inicio)", "Proporción de ventanas con alfa positivo frente a su categoría, sobre todo el historial."),
    MetricLabel("srri_nav", "since_inception", 0, "SRRI calculado (desde inicio)", "SRRI estimado a partir de la volatilidad realizada de todo el historial."),
    MetricLabel("short_max_drawdown", "rolling_6m", 0, "DD corto 6M %", "Máxima caída de los últimos 6 meses, sobre VL diarios."),
    MetricLabel("short_vol_adj", "rolling_3m", 0, "Vol ajustada 3M %", "Volatilidad ajustada por iliquidez de los últimos 3 meses, sobre VL diarios."),
    MetricLabel("short_liquidity_flag", "rolling_6m", 0, "Indicador liquidez 6M", "Indicador de VL poco fiables en los últimos 6 meses (0 a 1)."),
)

_INDEX = {(m.metric, m.horizon, m.real_flag): m for m in _LABELS}

# Columns of the sheet that are not a fund_metrics row: the header says what the number is.
REPORT_ONLY_LABELS = {
    "peso": MetricLabel("", "", 0, "Peso %", "Peso del fondo en la cartera maestra."),
    "score": MetricLabel("", "", 0, "Score (v1, régimen del escenario)", "Puntuación compuesta v1 calculada bajo el régimen del escenario."),
    "objetivo": MetricLabel("", "", 0, "Obj. IPC+M3 % (nominal anual)", "Objetivo de la cartera: IPC + M3 interanual (nominal, anual)."),
    "exceso": MetricLabel("", "", 0, "Exceso 1A vs obj. % (nominal)", "Rentabilidad nominal a 1 año menos el objetivo nominal anual: misma base y mismo plazo."),
    "ventana_1a": MetricLabel("", "", 0, "Ventana 1A (meses)", "Plazo real, en meses, de la ventana de la columna «Rent. 1A» (los VL mensuales son el último de cada mes)."),
    "historia": MetricLabel("", "", 0, "Historia (años)", "Años entre el primer y el último VL mensual del fondo."),
    "fecha_vl": MetricLabel("", "", 0, "Fecha último VL", "Fecha del último VL del fondo: todas las rentabilidades de la fila son a esa fecha."),
    "vl_obsoleto": MetricLabel("", "", 0, "VL obsoleto", "SI si el último VL es anterior al límite de antigüedad de P3 (`P3_FUND_NAV_MAX_AGE_DAYS`)."),
}


def metric_label(metric: str, horizon: str, real_flag: int) -> MetricLabel:
    """The declared label of (metric, horizon, real_flag). A KeyError means a displayed column has no scoped definition: declare it here."""
    try:
        return _INDEX[(metric, horizon, int(real_flag))]
    except KeyError:
        raise KeyError(f"no report label declared for metric={metric!r} horizon={horizon!r} real_flag={real_flag!r} (shared/metric_labels.py)") from None


def label_for(metric: str, horizon: str, real_flag: int) -> str:
    return metric_label(metric, horizon, real_flag).label


def report_label(key: str) -> str:
    return REPORT_ONLY_LABELS[key].label


def legend_lines(keys=None) -> list:
    """`header: definition` lines for the legend footer, for the declared metric labels (all, in declaration order) plus the report-only ones named in `keys`."""
    out = [f"{m.label}: {m.definition}" for m in _LABELS]
    for k in (REPORT_ONLY_LABELS if keys is None else keys):
        out.append(f"{REPORT_ONLY_LABELS[k].label}: {REPORT_ONLY_LABELS[k].definition}")
    return out
