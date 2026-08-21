# -*- coding: utf-8 -*-
"""
Created on Fri Jan  2 21:28:20 2026

@author: Administrador
"""
from datetime import date

def write_metrics(engine, isin, metrics_df, horizon='since_inception'):
    # LEGACY — this function is not called by run_pipeline.py (which uses its own
    # _write_metrics / _replace_beta_set / _write_timeseries helpers).
    # It is preserved for historical reference only and is NOT audit-column-aware
    # (v26: algorithm_version, batch_id). Do not use in new code.
    metrics_df = metrics_df.copy()
    metrics_df['isin'] = isin
    metrics_df['horizon'] = horizon
    metrics_df['calculation_date'] = date.today()
    metrics_df['metric_version'] = 'v1.0'

    metrics_df[['isin', 'metric', 'horizon',
                'value', 'real_flag',
                'calculation_date', 'metric_version']] \
        .to_sql('fund_metrics', engine, if_exists='append', index=False)
