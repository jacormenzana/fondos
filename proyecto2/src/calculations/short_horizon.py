# proyecto2/src/calculations/short_horizon.py
# -*- coding: utf-8 -*-
"""
Métricas de horizonte corto sobre series NAV diarias (v24).

Calcula retorno acumulado, drawdown, volatilidad y flag de iliquidez
para ventanas rolling_1m / rolling_3m / rolling_6m sobre fund_nav_daily.

Todas las métricas se emiten con metric_version='d1' (METRIC_VERSION_SHORT).
La volatilidad incluye corrección AC(1) de Getmansky para detectar iliquidez.

Diseño:
  - Sin imports de pipeline.py ni core.io (R-7: tests sin pipeline).
  - Reutiliza proyecto2.src.calculations.drawdown.compute_drawdown / max_drawdown (DRY).
  - Emite tuplas (metric, value, real_flag) idénticas al contrato de consistency_metrics.

Catálogo de métricas emitidas (metric_version='d1'):
  short_return_cum        Retorno acumulado nominal no anualizado          real_flag=0
  short_return_cum_real   Retorno acumulado deflactado por IPC             real_flag=1
  short_max_drawdown      Máximo drawdown pico-valle (≤ 0)                real_flag=0
  short_vol_ann           Volatilidad diaria anualizada (x√252)           real_flag=0
  short_vol_adj           Vol diaria AC(1)-ajustada (iliquidez corregida) real_flag=0
  short_liquidity_flag    Fracción días cero/repetidos (0=líquido, 1=seco) real_flag=0
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .drawdown import compute_drawdown, max_drawdown

# ---------------------------------------------------------------------------
# Thresholds (sobreescriben los defaults de config cuando se importan desde
# tests sin el módulo completo — se puede llamar directamente aquí)
# ---------------------------------------------------------------------------
_LIQUIDITY_THRESHOLD_DEFAULT: float = 0.20   # >20% días sin cambio → ilíquido


# ---------------------------------------------------------------------------
# Funciones auxiliares
# ---------------------------------------------------------------------------

def _daily_returns(nav: pd.Series) -> pd.Series:
    """Retornos simples diarios a partir de serie NAV."""
    return nav.pct_change().dropna()


def _liquidity_flag(nav: pd.Series, threshold: float = _LIQUIDITY_THRESHOLD_DEFAULT) -> float:
    """
    Fracción de sesiones con NAV idéntico al día anterior (proxy de iliquidez).
    Fondos ilíquidos tienen NAVs repetidos durante días/semanas.

    Returns: float en [0, 1]; > threshold → datos diarios no confiables.
    """
    if len(nav) < 2:
        return float("nan")
    rets = nav.pct_change().dropna()
    zero_frac = float((rets == 0.0).sum()) / len(rets)
    return round(zero_frac, 4)


def _ac1_adjusted_vol(rets: pd.Series) -> float:
    """
    Volatilidad anualizada con corrección de autocorrelación de primer orden
    (método Getmansky, Lo, Makarov 2004, simplificado al lag-1).

    Para series con autocorrelación positiva (fondos ilíquidos), la volatilidad
    observada subestima la "verdadera" volatilidad. Corrección:
        sigma_adj = sigma * sqrt(1 + 2*rho1)   si rho1 > 0, sino sigma

    Retorna NaN si hay menos de 10 observaciones (insuficiente para AC).
    """
    if len(rets) < 10:
        return float("nan")
    sigma = float(rets.std(ddof=1))
    if np.isnan(sigma) or sigma == 0:
        return float("nan")
    rho1 = float(rets.autocorr(lag=1))
    if np.isnan(rho1) or rho1 <= 0:
        # Sin autocorrelación positiva: sin ajuste
        factor = 1.0
    else:
        factor = (1.0 + 2.0 * rho1) ** 0.5
    return round(sigma * factor * (252 ** 0.5), 6)


def _cumulative_return(nav: pd.Series) -> float:
    """Retorno acumulado simple (no anualizado): nav[-1]/nav[0] - 1."""
    if len(nav) < 2:
        return float("nan")
    return round(float(nav.iloc[-1] / nav.iloc[0]) - 1.0, 6)


def _deflate_nav(nav: pd.Series, dates: pd.Series,
                 ipc_df: pd.DataFrame | None) -> pd.Series | None:
    """
    Deflacta la serie NAV por el IPC (ratio: ipc[t] / ipc[t0]).
    Devuelve None si ipc_df es None o no hay solapamiento.
    """
    if ipc_df is None or ipc_df.empty:
        return None

    # Alinear IPC a fechas de sesiones diarias (forward-fill desde mensual)
    ipc = ipc_df.set_index("date")["ipc_index"]
    ipc_aligned = ipc.reindex(dates).ffill().bfill()

    if ipc_aligned.isna().all():
        return None

    ipc_base = ipc_aligned.iloc[0]
    if ipc_base == 0 or np.isnan(ipc_base):
        return None

    deflator = ipc_aligned / ipc_base
    return nav.values / deflator.values  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Función pública principal
# ---------------------------------------------------------------------------

def compute_short_horizon_metrics(
    nav_df: pd.DataFrame,
    ipc_df: pd.DataFrame | None,
    liquidity_threshold: float = _LIQUIDITY_THRESHOLD_DEFAULT,
) -> list[tuple[str, float, int]]:
    """
    Calcula todas las métricas de horizonte corto para la ventana de nav_df.

    Parámetros:
        nav_df: DataFrame con columnas ['date', 'nav'], diario, ya sliceado
                a la ventana del horizonte (ej. tail(21) para rolling_1m).
        ipc_df: DataFrame con columnas ['date', 'ipc_index'], mensual.
                Si None, se omiten las métricas reales.
        liquidity_threshold: fracción de retornos cero que marca iliquidez.

    Devuelve:
        Lista de tuplas (metric_name, value, real_flag) lista para _write_metrics.
        real_flag=0 = nominal; real_flag=1 = deflactado por IPC.
    """
    if nav_df.empty or len(nav_df) < 2:
        return []

    nav     = nav_df["nav"].reset_index(drop=True)
    dates   = nav_df["date"].reset_index(drop=True)
    rets    = _daily_returns(nav)
    dd_ser  = compute_drawdown(nav)

    results: list[tuple[str, float, int]] = []

    # -- Retorno acumulado nominal -------------------------------------------
    ret_cum = _cumulative_return(nav)
    results.append(("short_return_cum", ret_cum, 0))

    # -- Drawdown máximo ------------------------------------------------------
    mdd = max_drawdown(dd_ser)
    results.append(("short_max_drawdown", mdd, 0))

    # -- Volatilidad diaria anualizada (cruda) --------------------------------
    if len(rets) >= 2:
        vol_raw = round(float(rets.std(ddof=1)) * (252 ** 0.5), 6)
        results.append(("short_vol_ann", vol_raw, 0))

    # -- Volatilidad AC(1)-ajustada (iliquidez) -------------------------------
    vol_adj = _ac1_adjusted_vol(rets)
    if not np.isnan(vol_adj):
        results.append(("short_vol_adj", vol_adj, 0))

    # -- Flag de iliquidez ----------------------------------------------------
    liq_flag = _liquidity_flag(nav, liquidity_threshold)
    if not np.isnan(liq_flag):
        results.append(("short_liquidity_flag", liq_flag, 0))

    # -- Retorno acumulado real (deflactado por IPC) --------------------------
    nav_real = _deflate_nav(nav, dates, ipc_df)
    if nav_real is not None:
        nav_real_s = pd.Series(nav_real)
        ret_real = _cumulative_return(nav_real_s)
        results.append(("short_return_cum_real", ret_real, 1))

    return results
