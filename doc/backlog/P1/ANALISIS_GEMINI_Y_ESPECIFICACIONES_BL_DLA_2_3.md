# Análisis de la contribución de Gemini y especificaciones BL-DLA-2 / BL-DLA-3

**Fecha:** 3 de mayo de 2026
**Autor:** José + Claude (sesión Nivel-3 Diseño Arquitectónico, post-cierre BL-DLA-1 1A/1B/1C)
**Estado:** Anexo a backlog v3.7
**Sprint sugerido:** Fase D — diferible tras estabilización de BL-DLA-1 Sub-fase 1D

---

## 1. ANÁLISIS DE LA CONTRIBUCIÓN DE GEMINI

### 1.1 Resumen ejecutivo

Gemini propone un módulo `KIIDLayoutAnalyzer` que combina dos responsabilidades en un único proceso por página:

1. **Extracción y serialización de tablas** (`_extract_and_serialize_tables`) — usa `pdfplumber.page.find_tables()` para localizar tablas, extrae sus celdas, y las serializa al formato `{Fila} || {Columna jerárquica} : {Valor}`.
2. **Extracción y clustering de párrafos** (`_cluster_into_blocks`) — agrupa palabras en líneas por tolerancia Y, ordena cada línea por X, y agrupa líneas en bloques verticales por proximidad (umbrales hardcoded: 30 puntos en Y, 50 puntos en X).

Las contribuciones **conceptuales** de Gemini son sólidas y deben incorporarse a la solución, pero **no son utilizables tal cual** como reemplazo de la implementación vigente. Hay incompatibilidades técnicas, regresiones potenciales, y violaciones de restricciones del proyecto que detallo abajo.

### 1.2 Aportaciones aprovechables

| # | Aportación | Valor para el proyecto |
|---|---|---|
| **G-1** | **Aislamiento semántico tablas / párrafos** vía bounding boxes — recortar virtualmente la región de las tablas antes de procesar el texto del resto. | **Alto.** Resuelve el caso "tablas atravesadas por flujo de párrafos" que hoy no abordamos. Aplicable directamente al diseño de BL-DLA-2. |
| **G-2** | **Serialización 1D preservando jerarquía 2D**: `{row_header} \|\| {col_header} : {cell}` para tablas Categoría 2 (jerárquicas). | **Alto.** Produce texto que un regex puede consumir sin perder la relación celda-encabezado. Es exactamente el patrón que Gemini argumenta correctamente como "interpretable por un LLM o un parser RegEx impecablemente". |
| **G-3** | **Reconocimiento de cabecera jerárquica como primera fila** — heurística suficiente para tablas regulares (Cat. 1 y 2 simples). | **Medio.** La heurística falla en Cat. 3 (matrices con cabecera multi-fila o multi-columna), pero es un buen punto de partida. |
| **G-4** | **Detección explícita de filas vacías** (`if not any(row_texts): continue`) y celdas vacías (`if cell_val: ...`) antes de serializar. | **Medio.** Evita que celdas vacías introduzcan ruido en el output. |
| **G-5** | **Sintaxis de marcadores `--- [INICIO TABLA] --- ... --- [FIN TABLA] ---`** entre regiones de tabla y párrafo. | **Medio.** Útil para que `kiid_parser.py` pueda saltar regiones tabulares cuando un detector busca patrones léxicos solo válidos en prosa, o concentrarse en regiones tabulares cuando un detector busca patrones tabulares. **Atención:** introduce tokens nuevos en `Raw_KIID_Text` que pueden afectar regex existentes — requiere audit (R5 del backlog). |

### 1.3 Problemas técnicos del código de Gemini

#### P-1 — **Incompatibilidad de motor PDF**
Gemini usa `pdfplumber` para todo el flujo. Nuestro `dla_extractor.py v1.2` usa `fitz` (PyMuPDF), por las razones documentadas en BL-DLA-1 (R3 + R4):
- `fitz` ya está importado por `srri_v4_geometric.py`.
- `fitz` es notablemente más rápido que `pdfplumber` (~3-5× según benchmarks publicados).
- `fitz.get_text("blocks")` produce bloques pre-agrupados con coordenadas, evitando el coste de re-agrupar palabras en líneas que tiene `pdfplumber.extract_words()`.

**Implicación:** el código de Gemini debe **portarse** a fitz, no integrarse literalmente. Esto es trabajo no trivial porque:
- `fitz` no tiene una API equivalente directa a `page.find_tables()` con la calidad de pdfplumber. Existe `page.find_tables()` en versiones recientes de PyMuPDF (≥1.23, modelo experimental), pero la fiabilidad sobre KIIDs reales debe validarse.
- Si la detección de tablas en `fitz` resulta inferior, hay dos opciones: (a) usar `pdfplumber` SOLO para la fase de detección+extracción de tablas y `fitz` para todo lo demás; (b) implementar nuestra propia detección de tablas a partir de bloques `fitz`. Opción (a) es la pragmática.

#### P-2 — **Pérdida de fixes ya incorporados a v1.2**
El código de Gemini no incluye:
- **Normalización NBSP / espacios múltiples** (`_normalize_block_text`, BUG-2 documentado para LU1084165304 Fidelity).
- **Fusión de líneas fragmentadas** con contexto de línea siguiente (BUG-1, IE00BZ4D7085, LU1458464713).
- **Manejo de bloques full-width transversales** en TWO_COL (BUG-3, LU0177592218 Schroders).
- **Kill-switch de fallback robusto** ante errores de parseo.
- **Telemetría estructurada** (`layout_meta`, `emit_dla_log`).

**Implicación:** integrar Gemini sin más perdería todos estos fixes. La integración debe ser **aditiva** sobre `dla_extractor.py v1.2`, no sustitutiva.

#### P-3 — **Cláusula `if i >= 3` con número mágico**
`MAX_PDF_PAGES` en `dla_extractor.py` y `io.py` ya tiene este valor en una constante con docstring. Gemini usa `if i >= 3:` con comentario "Tu MAX_PDF_PAGES" — esto evidencia que el autor reconoce la dependencia pero no la importa. Esta es una violación menor de DRY (Principio #2) que requeriría import explícito.

#### P-4 — **Heurística de clustering frágil**
El algoritmo `_cluster_into_blocks` agrupa una línea en un bloque existente si:
```python
(line_top - block['bottom'] < 30) and abs(line_x0 - block['x0']) < 50
```
- Los umbrales `30` y `50` son **hardcoded sin justificación empírica**. En nuestros umbrales (`NARROW_THRESHOLD=0.55`, `FULL_THRESHOLD=0.70`) hay calibración explícita sobre Q-DLA-03 (n=300).
- La condición `abs(line_x0 - block['x0']) < 50` falla en columnas con sangrías variables (listas con bullets, o párrafos justificados con primera línea sangrada).
- La condición de proximidad vertical `< 30` puede unir bloques visualmente separados con interlineado grande (cabeceras separadas del cuerpo por > 30 puntos).

Nuestra implementación actual evita este problema porque trabaja con `fitz.get_text("blocks")`, donde la agrupación ya viene hecha por PyMuPDF según su propio análisis de layout (más fiable que un re-clustering ad hoc).

#### P-5 — **Ordenación final por `(round(top/20)*20, x0)` introduce no-determinismo en bordes**
```python
all_elements.sort(key=lambda e: (round(e['top'] / 20) * 20, e['x0']))
```
Esta heurística asume que dos elementos a menos de 20 puntos en Y deben ordenarse por X. En la práctica, dos bloques con `top=200` y `top=219.9` se consideran "misma altura"; pero `top=200` y `top=220.1` no. Esto puede producir ordenaciones distintas para bloques físicamente equivalentes en PDFs casi idénticos (e.g. variaciones del mismo KIID por gestora). Es la base de bugs intermitentes muy difíciles de diagnosticar.

#### P-6 — **`extracted_table[0]` como cabecera siempre**
La asunción de que la primera fila es siempre la cabecera **falla en tablas Cat. 3** (matrices) que tienen:
- Cabecera en dos filas (e.g. "Período de tenencia / 1 año / 5 años / 10 años" en una fila, y "Si vende / Si mantiene" en la siguiente).
- Cabecera vertical (primera **columna** son los headers, e.g. tablas de escenarios PRIIPS donde la columna izquierda es "Tensión / Desfavorable / Moderado / Favorable").

Sin tratamiento específico de Cat. 3, el output queda léxicamente confuso.

#### P-7 — **Marcadores `--- [INICIO TABLA] ---` rompen capa L0-FUSED de srri_text.py v3**
La capa L0-FUSED de `srri_text.py v3` aplica `t_fused = text.replace(" ", "")`. Si el texto contiene `--- [INICIO TABLA] ---`, tras el fused queda `---[INICIOTABLA]---`. Los regex actuales no esperan estos tokens y podrían matchear dentro de ellos por accidente, o saltarlos perdiendo contexto. **Esta es una regresión potencial alta.** La integración del marcador requiere o bien:
- Un audit completo de `srri_text.py` y `kiid_parser.py` para verificar que ningún regex se confunde.
- O bien marcadores que sobrevivan al fused sin matchear nada (e.g. usar caracteres unicode no-ASCII que el fused no toca, como `\u2502[TABLE]\u2502`, pero esto introduce dependencias unicode peligrosas).

La opción más segura es **omitir los marcadores** y emitir solo el contenido serializado de la tabla rodeado de saltos de línea. Esto preserva compatibilidad con `srri_text.py` y `kiid_parser.py` sin tocar nada downstream.

#### P-8 — **Risk en `_is_in_any_bbox` con tolerancia ±2**
La tolerancia de ±2 puntos para considerar "palabra dentro de bbox de tabla" es muy estricta. En tablas con bordes finos o sin bordes (frecuente en KIIDs), `pdfplumber.find_tables()` calcula bbox por inferencia y puede dejar palabras del título a 3-5 puntos por encima de la primera fila. Estas palabras se asignan al flujo de texto en lugar de a la tabla, causando duplicación o pérdida de contexto.

#### P-9 — **No hay manejo de tablas anidadas o tablas dentro de párrafos**
KIIDs de Allianz, AXA, BNP Paribas frecuentemente intercalan mini-tablas dentro de un párrafo de texto (e.g. "los costes son: [tabla de 2 filas con costes] que se revisan anualmente"). El código de Gemini extrae la tabla y la pone al final del flujo (por la sort por `top`), perdiendo el contexto frasístico que precedía.

### 1.4 Implicaciones para BL-DLA-2 y BL-DLA-3

Las contribuciones de Gemini son **una excelente referencia conceptual**, pero la implementación efectiva en nuestro proyecto debe:

1. **Portarse a `fitz`** o usar un patrón híbrido (`pdfplumber` solo para tablas, `fitz` para texto y layout).
2. **Integrarse aditivamente sobre `dla_extractor.py v1.2`**, no sustituir ninguna lógica existente.
3. **Calibrar todos los umbrales** sobre el corpus de KIIDs (similar al ejercicio Q-DLA-03 de BL-DLA-0). Heurísticas hardcoded sin calibración no son aceptables (R-2 del proyecto: evidencia cuantitativa antes de implementar).
4. **No introducir marcadores `--- [INICIO TABLA] ---` sin audit completo** de `kiid_parser.py` y `srri_text.py` ante posibles regresiones.
5. **Construir taxonomía explícita Cat. 1 / 2 / 3** para tablas, con detector específico por tipo y serializadores separados, permitiendo cierre incremental por categoría.
6. **Tratar Cat. 3 (matrices) como ítem separado** (BL-DLA-3), porque la heurística "primera fila = cabecera" no aplica.

---

## 2. ESPECIFICACIÓN BL-DLA-2 — TABLAS CATEGORÍA 1 + CATEGORÍA 2

### 2.1 Definición de Categorías de Tabla (taxonomía Gemini, refinada)

| Cat. | Estructura | Ejemplo en KIID | Patología sin DLA |
|------|------------|-----------------|-------------------|
| **Cat. 1** | Tabla simple, 2 columnas, sin cabecera de columna explícita. Patrón `clave : valor`. | Tabla "Detalles del producto": `ISIN \| LU0006277684`, `Divisa \| EUR`, `Fecha de lanzamiento \| 1992`. | Línea pegada sin separador puede confundirse con prosa: "ISIN LU0006277684 Divisa EUR Fecha de lanzamiento 1992". Detectores que esperan `ISIN[:\s]+([A-Z]{2}[0-9]{10})` pueden funcionar, pero `Fund_Currency` o fechas pueden quedar ambiguos. |
| **Cat. 2** | Tabla regular con cabecera de columna en la primera fila, cabecera de fila en la primera columna. Cuerpo numérico o textual. | Tabla "Composición de costes": cabeceras de columna `Si vende tras 1 año / Si mantiene 5 años`, filas `Costes de entrada / Costes de salida / Costes recurrentes`. | Sin DLA, las celdas se intercalan: `Costes de entrada 0.00% 0.00% Costes de salida 0.00% 0.00%`. Los detectores `_detect_entry_fee` v24/v25 prio. 12 lo manejan via `t_fused`, pero solo para JPMorgan/Amundi específicamente. |
| **Cat. 3** | Matriz con cabecera multi-fila o multi-columna, o tabla con celdas combinadas (rowspan/colspan). | Tabla "Escenarios de rentabilidad PRIIPS": cabecera de 2 filas (`Período de tenencia / Si vende tras 1 año / Si mantiene 5 años / Si mantiene 10 años` + `Coste / Rentabilidad / Coste / Rentabilidad / Coste / Rentabilidad`). | Imposible de serializar correctamente con heurística "primera fila = cabecera". Requiere análisis estructural específico. |

### 2.2 BL-DLA-2 — Estado y prioridad

- **Estado:** **ABIERTO en v3.7**, prioridad Media. Diferible hasta estabilización completa de Sub-fase 1D de BL-DLA-1.

- **Causa raíz atacada:** las tablas Cat. 1 y Cat. 2 incrustadas en KIIDs son hoy serializadas por `fitz.get_text("blocks")` en orden Y, lo que produce líneas con celdas intercaladas o concatenadas sin estructura clave-valor. Los detectores downstream (`_detect_entry_fee`, `_detect_exit_fee`, `_detect_ongoing_charge`, `_detect_currency_hedged` cuando lee tabla de share classes) deben suplir esta carencia con heurísticas regex agresivas — la causa raíz es la pérdida de jerarquía en upstream.

- **Hipótesis de impacto cuantitativo (para validar antes de codificar):** los 115 fondos `Entry_Fee_Pct=NULL ∧ Fee_Known_Flag='NOT_FOUND'` y los 676 fondos `Exit_Fee_Pct=NULL` (ver BL-51A residual y BL-55) podrían beneficiarse parcialmente de serialización tabular correcta. Estimación previa: 30-50% de los `Exit_Fee_Pct=NULL` corresponden a "celda 0% en tabla Cat. 2". Validar empíricamente con la query Q-DLA-04 abajo.

### 2.3 Sub-fase 2-PRE — Diagnóstico cuantitativo obligatorio (BL-DLA-2-DIAG)

Antes de implementar nada, ejecutar:

**Q-DLA-04 — Conteo de tablas por KIID en corpus muestral (n=200):**

```python
# scripts/diag/dla_table_inventory.py
import pdfplumber
import csv
from collections import Counter

samples = sample_isins_with_pdf_cache(n=200, seed=42)
results = []
for isin in samples:
    pdf = load_from_cache(isin)
    with pdfplumber.open(pdf) as doc:
        n_tables_total = 0
        n_pages_with_tables = 0
        table_shapes = []   # list of (n_rows, n_cols) per table
        for page in doc.pages[:3]:   # MAX_PDF_PAGES
            tables = page.find_tables()
            if tables:
                n_pages_with_tables += 1
            for t in tables:
                extracted = t.extract()
                if extracted:
                    n_rows = len(extracted)
                    n_cols = max(len(r) for r in extracted) if extracted else 0
                    table_shapes.append((n_rows, n_cols))
                    n_tables_total += 1
        results.append({
            'isin': isin,
            'n_tables_total': n_tables_total,
            'n_pages_with_tables': n_pages_with_tables,
            'shapes': table_shapes,
        })

# Output CSV: isin, n_tables_total, n_pages_with_tables, shapes_csv
write_csv(results, 'dla_table_inventory.csv')
```

**Q-DLA-05 — Clasificación heurística Cat. 1/2/3 sobre el inventario:**

```python
def classify_table(shape):
    n_rows, n_cols = shape
    if n_cols == 2 and n_rows <= 8:
        return 'CAT_1'
    if 3 <= n_cols <= 6 and 2 <= n_rows <= 12:
        return 'CAT_2'
    if n_cols >= 4 and n_rows >= 4:
        return 'CAT_3'
    return 'UNKNOWN'

# Distribución esperada (hipótesis a validar):
# CAT_1: 50-60% (tabla detalles producto)
# CAT_2: 30-40% (tabla costes PRIIPS estándar)
# CAT_3: 5-15% (matriz escenarios)
# UNKNOWN: <5%
```

**Umbrales de decisión para implementación:**

| Resultado Q-DLA-04/05 | Decisión |
|----------------------|----------|
| Cat. 2 detectada en ≥40% de fondos del corpus | **Proceder con BL-DLA-2 priorizado.** ROI alto. |
| Cat. 2 entre 20-40% | **Proceder en piloto** (50 ISINs con tabla Cat. 2 detectada). Re-evaluar tras piloto. |
| Cat. 2 < 20% | **Diferir BL-DLA-2.** Revisar si los detectores específicos (`_detect_entry_fee` prio. 12) ya cubren los casos. |

### 2.4 Sub-fase 2A — Módulo nuevo `dla_table_serializer.py`

**Entregable:** módulo aislado `proyecto1/core/dla_table_serializer.py` con:

```python
# proyecto1/core/dla_table_serializer.py
# -*- coding: utf-8 -*-
"""
Document Layout Analysis — Fase 2: serialización de tablas Cat. 1 y Cat. 2.

Versión: 1.0  (BL-DLA-2 Sub-fase 2A)

CONTRATO EXTERNO:
    serialize_tables_for_page(
        page_pdfplumber : pdfplumber.page.Page,
        debug : bool = False,
    ) -> tuple[list[dict], list[tuple[float, float, float, float]]]

    Devuelve:
        serialized_tables — list de dicts:
            [{
              'category': 'CAT_1' | 'CAT_2' | 'CAT_3' | 'UNKNOWN',
              'bbox':     (x0, y0, x1, y1),
              'text':     str,           # texto serializado 1D-aware
              'top':      float,         # y0 para ordenación con bloques de párrafo
              'x0':       float,
            }]
        bboxes — list de bboxes para que el extractor de párrafos las excluya.

DISEÑO:
    Cat. 1 (2 columnas): {clave} : {valor} por fila
    Cat. 2 (matriz regular con cabecera fila 0): {row_header} || {col_header} : {valor}
    Cat. 3 (matriz multi-cabecera): NO se procesa en Fase 2 — devuelve UNKNOWN
                                     y delega a serialización por defecto del extractor base.
    UNKNOWN: idem.

DEPENDENCIA:
    pdfplumber para detección de tablas (page.find_tables()).
    fitz NO se usa aquí — la coordinación con fitz.get_text("blocks") la gestiona
    el caller en dla_extractor.py.

INTEGRACIÓN:
    Llamado desde dla_extractor.extract_text_dla_aware() para cada página.
    Las bboxes devueltas se usan para excluir las palabras dentro de tablas
    en el flujo de párrafos.
"""

from __future__ import annotations
import re
from typing import Optional
import pdfplumber

# Umbrales de clasificación de tabla (calibrar sobre Q-DLA-05)
CAT1_MAX_COLS = 2
CAT1_MAX_ROWS = 8
CAT2_MIN_COLS = 3
CAT2_MAX_COLS = 6
CAT2_MIN_ROWS = 2
CAT2_MAX_ROWS = 12

# Caracteres de separación (consistentes con dla_extractor)
ROW_HEADER_SEP    = " || "    # separador entre row y col header
KV_SEP            = " : "     # separador clave-valor
DEFAULT_ROW_LABEL = "Valor"   # cuando la primera celda de una fila está vacía


def _normalize_cell(text: str) -> str:
    """Normaliza el contenido de una celda: NBSP, espacios múltiples, LF interno."""
    if not text:
        return ""
    text = re.sub(r'[ \xa0]+', ' ', text)
    text = re.sub(r'\s*\n\s*', ' ', text)
    return text.strip()


def _classify_table_shape(extracted: list) -> str:
    """Clasifica la tabla según su forma (n_rows, n_cols)."""
    if not extracted or len(extracted) < 2:
        return 'UNKNOWN'
    n_rows = len(extracted)
    n_cols = max(len(r) for r in extracted) if extracted else 0
    if n_cols <= 0:
        return 'UNKNOWN'
    if n_cols <= CAT1_MAX_COLS and n_rows <= CAT1_MAX_ROWS:
        return 'CAT_1'
    if (CAT2_MIN_COLS <= n_cols <= CAT2_MAX_COLS
            and CAT2_MIN_ROWS <= n_rows <= CAT2_MAX_ROWS):
        return 'CAT_2'
    return 'UNKNOWN'   # CAT_3 o casos no previstos → fallback


def _serialize_cat1(extracted: list) -> str:
    """
    Cat. 1: tabla 2 columnas → {clave} : {valor} por fila.
    No se asume cabecera; cada fila es un par independiente.
    """
    rows = []
    for row in extracted:
        cells = [_normalize_cell(c) for c in row]
        if not any(cells):
            continue
        if len(cells) == 1:
            rows.append(cells[0])
        elif len(cells) >= 2 and cells[0] and cells[1]:
            rows.append(f"{cells[0]}{KV_SEP}{cells[1]}")
        elif cells[0]:
            rows.append(cells[0])
    return "\n".join(rows)


def _serialize_cat2(extracted: list) -> str:
    """
    Cat. 2: matriz regular con cabecera en fila 0 y row-header en columna 0.
    Serialización: {row_header} || {col_header} : {valor} por celda no-vacía.

    Equivalente al patrón propuesto por Gemini (`_extract_and_serialize_tables`)
    con adaptaciones:
      - normalización NBSP / LF interno por celda (BUG-2 solventado).
      - row_header ausente → DEFAULT_ROW_LABEL ("Valor"), igual que Gemini.
      - col_header ausente → "Columna_{i}" (igual que Gemini).
      - filas y celdas vacías se omiten.
    """
    if len(extracted) < 2:
        return ""
    headers = [_normalize_cell(h) for h in extracted[0]]
    rows = []
    for row in extracted[1:]:
        cells = [_normalize_cell(c) for c in row]
        if not any(cells):
            continue
        row_header = cells[0] if cells[0] else DEFAULT_ROW_LABEL
        for i in range(1, len(cells)):
            col_header = headers[i] if i < len(headers) and headers[i] else f"Columna_{i}"
            cell_val = cells[i]
            if cell_val:
                rows.append(f"{row_header}{ROW_HEADER_SEP}{col_header}{KV_SEP}{cell_val}")
    return "\n".join(rows)


def serialize_tables_for_page(
    page_pdfplumber: pdfplumber.page.Page,
    debug: bool = False,
) -> tuple:
    """
    Detecta y serializa tablas Cat. 1 y Cat. 2 de una página.
    Cat. 3 / UNKNOWN se devuelven con flag para que el caller decida fallback.
    """
    serialized: list = []
    bboxes: list = []
    try:
        found = page_pdfplumber.find_tables()
    except Exception:
        return serialized, bboxes

    for table_obj in found:
        bbox = tuple(table_obj.bbox)
        try:
            extracted = table_obj.extract()
        except Exception:
            continue
        if not extracted:
            continue

        cat = _classify_table_shape(extracted)
        if cat == 'CAT_1':
            text = _serialize_cat1(extracted)
        elif cat == 'CAT_2':
            text = _serialize_cat2(extracted)
        else:
            # CAT_3 o UNKNOWN: serializar con heurística simple para no
            # perder información, pero marcar como degradado.
            text = _serialize_cat1(extracted)   # fallback conservador

        if not text:
            continue

        serialized.append({
            'category': cat,
            'bbox':     bbox,
            'text':     text,
            'top':      bbox[1],
            'x0':       bbox[0],
        })
        bboxes.append(bbox)

    return serialized, bboxes
```

**Tests obligatorios (Sub-fase 2A):**

```python
def test_dla2_cat1_simple_kv_table():
    """Cat. 1: tabla 2-col con detalles de producto → KV serializado."""
    extracted = [
        ["ISIN", "LU0006277684"],
        ["Divisa", "EUR"],
        ["Fecha lanzamiento", "1992"],
    ]
    text = _serialize_cat1(extracted)
    assert "ISIN : LU0006277684" in text
    assert "Divisa : EUR" in text
    assert "Fecha lanzamiento : 1992" in text

def test_dla2_cat2_priips_costs_table():
    """Cat. 2: tabla costes PRIIPS → row||col:valor serializado."""
    extracted = [
        ["",                   "Si vende tras 1 año", "Si mantiene 5 años"],
        ["Costes de entrada",  "0,00 €",              "0,00 €"],
        ["Costes de salida",   "0,00 €",              "0,00 €"],
        ["Costes recurrentes", "150 €",               "750 €"],
    ]
    text = _serialize_cat2(extracted)
    assert "Costes de entrada || Si vende tras 1 año : 0,00 €" in text
    assert "Costes recurrentes || Si mantiene 5 años : 750 €" in text

def test_dla2_cat2_empty_row_header_uses_default():
    """Cat. 2: fila con primera celda vacía → DEFAULT_ROW_LABEL."""
    extracted = [
        ["",  "Col A", "Col B"],
        ["",  "1",     "2"],
    ]
    text = _serialize_cat2(extracted)
    assert "Valor || Col A : 1" in text

def test_dla2_cat2_nbsp_collapsed():
    """Cat. 2: NBSP en celda → un único espacio (BUG-2)."""
    extracted = [
        ["",                "Col\xa0A"],
        ["Row\xa0\xa0X",   "1\xa0,\xa00"],
    ]
    text = _serialize_cat2(extracted)
    # No NBSP, no doble espacio
    assert "\xa0" not in text
    assert "  " not in text

def test_dla2_cat3_falls_back_to_cat1_form():
    """Cat. 3 (matriz grande): se serializa con heurística simple, marcada UNKNOWN."""
    extracted = [
        ["", "Año 1 Coste", "Año 1 Rent", "Año 5 Coste", "Año 5 Rent", "Año 10 Coste", "Año 10 Rent"],
        ["Tensión",     "100", "-50%", "200", "-30%", "500", "-10%"],
        ["Desfavorable","50",  "-10%", "100", "-5%",  "200", "0%"],
        ["Moderado",    "20",  "5%",   "50",  "8%",   "100", "10%"],
        ["Favorable",   "10",  "20%",  "20",  "25%",  "50",  "30%"],
    ]
    cat = _classify_table_shape(extracted)
    assert cat == 'UNKNOWN'   # 7 cols × 5 filas → fuera de Cat. 2

def test_dla2_empty_table_returns_empty():
    """Tabla con todas celdas vacías → text vacío, no error."""
    extracted = [["",""], ["",""]]
    text = _serialize_cat2(extracted)
    assert text == ""

def test_dla2_serialize_tables_for_page_real_pdf_lu0006277684():
    """Smoke test sobre LU0006277684: ≥1 tabla Cat. 2 detectada con costes."""
    # ... (carga real de LU0006277684 desde cache, page.find_tables, valida output)
```

**Criterios de salida Sub-fase 2A:**
- AST OK del nuevo módulo.
- 7 tests unitarios pasando.
- Smoke test sobre 5 ISINs canónicos (LU0006277684, IE0032875985, FR0000989626, IE00B45H7020, LU0070177588) con tabla Cat. 2 verificada manualmente: el texto serializado contiene `Costes de entrada` y `Costes de salida` con los valores correctos.

### 2.5 Sub-fase 2B — Integración en `dla_extractor.py` con kill-switch

**Entregable:** modificación quirúrgica en `dla_extractor.py v1.3` que:

1. Añade una constante de control:
   ```python
   # Sub-flag DLA Fase 2: serialización tabular Cat. 1+2.
   # Permite roll-back de Fase 2 sin desactivar Fase 1.
   DLA_TABLE_SERIALIZATION_ENABLED = False   # default OFF en 2B
   ```

2. Modifica la API interna `_serialize_page` para aceptar tablas:
   ```python
   def _serialize_page(
       page_fitz: fitz.Page,
       layout: str,
       page_pdfplumber: Optional[pdfplumber.page.Page] = None,
   ) -> str:
       blocks = _get_text_blocks(page_fitz)

       if layout == _LABEL_NO_TEXT or not blocks:
           return ""

       # ── Sub-fase 2B: serialización tabular ─────────────────────────
       table_segments = []
       table_bboxes   = []
       if DLA_TABLE_SERIALIZATION_ENABLED and page_pdfplumber is not None:
           from core.dla_table_serializer import serialize_tables_for_page
           try:
               table_segments, table_bboxes = serialize_tables_for_page(page_pdfplumber)
           except Exception:
               table_segments = []
               table_bboxes   = []

       # Filtrar bloques fitz que caigan dentro de bbox de tabla
       if table_bboxes:
           blocks = [b for b in blocks if not _block_in_any_bbox(b, table_bboxes)]
       # ───────────────────────────────────────────────────────────────

       if layout == _LABEL_TWO:
           para_text = _serialize_two_col(blocks, page_fitz.rect.width)
       else:
           para_text = _serialize_single_col(blocks)

       # Combinar tablas + párrafos respetando orden Y
       if table_segments:
           # Mezclar tablas y bloques de párrafo en orden Y
           # (ver detalle en 2.6 abajo)
           return _merge_tables_and_paragraphs(table_segments, para_text, blocks)

       return para_text
   ```

3. La función `extract_text_dla_aware` abre `pdfplumber` UNA SOLA VEZ (no por página) para evitar coste duplicado:
   ```python
   def extract_text_dla_aware(pdf_bytes, ocr_enabled=True, ...):
       # Apertura dual: fitz para layout/párrafos, pdfplumber para tablas.
       doc_fitz = fitz.open(stream=pdf_bytes, filetype="pdf")
       doc_pp   = None
       if DLA_TABLE_SERIALIZATION_ENABLED:
           try:
               import pdfplumber
               doc_pp = pdfplumber.open(BytesIO(pdf_bytes))
           except Exception:
               doc_pp = None
       try:
           for i, page_fitz in enumerate(doc_fitz):
               # ...
               page_pp = doc_pp.pages[i] if (doc_pp and i < len(doc_pp.pages)) else None
               page_text = _serialize_page(page_fitz, final_layout, page_pp)
               # ...
       finally:
           if doc_pp:
               doc_pp.close()
           doc_fitz.close()
   ```

**Criterios de salida Sub-fase 2B:**
- Pipeline ejecutado con `DLA_TABLE_SERIALIZATION_ENABLED=False` produce salida idéntica a Fase 1 (test de no-regresión silenciosa).
- Pipeline ejecutado con `DLA_TABLE_SERIALIZATION_ENABLED=True` sobre 5 ISINs canónicos: el texto resultante contiene literalmente `Costes de entrada || Si vende tras 1 año : 0,00 €` (o equivalente).
- Tiempo medio de procesamiento por fondo: ≤ 250 ms (vs ~150 ms en Fase 1; aumento aceptable de ~100 ms por la apertura adicional de pdfplumber).

### 2.6 Sub-fase 2B-bis — Mezcla tablas + párrafos respetando orden Y

**Entregable:** función `_merge_tables_and_paragraphs(table_segments, para_text, para_blocks)` que produce un texto único en el cual cada tabla aparece **inmediatamente después del último bloque de párrafo cuyo y0 < bbox_table.y0**, preservando flujo lectura.

**Algoritmo:**
1. Crear una lista de elementos `(top, kind, content)` donde:
   - cada bloque de párrafo aporta `(b.y0, 'para', b.text)`
   - cada segmento de tabla aporta `(t.bbox.y0, 'table', t.text)`
2. Ordenar por `top`.
3. Reagrupar bloques de párrafo consecutivos según la lógica de columnas (TWO_COL si aplica) o natural (SINGLE_COL).
4. Emitir el texto entrelazando, con `\n` entre cada elemento (NO usar marcadores `--- [INICIO TABLA] ---` por las razones de P-7 arriba).

**Implementación de referencia:**

```python
def _merge_tables_and_paragraphs(table_segments, para_blocks, layout, page_w):
    """
    Mezcla bloques de párrafo y tablas serializadas en un único texto
    1D que preserva el orden Y (flujo de lectura humano).

    table_segments: list de dicts {bbox, text, top, x0, category}
    para_blocks:    list de tuplas fitz (x0, y0, x1, y1, text, ...)
    layout:         _LABEL_SINGLE | _LABEL_TWO
    page_w:         page width

    Estrategia:
        - Crear lista unificada de elementos con su 'top' y orden por Y.
        - Para SINGLE_COL: emisión natural por (top, x0).
        - Para TWO_COL: las tablas se tratan como bloques full-width y se
          emiten primero (en orden Y), luego columna izq, luego dcha.
          Esta regla es consistente con el manejo de bloques full-width
          ya existente en _serialize_two_col (BUG-3).
    """
    if layout == _LABEL_TWO:
        # Tablas como elementos full-width → emitir primero en orden Y
        full = sorted(
            [{'top': t['top'], 'text': t['text']} for t in table_segments],
            key=lambda e: e['top']
        )
        mid_x = page_w / 2
        # Bloques de columna ya están en para_blocks, separar por col
        left  = sorted([b for b in para_blocks if (b[0]+b[2])/2 < mid_x],
                       key=lambda b: (b[1], b[0]))
        right = sorted([b for b in para_blocks if (b[0]+b[2])/2 >= mid_x],
                       key=lambda b: (b[1], b[0]))
        parts = []
        if full:
            parts.append("\n".join(e['text'] for e in full))
        if left:
            parts.append("\n".join(_normalize_block_text(b[4]) for b in left))
        if right:
            parts.append("\n".join(_normalize_block_text(b[4]) for b in right))
        return "\n".join(parts)
    # SINGLE_COL: emitir todos los elementos por orden Y
    items = []
    for t in table_segments:
        items.append((t['top'], 'table', t['text']))
    for b in para_blocks:
        items.append((b[1], 'para', _normalize_block_text(b[4])))
    items.sort(key=lambda e: e[0])
    return "\n".join(item[2] for item in items)
```

### 2.7 Sub-fase 2C — Piloto sobre 25 ISINs

**Entregable:** activar `DLA_TABLE_SERIALIZATION_ENABLED=True` para 25 ISINs piloto:
- 5 ISINs canónicos de Sub-fase 2A.
- 10 ISINs adicionales con `Exit_Fee_Pct=NULL ∧ Fee_Known_Flag IN ('NOT_FOUND','EXIT_INFERRED_ZERO')` y tabla Cat. 2 detectada por Q-DLA-04.
- 10 ISINs adicionales con `Entry_Fee_Pct=NULL ∧ Fee_Known_Flag='NOT_FOUND'` y tabla Cat. 2 detectada.

**Criterios de éxito (similar a 1C, ver BL_DLA_DESIGN_DECISION.md sección 7.1):**
- **C2-1:** 0 fondos con regresión de detección (atributo poblado pasa a NULL). Aplicar exclusión BL-DLA-C3-EXCL si baseline pdfplumber corrupta.
- **C2-2:** ≥5 fondos con mejora demostrable en `Entry_Fee_Pct` o `Exit_Fee_Pct` o `Ongoing_Charge` (NULL → poblado).
- **C2-3:** Variación de longitud `Raw_KIID_Text` ≤+15% (las tablas serializadas pueden expandir el texto significativamente; +15% es tolerable).
- **C2-4:** 0 errores de pipeline.
- **C2-5:** ningún regex de `_detect_entry_fee` v24/v25 prio. 12 (capa L0-FUSED JPMorgan/Amundi) deja de matchear sobre los KIIDs JPMorgan/Amundi del piloto. Test específico: añadir 2 ISINs JPMorgan en el subset piloto y validar match.

### 2.8 Sub-fase 2D — Despliegue progresivo global

Solo si 2C pasa. Activar `DLA_TABLE_SERIALIZATION_ENABLED=True` global. Migración natural vía `mark_stale_for_refresh` (igual que Sub-fase 1D).

### 2.9 Riesgos específicos BL-DLA-2

| # | Riesgo | Severidad | Mitigación |
|---|---|---|---|
| R8 | Tablas Cat. 3 (escenarios PRIIPS) clasificadas como UNKNOWN producen serialización degradada que puede insertar concatenaciones no semánticas en el flujo. | Media | Cat. 3 cae a `_serialize_cat1` que serializa fila a fila como pares clave-valor. En el peor caso, esto produce líneas como `Tensión : 100`, no peor que el flujo actual sin DLA Fase 2. **Antes de activar 2C**, revisar manualmente que ningún detector de `kiid_parser.py` se confunde con esto. |
| R9 | Apertura dual fitz + pdfplumber duplica memoria por PDF. Para PDFs > 5 MB el coste es notable. | Media | `MAX_PDF_MB=20` ya limita. Telemetría: medir incremento de memoria por fondo en piloto 2C. Umbral aceptable: ≤ 100 MB pico por proceso. |
| R10 | `pdfplumber.find_tables()` produce false positives en PDFs sin tablas reales (líneas decorativas, separadores horizontales). Una "tabla detectada" vacía contamina el flujo. | Media | Filtro pre-existente en serializador: `if not extracted or len(extracted) < 2: skip`. Refuerzo: añadir filtro `if total_chars(extracted) < 20: skip` para descartar tablas residuales. |
| R11 | Detección de tabla pisa información que estaba siendo capturada por bloques de párrafo (e.g. KIID donde la información de costes está en prosa, no en tabla). | Media | Validación piloto C2-1 sobre KIIDs sin tablas (control negativo): el output debe ser idéntico al de Fase 1 cuando no se detectan tablas. |
| R12 | `pdfplumber.find_tables()` cambia entre versiones de pdfplumber. La versión actualmente instalada debe pinearse en `requirements.txt`. | Baja | Añadir version pin en setup. |
| R13 | El cambio de orden `tabla → párrafo` en SINGLE_COL puede romper detectores con dependencia ordinal sutil (e.g. "buscar el primer X tras 'Costes y comisiones'"). | Media | Auditar `kiid_parser.py` (similar a R5 de Fase 1) específicamente para detectores que usen `kiid_text.find(...)` con secuencia esperada. Si encuentra, documentar en el listado y validar que el orden Y se preserva. |

### 2.10 Beneficios esperados (cuantificados)

Asumiendo que Q-DLA-04/05 confirma Cat. 2 en ≥40% del corpus:

- **Entry_Fee_Pct:** reducción de NULL desde 129 a ≤ 70 (mejora estimada: 50%).
- **Exit_Fee_Pct:** reducción de NULL desde 676 a ≤ 350 (mejora estimada: 50%, complementaria a BL-55 que ya identifica casos `EXIT_INFERRED_ZERO`).
- **Ongoing_Charge:** reducción residual desde 74 a ≤ 50.
- **Eliminación de la capa L0-FUSED** específica de JPMorgan/Amundi en `srri_text.py v3` y `_detect_entry_fee` v24/v25 prio. 12 — los regex pueden volver a operar sobre texto con espacios normales. (Esto es un beneficio estructural diferido, no se ejecuta en 2D, requiere proyecto de simplificación posterior.)

---

## 3. ESPECIFICACIÓN BL-DLA-3 — TABLAS CATEGORÍA 3 (MATRICES)

### 3.1 Definición y casos de uso

**Cat. 3** son tablas con cabecera multi-fila o multi-columna, o con celdas combinadas (rowspan/colspan), o con la cabecera en la primera columna en lugar de la primera fila.

**Casos canónicos en KIIDs:**

1. **Matriz escenarios PRIIPS:** cabecera de 2 filas (`Período de tenencia` × `Coste/Rentabilidad`), 4 filas de cuerpo (Tensión, Desfavorable, Moderado, Favorable).
2. **Matriz costes desglosados:** cabecera vertical en columna 0 (`Costes únicos / Costes recurrentes / Costes accesorios`), columnas son items.
3. **Matriz performance histórica anual:** cabecera horizontal con años, fila única con %.
4. **Matriz comparativa de share classes:** cabecera horizontal con clases, filas con atributos (ISIN, divisa, distribución, comisiones por clase).

### 3.2 BL-DLA-3 — Estado y prioridad

- **Estado:** **ABIERTO en v3.7**, prioridad Baja. Diferible hasta cierre de BL-DLA-2 con métricas validadas.

- **Causa raíz atacada:** las matrices con cabecera multi-eje no son procesables por la heurística "primera fila = cabecera" (Gemini) ni por nuestra lógica de Cat. 1+2. Hoy estos contenidos producen serialización lineal sin estructura, lo que impide a detectores específicos extraer información (e.g. extracción de escenarios PRIIPS para análisis de risk profile, prevista en P3 régimen-dependiente).

- **Hipótesis de impacto:** las tablas Cat. 3 en KIIDs son principalmente:
  - Matrices PRIIPS de escenarios → no extraídas hoy. Su contenido sería útil en P2/P3 (volatilidad histórica, escenario tensión).
  - Matrices comparativas de share classes → potencialmente útiles para resolver `Currency_Hedged` por clase específica (BL-49).

  El beneficio inmediato sobre el corpus actual es bajo (los detectores existentes no usan estos datos). Sin embargo, BL-DLA-3 desbloquea el desarrollo de detectores P2/P3 dependientes de estos datos.

### 3.3 Sub-fase 3-PRE — Diagnóstico cuantitativo

**Q-DLA-06 — Inventario de tablas Cat. 3 en corpus muestral:**

```python
# Reutilizar dla_table_inventory.py (Q-DLA-04) y filtrar shapes Cat. 3:
# n_cols >= 4 AND n_rows >= 4
# Output: distribución por gestora, % del corpus afectado.
```

**Umbrales de decisión:**

| Resultado | Decisión |
|-----------|----------|
| Cat. 3 en ≥30% de fondos con detector P2/P3 planificado | **Implementar BL-DLA-3 antes de P3.** |
| Cat. 3 entre 10-30% | **Diferir BL-DLA-3 hasta inicio de P3.** |
| Cat. 3 < 10% | **Diferir indefinidamente.** Implementar parsing escenarios ad-hoc en P2 si necesario. |

### 3.4 Sub-fase 3A — Detector y serializador específicos

**Aproximación técnica:** el problema esencial es identificar:
- Si la cabecera ocupa **2+ filas** (cabecera horizontal expandida).
- Si la cabecera ocupa **1+ columna** (cabecera vertical con row-headers reales).
- Si hay **celdas combinadas** (mismo valor repetido en celdas adyacentes que indican rowspan/colspan).

**Heurística propuesta (calibrar sobre Q-DLA-06):**

```python
def detect_table_cat3_structure(extracted: list) -> dict:
    """
    Analiza la estructura de una tabla para determinar:
        - n_header_rows: cuántas filas iniciales son cabecera.
        - n_header_cols: cuántas columnas iniciales son cabecera (row-headers).
        - shape: 'matrix_2d_header' | 'transposed' | 'compound' | 'unknown'

    Heurísticas:
        H1 — n_header_rows > 1 si las primeras 2 filas tienen cuerpo
             contradictorio: fila 0 con tokens ['Período','tenencia','años']
             y fila 1 con tokens ['Coste','Rentabilidad','%','€'].
        H2 — n_header_cols > 1 si la columna 0 tiene tokens léxicamente
             coherentes (e.g. todos son palabras/frases sin números) y
             la columna 1 tiene tokens numéricos o monetarios.
        H3 — Detección de rowspan: dos celdas adyacentes con el mismo
             valor en una columna que es header (n_header_cols > 0).

    Return: dict con campos n_header_rows, n_header_cols, shape, confidence.
    """
    # Implementación pendiente de calibración sobre Q-DLA-06.
    ...


def _serialize_cat3(extracted: list, structure: dict) -> str:
    """
    Cat. 3: serializa según la estructura detectada.

    matrix_2d_header (H1+H2):
        Construir cabeceras compuestas concatenando filas 0..n_header_rows-1
        (e.g. "Si vende 1 año - Coste", "Si vende 1 año - Rentabilidad").
        Luego serializar como Cat. 2 con esas cabeceras compuestas.

    transposed (H2 dominante):
        Tratar la tabla con cabecera vertical: la columna 0 son row-headers,
        las columnas 1..N son cabeceras de columna implícitas (sin row 0
        explícita). Serializar: {row_header} || Col_{i} : {valor}.

    compound:
        Detectar bloques rowspan-equivalentes y emitir cada bloque como una
        sub-tabla Cat. 2 separada.

    unknown:
        Fallback a _serialize_cat1 (pares clave-valor lineales).
    """
    ...
```

### 3.5 Tests obligatorios BL-DLA-3

```python
def test_dla3_priips_scenarios_2row_header():
    """Matriz escenarios PRIIPS: cabecera de 2 filas reconocida."""
    extracted = [
        ["",            "Si vende 1 año", "",          "Si mantiene 5 años", ""],
        ["",            "Coste",          "Rentab",    "Coste",              "Rentab"],
        ["Tensión",     "100",            "-50%",      "500",                "-10%"],
        ["Desfavorable","50",             "-10%",      "200",                "0%"],
        ["Moderado",    "20",             "5%",        "100",                "10%"],
        ["Favorable",   "10",             "20%",       "50",                 "30%"],
    ]
    structure = detect_table_cat3_structure(extracted)
    assert structure['n_header_rows'] == 2
    text = _serialize_cat3(extracted, structure)
    assert "Tensión || Si vende 1 año - Coste : 100" in text
    assert "Favorable || Si mantiene 5 años - Rentab : 30%" in text


def test_dla3_share_classes_transposed():
    """Matriz share classes: cabecera vertical (col 0 son atributos)."""
    extracted = [
        ["Atributo",   "Class A",     "Class B",     "Class C"],
        ["ISIN",       "LU100",       "LU101",       "LU102"],
        ["Divisa",     "EUR",         "USD",         "GBP"],
        ["TER",        "1.50%",       "1.20%",       "1.00%"],
    ]
    structure = detect_table_cat3_structure(extracted)
    text = _serialize_cat3(extracted, structure)
    assert "Class A || Atributo : Class A" not in text   # no ruido
    assert "ISIN || Class A : LU100" in text
    assert "TER || Class C : 1.00%" in text
```

### 3.6 Riesgos BL-DLA-3

| # | Riesgo | Severidad | Mitigación |
|---|---|---|---|
| R14 | Detección de cabecera multi-fila falla en tablas con cuerpo no canónico (e.g. una sola fila de cuerpo) → matriz tratada como Cat. 2 incorrectamente. | Media | `n_header_rows > 1` solo si hay ≥2 filas de cuerpo bajo la cabecera. |
| R15 | Tablas con cabecera lateral combinada (`A1=A2` en col 0 son rowspan implícito) confunden la heurística de transposed vs matrix. | Media | Dual-pass: si H1 y H2 ambas son verdaderas, marcar como `compound` y serializar como subtablas. |
| R16 | El consumo de Cat. 3 por detectores P2/P3 aún no está implementado. Riesgo de over-engineering. | Alta | NO implementar BL-DLA-3 hasta que un detector P2/P3 lo requiera explícitamente. |

### 3.7 Beneficios esperados

- Datos PRIIPS de escenarios accesibles para P3 (régimen tensión / favorable).
- Reducción residual de `Currency_Hedged=NULL` (BL-49) tras lookup en tabla de share classes.
- Habilita futuros detectores específicos para tablas comparativas.

---

## 4. INTEGRACIÓN CON BL-DLA-1 Y SECUENCIACIÓN

### 4.1 Dependencias

```
BL-DLA-1 (cerrado 1A/1B/1C, 1D en ejecución continua)
    ▼
BL-DLA-RESTANTES-1 (consecuencia 1D, en sección 3 v3.6)
    ▼
BL-DLA-C3-EXCL (consecuencia 1D, en sección 3 v3.6)
    ▼
BL-DLA-2-DIAG (Q-DLA-04, Q-DLA-05) — bloqueante de BL-DLA-2
    ▼
BL-DLA-2 (Sub-fases 2A, 2B, 2C, 2D)
    ▼
BL-DLA-3-DIAG (Q-DLA-06) — bloqueante de BL-DLA-3
    ▼
BL-DLA-3 (Sub-fases 3-PRE, 3A, 3B, 3C, 3D)
```

### 4.2 Disparadores condicionales

- BL-DLA-2 NO se inicia hasta que Sub-fase 1D haya cubierto al menos el 30% del corpus (≈ 1.000 fondos re-procesados con DLA Fase 1). Razón: validar empíricamente que las tablas residuales no están siendo ya parcialmente resueltas por la mejora en bloques de párrafo (efecto colateral inesperado de Fase 1 sobre fondos con tablas pequeñas en líneas de párrafo).

- BL-DLA-3 NO se inicia hasta que P2/P3 documente un detector que requiera datos de Cat. 3.

### 4.3 Decisiones de diseño nuevas (a registrar en sección 9 del backlog v3.7)

| Decisión | Alternativa considerada | Razón de elección |
|----------|------------------------|-------------------|
| BL-DLA-2: módulo `dla_table_serializer.py` separado de `dla_extractor.py` | Función adicional dentro de `dla_extractor.py` | Separación de responsabilidades (Principio #2): `dla_extractor` gestiona layout y párrafos, `dla_table_serializer` gestiona tablas. Permite roll-back de Fase 2 sin tocar Fase 1. |
| BL-DLA-2: usar `pdfplumber` para detección de tablas, mantener `fitz` para todo lo demás | Migrar todo a `fitz.find_tables()` (PyMuPDF ≥1.23) | `pdfplumber.find_tables()` es maduro y validado en KIIDs reales por la propuesta de Gemini. `fitz.find_tables()` es experimental. Apertura dual añade ~100 ms por fondo, aceptable. |
| BL-DLA-2: NO usar marcadores `--- [INICIO TABLA] ---` | Usar marcadores como propone Gemini | Los marcadores rompen capa L0-FUSED de `srri_text.py v3` y pueden producir matches espurios en regex existentes. La compatibilidad total con detectores se prioriza sobre la legibilidad humana del texto serializado. |
| BL-DLA-2: serialización Cat. 2 con formato `{row} \|\| {col} : {valor}` | Formato `{row} {col} {valor}` (separadores espaciales) | El formato Gemini (`||` y `:`) introduce delimitadores únicos que sobreviven al fused y son consumibles por regex específicos sin ambigüedad. Es estable y determinista. |
| BL-DLA-2: heurística de clasificación shape (n_rows, n_cols) sobre detección semántica | Modelo ML para clasificación de tabla | La heurística es interpretable, debugeable y rápida. Un modelo ML es overkill para este problema y agregaría dependencias nuevas. |
| BL-DLA-2: kill-switch separado `DLA_TABLE_SERIALIZATION_ENABLED` | Kill-switch único compartido con Fase 1 | Permite roll-back independiente de Fase 2 sin afectar Fase 1, y activación incremental Cat. 2 antes de Cat. 3. |
| BL-DLA-3: heurística de detección de cabecera multi-fila vs vertical | Asumir siempre primera fila = cabecera (Gemini) | La asunción Gemini falla en matrices PRIIPS y matrices transpuestas, que son la mayoría de Cat. 3 en el corpus KIID. Detección estructural es necesaria. |
| BL-DLA-3: diferir hasta consumo P2/P3 | Implementar inmediatamente tras BL-DLA-2 | Sin detector que consuma los datos Cat. 3, el módulo no aporta valor — riesgo de over-engineering (R16). |

---

## 5. RESUMEN DE NUEVOS ÍTEMS A INTRODUCIR EN BACKLOG v3.7

| Ítem | Tipo | Prioridad | Bloqueante de | Bloqueado por |
|------|------|-----------|---------------|---------------|
| **BL-DLA-2-DIAG** | Diagnóstico (Q-DLA-04, Q-DLA-05) | Media | BL-DLA-2 | BL-DLA-1 Sub-fase 1D ≥30% |
| **BL-DLA-2** | Implementación tablas Cat. 1+2 | Media | (mejora de BL-51A residual y BL-55 estructural) | BL-DLA-2-DIAG |
| **BL-DLA-3-DIAG** | Diagnóstico (Q-DLA-06) | Baja | BL-DLA-3 | BL-DLA-2 cerrado |
| **BL-DLA-3** | Implementación matrices Cat. 3 | Baja | (detectores P2/P3 dependientes) | BL-DLA-3-DIAG, detector P2/P3 documentado |

---

## 6. PRINCIPIO ADICIONAL DERIVADO (a registrar en sección 7 del backlog)

**Principio de evaluación crítica de contribuciones externas (introducido en v3.7).**

Cualquier propuesta técnica externa (otro LLM, tutorial, librería, snippet) debe pasar por una evaluación crítica documentada antes de su integración. La evaluación debe identificar:
1. **Aportaciones aprovechables** — qué ideas conceptuales son sólidas y aplicables al contexto.
2. **Problemas técnicos** — qué partes del código son incompatibles con el motor, dependencias o convenciones del proyecto.
3. **Regresiones potenciales** — qué partes podrían introducir defectos nuevos en el sistema vigente.
4. **Violaciones de restricciones** — qué partes contradicen `RESTRICCIONES_ARQUITECTURA.md`.
5. **Adaptación necesaria** — qué reescritura/portabilidad requiere para integrarse correctamente.

**Aplicación documentada:** análisis crítico de la contribución de Gemini al diseño BL-DLA-2/3 (este documento, sección 1).

**Razón:** la incorporación literal de código externo sin esta evaluación es la principal causa de regresiones documentales en proyectos con alta carga acumulada de fixes. La calidad del input no garantiza compatibilidad con el sistema vigente.

---

**Fin del documento — anexo a backlog v3.7. Preparado para integración en sección 3 (items abiertos), sección 9 (decisiones), sección 7 (principios) y sección 10 (roadmap) del backlog autocontenido.**
