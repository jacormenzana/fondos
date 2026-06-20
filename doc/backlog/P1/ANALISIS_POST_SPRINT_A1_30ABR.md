# Análisis post-Sprint A.1 — Ciclo 30-abr-2026

**Fecha:** 30 de abril de 2026
**Log analizado:** `log_pipeline_20260430_082015.log`
**Export analizado:** `p1_export_20260430.xlsx`
**Sprint implementado:** SPRINT_A1_BL44_BL62_BL64.md (entregado 29-abr)

---

## 1. ANÁLISIS DEL IMPACTO DEL SPRINT A.1 SOBRE LAS CAUSAS RAÍZ

### 1.1 Hallazgo principal: el sprint no se implementó conforme a la especificación

Tras revisar el código actual de `pipeline.py`, `classify_utils.py` y `sqlite_writer.py`, y cruzarlo con el log del ciclo, hay tres divergencias materiales entre lo especificado y lo implementado, que explican la totalidad de los errores observados.

**Divergencia 1 — Sprint A.1 sección 2.1 (BL-44 con R-4): NO IMPLEMENTADO.**

La especificación obligaba a leer `Fund_Nature` y `SRRI` con fallback a BD para obtener el valor efectivo (patrón `_X_eff = record.get('X') or _X_bd`). El código real en `pipeline.py:683-694` lee únicamente del record entrante:

```python
# Real en pipeline.py:683 (SIN R-4):
_nat44 = fund_master_record.get("Fund_Nature")
if _srri_val is not None:
    _reclasify44 = (
        (_nat44 == "Monetario" and _srri_val >= 3)
        or (_nat44 == "Renta Fija Corto Plazo" and _srri_val >= 4)
    )
    if _reclasify44:
        ...
        fund_master_record["Fund_Nature"] = "Restantes"
```

No hay lectura BD. No hay `_nat44_eff = ... or _nat_bd`. La causa raíz que el sprint pretendía cerrar (regla escapando para fondos CACHED) sigue intacta.

**Divergencia 2 — Sprint A.1 sección 2.2 (BL-62 propagación): NO IMPLEMENTADO.**

`grep -rn "BL62\|propagate_nature_to_restantes\|LEXICAL_FAMILY_INFERENCE_BL62"` sobre todos los módulos de código devuelve cero ocurrencias. La función especificada nunca se añadió. El catálogo léxico de 30+ patrones nunca se incorporó a `classify_utils.py`. Sin embargo, el log muestra mensajes `[BL62]` con texto coherente con la spec ("inferidos léxicamente tras BL-44 → Restantes"), lo que indica que **se implementó algo similar pero en otro módulo no incluido en el upload** (probablemente algún `blocks/restantes.py` o un módulo intermedio). Eso por sí solo viola R-1 (centralización en `classify_utils`) y R-3 (no duplicar lógica en bloques).

**Divergencia 3 — Sprint A.1 sección 2.3 (BL-64 sobrescritura forzada): NO IMPLEMENTADO.**

`sqlite_writer.py` no detecta los flags `_bl44_force_overwrite` ni `_bl62_force_overwrite_*`. La cláusula UPSERT para `Fund_Nature`, `Type`, `Family` (líneas 354-360) es sobrescritura directa **sin COALESCE**: `Fund_Nature = excluded.Fund_Nature`. Esto es la causa estructural del nuevo error — explicado en sección 2 abajo.

### 1.2 Lo que sí cambió respecto a mi diseño original

El log muestra dos comportamientos no presentes en mi spec, que sugieren una variante del fix BL-62 que se implementó por otra vía:

- **`naturaleza no determinable, Fund_Nature=NULL`**: cuando la inferencia léxica falla, en vez de poner `Fund_Nature='Restantes'` con `Type=NULL`, `Family=NULL` y `Data_Quality_Flag='WARN'` (mi spec sección 2.2 Fase 3), el código pone **`Fund_Nature=NULL`** directamente. Esto rompe la restricción NOT NULL del schema y produce los errores del ciclo.

- **`re-inferido: Renta Fija Flexible`**: cuando la inferencia léxica encuentra patrón, el código asigna **directamente esa Family/Nature** en lugar de `Fund_Nature='Restantes'`. Esto contradice explícitamente la decisión de diseño que tú aprobaste el 29-abr (opción A — re-clasificar Type/Family pero mantener Nature='Restantes' como reflejo de que BL-44 detectó la incoherencia).

La consecuencia: BL-44 ya no señala la presencia del problema (Nature='Restantes' como bandera). En lugar de eso, asigna Nature de forma "optimista" o NULL con error. Ambos comportamientos son semánticamente diferentes a lo aprobado.

### 1.3 Cobertura R-4 del bloque RESTANTES

Cuando un fondo procesado por el bloque RESTANTES atraviesa la regla BL-44 hoy:

- **Si dispara**: el comportamiento depende del fragmento de código no incluido (32 casos en log "re-inferido", varios casos "naturaleza no determinable").
- **Si no dispara**: queda como en versiones previas — `Fund_Nature='Renta Fija Corto Plazo'` con SRRI=3 stale en BD. La cobertura R-4 sigue rota.

El control SQL del sprint — `SELECT COUNT(*) FROM fund_master WHERE Fund_Nature IN ('Monetario','RFCP') AND CAST(SRRI AS INTEGER) >= 3` — necesita verificarse contra el export del 30 para cuantificar. El backlog de referencia decía 0 esperado tras el fix; sin la implementación R-4, es muy probable que siga >0.

---

## 2. ANÁLISIS DE LOS ERRORES Y WARNINGS DEL LOG

### 2.1 Problema 1 — `NOT NULL constraint failed: fund_master.Fund_Nature`

**Cuantificación aproximada del log**: del muestreo, identifico al menos **15-20 ocurrencias visibles**, pero hay un dato clave en el resumen final del pipeline:

```
Registros publicados: 2316
Total: 3204 fondos procesados
```

**888 fondos no se publicaron en este ciclo**. Esto excede con creces los 15-20 errores visibles. Hay dos lecturas posibles:

- **Lectura A**: 888 fondos fallaron por NOT NULL constraint (todos los procesados por bloque RESTANTES con BL-44 disparado y inferencia fallida).
- **Lectura B**: 888 fondos no se publicaron por otras razones (filtros previos, KIID stale, etc.) y los errores de constraint son solo una fracción.

Sin acceso al fichero log entero por bash (solo project_knowledge), no puedo distinguir. **Eso es información que conviene cuantificar como primer paso del sprint correctivo**: cuántos errores `NOT NULL` totales hay en el log, y cuántos fondos fallidos suman al gap de 888.

**El análisis de Sonnet identifica correctamente la causa inmediata** (BL-65 — entiendo que se refiere al mecanismo, no al BL del backlog — pone `Fund_Nature=NULL` y el schema lo rechaza), pero conviene matizar la solución:

> *Cita Sonnet:* "La solución correcta no es añadir un valor comodín de vuelta. La solución es que cuando BL-44 no puede re-inferir la naturaleza, el fondo no se escribe en fund_master en este ciclo — se deja como está en BD (conservando el valor incorrecto pero existente) y se registra en fund_ingestion_log para revisión manual."

**Discrepo parcialmente con esta solución.** "Dejar el fondo como está en BD conservando el valor incorrecto" deja un fondo `Renta Fija Corto Plazo` con `SRRI=3` en BD, lo cual es **exactamente la situación que BL-44 fue diseñado para detectar y corregir**. La solución de Sonnet nos lleva al estado pre-Sprint A.1 con un log adicional. La solución correcta es la que tú aprobaste el 29-abr y que la spec entregaba textualmente:

```python
# Sección 2.2 Fase 3 de la spec aprobada:
fund_record['Family'] = None
fund_record['Type'] = None
fund_record['_bl62_force_overwrite_family'] = True
fund_record['_bl62_force_overwrite_type'] = True
if fund_record.get('Data_Quality_Flag') != 'WARN':
    fund_record['Data_Quality_Flag'] = 'WARN'
# ── NOTA CRÍTICA: Fund_Nature DEBE quedarse en 'Restantes', NO NULL ──
# Restantes es el valor canónico que indica "BL-44 detectó incoherencia
# y no pudo re-clasificar". Es valor válido en el schema; no rompe NOT NULL.
```

El bug es que el código implementado puso `Fund_Nature=NULL` cuando debió poner `Fund_Nature='Restantes'`. Esa es la causa raíz de los errores `NOT NULL constraint failed`. **Una sola línea de código.**

**El segundo punto de Sonnet** (cosmético, "→ Restantes" en mensaje BL-62 mientras Family no termina en Restantes) es síntoma del mismo bug: el mensaje fue copiado de la spec correcta, pero la lógica que lo emite divergió. Cuando se corrija el código para mantener Nature='Restantes', el mensaje volverá a ser coherente.

### 2.2 Problema 2 — Warning duplicado `[???]` + `[NOMBRE]` para InvestmentUniverse-NatureFallback

**Causa raíz exacta** (ya identificada con código en `classify_utils.py`):

La función `validate_all_semantic_consistency()` línea 2672 hace:
```python
isin = cr.get("ISIN", "???")
```

Y a continuación, en líneas 2820-2845 emite warnings con `logger.warning("[%s] %s: %s", isin, ...)`. Si el dict `cr` (copia del record entrante) **no tiene la clave 'ISIN'** (típico cuando el record es un sub-dict construido durante clasificación, antes de añadir el ISIN como campo explícito), `cr.get("ISIN", "???")` devuelve el literal `"???"`.

El wrapper `apply_semantic_validation()` en línea 2909-2929 hace algo distinto:
```python
isin = record.get("ISIN", fund_name)
```

Si el record llegado a `apply_semantic_validation` tampoco tiene 'ISIN' como clave, usa el nombre del fondo. **Pero internamente, `apply_semantic_validation` invoca a `validate_all_semantic_consistency` (línea 2916), y dentro de esa función vuelve a leer ISIN del record copia y obtiene "???"**. Resultado: dos eventos de log para el mismo problema, uno con `[???]` (emitido dentro de `validate_all_semantic_consistency` desde su propio logger) y otro con `[NOMBRE]` (emitido desde `apply_semantic_validation` con el wrapper).

**Verificación visual del log**: cada par de líneas tiene exactamente el mismo timestamp y mensaje, una con `[???]` y otra con el nombre. Eso confirma que es la misma incidencia logueada dos veces, no dos eventos distintos. Sonnet identifica esto correctamente.

El **fix correcto** es uno de:
- **A (preferido)**: en `validate_all_semantic_consistency`, eliminar el logging interno (líneas 2856-2860) y dejar que solo el wrapper `apply_semantic_validation` haga logging. La función master debe ser pura — devolver el dict de validación, sin efectos secundarios.
- **B (contingencia)**: pasar el `isin` como parámetro explícito a `validate_all_semantic_consistency` para que no dependa de la clave del dict.

**Implicación más amplia**: este patrón (logging duplicado por separar lógica de la función master del wrapper) probablemente afecta a otras reglas INTER que también se emiten desde la misma función. Lo veremos al auditar.

### 2.3 Problema 3 — Theme='Inflación' no permitido (homogeneidad lingüística)

**Causa raíz** (verificada en código):

`classify_utils.py:1372` en función `_detect_theme` retorna el literal **`"Inflación"`** (con tilde, español). Pero `ALLOWED_VALUES_BY_COLUMN["Theme"]` (línea 2235) tiene **`"Inflation"`** (inglés). Cuando el record pasa por `validate_all_semantic_consistency`, el bucle de la línea 2847-2853 emite warning porque el valor no está en la lista permitida.

Esto incumple el Principio #8 documentado en `PRINCIPIOS_DISENO.md`: "Theme — Inglés — Technology, Healthcare, Climate, etc." (cita literal del documento). El Theme debe estar en inglés y `_detect_theme` está emitiendo en español.

Hay **otro bug subyacente** que conviene anticipar: línea 153 lista `"Inflación"` entre las palabras de detección, mientras que el output esperado del catálogo es `"Inflation"`. La función probablemente detecta correctamente la palabra clave (input puede estar en español) pero devuelve el output en español en lugar de traducirlo.

**Fix**: en `classify_utils.py:1372`, cambiar `return "Inflación"` por `return "Inflation"`. Adicionalmente, auditar la función `_detect_theme` completa para verificar que **todos** los valores que retorna están en inglés (los valores permitidos son lista cerrada en línea 2230-2237). Y revisar el comentario de la línea 1769 ("BL-23: Inflation — prevenir Theme en español 'Inflación'") que sugiere que este bug ya fue identificado en BL-23 pero no se corrigió en este punto del código.

### 2.4 Análisis del FamilyBuilder al cierre

El log informa al final:

```
[FamilyBuilder] Inconsistencias encontradas: 10
[FamilyBuilder]   Corregibles (regla escalable): 3
[FamilyBuilder]   Heterogeneidad estructural:    2
[FamilyBuilder]   No determinables:              5
[FamilyBuilder] 3 correcciones aplicadas
[FamilyBuilder] AVISO: 7 familias con Fund_Nature inconsistente (ver log)
```

7 familias inconsistentes residuales (FAM_000945, FAM_000946, FAM_001699, FAM_001778, FAM_001900, FAM_002124, FAM_002320). Comparando con el ciclo del 29-abr (también 7 familias documentadas), no hay regresión, pero **tampoco progreso**. Esto es coherente con BL-60 documentado en backlog v3.4 ("5 bipartitas con empate total de calidad"). Las 2 nuevas (FAM_001699, FAM_001900) son mixed RV/Alternativo del mismo emisor (JPM US Value, Thematics Safety) — patrón conocido.

### 2.5 Resultado neto del sprint

**Cuantitativo**:
- BL-44 cobertura R-4: **No implementado**. Probable que el control SQL siga >0.
- BL-62 propagación: **Implementado parcialmente** en módulo no centralizado (viola R-1/R-3). Comportamiento divergente de la decisión aprobada (NULL en lugar de 'Restantes' Nature).
- BL-64 sobrescritura forzada: **No implementado**. Cláusulas UPSERT actuales (línea 354-367) sobrescriben directamente sin COALESCE para Nature/Type/Family — efecto colateral: cuando BL-44 mete Fund_Nature=NULL, el INSERT falla por NOT NULL constraint. **Es paradójicamente lo que está produciendo los errores**.
- 888 fondos no publicados (Total: 3204, Publicados: 2316). Magnitud por confirmar.

**Cualitativo**:
- El sprint introduce nuevos errores donde antes había datos incorrectos pero presentes. Esto es regresión funcional aunque la intención del sprint fuera mejora.
- El fix correcto, recordando la spec, son **3 cambios pequeños** (mantener Nature='Restantes', no NULL; añadir lectura BD R-4; añadir flags force_overwrite y respetar en sqlite_writer). El alcance del sprint no exigía nada más complejo. La implementación de Sonnet introdujo lógica adicional ("re-inferido directo a Family") que no estaba especificada.

---

## 3. MÓDULOS RELACIONADOS CON LOS ERRORES — PARA EL SIGUIENTE SPRINT

Para el sprint correctivo, te recomiendo subir al proyecto los siguientes módulos. Indico para cada uno el motivo concreto y qué necesitamos verificar:

### 3.1 Módulos imprescindibles (deben estar para diagnóstico completo)

**`pipeline.py`** — *(ya está en proyecto)*
- Verificar líneas 678-700 (BL-44 sin R-4).
- Verificar si existe punto de invocación a `propagate_nature_to_restantes_type_family`.
- Verificar todos los puntos donde se invoca `apply_semantic_validation` o `validate_all_semantic_consistency` (causa del log duplicado).

**`classify_utils.py`** — *(ya está en proyecto)*
- Verificar si existe la función `propagate_nature_to_restantes_type_family` y el catálogo `LEXICAL_FAMILY_INFERENCE_BL62`. **Ya verificado: no existen**.
- Línea 1372 (Theme='Inflación' bug).
- Líneas 2654-2867 (función master con logging duplicado).

**`sqlite_writer.py`** — *(ya está en proyecto)*
- Líneas 354-367: cláusulas UPSERT que sobrescriben Fund_Nature/Type/Family directamente sin condicional.
- Verificar si los flags `_bl44_force_overwrite` se filtran antes del INSERT (probablemente no — irían como columnas al INSERT y romperían).

### 3.2 Módulos faltantes que necesitamos para diagnóstico completo

**`blocks/restantes.py`** — **NO está en proyecto**, **subirlo es prioritario**.

Razón: el log muestra que `[BL44]` y `[BL62]` se emiten desde el bloque RESTANTES (los timestamps coinciden con líneas "RESTANTES LUxxx (N/2331)"). Como `pipeline.py:683-694` tiene una versión mínima de BL-44 que no genera los textos del log ("naturaleza no determinable", "re-inferido"), debe existir **otra implementación de BL-44/BL-62 dentro de `blocks/restantes.py`**. Sin este archivo no podemos auditar dónde está la lógica errónea ni cuál es el alcance del fix.

**`blocks/monetarios.py`** — **NO está en proyecto**, subirlo recomendable.

Razón: BL-44 dispara también desde el bloque MONETARIOS (LU1133289592, LU1133289758 al inicio del log). Mismo razonamiento: hay lógica BL-44 embebida en este bloque que no está centralizada.

**`init_db_v17.py` o equivalente con DDL del schema** — **NO está en proyecto**, subirlo recomendable.

Razón: Sonnet menciona "el schema SQLite tiene Fund_Nature TEXT NOT NULL — esa restricción no la conocía". Conocer el DDL exacto evita decisiones de diseño en vacío. Además, conviene verificar qué otras columnas son NOT NULL para anticipar bugs análogos (por ejemplo, si `Profile`, `Strategy`, `Family`, `Type` también son NOT NULL, cualquier asignación a NULL provocará el mismo error).

**`fund_characterizer.py`** — **NO está en proyecto**, recomendable para auditoría más amplia.

Razón: la función `_detect_theme` puede emitirse desde aquí también (no lo he confirmado). Si se duplica entre `classify_utils.py:1372` y `fund_characterizer.py`, hay otro bug latente del Principio #2 (DRY).

**Los 6 bloques restantes (`blocks/rf_corto.py`, `rf_flexible.py`, `renta_variable.py`, `mixtos.py`, `alternativos.py`, `restantes.py`)** — **NO están en proyecto**, deberían estar en sesión completa de auditoría R-4 (BL-67).

Razón: la auditoría sistémica BL-67 que abrió el sprint A.1 (sección 6) requiere ver todos los bloques para confirmar si BL-19, BL-33, BL-42, BL-47 sufren el mismo defecto R-4. Sin estos archivos, BL-67 no puede ejecutarse.

### 3.3 Otros módulos potencialmente relacionados

**`benchmark_normalizer.py`** — para verificar si los warnings sobre Benchmark provienen de aquí.
**`kiid_parser.py`** — relevante si el bug del Theme afecta a otros campos detectados desde texto KIID.
**`fund_family_builder.py`** — para auditar las 7 familias inconsistentes residuales.

---

## 4. SOBRE LA EXTENSIÓN DEL DEBUG (NUEVOS ERROR/WARNING) — RECOMENDACIÓN Y PROPUESTA NORMATIVA

### 4.1 Mi posición clara

**Sí, recomiendo extender la práctica al resto de módulos del core y los bloques.** El ciclo de hoy es el mejor argumento posible para hacerlo: tres bugs latentes que llevaban tiempo ocultos (Theme='Inflación' violando Principio #8, logging duplicado por arquitectura asimétrica, y la propia regresión de Fund_Nature=NULL) se han hecho **visibles en un solo ciclo gracias a los warnings añadidos**. Si no estuvieran, los habríamos descubierto cuando contaminaran datos de P3 — más tarde, con más coste, y con mayor dificultad de debugging.

Sin embargo, **la práctica tal como está hoy tiene tres defectos que conviene corregir antes de extenderla**. Si extendemos sin corregir, multiplicaremos por 3-5 el ruido del log y perderemos señal:

**Defecto 1 — Logging duplicado por dispatch asimétrico**.

Como vimos, el mismo evento se emite desde la función master y el wrapper. Si extendemos esa arquitectura, cada nueva regla generará dos líneas de log. Para 30+ reglas INTER, el ruido es prohibitivo.

**Defecto 2 — Severidad arbitraria entre WARNING e INFO**.

Algunos eventos van como `logger.warning` y otros como `logger.info`, sin criterio explícito. La sección 7.1 de tu `PRINCIPIOS_DISENO.md` sí define los niveles (ERROR/WARNING/INFO), pero no se aplica de forma consistente. Theme='Inflación' no permitido es WARNING — debería ser INFO porque hay autocorrección posible (traducir), o ERROR porque nadie corrige y el valor llega a BD inválido. La ambigüedad genera ruido.

**Defecto 3 — Falta convención uniforme de tags `[BLxx]`**.

El log mezcla `[BL44]`, `[BL62]`, `[NORM]`, `[FamilyBuilder]`, `Allowed-Values`, `InvestmentUniverse-NatureFallback`, `[BL62]`, `[ERROR]`, `[CACHED]`, etc. Sin convención, es difícil cuantificar incidencias por tipo (necesario para reportes de control SQL). Idealmente, todo evento de regla INTER debería emitirse con tag `[BL-XX]` consistente.

### 4.2 Propuesta normativa: nuevo md mandatorio o ampliación de uno existente

Mi recomendación es **ampliar `PRINCIPIOS_DISENO.md`** con una nueva sección dedicada (no un md nuevo). Razones:

1. La sección 7 actual ("Gestión de errores y logging") ya está allí pero es esquelética (3 ejemplos sin normativa de cuándo emitir).
2. Crear un md nuevo aumenta la carga de mantenimiento. La normativa de logging es transversal y pertenece a los principios.
3. La integración con los meta-principios #1 (Root Cause) y #2 (DRY) que ya están en PRINCIPIOS es directa: el logging es la herramienta de detección de incumplimientos, parte del mismo cuerpo doctrinal.

**Estructura propuesta para la ampliación de la sección 7 de PRINCIPIOS_DISENO.md:**

```
## 7. GESTIÓN DE ERRORES Y LOGGING (AMPLIADO — POST 30-ABR-2026)

### 7.1 Principio fundamental

Todo evento que cumpla CUALQUIERA de los siguientes criterios DEBE emitir log:

a) Una regla INTER detecta inconsistencia y aplica autocorrección.
b) Una regla INTER detecta inconsistencia y NO puede corregir (residual).
c) Un valor cae en autocorrección por defecto (fallback).
d) Una decisión de clasificación se toma con confianza < umbral establecido.
e) Una validación de catálogo (ALLOWED_VALUES_BY_COLUMN) falla.
f) Un atributo NOT NULL del schema recibe valor None tras procesamiento.
g) Una operación de extracción (parser) devuelve None donde se esperaba valor.

### 7.2 Niveles de severidad — convención obligatoria

| Nivel | Cuándo se emite | Acción del pipeline |
|---|---|---|
| ERROR | El fondo NO se puede persistir o tiene datos críticos faltantes | Logear y continuar; el fondo queda con su estado anterior en BD |
| WARNING | Inconsistencia detectada Y autocorregida; o residual no determinable | Logear; persistir con valor corregido o flag DQ='WARN' |
| INFO | Inferencia exitosa por fallback; trazabilidad de decisión | Logear; persistir con valor inferido |
| DEBUG | Diagnóstico interno, no visible en log de producción | Configurable por nivel |

### 7.3 Convención de tags

Formato obligatorio: `[BL-XX] [ISIN] mensaje` para cualquier regla INTER.
Para fallbacks no asociados a BL específico: `[NORM-NombreCategoría]`.
Para errores estructurales: `[ERROR-CATEGORÍA]`.

Ejemplos válidos:
- `[BL-44] LU1133289592 Nature_efectivo=Monetario incompatible con SRRI=3 → Restantes`
- `[NORM-Profile-SRRI] LU0907915168 Profile=Conservador SRRI=5 → Dinámico`
- `[ERROR-NotNull] LU0171298564 Fund_Nature=None tras BL-62 fallback`

### 7.4 Reglas anti-duplicación

a) Las funciones de validación master DEBEN ser puras (sin logging interno).
   El logging vive exclusivamente en el wrapper que las invoca.
b) Si una regla puede dispararse desde múltiples puntos del pipeline,
   DEBE invocarse desde un único punto canónico (consistente con R-1).
c) Cualquier evento debe loguearse exactamente una vez por incidencia
   (un fondo, un evento, una línea de log).

### 7.5 Reglas de elicitación obligatoria

a) Cuando se introduce nueva regla INTER, se DEBE listar en log al menos los
   primeros 5 disparos durante el ciclo (para verificar comportamiento).
b) El resumen final del pipeline DEBE incluir contador agregado por tag:
   "[BL-44] disparos: N. Reclasificados: N1. Re-inferidos: N2. Residuales WARN: N3."
   Esto facilita validación posterior sin revisar log línea a línea.

### 7.6 Métricas de monitorización (control SQL)

Cada regla INTER documentada en backlog DEBE tener:
a) Control SQL "antes del fix" (cuántos casos hay).
b) Control SQL "después del fix esperado" (qué retorna tras corrección).
c) Tag de log distintivo para cuantificar disparos por ciclo.

Sin estos tres elementos, no se debe abrir un BL en backlog.
```

### 4.3 Cómo extender al resto de módulos: tres olas

**Ola 1 — Bloques (`blocks/*.py`)**: añadir warning cuando un bloque retorna sin asignar Fund_Nature/Type/Family (silent failure mode); añadir info cuando el clasificador toma decisión por heurística débil (< 0.7 confidence).

**Ola 2 — Parsers (`kiid_parser.py`, `benchmark_normalizer.py`)**: añadir warning cuando una extracción que normalmente sí debería tener match retorna None (señal de que el patrón quedó obsoleto frente al formato actual del KIID).

**Ola 3 — Funciones de inferencia INTER en `classify_utils.py`**: refactorizar las que ya existen (BL-30, BL-31, BL-33, etc.) para emitir según convención 7.3 y desduplicar conforme a 7.4.

Cada ola debe ser un sprint en sí mismo, con sus controles SQL, no un cambio horizontal masivo.

### 4.4 Riesgo de no extender

Si no extendemos esta práctica:

- **Bugs como Theme='Inflación'** seguirán pasando inadvertidos hasta producir contaminación. Hoy lo descubrimos con 4-5 fondos afectados (AXA WF GLOBAL INFLAT.BONDS, AXA WF GLOBAL INFLATIO.BONDS, SISF GLOB INFLAT LINK BND, AXA WF GL INFLAT BNDS, PIMCO INFLATN MA). Sin warnings, esto habría seguido contaminando hasta que un fondo de Theme="Inflation" en P3 hubiese fallado al cruzar con el catálogo y hubiéramos tenido que rastrear la causa hacia atrás.

- **Bugs latentes R-4 análogos a BL-44** (BL-19, BL-33, BL-42, BL-47 listados en BL-67) seguirán produciendo silenciamente datos stale en BD. La auditoría empírica con warnings es más rápida que la inspección de código módulo a módulo.

- **Regresiones del propio Sprint A.1** (Fund_Nature=NULL, logging duplicado) se hubieran detectado solo en P3 después de meses de operación.

Por tanto, mi recomendación firme es: **extender la práctica con la normativa formalizada en sección 7 de PRINCIPIOS_DISENO.md**.

---

## 5. RESUMEN EJECUTIVO Y ACCIÓN INMEDIATA

**Estado del Sprint A.1**: implementación incompleta y divergente. BL-44 con R-4 no se aplicó. BL-62 se implementó parcialmente fuera del módulo canónico. BL-64 no se aplicó. Adicionalmente se introdujo regresión funcional (Fund_Nature=NULL para fondos sin patrón léxico) que produce errores `NOT NULL constraint failed` en al menos 15-20 ocurrencias visibles del log y posiblemente más.

**Estado de progresión a P3**: bloqueado. La situación actual es peor que pre-sprint para los fondos afectados (errores activos en lugar de datos stale).

**Acción inmediata recomendada (24-48h)**:

1. Subir al proyecto los módulos `blocks/restantes.py`, `blocks/monetarios.py` y el DDL del schema. Sin ellos no se puede preparar la spec correctiva.
2. Cuantificar exactamente cuántos `NOT NULL constraint failed` hay en el log completo (usar `findstr /c:"ERROR" log_pipeline_20260430_082015.log | find /c "NOT NULL"`). Mostrarme ese número.
3. Ejecutar control SQL del backlog (BL-44 actualizado): `SELECT COUNT(*) FROM fund_master WHERE Fund_Nature IN ('Monetario','Renta Fija Corto Plazo') AND CAST(SRRI AS INTEGER) >= 3` para verificar si la regla R-4 se está cumpliendo.
4. Decidir si el Sprint A.1.b (correctivo) corrige solo los 4 puntos detectados (Fund_Nature='Restantes' en lugar de NULL, logging duplicado, Theme bug, fix R-4 real en pipeline.py) **o si se extiende para incluir la nueva normativa de logging** propuesta en sección 4.

**Decisión de diseño que necesito de ti**:

¿Apruebas la propuesta de ampliar la sección 7 de `PRINCIPIOS_DISENO.md` con la normativa de logging detallada, o prefieres mantener el debug actual y solo corregir los bugs concretos del sprint A.1.b? La respuesta condiciona el alcance del próximo sprint.
