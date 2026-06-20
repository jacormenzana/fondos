.output control_report.txt

-- ============================================================
-- control_sql_sprint2.sql
-- Queries de control post-pipeline — Sprint 2 BL-COST-4c
-- Ejecutar en DBeaver contra fondos.sqlite tras el primer ciclo
-- con PRIIPS_COST_EXTRACTION_ENABLED = True
-- ============================================================

-- ── Q1: Distribución de Cost_Extraction_Quality ──────────────
-- Objetivo: HIGH + MEDIUM_* > 70% de los fondos procesados.
-- NONE esperado solo en fondos con KID_Format=UNKNOWN (no PRIIPs ni UCITS).
SELECT
    Cost_Extraction_Quality,
    COUNT(*)              AS n,
    ROUND(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM fund_master), 1) AS pct
FROM fund_master
GROUP BY Cost_Extraction_Quality
ORDER BY n DESC;
-- Resultado esperado (aprox.):
--   HIGH          ~500-800  (~15-25%)
--   MEDIUM_EUR    ~800-1200 (~25-37%)
--   MEDIUM_PCT    ~200-400
--   MEDIUM_CROSS  ~100-300
--   LOW           ~200-500
--   NONE          ~600-900  (fondos UNKNOWN format)
--   NULL          ~fondos no procesados aún (si ciclo parcial)


-- ── Q2: Fondos con al menos una fila en fund_cost_schedule ───
-- Objetivo: cuente coherente con HIGH + MEDIUM_* de Q1.
SELECT COUNT(DISTINCT ISIN) AS fondos_con_schedule
FROM fund_cost_schedule;


-- ── Q3: Distribución de KID_Format ───────────────────────────
-- Verificar que la clasificación PRIIPs/UCITS/UNKNOWN es razonable.
SELECT
    KID_Format,
    COUNT(*) AS n
FROM fund_master
WHERE KID_Format IS NOT NULL
GROUP BY KID_Format
ORDER BY n DESC;
-- Resultado esperado: PRIIPS_KID >> UCITS_KIID (solo ~5 fondos UCITS en corpus)


-- ── Q4: Mismatches OC/ACI detectados (para BL-COST-5) ────────
-- Estos fondos tienen OC en BD que el extractor sospecha que es ACI@RHP.
-- BL-COST-5 (sesión Opus separada) analizará y corregirá.
SELECT
    COUNT(*) AS n_mismatch_fondos
FROM ingestion_log
WHERE step = 'BL_COST_4C_OC_ACI_MISMATCH'
  AND status = 'WARN';

-- Detalle de los primeros 20 fondos con mismatch:
SELECT ISIN, message, created_at
FROM ingestion_log
WHERE step = 'BL_COST_4C_OC_ACI_MISMATCH'
  AND status = 'WARN'
ORDER BY created_at DESC
LIMIT 20;


-- ── Q5: Escala de valores — verificar % entero vs ratio ──────
-- CRÍTICO: todos los valores _Pct deben ser > 0.01 (% entero).
-- Si MIN < 0.001, hay fondos con valores en escala ratio (bug de escala).
SELECT
    'Management_Fee_Pct'   AS campo,
    MIN(Management_Fee_Pct)   AS min_val,
    MAX(Management_Fee_Pct)   AS max_val,
    AVG(Management_Fee_Pct)   AS avg_val,
    COUNT(Management_Fee_Pct) AS n_poblado
FROM fund_master WHERE Management_Fee_Pct IS NOT NULL
UNION ALL
SELECT
    'Ongoing_Charge_Recurrent',
    MIN(Ongoing_Charge_Recurrent),
    MAX(Ongoing_Charge_Recurrent),
    AVG(Ongoing_Charge_Recurrent),
    COUNT(Ongoing_Charge_Recurrent)
FROM fund_master WHERE Ongoing_Charge_Recurrent IS NOT NULL
UNION ALL
SELECT
    'ACI_RHP',
    MIN(ACI_RHP), MAX(ACI_RHP), AVG(ACI_RHP), COUNT(ACI_RHP)
FROM fund_master WHERE ACI_RHP IS NOT NULL
UNION ALL
SELECT
    'ACI_1Y',
    MIN(ACI_1Y), MAX(ACI_1Y), AVG(ACI_1Y), COUNT(ACI_1Y)
FROM fund_master WHERE ACI_1Y IS NOT NULL;
-- Resultado esperado: MIN > 0.01 en todos los campos (escala % entero confirmada)


-- ── Q6: Filas de schedule por fondo (distribución) ───────────
-- Cada fondo PRIIPs típicamente tiene 2-3 filas (1Y, 3Y/5Y RHP, etc.)
-- UCITS tiene exactamente 1 fila (sintética).
SELECT
    Source,
    COUNT(*)                          AS total_filas,
    COUNT(DISTINCT ISIN)              AS fondos_distintos,
    ROUND(AVG(1.0 * cnt), 1)         AS avg_filas_por_fondo
FROM (
    SELECT ISIN, Source, COUNT(*) AS cnt
    FROM fund_cost_schedule
    GROUP BY ISIN, Source
) sub
GROUP BY Source
ORDER BY total_filas DESC;


-- ── Q7: Fondos HIGH quality — verificar coherencia OC vs ACI_RHP
-- En fondos HIGH, OC ≈ ACI_1Y (ambos deberían ser cercanos al TER real).
-- Diferencias > 1pp son sospechosas.
SELECT
    fm.ISIN,
    fm.Ongoing_Charge_Recurrent AS OC,
    fm.ACI_1Y,
    fm.ACI_RHP,
    ABS(fm.Ongoing_Charge_Recurrent - fm.ACI_1Y) AS diff_OC_ACI1Y
FROM fund_master fm
WHERE fm.Cost_Extraction_Quality = 'HIGH'
  AND fm.Ongoing_Charge_Recurrent IS NOT NULL
  AND fm.ACI_1Y IS NOT NULL
  AND ABS(fm.Ongoing_Charge_Recurrent - fm.ACI_1Y) > 1.0
ORDER BY diff_OC_ACI1Y DESC
LIMIT 20;
-- Si hay muchas filas → candidatos adicionales para BL-COST-5


-- ── Q8: Errores del pipeline durante el ciclo de costes ──────
SELECT step, status, COUNT(DISTINCT ISIN) AS n_fondos
FROM ingestion_log
WHERE step LIKE 'BL_COST%'
  AND status != 'OK'
GROUP BY step, status
ORDER BY n_fondos DESC;


-- ── Q9: Fondos sin KID_Format (aún no procesados) ────────────
-- Si > 500, el ciclo fue parcial o hay fondos sin KIID disponible.
SELECT COUNT(*) AS fondos_sin_kid_format
FROM fund_master
WHERE KID_Format IS NULL;


-- ── Q10: Completitud del schedule (Is_RHP = 1 por fondo) ─────
-- Cada fondo con schedule debería tener exactamente 1 fila Is_RHP=1.
-- Fondos con 0 filas RHP pueden indicar problema de resolución del RHP.
SELECT
    CASE
        WHEN rhp_count = 0 THEN '0 filas RHP'
        WHEN rhp_count = 1 THEN '1 fila RHP (esperado)'
        ELSE '> 1 fila RHP (anomalía)'
    END AS categoria,
    COUNT(*) AS n_fondos
FROM (
    SELECT ISIN, SUM(Is_RHP) AS rhp_count
    FROM fund_cost_schedule
    GROUP BY ISIN
) sub
GROUP BY categoria
ORDER BY n_fondos DESC;

-- ── FIN DE QUERIES DE CONTROL ─────────────────────────────────



