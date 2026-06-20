# WORKFLOWS ESTRUCTURADOS — Templates por Tipo de Tarea

**Propósito:** Minimizar turnos de conversación y tokens consumidos mediante flujos predefinidos  
**Aplicación:** Identificar tipo de tarea y seguir workflow correspondiente estrictamente  
**Beneficio:** Reduce consumo de tokens en 50-70% vs. patrón reactivo

---

## CLASIFICACIÓN DE TAREAS

| Tipo | Descripción | Turnos esperados | Ejemplo |
|------|-------------|------------------|---------|
| **Debugging** | Identificar y corregir bug específico | 2-3 | SRRI_Validation_Status inconsistente |
| **Refactorización** | Modificar módulo existente para añadir funcionalidad | 2-3 | Implementar Regla 4 en fund_family_builder |
| **Nuevo Feature** | Crear módulo o funcionalidad nueva | 3-4 | Sistema de caching de PDF por hash |
| **Consulta rápida** | Pregunta sobre schema, docs, o arquitectura | 1 | ¿Nombre columna para TER? |
| **Diseño arquitectónico** | Proponer arquitectura para sistema complejo | 3-5 | Scoring regime-aware para P3 |

---

## WORKFLOW 1: DEBUGGING (2-3 turnos)

**Objetivo:** Diagnosticar causa raíz y aplicar fix quirúrgico en mínimo número de turnos

### TURNO 1 — Usuario: Reporte completo del bug

**Template:**

```
NIVEL-2: Debugging de [nombre descriptivo del bug]

SÍNTOMA OBSERVADO:
[Descripción precisa del comportamiento incorrecto]

DATOS PROBLEMÁTICOS:
[SQL query mostrando 5-10 casos problemáticos con columnas relevantes]

CONTEXTO CARGADO:
- Módulo(s) sospechoso(s): [lista de archivos]
- Schema: [si es relevante]

HIPÓTESIS INICIAL (opcional):
[Tu teoría sobre la causa, si la tienes]

OBJETIVO:
[Estado esperado tras el fix, con criterio de validación cuantificable]
```

**Ejemplo concreto:**

```
NIVEL-2: Debugging de SRRI_Validation_Status inconsistente

SÍNTOMA OBSERVADO:
220 fondos tienen SRRI_Validation_Status='VISUAL_ONLY' pero SRRI_Textual poblado.
Esto es inconsistente: si ambos Visual y Textual existen, debería ser MATCH o CONFLICT.

DATOS PROBLEMÁTICOS:
SELECT ISIN, Fund_Name, SRRI_Visual, SRRI_Textual, SRRI_Validation_Status
FROM fund_kiid_metadata km
JOIN fund_master fm ON km.ISIN = fm.ISIN
WHERE km.SRRI_Validation_Status = 'VISUAL_ONLY'
  AND km.SRRI_Textual IS NOT NULL
  AND km.KIID_Class = 1
LIMIT 10;

Resultados:
ISIN            | Fund_Name           | Visual | Textual | Status
LU0329592355    | Robeco QI Global... | 5      | 5       | VISUAL_ONLY
LU1244045850    | Robeco BP US Pre... | 4      | 4       | VISUAL_ONLY
[8 más...]

CONTEXTO CARGADO:
- kiid_parser.py (función parse_kiid_generic)
- srri_v4_geometric.py (extractor visual)
- sqlite_writer.py (lógica de persistencia)

HIPÓTESIS INICIAL:
Posiblemente el extractor visual está generando detecciones espurias, 
o la lógica de validación en kiid_parser no está evaluando correctamente
cuando ambos valores existen.

OBJETIVO:
0 fondos con VISUAL_ONLY + SRRI_Textual poblado (estado inconsistente).
```

### TURNO 1 — Claude: Plan de diagnóstico estructurado

**Qué hace Claude:**
1. Analiza síntoma y datos problemáticos
2. Identifica 2-3 hipótesis de causa raíz (ordenadas por probabilidad)
3. Propone queries SQL o validaciones adicionales para descartar hipótesis
4. **NO propone fix aún** (solo diagnóstico)

**Output esperado:**

```
DIAGNÓSTICO — SRRI_Validation_Status inconsistente

ANÁLISIS INICIAL:
Los 220 fondos problemáticos tienen Visual=Textual en todos los casos
(5=5, 4=4, etc.), lo que debería resultar en MATCH, no VISUAL_ONLY.

HIPÓTESIS (por probabilidad):

H1 [ALTA]: srri_v4_geometric.py genera detección visual espuria
   - Los 220 casos son 83% Robeco
   - Posible "blob" residual en layout PDF Robeco que se detecta como SRRI
   - Validación: Revisar Processing_Breakdown de estos fondos

H2 [MEDIA]: Lógica de validación en kiid_parser.py incorrecta
   - La función _validate_srri() no considera caso Visual=Textual correctamente
   - Validación: Revisar código de _validate_srri()

H3 [BAJA]: Problema de timing (SRRI_Textual se extrae después)
   - SRRI_Validation_Status se asigna antes de tener SRRI_Textual
   - Validación: Revisar orden de ejecución en parse_kiid_generic()

QUERIES DE VALIDACIÓN NECESARIAS:

-- Q1: Distribución por gestora de casos problemáticos
SELECT fm.Management_Company, COUNT(*) AS n
FROM fund_kiid_metadata km
JOIN fund_master fm ON km.ISIN = fm.ISIN
WHERE km.SRRI_Validation_Status = 'VISUAL_ONLY'
  AND km.SRRI_Textual IS NOT NULL
GROUP BY fm.Management_Company
ORDER BY n DESC;

-- Q2: Verificar si todos tienen Visual=Textual
SELECT 
    COUNT(*) AS total,
    SUM(CASE WHEN SRRI_Visual = SRRI_Textual THEN 1 ELSE 0 END) AS visual_eq_textual
FROM fund_kiid_metadata
WHERE SRRI_Validation_Status = 'VISUAL_ONLY'
  AND SRRI_Textual IS NOT NULL;

PRÓXIMO PASO:
Ejecuta las queries Q1 y Q2, y compárteme los resultados para confirmar hipótesis.
```

---

### TURNO 2 — Usuario: Resultados de validación

**Template:**

```
RESULTADOS DE VALIDACIÓN:

Q1 (Distribución por gestora):
[Pegar resultado de la query]

Q2 (Visual=Textual):
[Pegar resultado de la query]

[Cualquier observación adicional]
```

**Ejemplo concreto:**

```
RESULTADOS DE VALIDACIÓN:

Q1 (Distribución por gestora):
Management_Company    | n
Robeco                | 183  (83%)
Nordea                | 24   (11%)
DWS                   | 13   (6%)

Q2 (Visual=Textual):
total: 220
visual_eq_textual: 220  (100%)

OBSERVACIÓN: Todos los casos tienen Visual=Textual, confirmando que
deberían ser MATCH. El 83% son Robeco.
```

---

### TURNO 2 — Claude: Diagnóstico de causa raíz + Fix propuesto

**Qué hace Claude:**
1. Confirma hipótesis basada en validación
2. Identifica causa raíz precisa (línea de código, lógica incorrecta)
3. Propone fix quirúrgico
4. Incluye query SQL de validación post-fix

**Output esperado:**

```
CAUSA RAÍZ CONFIRMADA — Hipótesis H1

DIAGNÓSTICO:
srri_v4_geometric.py está generando detecciones espurias en fondos Robeco.
El layout PDF de Robeco tiene un "blob" residual en la esquina superior
que el extractor visual interpreta como un SRRI band cuando MAX_BAND_ITER=30
permite escanear zonas fuera del área típica.

CÓDIGO PROBLEMÁTICO (srri_v4_geometric.py, línea 87):
```python
MAX_BAND_ITER = 30  # ❌ Demasiado permisivo, escanea fuera de área SRRI
```

FIX PROPUESTO:
Reducir MAX_BAND_ITER a 15, limitando escaneo a área razonable.

```python
# srri_v4_geometric.py, línea 87
MAX_BAND_ITER = 15  # ✓ Limita escaneo a área esperada
```

IMPACTO:
- Los 220 fondos con detección espuria necesitan recalcular SRRI_Visual
- Marcarlos como FORCE_REFRESH para próximo ciclo

SQL POST-FIX (marcar para recálculo):
```sql
UPDATE fund_kiid_metadata 
SET KIID_Status = 'FORCE_REFRESH'
WHERE SRRI_Validation_Status = 'VISUAL_ONLY'
  AND SRRI_Textual IS NOT NULL
  AND KIID_Class = 1;
-- Afecta: 220 fondos
```

VALIDACIÓN POST-CICLO:
Tras próxima ejecución del pipeline, verificar:
```sql
SELECT COUNT(*) AS resueltos
FROM fund_kiid_metadata
WHERE SRRI_Validation_Status = 'MATCH'
  AND ISIN IN ([lista de 220 ISINs problemáticos]);
-- Esperado: 220 (100% resueltos)
```

ACCIÓN REQUERIDA:
1. Aplica el cambio en srri_v4_geometric.py (MAX_BAND_ITER=15)
2. Ejecuta el SQL de marcado FORCE_REFRESH
3. Re-ejecuta pipeline completo
4. Valida con query post-ciclo
```

---

### TURNO 3 — Usuario: Confirmación de fix aplicado

**Template:**

```
FIX APLICADO ✓

RESULTADOS:
[Query de validación post-ciclo]

[CERRADO] si validación confirma 100% resueltos
[NUEVA CONSULTA] si quedan casos sin resolver
```

**Ejemplo concreto:**

```
FIX APLICADO ✓

RESULTADOS POST-CICLO:
resueltos: 218 (99%)

PENDIENTES: 2 fondos (LU1234567890, LU0987654321) aún con VISUAL_ONLY.
Revisando manualmente, estos 2 tienen SRRI_Visual=NULL ahora (no detectado).

ACCIÓN: Marcados manualmente como TEXT_ONLY (aceptable).

[CERRADO]
```

---

## WORKFLOW 2: REFACTORIZACIÓN (2-3 turnos)

**Objetivo:** Modificar módulo existente para añadir funcionalidad o mejorar rendimiento

### TURNO 1 — Usuario: Especificación de refactorización

**Template:**

```
NIVEL-3: Refactorización de [módulo] — [objetivo]

OBJETIVO:
[Descripción funcional del cambio deseado]

RESTRICCIONES:
[Principios de diseño aplicables, compatibilidad, performance]

CONTEXTO CARGADO:
- Módulo principal: [archivo]
- Dependencias directas: [archivos relacionados]
- Casos de prueba: [ejemplos concretos]

CASOS DE USO:
[2-3 ejemplos concretos de input → output esperado]

VALIDACIÓN:
[Criterio cuantificable de éxito]
```

**Ejemplo concreto:**

```
NIVEL-3: Refactorización de fund_family_builder.py — Implementar Regla 4

OBJETIVO:
Añadir lógica de resolución de familias con naturaleza inconsistente
cuando hay exactamente 2 fondos en la familia con 2 naturalezas diferentes (50/50).

Criterio: Usar nombre de la familia + SRRI más alto para determinar
la naturaleza correcta.

RESTRICCIONES:
- Principio #2: Root cause > parches (no SQL ad-hoc)
- Principio #7: Corrección en módulo correcto
- Debe ser reproducible en cada ciclo del pipeline
- No romper Reglas 1-3 existentes

CONTEXTO CARGADO:
- fund_family_builder.py (módulo completo)
- classify_utils.py (infer_nature_from_name si existe)
- SQL de familias 50/50:

SELECT ff.family_id, ff.family_name, 
       GROUP_CONCAT(fm.Fund_Nature || ' (SRRI:' || fm.SRRI || ')') AS natures
FROM fund_families ff
JOIN fund_master fm ON fm.fund_family_id = ff.family_id
GROUP BY ff.family_id
HAVING COUNT(DISTINCT fm.Fund_Nature) = 2
   AND COUNT(*) = 2;

Resultados (7 familias):
family_id   | family_name                  | natures
FAM_001697  | JPMorgan US Value Equity     | RV (SRRI:5), Mixtos (SRRI:4)
FAM_001897  | Thematics Safety             | RV (SRRI:6), Mixtos (SRRI:5)
FAM_002121  | PIMCO Inflation Multi-Asset  | RF Flexible (SRRI:4), Mixtos (SRRI:4)
[4 más...]

CASOS DE USO:

Caso 1: JPMorgan US Value Equity (FAM_001697)
  Input: 2 fondos, RV (SRRI:5) vs Mixtos (SRRI:4)
  Nombre sugiere: "US Value Equity" → Renta Variable
  Output esperado: Corregir a Fund_Nature='Renta Variable'

Caso 2: PIMCO Inflation Multi-Asset (FAM_002121)
  Input: 2 fondos, RF Flexible (SRRI:4) vs Mixtos (SRRI:4)
  Nombre sugiere: "Multi-Asset" → Mixtos
  Output esperado: Corregir a Fund_Nature='Mixtos'

Caso 3: Si nombre no conclusivo, SRRI más alto gana
  Input: 2 fondos, Nature A (SRRI:6) vs Nature B (SRRI:4)
  Nombre: Ambiguo
  Output esperado: Corregir a Nature A

VALIDACIÓN:
Tras implementar, re-ejecutar fund_family_builder:
- Familias con inconsistencia 50/50: 7 → 0-1 (resuelve 6-7 de 7)
- Las 3 no-determinables documentadas (sin fix automático aceptable)
```

---

### TURNO 1 — Claude: Propuesta de diseño

**Qué hace Claude:**
1. Analiza módulo existente (estructura, funciones, reglas previas)
2. Propone arquitectura del cambio (nuevas funciones, modificaciones)
3. Identifica edge cases y cómo manejarlos
4. **NO genera código aún** (solo diseño)

**Output esperado:**

```
PROPUESTA DE DISEÑO — Regla 4 en fund_family_builder

ANÁLISIS DEL MÓDULO EXISTENTE:
fund_family_builder.py tiene:
- build_fund_families(): Crea familias iniciales
- correct_family_inconsistencies(): Aplica Reglas 1-3
  - Regla 1: Mayoría simple (>50% fondos con misma Nature)
  - Regla 2: SRRI más alto (cuando Regla 1 no aplica)
  - Regla 3: Benchmarks más poblados

PROPUESTA — Añadir Regla 4:

NUEVA FUNCIÓN:
```python
def _apply_rule_4_name_based(conn, family_id, family_name, funds):
    """
    Regla 4: Familias 50/50 (2 fondos, 2 naturalezas)
    Criterio: Inferir naturaleza del nombre de familia
    Fallback: SRRI más alto
    """
    # 1. Intentar inferir Nature desde nombre
    inferred_nature = _infer_nature_from_family_name(family_name)
    
    if inferred_nature:
        return inferred_nature
    
    # 2. Fallback: SRRI más alto
    fund_high_srri = max(funds, key=lambda f: f['SRRI'] or 0)
    return fund_high_srri['Fund_Nature']
```

NUEVA FUNCIÓN AUXILIAR:
```python
def _infer_nature_from_family_name(family_name):
    """
    Detecta Nature desde nombre de familia usando patrones.
    Retorna None si no concluyente.
    """
    patterns_rv = [
        r'\b(equity|stocks?|bolsa|acciones)\b',
        r'\b(global|european|us|emerging)\s+(equity|stocks)\b'
    ]
    patterns_mixtos = [
        r'\b(multi[\s-]?asset|balanced|allocation)\b',
        r'\b(mixto|equilibrado)\b'
    ]
    # ... [más patrones]
    
    if any(re.search(p, family_name, re.I) for p in patterns_rv):
        return 'Renta Variable'
    elif any(re.search(p, family_name, re.I) for p in patterns_mixtos):
        return 'Mixtos'
    # ... [resto de natures]
    
    return None  # No concluyente
```

MODIFICACIÓN EN correct_family_inconsistencies():
```python
# Tras Reglas 1-3, añadir:
if len(funds) == 2 and len(set(f['Fund_Nature'] for f in funds)) == 2:
    # Caso 50/50 → Aplicar Regla 4
    correct_nature = _apply_rule_4_name_based(conn, family_id, family_name, funds)
    # ... aplicar corrección
```

EDGE CASES:

1. Nombre ambiguo + SRRI empate (ej: ambos SRRI=4)
   → Fallback: No corregir, documentar como no-determinable
   
2. Nombre sugiere Nature C, pero fondos son A vs B
   → Priorizar nombre (Nature C) si confianza alta
   → Si confianza baja, usar SRRI más alto

3. Patrones multiidioma (inglés, español, alemán)
   → Usar patterns case-insensitive y multi-idioma

VALIDACIÓN:
Test con las 7 familias conocidas:
- FAM_001697 (JPMorgan US Value) → 'Renta Variable' ✓
- FAM_001897 (Thematics Safety) → 'Renta Variable' ✓
- FAM_002121 (PIMCO Multi-Asset) → 'Mixtos' ✓
- [resto según patrones]

Esperado: 6/7 resueltas automáticamente, 1 no-determinable.

¿Apruebas diseño o requiere ajustes?
```

---

### TURNO 2 — Usuario: Aprobación de diseño

**Template:**

```
DISEÑO APROBADO ✓

[O: ajustes solicitados si es necesario]

PROCEDE CON IMPLEMENTACIÓN.
```

---

### TURNO 2 — Claude: Código completo

**Qué hace Claude:**
1. Genera código Python completo y validado sintácticamente
2. Incluye comentarios de trazabilidad
3. Propone test de validación

**Output esperado:**

```python
# fund_family_builder.py — Añadida Regla 4 (v16)
# [Código completo del módulo modificado]
# ... [funciones existentes sin cambios]

def _infer_nature_from_family_name(family_name):
    """
    Regla 4: Inferir Fund_Nature desde nombre de familia.
    
    Usado en familias 50/50 (2 fondos, 2 naturalezas diferentes).
    
    Returns:
        str: 'Renta Variable', 'Mixtos', 'Renta Fija Flexible', etc.
        None: Si nombre no concluyente
    """
    patterns_rv = [
        r'\b(equity|equities|stocks?|shares?|bolsa|acciones)\b',
        r'\b(value|growth|dividend)\s+(equity|fund)\b',
        r'\b(global|european|us|asian|emerging)\s+(equity|stock)\b'
    ]
    patterns_mixtos = [
        r'\b(multi[\s-]?asset|balanced|allocation)\b',
        r'\b(mixto|equilibrado)\b',
        r'\b(moderate|conservative)\s+allocation\b'
    ]
    # ... [resto de patrones]
    
    fname_lower = family_name.lower()
    
    if any(re.search(p, fname_lower, re.I) for p in patterns_rv):
        return 'Renta Variable'
    elif any(re.search(p, fname_lower, re.I) for p in patterns_mixtos):
        return 'Mixtos'
    # ... [resto]
    
    return None


def _apply_rule_4_name_based(conn, family_id, family_name, funds):
    """
    Regla 4: Resolver familias 50/50 por nombre + SRRI.
    
    Args:
        conn: Conexión SQLite
        family_id: ID de la familia
        family_name: Nombre de la familia
        funds: Lista de 2 dicts con Fund_Nature, SRRI
    
    Returns:
        str: Naturaleza correcta determinada
    """
    # Paso 1: Inferir desde nombre
    inferred_nature = _infer_nature_from_family_name(family_name)
    
    if inferred_nature:
        # Validar que inferred_nature esté presente en los 2 fondos
        natures_in_family = {f['Fund_Nature'] for f in funds}
        if inferred_nature in natures_in_family:
            return inferred_nature
    
    # Paso 2: Fallback SRRI más alto
    fund_high_srri = max(funds, key=lambda f: f['SRRI'] or 0)
    return fund_high_srri['Fund_Nature']


def correct_family_inconsistencies(conn):
    """
    Corrige familias con Fund_Nature inconsistente.
    
    Reglas aplicadas (en orden):
      1. Mayoría simple (>50%)
      2. SRRI más alto
      3. Benchmarks más poblados
      4. Nombre de familia + SRRI (50/50 solo) [NUEVO v16]
    
    Returns:
        int: Número de familias corregidas
    """
    # ... [Reglas 1-3 existentes sin cambios]
    
    # REGLA 4: Familias 50/50 (2 fondos, 2 natures)
    fifty_fifty_families = conn.execute("""
        SELECT ff.family_id, ff.family_name, fm.ISIN, fm.Fund_Nature, fm.SRRI
        FROM fund_families ff
        JOIN fund_master fm ON fm.fund_family_id = ff.family_id
        WHERE ff.family_id IN (
            SELECT fund_family_id
            FROM fund_master
            WHERE fund_family_id IS NOT NULL
            GROUP BY fund_family_id
            HAVING COUNT(DISTINCT Fund_Nature) = 2
               AND COUNT(*) = 2
        )
    """).fetchall()
    
    families_50_50 = {}
    for row in fifty_fifty_families:
        fid = row['family_id']
        if fid not in families_50_50:
            families_50_50[fid] = {
                'family_name': row['family_name'],
                'funds': []
            }
        families_50_50[fid]['funds'].append({
            'ISIN': row['ISIN'],
            'Fund_Nature': row['Fund_Nature'],
            'SRRI': row['SRRI']
        })
    
    corrected_rule_4 = 0
    for fid, data in families_50_50.items():
        correct_nature = _apply_rule_4_name_based(
            conn, fid, data['family_name'], data['funds']
        )
        
        # Aplicar corrección
        conn.execute("""
            UPDATE fund_master
            SET Fund_Nature = ?
            WHERE fund_family_id = ?
              AND Fund_Nature != ?
        """, (correct_nature, fid, correct_nature))
        
        conn.execute("""
            UPDATE fund_families
            SET Fund_Nature = ?
            WHERE family_id = ?
        """, (correct_nature, fid))
        
        corrected_rule_4 += 1
    
    conn.commit()
    
    return {
        'rule_1': corrected_rule_1,
        'rule_2': corrected_rule_2,
        'rule_3': corrected_rule_3,
        'rule_4': corrected_rule_4  # NUEVO
    }
```

VALIDACIÓN POST-IMPLEMENTACIÓN:
```sql
-- Verificar familias 50/50 resueltas
SELECT ff.family_id, ff.family_name, ff.Fund_Nature,
       COUNT(DISTINCT fm.Fund_Nature) AS distinct_natures
FROM fund_families ff
JOIN fund_master fm ON fm.fund_family_id = ff.family_id
GROUP BY ff.family_id
HAVING COUNT(*) = 2 AND COUNT(DISTINCT fm.Fund_Nature) > 1;
-- Esperado: 0-1 filas (vs. 7 antes)
```

ARCHIVO COMPLETO DISPONIBLE:
[Aquí Claude proporciona el archivo completo fund_family_builder.py actualizado]
```

---

### TURNO 3 — Usuario: Confirmación de despliegue

```
DESPLEGADO ✓

VALIDACIÓN:
[Resultado de query post-despliegue]

Familias 50/50 sin resolver: 1 (FAM_002999)
→ Documentado como no-determinable (ambiguo incluso con Regla 4)

[CERRADO]
```

---

## WORKFLOW 3: NUEVO FEATURE (3-4 turnos)

**Objetivo:** Crear módulo o funcionalidad nueva desde cero

### TURNO 1 — Usuario: Especificación funcional

**Template:**

```
NIVEL-4: Nuevo feature — [nombre]

ESPECIFICACIÓN FUNCIONAL:
[Descripción detallada de qué debe hacer el sistema]

MÓDULOS EXISTENTES RELEVANTES:
[Código o módulos que serán integrados o referenciados]

CASOS DE USO:
[3-5 ejemplos concretos con input/output]

RESTRICCIONES TÉCNICAS:
[Performance, compatibilidad, principios de diseño]

INTEGRACIÓN CON PIPELINE:
[Dónde y cómo se invoca en el flujo existente]
```

**Ejemplo concreto:**

```
NIVEL-4: Nuevo feature — Sistema de caching de PDF por hash

ESPECIFICACIÓN FUNCIONAL:
Implementar sistema de caching para evitar re-descargas innecesarias de PDFs KIID.

Lógica:
1. Antes de descargar PDF, verificar si hash SHA256 de URL ya existe en BD
2. Si existe Y el PDF está en cache local → Usar cache
3. Si existe Y el PDF NO está en cache local → Re-descargar (archivo perdido)
4. Si NO existe → Descargar, calcular hash, persistir

MÓDULOS EXISTENTES RELEVANTES:
- io.py (get_kiid_for_isin — función de descarga)
- sqlite_writer.py (upsert_kiid_metadata — persistencia)
- fund_kiid_metadata.KIID_PDF_Hash (columna ya existe, sin uso actual)

CASOS DE USO:

Caso 1: PDF ya descargado, cache presente
  Input: ISIN=LU0123456789, URL=https://..., KIID_PDF_Hash='abc123...'
  Estado: Cache existe en c:\desarrollo\fondos\cache\abc123.pdf
  Output: Leer de cache, 0 descargas HTTP, <100ms

Caso 2: PDF ya descargado, cache ausente (archivo perdido)
  Input: ISIN=LU0123456789, URL=https://..., KIID_PDF_Hash='abc123...'
  Estado: Hash en BD pero archivo no existe
  Output: Re-descargar, recalcular hash, verificar match, persistir

Caso 3: PDF nuevo (primera descarga)
  Input: ISIN=LU9876543210, URL=https://..., KIID_PDF_Hash=NULL
  Estado: No en BD, no en cache
  Output: Descargar, calcular hash SHA256, persistir en cache/abc456.pdf,
          actualizar KIID_PDF_Hash en BD

Caso 4: PDF actualizado (mismo ISIN, nuevo hash)
  Input: ISIN=LU0123456789, URL=https://... (actualizado), 
         KIID_PDF_Hash_BD='abc123...', KIID_PDF_Hash_Real='xyz789...'
  Estado: Hash en BD diferente del hash del PDF descargado
  Output: Actualizar hash en BD, marcar FORCE_REFRESH, re-procesar texto

RESTRICCIONES TÉCNICAS:
- Performance: <500ms overhead por fondo (cálculo hash + verificación cache)
- Cache location: c:\desarrollo\fondos\cache\
- Naming: {hash_sha256}.pdf
- Cleanup: Purge cache si >10GB (LRU)
- Principio #1: COALESCE KIID_PDF_Hash (preservar si NULL en ciclo CACHED)

INTEGRACIÓN CON PIPELINE:
Modificar io.py → get_kiid_for_isin():
  1. Verificar KIID_Status (CACHED/OK/FORCE_REFRESH)
  2. [NUEVO] Si FORCE_REFRESH → Verificar cache por hash antes de HTTP
  3. Si cache hit → Leer de disco, validar integridad
  4. Si cache miss → Descargar HTTP normal
  5. [NUEVO] Tras descarga → Calcular hash, persistir en cache
  6. Actualizar KIID_PDF_Hash en fund_kiid_metadata
```

---

### TURNO 1 — Claude: Propuesta de arquitectura

**Qué hace Claude:**
1. Diseña estructura de módulos (nuevos archivos, funciones)
2. Define interfaces (parámetros, retornos)
3. Identifica impacto en módulos existentes
4. Propone plan de migración/despliegue

**Output esperado:**

```
PROPUESTA DE ARQUITECTURA — Sistema de caching PDF por hash

ESTRUCTURA DE MÓDULOS:

NUEVO ARCHIVO: core/pdf_cache.py
```python
# core/pdf_cache.py
import hashlib
from pathlib import Path

CACHE_DIR = Path("c:/desarrollo/fondos/cache")
MAX_CACHE_SIZE_GB = 10

def get_cached_pdf(pdf_hash: str) -> bytes | None:
    """Retorna contenido del PDF si existe en cache, None si no."""
    ...

def store_pdf_in_cache(pdf_content: bytes) -> str:
    """Calcula hash SHA256, persiste PDF, retorna hash."""
    ...

def validate_cache_integrity(pdf_hash: str, pdf_content: bytes) -> bool:
    """Verifica que hash del contenido coincide con hash esperado."""
    ...

def cleanup_cache_if_needed():
    """Purge LRU si cache excede MAX_CACHE_SIZE_GB."""
    ...
```

MODIFICACIONES EN io.py:
```python
# io.py — get_kiid_for_isin()

def get_kiid_for_isin(conn, isin, url, force_refresh=False):
    # ... [lógica existente]
    
    # [NUEVO] Verificar cache antes de HTTP
    if kiid_status in ('FORCE_REFRESH', 'NOT_FOUND'):
        cached_pdf = pdf_cache.get_cached_pdf(existing_hash)
        if cached_pdf:
            # Cache hit → Usar cache
            pdf_content = cached_pdf
            download_happened = False
        else:
            # Cache miss → Descargar HTTP
            pdf_content = _download_pdf(url)
            download_happened = True
    
    # [NUEVO] Persistir en cache si fue descarga nueva
    if download_happened:
        new_hash = pdf_cache.store_pdf_in_cache(pdf_content)
        # Actualizar hash en metadata
        update_pdf_hash(conn, isin, new_hash)
    
    # ... [resto de lógica]
```

MODIFICACIONES EN sqlite_writer.py:
```python
# Añadir COALESCE para KIID_PDF_Hash
INSERT INTO fund_kiid_metadata (...)
VALUES (...)
ON CONFLICT(ISIN, KIID_Class) DO UPDATE SET
    KIID_PDF_Hash = COALESCE(excluded.KIID_PDF_Hash, KIID_PDF_Hash),
    ...
```

PLAN DE MIGRACIÓN:

Fase 1: Crear pdf_cache.py (nuevo módulo)
  - Implementar get_cached_pdf()
  - Implementar store_pdf_in_cache()
  - Implementar cleanup_cache_if_needed()
  - Tests unitarios

Fase 2: Modificar io.py (integración)
  - Añadir lógica de verificación de cache
  - Añadir lógica de persistencia post-descarga
  - Backward compatible (si cache falla, fallback a HTTP normal)

Fase 3: Modificar sqlite_writer.py (COALESCE)
  - Añadir COALESCE para KIID_PDF_Hash

Fase 4: Migración de datos (one-time script)
  - Para fondos existentes con KIID_PDF_Hash=NULL:
    - Calcular hash del texto Raw_KIID_Text (aproximación)
    - O marcar como FORCE_REFRESH para re-descargar y calcular hash real

IMPACTO EN RENDIMIENTO:

Sin cache (actual):
  - 272 fondos OK/FORCE_REFRESH → 272 descargas HTTP
  - Tiempo: ~3-5 min (depende de red)

Con cache (propuesto):
  - Primera ejecución: 272 descargas + 272 hash calculations + 272 writes
  - Tiempo: ~4-6 min (overhead inicial ~20%)
  - Ejecución subsecuente (FORCE_REFRESH de 50 fondos):
    - Cache hits: ~40 fondos (80%) → 0 HTTP, <100ms cada uno
    - Cache misses: ~10 fondos (20%) → HTTP normal
  - Tiempo: ~30s (vs. ~5 min sin cache)
  
**Ahorro promedio: ~85% en tiempo de descarga para ciclos subsecuentes**

¿Apruebas arquitectura o requiere ajustes?
```

---

### TURNO 2 — Usuario: Aprobación de arquitectura

```
ARQUITECTURA APROBADA ✓

PROCEDE CON IMPLEMENTACIÓN DE FASE 1 (pdf_cache.py).
```

---

### TURNO 2 — Claude: Implementación Fase 1

**Output:** Código completo de pdf_cache.py con tests

---

### TURNO 3 — Usuario: Validación Fase 1

```
FASE 1 VALIDADA ✓

TESTS PASADOS:
- test_get_cached_pdf_hit: PASS
- test_get_cached_pdf_miss: PASS
- test_store_pdf_in_cache: PASS
- test_validate_cache_integrity: PASS
- test_cleanup_cache: PASS

PROCEDE CON FASE 2 (integración en io.py).
```

---

### TURNO 3 — Claude: Implementación Fase 2

**Output:** Modificaciones en io.py con integración de cache

---

### TURNO 4 — Usuario: Validación completa

```
FASES 2-4 DESPLEGADAS ✓

VALIDACIÓN EN PRODUCCIÓN:
- Ciclo con 50 FORCE_REFRESH:
  - Cache hits: 42 (84%)
  - Cache misses: 8 (16%)
  - Tiempo total: 28 segundos (vs. ~4 min sin cache)

AHORRO CONFIRMADO: 88%

[CERRADO]
```

---

## WORKFLOW 4: CONSULTA RÁPIDA (1 turno)

**Objetivo:** Responder pregunta sobre schema, docs, o arquitectura sin código

### TURNO 1 — Usuario: Pregunta directa

**Template:**

```
NIVEL-1: [Pregunta concisa]
```

**Ejemplos:**

```
NIVEL-1: ¿Nombre de la columna para TER en fund_master?
NIVEL-1: ¿Qué bloques de clasificación hay en P1?
NIVEL-1: ¿Cuál es la diferencia entre KIID_Status=OK y CACHED?
```

---

### TURNO 1 — Claude: Respuesta directa

**Qué hace Claude:**
1. Busca en SCHEMA_REFERENCE.md o CONTEXTO_OPERATIVO_V2.md
2. Responde de forma concisa (1-2 oraciones)
3. Opcionalmente incluye ejemplo SQL si es relevante

**Ejemplos:**

```
PREGUNTA: ¿Nombre de la columna para TER en fund_master?
RESPUESTA: Ongoing_Charge (tipo REAL, en % anual).

PREGUNTA: ¿Qué bloques de clasificación hay en P1?
RESPUESTA: 7 bloques secuenciales y excluyentes: Monetarios, RF Corto Plazo, 
RF Flexible, Renta Variable, Mixtos, Alternativos, Restantes.

PREGUNTA: ¿Diferencia entre KIID_Status=OK y CACHED?
RESPUESTA: OK = descarga HTTP exitosa en ciclo actual. CACHED = texto ya en BD, 
sin re-descarga. Ambos se tratan igual en el pipeline (usan Raw_KIID_Text de BD).
```

---

## RESUMEN DE WORKFLOWS

| Workflow | Turnos | Cuándo usar | Ahorro de tokens |
|----------|--------|-------------|------------------|
| Debugging | 2-3 | Bug específico, inconsistencia de datos | ~60% vs. reactivo |
| Refactorización | 2-3 | Modificar módulo existente, añadir funcionalidad | ~50% vs. reactivo |
| Nuevo Feature | 3-4 | Crear módulo nuevo, sistema complejo | ~40% vs. reactivo |
| Consulta rápida | 1 | Pregunta sobre schema, docs, arquitectura | ~90% vs. conversacional |

---

## INSTRUCCIONES PARA JOSÉ

**Antes de cada interacción con Claude:**

1. **Identificar tipo de tarea** (Debugging / Refactorización / Feature / Consulta)
2. **Determinar NIVEL de complejidad** (0-4)
3. **Preparar contexto mínimo** según nivel
4. **Usar template del workflow** correspondiente
5. **No derivar a patrón reactivo** (seguir workflow estrictamente)

**Checklist pre-consulta:**

```
[ ] ¿Tipo de tarea identificado? (D / R / F / C)
[ ] ¿NIVEL determinado? (0 / 1 / 2 / 3 / 4)
[ ] ¿Contexto mínimo preparado?
[ ] ¿Template de workflow aplicado?
[ ] ¿Información necesaria en TURNO 1? (evitar turnos extra)
```

**Si todas las respuestas son SÍ → Proceder**

---

**FIN WORKFLOWS ESTRUCTURADOS**

*Última actualización: 5 abril 2026*  
*Tokens estimados: ~4.500*
