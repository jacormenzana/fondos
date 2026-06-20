--=======================================================================================
--
-- Filtros duros (Gatekeepers)
--
--=======================================================================================

	--=======================================================================================
	--í´¹ Filtro 1 â€” DaÃ±o mÃ¡ximo real
	--
	-- Objetivo
	-- 		Excluir fondos que destruyen capital de forma profunda, aunque luego â€œrecuperenâ€.
	-- 
	-- Criterio
	--		max_drawdown_real â‰¥ -30%
	-- 
	-- InterpretaciÃ³n:
	-- 		-30% â†’ daÃ±o potencialmente recuperable
	-- 		< -30% â†’ pÃ©rdida psicolÃ³gica y financiera inaceptable para nÃºcleo
	-- 
	--=======================================================================================

	SELECT isin
	FROM fund_metrics
	WHERE metric = 'max_drawdown'
	  AND real_flag = 1
	  AND horizon = 'since_inception'
	  AND value >= -0.30;

	--=======================================================================================
	-- Filtro 2 â€” Regularidad mÃ­nima real
	--
	-- Objetivo
	-- 		Excluir fondos que ganan â€œa saltosâ€ pero erosionan en el dÃ­a a dÃ­a.
	-- 
	-- Criterio
	-- 		pct_positive_months_real â‰¥ 0.50
	-- 
	-- InterpretaciÃ³n:
	-- 		â‰¥ 50% â†’ mayorÃ­a de meses preservan poder adquisitivo
	-- 		< 50% â†’ fondo estructuralmente erosivo
	--=======================================================================================


	SELECT isin
	FROM fund_metrics
	WHERE metric = 'pct_positive_months'
	  AND real_flag = 1
	  AND horizon = 'since_inception'
	  AND value >= 0.50;



	--=======================================================================================
	--  Filtro 3 â€” Severidad de pÃ©rdidas recurrentes
	--
	-- Objetivo
	-- 		Excluir fondos con caÃ­das frecuentes y violentas, aunque el drawdown agregado no sea extremo.
	--
	-- Criterio
	-- 		pct_severe_loss_months_real â‰¤ 0.15
	-- 		(pÃ©rdidas mensuales â‰¤ -2%)
	-- 
	-- InterpretaciÃ³n:
	-- 		â‰¤ 15% â†’ episodios severos esporÃ¡dicos
	-- 		15% â†’ inestabilidad estructural
	--=======================================================================================


	SELECT isin
	FROM fund_metrics
	WHERE metric = 'pct_severe_loss_months'
	  AND real_flag = 1
	  AND horizon = 'since_inception'
	  AND value <= 0.15;


	--=======================================================================================
	--	 Filtro 4 â€” RecuperaciÃ³n razonable
	--
	--	Objetivo
	--		Excluir fondos que no se recuperan en horizontes temporales humanos.
	--
	--	Criterio
	--
	--		time_to_recovery_real â‰¤ 36 meses
	--		o recuperaciÃ³n inexistente â†’ excluido
	--
	--InterpretaciÃ³n:
	--		36 meses â†’ coste de oportunidad excesivo
	--		NaN â†’ colapso estructural
	--=======================================================================================  

	SELECT isin
	FROM fund_metrics
	WHERE metric = 'time_to_recovery'
	  AND real_flag = 1
	  AND horizon = 'since_inception'
	  AND value IS NOT NULL
	  AND value <= 36;




	--=======================================================================================
	--	Filtro 5 â€” Supervivencia en crisis
	--	
	--	Objetivo
	--		Excluir fondos que colapsan cuando el sistema entra en estrÃ©s real.
	--	
	--	Criterio base
	--		El fondo debe cumplir al menos 3 de 4 crisis analizadas
	--		En cada crisis:
	--			max_drawdown_real no extremo
	--			recuperaciÃ³n posterior existente
	--	
	--	Ejemplo SQL (crisis 2020):
	--=======================================================================================  

	SELECT isin
	FROM fund_metrics
	WHERE horizon = 'crisis_2020'
	  AND metric = 'max_drawdown'
	  AND real_flag = 1
	  AND value >= -0.35;


--=======================================================================================
--
--  Tipologias de Cartera
--
--=======================================================================================


	--=======================================================================================
	--	3.1 Filtros de daÃ±o mÃ¡ximo (max_drawdown_real)
	--	TipologÃ­a	Umbral
	--	Defensiva	â‰¥ -20%
	--	Equilibrada	â‰¥ -30%
	--	Crecimiento	â‰¥ -40%
	--	
	--	InterpretaciÃ³n:
	--		un drawdown del -35% es inaceptable en nÃºcleo defensivo,
	--		pero tolerable en crecimiento si se recupera.
	--=======================================================================================


	--=======================================================================================	
	--	3.2 Regularidad real (pct_positive_months_real)
	--	TipologÃ­a	Umbral
	--	Defensiva	â‰¥ 60%
	--	Equilibrada	â‰¥ 50%
	--	Crecimiento	â‰¥ 45%
	--	
	--	InterpretaciÃ³n:
	--		la defensiva exige â€œmeses tranquilosâ€,
	--		crecimiento acepta irregularidad.
	--=======================================================================================

	--=======================================================================================	
	--	3.3 PÃ©rdidas severas recurrentes (pct_severe_loss_months_real)
	--	TipologÃ­a	Umbral
	--	Defensiva	â‰¤ 10%
	--	Equilibrada	â‰¤ 15%
	--	Crecimiento	â‰¤ 20%
	--	
	--	InterpretaciÃ³n:
	--		defensiva penaliza violencia,
	--		crecimiento tolera golpes.
	--=======================================================================================


	--=======================================================================================
	--	3.4 RecuperaciÃ³n (time_to_recovery_real)
	--	TipologÃ­a	Umbral
	--	Defensiva	â‰¤ 18 meses
	--	Equilibrada	â‰¤ 36 meses
	--	Crecimiento	â‰¤ 60 meses
	--	
	--	InterpretaciÃ³n:
	--		el tiempo sÃ­ es riesgo,
	--		pero su tolerancia depende del rol.
	--=======================================================================================

	--=======================================================================================	
	--	3.5 Crisis (criterio mÃ­nimo)
	--	TipologÃ­a	Exigencia
	--	Defensiva	Superar 4/4 crisis
	--	Equilibrada	Superar 3/4 crisis
	--	Crecimiento	Superar 2/4 crisis
	--	â€œSuperarâ€ = no colapso irreversible + drawdown relativo aceptable.
	--=======================================================================================

	SELECT isin
	FROM fund_metrics
	WHERE horizon = 'since_inception'
	  AND real_flag = 1
	  AND (
	        (metric = 'max_drawdown' AND value >= -0.20) OR
	        (metric = 'pct_positive_months' AND value >= 0.60) OR
	        (metric = 'pct_severe_loss_months' AND value <= 0.10) OR
	        (metric = 'time_to_recovery' AND value <= 18)
	      );



--=======================================================================================
--
--  Scoring de preservaciÃ³n
--
--=======================================================================================


	--=======================================================================================	
	--	í´¹ A. DaÃ±o (Damage)
	--	
	--	MÃ©tricas:
	--		max_drawdown_real
	--		worst_month_real
	--	
	--	InterpretaciÃ³n:
	--		mide magnitud del daÃ±o, no frecuencia.
	--		damage_score =
  	--		0.7 Â· percentile(max_drawdown_real, invertido)
	--		+ 0.3 Â· percentile(worst_month_real, invertido)
	--=======================================================================================	


	--=======================================================================================	
	--	B. Regularidad (Consistency)
	--	
	--	MÃ©tricas:
	--		pct_positive_months_real
	--		pct_severe_loss_months_real
	--	
	--	InterpretaciÃ³n:
	--		mide experiencia del inversor en el tiempo,
	--		penaliza erosiÃ³n silenciosa.
	--	
	--	consistency_score =
  	--		0.6 Â· percentile(pct_positive_months_real)
	--		+ 0.4 Â· percentile(1 - pct_severe_loss_months_real)
	--=======================================================================================	


	--=======================================================================================	
	--	C. Resiliencia (Resilience)
	--	
	--	MÃ©tricas:
	--		time_to_recovery_real
	--		comportamiento agregado en crisis
	--	
	--	InterpretaciÃ³n:
	--		mide capacidad de absorber shocks y recomponerse.
	--	
	--	resilience_score =
	--		  0.6 Â· percentile(invertido(time_to_recovery_real))
	--		+ 0.4 Â· crisis_score
	--=======================================================================================	









SELECT pw.block, pw.isin, fm.Fund_Name, 
       fm.Fund_Nature, pw.weight
FROM portfolio_weights pw
JOIN fund_master fm ON fm.ISIN = pw.isin
WHERE pw.scenario_id = 'shock_energia_2026Q1'
ORDER BY pw.block, pw.weight DESC;

SELECT pw.isin, fm.Fund_Name, fm.Management_Company
FROM portfolio_weights pw
JOIN fund_master fm ON fm.ISIN = pw.isin
WHERE pw.scenario_id = 'shock_energia_2026Q1'
  AND pw.block = 'Equilibrada'
ORDER BY pw.weight DESC;





SELECT metric, ROUND(value,4) as value
FROM fund_metrics
WHERE isin = 'LU0957791311'
  AND metric IN ('beta_rate_eu','beta_rate_us','macro_r2','macro_n_obs',
                 'return_ann','max_drawdown','alpha_persistence')
  AND horizon = 'since_inception'
ORDER BY metric;



SELECT fs.isin, fm.Fund_Name, fs.score_total, fs.eligible
FROM fund_scores fs
JOIN fund_master fm ON fm.ISIN = fs.isin
WHERE fs.block = 'Defensiva'
  AND fm.Fund_Nature = 'Monetario'
  AND fs.eligible = 1
ORDER BY fs.score_total DESC
LIMIT 5;




-- Ver distribución de drawdown en Monetarios y RF Corto
SELECT fm.Fund_Nature,
       COUNT(*) as total,
       SUM(CASE WHEN dd.value >= -0.15 THEN 1 ELSE 0 END) as pasan_dd15,
       SUM(CASE WHEN ret.value >= 0 THEN 1 ELSE 0 END) as pasan_retorno,
       SUM(CASE WHEN srri.value <= 5 THEN 1 ELSE 0 END) as pasan_srri
FROM fund_master fm
LEFT JOIN fund_metrics dd  ON dd.isin=fm.ISIN  AND dd.metric='max_drawdown'
                           AND dd.horizon='since_inception' AND dd.real_flag=0
LEFT JOIN fund_metrics ret ON ret.isin=fm.ISIN AND ret.metric='return_ann'
                           AND ret.horizon='since_inception' AND ret.real_flag=1
LEFT JOIN fund_metrics srri ON srri.isin=fm.ISIN AND srri.metric='srri_nav'
                           AND srri.horizon='since_inception' AND srri.real_flag=0
WHERE fm.Fund_Nature IN ('Monetario','Renta Fija Corto Plazo','Renta Fija Flexible')
GROUP BY fm.Fund_Nature;



SELECT fm.Fund_Nature, COUNT(*) as elegibles,
       ROUND(AVG(score_total),4) as score_medio,
       ROUND(MAX(score_total),4) as score_max
FROM fund_scores fs
JOIN fund_master fm ON fm.ISIN = fs.isin
WHERE fs.block = 'Defensiva' AND fs.eligible = 1
GROUP BY fm.Fund_Nature
ORDER BY score_max DESC;




-- 1. ESTADÍSTICOS COMPLETOS
WITH medidas AS (
  SELECT
    LENGTH(Raw_KIID_Text)                   AS chars_total,
    LENGTH(REPLACE(Raw_KIID_Text, ' ', '')) AS chars_sin_espacios
  FROM fund_kiid_metadata
  WHERE Raw_KIID_Text IS NOT NULL
)
SELECT
    COUNT(*)                                 AS fondos,
    MIN(chars_total)                         AS min_chars,
    MAX(chars_total)                         AS max_chars,
    CAST(AVG(chars_total)        AS INTEGER) AS media_chars,
    CAST(AVG(chars_sin_espacios) AS INTEGER) AS media_sin_espacios,
    (SELECT chars_total FROM medidas ORDER BY chars_total LIMIT 1 OFFSET (SELECT COUNT(*)/100    FROM medidas)) AS p01,
    (SELECT chars_total FROM medidas ORDER BY chars_total LIMIT 1 OFFSET (SELECT COUNT(*)/10     FROM medidas)) AS p10,
    (SELECT chars_total FROM medidas ORDER BY chars_total LIMIT 1 OFFSET (SELECT COUNT(*)/4      FROM medidas)) AS p25,
    (SELECT chars_total FROM medidas ORDER BY chars_total LIMIT 1 OFFSET (SELECT COUNT(*)*50/100 FROM medidas)) AS p50_mediana,
    (SELECT chars_total FROM medidas ORDER BY chars_total LIMIT 1 OFFSET (SELECT COUNT(*)*75/100 FROM medidas)) AS p75,
    (SELECT chars_total FROM medidas ORDER BY chars_total LIMIT 1 OFFSET (SELECT COUNT(*)*90/100 FROM medidas)) AS p90,
    (SELECT chars_total FROM medidas ORDER BY chars_total LIMIT 1 OFFSET (SELECT COUNT(*)*99/100 FROM medidas)) AS p99
FROM medidas;