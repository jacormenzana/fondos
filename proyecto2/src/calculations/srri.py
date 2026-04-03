# proyecto2/src/calculations/srri.py
# -*- coding: utf-8 -*-
"""
Calculo del SRRI (Synthetic Risk and Reward Indicator) segun metodologia
ESMA - Guidelines on the methodology for the calculation of the synthetic
risk and reward indicator in the Key Investor Information Document (CESR/10-673).

Resumen de la metodologia:
  1. Calcular retornos semanales de la serie NAV mensual (aproximacion)
     o directamente desde NAV semanal si esta disponible.
     Con datos mensuales se usa retornos mensuales anualizados.
  2. Calcular la volatilidad anualizada de esos retornos.
  3. Mapear la volatilidad al bucket SRRI segun tabla ESMA.

Tabla de buckets ESMA (volatilidad anualizada):
  1:  [0%,     0.5%)
  2:  [0.5%,   2.0%)
  3:  [2.0%,   5.0%)
  4:  [5.0%,  10.0%)
  5:  [10.0%, 15.0%)
  6:  [15.0%, 25.0%)
  7:  [25.0%,    inf)

Nota sobre datos mensuales:
  ESMA prescribe retornos semanales con minimo 5 anos de historia.
  Con datos mensuales (lo que tenemos) se aplica la misma tabla pero
  usando volatilidad anualizada mensual (std_mensual * sqrt(12)).
  Esta es la aproximacion estandar del sector cuando no hay datos semanales.
  El resultado se guarda como metrica 'srri_nav' en fund_metrics para
  comparacion con el SRRI declarado en el KIID (campo SRRI de fund_master).
"""

import numpy as np
import pandas as pd

# ============================================================
# Tabla de buckets ESMA
# limites inferiores (inclusive) de volatilidad anualizada
# ============================================================

_SRRI_THRESHOLDS = [
    (0.0000, 1),   # 0.0%
    (0.0050, 2),   # 0.5%
    (0.0200, 3),   # 2.0%
    (0.0500, 4),   # 5.0%
    (0.1000, 5),   # 10.0%
    (0.1500, 6),   # 15.0%
    (0.2500, 7),   # 25.0%
]


def volatility_to_srri(volatility_ann: float) -> int:
    """
    Convierte volatilidad anualizada (ratio, no porcentaje) a bucket SRRI (1-7).

    Parametros:
        volatility_ann: volatilidad anualizada como ratio (ej. 0.08 para 8%)

    Devuelve:
        int entre 1 y 7

    Ejemplos:
        volatility_to_srri(0.003)  -> 1   (< 0.5%)
        volatility_to_srri(0.08)   -> 4   (5% - 10%)
        volatility_to_srri(0.30)   -> 7   (> 25%)
    """
    if np.isnan(volatility_ann) or volatility_ann < 0:
        return 0  # 0 = no calculable

    bucket = 1
    for threshold, srri in _SRRI_THRESHOLDS:
        if volatility_ann >= threshold:
            bucket = srri
        else:
            break
    return bucket


def compute_srri(nav_series: pd.Series, periods_per_year: int = 12) -> dict:
    """
    Calcula el SRRI desde una serie NAV mensual.

    Parametros:
        nav_series:       pd.Series con valores NAV (indice numerico secuencial)
        periods_per_year: 12 para datos mensuales (default), 52 para semanales

    Devuelve dict con:
        srri          (int 0-7, 0=no calculable)
        volatility_ann (float, volatilidad anualizada usada para el calculo)
        n_periods      (int, numero de retornos usados)
        method         (str, descripcion del metodo aplicado)
    """
    if len(nav_series) < 14:  # minimo 13 NAV -> 12 retornos mensuales = 1 ano
        return {
            "srri": 0,
            "volatility_ann": np.nan,
            "n_periods": len(nav_series),
            "method": "INSUF_DATA",
        }

    # Retornos simples periodicos
    returns = nav_series.pct_change().dropna()

    if len(returns) < 12:  # minimo 12 retornos mensuales
        return {
            "srri": 0,
            "volatility_ann": np.nan,
            "n_periods": len(returns),
            "method": "INSUF_RETURNS",
        }

    # Volatilidad anualizada (desviacion tipica muestral * sqrt(periodos/ano))
    vol_ann = float(returns.std(ddof=1) * np.sqrt(periods_per_year))

    srri = volatility_to_srri(vol_ann)

    return {
        "srri": srri,
        "volatility_ann": vol_ann,
        "n_periods": len(returns),
        "method": f"MONTHLY_ANN_SQRT{periods_per_year}",
    }


def srri_metrics(nav_series: pd.Series) -> list[tuple]:
    """
    Interfaz compatible con el pipeline de run_pipeline.
    Devuelve lista de (metric, value, real_flag) para integracion directa.

    Metricas generadas:
        srri_nav          bucket SRRI calculado desde NAV (0=no calculable)
        srri_volatility   volatilidad anualizada usada para el calculo
    """
    result = compute_srri(nav_series)

    return [
        ("srri_nav",        float(result["srri"]),         0),
        ("srri_volatility", result["volatility_ann"],       0),
    ]
