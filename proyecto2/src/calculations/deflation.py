# -*- coding: utf-8 -*-
"""
Deflacion de series NAV por IPC.
"""
import pandas as pd


def deflate_nav(nav_df: pd.DataFrame, ipc_df: pd.DataFrame | None) -> pd.DataFrame:
    """
    Deflacta una serie NAV por el IPC.

    Root-cause fix (2026-09-17): la version anterior usaba un INNER JOIN
    exacto por fecha, que descartaba SILENCIOSAMENTE cualquier fecha NAV sin
    una fecha IPC EXACTAMENTE coincidente. Sustituida por reindex+ffill+bfill
    en un primer intento -- pero ese intento tenia su propio defecto,
    root-caused el 2026-09-18: SCALAR_EQUALS_TIMESERIES seguia divergiendo
    ~50% real_flag=1 especificamente en rolling_3y (no en 1y/2y/5y/10y).
    Causa: run_pipeline.py pre-recorta ipc_df a la ventana ANTES de llamar
    aqui (ipc_w = ipc_df[ipc_df['date'] > cutoff]); reindex(nav_df['date'])
    solo puede rellenar hacia adelante (ffill) usando valores que
    SOBREVIVIERON al reindex -- si la primera fecha NAV de la ventana no
    coincide exactamente con una fecha IPC (frecuente: NAV es dia habil,
    IPC esta normalizado a fin de mes por load_ipc()), no hay ancla anterior
    DENTRO del conjunto recortado, y bfill() rellena con el valor SIGUIENTE
    (mas tardio) en vez del ultimo valor real ANTES de esa fecha -- una base
    de deflacion incorrecta justo en el borde de la ventana.

    Fix definitivo: pd.merge_asof(direction='backward'), que busca el
    ULTIMO valor IPC conocido en o antes de cada fecha NAV -- funciona
    correctamente sea cual sea el rango de ipc_df, sin necesitar que el
    llamador pase la serie completa (aunque run_pipeline.py TAMBIEN dejo de
    pre-recortar ipc_df como parte de este mismo fix, por higiene: pasar la
    serie completa es mas robusto que depender de que cada llamador calcule
    su propio margen de seguridad).

    Rebasa al primer valor IPC alineado (deflator=1.0 en la primera fecha
    NAV). El rebase no afecta ningun metrico downstream: return_ann, vol_ann,
    sharpe, sortino y max_dd son todos ratios/retornos sobre nav_real,
    invariantes a escalar toda la serie por una constante.

    Devuelve DataFrame vacio (columnas date/nav/nav_real) si ipc_df es None,
    vacio, o no hay ningun solapamiento aprovechable.
    """
    empty = pd.DataFrame(columns=["date", "nav", "nav_real"])

    if ipc_df is None or ipc_df.empty:
        return empty

    nav_df = nav_df.sort_values("date").reset_index(drop=True)
    ipc_df = ipc_df[["date", "ipc_index"]].sort_values("date").reset_index(drop=True)

    merged = pd.merge_asof(nav_df, ipc_df, on="date", direction="backward")

    if merged["ipc_index"].isna().any():
        # A NAV date precedes ipc_df's earliest available date entirely --
        # merge_asof(backward) has no earlier anchor for it. Backfill from
        # the earliest known IPC value instead of leaving it unusable
        # (never drop a row).
        merged["ipc_index"] = merged["ipc_index"].bfill()

    if merged["ipc_index"].isna().all():
        return empty

    ipc_base = merged["ipc_index"].iloc[0]
    if ipc_base == 0 or pd.isna(ipc_base):
        return empty

    merged["nav_real"] = merged["nav"].to_numpy() / (merged["ipc_index"].to_numpy() / ipc_base)
    return merged[["date", "nav", "nav_real"]]
