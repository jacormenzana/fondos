# MODELO SEMÁNTICO — Atributos de Clasificación

> **Propósito.** Define el dominio funcional de cada atributo de clasificación, la semántica
> de cada valor permitido, las condiciones de aplicabilidad (cuándo es obligatorio, condicional,
> o NULL), y las reglas de consistencia cruzada. Es la referencia normativa para clasificadores
> y reglas INTER. Las reglas INTER deben derivarse de este modelo, no añadirse ad-hoc cuando
> surgen inconsistencias individuales.
>
> **Alcance.** Los atributos están organizados en seis clústeres:
> - **Cluster A** — Jerarquía de clasificación: `Fund_Nature` → `Family`
> - **Cluster B** — Foco de inversión / especialización: `Investment_Focus` → `Theme` + `Sector_Focus`
> - **Cluster C** — Atributos específicos de renta fija: `Credit_Quality`, `Duration_Profile`, `Market_Cap_Focus`
> - **Cluster D** — Estructura del vehículo: `MMF_Structure`, `Alt_Strategy`, `Payoff_Profile`
> - **Cluster E** — Estilo y exposición: `Style_Profile`, `Exposure_Bias`, `Development_Status`
> - **Cluster F** — Consistencia operacional: `Strategy`↔`Replication_Method`, `Accumulation_Policy`↔`Distribution_Frequency`, `Is_ESG`↔`SFDR_Article`, `Leverage_Used`↔`Profile`
>
> **Fecha**: 2026-07-12. **Fuente**: análisis de distribución de la BD (~3.200 fondos).
>
> **Implementación:** Las guías de implementación para clasificadores e INTER rules viven en
> `NORMAS_IMPLEMENTACION.md` §3. Este documento es normativo (el *qué*), no prescriptivo (el *cómo*).

---

## §1. Cluster A — Jerarquía de clasificación

### §1.1 `Fund_Nature` (Nivel 1)

**Dominio funcional**: Categoría primaria regulatoria / clase de activo. Determina qué bloque P1
clasifica el fondo y qué atributos son aplicables en los clústeres restantes.

| Valor | Significado | Bloques aplicables |
|-------|------------|-------------------|
| `Renta Variable` | Predominantemente renta variable (≥ ~60% equity) | renta_variable |
| `Mixtos` | Mixto (equity + bonos, sin dominancia fuerte de ninguno) | mixtos |
| `Renta Fija Flexible` | Renta fija sin restricciones de duración / flexible (Duration_Profile ∈ {Intermediate, Long, Flexible}) | rf_flexible |
| `Renta Fija Corto Plazo` | RF con mandato de duración máxima ≤ 3 años (Duration_Profile ∈ {Ultra-Short, Short}); alineado con ICE BofA 1-3y / Morningstar Short-Term Bond (RF-RFF-POLICY-2026-07-16) | rf_corto |
| `Monetario` | Fondo monetario UCITS | monetarios |
| `Alternativo` | Estrategias alternativas (retorno absoluto, real assets, long/short) | alternativos |
| `Estructurado` | Productos estructurados con protección de capital | restantes/alternativos |
| `Restantes` | Sin clasificar; residual | restantes |

**Aplicabilidad**: Obligatorio para todos los fondos. NULL solo durante el procesamiento previo
a la ejecución del bloque clasificador.

---

### §1.2 `Family` (Nivel 2)

**Dominio funcional**: Sub-clasificación dentro de `Fund_Nature`. Codifica el estilo de inversión
o tipo de estrategia, aportando mayor granularidad que `Fund_Nature` por sí solo.

**Combinaciones `Fund_Nature` → `Family` válidas** (verificado en BD):

| Fund_Nature | Family válidas |
|-------------|---------------|
| `Renta Variable` | `Equity Core`, `Thematic Equity` |
| `Mixtos` | `Multi-Asset`, `Income Oriented` |
| `Renta Fija Flexible` | `Flexible Fixed Income`, `High Yield`, `Emerging Market Debt`, `Inflation-Linked`, `Income Oriented`, `Strategic Allocation` |
| `Renta Fija Corto Plazo` | `Short-Term Fixed Income` |
| `Monetario` | `Money Market` |
| `Alternativo` | `Absolute Return`, `Real Assets` |
| `Estructurado` | `Structured` |
| `Restantes` | NULL (categoría residual; no se asigna Family) |

**Aplicabilidad**: Obligatorio para todos los `Fund_Nature` excepto `Restantes`.

**Regla de consistencia SC-A1** — `Family` debe pertenecer al conjunto válido para su `Fund_Nature`.
Un fondo `Renta Variable` no puede tener `Family='Money Market'` o `Family='Short-Term Fixed Income'`.

**Violaciones conocidas en BD (2026-07-11)**:

| ISIN | Fund_Nature | Family | Causa raíz |
|------|-------------|--------|------------|
| 1 fondo | `Renta Variable` | `Short-Term Fixed Income` | Error de clasificador |
| 1 fondo | `Renta Variable` | `Money Market` | Error de clasificador |
| 1 fondo | `Restantes` | `Multi-Asset` | Artefacto COALESCE (Family antigua preservada tras reclasificación) |

---

## §2. Cluster B — Foco de inversión / especialización

Este clúster usa tres atributos conjuntamente:

- **`Investment_Focus`** — *cómo* está concentrado el mandato de inversión
- **`Theme`** — *sobre qué tema/foco* invierte el fondo (siempre sub-descriptor)
- **`Sector_Focus`** — *en qué sector industrial*, solo cuando el mandato es sector-concentrado

### §2.1 `Investment_Focus`

**Dominio funcional**: Grado y tipo de concentración de la cartera.

| Valor | Significado | Cuándo usar |
|-------|------------|------------|
| `Broad` | Cartera diversificada sin concentración en un único sector o tema; invierte en múltiples industrias o temas | Equity core, FI equilibrado, global diversificado |
| `Sector` | Cartera concentrada en un **único sector industrial** clásico (alineado con GICS/ICB); benchmarkeable frente a índice sectorial | Fondos de renta variable sectoriales, bonos sectoriales |
| `Thematic` | Cartera concentrada en una **tendencia macro / narrativa transversal** que no mapea a un único sector; la tesis de inversión abarca múltiples sectores tradicionales | Protección vs inflación, megatendencias, multi-temático |

**Distinción semántica crítica — Sector vs Thematic**:

> Un fondo es `Sector` cuando su universo está **definido por una clasificación industrial**
> (todas las empresas en GICS Technology, todas en GICS Healthcare, etc.), independientemente de
> los sub-temas que existan dentro de ese sector. Un fondo es `Thematic` cuando su universo está
> **definido por una narrativa** que cruza sectores (ej. "beneficiarios de inflación" incluye
> productores de commodities, RE, TIPS y bonos ligados a inflación — cruzando Equity, Fixed Income,
> Real Assets).

**Aplicabilidad por `Fund_Nature`**:

| Fund_Nature | Aplicabilidad de Investment_Focus |
|-------------|----------------------------------|
| `Renta Variable` | Obligatorio. Cualquiera de Broad / Sector / Thematic |
| `Mixtos` | Obligatorio. Típicamente Broad; Sector/Thematic para fondos de asignación especializados |
| `Renta Fija Flexible` | Condicional. NULL para estrategias sin restricción y sin mandato sectorial/temático; Broad / Sector / Thematic cuando hay especialización explícita |
| `Renta Fija Corto Plazo` | Típicamente NULL o Broad. Sector solo para bonos a corto plazo de un único sector |
| `Monetario` | NULL o Broad únicamente. Sin sub-clasificación sector/temática |
| `Alternativo` | Típicamente NULL o Broad. Thematic para fondos alternativos temáticos |
| `Estructurado` | NULL. Los productos estructurados no se sub-clasifican por foco de inversión |
| `Restantes` | NULL |

---

### §2.2 `Theme`

**Dominio funcional**: El tema, foco o narrativa específica que el fondo aborda dentro de su
categoría `Investment_Focus`. Opera con mayor granularidad que `Investment_Focus`.

**Relación con `Investment_Focus`**:

| Investment_Focus | Theme | Rol del Theme |
|-----------------|-------|--------------|
| `Broad` | Cualquiera; `Core/General` es el predeterminado | Tilt descriptivo. Un fondo global equity broad puede tener un sesgo hacia Technology sin ser un fondo sectorial puro. |
| `Sector` | Sub-tema específico dentro del sector; o `Core/General` para mandatos de sector completo | Sub-etiqueta de granularidad fina. Añade especificidad dentro del sector. |
| `Thematic` | La narrativa transversal | **Obligatorio y definitorio**. Sin Theme, un `Investment_Focus='Thematic'` está semánticamente vacío. |

**Semántica de valores de Theme y parejas válidas con `Investment_Focus`**:

| Theme | Investment_Focus válido | Notas |
|-------|------------------------|-------|
| `Core/General` | `Broad`, `Sector` | Predeterminado para mandatos broad o fondos sectoriales sin sub-tema |
| `Technology` | `Sector` | Sector tecnológico puro |
| `Artificial Intelligence` | `Sector`, `Thematic` | Sector: puro-play de IA en sector tech. Thematic: IA como transformación transversal |
| `Robotics` | `Sector`, `Thematic` | Mismo límite que IA |
| `Digital` | `Sector`, `Thematic` | Mismo límite |
| `Cybersecurity` | `Sector`, `Thematic` | Mismo límite |
| `Climate / Clean Energy` | `Sector`, `Thematic` | Sector: puro-play de transición energética. Thematic: narrativa ESG multi-sector |
| `Energy` | `Sector` | Sector energético tradicional. Si es transición energética multi-sector → usar `Climate / Clean Energy` |
| `Healthcare` | `Sector`, `Thematic` | Sector: mandato broad de sector salud. Thematic: narrativa de innovación sanitaria |
| `Biotechnology` | `Sector`, `Thematic` | Sector: biotech como sub-sector GICS. Thematic: narrativa revolución genómica/biotech |
| `Water` | `Sector`, `Thematic` | Sector: utilities/infraestructura. Thematic: narrativa ESG de escasez de agua |
| `Gold` | `Sector` | Metales preciosos / commodities |
| `Mining` | `Sector` | Materiales / minería |
| `Real Estate` | `Sector` | Sector inmobiliario (REITs). Si Real Assets incluye infraestructura + RE → Family `Real Assets` / Alternativo |
| `Financials` | `Sector` | Sector servicios financieros |
| `Insurance` | `Sector` | Sub-sector servicios financieros |
| `Consumer Brands` | `Sector`, `Thematic` | Sector: consumo básico/discrecional. Thematic: narrativa de consumo basada en marcas |
| `Silver Economy` | `Sector`, `Thematic` | Sector: salud/consumo relacionado con envejecimiento. Thematic: macro-tendencia demográfica |
| `Megatrends` | **`Thematic` ÚNICAMENTE** | Multi-sector por definición. No puede ser un fondo sectorial. |
| `Inflation` | **`Thematic` ÚNICAMENTE** | Tema macro monetario. Abarca TIPS, commodities, RE, bonos ligados a inflación — cruza clases de activos y sectores. |

**Regla SC-B1** — `Theme='Megatrends'` → `Investment_Focus` DEBE ser `'Thematic'`.

**Regla SC-B2** — `Theme='Inflation'` → `Investment_Focus` DEBE ser `'Thematic'`.

**Regla SC-B3** — `Investment_Focus='Thematic'` → `Theme` NO DEBE ser `'Core/General'`
(un fondo temático debe tener un tema nombrado; Core/General implica Broad).

---

### §2.3 `Sector_Focus`

**Dominio funcional**: El **bucket de sector industrial broad** en el que se concentra el fondo.
Actúa como etiqueta sectorial de grano grueso (Nivel 1) cuando `Theme` (arriba) da el sub-tema
de grano fino (Nivel 2) para fondos sectoriales.

**Regla de aplicabilidad crítica**:

> `Sector_Focus` se puebla **si y solo si** `Investment_Focus='Sector'`.
> Es NULL para `Investment_Focus='Broad'` y `Investment_Focus='Thematic'`.

Esto significa:
- Los fondos temáticos NO tienen `Sector_Focus`. Su foco está definido enteramente por `Theme`.
- Los fondos broad NO tienen `Sector_Focus`.
- Solo los fondos sectoriales tienen tanto `Theme` (sub-tema) como `Sector_Focus` (bucket sectorial).

**Regla SC-B4** — `Investment_Focus='Sector'` → `Sector_Focus` NO DEBE ser NULL.

**Regla SC-B5** — `Investment_Focus` ∈ `{'Broad', 'Thematic'}` → `Sector_Focus` DEBE ser NULL.

**Mapeo canónico `Theme` → `Sector_Focus`** (para fondos `Investment_Focus='Sector'`):

| Theme | Sector_Focus |
|-------|-------------|
| `Technology` | `Technology & Innovation` |
| `Artificial Intelligence` | `Technology & Innovation` |
| `Robotics` | `Technology & Innovation` |
| `Digital` | `Technology & Innovation` |
| `Cybersecurity` | `Technology & Innovation` |
| `Climate / Clean Energy` | `Energy & Resources` |
| `Energy` | `Energy & Resources` |
| `Healthcare` | `Healthcare & Life Sciences` |
| `Biotechnology` | `Healthcare & Life Sciences` |
| `Silver Economy` | `Healthcare & Life Sciences` |
| `Water` | `Utilities & Environment` |
| `Gold` | `Materials & Mining` |
| `Mining` | `Materials & Mining` |
| `Real Estate` | `Real Assets` |
| `Financials` | `Financial Services` |
| `Insurance` | `Financial Services` |
| `Consumer Brands` | `Consumer` |
| `Core/General` | *sector-específico* (cualquiera; usar la descripción de sector explícita del fondo) |

Los temas `Megatrends` e `Inflation` **no tienen mapeo válido de `Sector_Focus`** — deben
usar siempre `Investment_Focus='Thematic'` con `Sector_Focus=NULL`.

**Regla SC-B6** — SI `Investment_Focus='Sector'` Y `Theme` NO ES NULL ENTONCES
`Sector_Focus` DEBE coincidir con el mapeo canónico para ese `Theme`. Un fondo con
`Theme='Technology'` y `Sector_Focus='Healthcare & Life Sciences'` es inconsistente.

---

## §3. Interacciones entre clústeres (A + B)

### §3.1 `Family='Thematic Equity'` vs `Investment_Focus`

`Family='Thematic Equity'` significa que el fondo es un **fondo de renta variable con foco
especializado**. No determina por sí solo si el foco es un sector o un tema transversal.

| Family | Investment_Focus | Cuándo es válido |
|--------|-----------------|-----------------|
| `Thematic Equity` | `Sector` | El fondo se concentra en un único sector industrial (más común: 252 fondos) |
| `Thematic Equity` | `Thematic` | El fondo se concentra en un tema transversal (Megatrends, Inflation si se expresa en equity) |
| `Thematic Equity` | `Broad` | **No válido** — por definición un fondo temático equity tiene un foco |

**Regla SC-C1** — `Family='Thematic Equity'` → `Investment_Focus` DEBE ser `'Sector'` o `'Thematic'`.
Nunca `'Broad'`.

### §3.2 `Family='Equity Core'` vs `Investment_Focus`

`Equity Core` = fondo de renta variable diversificado. Por definición, un fondo equity core
no se concentra en un único sector. Sin embargo, existen fondos core sector-específicos
(ej. "European Technology Core" — broad-dentro-de-technology, sin sub-tema).

| Family | Investment_Focus | Cuándo es válido |
|--------|-----------------|-----------------|
| `Equity Core` | `Broad` | Fondo equity de mercado broad estándar (1.549 fondos) |
| `Equity Core` | `Sector` | Mandato core dentro de un sector (16 fondos — ej. energy core) |
| `Equity Core` | `Thematic` | **No válido** — un fondo core con mandato temático debería ser `Thematic Equity` |

**Regla SC-C2** — `Family='Equity Core'` → `Investment_Focus` NO DEBE ser `'Thematic'`.

### §3.3 `Family='Inflation-Linked'` vs `Investment_Focus`

`Inflation-Linked` como valor de `Family` se aplica a **fondos de renta fija** cuya cartera
consiste en bonos ligados a la inflación (OATs-i, TIPS, Bunds-i, etc.). Codifica una estrategia
de bonos, no una construcción de cartera temática.

| Family | Investment_Focus | Sector_Focus | Theme | ¿Válido? |
|--------|-----------------|-------------|-------|----------|
| `Inflation-Linked` | `Thematic` | NULL | `Inflation` | **Válido** — representación correcta |
| `Inflation-Linked` | `Broad` | NULL | `Inflation` | **Válido** — también aceptable |
| `Inflation-Linked` | `Sector` | `Inflation-Linked` | `Inflation` | **Inválido** — `Inflation` no es un sector; `Inflation-Linked` no está en `DOMAIN_VALUES` de `Sector_Focus` |

La regla SC-B1 (`Theme='Inflation'` → `Investment_Focus='Thematic'`) es suficiente para
prevenir el tercer caso.

---

## §4. Cluster C — Atributos específicos de renta fija

`Credit_Quality`, `Duration_Profile`, y `Market_Cap_Focus` son atributos **específicos por
clase de activo**. Su aplicabilidad depende de `Fund_Nature`.

| Atributo | Natures aplicables | No aplicable (→ `Not Applicable` o NULL) |
|----------|-------------------|------------------------------------------|
| `Credit_Quality` | `Renta Fija Flexible`, `Renta Fija Corto Plazo`, `Monetario` | `Renta Variable`, `Alternativo`, `Estructurado` |
| `Duration_Profile` | `Renta Fija Flexible`, `Renta Fija Corto Plazo`, `Monetario` | `Renta Variable`, `Alternativo`, `Estructurado` |
| `Market_Cap_Focus` | `Renta Variable`, `Mixtos` (componente equity) | Todas las natures de RF, `Monetario`, `Alternativo`, `Estructurado`, `Restantes` |

`Mixtos` se excluye de la auto-corrección de Credit_Quality/Duration_Profile porque los fondos
mixtos llevan legítimamente un componente de renta fija.

**Alineamiento industrial:**
- Los valores de `Credit_Quality` (`Investment Grade`, `High Yield`, `Mixed`, `Not Applicable`)
  mapean directamente a las categorías estándar de rating de crédito definidas por S&P/Moody's/Fitch
  y codificadas en los requisitos de divulgación del KIID UCITS.
- Los valores de `Duration_Profile` (`Ultra-Short` < 1y, `Short` 1-3y, `Intermediate` 3-7y,
  `Long` > 7y, `Flexible`, `Not Applicable`) se alinean con los tramos de duración de los índices
  de renta fija ICE BofA y la clasificación de categorías de renta fija de Morningstar.
  **Derivación de Fund_Nature RF (RF-RFF-POLICY-2026-07-16):** Duration_Profile ∈ {Ultra-Short, Short}
  → `Renta Fija Corto Plazo`; Duration_Profile ∈ {Intermediate, Long, Flexible} → `Renta Fija
  Flexible`. Boundary canónico: duración máxima mandatada ≤ 3 años = RF_Corto. Ambos atributos
  deben ser coherentes entre sí; la función `resolve_rf_subtype()` en `classify_utils.py` implementa
  esta regla y es la fuente única para la derivación del subtipo RF.

**Reglas de consistencia:**
- **SC-D1**: `Fund_Nature` ∈ `{Renta Variable, Alternativo, Estructurado}` + `Credit_Quality` ≠ `'Not Applicable'` → auto-corregir a `'Not Applicable'`. Implementado en INTER-17.
- **SC-D2**: Mismas natures + `Duration_Profile` ≠ `'Not Applicable'` → auto-corregir a `'Not Applicable'`. Implementado en INTER-17.

---

## §5. Cluster D — Atributos de estructura del vehículo

### `MMF_Structure`

**Dominio funcional**: Estructura legal/regulatoria de un fondo del mercado monetario bajo el
Reglamento UE 2017/1131 (MMFR). Solo aplica a `Fund_Nature='Monetario'`.

| Valor | Definición regulatoria | Referencia MMFR |
|-------|----------------------|----------------|
| `CNAV` | Valor liquidativo constante. NAV por unidad fijado en €1,00 (o par). Solo MMF de deuda pública/gubernamental. | Art. 29 MMFR |
| `LVNAV` | NAV de baja volatilidad. NAV por unidad puede desviarse ≤0,2% de €1,00. MMF a corto plazo. | Art. 30 MMFR |
| `VNAV` | NAV variable. NAV calculado a valor de mercado cada día hábil. MMF estándar o a corto plazo. | Art. 31 MMFR |
| `Standard MMF` | MMF VNAV estándar no a corto plazo (WAM 3-6 meses). | Art. 2(14) MMFR |
| `Not Applicable` | No es un MMF UCITS (aplica a todos los fondos no-Monetario). | — |

**Reglas de consistencia:**
- **SC-E1**: `Fund_Nature ≠ 'Monetario'` Y `MMF_Structure ≠ 'Not Applicable'` → auto-corregir a `'Not Applicable'`. Implementado en INTER-18.
- **SC-E2**: `Fund_Nature='Monetario'` Y `MMF_Structure='Not Applicable'` → WARN (todo MMF regulado debe tener una estructura MMFR explícita). Implementado en INTER-18.

### `Alt_Strategy`

**Dominio funcional**: Tipo de estrategia de inversión alternativa. Solo aplica a
`Fund_Nature='Alternativo'`. Se alinea con la clasificación de estrategias de hedge funds
de AIMA/HFR.

| Valor | Significado |
|-------|------------|
| `Opportunistic` | Sin estrategia única; oportunístico entre oportunidades |
| `Global Macro` | Macro top-down (divisas, tasas, commodities) |
| `Long/Short` | Long equity, short equity vía derivados |
| `Market Neutral` | Exposición neta a equity cero (pairs trading, arbitraje estadístico) |
| `Relative Value/Arbitrage` | Explota diferenciales de spread |
| `Volatility Target` | Cartera con objetivo de volatilidad gestionada |
| `Not Applicable` | Fondos no alternativos |

`Alt_Strategy='Not Applicable'` en un fondo Alternativo está permitido cuando la estrategia del
fondo no mapea a una categoría AIMA estándar. No hay regla de auto-corrección.

### `Payoff_Profile`

**Dominio funcional**: Estructura de payoff de productos estructurados / con protección de capital.
Solo aplica a `Fund_Nature='Estructurado'`. Actualmente solo `Autocallable` está observado en la BD.

---

## §6. Cluster E — Atributos de estilo y exposición

### `Style_Profile`

**Dominio funcional**: Estilo de inversión / tilt factorial de la cartera equity del fondo.

**Alineamiento industrial — Morningstar Style Box + MSCI Factor Indices:**

| Valor | Estándar industrial | Fuente |
|-------|-------------------|--------|
| `Growth` | Estilo crecimiento (P/E alto, crecimiento elevado de beneficios) | Morningstar Style Box |
| `Value` | Estilo valor (P/E bajo, yield de dividendo alto) | Morningstar Style Box |
| `Blend` | Mezcla de crecimiento y valor | Morningstar Style Box |
| `Income` | Equity centrado en dividendos/ingresos | Morningstar Income category |
| `Low Volatility` | Factor mínima volatilidad / beta baja | MSCI Min Vol factor |
| `Quality` | Factor calidad (ROE alto, bajo apalancamiento, beneficios estables) | MSCI Quality factor |
| `Momentum` | Factor momentum (comportamiento reciente superior) | MSCI Momentum factor |
| `Strategic Allocation` | Estilo asignación multi-activo (para Mixtos/fondos equilibrados) | Morningstar Allocation category |
| `Not Applicable` | No es un fondo de equity o sin estilo definido | — |

**Aplicabilidad por `Fund_Nature`:**
- `Renta Variable` → cualquier valor de estilo equity o `Not Applicable`
- `Mixtos` → `Strategic Allocation` (representa el estilo de asignación equilibrada) o `Not Applicable`
- `Alternativo` → puede llevar un estilo equity si el fondo es alternativo orientado a equity (ej. equity long/short con estilo blend); `Not Applicable` preferido si no hay estilo claro
- `Restantes` → `Not Applicable` o NULL (residual; el estilo de una clasificación previa es obsoleto)

**Regla SC-E3**: `Fund_Nature='Restantes'` + `Style_Profile` ≠ `'Not Applicable'` y ≠ NULL → WARN.
Implementado en INTER-19.

### `Exposure_Bias`

**Dominio funcional**: Exposición direccional neta del fondo.

| Valor | Significado | Fund_Nature típica |
|-------|------------|-------------------|
| `Long Only` | Solo posiciones largas (sin short) | Renta Variable, Mixtos, RF Flexible, RF Corto, Monetario |
| `Long/Short` | Combina posiciones largas y cortas | Alternativo (estrategia Long/Short) |
| `Market Neutral` | Exposición neta cero (delta-neutral) | Alternativo |
| `Net Short` | Predominantemente posiciones cortas | Alternativo (raro) |
| `Not Applicable` | Productos estructurados | Estructurado |

Los 3.213 fondos estándar (no alternativos, no estructurados) llevan `Long Only` — correcto
por definición. No se necesitan reglas semánticas adicionales más allá de la alineación de clasificación.

### `Development_Status`

**Dominio funcional**: Clasificación de desarrollo de mercado del universo de inversión del fondo.
Se alinea con el **marco de clasificación de mercados MSCI**.

| Valor | Equivalente MSCI | Ejemplos geográficos |
|-------|----------------|---------------------|
| `Developed` | MSCI Developed Markets | EE.UU., UE, Japón, UK, Canadá, Australia |
| `Emerging` | MSCI Emerging Markets | China, India, Brasil, Corea, México |
| `Frontier` | MSCI Frontier Markets | Vietnam, Nigeria, Kuwait |
| `Global/Mixed` | Mezcla (sin un único nivel) | Global, multi-región, DM+EM mezclados |

**Combinaciones `Geography` ↔ `Development_Status` válidas** (del análisis de BD):
- `Europe` + `Developed`: 560 fondos ✓ (Europa Occidental)
- `Europe` + `Emerging`: 2 fondos ✓ (fondos EM de Europa del Este — válidos)
- `Global` + `Global/Mixed`: 1.539 fondos ✓
- `Global` + `Emerging`: 164 fondos ✓ (equity global EM)
- `China/India/Latin America/Eastern Europe` + `Emerging`: ✓
- `North America/Japan` + `Developed`: ✓

No se han encontrado inconsistencias sistemáticas. No se necesitan reglas adicionales.

---

## §7. Cluster F — Consistencia operacional

Estos atributos gobiernan características operacionales del fondo y tienen requisitos de
consistencia por pares independientemente de `Fund_Nature`. Son validados por INTER-1
a INTER-8 en `validate_all_semantic_consistency`.

### `Strategy` ↔ `Replication_Method` (SC-F1)

Estándar industrial (ESMA ETF Guidelines ESMA/2012/832): las estrategias de seguimiento de
índice y pasivas **deben** usar un método de replicación no-Activo.

| Strategy | Replication_Method requerido |
|----------|-----------------------------|
| `Activo` | `Active` (o NULL) |
| `Indexado` | `Physical`, `Sampling`, o `Synthetic` |
| `Pasivo` | `Physical`, `Sampling`, o `Synthetic` |

Si `Strategy='Indexado'` o `'Pasivo'` y `Replication_Method='Active'` → **auto-corregir a
`Physical`** (más común) y WARN. Implementado en INTER-1.

### `Accumulation_Policy` ↔ `Distribution_Frequency` (SC-F2)

| Accumulation_Policy | Distribution_Frequency |
|---------------------|------------------------|
| `Accumulation` | Debe ser `NULL` (sin distribuciones) |
| `Distribution` | Debe estar poblado (frecuencia) |
| `Mixed` | Puede estar poblado |

Si `Accumulation_Policy='Accumulation'` Y `Distribution_Frequency` NO ES NULL
→ auto-corregir `Distribution_Frequency` a `NULL`. Implementado en INTER-2.

### `Is_ESG` ↔ `SFDR_Article` (SC-F3)

| Is_ESG | Semántica SFDR_Article | Esperado |
|--------|------------------------|---------|
| `1` (True) | El fondo declara características de sostenibilidad | Art. 8 o Art. 9 |
| `0` (False) | Sin declaración de sostenibilidad | Art. 6 o NULL |

Si `Is_ESG=1` Y `SFDR_Article=6` → solo WARN (el fondo se clasifica como ESG pero no tiene
declaración SFDR Art. 8/9; puede ser una brecha de divulgación, no necesariamente un error
de clasificación). Implementado como WARN en INTER-8 (sin auto-corrección).

### `Leverage_Used` ↔ `Profile` (SC-F4)

| Leverage_Used | Profile | Evaluación |
|---------------|---------|------------|
| `Yes` | `Conservative` | WARN — inusual (el apalancamiento incrementa el riesgo; contradice el mandato conservador) |
| `Yes` | `Moderate`, `Aggressive` | OK — esperado para estos perfiles |
| `No` / `Limited` | `Conservative` | OK — esperado |

WARN-only en INTER-7. Sin auto-corrección — puede que el fondo use apalancamiento exclusivamente
para cobertura (sin incrementar el riesgo neto).

---

## §7. Cluster G — Consistencia geográfica

Los atributos `Geography` e `Investment_Universe` describen el ámbito geográfico del fondo
desde dos perspectivas complementarias. La semántica de su combinación debe ser coherente.

### §7.1 `Geography` ↔ `Investment_Universe` (SC-G1)

**Regla de consistencia:**

| Geography | Investment_Universe requerido | Razón |
|-----------|------------------------------|-------|
| `Europe`, `North America`, `Asia-Pacific`, `Latin America`, `Eastern Europe`, `Middle East & Africa` | `Regional` | Región multi-país sub-global |
| `China`, `Japan`, `India` | `Country` | Foco en un único país |
| `Global` | `Global` | Mandato global sin restricción geográfica |

**Precedencia:** `Geography` gana. Es derivada de señales positivas explícitas en el nombre y
texto KIID del fondo; `Investment_Universe` puede ser una inferencia por defecto (BL-33).

**Auto-corrección:** SC-G1 es auto-correctable. Implementado en INTER-20.

**Severidad:** WARN→auto (igual que SC-D1/SC-D2): el valor corregido se persiste en BD
vía la A2-merge de pipeline.py + COALESCE de sqlite_writer.

**Causa raíz histórica:** BL-33 (INTER-13) asigna `Investment_Universe='Global'` a fondos
Monetario y Renta Fija Corto Plazo por defecto (inferencia por naturaleza del fondo), sin
considerar si Geography ya tiene un valor específico. Esto generaba ~137 fondos con
`IU='Global'` + Geography específica en la BD (Europe=93, North America=32, Asia-Pacific=6,
China=4, Japan=2). INTER-20 (SC-G1) se ejecuta después de INTER-13 para interceptar y
corregir esta inconsistencia.

**Regla SC-G1** — `Geography ∈ REGION_GEOS → Investment_Universe = 'Regional'`;
`Geography ∈ COUNTRY_GEOS → Investment_Universe = 'Country'`.
Implementado en INTER-20 de `validate_all_semantic_consistency`.

---

## §7. Cluster H — Consistencia con benchmark de mercado

Los atributos del fondo en `fund_master` deben ser coherentes con el benchmark de mercado
asociado en `fund_benchmarks`. El cluster SC-H es **solo detección** (WARN/INFO): el validador
no puede reclasificar, por lo que el camino de remediación es siempre
`FORCE_REFRESH → re-clasificar` (R-2).

**Fuente de datos:** tabla `fund_benchmarks` (schema v22). La fila `source='MORNINGSTAR'`
es la señal independiente de mayor calidad; `source='KIID'` es semi-redundante (parsea el
mismo documento que el clasificador). El pipeline prefiere MORNINGSTAR con fallback a KIID.

**Precedencia de `asset_class` entre fuentes (decisión 2026-09-26).** Implementada en
`core.benchmark_normalizer.merge_benchmark_sources` (única implementación; `pipeline.run_block` la
invoca):
- Si existen ambas fuentes, **MORNINGSTAR prevalece** aunque los valores no nulos difieran. Las dos
  etiquetas responden a preguntas distintas: `Mixed` de Morningstar describe la *asignación del
  fondo*, la etiqueta KIID describe la *composición de su benchmark*. Un desacuerdo entre dos valores
  no nulos **no es un defecto** y no se arbitra aquí: ambas señales llegan a las reglas SC-H, que las
  ponderan por `confidence`. Sobrescribir la asignación con la clase del benchmark podría falsear el
  perfil de riesgo de un fondo (objetivo: preservación de capital).
- **Excepción única:** una fila MORNINGSTAR con `asset_class` NULL (nombre no normalizado, confianza
  LOW) no oculta una fila KIID que sí tiene `asset_class`; en ese caso se usa la KIID.
- Medido el 2026-09-26 sobre los 1.939 ISINs con ambas fuentes: 319 difieren; 67 implican un NULL en
  un lado (mayoritariamente Morningstar rellenando un hueco KIID) y 252 son conflictos reales no
  nulos, de los cuales 209 son Morningstar `Mixed` frente a una clase única del benchmark KIID y 34
  son `Fixed Income` frente a `Rate` (granularidad). La excepción de arriba afecta a 5 ISINs.

**Vocabulario centralizado (R-1):** todos los mapas de polos de crédito, geografía y sector
del benchmark viven en `classify_utils.py` (`BMK_CONSISTENT`, `BMK_TOLERATED`,
`bmk_tok_credit()`, `bmk_geography()`, etc.) y son importados por el validador y por
`audit_benchmark_consistency.py`. No duplicar.

**Ponderación por confianza:** cuando `confidence='LOW'` o `'MEDIUM'`, los errores críticos
se demoran a WARN/INFO en el DQ para evitar falsos positivos de datos de baja calidad.

**Supresión de pares benignos:** `BMK_GEO_BENIGN_PAIRS` (p.ej. Japan ↔ Asia-Pacific) y el
carve-out EM-soberano (fondos EM HY legítimos con benchmarks de gobierno EM) se suprimen para
evitar ruido en el DQ.

### §7.1 `Fund_Nature` ↔ benchmark asset class (SC-H1)

**Señal:** `fund_benchmarks.asset_class` (p.ej. `'Equity'`, `'Fixed Income'`, `'Rate'`).

**Regla:** la combinación `(Fund_Nature, asset_class)` debe pertenecer al conjunto
`BMK_CONSISTENT[Fund_Nature]` (OK) o `BMK_TOLERATED[Fund_Nature]` (INFO). Cualquier otra
combinación → WARN. `benchmark_role='hurdle_rate'` nunca dispara SC-H.

Implementado en `validate_benchmark_nature` (classify_utils.py, INTER-18 ya existente).
Antes de SC-H, el pipeline pasaba `None` → era un no-op; SC-H1 alimenta el parámetro.

### §7.2 `Credit_Quality` ↔ polo de crédito del benchmark (SC-H2)

**Aplicación:** solo fondos FI (`Renta Fija Flexible`, `Renta Fija Corto Plazo`, `Monetario`).

**Señal:** nombre del benchmark (`fund_benchmarks.benchmark_name`), tokenizado por
`bmk_tok_credit()` → polo canónico (`'High Yield'`, `'Investment Grade'`, `'Aggregate'`, ...).

**Regla:** conflicto IG↔HY entre el polo del benchmark y `Credit_Quality` del fondo → WARN.

**Carve-out EM-soberano:** un benchmark con polo `'Government'` + `'sovereign'` + token EM
(`'em '`, `'emerg'`) no conflicta con `Credit_Quality='High Yield'` (los bonos soberanos EM
pueden cotizar a spread HY — esta combinación es legítima).

**DQ check\_code:** `SEM_BENCHMARK_CREDIT_SC_H2`. Confianza HIGH → `critical_errors`
(nivel WARN en DQ); confianza LOW/MEDIUM → `warnings` (nivel INFO).

### §7.3 `Geography` ↔ geografía del benchmark (SC-H3)

**Señal:** nombre del benchmark, tokenizado por `bmk_geography()` → geografía canónica
(`'Europe'`, `'North America'`, `'Japan'`, `'Asia-Pacific'`, `'Global'`, ...).

**Regla:** si ambas geografías son no-nulas, no-`Global`, no-`Mercados Emergentes`, y no
pertenecen a `BMK_GEO_BENIGN_PAIRS` → WARN (posible detección geográfica incorrecta en el
clasificador).

**Severidad:** siempre `warnings` (→ INFO DQ), nunca crítico — el conflicto de geografía
puede ser legítimo (p.ej. fondo europeo con benchmark global como referencia de rentabilidad).

**DQ check\_code:** `SEM_BENCHMARK_GEOGRAPHY_SC_H3`.

---

## §8. Transversal: Homogeneidad lingüística (Principio #8)

**Principio:** `PRINCIPIOS_DISENO.md` P#8. Esta sección documenta la tabla de asignación por columna.

Cada columna categórica del schema usa un único idioma para todos sus valores. Mezclar idiomas
rompe `GROUP BY` / `WHERE` queries fragmentando poblaciones.

| Idioma | Columnas |
|--------|---------|
| **Español** | `Fund_Nature`, `Profile`, `Geography` |
| **Inglés** | `Family`, `Investment_Focus`, `Theme`, `Sector_Focus`, `Market_Cap_Focus`, `Style_Profile`, `Exposure_Bias`, `Development_Status`, `Credit_Quality`, `Duration_Profile`, `MMF_Structure`, `Alt_Strategy`, `Hedging_Policy`, `Replication_Method`, `Derivatives_Usage`, `Liquidity_Profile`, `Distribution_Frequency`, `SFDR_Article` |
| **Código / neutro** | `ISIN`, `Fund_Currency`, `Asset_Currency`, `SRRI`, costes numéricos |

**Nota de evolución**: Antes de v20 (2026), `Family` estaba en español ('RV Núcleo',
'Renta Fija Flexible', 'Retorno Absoluto', etc.). La migración a inglés ('Equity Core',
'Flexible Fixed Income', 'Absolute Return') se realizó con el schema v20 para alinear con
Morningstar Category naming. Los valores legacy en español en la BD son residuales y deben
ser migrados por `sqlite_writer._normalize_record` (defensa en profundidad, única excepción
autorizada a R-1).

Los mapas de normalización para traducciones legacy ES→EN viven exclusivamente en
`classify_utils.py` (R-1 en `RESTRICCIONES_ARQUITECTURA.md`).

---

## §9. Transversal: Centinela vs NULL (Principio #10)

**Principio completo:** `PRINCIPIOS_DISENO.md` P#10.

Para atributos donde *"diverso/multi-valor intencionalmente"* es semánticamente distinto de
*"aún no determinado"*, se requiere un **valor centinela** categórico en lugar de `NULL`.

**Centinelas actuales:**

| Atributo | Centinela | Significado |
|----------|----------|-------------|
| `Asset_Currency` | `MCY` | El fondo tiene mandato explícito multi-divisa |
| `Geography` | `Global` | El fondo cubre explícitamente múltiples geografías |
| `Investment_Focus` | `Broad` | El fondo cubre universo broad (no sector ni temático) |

Colapsar diverso-por-diseño y desconocido en el mismo `NULL` destruye información y rompe
COALESCE (P#1): un centinela correcto no es nulo y sobrescribirá correctamente un valor único
obsoleto de un ciclo anterior, mientras que `None` sería preservado por COALESCE.

---

## §10. Catálogo completo de reglas de consistencia

### Cluster A — Alineación Nature/Family

| ID | Regla | Severidad | ¿Auto-corregible? | Regla INTER |
|----|-------|----------|-------------------|-------------|
| SC-A1 | `Family` debe estar en el conjunto válido para `Fund_Nature` | ERROR | No — requiere fix de clasificador | INTER-1 |

### Cluster B — Investment Focus / Theme / Sector

| ID | Regla | Severidad | ¿Auto-corregible? | Regla INTER |
|----|-------|----------|-------------------|-------------|
| SC-B1 | `Theme='Megatrends'` → `Investment_Focus='Thematic'`, `Sector_Focus=NULL` | WARN→auto | Sí | INTER-15 |
| SC-B2 | `Theme='Inflation'` → `Investment_Focus='Thematic'`, `Sector_Focus=NULL` | WARN→auto | Sí | INTER-15 |
| SC-B3 | `Investment_Focus='Thematic'` + `Theme='Core/General'` → `Investment_Focus='Broad'` | WARN | Sí | INTER-9 |
| SC-B4 | `Investment_Focus='Sector'` → `Sector_Focus` NO ES NULL | WARN | No — requiere saber el sector | — |
| SC-B5 | `Theme` ∈ `_THEMATIC_ONLY_THEMES` → `Sector_Focus=NULL` | WARN→auto | Sí | INTER-15 |
| SC-B6 | `Theme` → `Sector_Focus` debe coincidir con `THEME_SECTOR_MAPPING` | WARN→auto | Sí — aplicar tabla de mapeo | INTER-9 (actualizado) |
| SC-C1 | `Family='Thematic Equity'` + `Investment_Focus='Broad'` → WARN | WARN | No (fix de clasificador necesario) | — |
| SC-C2 | `Family='Equity Core'` + `Investment_Focus='Thematic'` → WARN | WARN | No (fix de clasificador necesario) | — |

### Cluster C — Atributos específicos de bonos (aplicabilidad por clase de activo)

| ID | Regla | Severidad | ¿Auto-corregible? | Regla INTER |
|----|-------|----------|-------------------|-------------|
| SC-D1 | `Fund_Nature` ∈ `{RV, Alternativo, Estructurado}` + `Credit_Quality` ≠ `'Not Applicable'` → corregir a `'Not Applicable'` | WARN→auto | Sí | INTER-17 |
| SC-D2 | Mismas natures + `Duration_Profile` ≠ `'Not Applicable'` → corregir a `'Not Applicable'` | WARN→auto | Sí | INTER-17 |

### Cluster D — Atributos de estructura MMF/alternativo

| ID | Regla | Severidad | ¿Auto-corregible? | Regla INTER |
|----|-------|----------|-------------------|-------------|
| SC-E1 | `Fund_Nature ≠ 'Monetario'` + `MMF_Structure ≠ 'Not Applicable'` → corregir a `'Not Applicable'` | WARN→auto | Sí | INTER-18 |
| SC-E2 | `Fund_Nature='Monetario'` + `MMF_Structure='Not Applicable'` → WARN (debería tener estructura MMFR explícita) | WARN | No — necesita prospecto | INTER-18 |

### Cluster G — Consistencia geográfica

| ID | Regla | Severidad | ¿Auto-corregible? | Regla INTER |
|----|-------|----------|-------------------|-------------|
| SC-G1 | `Geography` ∈ región → `Investment_Universe='Regional'`; `Geography` ∈ país → `Investment_Universe='Country'` (cuando IU='Global' e IU≠requerido) | WARN→auto | Sí — Geography tiene precedencia | INTER-20 |

### Cluster H — Consistencia con benchmark de mercado

Remediación de toda regla SC-H: `FORCE_REFRESH → re-clasificar` (R-2). El validador es de solo detección.

| ID | Regla | Severidad | ¿Auto-corregible? | check_code |
|----|-------|----------|-------------------|------------|
| SC-H1 | `Fund_Nature` ↔ `benchmark.asset_class`: combinación fuera de `BMK_CONSISTENT` + `BMK_TOLERATED` → WARN | WARN | No | `SEM_BENCHMARK_NATURE` |
| SC-H2 | `Credit_Quality` ↔ polo de crédito del benchmark: conflicto IG↔HY en fondos FI → WARN/INFO según confianza; carve-out EM-soberano | WARN (HIGH conf.) / INFO (LOW/MED) | No | `SEM_BENCHMARK_CREDIT_SC_H2` |
| SC-H3 | `Geography` ↔ geografía del benchmark: conflicto geográfico no-benign, no-Global → INFO | INFO | No | `SEM_BENCHMARK_GEOGRAPHY_SC_H3` |

### Cluster E — Atributos de estilo / exposición

| ID | Regla | Severidad | ¿Auto-corregible? | Regla INTER |
|----|-------|----------|-------------------|-------------|
| SC-E3 | `Fund_Nature='Restantes'` + `Style_Profile` poblado → WARN (atributo obsoleto de clasificación previa) | WARN | No — puede ser candidato FORCE_REFRESH | INTER-19 |

### Cluster F — Consistencia operacional

| ID | Regla | Severidad | ¿Auto-corregible? | Regla INTER |
|----|-------|----------|-------------------|-------------|
| SC-F1 | `Strategy` ∈ `{'Indexado','Pasivo'}` + `Replication_Method='Active'` → corregir a `Physical` | WARN→auto | Sí (default a `Physical`) | INTER-1 |
| SC-F2 | `Accumulation_Policy='Accumulation'` + `Distribution_Frequency` NO ES NULL → nullificarlo | WARN→auto | Sí | INTER-2 |
| SC-F3 | `Is_ESG=1` + `SFDR_Article=6` → WARN (falta declaración Art. 8/9) | WARN | No — requiere actualización de divulgación manual | INTER-8 |
| SC-F4 | `Leverage_Used='Yes'` + `Profile='Conservative'` → WARN (apalancamiento en mandato conservador) | WARN | No — requiere revisión del prospecto | INTER-7 |

---

## §11. Violaciones conocidas actuales (2026-07-11)

Todas las violaciones abajo son resultado del comportamiento legacy del clasificador antes de
que MODIFY #6 restringiera `Sector_Focus` solo a fondos sectoriales, y antes de que el modelo
semántico fuera definido formalmente.

| Cuenta ISIN | Fund_Nature | Family | Investment_Focus | Theme | Sector_Focus | Reglas violadas | Estado correcto |
|-------------|-------------|--------|-----------------|-------|-------------|----------------|-----------------|
| 3 (AXA inflation bonds) | RF Flexible | Inflation-Linked | Thematic | Inflation | Inflation-Linked | SC-B5, SC-B6 | Sector_Focus → NULL |
| 1 (SISF inflation bond) | RF Flexible | Inflation-Linked | Sector | Inflation | Inflation-Linked | SC-B2, SC-B5, SC-B6 | Investment_Focus → Thematic, Sector_Focus → NULL |
| 2 (PIMCO inflation equity) | Renta Variable | Thematic Equity | Sector | Inflation | Inflation-Linked | SC-B2, SC-B6 | Investment_Focus → Thematic, Sector_Focus → NULL |
| 2 (Pictet Megatrend) | Renta Variable | Thematic Equity | Thematic | Megatrends | Multi-Theme | SC-B5 | Sector_Focus → NULL |
| 1 (Pictet Megatrend) | Renta Variable | Thematic Equity | Sector | Megatrends | Multi-Theme | SC-B1, SC-B6 | Investment_Focus → Thematic, Sector_Focus → NULL |
| 3 (Healthcare+Thematic) | Renta Variable | Thematic Equity | Thematic | Healthcare | Healthcare & Life Sciences | SC-B5 | Investigar: puede ser correcto como Sector (ver nota) |
| 1 (Real Estate+Thematic) | Alternativo | Real Assets | Thematic | Real Estate | Real Assets | SC-B5 | Investigar: puede ser correcto como Sector |

**Nota sobre los casos Healthcare+Thematic y Real Estate+Thematic**: El valor de `Sector_Focus`
es válido (está en `DOMAIN_VALUES`), por lo que la regla SC-B6 no se viola. La pregunta es si
`Investment_Focus='Thematic'` es correcto o si deberían ser `'Sector'`. Esto depende del
mandato real del fondo. **Requiere decisión a nivel de clasificador, no auto-corrección.**

---

## Apéndice — Alineamiento industrial

Este apéndice mapea cada clúster de atributos al marco regulatorio o industrial externo del que
deriva. Las adiciones de atributos futuros deben estar fundamentadas en uno de estos marcos.

### A. Atributos de riesgo/perfil

| Atributo | Marco | Referencia |
|----------|-------|-----------|
| `SRRI` | Reglamento KIID UCITS (EU 583/2010), Anexo I, Parte 1 | Escala de desviación estándar de 7 niveles; divulgación obligatoria |
| `Profile` | CNMV / perfilado de idoneidad MiFID II | Mapea `Conservative` → SRRI 1-2, `Moderate` → 3-4, `Growth` → 5-6, `Aggressive` → 7 |
| `SFDR_Article` | Reglamento SFDR UE (EU 2019/2088) | Art. 6 = sin declaración de sostenibilidad, Art. 8 = características ESG, Art. 9 = objetivo de inversión sostenible |

**Mapeo SRRI ↔ Profile** (implementado en INTER-3):

| SRRI | Profile mapeado |
|------|----------------|
| 1–2 | Conservative |
| 3–4 | Moderate |
| 5–6 | Growth |
| 7 | Aggressive |

### B. Atributos de clasificación

| Atributo | Marco | Referencia |
|----------|-------|-----------|
| `Fund_Nature` | Morningstar Category + taxonomía de tipos de fondos CNMV | Bucket de clase de activo de nivel superior; mapea directamente a `Tipo de Fondo` CNMV |
| `Family` | Morningstar Category (Nivel 2) | ~30 sub-categorías dentro de cada naturaleza |
| `Investment_Focus` | Split de categoría Morningstar Equity: "Sector Equity" vs "Thematic Equity" | `Sector` = sector industrial GICS; `Thematic` = tema macro transversal |
| `Sector_Focus` | GICS (Global Industry Classification Standard), 11 sectores consolidados a 8 | Definido como consolidación GICS Nivel-1 (ver §2.3) |
| `Theme` | MSCI Thematic Indexes + taxonomía temática Morningstar | Mapea a macro-tendencias transversales (IA, Clima, Envejecimiento, etc.) |
| `Development_Status` | Marco de clasificación de mercados MSCI (Annual Market Classification Review) | Developed / Emerging / Frontier; revisado anualmente |
| `Style_Profile` | Morningstar Style Box (3×3: Value/Blend/Growth × Small/Mid/Large) + MSCI Factor Indexes | Aplicado a nivel de capitalización aquí; neutral en capitalización |
| `Market_Cap_Focus` | Eje de tamaño del Morningstar Style Box: Large > $10B, Mid $2-10B, Small < $2B | Segmentación institucional estándar |

### C. Atributos de renta fija

| Atributo | Marco | Referencia |
|----------|-------|-----------|
| `Credit_Quality` | Niveles de rating S&P/Moody's/Fitch; directiva de activos elegibles UCITS | `Investment Grade` ≥ BBB−/Baa3; `High Yield` < BBB−; `Mixed` = mezcla |
| `Duration_Profile` | Tramos de duración de índices ICE BofA / Bloomberg | `Ultra-Short` < 1y; `Short` 1-3y; `Intermediate` 3-7y; `Long` > 7y; `Flexible` = sin restricción |

### D. Estructura regulatoria MMF

| Valor | Regulación | Restricción legal |
|-------|-----------|------------------|
| `CNAV` | MMFR Art. 29 | Permitido solo para MMF CNAV de deuda pública/gubernamental; reembolsos a la par |
| `LVNAV` | MMFR Art. 30 | Solo MMF a corto plazo; desviación NAV ≤ 0,20% (collar) |
| `VNAV` | MMFR Art. 31 | Todos los MMF a corto plazo que no califican como CNAV/LVNAV; también MMF estándar |
| `Standard MMF` | MMFR Art. 2(14) | WAM 6 meses, WAL 12 meses; CNAV/LVNAV no permitido |

### E. Atributos ESG/Sostenibilidad

| Atributo | Marco | Referencia |
|----------|-------|-----------|
| `SFDR_Article` | SFDR UE (Reglamento 2019/2088), modificado por RTS marzo 2023 | Divulgación a nivel de fondo Art. 6/8/9 |
| `Is_ESG` | Derivado de SFDR_Article (Art. 8 o Art. 9 → True) | No es un término regulatorio; indicador de conveniencia |

### F. Derivados / apalancamiento

| Atributo | Marco | Referencia |
|----------|-------|-----------|
| `Derivatives_Usage` | Directiva UCITS (2009/65/EC) Art. 50; ESMA Guidelines on ETFs / gestión de riesgo | `None` = sin derivados; `Hedging Only` = cobertura FX/tipos solo; `Investment` = generación de alfa; `Both` = ambos usos |
| `Leverage_Used` | Nota de apalancamiento KIID UCITS; AIFMD (para AIFs) Art. 25 | Binario: `Yes` / `No` |

### G. Atributos operacionales

| Atributo | Marco | Referencia |
|----------|-------|-----------|
| `Liquidity_Profile` | Directiva UCITS + ESMA Liquidity Guidelines (ESMA/2020/1444) | `Daily` = reembolso UCITS estándar; `Weekly`/`Bi-Weekly` = UCITS menos líquido; `Monthly` = cerrado/ilíquido |
| `Distribution_Frequency` | Prospecto del fondo + reglas de divulgación ESMA | Acumulativo (retenido) vs distribución (pagado) |
| `Replication_Method` | ESMA ETF Guidelines (ESMA/2012/832) | `Physical` = compra todos los constituyentes del índice; `Sampling` = mantiene subconjunto; `Synthetic` = usa swaps; `Not Applicable` = no-índice |

---

**FIN MODELO SEMÁNTICO**

*Creado 2026-07-12. Fuente: `SEMANTIC_MODEL_CLASSIFICATION.md` (ahora `legacy_`), extendido y reorganizado.*  
*Clusters reordenados A→F. §6 Implementation guidance movida a `NORMAS_IMPLEMENTACION.md` §3.*  
*§3g/§8-§9 cross-cutting simplificados: principios canónicos en `PRINCIPIOS_DISENO.md` P#8 y P#10.*
