# -*- coding: utf-8 -*-
"""
Deflacion de series NAV por IPC.
"""
import pandas as pd


def deflate_nav(nav_df: pd.DataFrame, ipc_df: pd.DataFrame | None) -> pd.DataFrame:
    """
    Deflacta una serie NAV por el IPC.

    Root-cause fix (2026-09-17): la version anterior usaba un INNER JOIN
    exacto por fecha (nav_df.merge(ipc_df, on='date', how='inner')), que
    descartaba SILENCIOSAMENTE cualquier fecha NAV sin una fecha IPC
    EXACTAMENTE coincidente. En produccion la fecha NAV mas reciente suele
    ser una foto a mitad de mes (ej. '2026-09-14'), mientras que
    series_inflation esta normalizado a fin de mes por load_ipc() (ej.
    '2026-09-30') -- esa fecha nunca cruzaba, asi que el path escalar
    (_process_horizon -> compute_risk_metrics -> aqui) perdia
    sistematicamente su punto NAV mas reciente en TODO calculo real_flag=1,
    en TODOS los horizontes. Root-caused via el motor de auditoria
    estadistica: SCALAR_EQUALS_TIMESERIES mostraba real_flag=0 coincidiendo
    casi exacto entre fund_metrics y fund_metric_timeseries, pero
    real_flag=1 divergiendo ~50% -- el path rolling
    (rolling_stats.compute_rolling_rows) ya alineaba con
    reindex+ffill+bfill (nunca descarta una fecha), igual que
    short_horizon.py::_deflate_nav(). Ahora las tres implementaciones
    comparten el mismo criterio de alineacion (aunque siguen siendo tres
    funciones separadas -- consolidarlas en una sola es trabajo de
    seguimiento, no parte de este fix).

    Alinea IPC a las fechas NAV por indice (ffill+bfill, nunca descarta una
    fecha), rebasa al primer valor de la serie alineada (deflator=1.0 en esa
    fecha). El rebase no afecta ningun metrico downstream: return_ann,
    vol_ann, sharpe, sortino y max_dd son todos ratios/retornos sobre
    nav_real, invariantes a escalar toda la serie por una constante.

    Devuelve DataFrame vacio (columnas date/nav/nav_real) si ipc_df es None,
    vacio, o no hay ningun solapamiento aprovechable.
    """
    empty = pd.DataFrame(columns=["date", "nav", "nav_real"])

    if ipc_df is None or ipc_df.empty:
        return empty

    nav_df = nav_df.sort_values("date").reset_index(drop=True)
    ipc = ipc_df.set_index("date")["ipc_index"]
    ipc_aligned = ipc.reindex(nav_df["date"]).ffill().bfill()

    if ipc_aligned.isna().all():
        return empty

    ipc_base = ipc_aligned.iloc[0]
    if ipc_base == 0 or pd.isna(ipc_base):
        return empty

    deflator = (ipc_aligned / ipc_base).to_numpy()
    df = nav_df.copy()
    df["nav_real"] = df["nav"].to_numpy() / deflator
    return df[["date", "nav", "nav_real"]]
