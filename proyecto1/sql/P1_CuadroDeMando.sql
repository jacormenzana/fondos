		
	---=====================================================================================
	---	CONTROL TRAS CARGA
	---=====================================================================================


		---=====================================================================================
		--- CATEGORIZACION SRRI 1 y 2
		---=====================================================================================

	SELECT fund_name, Heuristic_block, fund_Nature, SRRI,
	    
	    -- Primer booleano: 1 si CONTIENE ALGUNO de los include_patterns, 0 si no
	    CASE 
	        WHEN LOWER(fund_name) LIKE '%money market%'
	          OR LOWER(fund_name) LIKE '%monetary%'
	          OR LOWER(fund_name) LIKE '%cash fund%'
	          OR LOWER(fund_name) LIKE '%cash management%'
	          OR LOWER(fund_name) LIKE '%treasury%'
	          OR LOWER(fund_name) LIKE '%tresorerie%'
	          OR LOWER(fund_name) LIKE '%ucits mmf%'
	          OR LOWER(fund_name) LIKE '%mmf%'
	        THEN 1 
	        ELSE 0 
	    END AS contiene_include_pattern,

	    -- Segundo booleano: 1 si NO CONTIENE NINGUNO de los exclude_patterns, 0 si contiene alguno
	    CASE 
	        WHEN LOWER(fund_name) LIKE '%short duration%'
	          OR LOWER(fund_name) LIKE '%ultra short%'
	          OR LOWER(fund_name) LIKE '%short term%'
	          OR LOWER(fund_name) LIKE '%bond%'
	          OR LOWER(fund_name) LIKE '%income%'
	          OR LOWER(fund_name) LIKE '%enhanced%'
	          OR LOWER(fund_name) LIKE '%plus%'
	        THEN 0 -- Si encuentra alguna palabra prohibida, devuelve Falso (0)
	        ELSE 1 -- Si pasa limpio sin coincidir con ninguna, devuelve Verdadero (1)
	    END AS no_contiene_exclude_pattern

	FROM  fund_master where SRRI<=2;


	SELECT 
		management_company,
	    fund_name,
	    Heuristic_block,
	    fund_Nature,
	    benchmark_type,
	    benchmark_declared,
	    ma.SRRI,
	        -- Primer booleano: 1 si CONTIENE ALGUNO de los include_patterns, 0 si no
	    CASE 
	        WHEN LOWER(fund_name) LIKE '%money market%'
	          OR LOWER(fund_name) LIKE '%monetary%'
	          OR LOWER(fund_name) LIKE '%cash fund%'
	          OR LOWER(fund_name) LIKE '%cash management%'
	          OR LOWER(fund_name) LIKE '%treasury%'
	          OR LOWER(fund_name) LIKE '%tresorerie%'
	          OR LOWER(fund_name) LIKE '%ucits mmf%'
	          OR LOWER(fund_name) LIKE '%mmf%'
	        THEN 1 
	        ELSE 0 
	    END AS contiene_include_pattern,

	    -- Segundo booleano: 1 si NO CONTIENE NINGUNO de los exclude_patterns, 0 si contiene alguno
	    CASE 
	        WHEN LOWER(fund_name) LIKE '%short duration%'
	          OR LOWER(fund_name) LIKE '%ultra short%'
	          OR LOWER(fund_name) LIKE '%short term%'
	          OR LOWER(fund_name) LIKE '%bond%'
	          OR LOWER(fund_name) LIKE '%income%'
	          OR LOWER(fund_name) LIKE '%enhanced%'
	          OR LOWER(fund_name) LIKE '%plus%'
	        THEN 0 -- Si encuentra alguna palabra prohibida, devuelve Falso (0)
	        ELSE 1 -- Si pasa limpio sin coincidir con ninguna, devuelve Verdadero (1)
	    END AS no_contiene_exclude_pattern,
	    SUBSTR(Raw_KIID_Text, 1, 600) AS kiid_text_resumen

	FROM  fund_master ma
	inner join fund_kiid_metadata md on ma.isin=md.isin
	where ma.SRRI<=2
	and (lower(fund_name) like '%liq%');


		---=====================================================================================
		---	GENERAL
		---=====================================================================================


		-- 01. Contabilidad de fondos por Estado de KIID 
		(*)
		SELECT KIID_Status, COUNT(*) AS n, ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(), 2) AS porcentaje
		FROM fund_kiid_metadata
		GROUP BY KIID_Status
		ORDER BY n DESC;

		-- 01. Contailidaddes por  naturalezas de fondos
		(*)
		SELECT f.Fund_Nature,  km.KIID_Status, COUNT(*) AS fondos,
	    SUM(CASE WHEN f.SRRI IS NOT NULL THEN 1 ELSE 0 END) AS con_srri,	
	    SUM(CASE WHEN km.SRRI_Visual IS NOT NULL THEN 1 ELSE 0 END) AS con_visual,
	    SUM(CASE WHEN km.SRRI_Validation_Status = 'MATCH' THEN 1 ELSE 0 END) AS match,
	    SUM(CASE WHEN km.SRRI_Validation_Status = 'TEXT_ONLY' THEN 1 ELSE 0 END) AS text_only,
	    SUM(CASE WHEN km.SRRI_Validation_Status = 'NOT_AVAILABLE' THEN 1 ELSE 0 END) AS not_available
		FROM fund_master f
		JOIN fund_kiid_metadata km USING (ISIN)
		--WHERE f.Heuristic_Block = 'MIXTOS'
		GROUP BY f.Fund_Nature,km.KIID_Status
		ORDER BY fondos DESC;

		-- 01. Contailidaddes por Gestor de fondos

    	SELECT fm.Management_Company, fm.Fund_Nature, km.KIID_Status, count(fm.ISIN) as np
		FROM fund_master fm
		JOIN fund_kiid_metadata km ON fm.ISIN = km.ISIN AND km.KIID_Class = 1
		group by fm.Management_Company, fm.Fund_Nature, km.KIID_Status 
		ORDER BY fm.Management_Company, fm.Fund_Nature, km.KIID_Status ;

 	    SELECT fm.Management_Company, fm.Fund_Nature, count(fm.ISIN) as np
		FROM fund_master fm
		JOIN fund_kiid_metadata km ON fm.ISIN = km.ISIN AND km.KIID_Class = 1
		group by fm.Management_Company, fm.Fund_Nature 
		ORDER BY fm.Management_Company  asc , np desc;


		-- 01. Transformación de heurísticas a naturalezas de fondos
		(*)
		SELECT f.Heuristic_Block, f.Fund_Nature,  km.KIID_Status, COUNT(*) AS fondos,
	    SUM(CASE WHEN f.SRRI IS NOT NULL THEN 1 ELSE 0 END) AS con_srri,	
	    SUM(CASE WHEN km.SRRI_Visual IS NOT NULL THEN 1 ELSE 0 END) AS con_visual,
	    SUM(CASE WHEN km.SRRI_Validation_Status = 'MATCH' THEN 1 ELSE 0 END) AS match,
	    SUM(CASE WHEN km.SRRI_Validation_Status = 'TEXT_ONLY' THEN 1 ELSE 0 END) AS text_only,
	    SUM(CASE WHEN km.SRRI_Validation_Status = 'NOT_AVAILABLE' THEN 1 ELSE 0 END) AS not_available
		FROM fund_master f
		JOIN fund_kiid_metadata km USING (ISIN)
		--WHERE f.Heuristic_Block = 'MIXTOS'
		GROUP BY f.Heuristic_Block, f.Fund_Nature,km.KIID_Status
		ORDER BY fondos DESC;



		---=====================================================================================
		---	SRRI
		---=====================================================================================

		(*)
		-- Contablidad general por estado de validación del SRRI
		SELECT 
		    COUNT(*) AS total,
		    SUM(CASE WHEN km.SRRI_Visual IS NOT NULL THEN 1 ELSE 0 END) AS con_visual,
		    SUM(CASE WHEN km.SRRI_Validation_Status = 'MATCH' THEN 1 ELSE 0 END) AS match,
		    SUM(CASE WHEN km.SRRI_Validation_Status = 'TEXT_ONLY' THEN 1 ELSE 0 END) AS text_only,
		    SUM(CASE WHEN km.SRRI_Validation_Status = 'CONFLICT' THEN 1 ELSE 0 END) AS conflict,		    
		    SUM(CASE WHEN km.SRRI_Validation_Status = 'NOT_AVAILABLE' THEN 1 ELSE 0 END) AS not_available
		FROM fund_kiid_metadata km
		WHERE km.KIID_Class = 1;

		-- Contabilidad de fondos segmentada por SRRI_Validation_Status
		SELECT SRRI_Validation_Status, COUNT(*) as n, ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(), 2) AS porcentaje
		FROM fund_kiid_metadata WHERE KIID_Class=1
		GROUP BY SRRI_Validation_Status ORDER BY 2 DESC;

		-- Contabilidad de fondos segmentada por KIID_Class
		SELECT KIID_Class, COUNT(*) 
		FROM fund_kiid_metadata ;


			---=====================================================================================
			---	SRRI No informado
			---=====================================================================================

			-- Listado detallado de fondos sin SRRI informado
			SELECT fm.ISIN, fm.Fund_Name, fm.Management_Company, fm.Fund_Nature,
			       km.KIID_Status, km.SRRI_Visual, km.SRRI_Textual, km.SRRI_Validation_Status
			FROM fund_master fm
			JOIN fund_kiid_metadata km ON fm.ISIN = km.ISIN AND km.KIID_Class = 1
			WHERE fm.SRRI IS NULL
			ORDER BY km.KIID_Status, fm.Management_Company;


			---=====================================================================================
			---	SRRI Textual y visual con distintos valores informados
			---=====================================================================================

			-- QUERY 1: Distribución de los 64 conflictos por par visual/textual
			SELECT 
			    km.SRRI_Visual,
			    km.SRRI_Textual,
			    COUNT(*) AS n,
			    GROUP_CONCAT(DISTINCT fm.Fund_Nature) AS natures,
			    GROUP_CONCAT(DISTINCT fm.Management_Company) AS gestoras
			FROM fund_kiid_metadata km
			JOIN fund_master fm ON km.ISIN = fm.ISIN
			WHERE km.SRRI_Validation_Status = 'CONFLICT'
			  AND km.KIID_Class = 1
			GROUP BY km.SRRI_Visual, km.SRRI_Textual
			ORDER BY n DESC;


			-- QUERY 2: Fondos en CONFLICT con detalle
			SELECT 
			    km.ISIN,
			    fm.Fund_Name,
			    fm.Management_Company,
			    fm.Fund_Nature,
			    km.SRRI_Visual,
			    km.SRRI_Textual,
			    km.KIID_Status,
			    km.KIID_Downloaded_At
			FROM fund_kiid_metadata km
			JOIN fund_master fm ON km.ISIN = fm.ISIN
			WHERE km.SRRI_Validation_Status = 'CONFLICT'
			  AND km.KIID_Class = 1
			ORDER BY fm.Management_Company, km.SRRI_Visual, km.SRRI_Textual;



			-- QUERY 3: Fondos con SRRI_Visual y SRRI_Textual ambos presentes 
			-- pero que tienen KIID_Status=CACHED (no han tenido descarga real reciente)
			-- → estos son los candidatos a conflictos generados por srri_textual_prev
			SELECT 
			    km.SRRI_Visual,
			    km.SRRI_Textual,
			    COUNT(*) AS n
			FROM fund_kiid_metadata km
			WHERE km.SRRI_Validation_Status = 'CONFLICT'
			  AND km.KIID_Class = 1
			  AND km.KIID_Status = 'CACHED'
			GROUP BY km.SRRI_Visual, km.SRRI_Textual
			ORDER BY n DESC;



		---=====================================================================================
		---	Restantes
		---=====================================================================================

		-- Poblaciópn de frondo que no salen de Restantes (para análisis de señales faltantes)
		SELECT Fund_Name, Management_Company, Benchmark_Declared
		FROM fund_master
		WHERE Fund_Nature = 'Restantes'
		and Heuristic_Block ="RESTANTES"
		ORDER BY Management_Company, Fund_Name;



		-- Restantes detallados
		SELECT Fund_Name, Management_Company, SRRI, Benchmark_Declared, 
		       Fund_Currency, Inference_Trace
		FROM fund_master 
		WHERE Fund_Nature = 'Restantes'
		ORDER BY Management_Company;



		---=====================================================================================
		---	Tiempo de procesamiento por fondo
		---=====================================================================================

	    CREATE VIEW v_lista_tiempo_procesamientoxfondo AS
		SELECT 
	    	m.Fund_Nature, 
	    	m.Management_Company, 
	        f.ISIN,
	        f.KIID_Status,
	        f.KIID_Downloaded_At, 
	        f.SRRI, 
	        f.SRRI_Visual, 
	        f.SRRI_Textual, 
	        f.SRRI_Validation_Status, 
	        (strftime('%s', LEAD(KIID_Downloaded_At) OVER (ORDER BY KIID_Downloaded_At ASC)) - strftime('%s', KIID_Downloaded_At) ) AS Dif_Seg 
	    FROM fund_kiid_metadata f
	    inner join fund_master m on f.isin=m.isin

	    (*)
		select 
			CASE WHEN Processing_Time_Ms< 1000 THEN '0-1'
				 WHEN Processing_Time_Ms>= 1000 AND Processing_Time_Ms < 2000 THEN '1-2'
				 WHEN Processing_Time_Ms>= 2000 AND Processing_Time_Ms < 5000 THEN '2-5'
				 WHEN Processing_Time_Ms>= 5000 AND Processing_Time_Ms < 10000 THEN '5-10'
				 WHEN Processing_Time_Ms>= 10000 AND Processing_Time_Ms < 20000 THEN '10-20'
				 WHEN Processing_Time_Ms>= 20000 THEN '>20'				 
				 WHEN Processing_Time_Ms IS NULL THEN 'NULO' 
				 ELSE 'RESTO'
			END AS Rango,
			COUNT(ISIN) as n,
			ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(), 2) as pct
		FROM fund_kiid_metadata 
		group by Rango
		order by  2 desc

	    ---- Listado de fondos con tiempo de proceso superior a 2 segundos.	
		select ISIN, KIID_Status, SRRI_Validation_Status, KIID_Downloaded_At , Processing_Time_Ms, Processing_Breakdown   
		FROM fund_kiid_metadata 
		where Processing_Time_Ms>2000 order by Processing_Time_Ms desc;




		select 
			CASE WHEN Processing_Time_Ms< 1000 THEN '0-1'
				 WHEN Processing_Time_Ms>= 1000 AND Processing_Time_Ms < 2000 THEN '1-2'
				 ELSE '>2'
			END AS Rango
		FROM fund_kiid_metadata 
		order by  1 asc

		---=====================================================================================
		---	Cobertura de atributos
		---=====================================================================================

		-- Cobertura completa de atributos
		
		(*)

		SELECT
		    COUNT(*) total,
		    SUM(CASE WHEN SRRI IS NOT NULL THEN 1 ELSE 0 END) srri,
		    SUM(CASE WHEN Profile IS NOT NULL THEN 1 ELSE 0 END) profile,
		    SUM(CASE WHEN Strategy  IS NOT NULL THEN 1 ELSE 0 END) strategy,		    
		    SUM(CASE WHEN Family IS NOT NULL THEN 1 ELSE 0 END) family,
		    SUM(CASE WHEN Geography IS NOT NULL THEN 1 ELSE 0 END) geoGRAPHY,
   		    SUM(CASE WHEN Investment_Universe  IS NOT NULL THEN 1 ELSE 0 END) investment_universe,
   		    SUM(CASE WHEN Investment_Focus  IS NOT NULL THEN 1 ELSE 0 END) investment_focus,   		    
		    SUM(CASE WHEN Type IS NOT NULL THEN 1 ELSE 0 END) type,
		    SUM(CASE WHEN Subtype  IS NOT NULL THEN 1 ELSE 0 END) subtype,		    
		    SUM(CASE WHEN Theme IS NOT NULL THEN 1 ELSE 0 END) theme,
		    SUM(CASE WHEN Style_Profile IS NOT NULL THEN 1 ELSE 0 END) style_profile,
		    SUM(CASE WHEN Sector_Focus IS NOT NULL THEN 1 ELSE 0 END) sector_focus,
		    SUM(CASE WHEN Fund_Currency  IS NOT NULL THEN 1 ELSE 0 END) fund_currency,
		    SUM(CASE WHEN Hedging_Policy   IS NOT NULL THEN 1 ELSE 0 END) hedging_policy,		    
		    SUM(CASE WHEN Currency_Hedged      IS NOT NULL THEN 1 ELSE 0 END) AS currency_hedged,		    
		    SUM(CASE WHEN Benchmark_Declared IS NOT NULL 
		        AND Benchmark_Declared != 'NO_BENCHMARK' THEN 1 ELSE 0 END) bench,
		    SUM(CASE WHEN Accumulation_Policy IS NOT NULL THEN 1 ELSE 0 END) accumulation_policy,
		    SUM(CASE WHEN Sfdr_Article IS NOT NULL THEN 1 ELSE 0 END) sfdr_article,
		    SUM(CASE WHEN Entry_Fee_Pct IS NOT NULL THEN 1 ELSE 0 END) entry_fee_pct,		    
		    SUM(CASE WHEN Ongoing_Charge IS NOT NULL THEN 1 ELSE 0 END) ongoing_charge,
		    SUM(CASE WHEN Exit_Fee_Pct IS NOT NULL THEN 1 ELSE 0 END) exit_fee,
		    SUM(CASE WHEN Leverage_Used IS NOT NULL THEN 1 ELSE 0 END) leverage,		    
		    SUM(CASE WHEN Market_Cap_Focus     IS NOT NULL THEN 1 ELSE 0 END) AS mkt_cap 
		FROM fund_master;


		WITH totals AS (
		    SELECT
		        COUNT(*) AS total,
		        -- Filled counts
		        SUM(CASE WHEN SRRI                IS NOT NULL THEN 1 ELSE 0 END) AS srri,
		        SUM(CASE WHEN Profile             IS NOT NULL THEN 1 ELSE 0 END) AS profile,
		        SUM(CASE WHEN Strategy            IS NOT NULL THEN 1 ELSE 0 END) AS strategy,
		        SUM(CASE WHEN Family              IS NOT NULL THEN 1 ELSE 0 END) AS family,
		        SUM(CASE WHEN Geography           IS NOT NULL THEN 1 ELSE 0 END) AS geography,
		        SUM(CASE WHEN Investment_Universe IS NOT NULL THEN 1 ELSE 0 END) AS investment_universe,
		        SUM(CASE WHEN Investment_Focus    IS NOT NULL THEN 1 ELSE 0 END) AS investment_focus,
		        SUM(CASE WHEN Type                IS NOT NULL THEN 1 ELSE 0 END) AS type,
		        SUM(CASE WHEN Subtype             IS NOT NULL THEN 1 ELSE 0 END) AS subtype,
		        SUM(CASE WHEN Theme               IS NOT NULL THEN 1 ELSE 0 END) AS theme,
		        SUM(CASE WHEN Style_Profile       IS NOT NULL THEN 1 ELSE 0 END) AS style_profile,
		        SUM(CASE WHEN Sector_Focus        IS NOT NULL THEN 1 ELSE 0 END) AS sector_focus,
		        SUM(CASE WHEN Fund_Currency       IS NOT NULL THEN 1 ELSE 0 END) AS fund_currency,
		        SUM(CASE WHEN Hedging_Policy      IS NOT NULL THEN 1 ELSE 0 END) AS hedging_policy,
		        SUM(CASE WHEN Currency_Hedged     IS NOT NULL THEN 1 ELSE 0 END) AS currency_hedged,
		        SUM(CASE WHEN Benchmark_Declared  IS NOT NULL
		                 AND Benchmark_Declared  != 'NO_BENCHMARK' THEN 1 ELSE 0 END) AS bench,
		        SUM(CASE WHEN Accumulation_Policy IS NOT NULL THEN 1 ELSE 0 END) AS accumulation_policy,
		        SUM(CASE WHEN Sfdr_Article        IS NOT NULL THEN 1 ELSE 0 END) AS sfdr_article,
		        SUM(CASE WHEN Entry_Fee_Pct       IS NOT NULL THEN 1 ELSE 0 END) AS entry_fee_pct,
		        SUM(CASE WHEN Ongoing_Charge      IS NOT NULL THEN 1 ELSE 0 END) AS ongoing_charge,
		        SUM(CASE WHEN Exit_Fee_Pct        IS NOT NULL THEN 1 ELSE 0 END) AS exit_fee_pct,
		        SUM(CASE WHEN Leverage_Used       IS NOT NULL THEN 1 ELSE 0 END) AS leverage_used,
		        SUM(CASE WHEN Market_Cap_Focus    IS NOT NULL THEN 1 ELSE 0 END) AS market_cap_focus
		    FROM fund_master
		),
		unpivoted AS (
		    SELECT 'srri'                AS attribute, srri                AS filled, total FROM totals UNION ALL
		    SELECT 'profile',               profile,                                  total FROM totals UNION ALL
		    SELECT 'strategy',              strategy,                                 total FROM totals UNION ALL
		    SELECT 'family',                family,                                   total FROM totals UNION ALL
		    SELECT 'geography',             geography,                                total FROM totals UNION ALL
		    SELECT 'investment_universe',   investment_universe,                      total FROM totals UNION ALL
		    SELECT 'investment_focus',      investment_focus,                         total FROM totals UNION ALL
		    SELECT 'type',                  type,                                     total FROM totals UNION ALL
		    SELECT 'subtype',               subtype,                                  total FROM totals UNION ALL
		    SELECT 'theme',                 theme,                                    total FROM totals UNION ALL
		    SELECT 'style_profile',         style_profile,                            total FROM totals UNION ALL
		    SELECT 'sector_focus',          sector_focus,                             total FROM totals UNION ALL
		    SELECT 'fund_currency',         fund_currency,                            total FROM totals UNION ALL
		    SELECT 'hedging_policy',        hedging_policy,                           total FROM totals UNION ALL
		    SELECT 'currency_hedged',       currency_hedged,                          total FROM totals UNION ALL
		    SELECT 'bench',                 bench,                                    total FROM totals UNION ALL
		    SELECT 'accumulation_policy',   accumulation_policy,                      total FROM totals UNION ALL
		    SELECT 'sfdr_article',          sfdr_article,                             total FROM totals UNION ALL
		    SELECT 'entry_fee_pct',         entry_fee_pct,                            total FROM totals UNION ALL
		    SELECT 'ongoing_charge',        ongoing_charge,                           total FROM totals UNION ALL
		    SELECT 'exit_fee_pct',          exit_fee_pct,                             total FROM totals UNION ALL
		    SELECT 'leverage_used',         leverage_used,                            total FROM totals UNION ALL
		    SELECT 'market_cap_focus',      market_cap_focus,                         total FROM totals
		)
		SELECT
		    attribute,
		    total,
		    filled,
		    (total - filled)                                       AS null_count,
		    ROUND((total - filled) * 100.0 / NULLIF(total, 0), 2) AS null_ratio_pct
		FROM unpivoted
		ORDER BY null_ratio_pct DESC;


		-- Cobertura completa atributos clave
		SELECT
		    COUNT(*) total,
		    SUM(CASE WHEN SRRI IS NOT NULL THEN 1 ELSE 0 END) srri,
		    SUM(CASE WHEN Profile IS NOT NULL THEN 1 ELSE 0 END) profile,
		    SUM(CASE WHEN Geography IS NOT NULL THEN 1 ELSE 0 END) geo,
		    SUM(CASE WHEN Theme IS NOT NULL THEN 1 ELSE 0 END) theme,
		    SUM(CASE WHEN Style_Profile IS NOT NULL THEN 1 ELSE 0 END) style,
		    SUM(CASE WHEN Benchmark_Declared IS NOT NULL 
		        AND Benchmark_Declared != 'NO_BENCHMARK' THEN 1 ELSE 0 END) bench,
		    SUM(CASE WHEN Investment_Universe IS NOT NULL THEN 1 ELSE 0 END) inv_univ,
		    SUM(CASE WHEN Accumulation_Policy IS NOT NULL THEN 1 ELSE 0 END) acc_pol,
		    SUM(CASE WHEN Sfdr_Article IS NOT NULL THEN 1 ELSE 0 END) sfdr,
		    SUM(CASE WHEN Ongoing_Charge IS NOT NULL THEN 1 ELSE 0 END) ter,
		    SUM(CASE WHEN Leverage_Used IS NOT NULL THEN 1 ELSE 0 END) leverage,
		    SUM(CASE WHEN Exit_Fee_Pct IS NOT NULL THEN 1 ELSE 0 END) exit_fee
		FROM fund_master;

		--==================================================================================================
		-- Tema: Distribución por Tema
		--==================================================================================================

			SELECT Theme, COUNT(*) AS n, ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(), 2) AS porcentaje
			FROM fund_master
			--WHERE Theme IS NOT NULL
			GROUP BY Theme ORDER BY 2 DESC;




			--==================================================================================================
			-- Tema: Segmentacion por Fund_Nature
			--==================================================================================================

			--- Tema segmentado pro Fund_Nature
			SELECT 
			    Theme, 
			    Fund_Nature, 
			    COUNT(*) AS n, 
			    -- Porcentaje respecto al total global de la tabla
			    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(), 2) AS porcentaje_global,
			    -- Porcentaje relativo de Fund_Nature dentro de su respectivo Theme
			    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(PARTITION BY Theme), 2) AS porcentaje_por_theme
			FROM fund_master
			-- WHERE Theme IS NOT NULL
			GROUP BY Theme, Fund_Nature   
			ORDER BY Theme, porcentaje_por_theme DESC;	

			--- Fund_Nature segmentado por Tema
			SELECT 
			    Fund_Nature, 			
			    Theme, 
			    COUNT(*) AS n, 
			    -- Porcentaje respecto al total global de la tabla
			    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(), 2) AS porcentaje_global,
			    -- Porcentaje relativo de Fund_Nature dentro de su respectivo Theme
			    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(PARTITION BY Fund_Nature), 2) AS porcentaje_por_fund_nature
			FROM fund_master
			-- WHERE Theme IS NOT NULL
			GROUP BY Fund_Nature, Theme   
			ORDER BY Fund_Nature, porcentaje_por_fund_nature DESC;	

			--- Peso relativo de fondos sin tema informado, segmentado por Fund_Nature 
			with s as (
				SELECT 
				    Fund_Nature, 			
				    Theme, 
				    COUNT(*) AS n, 
				    -- Porcentaje respecto al total global de la tabla
				    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(), 2) AS porcentaje_global,
				    -- Porcentaje relativo de Fund_Nature dentro de su respectivo Theme
				    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(PARTITION BY Fund_Nature), 2) AS porcentaje_por_fund_nature
				FROM fund_master
				-- WHERE Theme IS NOT NULL
				GROUP BY Fund_Nature, Theme   
				ORDER BY Fund_Nature, porcentaje_por_fund_nature DESC	
			) select *
			from s
			where Theme is NULL
			order by Fund_Nature asc;
				
			--==================================================================================================
			-- Tema: Segmentacion por Investment_Universe
			--==================================================================================================

			--- Tema segmentado por Investment_Universe
			SELECT 
			    Theme, 
			    Investment_Universe , 
			    COUNT(*) AS n, 
			    -- Porcentaje respecto al total global de la tabla
			    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(), 2) AS porcentaje_global,
			    -- Porcentaje relativo de Fund_Nature dentro de su respectivo Theme
			    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(PARTITION BY Theme), 2) AS porcentaje_por_theme
			FROM fund_master
			-- WHERE Theme IS NOT NULL
			GROUP BY Theme, Investment_Universe   
			ORDER BY Theme, porcentaje_por_theme DESC;		

			--- Investment_Universe segmentado por  Tema
			SELECT 
			    Investment_Universe, 			
			    Theme, 
			    COUNT(*) AS n, 
			    -- Porcentaje respecto al total global de la tabla
			    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(), 2) AS porcentaje_global,
			    -- Porcentaje relativo de Investment_Universe dentro de su respectivo Theme
			    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(PARTITION BY Investment_Universe), 2) AS porcentaje_por_Investment_Universe
			FROM fund_master
			-- WHERE Theme IS NOT NULL
			GROUP BY Investment_Universe, Theme   
			ORDER BY Investment_Universe, porcentaje_por_Investment_Universe DESC;	

			--- Peso relativo de fondos sin tema informado, segmentado por Investment_Universe

			with s as (
				SELECT 
				    Investment_Universe, 			
				    Theme, 
				    COUNT(*) AS n, 
				    -- Porcentaje respecto al total global de la tabla
				    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(), 2) AS porcentaje_global,
				    -- Porcentaje relativo de Investment_Universe dentro de su respectivo Theme
				    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(PARTITION BY Investment_Universe), 2) AS porcentaje_por_Investment_Universe
				FROM fund_master
				-- WHERE Theme IS NOT NULL
				GROUP BY Investment_Universe, Theme   
				ORDER BY Investment_Universe, porcentaje_por_Investment_Universe DESC
			) select *
			from s
			where Theme is NULL
			order by Investment_Universe asc;




		--==================================================================================================
		-- Style_Profile
		--==================================================================================================

		-- Investment_Universe: Distribución por Investment_Universe
		SELECT Style_Profile, COUNT(*) AS n, ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(), 2) AS porcentaje
		FROM fund_master
		GROUP BY Style_Profile ORDER BY 2 DESC;


			SELECT 
			    Style_Profile, 
			    Fund_Nature , 
			    COUNT(*) AS n, 
			    -- Porcentaje respecto al total global de la tabla
			    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(), 2) AS porcentaje_global,
			    -- Porcentaje relativo de Fund_Nature dentro de su respectivo Style_Profile
			    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(PARTITION BY Style_Profile), 2) AS porcentaje_por_Style_Profile
			FROM fund_master
			-- WHERE Style_Profile IS NOT NULL
			GROUP BY Style_Profile, Fund_Nature   
			ORDER BY Style_Profile, porcentaje_por_Style_Profile DESC;


			SELECT 
			    Fund_Nature, 			
			    Style_Profile, 
			    COUNT(*) AS n, 
			    -- Porcentaje respecto al total global de la tabla
			    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(), 2) AS porcentaje_global,
			    -- Porcentaje relativo de Fund_Nature dentro de su respectivo Style_Profile
			    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(PARTITION BY Fund_Nature), 2) AS porcentaje_por_Fund_Nature
			FROM fund_master
			-- WHERE Style_Profile IS NOT NULL
			GROUP BY Fund_Nature, Style_Profile   
			ORDER BY Fund_Nature, porcentaje_por_Fund_Nature DESC;	
		--==================================================================================================
		-- Investment_Universe
		--==================================================================================================

		-- Investment_Universe: Distribución por Investment_Universe
		SELECT Investment_Universe, COUNT(*) AS n, ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(), 2) AS porcentaje
		FROM fund_master
		GROUP BY Investment_Universe ORDER BY 2 DESC;


		--==================================================================================================
		-- Fund_Nature
		--==================================================================================================

		-- Fund_Nature: Distribución por Fund_nature
		SELECT Fund_Nature, COUNT(*) 
		FROM fund_master 
		GROUP BY Fund_Nature ORDER BY 2 DESC;


		--==================================================================================================
		-- Accumulation_Policy
		--==================================================================================================
		-- Accumulation_Policy: mejorado (objetivo: >1500, era 510)
		SELECT Accumulation_Policy, COUNT(*) 
		FROM fund_master
		GROUP BY Accumulation_Policy;



		--==================================================================================================
		-- Currency
		--==================================================================================================

			--==================================================================================================
			-- Fund_Currency
			--==================================================================================================

			SELECT Fund_Currency , COUNT(*) AS n, ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(), 2) AS porcentaje
			FROM fund_master
			GROUP BY Fund_Currency ORDER BY 2 DESC;
			ORDER BY 2 DESC;



			--==================================================================================================
			-- Portfolio_Currency
			--==================================================================================================

			SELECT Portfolio_Currency , COUNT(*) AS n, ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(), 2) AS porcentaje
			FROM fund_master
			GROUP BY Portfolio_Currency ORDER BY 2 DESC;
			ORDER BY 2 DESC;


		--==================================================================================================
		-- Comisiones
		--==================================================================================================

			SELECT "Entry_Fee_Pct " as Attribute, CASE WHEN Entry_Fee_Pct is not null then "INFORMADO" ELSE "NO_INFORMADO" END as Estado, MAX(Entry_Fee_Pct) as max, MIN(Entry_Fee_Pct) as min, AVG(Entry_Fee_Pct) as avg, COUNT(*) AS n, ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(), 2) AS porcentaje
			FROM fund_master
			GROUP BY Attribute, Estado 
			UNION ALL
			SELECT "Ongoing_Charge " as Attribute, CASE WHEN Ongoing_Charge is not null then "INFORMADO" ELSE "NO_INFORMADO" END as Estado, MAX(Ongoing_Charge ) as max, MIN(Ongoing_Charge ) as min, AVG(Ongoing_Charge ) as avg, COUNT(*) AS n, ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(), 2) AS porcentaje
			FROM fund_master
			GROUP BY Attribute, Estado 
			UNION ALL
			SELECT "Exit_Fee_Pct " as Attribute, CASE WHEN Exit_Fee_Pct is not null then "INFORMADO" ELSE "NO_INFORMADO" END as Estado, MAX(Exit_Fee_Pct ) as max, MIN(Exit_Fee_Pct ) as min, AVG(Exit_Fee_Pct ) as avg, COUNT(*) AS n, ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(), 2) AS porcentaje
			FROM fund_master
			GROUP BY Attribute, Estado 
			ORDER BY 1 ASC, 2 DESC;



			--==================================================================================================
			-- Ongoing_Charge
			--==================================================================================================

			SELECT CASE WHEN Ongoing_Charge is not null then "INFORMADO" ELSE "NO_INFORMADO" END as Estado, MAX(Ongoing_Charge ) as max, MIN(Ongoing_Charge ) as min, AVG(Ongoing_Charge ) as avg, COUNT(*) AS n, ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(), 2) AS porcentaje
			FROM fund_master
			GROUP BY Estado ORDER BY 1 ASC
			ORDER BY 2 DESC;

			--==================================================================================================
			-- Entry_Fee_Pct
			--==================================================================================================

			SELECT CASE WHEN Entry_Fee_Pct is not null then "INFORMADO" ELSE "NO_INFORMADO" END as Estado, MAX(Entry_Fee_Pct ) as max, MIN(Entry_Fee_Pct ) as min, AVG(Entry_Fee_Pct ) as avg, COUNT(*) AS n, ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(), 2) AS porcentaje
			FROM fund_master
			GROUP BY Estado ORDER BY 1 ASC
			ORDER BY 2 DESC;


			--==================================================================================================
			-- Exit_Fee_Pct
			--==================================================================================================


			SELECT CASE WHEN Exit_Fee_Pct is not null then "INFORMADO" ELSE "NO_INFORMADO" END as Estado, MAX(Exit_Fee_Pct ) as max, MIN(Exit_Fee_Pct ) as min, AVG(Exit_Fee_Pct ) as avg, COUNT(*) AS n, ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(), 2) AS porcentaje
			FROM fund_master
			GROUP BY Estado ORDER BY 1 ASC
			ORDER BY 2 DESC;


		-- 6. R-CO y DWS resueltos
		SELECT Fund_Name, SRRI, Fund_Nature, Inference_Trace
		FROM fund_master
		WHERE Fund_Name IN ('R-CO CONV CREDI EURO I EUR ACC','DWS GLBL INFRASTR FCH USDH ACC');