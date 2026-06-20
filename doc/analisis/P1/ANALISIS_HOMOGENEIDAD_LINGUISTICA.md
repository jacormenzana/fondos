# ANÁLISIS EXHAUSTIVO: Homogeneidad Lingüística en fund_master

**Fecha:** 5 de abril de 2026  
**Fuente:** Fund_master_20260401.xlsx (3.204 fondos)  
**Objetivo:** Identificar y corregir mezcla de idiomas en atributos clasificatorios

---

## RESUMEN EJECUTIVO

### Estado actual de la base de datos:

| Categoría | Cantidad | % del total |
|-----------|----------|-------------|
| **Columnas analizadas** | 25 | 100% |
| **Columnas HOMOGÉNEAS** ✅ | 20 | 80% |
| **Columnas con MEZCLA** ❌ | 5 | 20% |

### Impacto de la mezcla de idiomas:

**Problema identificado:** Las 5 columnas con mezcla de idiomas afectan la integridad de:
- Agrupaciones y estadísticas (queries GROUP BY generan duplicación semántica)
- Filtros y búsquedas (queries WHERE pierden registros con traducción diferente)
- Reportes y análisis cuantitativo (métricas inconsistentes por valores duplicados)

**Ejemplo concreto del problema:**

```sql
-- Query: ¿Cuántos fondos de gestión activa tenemos?
SELECT COUNT(*) FROM fund_master WHERE Type = 'Gestión Activa';
-- Resultado: 1564 fondos

SELECT COUNT(*) FROM fund_master WHERE Type = 'Active Management';
-- Resultado: 0 fondos (no existe este valor en español)

-- Pero si Type tuviera mezcla, ambas queries darían resultados parciales
-- perdiendo fondos en cada búsqueda
```

---

## 1. COLUMNAS CON MEZCLA DE IDIOMAS (REQUIEREN NORMALIZACIÓN)

### 1.1 Type (20 valores únicos, 3.177 fondos con valor)

**Estado actual:** MEZCLA ES/EN/UNKNOWN  
**Distribución:** 8 valores ES | 7 valores EN | 4 UNKNOWN | 1 MIXED  
**Idioma objetivo:** **ESPAÑOL** (mayoría de valores y registros en ES)

**Valores actuales:**

| Valor actual | Idioma | Fondos | Valor objetivo ES |
|--------------|--------|--------|-------------------|
| Gestión Activa | ES | 1.564 | Gestión Activa ✓ |
| Allocation | **EN** | 467 | Asignación |
| Renta Fija Flexible | ES | 440 | Renta Fija Flexible ✓ |
| Renta Fija Corto Plazo | ES | 355 | Renta Fija Corto Plazo ✓ |
| Monetario | ES | 100 | Monetario ✓ |
| Indexado | MIXED | 86 | Indexado ✓ |
| Crédito CP | ES | 59 | Crédito CP ✓ |
| Absolute Return | **EN** | 44 | Retorno Absoluto |
| Commodities | **EN** | 16 | Materias Primas |
| Target Volatility | **EN** | 11 | Volatilidad Objetivo |
| Total Return | **EN** | 8 | Retorno Total |
| Tactical Allocation | **EN** | 7 | Asignación Táctica |
| Real Assets | **EN** | 5 | Activos Reales |
| Monetario Privado | ES | 4 | Monetario Privado ✓ |
| Monetario Público | ES | 3 | Monetario Público ✓ |
| Deuda Pública CP | ES | 2 | Deuda Pública CP ✓ |
| Estructurado | UNKNOWN | 2 | Estructurado ✓ |
| Target Maturity | UNKNOWN | 1 | Vencimiento Objetivo |
| Gobierno CP | UNKNOWN | 1 | Gobierno CP ✓ |
| Floating Rate CP | UNKNOWN | 1 | CP Tipo Flotante |

**Impacto de normalización:** 567 fondos afectados (17,7% del total)

---

### 1.2 Family (16 valores únicos, 3.169 fondos con valor)

**Estado actual:** MEZCLA ES/EN/UNKNOWN  
**Distribución:** 8 valores ES | 3 valores EN | 4 UNKNOWN | 1 MIXED  
**Idioma objetivo:** **ESPAÑOL** (mayoría de valores en ES, coherencia con Nature/Type)

**Valores actuales:**

| Valor actual | Idioma | Fondos | Valor objetivo ES |
|--------------|--------|--------|-------------------|
| RV Core | **EN** | 1.438 | RV Núcleo |
| Renta Fija Corto Plazo | ES | 429 | Renta Fija Corto Plazo ✓ |
| Renta Fija Flexible | ES | 401 | Renta Fija Flexible ✓ |
| Mixtos | ES | 370 | Mixtos ✓ |
| RV Temática | ES | 212 | RV Temática ✓ |
| Income Oriented | **EN** | 104 | Orientado a Ingresos |
| Monetario | ES | 86 | Monetario ✓ |
| Retorno Absoluto | ES | 41 | Retorno Absoluto ✓ |
| RF High Yield | **EN** | 33 | RF Alto Rendimiento |
| Flexible Estratégico | ES | 23 | Flexible Estratégico ✓ |
| VNAV | UNKNOWN | 18 | VNAV ✓ |
| Activos Reales | MIXED | 15 | Activos Reales ✓ |
| RF Emergentes | UNKNOWN | 13 | RF Emergentes ✓ |
| LVNAV | UNKNOWN | 12 | LVNAV ✓ |
| RF Inflación | ES | 12 | RF Inflación ✓ |
| CNAV | UNKNOWN | 2 | CNAV ✓ |

**Impacto de normalización:** 1.575 fondos afectados (49,2% del total)

---

### 1.3 Theme (22 valores únicos, 394 fondos con valor)

**Estado actual:** MEZCLA UNKNOWN/EN/ES  
**Distribución:** 20 valores UNKNOWN | 1 EN | 1 ES  
**Idioma objetivo:** **INGLÉS** (los valores UNKNOWN son nombres técnicos/temáticos internacionales en inglés)

**Valores actuales:**

| Valor actual | Idioma | Fondos | Valor objetivo EN |
|--------------|--------|--------|-------------------|
| Technology | UNKNOWN | 108 | Technology ✓ |
| Climate / Clean Energy | UNKNOWN | 37 | Climate / Clean Energy ✓ |
| Artificial Intelligence | UNKNOWN | 35 | Artificial Intelligence ✓ |
| Healthcare | UNKNOWN | 35 | Healthcare ✓ |
| Gold | UNKNOWN | 33 | Gold ✓ |
| Energy | UNKNOWN | 25 | Energy ✓ |
| Water | UNKNOWN | 24 | Water ✓ |
| Infrastructure | UNKNOWN | 12 | Infrastructure ✓ |
| Financials | UNKNOWN | 10 | Financials ✓ |
| Silver Economy | UNKNOWN | 10 | Silver Economy ✓ |
| Real Estate | **EN** | 10 | Real Estate ✓ |
| Digital | UNKNOWN | 9 | Digital ✓ |
| Mining | UNKNOWN | 9 | Mining ✓ |
| Consumer Brands | UNKNOWN | 7 | Consumer Brands ✓ |
| Megatrends | UNKNOWN | 7 | Megatrends ✓ |
| Inflación | **ES** | 6 | Inflation |
| Robotics | UNKNOWN | 6 | Robotics ✓ |
| Biotechnology | UNKNOWN | 4 | Biotechnology ✓ |
| Consumer / Food & Beverage | UNKNOWN | 4 | Consumer / Food & Beverage ✓ |
| Cybersecurity | UNKNOWN | 1 | Cybersecurity ✓ |
| Healthcare / MedTech | UNKNOWN | 1 | Healthcare / MedTech ✓ |
| Insurance | UNKNOWN | 1 | Insurance ✓ |

**Impacto de normalización:** 6 fondos afectados (0,2% del total - solo "Inflación")

---

### 1.4 Subtype (12 valores únicos, 177 fondos con valor)

**Estado actual:** MEZCLA EN/UNKNOWN/MIXED  
**Distribución:** 6 valores EN | 5 UNKNOWN | 1 MIXED  
**Idioma objetivo:** **INGLÉS** (mayoría de valores son términos técnicos en inglés)

**Valores actuales:**

| Valor actual | Idioma | Fondos | Valor objetivo EN |
|--------------|--------|--------|-------------------|
| Fondo Indexado | **MIXED** | 70 | Index Fund |
| Opportunistic | UNKNOWN | 41 | Opportunistic ✓ |
| Physical / Derivatives | EN | 16 | Physical / Derivatives ✓ |
| ETF | UNKNOWN | 16 | ETF ✓ |
| Low Duration | EN | 9 | Low Duration ✓ |
| Autocallable | UNKNOWN | 8 | Autocallable ✓ |
| Floating Rate Notes | UNKNOWN | 7 | Floating Rate Notes ✓ |
| Global Macro | UNKNOWN | 5 | Global Macro ✓ |
| Total Return Bond | EN | 2 | Total Return Bond ✓ |
| Long/Short | EN | 1 | Long/Short ✓ |
| Real Estate | EN | 1 | Real Estate ✓ |
| Relative Value / Arbitrage | EN | 1 | Relative Value / Arbitrage ✓ |

**Impacto de normalización:** 70 fondos afectados (2,2% del total)

---

### 1.5 Strategy (3 valores únicos, 2.651 fondos con valor)

**Estado actual:** MEZCLA ES/MIXED  
**Distribución:** 2 valores ES | 1 MIXED  
**Idioma objetivo:** **ESPAÑOL** (ya está casi completamente en ES)

**Valores actuales:**

| Valor actual | Idioma | Fondos | Valor objetivo ES |
|--------------|--------|--------|-------------------|
| Activo | ES | 2.533 | Activo ✓ |
| Indexado | **MIXED** | 86 | Indexado ✓ |
| Pasivo | ES | 32 | Pasivo ✓ |

**Nota:** "Indexado" está marcado como MIXED por el algoritmo de detección, pero es un valor válido en español. **NO requiere normalización**, solo confirmación de que es ES.

**Impacto de normalización:** 0 fondos (solo confirmación de idioma)

---

## 2. COLUMNAS HOMOGÉNEAS (NO REQUIEREN NORMALIZACIÓN)

### 2.1 Columnas en ESPAÑOL (ya homogéneas) ✅

| Columna | Valores únicos | Idioma | Estado |
|---------|----------------|--------|--------|
| **Fund_Nature** | 8 | ES | ✅ Óptimo |
| **Profile** | 3 | ES | ✅ Óptimo |
| **Geography** | 10 | ES | ✅ Óptimo |

**Valores Fund_Nature (todos ES):**
- Renta Variable, Mixtos, Renta Fija Flexible, Renta Fija Corto Plazo, Monetario, Alternativo, Restantes, Estructurado

**Valores Profile (todos ES):**
- Dinámico, Moderado, Conservador

**Valores Geography (todos ES):**
- Global, Europa, EE.UU., Asia, Japón, Emergentes, Eurozona, Reino Unido, China, Pacífico

---

### 2.2 Columnas en INGLÉS (ya homogéneas) ✅

| Columna | Valores únicos | Idioma | Estado |
|---------|----------------|--------|--------|
| **Style_Profile** | 9 | EN | ✅ Óptimo |
| **Exposure_Bias** | 10 | EN | ✅ Óptimo |
| **Hedging_Policy** | 2 | EN | ✅ Óptimo |
| **Replication_Method** | 2 | EN | ✅ Óptimo |
| **SRRI_Quality_Flag** | 5 | EN | ✅ Óptimo |
| **Benchmark_Type** | 3 | EN | ✅ Óptimo |
| **Accumulation_Policy** | 2 | EN | ✅ Óptimo |
| **Leverage_Used** | 3 | EN | ✅ Óptimo |
| **Distribution_Frequency** | 4 | EN | ✅ Óptimo |
| **Market_Cap_Focus** | 3 | EN | ✅ Óptimo |
| **Sector_Focus** | 8 | EN | ✅ Óptimo |
| **Currency_Hedged** | 1 | EN | ✅ Óptimo |
| **Investment_Universe** | 6 | EN | ✅ Óptimo |

**Ejemplos de valores (todos EN):**
- Style_Profile: Strategic Allocation, Income, Growth, Value, Low Volatility, Momentum, Blend
- Exposure_Bias: Duration Bias, Income Bias, Liquidity Bias, Credit Bias, ...
- Hedging_Policy: HEDGED, UNHEDGED
- Market_Cap_Focus: Large Cap, Mid Cap, Small Cap

---

### 2.3 Columnas NEUTRALES/TÉCNICAS (valores técnicos sin idioma) ✅

| Columna | Valores únicos | Tipo | Estado |
|---------|----------------|------|--------|
| **Derivatives_Usage** | 1 | Técnico | ✅ Solo "YES" |
| **Data_Quality_Flag** | 3 | Técnico | ✅ COMPLETE/PARTIAL/MINIMAL |
| **Recommended_Holding_Period** | 10 | Técnico | ✅ "3 years", "5 years", etc. |
| **Liquidity_Profile** | 2 | Técnico | ✅ DAILY/WEEKLY |

---

## 3. DEFINICIÓN DE IDIOMA OBJETIVO POR COLUMNA

### Criterios de asignación:

1. **ESPAÑOL:** Columnas "core de negocio" que el usuario final interpreta (Nature, Type, Profile, Strategy, Geography, Family)
2. **INGLÉS:** Columnas técnicas/especializadas del sector financiero (Style, Sector, Theme, Exposure, Market Cap, Benchmark)
3. **NEUTRAL:** Columnas con códigos, valores técnicos o siglas internacionales

### Tabla resumen idioma objetivo:

| Columna | Idioma objetivo | Razón |
|---------|----------------|-------|
| Fund_Nature | **ESPAÑOL** ✅ | Core negocio, ya homogéneo |
| Type | **ESPAÑOL** 🔧 | Core negocio, requiere normalización |
| Subtype | **INGLÉS** 🔧 | Términos técnicos, requiere normalización |
| Family | **ESPAÑOL** 🔧 | Core negocio, requiere normalización |
| Profile | **ESPAÑOL** ✅ | Core negocio, ya homogéneo |
| Strategy | **ESPAÑOL** ✅ | Core negocio, ya homogéneo |
| Geography | **ESPAÑOL** ✅ | Core negocio, ya homogéneo |
| Theme | **INGLÉS** 🔧 | Términos técnicos internacionales, requiere normalización |
| Style_Profile | **INGLÉS** ✅ | Términos técnicos, ya homogéneo |
| Exposure_Bias | **INGLÉS** ✅ | Términos técnicos, ya homogéneo |
| Sector_Focus | **INGLÉS** ✅ | Términos técnicos, ya homogéneo |
| Market_Cap_Focus | **INGLÉS** ✅ | Términos técnicos, ya homogéneo |
| Investment_Universe | **INGLÉS** ✅ | Términos técnicos, ya homogéneo |
| Benchmark_Type | **INGLÉS** ✅ | Términos técnicos, ya homogéneo |
| Hedging_Policy | **INGLÉS** ✅ | Términos técnicos, ya homogéneo |
| Replication_Method | **INGLÉS** ✅ | Términos técnicos, ya homogéneo |
| Accumulation_Policy | **INGLÉS** ✅ | Términos técnicos, ya homogéneo |
| Distribution_Frequency | **INGLÉS** ✅ | Términos técnicos, ya homogéneo |
| Leverage_Used | **INGLÉS** ✅ | Términos técnicos, ya homogéneo |
| Currency_Hedged | **INGLÉS** ✅ | Términos técnicos, ya homogéneo |
| SRRI_Quality_Flag | **INGLÉS** ✅ | Metadata técnica, ya homogéneo |
| Data_Quality_Flag | **NEUTRAL** ✅ | Metadata técnica, ya homogéneo |
| Derivatives_Usage | **NEUTRAL** ✅ | Metadata técnica, ya homogéneo |
| Recommended_Holding_Period | **NEUTRAL** ✅ | Valores técnicos, ya homogéneo |
| Liquidity_Profile | **NEUTRAL** ✅ | Valores técnicos, ya homogéneo |

**Leyenda:**
- ✅ = Ya homogéneo, no requiere acción
- 🔧 = Requiere normalización

---

## 4. IMPACTO TOTAL DE LA NORMALIZACIÓN

### Fondos afectados por columna:

| Columna | Fondos a normalizar | % del total |
|---------|---------------------|-------------|
| Type | 567 | 17,7% |
| Family | 1.575 | 49,2% |
| Theme | 6 | 0,2% |
| Subtype | 70 | 2,2% |
| Strategy | 0 | 0,0% |
| **TOTAL ÚNICO** | **~1.700** | **~53%** |

**Nota:** El total único es aproximado porque algunos fondos pueden tener mezcla en múltiples columnas.

### Beneficios esperados post-normalización:

1. **Agrupaciones consistentes:** Queries `GROUP BY` sin duplicación semántica
2. **Filtros precisos:** Queries `WHERE` capturan 100% de registros relevantes
3. **Estadísticas confiables:** Métricas sin sesgo por valores duplicados
4. **Mantenibilidad:** Código más simple, sin diccionarios de traducción ad-hoc
5. **Escalabilidad:** Nuevos fondos siguen convención establecida

---

## 5. PRÓXIMOS PASOS

### Fase 1: Actualizar documentación ✅
- [x] Actualizar `PRINCIPIOS_DISENO.md` con Principio #8 corregido
- [x] Actualizar Custom Instructions del Claude Project
- [ ] Añadir validación de homogeneidad en `classify_utils.py`

### Fase 2: Migración de datos 🔧
- [ ] Ejecutar script `normalize_column_languages_v17.py --dry-run`
- [ ] Validar mapeos de traducción
- [ ] Ejecutar script `normalize_column_languages_v17.py --apply`
- [ ] Verificar post-migración con queries de control

### Fase 3: Prevención futura 🔧
- [ ] Integrar validación en `classify_fund()` (classify_utils.py)
- [ ] Añadir diccionario de valores permitidos por columna
- [ ] Auto-traducción de valores EN→ES o ES→EN según columna
- [ ] Log de errores para valores no reconocidos

---

**FIN DEL ANÁLISIS**

*Documento generado el 5 de abril de 2026*  
*Fuente: Fund_master_20260401.xlsx (3.204 fondos)*
