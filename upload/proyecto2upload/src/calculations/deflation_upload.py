# -*- coding: utf-8 -*-
"""
Created on Fri Jan  2 21:22:24 2026

@author: Administrador
"""
def deflate_nav(nav_df, ipc_df):
    df = nav_df.merge(ipc_df, on='date', how='inner')
    df['nav_real'] = df['nav'] / df['ipc_index']
    return df[['date', 'nav', 'nav_real']]
