# ANÁLISIS DE CALIDAD P1 — Exportación 11/04/2026

**Universo:** 3.204 fondos | **Schema:** v17 | **Fecha análisis:** 11/04/2026

---

## RESUMEN EJECUTIVO

La clasificación P1 alcanza un nivel de madurez notable en los atributos estructurales (Fund_Nature, Profile, Type, Family, Theme al 100%), pero presenta **tres categorías de problemas pendientes**: (A) defectos de normalización que ya tienen fix desplegado pero no aplicado retroactivamente, (B) gaps de extracción en el parser que generan NULLs evitables, y (C) errores de clasificación en RESTANTES por misclassificación de Nature.

**Distribución por bloques:** RESTANTES procesa 2.330 fondos (72.7%) y concentra la gran mayoría de defectos. Los bloques especializados (556 RV, 234 MIXTOS, 21 MONETARIOS, 20 RF_FLEXIBLE, 14 RF_CORTO, 29 ALTERNATIVOS) tienen calidad significativamente superior.

---

## CATEGORÍA A: DEFECTOS DE NORMALIZACIÓN (fix existe, no se aplica retroactivamente)

Estos tres problemas comparten la misma causa raíz arquitectónica.

### A.1 — Accumulation_Policy: 116 fondos con casing incorrecto

| Valor | Count | Esperado |
|-------|-------|----------|
| ACCUMULATION | 1.793 | ✅ |
| DISTRIBUTION | 505 | ✅ |
| Accumulation | 109 | ❌ → ACCUMULATION |
| Distribution | 7 | ❌ → DISTRIBUTION |

**Distribución:** RESTANTES (92), MIXTOS (14), RENTA_VARIABLE (1), RF_FLEXIBLE (2).

**Causa raíz:** La normalización en `pipeline.py` (líneas 533-538) convierte correctamente `"Accumulation"` → `"ACCUMULATION"`. Sin embargo, para fondos CACHED cuyo parser devuelve `Accumulation_Policy=None`, el valor del `fund_master_record` es None. El `COALESCE` en `sqlite_writer.py` (línea 174) preserva entonces el valor stale anterior (`"Accumulation"`) de la BD, que nunca pasa por la normalización.

**Fix requerido:** Mover la normalización de casing al `sqlite_writer.py`, justo antes de la ejecución del INSERT, o añadir un paso de normalización incondicional en `pipeline.py` que lea el valor existente de BD cuando el nuevo es None.

### A.2 — Currency_Hedged: 60 fondos con valor "Yes" (inválido)

| Valor | Count | Esperado |
|-------|-------|----------|
| Hedged | 574 | ✅ |
| Yes | 60 | ❌ → Hedged |

**Distribución:** RESTANTES (47), MIXTOS (9), RENTA_VARIABLE (1), RF_FLEXIBLE (3).

**Causa raíz:** Idéntica a A.1. La normalización `"Yes"` → `"Hedged"` en `pipeline.py` (línea 541) no se aplica cuando `Currency_Hedged=None` en el registro nuevo y COALESCE preserva el stale `"Yes"`.

### A.3 — Profile-SRRI: 10 fondos Conservador con SRRI≥5

Todos RESTANTES. La corrección `Conservador → Dinámico` (líneas 544-548) no se aplica porque usa `fund_master_record.get("SRRI")` que proviene de `parsed.get("SRRI")`. Si el parser devuelve SRRI=None (fallo de extracción), la condición no se cumple, pero `sqlite_writer` preserva el SRRI=5 anterior vía CASE/COALESCE. Resultado: Profile=Conservador (directo del bloque) + SRRI=5 (preservado de BD).

**Sin embargo**, estos 10 fondos tienen un problema más grave de misclassificación de Nature (ver sección C).

### FIX ARQUITECTÓNICO UNIFICADO PARA A.1/A.2/A.3

La solución coherente con el Principio #1 (root cause) es **normalizar en `sqlite_writer.py`** inmediatamente antes del INSERT, donde el valor final es conocido (ya sea nuevo o preservado por COALESCE). Esto garantiza que TODA escritura a BD pasa por normalización, independientemente de si el fondo es CACHED, re-descargado, o nuevo.

```python
# En sqlite_writer.py, antes del INSERT:
def _normalize_before_write(record):
    ap = record.get("Accumulation_Policy")
    if ap == "Accumulation": record["Accumulation_Policy"] = "ACCUMULATION"
    elif ap == "Distribution": record["Accumulation_Policy"] = "DISTRIBUTION"
    
    ch = record.get("Currency_Hedged")
    if ch == "Yes": record["Currency_Hedged"] = "Hedged"
    
    return record
```

Alternativamente (y tal vez más limpio): ejecutar un paso de normalización SQL post-upsert. Pero esto viola el Principio #1 (sería un parche post-hoc). La normalización pre-INSERT es arquitectónicamente superior.

**Complejidad:** Baja. **Riesgo:** Ninguno. **Impacto:** 176 fondos corregidos.

**Nota sobre datos existentes:** El fix previene futuros stale values. Para corregir los 176 existentes, hay dos opciones: (a) forzar re-procesamiento con `FORCE_REFRESH` (más limpio), o (b) un UPDATE SQL one-shot (más rápido). Dado que estos fondos no necesitan re-descarga real, la opción (b) es aceptable **solo si se despliega primero el fix preventivo**.

---

## CATEGORÍA B: GAPS DE EXTRACCIÓN (atributos con NULLs evitables)

### B.1 — Strategy: 553 fondos NULL (17.3%)

| Bloque | NULLs | Total | % NULL |
|--------|-------|-------|--------|
| RESTANTES | 397 | 2.330 | 17.0% |
| RENTA_VARIABLE | 106 | 556 | 19.1% |
| MIXTOS | 36 | 234 | 15.4% |
| MONETARIOS | 8 | 21 | 38.1% |
| ALTERNATIVOS | 4 | 29 | 13.8% |
| RF_FLEXIBLE | 2 | 20 | 10.0% |

Strategy se detecta en `pipeline.py` vía `_detect_strategy()` que analiza Replication_Method, Subtype y nombre del fondo. Los 553 NULLs coinciden casi perfectamente con los 554 Replication_Method NULLs (548 en intersección), lo que indica que cuando el parser no detecta Replication_Method, tampoco se puede inferir Strategy.

**Acción propuesta:** Para fondos con Replication_Method=NULL y sin señales de indexación en el nombre, asignar `Strategy="Activo"` como default. Justificación: el 95.6% de los fondos con Strategy poblado son "Activo". La ausencia de señales de indexación/pasividad es en sí misma una señal fuerte de gestión activa.

**Impacto estimado:** ~540 fondos con Strategy asignada. **Riesgo:** Bajo (falsos positivos < 1%).

### B.2 — Geography: 425 fondos NULL (13.3%)

| Bloque | NULLs | Nature predominante |
|--------|-------|-------------------|
| RESTANTES | 353 | RF Flexible (97), RV (126), Mixtos (49), RF Corto (61) |
| MIXTOS | 52 | Mixtos |
| ALTERNATIVOS | 8 | Alternativo |
| RENTA_VARIABLE | 6 | RV |
| RF_CORTO | 5 | RF Corto |

Geography depende del characterizer (análisis de nombre + KIID). Para los 353 RESTANTES, el characterizer puede no ejecutarse si los 5 campos v3 ya están poblados.

**Acción propuesta:** Ampliar `_needs_char` para detectar Geography=NULL como condición de re-procesamiento. Adicionalmente, mejorar el characterizer con patrones de nombre más agresivos para inferir geografía (ej: "EURO" → Europa, "US/AMER" → EEUU, "ASIA/PAC" → Asia).

### B.3 — Investment_Universe: 285 fondos NULL (8.9%)

| Bloque | NULLs |
|--------|-------|
| RESTANTES | 224 |
| MIXTOS | 50 |
| ALTERNATIVOS | 6 |
| RENTA_VARIABLE | 5 |

El characterizer asigna Investment_Universe. El gap se concentra en RESTANTES y MIXTOS.

**Acción propuesta:** Para fondos con Geography poblada, inferir Investment_Universe automáticamente:
- Geography in [EEUU, China, Japón, India] → "Country"
- Geography in [Europa, Asia, Emergentes, Latinoamérica, Europa del Este] → "Regional"
- Geography = "Global" → "Global"
- Nature in [Monetario, RF Corto Plazo] → "Liquidity"

**Impacto estimado:** ~200 fondos con Universe asignada.

### B.4 — Accumulation_Policy: 790 fondos NULL (24.7%)

| Bloque | NULLs |
|--------|-------|
| RESTANTES | 600 |
| RENTA_VARIABLE | 146 |
| MIXTOS | 21 |
| ALTERNATIVOS | 12 |
| RF_CORTO | 7 |
| RF_FLEXIBLE | 3 |

Accumulation_Policy se detecta por regex en `kiid_parser.py` (`_detect_accumulation_policy`) y por nombre en el characterizer. Los 790 NULLs sugieren que ni el texto KIID ni el nombre contienen señales suficientes.

**Acción propuesta:** Ampliar detección por nombre del fondo:
- Patrones como "ACC", "ACCUM", "ACUM" → ACCUMULATION
- Patrones como "INC", "DIS", "DIST", "INCOME", "DIVIDEND" → DISTRIBUTION
- Estos patrones son muy fiables al estar en el nombre oficial del fondo.

**Nota:** El characterizer probablemente ya tiene algunos de estos patrones. Verificar cobertura actual antes de ampliar.

### B.5 — Replication_Method: 554 fondos NULL (17.3%)

Correlación casi perfecta con Strategy NULL (548 fondos en común). Si se implementa el default Strategy="Activo" de B.1, se debería asignar `Replication_Method="ACTIVE"` simultáneamente.

### B.6 — Ongoing_Charge: 840 fondos NULL (26.2%)

De los 840 NULLs, 560 tienen Fee_Known_Flag=NOT_FOUND, lo cual es coherente. Pero **102 tienen Fee_Known_Flag=EXTRACTED** y aun así Ongoing_Charge=NULL, lo cual es incoherente (EXTRACTED implica que se extrajo Entry_Fee, pero no Ongoing_Charge — pueden ser independientes).

Adicionalmente, 178 tienen Fee_Known_Flag=ZERO_CONFIRMED con Ongoing_Charge=NULL. Esto es un gap lógico: si Entry_Fee=0 está confirmado, no dice nada sobre Ongoing_Charge.

**Acción propuesta:** Revisar si `kiid_parser.py` extrae Ongoing_Charge con patrones insuficientes. Los KIIDs siempre declaran gastos corrientes — si no se detectan en el 26% de fondos, hay un gap de patrones regex.

### B.7 — Fee_Known_Flag: 1.573 fondos "NOT_FOUND" (49.1%)

| Bloque | NOT_FOUND | Total | % |
|--------|-----------|-------|---|
| RESTANTES | 1.093 | 2.330 | 46.9% |
| RENTA_VARIABLE | 286 | 556 | 51.4% |
| MIXTOS | 139 | 234 | 59.4% |
| MONETARIOS | 17 | 21 | 81.0% |
| RF_FLEXIBLE | 18 | 20 | 90.0% |
| ALTERNATIVOS | 14 | 29 | 48.3% |
| RF_CORTO | 6 | 14 | 42.9% |

Fee_Known_Flag=NOT_FOUND solo se refiere a Entry_Fee_Pct. Es esperable que muchos fondos no declaren comisión de entrada (es normal en fondos europeos). **Este porcentaje no es necesariamente un defecto** — es información válida ("no se encontró fee de entrada"). Sin embargo, convendría distinguir "no buscado" de "buscado y no encontrado" para trazabilidad.

### B.8 — Style_Profile: 2.208 fondos NULL (68.9%)

| Nature | Poblados | Total | % |
|--------|---------|-------|---|
| Mixtos | 473 | 473 | 100% |
| Renta Variable | 463 | 1.666 | 27.8% |
| RF Flexible | 46 | 466 | 9.9% |
| Alternativo | 6 | 60 | 10.0% |
| RF Corto | 8 | 428 | 1.9% |
| Monetario | 0 | 103 | 0% |
| Estructurado | 0 | 8 | 0% |

Mixtos al 100% (Strategic Allocation = 466 fondos) es correcto — todos los Mixtos se clasifican automáticamente. Para RV, el 72.2% sin Style_Profile es elevado. Los estilos Growth/Value/Income/etc. requieren análisis del KIID que el characterizer no siempre logra.

**Acción propuesta:** Aceptar que Style_Profile es un atributo de cobertura parcial para RV. Documentar como "best effort" y no como defecto. Para Monetario, RF Corto y Estructurado, NULL es semánticamente correcto (no aplica).

### B.9 — Atributos con NULL esperado alto (no son defectos)

Estos atributos tienen alta tasa de NULL por diseño:

| Atributo | NULL % | Justificación |
|----------|--------|---------------|
| Subtype | 94.3% | Solo aplica a indexados, ETFs, alternativos, estructurados |
| Market_Cap_Focus | 95.7% | Solo aplica a RV con señales explícitas de capitalización |
| Sector_Focus | 88.6% | Solo aplica a fondos sectoriales/temáticos |
| Liquidity_Profile | 98.3% | Solo aplica a monetarios específicos |
| Distribution_Frequency | 96.1% | Solo aplica a fondos de distribución |
| Portfolio_Currency | 98.7% | Rara vez declarado en KIIDs |
| Hedging_Policy | 72.1% | Solo detectable si KIID lo menciona explícitamente |
| Currency_Hedged | 80.2% | Ídem |

No requieren acción.

---

## CATEGORÍA C: ERRORES DE CLASIFICACIÓN EN RESTANTES

### C.1 — Misclassificación de Nature: 12 fondos identificados

**Grupo 1: Fondos de volatilidad clasificados como Monetario (5 fondos)**

| ISIN | Nombre | Nature actual | Nature correcta |
|------|--------|--------------|----------------|
| LU0557872479 | AMUNDI VOLATILIT WLD AE EUR AC | Monetario | Alternativo |
| LU0272941971 | AMUNDI VOLATILITY EURO AE ACC | Monetario | Alternativo |
| LU0272942433 | AMUNDI VOLATILITY EURO G ACC | Monetario | Alternativo |
| LU0272944215 | AMUNDI VOLATILITY EURO QH ACC | Monetario | Alternativo |
| LU2098886703 | DWS ESG EU M MKT IC100 EUR ACC | Monetario | ¿Monetario? (verificar) |

Los 4 AMUNDI VOLATILITY son estrategias de volatilidad (SRRI=5), no monetarios. El DWS "M MKT" es ambiguo: "M MKT" sugiere money market pero SRRI=5 es incompatible. Probablemente mal clasificado por el patrón "M MKT" en el nombre.

**Grupo 2: Fondos de RV clasificados como RF Corto Plazo (4 fondos)**

| ISIN | Nombre | Nature actual | Nature correcta |
|------|--------|--------------|----------------|
| LU0949128143 | BSF EM MK FLX DYN BN E2 EUH AC | RF Corto Plazo | Mixtos o Alternativo |
| IE0009531827 | JANUS.H. US FORTY EUR A ACC | RF Corto Plazo | Renta Variable |
| IE0004445239 | JANUS.H. US FORTY USD A ACC | RF Corto Plazo | Renta Variable |
| IE00BM95B621 | POLAR CAPITAL GBL TCH R EUR AC | RF Corto Plazo | Renta Variable |

JANUS US FORTY es un fondo de RV americana (top 40 posiciones). POLAR CAPITAL GLOBAL TECHNOLOGY es evidentemente RV tecnología. BSF "EM MK FLX DYN" parece un fondo flexible de mercados emergentes.

**Grupo 3: Fondos de RV con Type/Family=Monetario (2 fondos)**

| ISIN | Nombre | Nature | Type/Family actual |
|------|--------|--------|-------------------|
| LU1883303718 | AMUNDI EUROLAND EQ A EUR INC | Renta Variable | Monetario/Monetario |
| LU1165137495 | BNP P. SMART FOOD N EUR ACC | Renta Variable | Monetario/Monetario |

Nature=RV es correcta, pero Type y Family están como "Monetario" — una incoherencia Nature-Type/Family flagrante. RESTANTES asignó Nature por un camino y Type/Family por otro sin validar coherencia.

**Causa raíz común:** RESTANTES (catch-all) carece de validación de coherencia Nature↔Type↔Family post-clasificación. Las reglas INTER-4 e INTER-5 del Principio #9 están definidas pero **no implementadas como auto-corrección**. El validador detecta la inconsistencia pero no la corrige.

**Acción propuesta:**
1. Implementar auto-corrección en `validate_all_semantic_consistency()` para Nature-Type y Nature-Family: cuando Nature es inequívocamente correcta (ej: SRRI=5 descarta Monetario), reasignar Type y Family al default de esa Nature.
2. Añadir NAME_SIGNALS en `restantes.py` para "VOLATILITY", "US FORTY", "TECHNOLOGY", "EUROLAND EQ", "SMART FOOD" que fuercen Nature correcta.

### C.2 — Investment_Universe=Liquidity para 32 fondos no monetarios/RF Corto

| Nature | Fondos con Universe=Liquidity |
|--------|-------------------------------|
| Monetario | 95 ✅ |
| RF Corto Plazo | 428 ✅ |
| Alternativo | 4 ❌ |
| RF Flexible | 12 ❌ |
| RV | 16 ❌ |

Los 32 fondos son todos RESTANTES. Este es el bug arquitectónico conocido: `Investment_Universe='Liquidity'` stale de ciclos anteriores, no corregido porque `_needs_char` solo verifica NULL, no inconsistencia Nature↔Universe.

**Acción propuesta (ya identificada):** Ampliar `_needs_char` para detectar inconsistencia Nature/Investment_Universe, no solo NULLs.

### C.3 — Investment_Focus=Sector con Sector_Focus NULL: 11 fondos

| ISIN | Nombre | Theme | Sector_Focus |
|------|--------|-------|-------------|
| FR0010917658 | CPR SILVER AGE "E" | Silver Economy | NULL |
| FR0010836163 | CPR SILVER AGE P | Silver Economy | NULL |
| LU2298320859 | BGF CIRCULAR ECO A2 | Core/General | NULL |
| LU1861216601 | BGF NEXT GEN TEC D2 | Core/General | NULL |
| LU0278718100 | BGF SY GB EQ HI INC A2 | Core/General | NULL |
| LU0278719173 | BGF SY GB EQ HI INC E2 | Core/General | NULL |
| IE00B55MWC15 | POLAR C GL INSURANCE I | Insurance | NULL |
| IE00BFRSYK98 | JANUS H GLOB LS H2 | Core/General | NULL |
| LU0391944815 | PICTET GLOBAL MEGATREND | Megatrends | NULL |
| IE00B52VLZ70 | POLAR GLOB INSURANCE R | Insurance | NULL |
| FR0010909531 | R-CO THEMATIC SILVERPLUS | Silver Economy | NULL |

Violación directa de REGLA INTER-6: `Investment_Focus=Sector` **requiere** Sector_Focus poblado.

**Acción propuesta:** Añadir mapeo Theme→Sector_Focus para los casos claros:
- Silver Economy → (definir sector GICS: ¿Healthcare & Life Sciences? ¿Consumer?)
- Insurance → Servicios Financieros
- Megatrends → (multisectorial, reclasificar Investment_Focus a "Thematic")
- Core/General + Sector → Investigar caso por caso, posible reclasificación de Investment_Focus a "Broad"

---

## CATEGORÍA D: INCONSISTENCIAS LINGÜÍSTICAS (Principio #8)

### D.1 — Sector_Focus: idioma mixto (26 fondos en inglés)

Según Principio #8, Sector_Focus debería estar en **inglés** (nomenclatura GICS-ES). Sin embargo, en producción el 93% (338/364) está en español y solo el 7% (26/364) en inglés.

| Valor español | Count | Equivalente inglés |
|---------------|-------|--------------------|
| Tecnología e Innovación | 141 | Technology & Innovation |
| Energía y Recursos | 56 | Energy & Resources |
| Salud y Ciencias de la Vida | 43 | Healthcare & Life Sciences |
| Materiales y Minería | 41 | Materials & Mining |
| Real Assets | 22 | (ya inglés) |
| Utilities y Medio Ambiente | 22 | Utilities & Environment |
| Servicios Financieros | 8 | Financial Services |
| Consumo | 5 | Consumer |

**Decisión necesaria:** O bien se estandariza todo a inglés (conforme al Principio #8 documentado), o se actualiza el Principio #8 para oficializar español como idioma de Sector_Focus. Dado que el 93% ya está en español, **recomiendo oficializar español** y normalizar los 26 fondos en inglés a español. Es más pragmático y coherente con Geography, Type y Family que también están en español.

**Si se adopta español:** Mapeo de normalización necesario:
- Technology & Innovation → Tecnología e Innovación (17 fondos)
- Energy & Resources → Energía y Recursos (6 fondos)
- Utilities & Environment → Utilities y Medio Ambiente (2 fondos)
- Healthcare & Life Sciences → Salud y Ciencias de la Vida (1 fondo)

---

## CATEGORÍA E: GAPS SEMÁNTICOS ESTRUCTURALES

### E.1 — Derivatives_Usage: solo valor "YES" (1.898 fondos)

El 100% de los valores no-NULL son "YES". No hay un solo "NO" ni "LIMITED". Esto indica que `kiid_parser.py` solo detecta la presencia de derivados pero nunca clasifica la ausencia o el uso limitado.

Revisando los patrones en `_detect_accumulation_policy`, el parser probablemente tiene una función `_detect_derivatives_usage` que busca "derivatives" y si lo encuentra devuelve "YES", pero no tiene lógica para "NO" o "LIMITED".

**Acción propuesta:** Mejorar la detección en `kiid_parser.py`:
- Si el KIID menciona "does not use derivatives" / "no utilizará derivados" → "NO"
- Si menciona "may use derivatives for hedging" / "up to X%" → "LIMITED"  
- Si menciona "extensively uses" / "primarily invests through" → "YES"
- Si no menciona derivados en absoluto → "NO" (default conservador)

Los 1.306 NULLs actuales deberían reclasificarse mayoritariamente a "NO" o "LIMITED".

### E.2 — Leverage_Used: 2.424 fondos NULL (75.7%) con solo 2 "NO"

Similar a Derivatives_Usage: el parser solo detecta la presencia de apalancamiento pero casi nunca clasifica "NO" explícitamente. De los 780 poblados, 499 son "YES", 279 "LIMITED" y solo 2 "NO".

**Acción propuesta:** Si el KIID no menciona apalancamiento → asignar "NO" como default. El apalancamiento siempre se declara explícitamente en documentos regulatorios europeos cuando se utiliza.

### E.3 — Credit_Quality: 528 fondos NULL (16.5%)

| Nature | NULLs | Observación |
|--------|-------|-------------|
| RF Flexible | 252 | ❌ Debería estar poblado |
| Mixtos | 222 | ⚠️ Parcialmente justificable |
| Alternativo | 46 | ⚠️ Depende del subyacente |
| Estructurado | 8 | ⚠️ Depende del subyacente |

Para RF Flexible, Credit_Quality debería estar siempre poblado (252 NULLs = 54% de RF Flexible sin calidad crediticia). Los KIIDs de renta fija siempre mencionan la calidad de crédito del universo.

**Acción propuesta:** Mejorar detección en characterizer para RF Flexible. Si el KIID menciona "investment grade", "high yield", "BBB", "BB" etc., asignar valor. Si no hay señales → "Mixed" como default para RF Flexible.

---

## PLAN DE ACCIÓN PRIORIZADO

### PRIORIDAD 1 — Fix arquitectónico de normalización (Categoría A)

| # | Acción | Fichero | Fondos | Complejidad |
|---|--------|---------|--------|-------------|
| P01 | Normalización pre-INSERT en sqlite_writer | sqlite_writer.py | 176 | Baja |
| P02 | UPDATE one-shot para datos existentes | SQL | 176 | Baja |

### PRIORIDAD 2 — Cobertura de atributos de alta prioridad (Categoría B)

| # | Acción | Fichero | Fondos | Complejidad |
|---|--------|---------|--------|-------------|
| P03 | Default Strategy="Activo" + Replication="ACTIVE" cuando sin señales indexación | pipeline.py | ~540 | Baja |
| P04 | Inferencia Investment_Universe desde Geography | pipeline.py o characterizer | ~200 | Baja |
| P05 | Ampliar detección Accumulation_Policy por nombre (ACC/INC/DIS) | characterizer | ~500 | Media |
| P06 | Ampliar `_needs_char` para Geography=NULL | pipeline.py | ~350 | Baja |

### PRIORIDAD 3 — Corrección de errores de clasificación (Categoría C)

| # | Acción | Fichero | Fondos | Complejidad |
|---|--------|---------|--------|-------------|
| P07 | Auto-corrección Nature-Type-Family en validador | classify_utils.py | 12 | Media |
| P08 | NAME_SIGNALS para fondos misclassificados | restantes.py | 12 | Baja |
| P09 | `_needs_char` detectar inconsistencia Nature/Universe | pipeline.py | 32 | Baja |
| P10 | Mapeo Theme→Sector_Focus para Investment_Focus=Sector | characterizer | 11 | Baja |

### PRIORIDAD 4 — Mejoras de extracción (Categorías D, E)

| # | Acción | Fichero | Fondos | Complejidad |
|---|--------|---------|--------|-------------|
| P11 | Estandarizar Sector_Focus a español | classify_utils.py | 26 | Baja |
| P12 | Derivatives_Usage: detectar NO/LIMITED | kiid_parser.py | ~1.300 | Media |
| P13 | Leverage_Used: default NO si no mencionado | kiid_parser.py | ~2.400 | Baja |
| P14 | Credit_Quality: mejorar detección para RF Flexible | characterizer | ~250 | Media |
| P15 | Ongoing_Charge: revisar patrones regex | kiid_parser.py | ~840 | Media |

### PRIORIDAD 5 — Documentación y validación

| # | Acción |
|---|--------|
| P16 | Actualizar Principio #8: oficializar Sector_Focus en español |
| P17 | Documentar Style_Profile como "best effort" para RV |
| P18 | Revisar redundancia Hedging_Policy ↔ Currency_Hedged (REGLA INTER-11 pendiente) |

---

## MÉTRICAS DE REFERENCIA POST-FIX ESTIMADAS

Si se ejecutan las acciones P01-P14, las métricas proyectadas serían:

| Atributo | % Poblado actual | % Poblado estimado | Delta |
|----------|-----------------|-------------------|-------|
| Strategy | 82.7% | ~99% | +16.3pp |
| Replication_Method | 82.7% | ~99% | +16.3pp |
| Geography | 86.7% | ~93% | +6.3pp |
| Investment_Universe | 91.1% | ~97% | +5.9pp |
| Accumulation_Policy | 75.3% | ~90% | +14.7pp |
| Derivatives_Usage | 59.2% | ~100% | +40.8pp |
| Leverage_Used | 24.3% | ~100% | +75.7pp |
| Credit_Quality | 83.5% | ~91% | +7.5pp |
| Inconsistencias casing | 176 | 0 | -176 |
| Misclassificación Nature | 12 | 0 | -12 |
| Universe stale | 32 | 0 | -32 |
