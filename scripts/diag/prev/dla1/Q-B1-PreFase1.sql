-- B-1: Cobertura actual de detectores principales
SELECT
    SUM(CASE WHEN Type IS NULL THEN 1 ELSE 0 END) AS null_type,
    SUM(CASE WHEN Family IS NULL THEN 1 ELSE 0 END) AS null_family,
    SUM(CASE WHEN Entry_Fee_Pct IS NULL THEN 1 ELSE 0 END) AS null_entry,
    SUM(CASE WHEN Exit_Fee_Pct IS NULL THEN 1 ELSE 0 END) AS null_exit,
    SUM(CASE WHEN Ongoing_Charge IS NULL THEN 1 ELSE 0 END) AS null_oc,
    SUM(CASE WHEN Benchmark_Declared IS NULL THEN 1 ELSE 0 END) AS null_bm,
    SUM(CASE WHEN SRRI IS NULL THEN 1 ELSE 0 END) AS null_srri
FROM fund_master;

-- B-2: SRRI_Quality_Flag distribution (referencia de salud actual)
SELECT SRRI_Quality_Flag, COUNT(*) n
FROM fund_kiid_metadata
WHERE KIID_Class = 1
GROUP BY SRRI_Quality_Flag;