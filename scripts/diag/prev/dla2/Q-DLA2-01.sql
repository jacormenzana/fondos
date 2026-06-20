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
-- El diagnóstico se organiza en cuatro preguntas independientes que cubren progresivamente de lo macro a lo micro:
-- Q-DLA2-04 cuantifica la solapabilidad entre cobertura de atributos NULL y presencia de tablas en el mismo fondo — es el indicador de ROI real.
--==========================================================================================================================================

