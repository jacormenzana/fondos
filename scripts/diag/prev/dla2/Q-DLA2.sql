-- BL-DLA-2-DIAG es el diagnóstico cuantitativo obligatorio previo a cualquier implementación de BL-DLA-2. 
-- Su función es idéntica a la de BL-DLA-0 respecto a BL-DLA-1: cuantificar la prevalencia del problema, caracterizar las variantes estructurales que necesitan cobertura, 
-- y proporcionar la base empírica para la decisión Go/No-Go.

-- BL-DLA-2 aborda la patología análoga sobre tablas (Cat. 1+2): cuando pdfplumber o fitz extraen el contenido de una tabla como texto plano, 
-- las celdas se concatenan sin semántica fila×columna, destruyendo la información numérica que detectores como _detect_entry_fee,
--  _detect_exit_fee, y _detect_ongoing_charge necesitan.

--==========================================================================================================================================
-- El diagnóstico se organiza en cuatro preguntas independientes que cubren progresivamente de lo macro a lo micro:
-- Q-DLA2-01 cuantifica qué volumen de atributos objetivo tiene cobertura insuficiente y constituiría el beneficio potencial de BL-DLA-2.
-- Q-DLA2-02 cuantifica en cuántos KIIDs las tablas de costes/atributos son físicamente detectables por pdfplumber.find_tables().
-- Q-DLA2-03 caracteriza la distribución de categorías estructurales (Cat. 1, 2, 3) dentro de los KIIDs con tablas detectadas.
-- Q-DLA2-04 cuantifica la solapabilidad entre cobertura de atributos NULL y presencia de tablas en el mismo fondo — es el indicador de ROI real.
-- Q-DLA2-05a: fondos con patología de tabla serializada en texto
-- Q-DLA2-05b: distribución de Fee_Known_Flag para fondos con tabla de costes
--==========================================================================================================================================



--==========================================================================================================================================
-- Q-DLA2-01: Cuantifica qué volumen de atributos objetivo tiene cobertura insuficiente y constituiría el beneficio potencial de BL-DLA-2.
-- Q-DLA2-01: Cobertura actual de atributos objetivo de BL-DLA-2	
-- Ejecutar sobre fund_master post-BL-DLA-1 (producción actual)
--==========================================================================================================================================
SELECT
    -- Cat. 2: atributos de costes (impacto directo en BL-51A/BL-55)
    SUM(CASE WHEN Entry_Fee_Pct  IS NULL THEN 1 ELSE 0 END)   AS null_entry_fee,
    SUM(CASE WHEN Exit_Fee_Pct   IS NULL THEN 1 ELSE 0 END)   AS null_exit_fee,
    SUM(CASE WHEN Ongoing_Charge IS NULL THEN 1 ELSE 0 END)   AS null_ongoing_charge,

    -- Cat. 2: atributos de política (impacto en BL-49)
    SUM(CASE WHEN Accumulation_Policy   IS NULL THEN 1 ELSE 0 END) AS null_acc_policy,
    SUM(CASE WHEN Distribution_Frequency IS NULL
             AND Accumulation_Policy = 'DISTRIBUTION' THEN 1 ELSE 0 END) AS null_dist_freq_distribution,
    SUM(CASE WHEN Hedging_Policy        IS NULL THEN 1 ELSE 0 END) AS null_hedging_policy,
    SUM(CASE WHEN Currency_Hedged       IS NULL THEN 1 ELSE 0 END) AS null_currency_hedged,

    -- Cat. 1: atributos de lista simple (nuevos atributos propuestos en BL-DLA-2)
    SUM(CASE WHEN Fund_Currency IS NULL THEN 1 ELSE 0 END)    AS null_fund_currency,

    -- Total fondos con al menos un atributo objetivo NULL
    SUM(CASE WHEN (
            Entry_Fee_Pct IS NULL OR Exit_Fee_Pct IS NULL OR Ongoing_Charge IS NULL
         OR Accumulation_Policy IS NULL OR Hedging_Policy IS NULL
         OR Currency_Hedged IS NULL
        ) THEN 1 ELSE 0 END) AS fondos_con_algun_null_objetivo,

    COUNT(*) AS total_fondos
FROM fund_master;


--==========================================================================================================================================
-- El diagnóstico se organiza en cuatro preguntas independientes que cubren progresivamente de lo macro a lo micro:
-- Q-DLA2-02 cuantifica en cuántos KIIDs las tablas de costes/atributos son físicamente detectables por pdfplumber.find_tables().
--==========================================================================================================================================

-- Q-DLA-02 — KIIDs sospechosos de patología 2-col (heurística textual):**
-- Contar fondos cuyos `Raw_KIID_Text` contienen las firmas léxicas que solo aparecen en texto cruzado:

SELECT COUNT(*) AS n_sospechosos
FROM fund_kiid_metadata
WHERE Raw_KIID_Text REGEXP '\bTipo\s+(tres|cinco|diez)\s+a[ñn]os\b'
   OR Raw_KIID_Text REGEXP '\bsubfondos\s+mide\b'
   OR Raw_KIID_Text REGEXP '\bdurante\s+un\s+per[ií]odo\s+(El|La|Los|Las|Es)\s+'
   OR Raw_KIID_Text REGEXP '\bproducto\s+(Este|Esta)\s+';


--==========================================================================================================================================
-- El diagnóstico se organiza en cuatro preguntas independientes que cubren progresivamente de lo macro a lo micro:
-- Q-DLA2-03 caracteriza la distribución de categorías estructurales (Cat. 1, 2, 3) dentro de los KIIDs con tablas detectadas.
--==========================================================================================================================================


--==========================================================================================================================================
-- Q-DLA2-04 cuantifica la solapabilidad entre cobertura de atributos NULL y presencia de tablas en el mismo fondo — es el indicador de ROI real.
--==========================================================================================================================================

-- Q-DLA2-04: ROI real de BL-DLA-2
-- Prerequisito: importar dla_table_inventory.csv en tabla temporal

-- Paso 1: crear tabla temporal desde CSV (ejecutar en Python antes de la query)
-- conn.execute("""
--     CREATE TEMP TABLE dla_table_inv (
--         ISIN TEXT PRIMARY KEY,
--         n_tables_detected INTEGER,
--         n_cat1 INTEGER, n_cat2 INTEGER, n_cat3 INTEGER,
--         has_cost_table INTEGER,  -- 0/1
--         has_scenario_table INTEGER,
--         cat_max INTEGER,
--         processing_error TEXT
--     )
-- """)
-- (importar filas del CSV con INSERT)

-- Paso 2: query de ROI
SELECT
    -- Segmento A: fondos con tabla Cat. 2 de costes Y atributo objetivo NULL
    SUM(CASE WHEN inv.has_cost_table = 1
              AND (fm.Entry_Fee_Pct IS NULL
                   OR fm.Exit_Fee_Pct IS NULL
                   OR fm.Ongoing_Charge IS NULL)
        THEN 1 ELSE 0 END)                          AS roi_costes_accionable,

    -- Segmento B: fondos con tabla Cat. 2 de política Y atributo NULL
    SUM(CASE WHEN inv.n_cat2 > 0
              AND (fm.Accumulation_Policy IS NULL
                   OR fm.Hedging_Policy IS NULL
                   OR fm.Currency_Hedged IS NULL)
        THEN 1 ELSE 0 END)                          AS roi_politica_accionable,

    -- Segmento C: fondos con tabla Cat. 3 (BL-DLA-3 scope)
    SUM(CASE WHEN inv.has_scenario_table = 1
        THEN 1 ELSE 0 END)                          AS scope_dla3,

    -- Totales de referencia
    SUM(CASE WHEN inv.n_tables_detected > 0
        THEN 1 ELSE 0 END)                          AS fondos_con_alguna_tabla,
    SUM(CASE WHEN inv.n_cat2 > 0
        THEN 1 ELSE 0 END)                          AS fondos_con_tabla_cat2,
    SUM(CASE WHEN inv.processing_error != ''
        THEN 1 ELSE 0 END)                          AS fondos_con_error_script,
    COUNT(*)                                         AS total_inventariados

FROM dla_table_inv inv
JOIN fund_master fm ON fm.ISIN = inv.ISIN;


--==========================================================================================================================================
-- Q-DLA2-05a: fondos con patología de tabla serializada en texto
-- (números porcentuales flotando sin contexto de header)
-- Requiere REGEXP vía Python: conn.create_function("REGEXP", 2, ...)
--==========================================================================================================================================

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

--==========================================================================================================================================
-- Q-DLA2-05b: distribución de Fee_Known_Flag para fondos con tabla de costes
-- (cruce con inventario CSV)
-- Ejecutar tras importar dla_table_inv
--==========================================================================================================================================
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






