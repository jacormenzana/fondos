# Incidencia Crítica: Lógica Errada en `dla2_decision_diag.py`

**Fecha:** 10 de mayo de 2026  
**Severidad:** CRÍTICA — Afecta decisión Go/No-Go de BL-DLA-2  
**Identificación:** IE00BJGT6Q17 (ISIN ejemplo)  
**Módulo afectado:** `dla2_decision_diag.py` (líneas 258-260)  

---

## 1. PROBLEMA IDENTIFICADO

### 1.1 Síntoma observable

En el fondo **IE00BJGT6Q17**:
- **KIID contiene:** Tabla Cat. 2 (Costes) + Tabla Cat. 3 (Escenarios PRIIPS)
- **Lógica actual:** Asigna `cat_max = 3` (máximo detectado globalmente)
- **Consecuencia:** Busca `Exit_Fee_Pct` SOLO en tabla Cat. 3 (escenarios de rendimiento)
- **Realidad:** `Exit_Fee_Pct` existe **SOLO en tabla Cat. 2** (costes)
- **Resultado:** Marca `Exit_Fee_Pct = NULL` incorrectamente

### 1.2 Raíz del problema

**Líneas 258-260 de `dla2_decision_diag.py`:**
```python
if   r["has_cat3_escenarios"]:                              r["cat_max"] = 3
elif r["has_cat2_costes"] or r["has_cat2_politica"]:       r["cat_max"] = 2
elif r["has_cat1_signal"]:                                  r["cat_max"] = 1
```

Esta lógica calcula un **máximo global** (`cat_max`) como variable de decisión única para **todos los atributos**. 

**Suposición implícita (FALSA):** "Si un fondo tiene Cat. 3, todos sus atributos estarán en Cat. 3"

**Realidad:** Los atributos están distribuidos por **categoría específica**:
- `Entry_Fee_Pct`, `Exit_Fee_Pct`, `Ongoing_Charge` → **Cat. 2** (tabla "Costes")
- `Accumulation_Policy`, `Distribution_Frequency` → **Cat. 2** (tabla "Política de distribución")
- Escenarios PRIIPS (rendimiento, volatilidad, etc.) → **Cat. 3** (matriz escenarios)

### 1.3 Impacto cuantitativo

En el corpus actual (3.201 fondos):
- **1.023 fondos** tienen `has_cat3_escenarios = 1` (32%)
- De estos, **múltiples también tienen `has_cat2_costes = 1`** (fondos con Cat. 2 Y Cat. 3)
- **TODOS estos fondos** son procesados incorrectamente si buscamos `Exit_Fee_Pct` en Cat. 3

**Estimación conservadora:** 
- Si el 70% de los fondos con Cat. 3 también tienen Cat. 2: ~716 fondos afectados
- Si cada uno tiene `Exit_Fee_Pct` correctamente en Cat. 2: ~716 NULLs falsos en la BD actual

---

## 2. RAÍZ FUNDAMENTAL (Principio #1)

**Causa raíz:** El diseño de `dla2_decision_diag.py` **asume una jerarquía de categorías que no existe**.

La taxonomía REAL de un KIID es:
```
KIID = {
    "Cat. 1": {"Share classes list", "Fund characteristics"},       ← Presente en ~100% KIIDs
    "Cat. 2": {"Costs table", "Distribution policy"},              ← Presente en ~95% KIIDs
    "Cat. 3": {"PRIIPS scenarios (performance matrix)"},           ← Presente en ~32% KIIDs (SUBSET)
}
```

**NO es jerárquica:** Un fondo puede tener {Cat. 1 + Cat. 2}, {Cat. 1 + Cat. 2 + Cat. 3}, o incluso {Cat. 1 + Cat. 3} (sin Cat. 2 en casos raros).

**Error en dla2_decision_diag.py:**
```python
# ❌ FALSO: Supone que si Cat. 3 existe, todos los datos están en Cat. 3
cat_max = MAX(has_cat1, has_cat2, has_cat3)  
# Luego usa cat_max para TODOS los atributos
```

**Corrección necesaria:**
```python
# ✅ CORRECTO: Mapear cada atributo a su categoría específica
attribute_category_mapping = {
    'Entry_Fee_Pct': 2,           # Cat. 2 (tabla Costes)
    'Exit_Fee_Pct': 2,            # Cat. 2 (tabla Costes)
    'Ongoing_Charge': 2,          # Cat. 2 (tabla Costes)
    'Accumulation_Policy': 2,     # Cat. 2 (tabla Política)
    'Distribution_Frequency': 2,  # Cat. 2 (tabla Política)
    'PRIIPS_Scenarios': 3,        # Cat. 3 (matriz escenarios)
    # ... etc
}
```

---

## 3. IDENTIFICACIÓN DE FONDOS AFECTADOS

**Patrón de riesgo:** Fondos donde `has_cat2_costes = 1` AND `has_cat3_escenarios = 1`

**Query de diagnóstico:**
```sql
SELECT COUNT(*) as fondos_afectados
FROM dla_inv
WHERE has_cat2_costes = 1 AND has_cat3_escenarios = 1;
```

**Impacto esperado:**
- Estos fondos tienen `Exit_Fee_Pct` extraíble de Cat. 2
- Pero `dla2_decision_diag.py` busca en Cat. 3 (por `cat_max=3`)
- Resultado: NULL falso → Inflación artificial de KPI "NULL_pct"

---

## 4. ACCIONES CORRECTIVAS OBLIGATORIAS

### 4.1 Fase Inmediata (urgente)

**BL-DLA-2-LOGIC-FIX** (NUEVO, bloqueante de BL-DLA-2)
- **Descripción:** Refactorizar `dla2_decision_diag.py` para mapear cada atributo a su categoría específica
- **Módulo:** `dla2_decision_diag.py` v1.1
- **Cambios:**
  1. Eliminar variable `cat_max` como decisión única
  2. Crear mapeo explícito `ATTRIBUTE_CATEGORY_MAPPING`
  3. Refactorizar Fase 6 para usar `attribute_category` en lugar de `cat_max`
  4. Actualizar queries SQL para consultar `has_cat{N}` según atributo
  
- **Beneficio:** Diagnóstico correcto de ROI real en BL-DLA-2

### 4.2 Fase Posterior (correcciones en cascada)

**BL-DLA-2-TABLE-SERIALIZER** (dependencia de BL-DLA-2-LOGIC-FIX)
- Una vez que sabemos la categoría CORRECTA de cada atributo, podemos diseñar el serializador (`dla_table_serializer.py`) para extraer datos de la tabla Cat. 2 (costes) y Cat. 3 (escenarios) por separado.

---

## 5. EJEMPLOS ESPECÍFICOS DE AFECTACIÓN

### 5.1 IE00BJGT6Q17 (caso documental)

```
CSV actual:
  ISIN=IE00BJGT6Q17
  has_cat2_costes=1  ← Cat. 2 PRESENTE
  has_cat2_politica=1 ← Cat. 2 PRESENTE
  has_cat3_escenarios=1 ← Cat. 3 PRESENTE
  cat_max=3  ← MÁXIMO GLOBAL
  exit_fee_null=1  ← MARCADO COMO NULL (INCORRECTO)

Realidad KIID:
  Tabla Cat. 2 (Costes):
    Comisión de entrada: 0.00%
    Comisión de salida: 1.50%  ← EXISTE, pero no detectada
    Comisión de gestión: 0.45%
  
  Tabla Cat. 3 (Escenarios):
    Rendimiento 1 año: +3.2%
    Rendimiento 3 años: +2.1%
    ...
    [NO contiene comisión de salida]

Búsqueda errónea:
  dla2_decision_diag examina Cat. 3 por cat_max=3 → NO encuentra Exit_Fee_Pct
  Marca exit_fee_null=1 (FALSO POSITIVO)
```

---

## 6. ARQUITECTURA PROPUESTA PARA CORRECCIÓN

### 6.1 Mapeo de atributos a categorías

```python
ATTRIBUTE_CATEGORY_MAPPING = {
    # Atributos de COSTE (Cat. 2)
    'Entry_Fee_Pct': {
        'category': 2,
        'table_name': 'Costes / Comisiones',
        'pattern': r'(?:comisi[óo]n|tarifa)\s+(?:de\s+)?entrada',
        'unit': 'percentage'
    },
    
    'Exit_Fee_Pct': {
        'category': 2,
        'table_name': 'Costes / Comisiones',
        'pattern': r'(?:comisi[óo]n|tarifa)\s+(?:de\s+)?salida',
        'unit': 'percentage'
    },
    
    'Ongoing_Charge': {
        'category': 2,
        'table_name': 'Costes / Comisiones',
        'pattern': r'(?:comisi[óo]n|gasto)\s+(?:anual|de gestión)',
        'unit': 'percentage'
    },
    
    # Atributos de POLÍTICA (Cat. 2)
    'Accumulation_Policy': {
        'category': 2,
        'table_name': 'Política de distribución',
        'pattern': r'(?:acumulaci[óo]n|capitalizaci[óo]n|reinversi[óo]n)',
        'unit': 'categorical'
    },
    
    'Distribution_Frequency': {
        'category': 2,
        'table_name': 'Política de distribución',
        'pattern': r'(?:frecuencia|periodicidad)\s+(?:de\s+)?distribuci[óo]n',
        'unit': 'categorical'
    },
    
    # Atributos de ESCENARIOS (Cat. 3)
    'PRIIPS_Performance_1Y': {
        'category': 3,
        'table_name': 'Escenarios PRIIPS',
        'pattern': r'(?:rendimiento|performance)\s+(?:estimado|esperado)?\s*1\s+a[ñn]o',
        'unit': 'percentage'
    },
    
    'PRIIPS_Performance_3Y': {
        'category': 3,
        'table_name': 'Escenarios PRIIPS',
        'pattern': r'(?:rendimiento|performance)\s+(?:estimado|esperado)?\s*3\s+a[ñn]os',
        'unit': 'percentage'
    },
    
    # ... etc para todos los atributos
}
```

### 6.2 Refactorización de Fase 6

**Antes (incorrecto):**
```python
# Fase 6: Distribución cat_max para fondos con atributo NULL
query = """
    SELECT cat_max, COUNT(*) AS n
    FROM dla_inv
    WHERE exit_fee_null = 1
    GROUP BY cat_max
"""
```

**Después (correcto):**
```python
# Fase 6: Distribución categoría CORRECTA para fondos con atributo NULL
def fase6_attribute_category_distribution(conn, attribute: str):
    target_category = ATTRIBUTE_CATEGORY_MAPPING[attribute]['category']
    category_flag = f'has_cat{target_category}_*'  # según atributo
    
    query = f"""
        SELECT {category_flag}, COUNT(*) AS n
        FROM dla_inv
        WHERE {attribute}_null = 1
        GROUP BY {category_flag}
    """
    # Esto muestra si el atributo NULL está en fondos que SÍ tienen la categoría
    # donde debería estar (falso positivo si sí, legítimo NULL si no)
```

---

## 7. DECISIÓN GO/NO-GO ACTUALIZADA

La decisión actual de BL-DLA-2 (GO/NO-GO) se basa en premisas falsas:
- ❌ "El ROI esperado de BL-DLA-2 es −50% en NULLs de `Exit_Fee_Pct`"
- **Motivo:** El diagnóstico actual sobreestima estos NULLs (falsos positivos)
- **Acción necesaria:** Recalcular diagnóstico con lógica correcta antes de decidir

**Nueva regla para decisión GO/NO-GO:**
```
SI (fondos_afectados_correctamente AND cat2_prevalence >= 30%) ENTONCES GO
SINO NO-GO (diferir hasta Sub-fase 1D >= 30% cobertura DLA Fase 1)
```

---

## 8. PRÓXIMOS PASOS OPERATIVOS

1. ✅ **Crear BL-DLA-2-LOGIC-FIX** en el backlog actualizado
2. ⏳ **Implementar mapeo `ATTRIBUTE_CATEGORY_MAPPING`** en `dla2_decision_diag.py v1.1`
3. ⏳ **Re-ejecutar diagnóstico completo** con lógica corregida
4. ⏳ **Recalcular KPIs de ROI** (esperado: reducción de NULLs más realista)
5. ⏳ **Emitir decisión GO/NO-GO actualizada** para BL-DLA-2

**Bloqueador:** BL-DLA-2-LOGIC-FIX debe completarse antes de cualquier decisión sobre BL-DLA-2.

---

**Fin del análisis crítico. Severidad: CRÍTICA — requiere acción inmediata.**
