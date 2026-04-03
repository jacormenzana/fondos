--=======================================================================================
--
-- Filtros duros (Gatekeepers)
--
--=======================================================================================

	--=======================================================================================
	--��� Filtro 1 — Daño máximo real
	--
	-- Objetivo
	-- 		Excluir fondos que destruyen capital de forma profunda, aunque luego “recuperen”.
	-- 
	-- Criterio
	--		max_drawdown_real ≥ -30%
	-- 
	-- Interpretación:
	-- 		-30% → daño potencialmente recuperable
	-- 		< -30% → pérdida psicológica y financiera inaceptable para núcleo
	-- 
	--=======================================================================================

	SELECT isin
	FROM fund_metrics
	WHERE metric = 'max_drawdown'
	  AND real_flag = 1
	  AND horizon = 'since_inception'
	  AND value >= -0.30;

	--=======================================================================================
	-- Filtro 2 — Regularidad mínima real
	--
	-- Objetivo
	-- 		Excluir fondos que ganan “a saltos” pero erosionan en el día a día.
	-- 
	-- Criterio
	-- 		pct_positive_months_real ≥ 0.50
	-- 
	-- Interpretación:
	-- 		≥ 50% → mayoría de meses preservan poder adquisitivo
	-- 		< 50% → fondo estructuralmente erosivo
	--=======================================================================================


	SELECT isin
	FROM fund_metrics
	WHERE metric = 'pct_positive_months'
	  AND real_flag = 1
	  AND horizon = 'since_inception'
	  AND value >= 0.50;



	--=======================================================================================
	--  Filtro 3 — Severidad de pérdidas recurrentes
	--
	-- Objetivo
	-- 		Excluir fondos con caídas frecuentes y violentas, aunque el drawdown agregado no sea extremo.
	--
	-- Criterio
	-- 		pct_severe_loss_months_real ≤ 0.15
	-- 		(pérdidas mensuales ≤ -2%)
	-- 
	-- Interpretación:
	-- 		≤ 15% → episodios severos esporádicos
	-- 		15% → inestabilidad estructural
	--=======================================================================================


	SELECT isin
	FROM fund_metrics
	WHERE metric = 'pct_severe_loss_months'
	  AND real_flag = 1
	  AND horizon = 'since_inception'
	  AND value <= 0.15;


	--=======================================================================================
	--	 Filtro 4 — Recuperación razonable
	--
	--	Objetivo
	--		Excluir fondos que no se recuperan en horizontes temporales humanos.
	--
	--	Criterio
	--
	--		time_to_recovery_real ≤ 36 meses
	--		o recuperación inexistente → excluido
	--
	--Interpretación:
	--		36 meses → coste de oportunidad excesivo
	--		NaN → colapso estructural
	--=======================================================================================  

	SELECT isin
	FROM fund_metrics
	WHERE metric = 'time_to_recovery'
	  AND real_flag = 1
	  AND horizon = 'since_inception'
	  AND value IS NOT NULL
	  AND value <= 36;




	--=======================================================================================
	--	Filtro 5 — Supervivencia en crisis
	--	
	--	Objetivo
	--		Excluir fondos que colapsan cuando el sistema entra en estrés real.
	--	
	--	Criterio base
	--		El fondo debe cumplir al menos 3 de 4 crisis analizadas
	--		En cada crisis:
	--			max_drawdown_real no extremo
	--			recuperación posterior existente
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
	--	3.1 Filtros de daño máximo (max_drawdown_real)
	--	Tipología	Umbral
	--	Defensiva	≥ -20%
	--	Equilibrada	≥ -30%
	--	Crecimiento	≥ -40%
	--	
	--	Interpretación:
	--		un drawdown del -35% es inaceptable en núcleo defensivo,
	--		pero tolerable en crecimiento si se recupera.
	--=======================================================================================


	--=======================================================================================	
	--	3.2 Regularidad real (pct_positive_months_real)
	--	Tipología	Umbral
	--	Defensiva	≥ 60%
	--	Equilibrada	≥ 50%
	--	Crecimiento	≥ 45%
	--	
	--	Interpretación:
	--		la defensiva exige “meses tranquilos”,
	--		crecimiento acepta irregularidad.
	--=======================================================================================

	--=======================================================================================	
	--	3.3 Pérdidas severas recurrentes (pct_severe_loss_months_real)
	--	Tipología	Umbral
	--	Defensiva	≤ 10%
	--	Equilibrada	≤ 15%
	--	Crecimiento	≤ 20%
	--	
	--	Interpretación:
	--		defensiva penaliza violencia,
	--		crecimiento tolera golpes.
	--=======================================================================================


	--=======================================================================================
	--	3.4 Recuperación (time_to_recovery_real)
	--	Tipología	Umbral
	--	Defensiva	≤ 18 meses
	--	Equilibrada	≤ 36 meses
	--	Crecimiento	≤ 60 meses
	--	
	--	Interpretación:
	--		el tiempo sí es riesgo,
	--		pero su tolerancia depende del rol.
	--=======================================================================================

	--=======================================================================================	
	--	3.5 Crisis (criterio mínimo)
	--	Tipología	Exigencia
	--	Defensiva	Superar 4/4 crisis
	--	Equilibrada	Superar 3/4 crisis
	--	Crecimiento	Superar 2/4 crisis
	--	“Superar” = no colapso irreversible + drawdown relativo aceptable.
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
--  Scoring de preservación
--
--=======================================================================================


	--=======================================================================================	
	--	��� A. Daño (Damage)
	--	
	--	Métricas:
	--		max_drawdown_real
	--		worst_month_real
	--	
	--	Interpretación:
	--		mide magnitud del daño, no frecuencia.
	--		damage_score =
  	--		0.7 · percentile(max_drawdown_real, invertido)
	--		+ 0.3 · percentile(worst_month_real, invertido)
	--=======================================================================================	


	--=======================================================================================	
	--	B. Regularidad (Consistency)
	--	
	--	Métricas:
	--		pct_positive_months_real
	--		pct_severe_loss_months_real
	--	
	--	Interpretación:
	--		mide experiencia del inversor en el tiempo,
	--		penaliza erosión silenciosa.
	--	
	--	consistency_score =
  	--		0.6 · percentile(pct_positive_months_real)
	--		+ 0.4 · percentile(1 - pct_severe_loss_months_real)
	--=======================================================================================	


	--=======================================================================================	
	--	C. Resiliencia (Resilience)
	--	
	--	Métricas:
	--		time_to_recovery_real
	--		comportamiento agregado en crisis
	--	
	--	Interpretación:
	--		mide capacidad de absorber shocks y recomponerse.
	--	
	--	resilience_score =
	--		  0.6 · percentile(invertido(time_to_recovery_real))
	--		+ 0.4 · crisis_score
	--=======================================================================================	



