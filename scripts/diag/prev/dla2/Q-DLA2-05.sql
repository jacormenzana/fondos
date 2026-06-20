-- Q-DLA2-05a: fondos con patología de tabla serializada en texto
-- (números porcentuales flotando sin contexto de header)
-- Requiere REGEXP vía Python: conn.create_function("REGEXP", 2, ...)
SELECT COUNT(*) AS n_patologia_tabla_costes
FROM fund_kiid_metadata km
JOIN fund_master fm ON fm.ISIN = km.ISIN
WHERE fm.Entry_Fee_Pct IS NULL
  AND fm.Fee_Known_Flag = 'NOT_FOUND'
  AND (
      -- Número % existe en el texto pero sin palabra-clave de fees en vecindad
      km.Raw_KIID_Text REGEXP '\b\d{1,2}[,\.]\d{2}\s*%'
      -- Y NO hay patrón normal de detección en entorno cercano
      -- (indicador de que el % existe pero sin contexto legible)
  );

-- Q-DLA2-05b: distribución de Fee_Known_Flag para fondos con tabla de costes
-- (cruce con inventario CSV)
-- Ejecutar tras importar dla_table_inv
SELECT
    fm.Fee_Known_Flag,
    COUNT(*)                            AS n_fondos,
    SUM(CASE WHEN inv.has_cost_table = 1
        THEN 1 ELSE 0 END)              AS con_tabla_costes_detectada,
    SUM(CASE WHEN fm.Entry_Fee_Pct IS NULL
        THEN 1 ELSE 0 END)              AS null_entry_fee
FROM fund_master fm
LEFT JOIN dla_table_inv inv ON inv.ISIN = fm.ISIN
GROUP BY fm.Fee_Known_Flag
ORDER BY n_fondos DESC;