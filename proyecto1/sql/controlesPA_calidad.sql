------

-- BL-19: Sin violaciones Mixto (singular) en BD
SELECT COUNT(*) FROM fund_master WHERE Fund_Nature = 'Mixto';  -- debe ser 0

-- BL-30: Sin Broad+Sector_Focus coexistiendo
SELECT COUNT(*) FROM fund_master WHERE Investment_Focus='Broad' AND Sector_Focus IS NOT NULL;  -- debe ser 0

-- BL-31: Sin contradicción Currency_Hedged vs Hedging_Policy
SELECT COUNT(*) FROM fund_master 
WHERE Currency_Hedged='Hedged' AND Hedging_Policy='UNHEDGED'
   OR Currency_Hedged='Unhedged' AND Hedging_Policy='HEDGED';  -- debe ser 0

-- BL-32: Sin Distribution_Frequency con Accumulation_Policy=NULL
SELECT COUNT(*) FROM fund_master WHERE Distribution_Frequency IS NOT NULL AND Accumulation_Policy IS NULL;  -- debe ser 0

-- BL-33: Sin Monetario/RFC con Investment_Universe NULL
SELECT COUNT(*) FROM fund_master WHERE Fund_Nature IN ('Monetario','Renta Fija Corto Plazo') AND Investment_Universe IS NULL;  -- debe ser 0


-- BL-34: Sin "No aplica" en Credit_Quality
SELECT COUNT(*) FROM fund_master WHERE Credit_Quality = 'No aplica';  -- debe ser 0

-- Verificación cruzada Sesión 1+2: todos los valores Credit_Quality son válidos
SELECT Credit_Quality, COUNT(*) FROM fund_master
WHERE Credit_Quality NOT IN ('Investment Grade','High Yield','Mixed','Not Applicable')
GROUP BY Credit_Quality;  -- debe estar vacío

-- BL-37: Verificar mejora cobertura Ongoing_Charge
SELECT COUNT(*) FROM fund_master WHERE Ongoing_Charge IS NULL;  -- objetivo < 600

-- BL-35: Verificar reducción NOT_FOUND
SELECT Fee_Known_Flag, COUNT(*) FROM fund_master GROUP BY Fee_Known_Flag;

-- BL-38: Verificar que no hay benchmarks contaminados largos
SELECT COUNT(*) FROM fund_master 
WHERE LENGTH(Benchmark_Declared) > 100 
  AND Benchmark_Declared != 'NO_BENCHMARK';  -- debe ser 0

-- BL-38: Verificar que no hay benchmarks con texto en español
SELECT Benchmark_Declared FROM fund_master
WHERE Benchmark_Declared REGEXP '(además|través|riesgo|corro|hemos|clasificado)'
  AND Benchmark_Declared != 'NO_BENCHMARK';  -- debe estar vacío
  
 -- BL-26: Sin Currency_Hedged="Yes" ni "No"
SELECT COUNT(*) FROM fund_master WHERE Currency_Hedged IN ('Yes','No');  -- debe ser 0

-- BL-28: Sin Credit_Quality="No aplica"
SELECT COUNT(*) FROM fund_master WHERE Credit_Quality = 'No aplica';  -- debe ser 0

-- BL-27: Mejora cobertura Market_Cap_Focus en RV
SELECT COUNT(*) FROM fund_master 
WHERE Fund_Nature='Renta Variable' AND Market_Cap_Focus IS NOT NULL;  -- objetivo >200 (era 114)
  
  


-- Sesión 1
SELECT COUNT(*) FROM fund_master WHERE Fund_Nature='Mixto';                    -- 0
SELECT COUNT(*) FROM fund_master WHERE Investment_Focus='Broad' AND Sector_Focus IS NOT NULL; -- 0
SELECT COUNT(*) FROM fund_master WHERE Distribution_Frequency IS NOT NULL AND Accumulation_Policy IS NULL; -- 0
SELECT COUNT(*) FROM fund_master WHERE Fund_Nature IN ('Monetario','Renta Fija Corto Plazo') AND Investment_Universe IS NULL; -- 0

-- Sesión 2
SELECT COUNT(*) FROM fund_master WHERE Credit_Quality IN ('No aplica'); -- 0

-- Sesión 3
SELECT COUNT(*) FROM fund_master WHERE LENGTH(Benchmark_Declared)>100 AND Benchmark_Declared!='NO_BENCHMARK'; -- 0
SELECT Fee_Known_Flag, COUNT(*) FROM fund_master GROUP BY Fee_Known_Flag; -- NOT_FOUND < 1574

-- Sesión 4
SELECT COUNT(*) FROM fund_master WHERE Currency_Hedged IN ('Yes','No'); -- 0
SELECT Currency_Hedged, COUNT(*) FROM fund_master WHERE Currency_Hedged IS NOT NULL GROUP BY Currency_Hedged; -- solo Hedged/Unhedged
SELECT COUNT(*) FROM fund_master WHERE Fund_Nature='Renta Variable' AND Market_Cap_Focus IS NOT NULL; -- objetivo >200

SELECT COUNT(*) FROM fund_master 
WHERE Investment_Focus='Broad' AND Sector_Focus IS NOT NULL;   -- debe ser 0  (BL-30)

SELECT COUNT(*) FROM fund_master 
WHERE (Currency_Hedged='Hedged' AND Hedging_Policy='UNHEDGED')
   OR (Currency_Hedged='Unhedged' AND Hedging_Policy='HEDGED'); -- debe ser 0  (BL-31)

SELECT COUNT(*) FROM fund_master 
WHERE Credit_Quality = 'No aplica';                            -- debe ser 0  (BL-34)

SELECT COUNT(*) FROM fund_master 
WHERE Fund_Nature='Renta Variable' AND Market_Cap_Focus IS NOT NULL; -- objetivo >400 (BL-27)




-- BL-19: Sin violaciones Mixto (singular) en BD -> CORRECTO 0
-- BL-30: Sin Broad+Sector_Focus coexistiendo -> INCORRECTO 97
SELECT COUNT(*) FROM fund_master WHERE Investment_Focus='Broad' AND Sector_Focus IS NOT NULL;  -- debe ser 0

-- BL-31: Sin contradicción Currency_Hedged vs Hedging_Policy -> INCORRECTO 57
SELECT COUNT(*) FROM fund_master 
WHERE Currency_Hedged='Hedged' AND Hedging_Policy='UNHEDGED'
   OR Currency_Hedged='Unhedged' AND Hedging_Policy='HEDGED';  -- debe ser 0

-- BL-32: Sin Distribution_Frequency con Accumulation_Policy=NULL  -> CORRECTO 0
SELECT COUNT(*) FROM fund_master WHERE Distribution_Frequency IS NOT NULL AND Accumulation_Policy IS NULL;  -- debe ser 0

-- BL-33: Sin Monetario/RFC con Investment_Universe NULL -> CORRECTO 0
SELECT COUNT(*) FROM fund_master WHERE Fund_Nature IN ('Monetario','Renta Fija Corto Plazo') AND Investment_Universe IS NULL;  -- debe ser 0


-- BL-34: Sin "No aplica" en Credit_Quality -> CORRECTO 0
SELECT COUNT(*) FROM fund_master WHERE Credit_Quality = 'No aplica';  -- debe ser 0

-- Verificación cruzada Sesión 1+2: todos los valores Credit_Quality son válidos  -> CORRECTO VACIO
SELECT Credit_Quality, COUNT(*) FROM fund_master
WHERE Credit_Quality NOT IN ('Investment Grade','High Yield','Mixed','Not Applicable')
GROUP BY Credit_Quality;  -- debe estar vacío

-- BL-37: Verificar mejora cobertura Ongoing_Charge -> INCORRECTO 275 PERO DENTO DE OBJETIV < 600
SELECT COUNT(*) FROM fund_master WHERE Ongoing_Charge IS NULL;  -- objetivo < 600

-- BL-35: Verificar reducción NOT_FOUND -> INCORRECTO NOT_FOUND 870
SELECT Fee_Known_Flag, COUNT(*) FROM fund_master GROUP BY Fee_Known_Flag;

-- BL-38: Verificar que no hay benchmarks contaminados largos -> INCORRECTO 1
  
SELECT COUNT(*) FROM fund_master 
WHERE LENGTH(Benchmark_Declared) > 100 
  AND Benchmark_Declared != 'NO_BENCHMARK';  -- debe ser 0

-- BL-38: Verificar que no hay benchmarks con texto en español -> INCORRECTO 14 
SELECT Benchmark_Declared 
FROM fund_master
WHERE (
       Benchmark_Declared LIKE '%además%'
    OR Benchmark_Declared LIKE '%través%'
    OR Benchmark_Declared LIKE '%riesgo%'
    OR Benchmark_Declared LIKE '%corro%'
    OR Benchmark_Declared LIKE '%hemos%'
    OR Benchmark_Declared LIKE '%clasificado%'
)
AND Benchmark_Declared != 'NO_BENCHMARK';

  
 -- BL-26: Sin Currency_Hedged="Yes" ni "No"  -> CORRECTO 0
SELECT COUNT(*) FROM fund_master WHERE Currency_Hedged IN ('Yes','No');  -- debe ser 0

-- BL-28: Sin Credit_Quality="No aplica"  -> CORRECTO 0
SELECT COUNT(*) FROM fund_master WHERE Credit_Quality = 'No aplica';  -- debe ser 0

-- BL-27: Mejora cobertura Market_Cap_Focus en RV  -> MEJORA HASTA 433
SELECT COUNT(*) FROM fund_master 
WHERE Fund_Nature='Renta Variable' AND Market_Cap_Focus IS NOT NULL;  -- objetivo >200 (era 114)





-- BL-19: Sin violaciones Mixto (singular) en BD -> CORRECTO 0
-- BL-32: Sin Distribution_Frequency con Accumulation_Policy=NULL  -> CORRECTO 0
-- BL-33: Sin Monetario/RFC con Investment_Universe NULL -> CORRECTO 0
-- BL-34: Sin "No aplica" en Credit_Quality -> CORRECTO 0
-- Verificación cruzada Sesión 1+2: todos los valores Credit_Quality son válidos  -> CORRECTO VACIO
-- BL-37: Verificar mejora cobertura Ongoing_Charge -> INCORRECTO 275 PERO DENTO DE OBJETIVO < 600
-- BL-26: Sin Currency_Hedged="Yes" ni "No"  -> CORRECTO 0
-- BL-28: Sin Credit_Quality="No aplica"  -> CORRECTO 0

-- BL-30: Sin Broad+Sector_Focus coexistiendo -> CORRECTO 0
-- BL-31: Sin contradicción Currency_Hedged vs Hedging_Policy -> CORRECTO 0
-- BL-38: Verificar que no hay benchmarks contaminados largos -> CORRECTO 0

--- PENDIENTES
-- BL-27: Mejora cobertura Market_Cap_Focus en RV  -> INCORRECTO, 433  IGUAL QUE EN EJECUCION ANTERIOR
-- BL-35: Verificar reducción NOT_FOUND -> INCORRECTO NOT_FOUND 870 IGUAL QUE EN EJECUCION ANTERIOR
-- BL-37: Verificar mejora cobertura Ongoing_Charge -> INCORRECTO 275 IGUAL QUE EN EJECUCION ANTERIOR
-- BL-38: Verificar que no hay benchmarks con texto en español -> INCORRECTO 11 CASOS TODO ELLOS CON PATRON "sofr), además"



-- BL-37: Verificar mejora cobertura Ongoing_Charge -> INCORRECTO 275 IGUAL QUE EN EJECUCION ANTERIOR
SELECT COUNT(*) FROM fund_master WHERE Ongoing_Charge IS NULL;  -- objetivo < 600

-- BL-35: Verificar reducción NOT_FOUND -> INCORRECTO NOT_FOUND 870 IGUAL QUE EN EJECUCION ANTERIOR
SELECT Fee_Known_Flag, COUNT(*) FROM fund_master GROUP BY Fee_Known_Flag;

-- BL-38: Verificar que no hay benchmarks con texto en español -> INCORRECTO 11 CASOS TODO ELLOS CON PATRON "sofr), además"
SELECT Benchmark_Declared 
FROM fund_master
WHERE (
       Benchmark_Declared LIKE '%además%'
    OR Benchmark_Declared LIKE '%través%'
    OR Benchmark_Declared LIKE '%riesgo%'
    OR Benchmark_Declared LIKE '%corro%'
    OR Benchmark_Declared LIKE '%hemos%'
    OR Benchmark_Declared LIKE '%clasificado%'
)
AND Benchmark_Declared != 'NO_BENCHMARK';

-- BL-27: Mejora cobertura Market_Cap_Focus en RV  -> INCORRECTO, 433  IGUAL QUE EN EJECUCION ANTERIOR
SELECT COUNT(*) FROM fund_master 
WHERE Fund_Nature='Renta Variable' AND Market_Cap_Focus IS NOT NULL;  -- objetivo >200 (era 114)


Para que tengas mas contexto, te informo que antes de que ejecutaramos el ultimo sprint, cetnrado en Accumulation_Policy, Geography y SFDR_Article, los controles de los puntos de acción mostraban que esos csaos no se habían corregido.

--- PUNTOS DE ACCION PENDIENTES
BL-27: Mejora cobertura Market_Cap_Focus en RV  -> INCORRECTO, 433  IGUAL QUE EN EJECUCION ANTERIOR
BL-35: Verificar reducción NOT_FOUND -> INCORRECTO NOT_FOUND 870 IGUAL QUE EN EJECUCION ANTERIOR
BL-37: Verificar mejora cobertura Ongoing_Charge -> INCORRECTO 275 IGUAL QUE EN EJECUCION ANTERIOR
BL-38: Verificar que no hay benchmarks con texto en español -> INCORRECTO 11 CASOS TODO ELLOS CON PATRON "sofr), además"


Y en relación al PORCENTAJE DE ATRIBUTOS REGISTRANDO VALORES NULOS, EL INDICADOR DEL PORCENTAJE DE FONDOS REGISTRANDO UN VALORES NULOS, SEGMENTADO POR ATRBUOT MUESTRA QUE  TAN SOLO SE HA MEJORADO 
EL VALOR DEL INDICADOR EN UN ATRIBUTO, CURRENCY_HEDGED, MIENTRAS QUE LOS ATRIBUTOS subtype, sector_focus, market_cap_focus regitran una ratio de valores nulos superiores al 80%, hedging_policy y style_profile ceracnos al 70% y benchmark_declared próximo al 46%
La siguiente es la tabla actualizada.

attribute	total	filled	null_count	null_ratio_pct	Variation
accumulation_policy	3204	2418	786	24,53	0
bench	3204	1741	1463	45,66	-0,03
currency_hedged	3204	2009	1195	37,3	-42,76
entry_fee_pct	3204	2341	863	26,94	0
exit_fee_pct	3204	2462	742	23,16	0
family	3204	3204	0	0	0
fund_currency	3204	3147	57	1,78	0
geography	3204	2780	424	13,23	0
hedging_policy	3204	895	2309	72,07	0
investment_focus	3204	3169	35	1,09	0
investment_universe	3204	2925	279	8,71	0
leverage_used	3204	3204	0	0	0
market_cap_focus	3204	466	2738	85,46	0
ongoing_charge	3204	2929	275	8,58	0
profile	3204	3204	0	0	0
sector_focus	3204	374	2830	88,33	0
sfdr_article	3204	1985	1219	38,05	0
srri	3204	3186	18	0,56	0
strategy	3204	3204	0	0	0
style_profile	3204	994	2210	68,98	0
subtype	3204	184	3020	94,26	0
theme	3204	3204	0	0	0
type	3204	3204	0	0	0


Te informo de todo esto para que tengan un mejor contexto y puedas realizar un análisis más certero. 


--------


-- BL-37: Verificar mejora cobertura Ongoing_Charge -> INCORRECTO 275 IGUAL QUE EN EJECUCION ANTERIOR
SELECT COUNT(*) FROM fund_master WHERE Ongoing_Charge IS NULL;  -- objetivo < 600

-- BL-35: Verificar reducción NOT_FOUND -> INCORRECTO NOT_FOUND 870 IGUAL QUE EN EJECUCION ANTERIOR
SELECT Fee_Known_Flag, COUNT(*) FROM fund_master GROUP BY Fee_Known_Flag;

-- BL-38: Verificar que no hay benchmarks con texto en español -> INCORRECTO 11 CASOS TODO ELLOS CON PATRON "sofr), además"
SELECT Benchmark_Declared 
FROM fund_master
WHERE (
       Benchmark_Declared LIKE '%además%'
    OR Benchmark_Declared LIKE '%través%'
    OR Benchmark_Declared LIKE '%riesgo%'
    OR Benchmark_Declared LIKE '%corro%'
    OR Benchmark_Declared LIKE '%hemos%'
    OR Benchmark_Declared LIKE '%clasificado%'
)
AND Benchmark_Declared != 'NO_BENCHMARK';

-- BL-27: Mejora cobertura Market_Cap_Focus en RV  -> INCORRECTO, 433  IGUAL QUE EN EJECUCION ANTERIOR
SELECT COUNT(*) FROM fund_master 
WHERE Fund_Nature='Renta Variable' AND Market_Cap_Focus IS NOT NULL;  -- objetivo >200 (era 114)


-- BL-41: Style_Profile RV — debe subir respecto a 466 actuales
SELECT Style_Profile, COUNT(*) FROM fund_master
WHERE Fund_Nature = 'Renta Variable'
GROUP BY Style_Profile ORDER BY 2 DESC;

-- BL-42: Credit_Quality Mixtos NULL — debe ir a 0
SELECT COUNT(*) FROM fund_master
WHERE Fund_Nature = 'Mixtos' AND Credit_Quality IS NULL;
-- Esperado: 0

-- BL-43: Subtype Monetario — 18 JPMorgan + potenciales adicionales
SELECT Subtype, COUNT(*) FROM fund_master
WHERE Fund_Nature = 'Monetario'
GROUP BY Subtype ORDER BY 2 DESC;

-- BL-43: Subtype Mixtos — 10 Fixed Band + 1 Volatility Target
SELECT Subtype, COUNT(*) FROM fund_master
WHERE Fund_Nature = 'Mixtos' AND Subtype IS NOT NULL
GROUP BY Subtype ORDER BY 2 DESC;

-- Verificación solapamiento Family/Subtype Monetario (análisis posterior)
SELECT Family, Subtype, COUNT(*) FROM fund_master
WHERE Fund_Nature = 'Monetario'
GROUP BY Family, Subtype ORDER BY 1, 2;



SELECT COUNT(*) FROM fund_master WHERE Ongoing_Charge IS NULL;
-- Esperado: ≈ 93

SELECT Fee_Known_Flag, COUNT(*) FROM fund_master
GROUP BY Fee_Known_Flag ORDER BY 2 DESC;
-- Esperado: NOT_FOUND ≈ 144

SELECT COUNT(*) FROM fund_master WHERE Accumulation_Policy IS NULL;
-- Esperado: ≈ 396


----
----------------------------------------------
------
------

-- BL-37: Verificar mejora cobertura Ongoing_Charge -> INCORRECTO 74 -> BAJA DESDE 270 
-- BL-35: Verificar reducción NOT_FOUND -> INCORRECTO NOT_FOUND 139 -> BAJA DESDE 223 
-- BL-27: Mejora cobertura Market_Cap_Focus en RV  -> INCORRECTO 432 -> BAJA DESDE 433
-- BL-41: Style_Profile RV — sube a 544 desde a 466
-- BL-42: Credit_Quality Mixtos NULL — CORRECTO 0 
-- BL-43: Subtype Monetario — INCORRECTO 62 NULOS

Subtype	COUNT(*)
[NULL]	62
VNAV	19
LVNAV	12
CNAV	5


-- BL-43: Subtype Mixtos — 10 Fixed Band + 1 Volatility Target - SI DIAGNOSTICO

Subtype	COUNT(*)
Volatility Target	3
Fixed Band 75	3
Fixed Band 50	3
Fixed Band 15	3

-- Verificación solapamiento Family/Subtype Monetario (análisis posterior)
Family	Subtype	COUNT(*)
CNAV	CNAV	3
LVNAV	LVNAV	9
Monetario	[NULL]	62
Monetario	CNAV	2
Monetario	LVNAV	3
Monetario	VNAV	13
VNAV	VNAV	6



--- Ratio de valores nulos por atributo
attribute	total	filled	null_count	null_ratio_pct
subtype	3.204	232	2.972	92,76
sector_focus	3.204	374	2.830	88,33
market_cap_focus	3.204	466	2.738	85,46
style_profile	3.204	1.215	1.989	62,08
bench	3.204	1.732	1.472	45,94
sfdr_article	3.204	1.994	1.210	37,77
currency_hedged	3.204	2.056	1.148	35,83
hedging_policy	3.204	2.412	792	24,72
exit_fee_pct	3.204	2.469	735	22,94
accumulation_policy	3.204	2.810	394	12,3
geography	3.204	2.898	306	9,55
investment_universe	3.204	3.001	203	6,34
entry_fee_pct	3.204	3.070	134	4,18
ongoing_charge	3.204	3.130	74	2,31
fund_currency	3.204	3.147	57	1,78
investment_focus	3.204	3.169	35	1,09
srri	3.204	3.186	18	0,56
profile	3.204	3.204	0	0
strategy	3.204	3.204	0	0
family	3.204	3.204	0	0
type	3.204	3.204	0	0
theme	3.204	3.204	0	0
leverage_used	3.204	3.204	0	0




----
----------------------------------------------
------
------


-- BL-27: Mejora cobertura Market_Cap_Focus en RV  -> INCORRECTO, 433  IGUAL QUE EN EJECUCION ANTERIOR
SELECT COUNT(*) FROM fund_master 
WHERE Fund_Nature='Renta Variable' AND Market_Cap_Focus IS NOT NULL;  -- objetivo >200 (era 114)

-- BL-35: Verificar reducción NOT_FOUND -> INCORRECTO NOT_FOUND 870 IGUAL QUE EN EJECUCION ANTERIOR
SELECT Fee_Known_Flag, COUNT(*) FROM fund_master GROUP BY Fee_Known_Flag;

-- BL-37: Verificar mejora cobertura Ongoing_Charge -> INCORRECTO 275 IGUAL QUE EN EJECUCION ANTERIOR
SELECT COUNT(*) FROM fund_master WHERE Ongoing_Charge IS NULL;  -- objetivo < 600

-- BL-41: Style_Profile RV — debe subir respecto a 466 actuales
SELECT Style_Profile, COUNT(*) FROM fund_master
WHERE Fund_Nature = 'Renta Variable'
GROUP BY Style_Profile ORDER BY 2 DESC;

-- BL-42: Credit_Quality Mixtos NULL — debe ir a 0
SELECT COUNT(*) FROM fund_master
WHERE Fund_Nature = 'Mixtos' AND Credit_Quality IS NULL;
-- Esperado: 0

-- BL-43: Subtype Monetario — 18 JPMorgan + potenciales adicionales
SELECT Subtype, COUNT(*) FROM fund_master
WHERE Fund_Nature = 'Monetario'
GROUP BY Subtype ORDER BY 2 DESC;

-- BL-43: Subtype Mixtos — 10 Fixed Band + 1 Volatility Target
SELECT Subtype, COUNT(*) FROM fund_master
WHERE Fund_Nature = 'Mixtos' AND Subtype IS NOT NULL
GROUP BY Subtype ORDER BY 2 DESC;

-- Verificación solapamiento Family/Subtype Monetario (análisis posterior)
SELECT Family, Subtype, COUNT(*) FROM fund_master
WHERE Fund_Nature = 'Monetario'
GROUP BY Family, Subtype ORDER BY 1, 2;


-- BL-44: Nature incompatible con SRRI → debe ser 0
SELECT Fund_Nature, SRRI, COUNT(*) FROM fund_master
WHERE Fund_Nature IN ('Monetario','Renta Fija Corto Plazo') 
  AND CAST(SRRI AS INTEGER) >= 3
  AND Fund_Nature = 'Monetario'
UNION ALL
SELECT Fund_Nature, SRRI, COUNT(*) FROM fund_master
WHERE Fund_Nature = 'Renta Fija Corto Plazo' 
  AND CAST(SRRI AS INTEGER) >= 4
GROUP BY Fund_Nature, SRRI;
-- Debe devolver 0 filas

-- BL-46: Benchmark_Type NULL con Benchmark_Declared poblado → debe ser 0
SELECT COUNT(*) FROM fund_master
WHERE Benchmark_Declared IS NOT NULL
  AND Benchmark_Declared != 'NO_BENCHMARK'
  AND Benchmark_Type IS NULL;

-- BL-47: Is_ESG=1 sin Sfdr_Article → debe ser 0
SELECT COUNT(*) FROM fund_master WHERE Is_ESG=1 AND Sfdr_Article IS NULL;

-- BL-48: Family LVNAV/VNAV/CNAV en Monetario → debe ser 0
SELECT COUNT(*) FROM fund_master
WHERE Fund_Nature='Monetario' AND Family IN ('LVNAV','VNAV','CNAV');

-- BL-48: verificar distribución Subtype en Monetario tras normalización
SELECT Family, Subtype, COUNT(*) FROM fund_master
WHERE Fund_Nature='Monetario' GROUP BY Family, Subtype ORDER BY 1,2;
-- Esperado: Family siempre 'Monetario'; Subtype con LVNAV/VNAV/CNAV/Standard MMF/NULL


-- BL-27: Mejora cobertura Market_Cap_Focus en RV  -> INCORRECTO - 1648   a
-- BL-35: Verificar reducción NOT_FOUND -> INCORRECTO NOT_FOUND 139
-- BL-37: Verificar mejora cobertura Ongoing_Charge -> INCORRECTO 74 
-- BL-41: Style_Profile RV — INCORRECTO 1 NULL

-- BL-42: Credit_Quality Mixtos NULL —CORRECTO 0
-- BL-43: Subtype Monetario — CORRECTO 0 NO CLASIFICADO
-- BL-43: Subtype Mixtos — CORRECTO 0
-- BL-44: Nature incompatible con SRRI → CORRECTO 0 
-- BL-46: Benchmark_Type NULL con Benchmark_Declared poblado → CORRECTO 0
-- BL-47: Is_ESG=1 sin Sfdr_Article → CORRECTO 0
-- BL-48: Family LVNAV/VNAV/CNAV en Monetario → CORRECTO 0
-- BL-48: verificar distribución Subtype en Monetario tras normalización -> CORRECTO





-- BL-35: Verificar reducción NOT_FOUND -> INCORRECTO NOT_FOUND 139
-- Esperado: NOT_FOUND ≈ 144 ->  RESULTADO 139
SELECT Fee_Known_Flag, COUNT(*) FROM fund_master
GROUP BY Fee_Known_Flag ORDER BY 2 DESC;


-- BL-37: Verificar mejora cobertura Ongoing_Charge -> INCORRECTO 74 
-- Esperado: ≈ 93  -> RESULTADO 74
SELECT COUNT(*) FROM fund_master WHERE Ongoing_Charge IS NULL;

-- BL-40 | Accumulation_Policy NULL Deutsche+BlackRock
-- Esperado: ≈ 396 -> RESULTADO 394
SELECT COUNT(*) FROM fund_master WHERE Accumulation_Policy IS NULL;




---- 


-- BL-49 — Currency_Hedged: extensión de extracción al texto KIID -> INCORRECTO 170
  -- Cuantificación del gap
  SELECT COUNT(*) FROM fund_master
  WHERE Fund_Currency IN ('USD','GBP','CHF','JPY','CNH')
    AND Currency_Hedged IS NULL;
  -- Resultado actual: 267


-- BL-50 — Nulos cruzados Investment_Universe / Geography (inferencia INTER)** -> INCORRECTO 7

-- Geography poblado pero Universe NULL
     SELECT Geography, COUNT(*) FROM fund_master
     WHERE Investment_Universe IS NULL AND Geography IS NOT NULL
     GROUP BY Geography ORDER BY 2 DESC;

     -- Universe poblado pero Geography NULL (Country/Regional)
     SELECT Investment_Universe, COUNT(*) FROM fund_master
     WHERE Investment_Universe IN ('Country','Regional') AND Geography IS NULL
     GROUP BY Investment_Universe;

  --- Control SQL post-fix:
    SELECT COUNT(*) FROM fund_master
    WHERE Investment_Universe IS NULL AND Geography IS NOT NULL
    AND Geography IN ('EEUU','China','Japón','India','Brasil','Europa','Asia','Emergentes','Global');
  -- Objetivo: 0     



-- BL-51 — Comisiones con estructura mixta: porcentaje + tope fijo -> INCORRECTO 676 CON  Entry_Fee_Pct NULL (MEJORA-> REGISTRAR IMPLICITAMENTE CON VALOR 0 AQUELLOS CASOS EN LOS QUE SE INFORMA QUE COMISION DE SALIDA ES CERO )
-- BL-51 — Comisiones con estructura mixta: porcentaje + tope fijo -> INCORRECTO 110 CON  Entry_Fee_Pct NULL Y FEE_KNOWN_FLAG NOT_FOUND
    --- Problema A — Patrones de extracción no cubiertos → RESUELTO en v24

      SELECT COUNT(*) FROM fund_master WHERE Exit_Fee_Pct IS NULL;
      -- Objetivo: reducción desde 735 a ~680-710

      SELECT COUNT(*) FROM fund_master WHERE Entry_Fee_Pct IS NULL AND Fee_Known_Flag='NOT_FOUND';
      -- Objetivo: reducción desde 134 a ~75-90


  --- Problema B — Estructura mixta (pct + tope fijo) → análisis completado, schema pendiente





  -- BL-52 — Inconsistencia semántica Investment_Universe vs Geography: valores de región en campo Country -> CORRECTO 0
      SELECT Geography, Investment_Universe, COUNT(*) FROM fund_master
      WHERE Investment_Universe = 'Country'
      AND Geography IN ('Latinoamérica','Europa del Este','Asia','Emergentes','América Latina','Europa Central')
      GROUP BY Geography, Investment_Universe;


  -- BL-53 — Inconsistencias lingüísticas intra-atributo: valores en inglés en columnas con idioma objetivo español -> INCORRECTO

      SELECT 'family' AS col, Family AS val, COUNT(*) AS n FROM fund_master WHERE Family IS NOT NULL GROUP BY Family
      UNION ALL
      SELECT 'type', Type, COUNT(*) FROM fund_master WHERE Type IS NOT NULL GROUP BY Type
      UNION ALL
      SELECT 'subtype', Subtype, COUNT(*) FROM fund_master WHERE Subtype IS NOT NULL GROUP BY Subtype
      UNION ALL
      SELECT 'sector_focus', Sector_Focus, COUNT(*) FROM fund_master WHERE Sector_Focus IS NOT NULL GROUP BY Sector_Focus
      ORDER BY 1, 3 DESC;


            SELECT 'family' AS col, Family AS val, COUNT(*) AS n FROM fund_master WHERE Family IS NOT NULL GROUP BY Family
      UNION ALL
      SELECT 'type', Type, COUNT(*) FROM fund_master WHERE Type IS NOT NULL GROUP BY Type
      UNION ALL
      SELECT 'subtype', Subtype, COUNT(*) FROM fund_master WHERE Subtype IS NOT NULL GROUP BY Subtype
      UNION ALL
      SELECT 'sector_focus', Sector_Focus, COUNT(*) FROM fund_master WHERE Sector_Focus IS NOT NULL GROUP BY Sector_Focus
      ORDER BY 1, 3 DESC;

col val n
family  RV Core 1.455
family  Renta Fija Corto Plazo  427
family  Renta Fija Flexible 415
family  Mixtos  365
family  RV Temática 218
family  Income Oriented 104
family  Monetario 99
family  Retorno Absoluto  43
family  RF High Yield 39
family  Activos Reales  17
family  Estructurado  8
family  RF Emergentes 5
family  RF Inflación  5
family  Flexible Estratégico  4
sector_focus  Tecnología e Innovación 163
sector_focus  Energía y Recursos  56
sector_focus  Salud y Ciencias de la Vida 46
sector_focus  Materiales y Minería  41
sector_focus  Utilities y Medio Ambiente  22
sector_focus  Activos Reales  11
sector_focus  Real Assets 11
sector_focus  Servicios Financieros 10
sector_focus  Energy & Resources  6
sector_focus  Consumo 5
sector_focus  Utilities & Environment 2
sector_focus  Healthcare & Life Sciences  1
subtype Fondo Indexado  73
subtype Oportunista 41
subtype Standard MMF  38
subtype VNAV  20
subtype ETF 16
subtype Física / Derivados  16
subtype LVNAV 12
subtype Corta Duración  9
subtype Autocallable  8
subtype Floating Rate Notes 7
subtype CNAV  5
subtype Global Macro  5
subtype Banda Fija 15 3
subtype Banda Fija 50 3
subtype Banda Fija 75 3
subtype Convertibles  3
subtype Objetivo de Volatilidad 3
subtype Total Return Bond 2
subtype Inmobiliario  1
subtype Long/Short  1
subtype Valor Relativo / Arbitraje  1
type  Gestión Activa  1.584
type  Allocation  464
type  Renta Fija Flexible 455
type  Renta Fija Corto Plazo  353
type  Monetario 94
type  Indexado  89
type  Crédito CP  59
type  Absolute Return 46
type  Materias Primas 16
type  Estructurado  8
type  Floating Rate CP  7
type  Gobierno CP 6
type  Target Maturity 6
type  Tactical Allocation 4
type  Total Return  4
type  Monetario Privado 3
type  Deuda Pública CP  2
type  Monetario Público 2
type  Activos Reales  1
type  Objetivo de Volatilidad 1




-- BL-49 — Currency_Hedged: extensión de extracción al texto KIID -> INCORRECTO 170
-- BL-50 — Nulos cruzados Investment_Universe / Geography (inferencia INTER)** -> INCORRECTO 7
-- BL-51 — Comisiones con estructura mixta: porcentaje + tope fijo -> INCORRECTO 676 CON  Entry_Fee_Pct NULL (MEJORA-> REGISTRAR IMPLICITAMENTE CON VALOR 0 AQUELLOS CASOS EN LOS QUE SE INFORMA QUE COMISION DE SALIDA ES CERO )
-- BL-51 — Comisiones con estructura mixta: porcentaje + tope fijo -> INCORRECTO 110 CON  Entry_Fee_Pct NULL Y FEE_KNOWN_FLAG NOT_FOUND
-- BL-52 — Inconsistencia semántica Investment_Universe vs Geography: valores de región en campo Country -> CORRECTO 0
-- BL-53 — Inconsistencias lingüísticas intra-atributo: valores en inglés en columnas con idioma objetivo español -> INCORRECTO

      SELECT 'family' AS col, Family AS val, COUNT(*) AS n FROM fund_master WHERE Family IS NOT NULL GROUP BY Family
      UNION ALL
      SELECT 'type', Type, COUNT(*) FROM fund_master WHERE Type IS NOT NULL GROUP BY Type
      UNION ALL
      SELECT 'subtype', Subtype, COUNT(*) FROM fund_master WHERE Subtype IS NOT NULL GROUP BY Subtype
      UNION ALL
      SELECT 'sector_focus', Sector_Focus, COUNT(*) FROM fund_master WHERE Sector_Focus IS NOT NULL GROUP BY Sector_Focus
      ORDER BY 1, 3 DESC;


            SELECT 'family' AS col, Family AS val, COUNT(*) AS n FROM fund_master WHERE Family IS NOT NULL GROUP BY Family
      UNION ALL
      SELECT 'type', Type, COUNT(*) FROM fund_master WHERE Type IS NOT NULL GROUP BY Type
      UNION ALL
      SELECT 'subtype', Subtype, COUNT(*) FROM fund_master WHERE Subtype IS NOT NULL GROUP BY Subtype
      UNION ALL
      SELECT 'sector_focus', Sector_Focus, COUNT(*) FROM fund_master WHERE Sector_Focus IS NOT NULL GROUP BY Sector_Focus
      ORDER BY 1, 3 DESC;



-- BL-49 — Currency_Hedged: extensión de extracción al texto KIID -> INCORRECTO 170
-- BL-50 — Nulos cruzados Investment_Universe / Geography (inferencia INTER)** -> INCORRECTO 7
-- BL-51 — Comisiones con estructura mixta: porcentaje + tope fijo -> INCORRECTO 676 CON  Entry_Fee_Pct NULL (MEJORA-> REGISTRAR IMPLICITAMENTE CON VALOR 0 AQUELLOS CASOS EN LOS QUE SE INFORMA QUE COMISION DE SALIDA ES CERO )
-- BL-51 — Comisiones con estructura mixta: porcentaje + tope fijo -> INCORRECTO 110 CON  Entry_Fee_Pct NULL Y FEE_KNOWN_FLAG NOT_FOUND
-- BL-53 — Inconsistencias lingüísticas intra-atributo: valores en inglés en columnas con idioma objetivo español -> INCORRECTO



-- BL-49 — Currency_Hedged: extensión de extracción al texto KIID -> INCORRECTO 170
-- BL-50 — Nulos cruzados Investment_Universe / Geography (inferencia INTER)** -> INCORRECTO 7
-- BL-51 — Comisiones con estructura mixta: porcentaje + tope fijo -> INCORRECTO 673 CON  Entry_Fee_Pct NULL (MEJORA-> REGISTRAR IMPLICITAMENTE CON VALOR 0 AQUELLOS CASOS EN LOS QUE SE INFORMA QUE COMISION DE SALIDA ES CERO )
-- BL-51 — Comisiones con estructura mixta: porcentaje + tope fijo -> INCORRECTO 110 CON  Entry_Fee_Pct NULL Y FEE_KNOWN_FLAG NOT_FOUND
-- BL-53 — Inconsistencias lingüísticas intra-atributo: valores en inglés en columnas con idioma objetivo español -> INCORRECTO

    family  RV Core 1455
    family  Renta Fija Corto Plazo  427
    family  Renta Fija Flexible 415
    family  Mixtos  365
    family  RV Temática 218
    family  Income Oriented 104
    family  Monetario 99
    family  Retorno Absoluto  43
    family  RF High Yield 39
    family  Activos Reales  17
    family  Estructurado  8
    family  RF Emergentes 5
    family  RF Inflación  5
    family  Flexible Estratégico  4
    sector_focus  Tecnología e Innovación 163
    sector_focus  Energía y Recursos  56
    sector_focus  Salud y Ciencias de la Vida 46
    sector_focus  Materiales y Minería  41
    sector_focus  Utilities y Medio Ambiente  22
    sector_focus  Activos Reales  11
    sector_focus  Real Assets 11
    sector_focus  Servicios Financieros 10
    sector_focus  Energy & Resources  6
    sector_focus  Consumo 5
    sector_focus  Utilities & Environment 2
    sector_focus  Healthcare & Life Sciences  1
    subtype Fondo Indexado  73
    subtype Oportunista 41
    subtype Standard MMF  38
    subtype VNAV  20
    subtype ETF 16
    subtype Física / Derivados  16
    subtype LVNAV 12
    subtype Corta Duración  9
    subtype Autocallable  8
    subtype Floating Rate Notes 7
    subtype CNAV  5
    subtype Global Macro  5
    subtype Banda Fija 15 3
    subtype Banda Fija 50 3
    subtype Banda Fija 75 3
    subtype Objetivo de Volatilidad 3
    subtype Total Return Bond 2
    subtype Inmobiliario  1
    subtype Long/Short  1
    subtype Valor Relativo / Arbitraje  1
    type  Gestión Activa  1584
    type  Allocation  464
    type  Renta Fija Flexible 455
    type  Renta Fija Corto Plazo  353
    type  Monetario 94
    type  Indexado  89
    type  Crédito CP  59
    type  Absolute Return 46
    type  Materias Primas 16
    type  Estructurado  8
    type  Floating Rate CP  7
    type  Gobierno CP 6
    type  Target Maturity 6
    type  Tactical Allocation 4
    type  Total Return  4
    type  Monetario Privado 3
    type  Deuda Pública CP  2
    type  Monetario Público 2
    type  Activos Reales  1
    type  Objetivo de Volatilidad 1


-- ── BL-53/54: Sector_Focus debe estar 100% en español ────────────────────── -> INCORRECTO
SELECT Sector_Focus, COUNT(*) AS n
FROM fund_master
WHERE Sector_Focus IN (
    'Real Assets', 'Energy & Resources', 'Utilities & Environment',
    'Healthcare & Life Sciences', 'Technology & Innovation',
    'Materials & Mining', 'Financials & Insurance', 'Consumer Discretionary'
)
GROUP BY Sector_Focus;
-- Objetivo: 0 filas

-- ── BL-55: Exit_Fee_Pct y distribución de Fee_Known_Flag ──────────────────── Incorrecto 673 en Exit_Fee_Pct IS NULL
SELECT COUNT(*) AS exit_null FROM fund_master WHERE Exit_Fee_Pct IS NULL;
-- Objetivo: bajada desde 676 al rango 200-350

SELECT Fee_Known_Flag, COUNT(*) AS n FROM fund_master
GROUP BY Fee_Known_Flag ORDER BY 2 DESC;
-- Esperado: aparición de EXIT_INFERRED_ZERO (~250-400 casos)
--           y EXIT_EXPLICIT_ZERO (~50-100 casos adicionales por patrones nuevos 9-12)

--- Segmentación Fee_Known_Flag
Fee_Known_Flag  n
ZERO_CONFIRMED  1.669
EXTRACTED 1.420
NOT_FOUND 115

-- ── BL-56: Auditoría lingüística general post-normalización ───────────────── ->  INCORRECTO
SELECT 'family' AS col, Family AS val, COUNT(*) AS n
FROM fund_master WHERE Family IS NOT NULL GROUP BY Family
UNION ALL
SELECT 'type', Type, COUNT(*) FROM fund_master WHERE Type IS NOT NULL GROUP BY Type
UNION ALL
SELECT 'sector_focus', Sector_Focus, COUNT(*) FROM fund_master WHERE Sector_Focus IS NOT NULL GROUP BY Sector_Focus
ORDER BY 1, 3 DESC;


col val n
family  RV Core 1.455
family  Renta Fija Corto Plazo  427
family  Renta Fija Flexible 415
family  Mixtos  365
family  RV Temática 218
family  Income Oriented 104
family  Monetario 99
family  Retorno Absoluto  43
family  RF High Yield 39
family  Activos Reales  17
family  Estructurado  8
family  RF Emergentes 5
family  RF Inflación  5
family  Flexible Estratégico  4
sector_focus  Tecnología e Innovación 163
sector_focus  Energía y Recursos  56
sector_focus  Salud y Ciencias de la Vida 46
sector_focus  Materiales y Minería  41
sector_focus  Utilities y Medio Ambiente  22
sector_focus  Activos Reales  11
sector_focus  Real Assets 11
sector_focus  Servicios Financieros 10
sector_focus  Energy & Resources  6
sector_focus  Consumo 5
sector_focus  Utilities & Environment 2
sector_focus  Healthcare & Life Sciences  1
type  Gestión Activa  1.584
type  Allocation  464
type  Renta Fija Flexible 455
type  Renta Fija Corto Plazo  353
type  Monetario 94
type  Indexado  89
type  Crédito CP  59
type  Absolute Return 46
type  Materias Primas 16
type  Estructurado  8
type  Floating Rate CP  7
type  Gobierno CP 6
type  Target Maturity 6
type  Tactical Allocation 4
type  Total Return  4
type  Monetario Privado 3
type  Deuda Pública CP  2
type  Monetario Público 2
type  Activos Reales  1
type  Objetivo de Volatilidad 1
-- ── BL-57: Family='Income Oriented' sin cambio (excepción documentada) ────── -> INCORRECTO ->104
SELECT Fund_Nature, COUNT(*) FROM fund_master
WHERE Family = 'Income Oriented' GROUP BY Fund_Nature ORDER BY 2 DESC;
-- Objetivo: distribución estable respecto a v3.2 (~104 fondos)


-- ── Regresión: items resueltos deben seguir en 0 ──────────────────────────── CORRECTO -> 0
SELECT 'BL-19' AS bl, COUNT(*) AS n FROM fund_master WHERE Fund_Nature = 'Mixto'
UNION ALL SELECT 'BL-26', COUNT(*) FROM fund_master WHERE Currency_Hedged IN ('Yes', 'No')
UNION ALL SELECT 'BL-31', COUNT(*) FROM fund_master
  WHERE (Currency_Hedged='Hedged' AND Hedging_Policy='UNHEDGED')
     OR (Currency_Hedged='Unhedged' AND Hedging_Policy='HEDGED')
UNION ALL SELECT 'BL-52', COUNT(*) FROM fund_master
  WHERE Investment_Universe='Country'
    AND Geography IN ('Latinoamérica','Europa del Este','Asia Pacífico',
                      'Emergentes','América Latina','Europa Central',
                      'África','Oriente Medio','América del Norte');
-- Todos deben devolver 0




--- RESUITADO
-- BL-19 Regresión: items resueltos deben seguir en 0 CORRECTO -> 0
-- BL-49 — Currency_Hedged: extensión de extracción al texto KIID -> INCORRECTO 170
-- BL-50 — Nulos cruzados Investment_Universe / Geography (inferencia INTER)** -> INCORRECTO 7
-- BL-51 — Comisiones con estructura mixta: porcentaje + tope fijo -> INCORRECTO 673 CON  Entry_Fee_Pct NULL (MEJORA-> REGISTRAR IMPLICITAMENTE CON VALOR 0 AQUELLOS CASOS EN LOS QUE SE INFORMA QUE COMISION DE SALIDA ES CERO )
-- BL-51 — Comisiones con estructura mixta: porcentaje + tope fijo -> INCORRECTO 110 CON  Entry_Fee_Pct NULL Y FEE_KNOWN_FLAG NOT_FOUND
-- BL-53 — Inconsistencias lingüísticas intra-atributo: valores en inglés en columnas con idioma objetivo español -> INCORRECTO

    family  RV Core 1455
    family  Renta Fija Corto Plazo  427
    family  Renta Fija Flexible 415
    family  Mixtos  365
    family  RV Temática 218
    family  Income Oriented 104
    family  Monetario 99
    family  Retorno Absoluto  43
    family  RF High Yield 39
    family  Activos Reales  17
    family  Estructurado  8
    family  RF Emergentes 5
    family  RF Inflación  5
    family  Flexible Estratégico  4
    sector_focus  Tecnología e Innovación 163
    sector_focus  Energía y Recursos  56
    sector_focus  Salud y Ciencias de la Vida 46
    sector_focus  Materiales y Minería  41
    sector_focus  Utilities y Medio Ambiente  22
    sector_focus  Activos Reales  11
    sector_focus  Real Assets 11
    sector_focus  Servicios Financieros 10
    sector_focus  Energy & Resources  6
    sector_focus  Consumo 5
    sector_focus  Utilities & Environment 2
    sector_focus  Healthcare & Life Sciences  1
    subtype Fondo Indexado  73
    subtype Oportunista 41
    subtype Standard MMF  38
    subtype VNAV  20
    subtype ETF 16
    subtype Física / Derivados  16
    subtype LVNAV 12
    subtype Corta Duración  9
    subtype Autocallable  8
    subtype Floating Rate Notes 7
    subtype CNAV  5
    subtype Global Macro  5
    subtype Banda Fija 15 3
    subtype Banda Fija 50 3
    subtype Banda Fija 75 3
    subtype Objetivo de Volatilidad 3
    subtype Total Return Bond 2
    subtype Inmobiliario  1
    subtype Long/Short  1
    subtype Valor Relativo / Arbitraje  1
    type  Gestión Activa  1584
    type  Allocation  464
    type  Renta Fija Flexible 455
    type  Renta Fija Corto Plazo  353
    type  Monetario 94
    type  Indexado  89
    type  Crédito CP  59
    type  Absolute Return 46
    type  Materias Primas 16
    type  Estructurado  8
    type  Floating Rate CP  7
    type  Gobierno CP 6
    type  Target Maturity 6
    type  Tactical Allocation 4
    type  Total Return  4
    type  Monetario Privado 3
    type  Deuda Pública CP  2
    type  Monetario Público 2
    type  Activos Reales  1
    type  Objetivo de Volatilidad 1


-- ── BL-53/54: Sector_Focus debe estar 100% en español ────────────────────── -> INCORRECTO 4 FILAS

-- ── BL-55: Exit_Fee_Pct y distribución de Fee_Known_Flag ──────────────────── Incorrecto 673 en Exit_Fee_Pct IS NULL
--- Segmentación Fee_Known_Flag
Fee_Known_Flag  n
ZERO_CONFIRMED  1.669
EXTRACTED 1.420
NOT_FOUND 115

-- ── BL-56: Auditoría lingüística general post-normalización ───────────────── ->  INCORRECTO Listado en BL-53
-- ── BL-57: Family='Income Oriented' sin cambio (excepción documentada) ────── -> INCORRECTO ->104





-- BL-49 — Currency_Hedged: extensión de extracción al texto KIID -> INCORRECTO 170
  -- Cuantificación del gap
  SELECT COUNT(*) FROM fund_master
  WHERE Fund_Currency IN ('USD','GBP','CHF','JPY','CNH')
    AND Currency_Hedged IS NULL;
  -- Resultado actual: 267

-- BL-50 — Nulos cruzados Investment_Universe / Geography (inferencia INTER)** -> INCORRECTO 7
-- Geography poblado pero Universe NULL
     SELECT Geography, COUNT(*) FROM fund_master
     WHERE Investment_Universe IS NULL AND Geography IS NOT NULL
     GROUP BY Geography ORDER BY 2 DESC;

      EEUU  5
      Asia  2

-- BL-51 — Comisiones con estructura mixta: porcentaje + tope fijo -> INCORRECTO 673 CON  Entry_Fee_Pct NULL 
-- BL-51 — Comisiones con estructura mixta: porcentaje + tope fijo -> INCORRECTO 110 CON  Entry_Fee_Pct NULL Y FEE_KNOWN_FLAG NOT_FOUND
-- BL-53 — Inconsistencias lingüísticas intra-atributo: valores en inglés en columnas con idioma objetivo español -> INCORRECTO

      SELECT 'family' AS col, Family AS val, COUNT(*) AS n FROM fund_master WHERE Family IS NOT NULL GROUP BY Family
      UNION ALL
      SELECT 'type', Type, COUNT(*) FROM fund_master WHERE Type IS NOT NULL GROUP BY Type
      UNION ALL
      SELECT 'subtype', Subtype, COUNT(*) FROM fund_master WHERE Subtype IS NOT NULL GROUP BY Subtype
      UNION ALL
      SELECT 'sector_focus', Sector_Focus, COUNT(*) FROM fund_master WHERE Sector_Focus IS NOT NULL GROUP BY Sector_Focus
      ORDER BY 1, 3 DESC;

      family  RV Core 1455
      family  Renta Fija Corto Plazo  427
      family  Renta Fija Flexible 415
      family  Mixtos  365
      family  RV Temática 218
      family  Income Oriented 104
      family  Monetario 99
      family  Retorno Absoluto  43
      family  RF High Yield 39
      family  Activos Reales  17
      family  Estructurado  8
      family  RF Emergentes 5
      family  RF Inflación  5
      family  Flexible Estratégico  4
      sector_focus  Tecnología e Innovación 163
      sector_focus  Energía y Recursos  62
      sector_focus  Salud y Ciencias de la Vida 47
      sector_focus  Materiales y Minería  41
      sector_focus  Utilities y Medio Ambiente  24
      sector_focus  Activos Reales  22
      sector_focus  Servicios Financieros 10
      sector_focus  Consumo 5
      subtype Fondo Indexado  73
      subtype Oportunista 41
      subtype Standard MMF  38
      subtype VNAV  20
      subtype ETF 16
      subtype Física / Derivados  16
      subtype LVNAV 12
      subtype Corta Duración  9
      subtype Autocallable  8
      subtype Floating Rate Notes 7
      subtype CNAV  5
      subtype Global Macro  5
      subtype Banda Fija 15 3
      subtype Banda Fija 50 3
      subtype Banda Fija 75 3
      subtype Objetivo de Volatilidad 3
      subtype Total Return Bond 2
      subtype Inmobiliario  1
      subtype Long/Short  1
      subtype Valor Relativo / Arbitraje  1
      type  Gestión Activa  1584
      type  Allocation  464
      type  Renta Fija Flexible 455
      type  Renta Fija Corto Plazo  353
      type  Monetario 94
      type  Indexado  89
      type  Crédito CP  59
      type  Absolute Return 46
      type  Materias Primas 16
      type  Estructurado  8
      type  Floating Rate CP  7
      type  Gobierno CP 6
      type  Target Maturity 6
      type  Tactical Allocation 4
      type  Total Return  4
      type  Monetario Privado 3
      type  Deuda Pública CP  2
      type  Monetario Público 2
      type  Activos Reales  1
      type  Objetivo de Volatilidad 1



-- ── BL-53/54: Sector_Focus debe estar 100% en español ────────────────────── CORREGIDO
SELECT Sector_Focus, COUNT(*) AS n
FROM fund_master
WHERE Sector_Focus IN (
    'Real Assets', 'Energy & Resources', 'Utilities & Environment',
    'Healthcare & Life Sciences', 'Technology & Innovation',
    'Materials & Mining', 'Financials & Insurance', 'Consumer Discretionary'
)
GROUP BY Sector_Focus;
-- Objetivo: 0 filas

-- ── BL-55: Exit_Fee_Pct y distribución de Fee_Known_Flag ──────────────────── Incorrecto 673 en Exit_Fee_Pct IS NULL
SELECT COUNT(*) AS exit_null FROM fund_master WHERE Exit_Fee_Pct IS NULL;
-- Objetivo: bajada desde 676 al rango 200-350

--- No aparece ni EXIT_INFERRED_ZERO ni EXIT_EXPLICIT_ZERO
SELECT Fee_Known_Flag, COUNT(*) AS n FROM fund_master
GROUP BY Fee_Known_Flag ORDER BY 2 DESC;

  ZERO_CONFIRMED  1669
  EXTRACTED 1420
  NOT_FOUND 115


-- ── BL-56: Auditoría lingüística general post-normalización ───────────────── ->  INCORRECTO en Family y Type
SELECT 'family' AS col, Family AS val, COUNT(*) AS n
FROM fund_master WHERE Family IS NOT NULL GROUP BY Family
UNION ALL
SELECT 'type', Type, COUNT(*) FROM fund_master WHERE Type IS NOT NULL GROUP BY Type
UNION ALL
SELECT 'sector_focus', Sector_Focus, COUNT(*) FROM fund_master WHERE Sector_Focus IS NOT NULL GROUP BY Sector_Focus
ORDER BY 1, 3 DESC;


col val n
family  RV Core 1.455
family  Renta Fija Corto Plazo  427
family  Renta Fija Flexible 415
family  Mixtos  365
family  RV Temática 218
family  Income Oriented 104
family  Monetario 99
family  Retorno Absoluto  43
family  RF High Yield 39
family  Activos Reales  17
family  Estructurado  8
family  RF Emergentes 5
family  RF Inflación  5
family  Flexible Estratégico  4
sector_focus  Tecnología e Innovación 163
sector_focus  Energía y Recursos  56
sector_focus  Salud y Ciencias de la Vida 46
sector_focus  Materiales y Minería  41
sector_focus  Utilities y Medio Ambiente  22
sector_focus  Activos Reales  11
sector_focus  Real Assets 11
sector_focus  Servicios Financieros 10
sector_focus  Energy & Resources  6
sector_focus  Consumo 5
sector_focus  Utilities & Environment 2
sector_focus  Healthcare & Life Sciences  1
type  Gestión Activa  1.584
type  Allocation  464
type  Renta Fija Flexible 455
type  Renta Fija Corto Plazo  353
type  Monetario 94
type  Indexado  89
type  Crédito CP  59
type  Absolute Return 46
type  Materias Primas 16
type  Estructurado  8
type  Floating Rate CP  7
type  Gobierno CP 6
type  Target Maturity 6
type  Tactical Allocation 4
type  Total Return  4
type  Monetario Privado 3
type  Deuda Pública CP  2
type  Monetario Público 2
type  Activos Reales  1
type  Objetivo de Volatilidad 1
-- ── BL-57: Family='Income Oriented' sin cambio (excepción documentada) ────── -> INCORRECTO ->104
SELECT Fund_Nature, COUNT(*) FROM fund_master
WHERE Family = 'Income Oriented' GROUP BY Fund_Nature ORDER BY 2 DESC;
-- Objetivo: distribución estable respecto a v3.2 (~104 fondos)  



-----

-- BL-49: 170 → 136
SELECT COUNT(*) FROM fund_master
WHERE Fund_Currency IN ('USD','GBP','CHF','JPY','CNH') AND Currency_Hedged IS NULL;

-- BL-50: 7 → 0
SELECT Geography, COUNT(*) FROM fund_master WHERE Investment_Universe IS NULL AND Geography IS NOT NULL GROUP BY Geography;

-- BL-53/56: 0 filas
SELECT Sector_Focus, COUNT(*) FROM fund_master
WHERE Sector_Focus IN ('Real Assets','Energy & Resources','Utilities & Environment',
  'Healthcare & Life Sciences','Technology & Innovation','Materials & Mining',
  'Financials & Insurance','Consumer Discretionary') GROUP BY Sector_Focus;

-- BL-57: 0 filas
SELECT COUNT(*) FROM fund_master WHERE Family = 'Income Oriented';

-- BL-57 verificación positiva: 0 Filas
SELECT COUNT(*) FROM fund_master WHERE Family = 'Orientado a Renta';






-- 1. ¿Cuántos fondos tienen Family NULL o vacío? -> 0
SELECT COUNT(*) FROM fund_master 
WHERE Family IS NULL OR Family = '';
-- Si ~104 más que el ciclo anterior → Hipótesis 3

-- 2. Distribución completa de Family (encontrar dónde están los 104)
SELECT Family, COUNT(*) AS n FROM fund_master 
GROUP BY Family ORDER BY n DESC;
-- Buscar (a) un Family con +104 vs ciclo anterior → Hipótesis 1
--        (b) un literal nuevo no esperado → Hipótesis 2

-- 3. Identificar nominalmente los fondos que ANTES eran Income Oriented
-- (asumiendo que conservas p1_export_20260423.xlsx como referencia)
-- Cruzar ISINs de aquellos 104 contra el estado actual y ver qué Family tienen ahora
SELECT Family, COUNT(*) FROM fund_master 
WHERE ISIN IN (<lista de los 104 ISINs del export del 23-abr>)
GROUP BY Family;



-- BL-49 — Currency_Hedged: extensión de extracción al texto KIID -> INCORRECTO 170
  -- Cuantificación del gap
  SELECT COUNT(*) FROM fund_master  WHERE Fund_Currency IN ('USD','GBP','CHF','JPY','CNH')  AND Currency_Hedged IS NULL;
  -- Resultado actual: 267

-- BL-50 — Nulos cruzados Investment_Universe / Geography (inferencia INTER)** -> INCORRECTO 7
-- Geography poblado pero Universe NULL
     SELECT Geography, COUNT(*) FROM fund_master WHERE Investment_Universe IS NULL AND Geography IS NOT NULL   GROUP BY Geography ORDER BY 2 DESC;

-- BL-51 — Comisiones con estructura mixta: porcentaje + tope fijo -> INCORRECTO 673 CON  Entry_Fee_Pct NULL 
-- BL-51 — Comisiones con estructura mixta: porcentaje + tope fijo -> INCORRECTO 110 CON  Entry_Fee_Pct NULL Y FEE_KNOWN_FLAG NOT_FOUND
-- BL-53 — Inconsistencias lingüísticas intra-atributo: valores en inglés en columnas con idioma objetivo español -> INCORRECTO

      SELECT 'family' AS col, Family AS val, COUNT(*) AS n FROM fund_master WHERE Family IS NOT NULL GROUP BY Family
      UNION ALL
      SELECT 'type', Type, COUNT(*) FROM fund_master WHERE Type IS NOT NULL GROUP BY Type
      UNION ALL
      SELECT 'subtype', Subtype, COUNT(*) FROM fund_master WHERE Subtype IS NOT NULL GROUP BY Subtype
      UNION ALL
      SELECT 'sector_focus', Sector_Focus, COUNT(*) FROM fund_master WHERE Sector_Focus IS NOT NULL GROUP BY Sector_Focus
      ORDER BY 1, 3 DESC;


-- ── BL-53/54: Sector_Focus debe estar 100% en español ────────────────────── CORREGIDO
SELECT Sector_Focus, COUNT(*) AS n
FROM fund_master
WHERE Sector_Focus IN (
    'Real Assets', 'Energy & Resources', 'Utilities & Environment',
    'Healthcare & Life Sciences', 'Technology & Innovation',
    'Materials & Mining', 'Financials & Insurance', 'Consumer Discretionary'
)
GROUP BY Sector_Focus;
-- Objetivo: 0 filas

-- ── BL-55: Exit_Fee_Pct y distribución de Fee_Known_Flag ──────────────────── Incorrecto 673 en Exit_Fee_Pct IS NULL
SELECT COUNT(*) AS exit_null FROM fund_master WHERE Exit_Fee_Pct IS NULL;
-- Objetivo: bajada desde 676 al rango 200-350

--- No aparece ni EXIT_INFERRED_ZERO ni EXIT_EXPLICIT_ZERO
SELECT Fee_Known_Flag, COUNT(*) AS n FROM fund_master
GROUP BY Fee_Known_Flag ORDER BY 2 DESC;

  ZERO_CONFIRMED  1669
  EXTRACTED 1420
  NOT_FOUND 115


-- ── BL-56: Auditoría lingüística general post-normalización ───────────────── ->  INCORRECTO en Family y Type
SELECT 'family' AS col, Family AS val, COUNT(*) AS n
FROM fund_master WHERE Family IS NOT NULL GROUP BY Family
UNION ALL
SELECT 'type', Type, COUNT(*) FROM fund_master WHERE Type IS NOT NULL GROUP BY Type
UNION ALL
SELECT 'sector_focus', Sector_Focus, COUNT(*) FROM fund_master WHERE Sector_Focus IS NOT NULL GROUP BY Sector_Focus
ORDER BY 1, 3 DESC;


-- ── BL-57: Family='Income Oriented' sin cambio (excepción documentada) ────── -> INCORRECTO ->104
SELECT Fund_Nature, COUNT(*) FROM fund_master
WHERE Family = 'Income Oriented' GROUP BY Fund_Nature ORDER BY 2 DESC;
-- Objetivo: distribución estable respecto a v3.2 (~104 fondos)  
