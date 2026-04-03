# proyecto2/src/calculations/currency_factor.py
# -*- coding: utf-8 -*-
"""
Descomposicion del retorno del fondo en componente divisa vs activo subyacente.

Para fondos no hedgeados denominados en una divisa distinta al EUR, parte de
la rentabilidad observada por el inversor europeo proviene del movimiento del
tipo de cambio, no de la gestion del activo subyacente.

Metodologia:
    retorno_total(t)  = log(NAV(t) / NAV(t-1))           -- en EUR
    retorno_fx(t)     = log(TC_EUR_divisa(t) / TC_EUR_divisa(t-1))
    retorno_activo(t) = retorno_total(t) - retorno_fx(t)

Donde TC_EUR_divisa es el tipo de cambio EUR/divisa (unidades de divisa por 1 EUR).

Si el fondo esta hedgeado (Hedging_Policy = 'Full') el factor divisa es ~0
y retorno_activo ~ retorno_total.

Metricas generadas (horizon=since_inception, real_flag=0):
    fx_contribution_ann   contribucion anualizada de la divisa al retorno total
    fx_contribution_pct   porcentaje del retorno total explicado por la divisa
    fx_volatility_ann     volatilidad anualizada del componente divisa

Solo se calcula para fondos cuya Fund_Currency no sea EUR.
Divisas soportadas: USD, JPY, GBP, CNY.
Para otras divisas se devuelve lista vacia.
"""

import numpy as np
import pandas as pd
import sqlite3

MIN_OBS = 24  # minimo de observaciones para calcular la metrica

# Indicadores FX en series_macro para construir EUR/divisa
# Todos expresados como "unidades de divisa por 1 USD"
# EUR/divisa = (USD/EUR)^-1 * (divisa/USD)
_FX_INDICATORS = {
    "USD": "fx_usd_eur",   # USD por EUR directamente
    "JPY": "fx_jpy_usd",   # JPY por USD -- necesita cruzar con USD/EUR
    "GBP": "fx_usd_gbp",   # USD por GBP -- necesita cruzar con USD/EUR
    "CNY": "fx_cny_usd",   # CNY por USD -- necesita cruzar con USD/EUR
}


# ============================================================
# Carga de tipo de cambio EUR/divisa
# ============================================================

def load_fx_eur_divisa(
    conn: sqlite3.Connection,
    currency: str,
) -> pd.Series | None:
    """
    Devuelve serie mensual del tipo de cambio EUR/divisa
    (unidades de divisa extranjera por 1 EUR).
    Fechas normalizadas a fin de mes.

    Ejemplos:
        EUR/USD: ~1.10  (1 EUR = 1.10 USD)
        EUR/JPY: ~160   (1 EUR = 160 JPY)
        EUR/GBP: ~0.85  (1 EUR = 0.85 GBP)
    """
    currency = currency.upper().strip()

    if currency not in _FX_INDICATORS:
        return None

    # Cargar USD/EUR siempre (necesario para cruces)
    rows_usdeur = conn.execute("""
        SELECT date, value FROM series_macro
        WHERE indicator = 'fx_usd_eur' AND geography = 'GLOBAL'
        ORDER BY date
    """).fetchall()

    if not rows_usdeur:
        return None

    df_usdeur = pd.DataFrame(rows_usdeur, columns=["date", "usd_eur"])
    df_usdeur["date"] = pd.to_datetime(df_usdeur["date"]) + pd.offsets.MonthEnd(0)
    df_usdeur = df_usdeur.set_index("date")["usd_eur"].astype(float)

    if currency == "USD":
        # USD/EUR = EUR/USD directamente
        return df_usdeur.rename("fx")

    # Para otras divisas: cargar divisa/USD y cruzar
    indicator = _FX_INDICATORS[currency]
    rows_fx = conn.execute("""
        SELECT date, value FROM series_macro
        WHERE indicator = ? AND geography = 'GLOBAL'
        ORDER BY date
    """, (indicator,)).fetchall()

    if not rows_fx:
        return None

    df_fx = pd.DataFrame(rows_fx, columns=["date", "fx_usd"])
    df_fx["date"] = pd.to_datetime(df_fx["date"]) + pd.offsets.MonthEnd(0)
    df_fx = df_fx.set_index("date")["fx_usd"].astype(float)

    # Alinear series
    merged = pd.concat([df_usdeur, df_fx], axis=1, join="inner").dropna()
    merged.columns = ["usd_eur", "fx_usd"]

    if currency == "GBP":
        # fx_usd_gbp = USD por GBP
        # EUR/GBP = (USD/GBP) / (USD/EUR) = fx_usd / usd_eur
        eur_fx = merged["fx_usd"] / merged["usd_eur"]
    else:
        # JPY, CNY: fx = divisa por USD
        # EUR/divisa = (divisa/USD) * (USD/EUR)
        eur_fx = merged["fx_usd"] * merged["usd_eur"]

    return eur_fx.rename("fx")


# ============================================================
# Calculo del factor divisa para un fondo
# ============================================================

def compute_currency_factor(
    isin: str,
    fund_currency: str,
    hedging_policy: str | None,
    nav_df: pd.DataFrame,
    conn: sqlite3.Connection,
) -> list[tuple]:
    """
    Calcula la contribucion de la divisa al retorno del fondo.

    Parametros:
        isin:            ISIN del fondo
        fund_currency:   divisa del fondo (Fund_Currency en fund_master)
        hedging_policy:  politica de cobertura (Hedging_Policy en fund_master)
        nav_df:          DataFrame con columnas ['date', 'nav']
        conn:            conexion sqlite3

    Devuelve lista de (metric, value, real_flag).
    Devuelve lista vacia si el fondo es EUR o esta hedgeado.
    """
    if nav_df.empty:
        return []

    # Fondos EUR: no hay factor divisa
    if not fund_currency or fund_currency.upper() == "EUR":
        return []

    # Fondos hedgeados: factor divisa es despreciable
    if hedging_policy and "full" in str(hedging_policy).lower():
        return []

    currency = fund_currency.upper().strip()
    if currency not in _FX_INDICATORS:
        return []

    # Cargar tipo de cambio EUR/divisa
    fx_series = load_fx_eur_divisa(conn, currency)
    if fx_series is None or fx_series.empty:
        return []

    # Retornos logaritmicos del fondo
    nav = nav_df.set_index("date")["nav"].sort_index()
    nav.index = nav.index + pd.offsets.MonthEnd(0)
    r_total = np.log(nav / nav.shift(1)).dropna()

    # Retornos logaritmicos del tipo de cambio EUR/divisa
    r_fx = np.log(fx_series / fx_series.shift(1)).dropna()

    # Alinear
    merged = pd.concat([r_total.rename("r_total"), r_fx.rename("r_fx")],
                       axis=1, join="inner").dropna()

    if len(merged) < MIN_OBS:
        return []

    # Componente divisa y componente activo
    r_fx_arr     = merged["r_fx"].values
    r_total_arr  = merged["r_total"].values
    r_activo_arr = r_total_arr - r_fx_arr

    # Anualizacion
    fx_contribution_ann = float((1 + np.mean(r_fx_arr)) ** 12 - 1)
    fx_vol_ann          = float(np.std(r_fx_arr, ddof=1) * np.sqrt(12))

    # % del retorno total explicado por divisa
    total_ann = float((1 + np.mean(r_total_arr)) ** 12 - 1)
    if abs(total_ann) > 1e-6:
        fx_pct = fx_contribution_ann / total_ann
        fx_pct = max(-5.0, min(5.0, fx_pct))  # clamp outliers
    else:
        fx_pct = 0.0

    return [
        ("fx_contribution_ann", fx_contribution_ann, 0),
        ("fx_contribution_pct", fx_pct,              0),
        ("fx_volatility_ann",   fx_vol_ann,           0),
    ]
