# PRINCIPIOS DE DISEÑO — No Negociables

**Propósito:** Reglas fundamentales que guían todas las decisiones técnicas del proyecto  
**Aplicación:** Obligatoria en todo desarrollo, debugging, y refactorización  
**Consecuencia de violación:** Fix rechazado, requiere rediseño

---

## PRINCIPIO #1: COALESCE es obligatorio para preservar información

**Regla:**  
En SQLite, todo campo de texto extraído del KIID debe usar `COALESCE(excluded.columna, columna)` en la cláusula `ON CONFLICT` para **nunca sobreescribir con NULL** valores previamente extraídos.

**Razón:**  
Un ciclo en estado `CACHED` (sin descarga HTTP) no re-extrae información del KIID. Si el código de escritura no usa COALESCE, todos los campos extraídos se sobrescribirían con NULL, perdiendo información valiosa de ciclos anteriores.

**Ejemplo correcto (`sqlite_writer.py`):**

```python
# CORRECTO - Preserva valor anterior si nuevo es NULL
INSERT INTO fund_kiid_metadata (ISIN, KIID_Class, Raw_KIID_Text, Language, SRRI_Textual)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(ISIN, KIID_Class) DO UPDATE SET
    Raw_KIID_Text = COALESCE(excluded.Raw_KIID_Text, Raw_KIID_Text),
    Language = COALESCE(excluded.Language, Language),
    SRRI_Textual = COALESCE(excluded.SRRI_Textual, SRRI_Textual),
    KIID_Downloaded_At = COALESCE(excluded.KIID_Downloaded_At, KIID_Downloaded_At);
```

**Ejemplo incorrecto:**

```python
# INCORRECTO - Sobrescribe con NULL en ciclos CACHED
ON CONFLICT(ISIN, KIID_Class) DO UPDATE SET
    Raw_KIID_Text = excluded.Raw_KIID_Text,  -- ❌ Puede ser NULL en CACHED
    Language = excluded.Language,            -- ❌ Se pierde info anterior
    SRRI_Textual = excluded.SRRI_Textual;    -- ❌ Degradación de datos
```

**Excepciones:**
- `SRRI_Visual`: Se regenera en cada ciclo (incluso CACHED), no requiere COALESCE
- `KIID_Status`: Tiene lógica especial (nuevo=CACHED preserva previo si era OK)

**Consecuencia histórica:**  
Bug P06 (31-mar-2026): `KIID_Downloaded_At` se sobrescribía con NULL en ciclos CACHED, dejando 478 fondos con timestamp perdido. Fix: añadir COALESCE.

---

## PRINCIPIO #2: Root cause analysis > parches de síntomas

**Regla:**  
Toda corrección de bug o disfunción debe identificar y resolver la **causa raíz**, no mitigar los síntomas. Los parches temporales están prohibidos.

**Razón:**  
Los parches crean deuda técnica, bugs recurrentes, y hacen el sistema impredecible. La única solución aceptable es la que elimina el problema estructuralmente.

**Ejemplo correcto (Bug P01: SRRI_Validation_Status inflado):**

```
SÍNTOMA: 220 fondos con SRRI_Validation_Status='VISUAL_ONLY' pero SRRI_Textual poblado
         (debería ser MATCH o CONFLICT)

❌ PARCHE (rechazado):
   UPDATE fund_kiid_metadata 
   SET SRRI_Validation_Status='MATCH' 
   WHERE SRRI_Visual = SRRI_Textual AND SRRI_Validation_Status='VISUAL_ONLY';

✓ ROOT CAUSE FIX (aplicado):
   1. Diagnóstico: srri_v4_geometric.py genera detección visual espuria
      en fondos Robeco por "blob" residual en esquina PDF
   2. Causa raíz: MAX_BAND_ITER=30 demasiado permisivo, escanea zonas fuera
      del área esperada de SRRI
   3. Fix estructural: MAX_BAND_ITER=15 (limita escaneo a área razonable)
   4. Validación: Re-ejecutar pipeline → 64 fondos encolados FORCE_REFRESH
      para recalcular SRRI_Visual correctamente
```

**Consecuencia de aplicar parche:**  
El síntoma desaparece pero la causa permanece. Próximo PDF con layout similar volvería a generar detección espuria. Fix estructural previene recurrencia.

---

## PRINCIPIO #3: Verificar ficheros de producción antes de modificar

**Regla:**  
Antes de modificar cualquier archivo de código en producción, **leer el archivo actual** para confirmar su contenido, estructura, y estado. Nunca asumir contenido sin verificar.

**Razón:**  
Los archivos pueden haber sido modificados manualmente, parcheados en ciclos anteriores, o tener versiones desactualizadas. Modificar sin verificar puede introducir regresiones o duplicar lógica.

**Ejemplo correcto:**

```
Usuario: "Añade soporte para detectar 'derivatives' en inglés en classify_utils.py"

Claude:
  1. [Llama view tool en classify_utils.py]
  2. [Lee función detect_derivatives(), líneas 450-520]
  3. [Verifica que NO existe patrón EN para 'derivatives']
  4. [Propone fix quirúrgico añadiendo patrón EN sin tocar lógica ES existente]
```

**Ejemplo incorrecto:**

```
Usuario: "Añade soporte para detectar 'derivatives' en inglés en classify_utils.py"

Claude (SIN leer archivo):
  "Aquí está la función actualizada:
   def detect_derivatives(text):
       # [código completo regenerado desde cero]
       # ❌ Perdiste optimizaciones previas
       # ❌ Introdujiste bugs ya corregidos
       # ❌ Rompiste lógica que funcionaba"
```

**Validación:**  
Después de modificar, ejecutar validación sintáctica (`ast.parse()` para Python, verificar nombres de columnas para SQL) antes de entregar.

---

## PRINCIPIO #4: Scoring condicional a régimen macro (no global)

**Regla:**  
Las métricas de performance de fondos deben evaluarse **condicionadas al régimen macroeconómico** vigente, no sobre el historial completo sin contexto.

**Razón:**  
Un fondo monetario evaluado en el período 2015-2025 (tasas de interés ~0%) aparecerá sistemáticamente como bajo rendimiento, cuando su función es preservación en entornos de tasas bajas. En un régimen de tasas altas (2022+), ese mismo fondo es óptimo para su naturaleza.

**Ejemplo incorrecto (scoring global):**

```python
# ❌ Penaliza monetarios por performance en era de tipos 0%
def score_fund(fund):
    return_5y = fund.return_last_5_years()  # 2020-2025: tasas ~0%
    sharpe_5y = fund.sharpe_last_5_years()
    score = 0.6 * return_5y + 0.4 * sharpe_5y
    return score

# Monetarios obtienen score bajo porque 2020-2023 era de tipos 0%
# Pero en 2024-2025 (tipos >4%) son óptimos para preservación
```

**Ejemplo correcto (scoring regime-aware):**

```python
# ✓ Evalúa monetarios solo en períodos de tipos altos
def score_fund_regime_aware(fund, current_regime):
    if fund.nature == 'Monetario':
        # Solo considerar períodos históricos con régimen similar
        relevant_periods = filter_by_regime(fund.history, regime='high_rates')
        return_relevant = fund.return_in_periods(relevant_periods)
        sharpe_relevant = fund.sharpe_in_periods(relevant_periods)
    else:
        # Lógica para otros tipos de fondos
        ...
    
    # Ponderar más períodos recientes (ventana móvil)
    score = weighted_score(return_relevant, sharpe_relevant, recency_weight=0.7)
    return score
```

**Estado actual:**  
P3 (scoring y selección) está diseñado pero no implementado. Framework regime-aware de 5 fases está documentado en `TRASPASO_CONTEXTO_APR2026.md` sección 10.

---

## PRINCIPIO #5: Señales genéricas > nombres específicos de fondo

**Regla:**  
La lógica de clasificación debe basarse en **señales semánticas genéricas** (contenido del KIID, patrones de texto, estructura documental), **nunca en nombres específicos de fondos** o gestoras.

**Razón:**  
Hardcodear nombres de fondos crea un sistema frágil, no escalable, y con bugs latentes. Cada nuevo fondo requeriría actualización manual. La clasificación debe ser generalizable.

**Ejemplo incorrecto:**

```python
# ❌ Anti-patrón: hardcodear nombres de fondos
def classify_geography(fund_name, kiid_text):
    if 'JPMorgan US Value' in fund_name:
        return 'EE.UU.'
    elif 'Robeco European Stars' in fund_name:
        return 'Europa'
    elif 'DWS Top Dividende' in fund_name:
        return 'Alemania'
    # ... ❌ Lista infinita de casos especiales
```

**Ejemplo correcto:**

```python
# ✓ Señales semánticas genéricas
def detect_geography(kiid_text):
    patterns_us = [
        r'\b(estados?\s+unidos?|usa?|norte[\s-]?american[oa])\b',
        r'\b(s&p\s*500|russell\s*\d{4}|nasdaq)\b',
        r'\b(acciones?\s+estadounidenses?)\b'
    ]
    patterns_europe = [
        r'\b(europ[ae][oa]s?|zona\s+euro|eurozona)\b',
        r'\b(euro\s*stoxx|msci\s+europe)\b',
        r'\b(bolsas?\s+europeas?)\b'
    ]
    
    if any(re.search(p, kiid_text, re.I) for p in patterns_us):
        return 'EE.UU.'
    elif any(re.search(p, kiid_text, re.I) for p in patterns_europe):
        return 'Europa'
    # ... patrones genéricos reutilizables
```

**Consecuencia histórica:**  
Varios bugs de clasificación se resolvieron eliminando referencias a nombres específicos y reemplazándolas con señales semánticas (ventana DDF [500:5000], patrones de benchmark, etc.).

---

## PRINCIPIO #6: SRRI no puede ser fallback de clasificación

**Regla:**  
El SRRI (nivel de riesgo 1-7) **no puede usarse como criterio de clasificación** para determinar `Fund_Nature`, `Type`, `Profile`, u otros atributos estructurales del fondo.

**Razón:**  
El SRRI mide **volatilidad histórica**, no naturaleza del activo. Un fondo de Renta Variable con SRRI=3 sigue siendo Renta Variable, no Mixto. Un monetario con SRRI=2 por error técnico no se convierte en RF Corto Plazo.

**Ejemplo incorrecto:**

```python
# ❌ Usar SRRI como fallback de clasificación
def classify_fund_nature(kiid_text, srri):
    nature = detect_nature_from_text(kiid_text)
    
    if nature is None:  # No detectado en texto
        # ❌ INCORRECTO: clasificar por SRRI
        if srri <= 2:
            return 'Monetario'
        elif srri <= 4:
            return 'Renta Fija'
        elif srri <= 7:
            return 'Renta Variable'
    
    return nature
```

**Ejemplo correcto:**

```python
# ✓ SRRI solo informa Profile, no Nature
def classify_fund(kiid_text, srri):
    # Nature: Solo desde contenido semántico
    nature = detect_nature_from_text(kiid_text)
    
    # Profile: Puede usar SRRI como señal secundaria
    if srri is not None:
        if srri <= 2:
            profile = 'Muy Conservador'
        elif srri <= 4:
            profile = 'Conservador'
        elif srri <= 5:
            profile = 'Moderado'
        else:
            profile = 'Agresivo'
    else:
        profile = None
    
    return {
        'Fund_Nature': nature,  # Nunca derivado de SRRI
        'Profile': profile      # Puede usar SRRI
    }
```

**Relación SRRI ↔ Profile:**  
El SRRI **domina** la asignación de `Profile` cuando está disponible, pero `Profile` es un atributo **separado** de `Fund_Nature`. La clasificación estructural (Nature → Type → Subtype) debe basarse en contenido semántico del KIID.

### Alcance y excepción acotada (2026-07-17): SRRI *declarado* vs. volatilidad *realizada*

Este principio se refiere al **SRRI declarado** — el indicador de riesgo 1-7 auto-reportado
en el KIID. Es una etiqueta, puede ser errónea o estar desalineada, y **nunca** deriva `Fund_Nature`.

La **volatilidad realizada** (`fund_metrics.srri_nav`, calculada por P2 a partir del histórico de
NAV y bandeada 1-7 según las bandas CESR/ESMA) es una señal **distinta e independiente**: comportamiento
de mercado medido, no auto-declarado. `resolve_nature_evidence` (clasificador ponderado por evidencia,
`classify_utils.py`) la usa bajo una **excepción acotada y verificada** a este principio:

- **PERMITIDO** — la volatilidad realizada **VETA** una naturaleza incompatible con la banda
  (p.ej. `Monetario` a SRRI 6-7 es incompatible con MMFR) y **ARBITRA** entre naturalezas que **otras
  señales documentales ya propusieron** (KIID / nombre / benchmark). En bandas inequívocas {1,6,7}
  (la volatilidad admite una sola clase de activo) el arbitraje cubre también inconsistencia adyacente.
- **PROHIBIDO** — **derivar** una `Fund_Nature` que ninguna señal documental propuso. No hay fallback
  "banda de volatilidad → naturaleza": si todas las señales ex-ante abstienen, el resultado es `None`
  (→ `Restantes`), nunca una conjetura por volatilidad. Si la primaria es incompatible con la banda
  pero **ningún** candidato ex-ante encaja, se **mantiene** la primaria con confianza baja
  (`NATURE_LOW_CONFIDENCE` DQ WARN), no se fabrica una respuesta.

**Regla operativa:** el KIID (evidencia documental) es la fuente **primaria** de `Fund_Nature`; la
volatilidad realizada sólo **restringe/desempata**, nunca **crea**. Esto preserva la sustancia de P#6
(volatilidad ≠ naturaleza del activo) permitiendo a la vez vetar clasificaciones físicamente imposibles.
Ver el feedback P1←P2 acotado en `AGENTS.md` §Architecture.

---

## PRINCIPIO #7: Corrección en el módulo correcto (no SQL ad-hoc)

**Regla:**  
Las correcciones de clasificación, validación de familias, o cualquier lógica de negocio deben implementarse en el **módulo Python correspondiente**, no mediante queries SQL ad-hoc sobre la base de datos.

**Razón:**  
Las correcciones SQL son volátiles, no trazables, no reproducibles, y se pierden en el próximo ciclo del pipeline. El código es la fuente de verdad, la BD es el resultado.

**Ejemplo incorrecto (Bug: 9 familias con Fund_Nature inconsistente):**

```sql
-- ❌ Corrección SQL ad-hoc (se pierde en próximo ciclo)
UPDATE fund_master 
SET Fund_Nature = 'Renta Variable'
WHERE fund_family_id = 'FAM_001697' 
  AND Fund_Nature = 'Mixtos';
```

**Ejemplo correcto:**

```python
# ✓ Fix en fund_family_builder.py (Regla 4)
def correct_family_inconsistencies(conn):
    inconsistent = get_50_50_families(conn)
    for family in inconsistent:
        correct_nature = infer_nature_from_family_name(family.family_name)
        update_family_nature(conn, family.family_id, correct_nature)
    return len(inconsistent)
```

**Excepción:**  
SQL directo es aceptable **solo** para:
- Marcar fondos para re-descarga (`UPDATE fund_kiid_metadata SET KIID_Status='FORCE_REFRESH' WHERE ISIN='...'`)
- Consultas de análisis/debugging (SELECT)
- Migraciones de schema una sola vez (scripts idempotentes en `scripts/mig/`)

---

## PRINCIPIO #8: Homogeneidad lingüística por columna

**Regla:**  
Cada columna categórica del schema usa **un único idioma** para todos sus valores.
No mezclar español e inglés en la misma columna.

| Idioma | Columnas |
|--------|---------|
| **Español** | `Fund_Nature`, `Profile`, `Geography` |
| **Inglés** | `Family`, `Investment_Focus`, `Theme`, `Sector_Focus`, `Style_Profile`, `Market_Cap_Focus`, `Exposure_Bias`, `Development_Status`, `Credit_Quality`, `Duration_Profile`, `MMF_Structure`, `Alt_Strategy`, `Hedging_Policy`, `Replication_Method`, `Derivatives_Usage`, `Liquidity_Profile`, `Distribution_Frequency` |
| **Código / neutro** | `ISIN`, `Fund_Currency`, `Asset_Currency`, `SRRI`, costes numéricos |

Ver tabla completa y nota de evolución histórica en `MODELO_SEMANTICO.md` §8.

**Razón:**  
Mezclar idiomas en una columna fragmenta poblaciones en `GROUP BY` / `WHERE` queries,
multiplica los paths de normalización, y genera falsos negativos en tests de regresión.

**Consecuencia histórica:**  
Antes de v20 (2026), `Family` estaba en español ('RV Núcleo', 'Renta Fija Flexible', etc.).
La migración a inglés ('Equity Core', 'Flexible Fixed Income') fue necesaria para alinear con
Morningstar Category naming. Los valores legacy en español deben ser migrados por
`sqlite_writer._normalize_record` (defensa en profundidad).

**Aplicación:**  
Los mapas de normalización ES→EN viven exclusivamente en `classify_utils.py` (R-1 en
`RESTRICCIONES_ARQUITECTURA.md`; instancia enforced de **P#11**). La copia en
`sqlite_writer._normalize_record` es la **única excepción autorizada** al DRY estricto (P#11) y
debe documentarse en cada definición.

---

## PRINCIPIO #9: Consistencia semántica entre atributos

**Regla:**  
Los atributos clasificatorios tienen restricciones cruzadas (pares, clusters). Las
inconsistencias detectadas deben corregirse, registrarse con DQ flag, o señalarse con
WARN — **nunca silenciarse**.

**Implementación:**  
Reglas INTER en `validate_all_semantic_consistency()` (`classify_utils.py`).
Ver catálogo completo SC-A1 → SC-F4 en `MODELO_SEMANTICO.md` §10.

**Razón:**  
Atributos inconsistentes producen señales contradictorias en P2/P3 y degradan el scoring
sin que el sistema lo registre. Una inconsistencia no registrada es deuda de datos invisible.

**Consecuencia:**  
Toda inconsistencia DEBE persistirse en `fund_data_quality_issues` con `check_code`
canónico (formato `SEM_<REGLA_UPPER>` — ver `NORMAS_IMPLEMENTACION.md` §5). Las funciones
de validación deben ser puras; el logging vive en el wrapper (`NORMAS_IMPLEMENTACION.md` §4.4).

---

## PRINCIPIO #10: Valor categórico "indeterminado por naturaleza" ≠ NULL

**Regla:**  
Un atributo extraído tiene **dos causas distintas** para no contener una categoría única,
y no deben confundirse:

1. **Indeterminado por naturaleza** — el KIID/nombre declara explícitamente que el fondo
   NO tiene un valor único (ej. mandato multi-divisa: *"euros u otras divisas"*). Es una
   propiedad **positiva y conocida**. → **valor categórico centinela** (no NULL).
2. **No descubierto** — no hay señal suficiente para determinar el valor. Es **ausencia de
   conocimiento**. → **`NULL`**.

Colapsar ambos en `NULL` destruye información e impide aislar las dos poblaciones aguas abajo.

**Implementación de referencia — `Asset_Currency` → centinela `MCY`:**

```python
# classify_utils.py — único punto de definición (R-1)
ASSET_CURRENCY_MULTI = "MCY"   # "Multi-CurrencY"; no colisiona con ISO-4217
```

También declarado en `DOMAIN_VALUES["Asset_Currency"]` (documentación de dominio).
Dos señales de emisión: token multi-divisa explícito en nombre, o continuación multi-divisa
en texto KIID sin divisa dominante limpia.

**Centinelas activos:**

| Atributo | Centinela | Significado |
|----------|-----------|-------------|
| `Asset_Currency` | `MCY` | Mandato explícitamente multi-divisa |
| `Geography` | `Global` | Universo explícitamente global |
| `Investment_Focus` | `Broad` | Sin concentración sector/temática |

**Razón:**  
- **Compatibilidad con COALESCE (P#1):** `None` no puede sobrescribir un valor previo
  (COALESCE lo preserva). Un centinela sí es no-nulo y sobrescribe correctamente un valor
  heredado ya no vigente.  
- **Aislamiento de poblaciones:** `WHERE Asset_Currency = 'MCY'` devuelve exactamente los
  fondos multi-divisa por diseño, no los "sin dato".  
- **Root cause (P#2):** emitir el centinela desde el clasificador es la corrección
  estructural; parchear con SQL o listas de ISINs es un parche de síntoma prohibido.

**Regla de extensión:**  
Al añadir cualquier atributo categórico, decidir si admite *"diverso/indeterminado por
naturaleza"*. Si lo admite y no existe ya un valor que lo capture, definir un centinela,
declararlo en `DOMAIN_VALUES`, emitirlo desde el clasificador (nunca por SQL), y proteger
los consumidores que asumen valor único.

---

## PRINCIPIO #11: Escalabilidad y Principio DRY (Don't Repeat Yourself)

**Regla:**  
Maximiza la reusabilidad aplicando siempre una arquitectura modular. Está **estrictamente
prohibido duplicar lógica de negocio o crear lógicas similares en distintos módulos**. Cuando
detectes requerimientos iguales o parecidos en múltiples áreas, tu deber es diseñar e implementar
un módulo o pieza de software genérica que **centralice** esa funcionalidad, e importarla desde
todos los puntos de uso.

**Razón:**  
La duplicación de lógica es la causa estructural de una gran parte de los defectos históricos: dos
copias de un mapa/regla divergen con el tiempo, se corrige una y no la otra, y el bug reaparece.
La normalización lingüística es el caso paradigmático — la duplicación de mapas EN→ES en varios
módulos causó ~50 % de los defectos lingüísticos (BL-22, BL-53, BL-54). DRY generaliza esa
lección a **toda** lógica de negocio, no solo a los mapas de normalización.

**Emparejamiento con P#2:**  
En las instrucciones fundacionales del proyecto, DRY y *Root cause analysis* (P#2) eran los dos
principios rectores. Se mantienen como pareja: P#2 exige eliminar la causa, no el síntoma; P#11
exige eliminarla **en un único lugar**, no replicar el fix.

**Instancia canónica enforced — R-1:**  
`RESTRICCIONES_ARQUITECTURA.md` **R-1** («punto único de normalización lingüística») es la
instancia concreta y verificable de este principio: los mapas categóricos viven exclusivamente en
`classify_utils.py`. La copia en `sqlite_writer._normalize_record` es la **única excepción
autorizada** al DRY estricto (defensa en profundidad) y debe documentarse en cada definición
(ver P#8).

**Regla de extensión:**  
Antes de escribir una función/constante/mapa, verificar (`grep`) si ya existe una equivalente. Si
existe, importarla; si existe *parcialmente* en varios sitios, consolidarla en el módulo genérico
correspondiente antes de añadir el nuevo caso. Nunca resolver un requisito nuevo copiando y
adaptando lógica existente en otro módulo.

---

## Normativa de logging → `NORMAS_IMPLEMENTACION.md` §4

La normativa de logging (criterios obligatorios, niveles de severidad, convención de tags,
reglas anti-duplicación, cobertura mínima por módulo, despliegue incremental) vive en
`NORMAS_IMPLEMENTACION.md` §4 (versión vigente: v2, 30-abr-2026).

---

## RESUMEN DE APLICACIÓN

| Principio | Se aplica en | Validación |
|-----------|--------------|------------|
| #1 COALESCE | `sqlite_writer.py`, cualquier INSERT/UPDATE | ¿ON CONFLICT usa COALESCE? |
| #2 Root cause | Todo debugging, fix de bug | ¿Se eliminó la causa o solo el síntoma? |
| #3 Verificar ficheros | Modificación de código | ¿Leíste el archivo antes de modificar? |
| #4 Regime-aware | P3 scoring | ¿Métricas condicionadas al régimen macro? |
| #5 Señales genéricas | `classify_utils.py`, `kiid_parser.py` | ¿Hay nombres de fondo hardcodeados? |
| #6 SRRI no fallback | `classify_utils.py`, bloques P1 | ¿SRRI usado para inferir Nature/Type? |
| #7 Fix en módulo | `fund_family_builder`, clasificadores | ¿SQL ad-hoc o código Python? |
| #8 Homogeneidad lingüística | Todos los módulos que emiten atributos | ¿Valores en idioma correcto por columna? |
| #9 Consistencia semántica | `classify_utils.py`, `pipeline.py` | ¿Inconsistencias en `fund_data_quality_issues`? |
| #10 Centinela vs NULL | Clasificadores, `fund_characterizer.py` | ¿Diversidad explícita usa centinela, no NULL? |
| #11 Escalabilidad y DRY | Todos los módulos; `classify_utils.py` (R-1) | ¿Se duplicó lógica en vez de centralizarla e importarla? |

---

## CONSECUENCIAS DE VIOLACIÓN

**Violación de principio → Fix rechazado**

Si una propuesta de solución viola alguno de estos principios:
1. Se rechaza la propuesta
2. Se solicita rediseño acorde a principios
3. Se documenta el principio violado y su razón

---

**FIN PRINCIPIOS DE DISEÑO**

*Última actualización: 2026-07-12 — Añadidos P#8 (Homogeneidad lingüística), P#9 (Consistencia semántica), P#10 (Centinela vs NULL), P#11 (Escalabilidad y DRY — elevado desde R-1 / instrucciones fundacionales). Normativa logging movida a `NORMAS_IMPLEMENTACION.md` §4.*  
