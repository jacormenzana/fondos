# BL-KIID-LOCAL-FIRST — Análisis de viabilidad e impacto arquitectónico

**Objetivo:** evaluar la evolución de `get_kiid_for_isin` hacia una estrategia de carga híbrida que priorice el repositorio local de PDFs (`C:\data\fondos\kiid`) antes de recurrir a la descarga por URL del Excel maestro.

**Estado:** análisis previo a implementación. Diagnóstico-first. No se entrega código todavía; se entrega un Go/No-Go razonado y el diseño propuesto para decisión.

---

## 1. Resumen ejecutivo

La propuesta es **viable y de bajo riesgo**, pero su valor depende de un matiz que conviene fijar antes de escribir una línea: el flujo actual **ya tiene una caché que evita descargas** (la caché A+B sobre `Raw_KIID_Text` en BD). Por tanto, leer el PDF desde disco local **no ahorra descargas en el caso normal** — ese ahorro ya lo da la BD. El PDF local solo se necesita cuando hay que **re-extraer texto del binario** (hash distinto, FORCE_REFRESH, o texto ausente en BD). Es decir: el repositorio local es un sustituto del `download_pdf`, no un sustituto de la caché de texto.

Con esa lectura, el cambio es correcto y encaja en la arquitectura sin tocar la interfaz de los bloques. El impacto real se concentra en **un solo módulo** (`io.py`). `run_block.py` y `pipeline.py` solo necesitan cambios si se quiere exponer la modalidad como un parámetro configurable (recomendado pero opcional). Los módulos de bloque (`monetarios.py` y el resto) **no se ven afectados en absoluto**: son agnósticos al origen del documento.

**Recomendación: GO**, con la precisión de orden de prioridad descrita en §3 y el parámetro de modalidad de §5.

---

## 2. Punto de partida verificado (cómo funciona hoy)

He revisado el código real antes de analizar. El flujo actual de `get_kiid_for_isin(isin, excel_master_path, conn)` es, en orden:

1. **Caché A (texto en BD).** Consulta `fund_kiid_metadata` por `Raw_KIID_Text` con `KIID_Status IN ('OK','CACHED')`. Si hay texto, lo devuelve inmediatamente (enriquecido con `DLA2_Table_Text` si procede) con `KIID_Status='CACHED'`. **Nunca descarga.** Este es el camino del ~98% del corpus en régimen estacionario.
2. **Resolución de enlaces.** Si no hubo caché de texto, indexa el Excel maestro y obtiene las URLs candidatas del ISIN.
3. **Descarga + Caché B (hash).** Por cada URL: `download_pdf` → `pdf_sha256`. Si el hash coincide con `_cached_hash` de BD, reutiliza el texto cacheado (evita OCR/SRRI visual) con `KIID_Status='OK'`. Si difiere, **procesa el PDF completo**: `extract_text_from_pdf_bytes` → DLA-2 (`serialize_tables`) → almacenamiento físico (`store_kiid_pdf`, BL-KIID-STORE) → devuelve texto con `KIID_Status='OK'` y `KIID_PDF_BYTES` para el pipeline.

Dos hechos arquitectónicos clave que condicionan el diseño:

- **La antigüedad no se decide en `io.py`.** La caché A devuelve siempre que haya texto y estado `OK`/`CACHED`. El re-procesamiento por antigüedad lo dispara `mark_stale_for_refresh` (externo), que cambia el estado a `FORCE_REFRESH`; eso hace que la consulta de la caché A no encuentre fila y el flujo "caiga" a descarga. **El disparador de "ir a por el binario" es el estado en BD, no una fecha evaluada en `io.py`.**
- **El binario solo hace falta para re-extraer.** Cuando la caché A acierta, no se toca ningún PDF. El PDF (hoy de internet, mañana de local) solo es necesario en el camino de "procesar completo".

---

## 3. Estudio de viabilidad de la lógica propuesta

La lógica que planteas (Flujo A = local encontrado; Flujo B = fallback a internet) es implementable, pero **debe insertarse en el lugar correcto del orden de prioridad existente**, o de lo contrario rompe la optimización de la caché A.

### 3.1 El riesgo principal: no degradar la caché de texto

Tu descripción del Flujo A dice: "cargar el fichero en RAM → recuperar el hash de BD → si coincide, recuperar el texto raw de BD; si difiere, extraer y procesar completo". Esto es **exactamente la caché B**, pero alimentada desde disco en lugar de desde `download_pdf`. Correcto.

El peligro es de **secuenciación**. Si el repositorio local se consultara *antes* que la caché A de texto, entonces para el 98% de fondos ya cacheados estaríamos:

- leyendo el PDF de disco (I/O innecesaria),
- calculando su SHA-256 (CPU innecesaria),

…solo para acabar comparando el hash, ver que coincide y devolver el mismo texto de BD que la caché A habría devuelto sin tocar el disco. Es decir, introduciríamos un coste por fondo que hoy no existe, sin beneficio.

**Orden propuesto correcto** (preserva la optimización y añade el local-first donde aporta):

```
1. Caché A (texto en BD, KIID_Status OK/CACHED)   → devolver texto. SIN tocar disco ni red.
2. [Solo si A falla] ¿Existe PDF local {ISIN}.pdf?
     2a. SÍ  → Flujo A: leer bytes de disco → hash.
               · hash == hash_BD → reusar texto BD (Caché B local).
               · hash != hash_BD → extraer + DLA-2 + (re)almacenar → procesar completo.
     2b. NO  → Flujo B: fallback actual (resolver URL Excel → download_pdf → procesar).
3. [Tras 2b, PDF nuevo] store_kiid_pdf ya deja el binario en local para futuros ciclos.
```

Con este orden, el caso normal (caché A) es idéntico al actual: cero coste añadido. El local-first actúa precisamente en el camino caro (re-extracción), que es donde sustituir una descarga de red por una lectura de disco aporta valor real (latencia, robustez ante caída del servidor del gestor, reproducibilidad offline).

### 3.2 Compatibilidad con el almacenamiento físico ya existente (BL-KIID-STORE)

La pieza encaja de forma natural con lo ya implementado. `store_kiid_pdf` guarda cada PDF descargado como `{ISIN}.pdf` en `C:\data\fondos\kiid`. Eso significa que **el repositorio local se autoalimenta**: la primera vez que un fondo se descarga por Flujo B, queda en disco; en ciclos posteriores donde haga falta re-extraer, el Flujo A lo encontrará. El nombre de fichero ya es `{ISIN}.pdf`, que es justo la clave de búsqueda que el Flujo A necesita. No hay desalineación de convención de nombres.

### 3.3 Coherencia de hash entre disco y BD

El Flujo A compara el hash del PDF local con `KIID_PDF_Hash` de BD. Hay que tener clara la semántica de esa comparación:

- **Coinciden:** el PDF en disco es el mismo binario cuyo texto está en BD. Reutilizar texto es correcto.
- **Difieren:** alguien ha colocado en disco un PDF distinto del que generó el texto en BD (p. ej., depósito manual de una versión nueva, o un `store_kiid_pdf` que versionó el antiguo). Re-extraer es correcto, y tras hacerlo hay que actualizar `KIID_PDF_Hash` y `Raw_KIID_Text` en BD vía el writer — cosa que el pipeline ya hace cuando `KIID_Status='OK'` con `KIID_PDF_BYTES` presente. **Sin cambios en el writer.**

Caso límite a contemplar en la implementación: **PDF local existe pero no hay fila en BD** (fondo nunca procesado, pero alguien dejó el PDF manualmente). Aquí `hash_BD` es `None`; la comparación debe tratarse como "difiere" → procesar completo. Es el comportamiento deseado y trivial de codificar (`if cached_hash and local_hash == cached_hash`).

### 3.4 Manejo de errores nuevos del Flujo A

Leer de disco introduce modos de fallo que `download_pdf` no tenía y que deben degradar limpiamente a Flujo B (no abortar):

- Fichero presente pero corrupto / ilegible / truncado (`IOError`, PDF inválido en `extract_text_from_pdf_bytes`).
- Permisos.
- Fichero de 0 bytes.

La política correcta es: **cualquier fallo del Flujo A → caer a Flujo B (internet)**, registrando el motivo. Así el repositorio local nunca puede empeorar la robustez respecto al estado actual; en el peor caso, replica el comportamiento de hoy.

### 3.5 Veredicto de viabilidad

**Viable, GO.** La lógica es correcta siempre que (a) se inserte *después* de la caché A de texto, no antes; (b) los fallos de lectura local degraden a internet; (c) el caso "PDF local sin hash en BD" se trate como "difiere". No requiere estructuras de datos nuevas ni cambios de esquema.

---

## 4. Análisis de impacto en arquitectura

### 4.1 `io.py` — IMPACTO ALTO (único módulo con cambios sustantivos)

Toda la lógica nueva vive aquí. Cambios concretos:

- Nueva constante de directorio de lectura (reutilizar la `KIID_STORAGE_DIR` ya existente de BL-KIID-STORE — **DRY**, no crear una segunda constante para el mismo path).
- Nuevo helper `_load_local_kiid(isin) -> Optional[bytes]`: comprueba `os.path.exists({ISIN}.pdf)` y lee los bytes, con manejo de excepciones que devuelve `None` ante cualquier fallo.
- Refactor de `get_kiid_for_isin` para insertar el bloque de decisión local-first **entre** la caché A (que se mantiene intacta y primera) y el bucle de descarga por URL (que pasa a ser el fallback del Flujo B).
- La rama de "procesar completo" (extracción + DLA-2 + escritura de `meta`) debe **factorizarse** para ser reutilizable por ambos orígenes (local y red), evitando duplicar el bloque DLA-2 y la construcción de `meta`. Esto es aplicación directa del **Principio #2 (DRY)**: hoy ese bloque existe una sola vez dentro del bucle `for url in links`; al añadir el origen local, la tentación es copiarlo. Debe extraerse a una función interna `_process_pdf_bytes(pdf_bytes, isin, source) -> (kiid_text, meta_updates)` invocada desde los dos caminos.

Riesgo de regresión: **medio-bajo**, contenido en un solo archivo y cubierto por el kill-switch propuesto en §5. La factorización del bloque "procesar completo" es la parte más delicada porque toca el camino ya probado de descarga; debe hacerse con tests que verifiquen que el camino de red sigue produciendo el mismo `meta` que hoy.

### 4.2 `run_block.py` — IMPACTO BAJO (opcional)

Tu enunciado dice que este módulo "determina la modalidad de carga de ficheros externos". Tras revisar el código: **hoy no lo hace**. `run_block.py` solo parsea argumentos (`--block`, `--db`, `--master`, `--sample`, `--list-isin`), carga el Excel, abre la conexión y delega en `run_block()` del pipeline. No tiene ninguna noción de origen local vs. internet.

Por tanto, hay dos opciones de diseño:

- **Opción mínima (sin cambios):** la modalidad local-first se controla con un kill-switch a nivel de módulo en `io.py` (`KIID_LOCAL_FIRST_ENABLED`, ver §5). `run_block.py` no se toca. Es lo más alineado con tu patrón actual de feature flags (como `DLA_TABLE_SERIALIZATION_ENABLED` o `KIID_PHYSICAL_STORAGE_ENABLED`).
- **Opción configurable (recomendada si quieres control por ejecución):** añadir un argumento CLI `--kiid-source {auto,local,remote}` que se propague `run_block.py → run_block(pipeline) → get_kiid_for_isin`. Esto convierte a `run_block.py` en el lugar que "determina la modalidad", que parece ser tu intención de diseño. Coste: un `add_argument`, un parámetro nuevo en la firma de `run_block()` del pipeline, y un parámetro nuevo en `get_kiid_for_isin`. Trivial, pero toca tres firmas.

Mi recomendación: implementar la **Opción mínima primero** (kill-switch booleano, sin tocar firmas), validar en producción, y solo añadir el argumento CLI `--kiid-source` si surge la necesidad operativa de alternar modalidad por ejecución. Esto respeta tu principio de cambios quirúrgicos y evita modificar firmas estables antes de tener evidencia de que se necesita la flexibilidad.

### 4.3 `pipeline.py` — IMPACTO NULO o BAJO (según opción de §4.2)

`run_block()` del pipeline invoca `get_kiid_for_isin(isin, str(master_excel_path), conn=conn)` en la línea 519 y consume el resultado vía `.get()` puntuales (verificado en el análisis previo: no hay volcado dinámico de `kiid_meta`). 

- **Con la Opción mínima:** `pipeline.py` **no se toca**. La firma de `get_kiid_for_isin` no cambia; el comportamiento local-first es interno y transparente al pipeline. El `meta` que recibe puede ganar una clave informativa (`KIID_Source: 'LOCAL'|'REMOTE'`), pero como el pipeline solo lee claves concretas, una clave extra se ignora silenciosamente (mismo razonamiento que validamos para `KIID_Stored_Action`).
- **Con la Opción configurable:** un único cambio — añadir el parámetro `kiid_source` a la firma de `run_block()` y pasarlo a `get_kiid_for_isin`. Nada más en el cuerpo.

Observación relevante: el pipeline **ya está preparado** para el local-first sin saberlo. Su lógica distingue `KIID_Status` `CACHED` vs. no-`CACHED` (línea 600: `_needs_char = (_kiid_status_c != "CACHED")`) para decidir si re-caracteriza. Un PDF leído de local que se re-procesa devolverá `KIID_Status='OK'` (igual que una descarga), por lo que el pipeline lo tratará correctamente como "documento procesado" sin cambios. La semántica de estados encaja.

### 4.4 Módulos de bloque (`monetarios.py` y el resto) — IMPACTO NULO

Verificado directamente sobre `monetarios.py`: su interfaz pública es `get_universe_isins(df_master)` y `classify_fund(fund_name, kiid_text)`. La firma de `classify_fund` recibe **únicamente el texto ya extraído** (`kiid_text: Optional[str]`). No importa `io`, no llama a `get_kiid_for_isin`, no abre ficheros, no toca `pdf_bytes` ni `requests` ni el disco. La única coincidencia de búsqueda con "io" fue un comentario de la regla BL-44.

Esto es consecuencia de una separación de capas correcta que ya existe en tu arquitectura: **la obtención del documento (capa IO/pipeline) está desacoplada de la clasificación (capa bloque)**. El bloque recibe texto y devuelve atributos; le es indiferente si ese texto vino de BD, de un PDF de disco o de uno descargado. Por inducción sobre esa interfaz común (todos los bloques exponen `classify_fund(fund_name, kiid_text, ...)`), **ningún bloque se ve afectado** por esta refactorización: ni MONETARIOS, ni RF, ni RV, ni MIXTOS, ni ALTERNATIVO, ni RESTANTES.

Conviene una verificación de confirmación durante la implementación: hacer un `grep` en `blocks/` de `get_kiid_for_isin|download_pdf|from core.io|import io|pdf_bytes|open(` para descartar que algún bloque rompa la separación de capas. En `monetarios.py` ya está confirmado que no lo hace; la hipótesis es que los demás siguen el mismo contrato, pero debe verificarse, no asumirse.

### 4.5 Otros módulos del recorrido

- **`sqlite_writer.py` (`upsert_kiid_metadata`):** sin cambios. Escribe columnas explícitas; el hash y el texto re-extraídos por Flujo A llegan por las mismas claves (`KIID_PDF_Hash`, `Raw_KIID_Text`, `DLA2_Table_Text`) que hoy. La política COALESCE existente preserva valores correctamente.
- **`schema_fondos.sql`:** sin cambios. La propuesta no introduce atributos nuevos persistibles. (Si más adelante se quisiera auditar el origen desde SQL — qué fondos se procesaron desde local vs. red — eso sería un BL aparte con columna `KIID_Source`, exactamente el mismo patrón que discutimos para `KIID_Stored_Action`. No forma parte de esta propuesta.)
- **`store_kiid_pdf` (BL-KIID-STORE):** sin cambios funcionales, pero pasa a tener un segundo rol: además de archivar descargas, es el mecanismo que **puebla** el repositorio que el Flujo A consume. Esta sinergia es deseable y conviene documentarla en el diseño.

---

## 5. Diseño propuesto (resumen para implementación)

**Kill-switch (Opción mínima recomendada como primer paso):**

```
KIID_LOCAL_FIRST_ENABLED = True   # en Config de io.py, junto a los demás flags
```

- `True`  → activa el Flujo A/B descrito en §3.1.
- `False` → comportamiento idéntico al actual (caché A → descarga directa). Roll-back inmediato.

**Orden de resolución dentro de `get_kiid_for_isin`:**

1. Caché A (texto BD) — **sin cambios, primera siempre**.
2. Si `KIID_LOCAL_FIRST_ENABLED` y existe `{ISIN}.pdf` local → leer bytes; comparar hash con BD; reusar texto o procesar completo (`_process_pdf_bytes(..., source='LOCAL')`).
3. Fallback: bucle de descarga por URL actual (`_process_pdf_bytes(..., source='REMOTE')`), que además deja el PDF en local vía `store_kiid_pdf`.

**Factorización DRY obligatoria:** extraer el bloque "procesar completo" (extracción + DLA-2 + construcción de `meta` + almacenamiento) a `_process_pdf_bytes(pdf_bytes, isin, source)` para que local y red compartan exactamente la misma lógica de negocio. Esto satisface el requisito explícito de "aplicar la misma lógica de negocio existente para ficheros de internet".

**Tratamiento de fallos del Flujo A:** cualquier excepción de lectura/parseo local → log + caer a Flujo B. El local nunca empeora la robustez.

**Clave informativa opcional en `meta`:** `KIID_Source ∈ {'CACHE','LOCAL','REMOTE'}` para trazabilidad en logs. No se persiste (el pipeline la ignora salvo que se decida un BL de auditoría).

---

## 6. Checklist de cumplimiento de principios

| Principio | Cumplimiento en esta propuesta |
|-----------|--------------------------------|
| **#1 Root Cause** | El cambio ataca la causa (origen del binario) en el punto canónico `get_kiid_for_isin`, no parchea consumidores. |
| **#2 DRY** | Reutiliza `KIID_STORAGE_DIR` y `pdf_sha256`; factoriza "procesar completo" en una función única compartida por local y red; no duplica el bloque DLA-2. |
| **Kill-switch** | `KIID_LOCAL_FIRST_ENABLED` siguiendo el patrón establecido. |
| **Cambios quirúrgicos** | Impacto concentrado en `io.py`; firmas de pipeline/run_block intactas en la Opción mínima. |
| **Separación de capas** | Respetada: los bloques siguen recibiendo solo `kiid_text`; ninguno se modifica. |
| **Defensa en profundidad** | Fallo local → fallback a red; el peor caso replica el comportamiento actual. |

---

## 7. Decisiones pendientes de tu validación

1. **Opción mínima (kill-switch) vs. configurable (`--kiid-source` en CLI)?** Recomiendo empezar por la mínima.
2. **Orden de prioridad confirmado:** ¿aceptas que la caché A de texto en BD siga siendo *primera*, y el local-first actúe solo en el camino de re-extracción? (Es lo correcto técnicamente; lo confirmo porque tu redacción del Flujo A podría leerse como "local primero, siempre").
3. **¿Auditoría de origen en BD (`KIID_Source`)?** Fuera de alcance de esta propuesta; sería un BL aparte si lo quieres.

Con tu confirmación de estos tres puntos, el siguiente paso sería el BL de implementación sobre `io.py` con su suite de tests (caché A intacta, Flujo A hash-match, Flujo A hash-mismatch, Flujo A fichero corrupto → fallback, Flujo B sin local, autoalimentación del repositorio).
