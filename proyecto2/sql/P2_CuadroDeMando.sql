-- 1. Resumen global
SELECT 
    COUNT(DISTINCT isin)    AS fondos,
    COUNT(*)                AS metricas_totales,
    COUNT(DISTINCT metric)  AS tipos_metrica,
    MAX(calculation_date)   AS ultimo_calculo
FROM fund_metrics;



-- 1. Resumen global
SELECT 
    COUNT(DISTINCT isin)    AS fondos,
    COUNT(*)                AS metricas_totales,
    COUNT(DISTINCT metric)  AS tipos_metrica,
    MAX(calculation_date)   AS ultimo_calculo
FROM fund_metrics;

-- 2. Metricas nuevas -- cobertura
SELECT 
    metric,
    COUNT(*)                        AS fondos_con_valor,
    ROUND(AVG(value), 4)            AS media,
    ROUND(MIN(value), 4)            AS minimo,
    ROUND(MAX(value), 4)            AS maximo
FROM fund_metrics
WHERE metric IN (
    'beta_rate_eu', 'beta_rate_us', 'beta_rate_jp', 'beta_rate_cn',
    'beta_m3_yoy',
    'beta_ipc_es', 'beta_ipc_eu', 'beta_ipc_us', 'beta_ipc_jp', 'beta_ipc_cn',
    'macro_r2', 'macro_alpha', 'macro_n_obs',
    'momentum_1y', 'momentum_3y', 'momentum_rank',
    'upside_capture', 'downside_capture', 'capture_ratio'
)
  AND horizon = 'since_inception'
  AND value IS NOT NULL
GROUP BY metric
ORDER BY metric;

-- 3. Distribucion macro_r2 (calidad del modelo macro por fondo)
SELECT
    CASE
        WHEN value < 0.05 THEN '1. < 5%'
        WHEN value < 0.10 THEN '2. 5-10%'
        WHEN value < 0.20 THEN '3. 10-20%'
        WHEN value < 0.30 THEN '4. 20-30%'
        WHEN value < 0.50 THEN '5. 30-50%'
        ELSE                   '6. > 50%'
    END AS tramo_r2,
    COUNT(*) AS fondos
FROM fund_metrics
WHERE metric = 'macro_r2'
  AND horizon = 'since_inception'
  AND value IS NOT NULL
GROUP BY tramo_r2
ORDER BY tramo_r2;

-- 4. Top 10 fondos con mayor R2 macro (los mas sensibles a factores macro)
SELECT 
    fmet.isin,
    fm.Fund_Name,
    fm.Fund_Nature,
    ROUND(fmet.value, 3)    AS macro_r2,
    CAST(srri.value AS INTEGER) AS srri
FROM fund_metrics fmet
JOIN fund_master fm ON fm.ISIN = fmet.isin
LEFT JOIN fund_metrics srri ON srri.isin = fmet.isin 
    AND srri.metric = 'srri_nav' 
    AND srri.horizon = 'since_inception' 
    AND srri.real_flag = 0
WHERE fmet.metric  = 'macro_r2'
  AND fmet.horizon = 'since_inception'
  AND fmet.value IS NOT NULL
ORDER BY fmet.value DESC
LIMIT 10;
