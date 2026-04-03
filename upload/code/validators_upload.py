# -*- coding: utf-8 -*-
"""
Created on Fri Jan  2 20:39:10 2026

@author: Administrador
"""

def validate_nav(nav_df):
    if nav_df.isnull().any().any():
        return False, "NAV contiene nulos"
    if not nav_df["date"].is_monotonic_increasing:
        return False, "Fechas no ordenadas"
    if (nav_df["nav"] <= 0).any():
        return False, "NAV no positivo"
    return True, None


def validate_ipc(ipc_df):
    if ipc_df["ipc_index"].isnull().any():
        return False, "IPC con nulos"
    if (ipc_df["ipc_index"] <= 0).any():
        return False, "IPC no positivo"
    return True, None
