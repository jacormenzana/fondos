-- ============================================================
-- PROYECTO 2 - QUERIES DE EXPLORACION DEL UNIVERSO
-- Ejecutar en DBeaver sobre fondos.sqlite
-- Una vez completado el pipeline P2
-- ============================================================


-- ============================================================
-- 1. RESUMEN GENERAL DEL PIPELINE
-- ============================================================

-- 1a. Estado global
SELECT 
    COUNT(DISTINCT isin)    AS fondos_con_metricas,
    COUNT(*)                AS metricas_totales,
    MIN(calculation_date)   AS primera_calculo,
    MAX(calculation_date)   AS ultimo_calculo
FROM fund_metrics;

-- 1b. Fondos sin metricas (pipeline no los proceso)
SELECT COUNT(*) AS fondos_sin_metricas
FROM fund_master fm
WHERE NOT EXISTS (
    SELECT 1 FROM fund_metrics fmet WHERE fmet.isin = fm.ISIN
);

-- 1c. Metricas por horizonte
SELECT horizon, COUNT(DISTINCT isin) AS fondos, COUNT(*) AS metricas
FROM fund_metrics
GROUP BY horizon
ORDER BY fondos DESC;


-- ============================================================
-- 2. DISTRIBUCION SRRI
-- ============================================================

-- 2a. SRRI calculado vs declarado en KIID
SELECT 
    fm.SRRI                         AS srri_kiid,
    CAST(fmet.value AS INTEGER)     AS srri_calculado,
    COUNT(*)                        AS fondos
FROM fund_metrics fmet
JOIN fund_master fm ON fm.ISIN = fmet.isin
WHERE fmet.metric  = 'srri_nav'
  AND fmet.horizon = 'since_inception'
  AND fmet.real_flag = 0
  AND fm.SRRI IS NOT NULL
GROUP BY fm.SRRI, CAST(fmet.value AS INTEGER)
ORDER BY fm.SRRI, srri_calculado;

-- 2b. Discrepancias SRRI (calculado difiere del KIID)
SELECT 
    fmet.isin,
    fm.Fund_Name,
    fm.SRRI                         AS srri_kiid,
    CAST(fmet.value AS INTEGER)     AS srri_calculado,
    ABS(fm.SRRI - fmet.value)       AS diferencia
FROM fund_metrics fmet
JOIN fund_master fm ON fm.ISIN = fmet.isin
WHERE fmet.metric  = 'srri_nav'
  AND fmet.horizon = 'since_inception'
  AND fmet.real_flag = 0
  AND fm.SRRI IS NOT NULL
  AND ABS(fm.SRRI - fmet.value) >= 2
ORDER BY diferencia DESC
LIMIT 50;


-- ============================================================
-- 3. RENTABILIDAD
-- ============================================================

-- 3a. Distribucion rentabilidad anualizada nominal
SELECT 
    CASE 
        WHEN value < -0.05 THEN '1. < -5%'
        WHEN value < 0     THEN '2. -5% a 0%'
        WHEN value < 0.03  THEN '3. 0% a 3%'
        WHEN value < 0.05  THEN '4. 3% a 5%'
        WHEN value < 0.08  THEN '5. 5% a 8%'
        WHEN value < 0.11  THEN '6. 8% a 11%'
        WHEN value < 0.15  THEN '7. 11% a 15%'
        ELSE                    '8. > 15%'
    END AS tramo,
    COUNT(*) AS fondos
FROM fund_metrics
WHERE metric   = 'return_ann'
  AND horizon  = 'since_inception'
  AND real_flag = 0
  AND value IS NOT NULL
GROUP BY tramo
ORDER BY tramo;

-- 3b. Distribucion rentabilidad anualizada REAL
SELECT 
    CASE 
        WHEN value < -0.05 THEN '1. < -5%'
        WHEN value < 0     THEN '2. -5% a 0%'
        WHEN value < 0.03  THEN '3. 0% a 3%'
        WHEN value < 0.05  THEN '4. 3% a 5%'
        WHEN value < 0.08  THEN '5. 5% a 8%'
        WHEN value < 0.11  THEN '6. 8% a 11%'
        WHEN value < 0.15  THEN '7. 11% a 15%'
        ELSE                    '8. > 15%'
    END AS tramo,
    COUNT(*) AS fondos
FROM fund_metrics
WHERE metric   = 'return_ann'
  AND horizon  = 'since_inception'
  AND real_flag = 1
  AND value IS NOT NULL
GROUP BY tramo
ORDER BY tramo;

-- 3c. Top 30 fondos por rentabilidad real anualizada
SELECT 
    fmet.isin,
    fm.Fund_Name,
    fm.Fund_Nature,
    fm.Management_Company,
    ROUND(fmet.value * 100, 2)      AS return_real_pct,
    ROUND(vol.value * 100, 2)       AS volatilidad_pct,
    ROUND(sh.value, 2)              AS sharpe,
    CAST(srri.value AS INTEGER)     AS srri_calculado,
    fmet.source_rows                AS meses_datos
FROM fund_metrics fmet
JOIN fund_master fm   ON fm.ISIN = fmet.isin
LEFT JOIN fund_metrics vol  ON vol.isin  = fmet.isin AND vol.metric  = 'volatility_ann'
                           AND vol.horizon = 'since_inception' AND vol.real_flag = 0
LEFT JOIN fund_metrics sh   ON sh.isin   = fmet.isin AND sh.metric   = 'sharpe'
                           AND sh.horizon = 'since_inception' AND sh.real_flag = 0
LEFT JOIN fund_metrics srri ON srri.isin = fmet.isin AND srri.metric = 'srri_nav'
                           AND srri.horizon = 'since_inception' AND srri.real_flag = 0
WHERE fmet.metric   = 'return_ann'
  AND fmet.horizon  = 'since_inception'
  AND fmet.real_flag = 1
  AND fmet.value IS NOT NULL
ORDER BY fmet.value DESC
LIMIT 30;


-- ============================================================
-- 4. RIESGO Y DRAWDOWN
-- ============================================================

-- 4a. Distribucion max drawdown nominal
SELECT 
    CASE 
        WHEN value > -0.10 THEN '1. > -10%'
        WHEN value > -0.20 THEN '2. -10% a -20%'
        WHEN value > -0.30 THEN '3. -20% a -30%'
        WHEN value > -0.40 THEN '4. -30% a -40%'
        WHEN value > -0.50 THEN '5. -40% a -50%'
        ELSE                    '6. < -50%'
    END AS tramo,
    COUNT(*) AS fondos
FROM fund_metrics
WHERE metric   = 'max_drawdown'
  AND horizon  = 'since_inception'
  AND real_flag = 0
  AND value IS NOT NULL
GROUP BY tramo
ORDER BY tramo;

-- 4b. Fondos con mejor ratio retorno/drawdown (calidad de retorno)
SELECT 
    ret.isin,
    fm.Fund_Name,
    fm.Fund_Nature,
    ROUND(ret.value * 100, 2)       AS return_real_pct,
    ROUND(dd.value * 100, 2)        AS max_drawdown_pct,
    ROUND(ret.value / ABS(dd.value), 2) AS ret_dd_ratio,
    CAST(srri.value AS INTEGER)     AS srri,
    ret.source_rows                 AS meses
FROM fund_metrics ret
JOIN fund_master fm   ON fm.ISIN = ret.isin
JOIN fund_metrics dd  ON dd.isin  = ret.isin AND dd.metric  = 'max_drawdown'
                     AND dd.horizon = 'since_inception' AND dd.real_flag = 0
LEFT JOIN fund_metrics srri ON srri.isin = ret.isin AND srri.metric = 'srri_nav'
                           AND srri.horizon = 'since_inception' AND srri.real_flag = 0
WHERE ret.metric   = 'return_ann'
  AND ret.horizon  = 'since_inception'
  AND ret.real_flag = 1
  AND ret.value  >= 0.05
  AND dd.value   IS NOT NULL
  AND dd.value   < 0
ORDER BY ret_dd_ratio DESC
LIMIT 30;


-- ============================================================
-- 5. CONSISTENCIA
-- ============================================================

-- 5a. Fondos mas consistentes (alto % meses positivos + baja perdida severa)
SELECT 
    pos.isin,
    fm.Fund_Name,
    fm.Fund_Nature,
    ROUND(pos.value * 100, 1)       AS pct_meses_positivos,
    ROUND(sev.value * 100, 1)       AS pct_meses_perdida_severa,
    ROUND(ret.value * 100, 2)       AS return_real_pct,
    CAST(srri.value AS INTEGER)     AS srri
FROM fund_metrics pos
JOIN fund_master fm   ON fm.ISIN = pos.isin
JOIN fund_metrics sev ON sev.isin = pos.isin AND sev.metric = 'pct_severe_loss_months'
                     AND sev.horizon = 'since_inception' AND sev.real_flag = 0
JOIN fund_metrics ret ON ret.isin = pos.isin AND ret.metric = 'return_ann'
                     AND ret.horizon = 'since_inception' AND ret.real_flag = 1
LEFT JOIN fund_metrics srri ON srri.isin = pos.isin AND srri.metric = 'srri_nav'
                           AND srri.horizon = 'since_inception' AND srri.real_flag = 0
WHERE pos.metric  = 'pct_positive_months'
  AND pos.horizon = 'since_inception'
  AND pos.real_flag = 0
  AND pos.value   >= 0.60
  AND sev.value   <= 0.10
  AND ret.value   >= 0.05
ORDER BY pos.value DESC, sev.value ASC
LIMIT 30;


-- ============================================================
-- 6. COMPORTAMIENTO EN CRISIS
-- ============================================================

-- 6a. Fondos con mejor comportamiento en crisis 2020 y 2022
SELECT 
    c20.isin,
    fm.Fund_Name,
    fm.Fund_Nature,
    ROUND(c20.value * 100, 2)       AS return_crisis2020,
    ROUND(c22.value * 100, 2)       AS return_crisis2022,
    ROUND(si.value * 100, 2)        AS return_real_anual,
    CAST(srri.value AS INTEGER)     AS srri
FROM fund_metrics c20
JOIN fund_master fm  ON fm.ISIN = c20.isin
JOIN fund_metrics c22 ON c22.isin = c20.isin AND c22.metric = 'return_ann'
                     AND c22.horizon = 'crisis_2022' AND c22.real_flag = 0
JOIN fund_metrics si  ON si.isin  = c20.isin AND si.metric  = 'return_ann'
                     AND si.horizon = 'since_inception' AND si.real_flag = 1
LEFT JOIN fund_metrics srri ON srri.isin = c20.isin AND srri.metric = 'srri_nav'
                           AND srri.horizon = 'since_inception' AND srri.real_flag = 0
WHERE c20.metric  = 'return_ann'
  AND c20.horizon = 'crisis_2020'
  AND c20.real_flag = 0
  AND c20.value IS NOT NULL
  AND c22.value IS NOT NULL
ORDER BY (c20.value + c22.value) DESC
LIMIT 30;


-- ============================================================
-- 7. CANDIDATOS P3 (PRE-FILTRO)
-- Fondos que superan los criterios minimos para entrar en scoring P3
-- ============================================================

-- 7a. Candidatos base: retorno real >= 5%, sharpe >= 0, drawdown > -40%, consistencia >= 55%
SELECT 
    ret.isin,
    fm.Fund_Name,
    fm.Fund_Nature,
    fm.Management_Company,
    ROUND(ret.value * 100, 2)       AS return_real_pct,
    ROUND(vol.value * 100, 2)       AS volatilidad_pct,
    ROUND(sh.value, 2)              AS sharpe,
    ROUND(dd.value * 100, 2)        AS max_drawdown_pct,
    ROUND(pos.value * 100, 1)       AS pct_meses_positivos,
    CAST(srri.value AS INTEGER)     AS srri,
    ret.source_rows                 AS meses_datos
FROM fund_metrics ret
JOIN fund_master fm   ON fm.ISIN = ret.isin
JOIN fund_metrics vol ON vol.isin  = ret.isin AND vol.metric  = 'volatility_ann'
                     AND vol.horizon = 'since_inception' AND vol.real_flag = 0
JOIN fund_metrics sh  ON sh.isin   = ret.isin AND sh.metric   = 'sharpe'
                     AND sh.horizon = 'since_inception' AND sh.real_flag = 0
JOIN fund_metrics dd  ON dd.isin   = ret.isin AND dd.metric   = 'max_drawdown'
                     AND dd.horizon = 'since_inception' AND dd.real_flag = 0
JOIN fund_metrics pos ON pos.isin  = ret.isin AND pos.metric  = 'pct_positive_months'
                     AND pos.horizon = 'since_inception' AND pos.real_flag = 0
LEFT JOIN fund_metrics srri ON srri.isin = ret.isin AND srri.metric = 'srri_nav'
                           AND srri.horizon = 'since_inception' AND srri.real_flag = 0
WHERE ret.metric   = 'return_ann'
  AND ret.horizon  = 'since_inception'
  AND ret.real_flag = 1
  AND ret.value   >= 0.05          -- retorno real >= 5%
  AND sh.value    >= 0             -- sharpe positivo
  AND dd.value    >= -0.40         -- drawdown maximo -40%
  AND pos.value   >= 0.55          -- al menos 55% meses positivos
  AND ret.source_rows >= 36        -- minimo 3 anos de datos
ORDER BY ret.value DESC;

-- 7b. Conteo de candidatos por naturaleza de fondo
SELECT 
    fm.Fund_Nature,
    COUNT(*) AS candidatos
FROM fund_metrics ret
JOIN fund_master fm   ON fm.ISIN = ret.isin
JOIN fund_metrics sh  ON sh.isin  = ret.isin AND sh.metric  = 'sharpe'
                     AND sh.horizon = 'since_inception' AND sh.real_flag = 0
JOIN fund_metrics dd  ON dd.isin  = ret.isin AND dd.metric  = 'max_drawdown'
                     AND dd.horizon = 'since_inception' AND dd.real_flag = 0
JOIN fund_metrics pos ON pos.isin  = ret.isin AND pos.metric = 'pct_positive_months'
                     AND pos.horizon = 'since_inception' AND pos.real_flag = 0
WHERE ret.metric   = 'return_ann'
  AND ret.horizon  = 'since_inception'
  AND ret.real_flag = 1
  AND ret.value   >= 0.05
  AND sh.value    >= 0
  AND dd.value    >= -0.40
  AND pos.value   >= 0.55
  AND ret.source_rows >= 36
GROUP BY fm.Fund_Nature
ORDER BY candidatos DESC;
