# -*- coding: utf-8 -*-
"""
Created on Fri Jan  2 21:28:20 2026

@author: Administrador
"""
from datetime import date

def write_metrics(engine, isin, metrics_df, horizon='since_inception'):
    metrics_df = metrics_df.copy()
    metrics_df['isin'] = isin
    metrics_df['horizon'] = horizon
    metrics_df['calculation_date'] = date.today()
    metrics_df['metric_version'] = 'v1.0'

    metrics_df[['isin', 'metric', 'horizon',
                'value', 'real_flag',
                'calculation_date', 'metric_version']] \
        .to_sql('fund_metrics', engine, if_exists='append', index=False)
