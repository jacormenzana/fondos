# proyecto2/src/calculations/regime_returns.py
# -*- coding: utf-8 -*-
"""
Calculo de retornos y riesgo por regimen macroeconomico para cada fondo.

Para cada fondo, cruza su serie de retornos mensuales con la clasificacion
historica de regimenes y calcula estadisticas de rendimiento por regimen.

Metricas generadas por fondo (horizon=since_inception, real_flag=0):
    return_ann_{regimen}      retorno anualizado en ese regimen (decimal, ej. 0.08 = 8%)
    sharpe_{regimen}          ratio Sharpe en ese regimen (rf anualizado)
    sortino_{regimen}         ratio Sortino en ese regimen (downside deviation)
    vol_ann_{regimen}         volatilidad anualizada en ese regimen (decimal)
    max_dd_{regimen}          max drawdown sobre NAV en ese regimen
    n_obs_{regimen}           numero de meses en ese regimen con retorno disponible
    regime_coverage_ratio     fraccion de 7 regimenes con n_obs >= MIN_OBS_REGIME [0,1] (P3-01)
    crisis_stress_score_mdd   max drawdown sobre meses de Crisis_Financiera (P3-02)
    crisis_stress_score_ttr   meses de recuperacion sobre meses de Crisis_Financiera (P3-02)

Canonicalizacion (P0, 2026-09-15): return_ann/vol_ann/sharpe/sortino por
regimen se calculan sobre retornos SIMPLES (no logaritmicos) delegando en
src.calculations.returns.*_from_returns -- variantes de la capa canonica
(returns.annualized_return/sharpe_ratio/sortino_ratio) que operan sobre un
array de retornos periodicos en lugar de una serie NAV de niveles, porque la
muestra de un regimen es un subconjunto disperso y no contiguo de meses (no
existe una serie NAV valida que reconstruir). Antes de esta canonicalizacion
el fichero mantenia 4 formulas locales divergentes de la capa canonica
(retornos log x100, MAR mensual geometrico, downside deviation solo sobre el
subconjunto negativo con ddof=1) -- ver returns.py::downside_deviation_ann
para el historial completo (root-caused 2026-09-14, ISIN BE0058182792).

Sufijo de regimen (nombre en minusculas con guiones bajos):
    expansion
    recalentamiento
    recalentamiento_tardio
    estanflacion
    contraccion
    shock_energetico
    crisis_financiera

Requisitos:
    MIN_OBS_REGIME = 12 meses minimos en un regimen para calcular estadisticas
    MIN_NAV_TOTAL  = 36 meses totales de NAV (mismo umbral que macro_sensitivity)

Uso en pipeline:
    from src.calculations.regime_returns import (
        load_regime_history, compute_regime_returns
    )
    regime_df = load_regime_history(conn)          # una vez, fuera del bucle
    metrics   = compute_regime_returns(nav_df, regime_df)

Nota sobre cobertura de regimenes (P2-03 / cierre 2026-08-18):
    En la muestra 2000-03 → 2026-08 (320 meses), solo 5 de los 7 regimenes aparecen.
    Recalentamiento y Recalentamiento_Tardio registran n_obs=0 en todos los fondos.
    Causa: Shock_Energetico (WTI interanual > 25%) tiene prioridad en el clasificador
    y captura todos los periodos de IPC alto del ciclo 2021-2023.  En ningun mes
    del registro disponible se dan conjuntamente (a) IPC alto, (b) crecimiento fuerte /
    tasas subiendo, (c) ausencia de shock energetico o crisis financiera.
    Esto es un resultado empirico del periodo analizado, no un defecto de codigo.
    Si en el futuro un ciclo de IPC alto ocurre sin shock energetico, los dos
    regimenes faltantes se activaran automaticamente sin cambios de codigo.
    regime_coverage_ratio refleja correctamente 5/7 = 0.714 para todos los fondos
    (metrica P3-01, no un error).
"""

import numpy as np
import pandas as pd
import sqlite3

from shared.config import (
    RISK_FREE_RATE_ANN,
    REGIME_MIN_OBS as MIN_OBS_REGIME,
    REGIME_MIN_NAV_TOTAL as MIN_NAV_TOTAL,
    REGIME_MIN_OBS_SORTINO_DOWNSIDE as MIN_OBS_SORTINO_DOWNSIDE,
)
# Fase 1g (P#11/R-1): este dict se re-declaraba a mano aqui, por separado de
# la definicion equivalente en proyecto3/src/regime_classifier.py -- ambas
# coincidian por disciplina, no por construccion. Fuente unica ahora en
# shared/regime_taxonomy.py (ver ese modulo para el porque vive en shared/
# y no en proyecto3/).
from shared.regime_taxonomy import REGIME_SUFFIX as _REGIME_SUFFIX
from src.calculations.drawdown import compute_drawdown, max_drawdown, time_to_recovery
from src.calculations.returns import (
    annualized_return_from_returns,
    annualized_volatility_from_returns,
    sharpe_ratio_from_returns,
    sortino_ratio_from_returns,
)


# ============================================================
# Carga del historico de regimenes
# ============================================================

def load_regime_history(conn: sqlite3.Connection) -> pd.DataFrame:
    """
    Construye el historico de regimenes usando RegimeClassifier.

    Devuelve DataFrame indexado por fecha (fin de mes) con columna 'regime'.
    Si no hay datos macro suficientes devuelve DataFrame vacio.

    Se llama UNA VEZ fuera del bucle de fondos -- es comun a todos.
    """
    try:
        # Importacion local para evitar dependencia circular en tests.
        # Repo root ya esta en sys.path (production: cwd al invocar via -m;
        # tests: proyecto2/pytest.ini pythonpath) -- no requiere bootstrap propio.
        from proyecto3.src.regime_classifier import RegimeClassifier

        clf    = RegimeClassifier(conn)
        hist   = clf.classify_historical()

        if hist.empty:
            print("  [RegimeReturns] Sin historico de regimenes disponible")
            return pd.DataFrame()

        # Normalizar indice a fin de mes
        hist.index = pd.to_datetime(hist.index) + pd.offsets.MonthEnd(0)
        hist = hist[~hist.index.duplicated(keep="last")]

        n_regimes = hist["regime"].nunique()
        print(f"  [RegimeReturns] Historico cargado: {len(hist)} meses | "
              f"{n_regimes} regimenes distintos")
        return hist[["regime"]]

    except Exception as e:
        print(f"  [RegimeReturns] Error cargando historico: {e}")
        return pd.DataFrame()


# ============================================================
# Calculo de estadisticas por regimen para un fondo
# ============================================================

def _sortino(monthly_returns: np.ndarray) -> float | None:
    """
    Sortino ratio anualizado por regimen.

    Guarda de cobertura local (numero de observaciones con exceso negativo
    frente al MAR) + valor delegado a la capa canonica
    (returns.sortino_ratio_from_returns, que reutiliza
    returns.downside_deviation_ann sin duplicar la formula). La guarda es
    una politica de reporting especifica de este modulo -- "no informar
    sortino por regimen si hay menos de MIN_OBS_SORTINO_DOWNSIDE meses
    negativos, la estimacion de semi-desviacion no es fiable" -- no una
    formula estadistica, por eso permanece local.

    Devuelve None si no hay suficientes retornos con exceso negativo.
    """
    mar_per_period = RISK_FREE_RATE_ANN / 12
    arr = np.asarray(monthly_returns, dtype=float)
    excess = arr - mar_per_period
    if int(np.sum(excess < 0)) < MIN_OBS_SORTINO_DOWNSIDE:
        return None
    value = sortino_ratio_from_returns(arr, RISK_FREE_RATE_ANN, periods_per_year=12)
    return None if np.isnan(value) else float(value)


def compute_regime_returns(
    nav_df:    pd.DataFrame,
    regime_df: pd.DataFrame,
) -> list[tuple]:
    """
    Calcula retornos y riesgo por regimen para un fondo.

    Parametros:
        nav_df:    DataFrame con columnas [date, nav] (fechas fin de mes)
        regime_df: DataFrame indexado por fecha con columna 'regime'
                   (salida de load_regime_history)

    Devuelve lista de (metric_name, value, real_flag).
    Devuelve lista vacia si no hay suficientes datos o regimes.
    """
    if nav_df.empty or regime_df is None or regime_df.empty:
        return []

    if len(nav_df) < MIN_NAV_TOTAL:
        return []

    # Calcular retornos simples mensuales (canonicalizado P0 2026-09-15 --
    # antes retornos logaritmicos, ver docstring del modulo)
    nav = nav_df.set_index("date")["nav"].sort_index()
    nav.index = pd.to_datetime(nav.index) + pd.offsets.MonthEnd(0)
    # MonthEnd snap puede crear duplicados si hay dos registros en el mismo mes;
    # conservar el último (cierre de mes más reciente)
    nav = nav[~nav.index.duplicated(keep="last")]
    r_simple = nav.pct_change().dropna()

    # Cruzar con regimenes
    merged = pd.concat(
        [r_simple.rename("r_simple"), regime_df["regime"]],
        axis=1, join="inner"
    ).dropna()

    if merged.empty:
        return []

    metrics   = []
    n_covered = 0  # P3-01: count regimes with n_obs >= MIN_OBS_REGIME

    for regime_name, suffix in _REGIME_SUFFIX.items():
        mask      = merged["regime"] == regime_name
        r_regime  = merged.loc[mask, "r_simple"].values
        n_obs     = len(r_regime)

        # Siempre persistir n_obs (aunque sea 0 -- util para saber cobertura)
        metrics.append((f"n_obs_{suffix}", float(n_obs), 0))

        if n_obs < MIN_OBS_REGIME:
            # Sin suficientes datos -- no calcular estadisticas
            continue

        n_covered += 1
        ret_ann = annualized_return_from_returns(r_regime, periods_per_year=12)
        vol_ann = annualized_volatility_from_returns(r_regime, periods_per_year=12)
        sharpe  = sharpe_ratio_from_returns(r_regime, RISK_FREE_RATE_ANN, periods_per_year=12)
        sortino = _sortino(r_regime)

        metrics.append((f"return_ann_{suffix}", ret_ann,  0))
        metrics.append((f"vol_ann_{suffix}",    vol_ann,  0))
        metrics.append((f"sharpe_{suffix}",     sharpe,   0))
        if sortino is not None:
            metrics.append((f"sortino_{suffix}", sortino, 0))

        # max_dd over the NAV level at regime dates (mirrors crisis_stress_score_mdd)
        regime_dates = merged[mask].index
        nav_regime   = nav.loc[nav.index.isin(regime_dates)].reset_index(drop=True)
        if len(nav_regime) >= 2:
            mdd = max_drawdown(compute_drawdown(nav_regime))
            metrics.append((f"max_dd_{suffix}", float(mdd), 0))

    # P3-01: fraction of regimes with sufficient history [0, 1]
    metrics.append(("regime_coverage_ratio", n_covered / len(_REGIME_SUFFIX), 0))

    # P3-02: drawdown + recovery during Crisis_Financiera months only
    crisis_mask  = merged["regime"] == "Crisis_Financiera"
    crisis_dates = merged[crisis_mask].index
    if len(crisis_dates) >= MIN_OBS_REGIME:
        nav_crisis = nav.loc[nav.index.isin(crisis_dates)].reset_index(drop=True)
        dd_crisis  = compute_drawdown(nav_crisis)
        mdd        = max_drawdown(dd_crisis)
        ttr        = time_to_recovery(nav_crisis)
        metrics.append(("crisis_stress_score_mdd", float(mdd), 0))
        ttr_val = None if np.isnan(ttr) else float(ttr)
        metrics.append(("crisis_stress_score_ttr", ttr_val, 0))

    return metrics
