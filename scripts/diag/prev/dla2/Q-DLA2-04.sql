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