# BL-DLA — Document Layout Analysis: serialización 2D-aware del texto KIID

**Tipo:** Decisión de diseño arquitectónica — **NO ejecutar implementación sin aprobación previa**
**Fecha:** 2 de mayo de 2026
**Autor:** José + Claude (sesión Nivel-3 Diseño Arquitectónico)
**Estado:** PROPUESTA — pendiente de validación de ROI por Fase 0
**Versión:** 1.0
**Sprint sugerido:** Fase A (cierre P1 consolidado) — categoría "diferible sin riesgo" hasta confirmar ROI

---

## 0. RESUMEN EJECUTIVO

**Problema:** `core/io.py:extract_text_from_pdf_bytes()` usa `pdfplumber.page.extract_text()` con configuración por defecto, que serializa el texto de un PDF en orden estricto de coordenada Y. En layouts de **dos columnas** —prevalentes en KIIDs/DDFs— esto **intercala líneas físicamente alineadas que pertenecen a párrafos lógicamente distintos**, produciendo frases sintácticamente incoherentes que (a) silencian patrones regex correctos y (b) habilitan matches espurios.

**Validación empírica (Fase 0 ya realizada en este documento, sección 2):** sobre 5 PDFs muestreados, se confirma 2-cols en al menos una página de los 5, con frases efectivamente concatenadas en cruz. Ejemplo verificado en IE0032875985:

```
Tipo tres años menos que la duración del Bloomberg Global Aggregate Index. La duración
Este producto es un subfondo de un OICVM de una sociedad de inversión de tipo
mide la sensibilidad de los activos al riesgo de tipos de interés.
```

`"Tipo"` es heading de la columna izquierda; `"tres años menos..."` es continuación de un párrafo de la columna derecha. Frase resultante: **léxicamente imposible**.

**Solución propuesta:** introducir un módulo nuevo upstream `core/dla_extractor.py` que sustituye selectivamente la función `extract_text_from_pdf_bytes()` actual. Estrategia layered:
1. **Detectar layout** página-a-página (1-col vs 2-col, presencia de tablas).
2. **Serializar por regiones** respetando la lectura humana (toda columna izquierda, luego toda columna derecha; tablas según taxonomía de Categorías 1/2/3 de Gemini).
3. **Mantener interfaz de salida idéntica**: una sola string `kiid_text` que el parser consume sin cambios.

**Alcance acotado:** **Fase 1** únicamente migra el tratamiento de **párrafos en dos columnas**. Las tablas (Categorías 1/2/3) se difieren a Fase 2/3 según ROI medido. Los detectores de `kiid_parser.py` no se modifican en absoluto en Fase 1.

**Riesgo principal:** los regex calibrados sobre la serialización antigua —especialmente capas "fused" tipo JPMorgan/Amundi en `srri_text.py` v3 y `_detect_entry_fee` v24/v25 prioridades 3 y 12— pueden **perder cobertura** si el nuevo serializador produce texto distinto. Mitigación obligatoria: tests de no-regresión sobre el corpus completo de 3.195 KIIDs con texto, antes de cualquier despliegue.

**Criterio de cierre (Definition of Done):** ningún regex actualmente operativo pierde detección, y al menos un atributo medible mejora cobertura. Sin ambos, la migración no se despliega.

---

## 1. CONTEXTO Y CADENA DE EXTRACCIÓN

### 1.1 Diagnóstico del flujo actual

```
fund_master_excel  ──[find_kiid_links_from_excel]──┐
                                                     ▼
                                           [download_pdf]                   ◄ HTTP retries
                                                     │
                                                     ▼
                                           [pdfplumber.page.extract_text()] ◄────── PUNTO CRÍTICO
                                                     │                              (pierde semántica 2D)
                                                     ▼
                                           kiid_text (str, 1D plano)
                                                     │
                          ┌──────────────────────────┴──────────────────────────┐
                          ▼                          ▼                          ▼
              srri_text.extract_srri      kiid_parser.parse_kiid_generic   bloques de clasificación
              (regex sobre kiid_text)     (23 detectores sobre kiid_text)  (regex sobre kiid_text)
```

**El parser y los bloques nunca ven el PDF original** (excepto SRRI visual, que es ortogonal). Toda la inferencia depende de la calidad de la string que produce `extract_text_from_pdf_bytes`.

### 1.2 Patología confirmada empíricamente

**Fuente:** PDFs reales del corpus + script de diagnóstico ejecutado en sesión.

| Fondo | Páginas | Páginas en 2-cols | Síntoma observado |
|---|---|---|---|
| LU0006277684 | 3 | 2/3 (págs. 1, 2) | Tabla escenarios y costes Cat. 2 |
| LU0070177588 | 3 | 2/3 (págs. 1, 2) | Tabla escenarios Cat. 3 |
| **IE0032875985** | 3 | **3/3** | **Frases cruzadas verificadas en pág. 0** |
| IE00B45H7020 | 3 | 1/3 (pág. 2) | Costes Cat. 2 |
| FR0000989626 | 3 | 1/3 (pág. 0) | Frases cruzadas (visible en log adjunto) |

Patrón concreto detectado (regex `Tipo\s+(?:tres|cinco|diez)\s+a[ñn]os` solo matchea texto corrupto): **1 hit en IE0032875985 actual, 0 hits en su versión re-serializada**. Recíprocamente, regex `Tipo[\s\n]+(?:Este\s+producto\s+es\s+(?:un\s+)?subfondo)`: **0 hits en actual, 1 hit en re-serializada**. Este es el caso paradigmático del falso negativo silencioso.

### 1.3 Por qué esto es Root Cause (Principio #1)

El `kiid_parser.py v24/v25` acumula 23 prioridades en `_detect_entry_fee`, una capa `t_fused = text.replace(" ", "")` específica para JPMorgan/Amundi en `srri_text.py v3`, y BL-55/2 con su ventana acotada de ±1500 chars. Cada uno de estos parches es legítimo localmente, pero **todos comparten una causa común que ninguno aborda**: el upstream que produce `kiid_text` está perdiendo información estructural del PDF.

Resolver este punto único elimina la presión sobre los 23 niveles de fallback regex y cumple Principio #2 (DRY) por construcción: una mejora upstream beneficia a *todos* los detectores downstream sin tocarlos.

### 1.4 Por qué la solución NO va en `kiid_parser.py` ni en `srri_text.py`

Aplicar reordenamiento por columnas dentro de `kiid_parser.py` requeriría que el parser leyera el PDF, lo cual:
- Violaría su contrato actual (`kiid_text: str` como entrada).
- Duplicaría lógica con `srri_text.py` y `srri_v4_geometric.py`.
- Cargaría el coste de PyMuPDF/pdfplumber en cada llamada del parser.

La separación correcta de responsabilidades es: **`io.py` extrae texto; `kiid_parser.py` razona sobre texto**. La mejora se introduce en el primero (o más exactamente, en un módulo que lo asiste).

---

## 2. EVIDENCIA EMPÍRICA — DIAGNÓSTICO REPRODUCIBLE

### 2.1 Script ejecutado

Disponible en sesión: `diag_dla.py`, `compara_seccion.py`, `check_patrones.py`. Reproducción:

```python
import pdfplumber, fitz
# ACTUAL (replica io.py):
with pdfplumber.open(pdf) as p:
    actual = "\n".join(page.extract_text() for page in p.pages[:3])

# AWARE (PyMuPDF blocks + reordenación por columnas):
doc = fitz.open(pdf)
for page in doc:
    blocks = [b for b in page.get_text("blocks") if b[6] == 0 and b[4].strip()]
    # Si narrow > full: separar por mitad de página y leer izq luego der.
```

### 2.2 Resultados clave

**Líneas concatenadas patológicamente** (regex `Tipo\s+(?:tres|cinco|diez)\s+años`):

| Fondo | Actual | Aware | Interpretación |
|---|---|---|---|
| IE0032875985 | **1** | 0 | Texto corrupto presente, eliminado por aware |
| LU0006277684 | 0 | 0 | Sin patología en este patrón concreto |
| IE00B45H7020 | 0 | 0 | Sin patología en este patrón concreto |

**Patrón heading-frase intacto** (regex `Tipo[\s\n]+Este\s+producto\s+es\s+un\s+subfondo`):

| Fondo | Actual | Aware | Interpretación |
|---|---|---|---|
| IE0032875985 | **0** | **1** | **Falso negativo silencioso eliminado por aware** |

### 2.3 Conclusión empírica

La hipótesis está confirmada en 1/5 fondos del muestreo en este patrón concreto. Para un corpus de 3.195 fondos, una tasa de ocurrencia del 20% implicaría ~640 fondos potencialmente afectados — pero esto es proyección, no medición. **Fase 0 completa es un requisito antes de Fase 1**: contar sobre el corpus completo cuántos KIIDs presentan layout 2-col en al menos una página.

---

## 3. PROPUESTA DE ARQUITECTURA — DETALLE TÉCNICO

### 3.1 Estructura de módulos

**Nuevo módulo:** `core/dla_extractor.py`

```python
# core/dla_extractor.py — Document Layout Analysis aware extractor
"""
Sustituye selectivamente extract_text_from_pdf_bytes de io.py cuando el PDF
presenta layout multi-columna o tablas estructuradas.

Contrato externo:
    extract_text_dla_aware(pdf_bytes: bytes,
                           ocr_enabled: bool = True,
                           debug: bool = False) -> tuple[str, dict]

Devuelve:
    (kiid_text, layout_metadata)
        kiid_text         — string compatible con la salida actual
        layout_metadata   — dict de telemetría (tipos por página, decisiones)
"""
```

**Modificación mínima a `core/io.py`:** una línea, parametrizada y backward-compatible:

```python
# io.py:68 — modificación propuesta
DLA_ENABLED = True  # kill-switch global

def extract_text_from_pdf_bytes(pdf_bytes, ocr_enabled=OCR_ENABLED, ...):
    if DLA_ENABLED:
        from core.dla_extractor import extract_text_dla_aware
        text, _meta = extract_text_dla_aware(pdf_bytes, ocr_enabled=ocr_enabled)
        return text
    # fallback: lógica original (mantener intacta)
    ...
```

### 3.2 Algoritmo de Fase 1 — solo párrafos en 2 columnas

**Por cada página del PDF (máx. 3, conforme a `MAX_PDF_PAGES`):**

```
1. Extraer bloques con coordenadas:
     blocks = page.get_text("blocks")
     filtrar block_type == 0 (texto), descartar bloques vacíos.

2. Clasificar layout de la página:
     widths = [b.x1 - b.x0 for b in blocks]
     n_narrow = #(widths < page_width * 0.55)
     n_full   = #(widths > page_width * 0.70)

     Si n_narrow > n_full Y #blocks_izq >= 3 Y #blocks_der >= 3:
         layout = TWO_COLUMN
     Sino:
         layout = SINGLE_COLUMN_OR_MIXED  # comportamiento actual

3. Serializar según layout:
     CASO TWO_COLUMN:
         izq = [b for b in blocks si centro_x(b) < page_width/2]
         der = [b for b in blocks si centro_x(b) >= page_width/2]
         izq.sort(key=lambda b: (b.y0, b.x0))
         der.sort(key=lambda b: (b.y0, b.x0))
         emitir izq + der como streams contiguos.

     CASO SINGLE_COLUMN_OR_MIXED:
         blocks.sort(key=lambda b: (b.y0, b.x0))
         emitir como antes.

4. Fallback OCR (idéntico al actual):
     Si extract_text() devuelve vacío Y OCR habilitado → tesseract.image_to_string.
     (En Fase 1, OCR mantiene comportamiento actual; OCR-aware es Fase 4.)
```

**Heurística de threshold (0.55 / 0.70):** establecida sobre el muestreo de los 5 PDFs. Necesita validación sobre corpus completo en Fase 0 — sección 4.

### 3.3 Lo que NO entra en Fase 1

- **Tablas Categoría 1, 2, 3** de Gemini: diferidas a Fase 2 (Cat. 1+2 — costes) y Fase 3 (Cat. 3 — escenarios). Razón: las tablas tienen su propio detector dedicado en pdfplumber (`page.find_tables()` / `page.extract_tables()`) cuya integración requiere análisis específico. Las patologías por tablas mal serializadas son ya parcialmente tratadas por la sofisticación del `_detect_entry_fee` v24/v25 — el ROI marginal es menor que el de párrafos 2-col.
- **OCR-aware DLA** (uso de `image_to_data` en lugar de `image_to_string` para reconstruir layout cuando no hay capa de texto): diferido a Fase 4. Solo aplica a KIIDs scaneados, una minoría.
- **Cambios en `kiid_parser.py`, `srri_text.py`, `srri_v4_geometric.py`**: ninguno. La promesa de la arquitectura es que estos módulos no se tocan.

### 3.4 Backward compatibility — política

- **Kill switch:** `DLA_ENABLED = False` en `io.py` revierte el comportamiento al actual sin tocar código adicional.
- **Roll-out gradual:** primer despliegue con flag `DLA_ENABLED = False` por defecto; activar para subset de ISINs problemáticos vía variable de entorno; activar global solo tras validación de no-regresión.
- **Persistencia:** `Raw_KIID_Text` se sobrescribe vía `COALESCE` (Principio #1), pero como el flujo lo escribe siempre desde `OK` o `FORCE_REFRESH`, una migración requiere marcar los ISINs candidatos como `FORCE_REFRESH` para repoblar `Raw_KIID_Text`. **Sin migración masiva inicial**: el corpus actual sigue válido; la mejora se aplica progresivamente conforme los KIIDs se re-descargan por antigüedad (`mark_stale_for_refresh` ya distribuye 50/ciclo).

---

## 4. FASE 0 — DIAGNÓSTICO CUANTITATIVO OBLIGATORIO

**Antes de implementar nada de la Fase 1**, este diagnóstico DEBE ejecutarse y sus resultados DEBEN cumplir umbrales mínimos.

### 4.1 Queries de diagnóstico (sobre `fund_kiid_metadata`)

**Q-DLA-01 — Distribución de longitudes y línguas (corpus baseline):**

```sql
SELECT
    Language,
    COUNT(*) AS n_funds,
    AVG(LENGTH(Raw_KIID_Text)) AS avg_len,
    MIN(LENGTH(Raw_KIID_Text)) AS min_len,
    MAX(LENGTH(Raw_KIID_Text)) AS max_len
FROM fund_kiid_metadata
WHERE Raw_KIID_Text IS NOT NULL
  AND LENGTH(Raw_KIID_Text) > 100
GROUP BY Language
ORDER BY n_funds DESC;
```

**Q-DLA-02 — KIIDs sospechosos de patología 2-col (heurística textual):**

Contar fondos cuyos `Raw_KIID_Text` contienen las firmas léxicas que solo aparecen en texto cruzado:

```sql
SELECT COUNT(*) AS n_sospechosos
FROM fund_kiid_metadata
WHERE Raw_KIID_Text REGEXP '\bTipo\s+(tres|cinco|diez)\s+a[ñn]os\b'
   OR Raw_KIID_Text REGEXP '\bsubfondos\s+mide\b'
   OR Raw_KIID_Text REGEXP '\bdurante\s+un\s+per[ií]odo\s+(El|La|Los|Las|Es)\s+'
   OR Raw_KIID_Text REGEXP '\bproducto\s+(Este|Esta)\s+';
```

(Nota: SQLite REGEXP requiere `conn.create_function("REGEXP", 2, lambda p, s: bool(re.search(p, s)) if s else False)`.)

**Q-DLA-03 — Análisis físico sobre PDFs (script auxiliar):**

Sobre los 639 fondos con `KIID_Status='OK'` (PDFs físicamente disponibles vía hash-cache), ejecutar un script Python que:

```python
# scripts/diag/dla_layout_inventory.py
for isin in isins_with_pdf:
    pdf = load_from_cache(isin)
    pages = pymupdf.open(pdf)
    n_pages_2col = sum(1 for p in pages if is_two_column(p))
    log_to_csv(isin, n_pages_total=len(pages), n_pages_2col=n_pages_2col)
```

Salida CSV con `(ISIN, n_pages_total, n_pages_2col)`. Fácil de agregar.

### 4.2 Umbrales de decisión

**Si Q-DLA-03 muestra:**

| Resultado | Decisión |
|---|---|
| ≥30% de fondos con 1 o más páginas en 2-cols | **Proceder con Fase 1.** ROI alto justificado. |
| 15-30% | **Proceder con Fase 1 acotada a piloto** (lista de ISINs candidatos). Re-evaluar tras piloto. |
| 5-15% | **Diferir Fase 1.** Probable cambio de prioridad: corregir parches específicos en `kiid_parser.py` para los regex problemáticos individualmente. |
| <5% | **Cerrar BL-DLA sin implementación.** El problema es marginal frente a otros gaps del backlog. |

### 4.3 Métricas baseline para comparación post-Fase 1

Antes de ejecutar Fase 1, capturar:

```sql
-- B-1: Cobertura actual de detectores principales
SELECT
    SUM(CASE WHEN Type IS NULL THEN 1 ELSE 0 END) AS null_type,
    SUM(CASE WHEN Family IS NULL THEN 1 ELSE 0 END) AS null_family,
    SUM(CASE WHEN Entry_Fee_Pct IS NULL THEN 1 ELSE 0 END) AS null_entry,
    SUM(CASE WHEN Exit_Fee_Pct IS NULL THEN 1 ELSE 0 END) AS null_exit,
    SUM(CASE WHEN Ongoing_Charge IS NULL THEN 1 ELSE 0 END) AS null_oc,
    SUM(CASE WHEN Benchmark_Declared IS NULL THEN 1 ELSE 0 END) AS null_bm,
    SUM(CASE WHEN SRRI IS NULL THEN 1 ELSE 0 END) AS null_srri
FROM fund_master;

-- B-2: SRRI_Quality_Flag distribution (referencia de salud actual)
SELECT SRRI_Quality_Flag, COUNT(*) n
FROM fund_kiid_metadata
WHERE KIID_Class = 1
GROUP BY SRRI_Quality_Flag;
```

Estas métricas se comparan tras el ciclo siguiente al despliegue.

---

## 5. FASE 1 — IMPLEMENTACIÓN PROPUESTA

### 5.1 Sub-fase 1A — módulo nuevo, sin integrar

**Entregable:** `core/dla_extractor.py` ejecutable de forma aislada.

**Tests unitarios mínimos:**
- 5 PDFs muestreados deben producir output con `is_two_column` correcto en cada página.
- Re-serializar IE0032875985 página 0 debe contener la frase `"Este producto es un subfondo de un OICVM"` íntegra (regex matchea).
- Re-serializar IE0032875985 página 0 NO debe contener `"Tipo tres años"` (frase corrupta eliminada).

**Aprobación necesaria antes de Sub-fase 1B:** José ejecuta los tests, valida outputs.

### 5.2 Sub-fase 1B — integración detrás de kill-switch (`DLA_ENABLED=False`)

**Entregable:** modificación quirúrgica en `core/io.py` (≤10 líneas), kill-switch activo. Ningún cambio de comportamiento por defecto.

**Validación:** ejecutar pipeline normal con `DLA_ENABLED=False`. Resultado: idéntico a antes (test de no-regresión silenciosa por reorganización del código).

### 5.3 Sub-fase 1C — piloto sobre subset

**Entregable:** activar `DLA_ENABLED=True` solo para una lista enumerada de ISINs (los 5 muestreados + 20 más seleccionados de Q-DLA-03 con ≥2 pág en 2-cols). Marcar esos 25 ISINs como `FORCE_REFRESH`.

**Validación post-ciclo:**

```sql
-- V-1C: comparar Raw_KIID_Text antes/después en ISINs piloto.
-- (Requiere snapshot pre-piloto del Raw_KIID_Text de los 25 fondos.)
SELECT km.ISIN,
       LENGTH(km.Raw_KIID_Text) AS len_post,
       p.len_pre,
       fm.Type, fm.Family, fm.Entry_Fee_Pct, fm.Exit_Fee_Pct
FROM fund_kiid_metadata km
JOIN fund_master fm ON fm.ISIN = km.ISIN
JOIN _piloto_snapshot p ON p.ISIN = km.ISIN
WHERE km.ISIN IN (...lista 25...);
```

**Criterios de éxito:**
- 0 fondos con regresión de detección (un atributo poblado pasa a NULL).
- ≥1 fondo con mejora demostrable (Type/Family/Entry/Exit que era NULL ahora poblado, o cambio cualitativo verificable manualmente).

**Si falla:** roll-back con `DLA_ENABLED=False` y análisis de causa.

### 5.4 Sub-fase 1D — despliegue progresivo

Solo si 1C pasa. Activación global con migración natural vía `mark_stale_for_refresh` (180 días, 50 fondos/ciclo). En ~64 ciclos el corpus completo se re-procesa con el nuevo serializador.

### 5.5 Telemetría obligatoria (Principio #7-Logging)

`core/dla_extractor.py` emite log por fondo procesado:

```
[DLA] LU0006277684 layout=[1col, 2col, 2col] strategy=COL_REORDER pages=3
[DLA] FR0000989626 layout=[2col, 1col, 1col] strategy=COL_REORDER pages=3
[DLA-FALLBACK] LU9999999999 layout_undetermined → fallback_pdfplumber
[DLA-OCR] LU8888888888 page_2 capa_texto_vacía → OCR (sin DLA)
```

Resumen de ciclo:

```
--- RESUMEN DLA DEL CICLO ---
Fondos procesados con DLA: 50
Fondos con layout 2-col detectado: 38 (76%)
Fondos con layout 1-col puro: 10 (20%)
Fondos con fallback: 2 (4%)
---
```

---

## 6. RIESGOS Y MITIGACIONES

| # | Riesgo | Severidad | Mitigación |
|---|---|---|---|
| R1 | Regex calibrados en `t_fused = text.replace(" ", "")` (JPMorgan/Amundi capa L0-FUSED de srri_text.py v3) podrían perder cobertura porque la nueva serialización reordena bloques pero **no necesariamente fusiona/deshace los espacios** del mismo modo. | Alta | Sub-fase 1A test específico: tomar un KIID JPMorgan/Amundi del histórico, comparar match de patrones FUSED en kiid_text actual vs aware. Si difiere, ajustar `dla_extractor` para preservar el espaciado intra-columna idéntico (no solo el orden de columnas). |
| R2 | Heurística 0.55 / 0.70 calibrada sobre 5 PDFs puede no generalizar a layouts atípicos (ej. 3 columnas, columnas asimétricas, sidebar). | Media | Q-DLA-03 sobre los 639 PDFs con cache verifica frecuencia de cada tipo de layout. Si aparecen layouts no previstos, ampliar la taxonomía o degradar a fallback con log. |
| R3 | PyMuPDF (`fitz`) es nueva dependencia para `io.py`. Coste de import en cada ejecución. | Baja | `srri_v4_geometric.py` ya importa `fitz`. La dependencia ya está instalada. Coste de import único por proceso, no por fondo. |
| R4 | Coste computacional por fondo: `get_text("blocks")` + lógica de clasificación + reordenación añade overhead respecto a `extract_text()` puro. | Baja | Medición empírica en Fase 1A: tiempo por PDF antes/después. Umbral aceptable: <50ms overhead por fondo. PyMuPDF es notablemente más rápido que pdfplumber, esperable que el cambio neto sea ≤0. |
| R5 | El esquema de "izquierda completa, luego derecha completa" altera el orden lineal de aparición de las secciones. Si un detector busca "Indicador de riesgo" *después* de "Costes" (asumiendo orden de página), la inversión de orden puede confundirlo. | Media | Inspección de código completa de `kiid_parser.py` antes de Fase 1B para identificar detectores con dependencia ordinal entre secciones. Hipótesis preliminar: ninguno depende de orden estricto, pero **debe verificarse**. |
| R6 | `Raw_KIID_Text` post-DLA tiene contenido distinto al previo. Si un fondo CACHED nunca se re-descarga, queda con la versión vieja indefinidamente. | Baja | Aceptable: la mejora se propaga progresivamente vía `mark_stale_for_refresh`. No procede migración masiva forzada (consumiría todos los slots de descarga del periodo). |
| R7 | Tests sobre 5 PDFs no son representativos del universo de 3.195. | Alta | Fase 0 (Q-DLA-03) sobre 639 PDFs en cache es **bloqueante** antes de Fase 1A. |

---

## 7. CRITERIOS DE ÉXITO Y DEFINICIÓN DE CIERRE

### 7.1 Criterios cuantificables de éxito (Sub-fase 1C — piloto)

Sobre los 25 ISINs piloto, comparando ciclo pre vs post:

- **C-1:** 0 fondos con un atributo poblado en pre que esté NULL en post (sin regresión).
- **C-2:** ≥3 fondos con al menos un atributo NULL en pre que esté poblado en post (mejora demostrable).
- **C-3:** Variación de longitud `Raw_KIID_Text` ≤±5% (descartando casos donde la lógica de columnas extiende correctamente el contenido).
- **C-4:** 0 errores de pipeline (ERROR-NotNull, persistencia) introducidos.

### 7.2 Definition of Done — BL-DLA Fase 1 cerrada

Todos los siguientes:
- ✅ Q-DLA-01, 02, 03 ejecutadas y resultados documentados.
- ✅ `core/dla_extractor.py` con tests unitarios pasando.
- ✅ Modificación `core/io.py` integrada con kill-switch.
- ✅ Piloto sobre 25 ISINs ejecutado y validado (criterios C-1 a C-4).
- ✅ Despliegue gradual activado.
- ✅ Resumen de ciclo DLA emitido en log.
- ✅ Backlog actualizado con item BL-DLA-1 cerrado.

### 7.3 Disparadores de roll-back

Cualquiera de estos en cualquier ciclo post-despliegue:
- C-1 fallido (regresión detectada).
- C-4 fallido (errores de pipeline aparecen).
- Variación neta de cobertura agregada negativa (más NULLs introducidos que eliminados).

Acción inmediata: `DLA_ENABLED = False`, análisis de causa, no progresar Fase 2 hasta resolución.

---

## 8. INTEGRACIÓN CON EL BACKLOG

### 8.1 Nuevos ítems sugeridos para `ESTADO_BACKLOG_APR2026_v3_5.md`

**BL-DLA-0 (Diagnóstico)** — Prioridad Alta — categoría preparatoria.
- Ejecutar Q-DLA-01, 02, 03.
- Validar umbrales de Sección 4.2.
- Output: decisión Go/No-Go para BL-DLA-1.

**BL-DLA-1 (Fase 1 — Párrafos 2-col)** — Prioridad por confirmar tras BL-DLA-0.
- Implementar `core/dla_extractor.py` y modificación de `core/io.py`.
- Sub-fases 1A → 1B → 1C → 1D.

**BL-DLA-2 (Fase 2 — Tablas Cat. 1+2 Gemini)** — Prioridad Diferida.
- Solo tras cierre exitoso de BL-DLA-1 con métricas validadas.
- Aborda mejora de Entry_Fee/Exit_Fee/Ongoing_Charge en KIIDs con tablas jerárquicas.

**BL-DLA-3 (Fase 3 — Matrices Cat. 3 Gemini)** — Prioridad Baja.
- Tablas escenarios. Beneficio marginal: actualmente los detectores no extraen escenarios.

**BL-DLA-4 (Fase 4 — OCR-aware DLA)** — Prioridad Muy Baja.
- Solo si Q-DLA-01 muestra alto número de KIIDs sin capa de texto (`Language IS NULL` y patrones FUSED en cobertura).

### 8.2 Encaje en el roadmap actual (sección 10 backlog)

Recomendación:

```
Fase A — Cerrar P1 a estado consolidado:
1. BL-61 — verificación causa raíz (existente)
2. BL-59 — caso límite Restantes (existente)
3. BL-49 — Currency_Hedged (existente)
4. BL-50 — Universe→Geography (existente)
5. BL-DLA-0 — Diagnóstico DLA  ◄── NUEVO, paralelizable

Fase B — Paralela:
6. P2 — factores macro (existente)

Fase C — tras Fase A + BL-DLA-0:
7. BL-DLA-1 — si umbrales Q-DLA-03 lo justifican
   (Si BL-DLA-1 se aprueba, se prioriza sobre Fase D porque su resolución
    aliviaría la presión sobre varios items de Fase D simultáneamente.)

Fase D — Refinamientos diferibles (existente, sin cambios).
```

### 8.3 Principio derivado para añadir a `PRINCIPIOS_DISENO.md`

Si BL-DLA-1 se cierra exitosamente, se añade:

> **Principio #X — Calidad upstream antes que cobertura downstream.**
> Cuando un atributo presenta cobertura insuficiente, antes de añadir nuevos patrones regex en el detector correspondiente, verificar si la causa raíz es la calidad de la entrada que el detector recibe. Una mejora upstream que beneficie a N detectores es preferible a N parches downstream que solo benefician a uno cada uno. Aplicación documentada: BL-DLA-1.

---

## 9. ALTERNATIVAS CONSIDERADAS Y DESCARTADAS

### 9.1 Alternativa A — Configurar pdfplumber con `layout=True`

`page.extract_text(layout=True)` preserva posición de columnas con espacios en blanco. **Descartada** porque:
- No reordena el flujo lógico (sigue siendo top-bottom-left-right).
- Aumenta tamaño del texto con padding decorativo que confunde regex existentes.
- Probado empíricamente: la patología persiste.

### 9.2 Alternativa B — Reescribir todos los regex del parser para tolerar concatenación cruzada

**Descartada** por violar Principio #2 (DRY): obligaría a actualizar 23+ detectores individualmente, cada uno con su propia heurística. Es exactamente el camino que la v24/v25 ha estado tomando, con rendimientos decrecientes documentados.

### 9.3 Alternativa C — Cambiar pdfplumber por PyMuPDF en io.py sin refactor estructural

Sustituir `pdfplumber.extract_text()` por `fitz.page.get_text()` directo. **Descartada** porque:
- `fitz.get_text()` por defecto tiene la misma patología 2D→1D.
- Requiere análisis de bloques para reordenar — es exactamente lo que `dla_extractor.py` hace, pero sin la separación de responsabilidades.

### 9.4 Alternativa D — DLA en kiid_parser, no en io

**Descartada** por las razones expuestas en sección 1.4: rompe el contrato del parser, duplica lógica con `srri_text.py`, mezcla extracción y razonamiento.

---

## 10. CHECKLIST DE APROBACIÓN

Antes de proceder con BL-DLA-0:

- [ ] José revisa el documento completo.
- [ ] José confirma el alcance acotado a Fase 1 (solo párrafos 2-col).
- [ ] José confirma criterio de roll-back automático (sección 7.3).
- [ ] José aprueba la nueva entrada de backlog BL-DLA-0 como bloqueante de BL-DLA-1.
- [ ] Se actualiza `ESTADO_BACKLOG_APR2026_v3_5.md` con los items 8.1.
- [ ] Se programa la sesión Sprint A.X para ejecutar Q-DLA-01, 02, 03.

---

**FIN DEL DOCUMENTO BL-DLA — DECISIÓN DE DISEÑO**

*Pendiente de la aprobación de José antes de cualquier acción de implementación.*
*El documento es autocontenido conforme al principio 5.4 introducido en backlog v3.4.*
