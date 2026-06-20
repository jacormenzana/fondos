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

## 7. GESTIÓN DE ERRORES Y LOGGING (v2 — VIGENTE DESDE 30-ABR-2026)

Esta sección sustituye a la versión esquelética anterior. La normativa surge del
ciclo del 30-abr-2026, donde los warnings añadidos visibilizaron tres bugs
ocultos (Theme='Inflación', logging duplicado, regresión Fund_Nature=NULL) que
hubieran contaminado P3 silenciosamente.

### 7.1 Principio fundamental

Todo evento que cumpla CUALQUIERA de los siguientes criterios DEBE emitir log:

a) Una regla INTER detecta inconsistencia y aplica autocorrección.  
b) Una regla INTER detecta inconsistencia y NO puede corregir (residual con DQ=WARN).  
c) Un valor cae en autocorrección por defecto (fallback heurístico).  
d) Una decisión de clasificación se toma con confianza < umbral (≤3 atributos).  
e) Una validación de catálogo (ALLOWED_VALUES_BY_COLUMN) falla.  
f) Un atributo NOT NULL del schema recibe valor None tras procesamiento.  
g) Una operación de extracción (parser) devuelve None donde se esperaba valor.  
h) Un bloque clasificador retorna sin asignar Fund_Nature, Profile, Type, Strategy o Family.  
i) El UPSERT usa COALESCE preservando valor BD distinto al record entrante (cambio silente).

### 7.2 Niveles de severidad — convención obligatoria

| Nivel   | Cuándo se emite                                       | Acción del pipeline                                  |
|---------|-------------------------------------------------------|------------------------------------------------------|
| ERROR   | Datos críticos faltantes; el fondo no se persiste     | Logear; el fondo queda con su estado anterior en BD  |
| WARNING | Inconsistencia detectada Y autocorregida; o residual  | Logear; persistir con valor corregido o flag DQ=WARN |
| INFO    | Inferencia exitosa por fallback; trazabilidad         | Logear; persistir con valor inferido                 |
| DEBUG   | Diagnóstico interno, no visible en log de producción  | Configurable por nivel                               |

**Criterio de decisión rápida:**
- ¿El fondo se persiste con datos coherentes? → WARNING (más DQ=WARN si aplica).
- ¿El fondo NO puede persistirse o tiene datos críticos perdidos? → ERROR.
- ¿La incidencia es informativa (decisión por fallback)? → INFO.

### 7.3 Convención de tags

**Formato obligatorio** para reglas INTER documentadas en backlog:

```
[BL-XX] [ISIN] mensaje
```

donde XX es el número del BL en backlog (BL-01 a BL-99).

**Ejemplos válidos:**
```
[BL-44] LU1133289592 Nature_efectivo=Monetario incompatible con SRRI_efectivo=3 → Restantes
[BL-62] LU0907915168 Family=Mixtos Type=Allocation inferidos léxicamente tras BL-44 → Restantes
[BL-44] LU0907915598 sin patrón léxico identificable; Family/Type=NULL; Data_Quality_Flag=WARN
```

**Para fallbacks no asociados a BL específico:** prefijo descriptivo entre corchetes.
```
[NORM-Profile-SRRI] LU0907915168 Profile=Conservador SRRI=5 → Dinámico
[NORM-Theme-Default] LU0123456789 Theme no detectado en KIID → Core/General
```

**Para errores estructurales:** prefijo ERROR-CATEGORÍA.
```
[ERROR-NotNull] LU0171298564 Fund_Nature=None tras BL-62 fallback → INSERT rechazado
[ERROR-Persistence] LU2267099674 UPSERT failed: foreign key constraint
```

**Tags antiguos sin guion** (`[BL44]`, `[BL62]`) **están desestimados**.

### 7.4 Reglas anti-duplicación

**a)** Las funciones de validación master DEBEN ser puras (sin logging interno).
   El logging vive exclusivamente en el wrapper que las invoca.

**b)** Si una regla puede dispararse desde múltiples puntos del pipeline, DEBE
   invocarse desde un único punto canónico (consistente con R-1).

**c)** Cualquier evento debe loguearse exactamente una vez por incidencia
   (un fondo, un evento, una línea de log).

**d)** Si una función puede invocarse desde múltiples wrappers que también logueen,
   ELLA debe ser pura. Los wrappers son los responsables.

### 7.5 Resumen de ciclo obligatorio

El pipeline DEBE emitir al final de cada ciclo un resumen agregado por tag:

```
--- RESUMEN DE INCIDENCIAS DEL CICLO ---
[WARN] BL44_NATURE_SRRI_R4: N fondos
[INFO] BL47_SFDR_DEFAULT: N fondos
...
---
```

Esto facilita validación posterior sin revisar log línea a línea.

### 7.6 Métricas de monitorización (control SQL)

**Cada regla INTER documentada en backlog DEBE tener:**

a) **Control SQL "antes del fix"** — qué retorna en estado defectuoso.  
b) **Control SQL "después del fix esperado"** — qué retorna tras corrección.  
c) **Tag de log distintivo** para cuantificar disparos por ciclo.

Sin estos tres elementos, no se debe abrir un BL en backlog.

### 7.7 Cobertura mínima por módulo

| Módulo                    | Log mínimo obligatorio                                              |
|---------------------------|---------------------------------------------------------------------|
| `pipeline.py`             | Inicio/fin de bloque, BL disparos universales, resumen de ciclo     |
| `classify_utils.py`       | apply_semantic_validation (warnings), inferencias por fallback      |
| `sqlite_writer.py`        | UPSERT con flags forzados, normalizaciones EN→ES aplicadas          |
| `kiid_parser.py`          | Atributos NO detectados con patrones esperados (señal de regresión) |
| `fund_characterizer.py`   | Atributos enriquecidos por fallback (no por extracción directa)     |
| `benchmark_normalizer.py` | Benchmarks no reconocidos (señal de catálogo obsoleto)              |
| `fund_family_builder.py`  | Familias inconsistentes (Nature mixta), correcciones aplicadas      |
| `srri_v4_geometric.py`    | Detecciones VISUAL_ONLY donde el textual también está poblado       |
| `blocks/*.py`             | Clasificaciones con SRRI=None (Capa 3 fallback no funcional)        |
| `blocks/restantes.py`     | Detección por capa (cuál de las 3 disparó); confianza baja          |

### 7.8 Implementación incremental — orden de despliegue

**Ola 1 (Sprint A.1.b — completado):** `pipeline.py`, `classify_utils.py`, `sqlite_writer.py`, `restantes.py`.

**Ola 2 (Sprint A.2):** `monetarios.py`, `rf_corto.py`, `rf_flexible.py`, `renta_variable.py`, `mixtos.py`, `alternativos.py`.

**Ola 3 (Sprint A.3):** `kiid_parser.py`, `benchmark_normalizer.py`, `srri_v4_geometric.py`, `fund_characterizer.py`.

---

## RESUMEN DE APLICACIÓN

| Principio | Se aplica en | Validación |
|-----------|--------------|------------|
| #1 COALESCE | sqlite_writer.py, cualquier INSERT/UPDATE | Verificar ON CONFLICT con COALESCE |
| #2 Root cause | Todo debugging, fix de bug | ¿Se eliminó la causa o solo el síntoma? |
| #3 Verificar ficheros | Modificación de código | ¿Leíste el archivo con view tool? |
| #4 Regime-aware | P3 scoring (futuro) | ¿Métricas condicionadas a régimen? |
| #5 Señales genéricas | classify_utils.py, kiid_parser.py | ¿Hay hardcoded de nombres de fondo? |
| #6 SRRI no fallback | classify_utils.py, bloques P1 | ¿SRRI usado para Nature/Type? |
| #7 Fix en módulo | fund_family_builder, clasificación | ¿SQL ad-hoc o código Python? |
| **#7-Logging** | Todos los módulos | ¿Logging según normativa sección 7 v2? |

---

## CONSECUENCIAS DE VIOLACIÓN

**Violación de principio → Fix rechazado**

Si una propuesta de solución viola alguno de estos principios:
1. Se rechaza la propuesta
2. Se solicita rediseño acorde a principios
3. Se documenta el principio violado y su razón

---

**FIN PRINCIPIOS DE DISEÑO**

*Última actualización: 30 abril 2026 — Sección 7 reemplazada por v2 (normativa logging completa, Sprint A.1.b)*  
