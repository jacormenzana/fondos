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

UPDATE fund_families
SET Fund_Nature = 'Renta Variable'
WHERE family_id = 'FAM_001697';

-- Próximo ciclo del pipeline → vuelve a 'Mixtos' porque la lógica
-- de clasificación no cambió
```

**Ejemplo correcto:**

```python
# ✓ Fix en fund_family_builder.py (Regla 4)
def correct_family_inconsistencies(conn):
    """
    Regla 4: Familias 50/50 (2 fondos, 2 naturalezas diferentes)
    Criterio: Nombre de familia + SRRI más alto gana
    """
    inconsistent = get_50_50_families(conn)
    
    for family in inconsistent:
        funds = get_family_funds(conn, family.family_id)
        
        # Determinar naturaleza correcta por nombre de familia
        correct_nature = infer_nature_from_family_name(family.family_name)
        
        if correct_nature is None:
            # Fallback: SRRI más alto gana
            fund_high_srri = max(funds, key=lambda f: f.srri or 0)
            correct_nature = fund_high_srri.Fund_Nature
        
        # Aplicar corrección
        update_family_nature(conn, family.family_id, correct_nature)
    
    return len(inconsistent)
```

**Consecuencia:**  
La Regla 4 en `fund_family_builder.py` resuelve 6 de 9 familias inconsistentes de forma **estructural y reproducible**. En cada ciclo del pipeline, la validación se ejecuta y corrige automáticamente.

**Excepción:**  
SQL directo es aceptable **solo** para:
- Marcar fondos individuales para re-descarga (`UPDATE fund_kiid_metadata SET KIID_Status='FORCE_REFRESH' WHERE ISIN='...'`)
- Consultas de análisis/debugging (SELECT, no UPDATE/DELETE)
- Migraciones de schema una sola vez (con script idempotente en `scripts/mig/`)

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

---

## CONSECUENCIAS DE VIOLACIÓN

**Violación de principio → Fix rechazado**

Si una propuesta de solución viola alguno de estos principios:
1. Se rechaza la propuesta
2. Se solicita rediseño acorde a principios
3. Se documenta el principio violado y su razón

**Ejemplo histórico:**  
Propuesta inicial para bug P01 (SRRI inflado) era un UPDATE SQL ad-hoc (violación #2 y #7). Rechazada. Fix final: modificación estructural en srri_v4_geometric.py con re-ejecución de pipeline.

---

**FIN PRINCIPIOS DE DISEÑO**

*Última actualización: 5 abril 2026*  
*Tokens estimados: ~2.600*
