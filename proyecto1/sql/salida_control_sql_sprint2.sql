-- ── Q1: Distribución de Cost_Extraction_Quality ──────────────
-- Objetivo: HIGH + MEDIUM_* > 70% de los fondos procesados.
-- NONE esperado solo en fondos con KID_Format=UNKNOWN (no PRIIPs ni UCITS).

Cost_Extraction_Quality	n	pct
LOW	1.605	50,1
MEDIUM_EUR	597	18,6
HIGH	587	18,3
NONE	347	10,8
[NULL]	38	1,2
MEDIUM_CROSS	28	0,9
MEDIUM_PCT	3	0,1

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

fondos_con_schedule
1.522



-- ── Q3: Distribución de KID_Format ───────────────────────────
-- Verificar que la clasificación PRIIPs/UCITS/UNKNOWN es razonable.

KID_Format	n
PRIIPS_KID	2.798
UCITS_KIID	5
-- Resultado esperado: PRIIPS_KID >> UCITS_KIID (solo ~5 fondos UCITS en corpus)


-- ── Q4: Mismatches OC/ACI detectados (para BL-COST-5) ────────
-- Estos fondos tienen OC en BD que el extractor sospecha que es ACI@RHP.
-- BL-COST-5 (sesión Opus separada) analizará y corregirá.

n_mismatch_fondos
13


ISIN	message	created_at
LU1840769696	OC en BD parece ACI; diferido a BL-COST-5	2026-05-23T20:55:57
LU1193126809	OC en BD parece ACI; diferido a BL-COST-5	2026-05-23T20:54:05
LU0871827464	OC en BD parece ACI; diferido a BL-COST-5	2026-05-23T20:53:07
LU0173778175	OC en BD parece ACI; diferido a BL-COST-5	2026-05-23T20:49:04
LU0289472085	OC en BD parece ACI; diferido a BL-COST-5	2026-05-23T20:43:53
LU0323456896	OC en BD parece ACI; diferido a BL-COST-5	2026-05-23T20:43:52
LU0289472085	OC en BD parece ACI; diferido a BL-COST-5	2026-05-23T20:40:22
LU0323456896	OC en BD parece ACI; diferido a BL-COST-5	2026-05-23T20:40:19
LU0173778175	OC en BD parece ACI; diferido a BL-COST-5	2026-05-23T20:34:45
LU0289472085	OC en BD parece ACI; diferido a BL-COST-5	2026-05-23T20:30:13
LU0323456896	OC en BD parece ACI; diferido a BL-COST-5	2026-05-23T20:30:12
LU0289472085	OC en BD parece ACI; diferido a BL-COST-5	2026-05-23T20:26:46
LU0323456896	OC en BD parece ACI; diferido a BL-COST-5	2026-05-23T20:26:42


-- ── Q5: Escala de valores — verificar % entero vs ratio ──────
-- CRÍTICO: todos los valores _Pct deben ser > 0.01 (% entero).
-- Si MIN < 0.001, hay fondos con valores en escala ratio (bug de escala).

campo	min_val	max_val	avg_val	n_poblado
Management_Fee_Pct	0	3,3	1,322425979	1.047
Ongoing_Charge_Recurrent	0,0001	2,7	0,0180849057	3.180
ACI_RHP	0,05	8,99	5,1668307692	650
ACI_1Y	0,03	12,85	3,7956378251	846


-- ── Q6: Filas de schedule por fondo (distribución) ───────────
-- Cada fondo PRIIPs típicamente tiene 2-3 filas (1Y, 3Y/5Y RHP, etc.)
-- UCITS tiene exactamente 1 fila (sintética).

Source	total_filas	fondos_distintos	avg_filas_por_fondo
PRIIPS_COSTS_OVER_TIME	1.515	1.515	1,5
UCITS_DERIVED	7	7	1


-- ── Q7: Fondos HIGH quality — verificar coherencia OC vs ACI_RHP
-- En fondos HIGH, OC ≈ ACI_1Y (ambos deberían ser cercanos al TER real).
-- Diferencias > 1pp son sospechosas.

ISIN	OC	ACI_1Y	ACI_RHP	diff_OC_ACI1Y
LU0187077309	0,0286	7,9	7,9	7,8714
IE0009356076	0,0052	7,7	7,7	7,6948
IE0002122038	0,0056	7,7	7,7	7,6944
LU2409249781	0,0082	7,7	7,7	7,6918
LU0135928298	0,0106	7,7	7,7	7,6894
IE0009355771	0,005	7,6	7,6	7,595
LU0135928611	0,0084	7,6	7,6	7,5916
IE0009531827	0,0086	7,6	7,6	7,5914
LU0143551892	0,037	7,6	7,6	7,563
LU1438969351	0,037	7,6	7,6	7,563
LU0910073989	0,04	7,6	7,6	7,56
LU0135928025	0,0076	7,5	7,5	7,4924
LU0554840230	0,0252	7,5	7,5	7,4748
LU0699433149	0,0252	7,5	7,5	7,4748
LU0910074284	0,039	7,5	7,5	7,461
IE0001257090	0,0092	7,4	7,4	7,3908
LU0355496257	0,0242	7,4	7,4	7,3758
LU0355496760	0,038	7,4	7,4	7,362
IE00B06CFP96	0,0195	7,3	7,3	7,2805
IE00B23T0K72	0,0195	7,3	7,3	7,2805


-- ── Q8: Errores del pipeline durante el ciclo de costes ──────
step	status	n_fondos
BL_COST_4C_OC_ACI_MISMATCH	WARN	6


 ────────────
-- Si > 500, el ciclo fue parcial o hay fondos sin KIID disponible.
fondos_sin_kid_format
402


-- ── Q10: Completitud del schedule (Is_RHP = 1 por fondo) ─────
-- Cada fondo con schedule debería tener exactamente 1 fila Is_RHP=1.
-- Fondos con 0 filas RHP pueden indicar problema de resolución del RHP.

categoria	n_fondos
1 fila RHP (esperado)	1.216
0 filas RHP	306









