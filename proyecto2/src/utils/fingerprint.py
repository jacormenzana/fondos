# proyecto2/src/utils/fingerprint.py
# -*- coding: utf-8 -*-
"""
Fingerprinting de inputs para idempotencia del pipeline P2 (v27).

compute_input_hash() produce un hash SHA-1 que captura:
  - Última fecha y número de filas del NAV mensual
  - Último valor NAV (detecta correcciones de datos silenciosas)
  - Cobertura y última fecha del IPC
  - metric_version + calc_version (fuerza cache-miss al cambiar lógica de cálculo)

Diseño R-7: sin imports de pipeline.py ni core.io.
"""

from __future__ import annotations

import hashlib

import pandas as pd


def compute_input_hash(
    nav_df: pd.DataFrame,
    ipc_df: pd.DataFrame | None,
    metric_version: str,
    calc_version: str,
) -> str:
    """
    Calcula un fingerprint SHA-1 de los inputs de cálculo de un fondo.

    Parameters
    ----------
    nav_df         : DataFrame con columnas 'date' y 'nav'.
                     Mismo contrato que load_nav() en db_readers.py.
    ipc_df         : DataFrame con columnas 'date' e 'ipc_index', o None.
                     Mismo contrato que load_ipc() en db_readers.py.
    metric_version : p.ej. 'v1' — campo metric_version en fund_metrics.
    calc_version   : constante CALC_VERSION de run_pipeline.py.
                     Incrementar cuando cambie la lógica de cálculo para
                     forzar cache-miss aunque el NAV/IPC no haya cambiado.

    Returns
    -------
    Hexdigest SHA-1 de 40 caracteres (determinista, no criptográfico).

    Notes
    -----
    El hash captura: max_date, row_count, last_nav del NAV (detecta nuevas
    filas y correcciones de valores), max_date + count del IPC, y las
    versiones de código. No cubre cambios internos al bulk del NAV que no
    afecten al último valor; si se necesita más sensibilidad, pasar
    calc_version nuevo para forzar recalculo completo.
    """
    # --- NAV fingerprint --------------------------------------------------
    if nav_df is None or nav_df.empty:
        nav_fp = "EMPTY"
    else:
        max_date  = str(nav_df["date"].max())
        row_count = len(nav_df)
        last_nav  = float(nav_df["nav"].iloc[-1])
        nav_fp = f"{max_date}|{row_count}|{last_nav:.8f}"

    # --- IPC fingerprint --------------------------------------------------
    if ipc_df is None or ipc_df.empty:
        ipc_fp = "NOIPC"
    else:
        ipc_max = str(ipc_df["date"].max())
        ipc_n   = len(ipc_df)
        ipc_fp  = f"{ipc_max}|{ipc_n}"

    # --- Compose and hash -------------------------------------------------
    raw = f"{nav_fp}||{ipc_fp}||{metric_version}||{calc_version}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()
