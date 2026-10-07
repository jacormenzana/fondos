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

Si el fondo esta completamente hedgeado (Hedging_Policy = 'Hedged') el factor
divisa es ~0 y retorno_activo ~ retorno_total. Los fondos 'Partially Hedged'
siguen procesandose porque tienen exposicion FX residual.

Metricas generadas (horizon=since_inception, real_flag=0):
    fx_contribution_ann   contribucion anualizada de la divisa al retorno total
    fx_contribution_pct   porcentaje del retorno total explicado por la divisa
    fx_volatility_ann     volatilidad anualizada del componente divisa

Divisa efectiva: si Asset_Currency esta disponible y no es EUR/MCY tiene
preferencia sobre Fund_Currency para identificar la exposicion FX subyacente
(cubre el caso de clases EUR que invierten en activos USD sin cobertura).
Divisas soportadas: USD, JPY, GBP, CNY. Otras → lista vacia.

FND-0235 (FX_CONTRIBUTION_EUR_VIEW_ENABLED, apagado): la metodologia de arriba es la ALMACENADA. Tiene el signo opuesto al de la
contribucion que ve un inversor EUR (el log-cambio de unidades-de-divisa-por-EUR sube cuando el euro se aprecia) y, para las clases no EUR
(NAV ya en divisa extranjera), divide por un retorno en otra divisa. Con el interruptor se usa _compute_eur_view: contribucion firmada en pp
(-log-cambio de la divisa del ACTIVO), total convertido a EUR, razon solo si esta definida. Ver doc/reglas/NORMAS_IMPLEMENTACION.md §5b.
"""

import numpy as np
import pandas as pd

from shared import config
from shared.config import FX_CONTRIBUTION_PCT_CLAMP


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
    conn: "psycopg.Connection",
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
    df_usdeur = df_usdeur[~df_usdeur.index.duplicated(keep="last")]

    if currency == "USD":
        # USD/EUR = EUR/USD directamente
        return df_usdeur.rename("fx")

    # Para otras divisas: cargar divisa/USD y cruzar
    indicator = _FX_INDICATORS[currency]
    # Postgres migration Stage 9 (found live 2026-09-22): see momentum.py's identical note.
    ph = "%s"
    rows_fx = conn.execute(f"""
        SELECT date, value FROM series_macro
        WHERE indicator = {ph} AND geography = 'GLOBAL'
        ORDER BY date
    """, (indicator,)).fetchall()

    if not rows_fx:
        return None

    df_fx = pd.DataFrame(rows_fx, columns=["date", "fx_usd"])
    df_fx["date"] = pd.to_datetime(df_fx["date"]) + pd.offsets.MonthEnd(0)
    df_fx = df_fx.set_index("date")["fx_usd"].astype(float)
    df_fx = df_fx[~df_fx.index.duplicated(keep="last")]

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
# Vista del inversor EUR (FND-0235, FX_CONTRIBUTION_EUR_VIEW_ENABLED)
# ============================================================

def eur_view_currencies(fund_currency: str | None, asset_currency: str | None) -> tuple:
    """(asset_ccy, class_ccy): the currency the EUR investor is EXPOSED to and the currency the NAV is quoted in.

    asset_ccy None = no FX exposure (Asset_Currency = EUR means none whatever the class currency: a USD class of EUR assets nets to zero
    for a EUR investor). Asset_Currency empty / MCY falls back to the class currency. class_ccy None = the NAV is already in EUR."""
    ac = (asset_currency or "").upper().strip()
    fc = (fund_currency or "").upper().strip()
    class_ccy = fc if fc and fc != "EUR" else None
    if ac == "EUR":
        return None, class_ccy
    if ac and ac != "MCY":
        return ac, class_ccy
    return class_ccy, class_ccy


def eur_view_decomposition(r_total, r_fx_asset, r_fx_class=None) -> dict:
    """Annual FX contribution seen by a EUR investor, from aligned monthly LOG returns.

    r_fx_* are the log changes of foreign units per 1 EUR (load_fx_eur_divisa): positive = the EUR appreciated = a LOSS for a holder of the
    foreign asset, so the contribution of holding currency X is -r_fx_asset. r_total is the NAV's own log return; a non-EUR class (r_fx_class
    given) is converted to EUR with -r_fx_class. Returns fx_contribution_ann (signed), total_ann (EUR), fx_volatility_ann."""
    r_total = np.asarray(r_total, dtype=float)
    fx_c = -np.asarray(r_fx_asset, dtype=float)
    r_eur = r_total - (np.asarray(r_fx_class, dtype=float) if r_fx_class is not None else 0.0)
    return {
        "fx_contribution_ann": float((1 + np.mean(fx_c)) ** 12 - 1),
        "total_ann": float((1 + np.mean(r_eur)) ** 12 - 1),
        "fx_volatility_ann": float(np.std(fx_c, ddof=1) * np.sqrt(12)),
    }


def _compute_eur_view(conn, nav_df, fund_currency, asset_currency) -> list:
    asset_ccy, class_ccy = eur_view_currencies(fund_currency, asset_currency)
    if asset_ccy is None or asset_ccy not in _FX_INDICATORS or (class_ccy is not None and class_ccy not in _FX_INDICATORS):
        return []
    fx_asset = load_fx_eur_divisa(conn, asset_ccy)
    fx_class = load_fx_eur_divisa(conn, class_ccy) if class_ccy is not None else None
    if fx_asset is None or fx_asset.empty or (class_ccy is not None and (fx_class is None or fx_class.empty)):
        return []

    nav = nav_df.set_index("date")["nav"].sort_index()
    nav.index = nav.index + pd.offsets.MonthEnd(0)
    nav = nav[~nav.index.duplicated(keep="last")]
    parts = [np.log(nav / nav.shift(1)).dropna().rename("r_total"),
             np.log(fx_asset / fx_asset.shift(1)).dropna().rename("r_fx_asset")]
    if fx_class is not None:
        parts.append(np.log(fx_class / fx_class.shift(1)).dropna().rename("r_fx_class"))
    merged = pd.concat(parts, axis=1, join="inner").dropna()
    if len(merged) < MIN_OBS:
        return []

    d = eur_view_decomposition(merged["r_total"].values, merged["r_fx_asset"].values,
                               merged["r_fx_class"].values if fx_class is not None else None)
    out = [("fx_contribution_ann", d["fx_contribution_ann"], 0)]
    if abs(d["total_ann"]) >= config.FX_RATIO_MIN_TOTAL_ANN:     # an undefined ratio is NOT written (the legacy path wrote a fake 0.0)
        pct = d["fx_contribution_ann"] / d["total_ann"]
        out.append(("fx_contribution_pct", max(-FX_CONTRIBUTION_PCT_CLAMP, min(FX_CONTRIBUTION_PCT_CLAMP, pct)), 0))
    out.append(("fx_volatility_ann", d["fx_volatility_ann"], 0))
    return out


# ============================================================
# Calculo del factor divisa para un fondo
# ============================================================

def compute_currency_factor(
    isin: str,
    fund_currency: str,
    hedging_policy: str | None,
    nav_df: pd.DataFrame,
    conn: "psycopg.Connection",
    asset_currency: str | None = None,
) -> list[tuple]:
    """
    Calcula la contribucion de la divisa al retorno del fondo.

    Parametros:
        isin:            ISIN del fondo
        fund_currency:   divisa de la clase de participacion (Fund_Currency)
        hedging_policy:  politica de cobertura (Hedging_Policy)
        nav_df:          DataFrame con columnas ['date', 'nav']
        conn:            conexion sqlite3
        asset_currency:  divisa del activo subyacente (Asset_Currency, opcional).
                         Si se provee y no es EUR/MCY, tiene preferencia sobre
                         fund_currency para identificar la exposicion FX.

    Devuelve lista de (metric, value, real_flag).
    Devuelve lista vacia si no hay exposicion FX (EUR puro) o si el fondo
    esta completamente hedgeado (Hedging_Policy = 'Hedged').
    """
    if nav_df.empty:
        return []

    # Fondos completamente hedgeados: factor divisa es despreciable
    # 'Partially Hedged' sigue procesandose (exposicion FX residual)
    if hedging_policy and hedging_policy.strip() == "Hedged":
        return []

    if config.FX_CONTRIBUTION_EUR_VIEW_ENABLED:     # FND-0235: EUR investor's signed contribution (off = the stored metric below)
        return _compute_eur_view(conn, nav_df, fund_currency, asset_currency)

    # Determinar divisa efectiva del riesgo FX.
    # Asset_Currency (divisa del subyacente) tiene preferencia cuando esta
    # disponible: cubre clases EUR que invierten en activos USD sin cobertura.
    _ac = (asset_currency or "").upper().strip()
    _fc = (fund_currency  or "").upper().strip()
    if _ac and _ac not in ("EUR", "MCY"):
        currency = _ac
    elif _fc and _fc != "EUR":
        currency = _fc
    else:
        return []  # Sin exposicion FX (EUR puro o MCY sin activo identificado)

    if currency not in _FX_INDICATORS:
        return []

    # Cargar tipo de cambio EUR/divisa
    fx_series = load_fx_eur_divisa(conn, currency)
    if fx_series is None or fx_series.empty:
        return []

    # Retornos logaritmicos del fondo
    nav = nav_df.set_index("date")["nav"].sort_index()
    nav.index = nav.index + pd.offsets.MonthEnd(0)
    nav = nav[~nav.index.duplicated(keep="last")]
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
        fx_pct = max(-FX_CONTRIBUTION_PCT_CLAMP, min(FX_CONTRIBUTION_PCT_CLAMP, fx_pct))  # clamp outliers
    else:
        fx_pct = 0.0

    return [
        ("fx_contribution_ann", fx_contribution_ann, 0),
        ("fx_contribution_pct", fx_pct,              0),
        ("fx_volatility_ann",   fx_vol_ann,           0),
    ]
