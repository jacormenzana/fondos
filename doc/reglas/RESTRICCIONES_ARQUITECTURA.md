# RESTRICCIONES_ARQUITECTURA — Proyecto 1 Análisis de Fondos

**Versión:** 1.0
**Fecha:** 25 de abril de 2026
**Propósito:** documento de referencia obligatorio para cualquier sesión de codificación (humana o asistida por LLM) sobre los módulos de Proyecto 1. Define **restricciones inviolables** y **principios de aplicación operativa** derivados de los defectos detectados en ciclos previos.

> **Uso recomendado:** este documento debe incluirse en cada upload de codificación, junto con los módulos directamente afectados. Antes de modificar cualquier archivo, leer este documento y verificar que la solución propuesta no viola ninguna restricción. Si hay tensión entre la solicitud y una restricción, parar y reportar.

---

## 1. CONTEXTO ESTRUCTURAL DE LOS MÓDULOS

El pipeline está formado por estos módulos, en orden de ejecución por fondo:

| Módulo | Función | Entrada | Salida |
|--------|---------|---------|--------|
| `kiid_parser.py` | Parser KIID determinista | PDF/texto bruto | dict con extractos numéricos y textuales |
| `classify_utils.py` | Helpers clasificación + traducción | nombre, KIID | strategy, theme, mapas EN→ES |
| `blocks/*.py` | Clasificadores especializados (MONETARIOS, RF, RV, MIXTOS, ALTERNATIVO, RESTANTES) | parsed dict | classification dict |
| `fund_characterizer.py` | Enriquecimiento universal (Subtype, Style, Currency_Hedged, Sector_Focus, etc.) | classification dict + KIID | classification enriquecido |
| `pipeline.py` | Orquestador: lee BD, llama bloques+characterize, aplica reglas INTER, llama writer | maestro Excel | UPSERT a SQLite |
| `sqlite_writer.py` | Persistencia idempotente (UPSERT con COALESCE) | record dict | escritura BD |

Cada módulo tiene **una responsabilidad** y debe respetarla.

---

## 2. RESTRICCIONES INVIOLABLES

### R-1 — Punto único de normalización lingüística

La normalización de valores categóricos (mapas EN→ES para Sector_Focus, Type, Family, Subtype, Theme) **vive EXCLUSIVAMENTE en `classify_utils.py`**.

**Prohibido:**
- Crear mapas paralelos en `pipeline.py`, `fund_characterizer.py`, `blocks/*.py`, `kiid_parser.py`.
- Hardcodear mapeos inline en condicionales de bloques o pipeline.

**Permitido y obligatorio:**
- Importar las funciones canónicas (`map_theme_to_sector_focus`, `normalize_sector_focus`, `apply_post_characterize_normalization`) desde `classify_utils.py`.
- En `sqlite_writer.py`, mantener `_normalize_record` y `_post_upsert_normalize_db` exclusivamente como **defensa en profundidad** que duplica intencionalmente los mapas de `classify_utils.py`. Esta es la única excepción autorizada al DRY estricto y debe documentarse en cada definición.

**Justificación:** la duplicación de mapas en múltiples módulos es la causa estructural del 50% de los defectos lingüísticos detectados (BL-22, BL-53, BL-54). Cada nuevo Theme/Type/Family añadido debe ser un cambio en una sola línea.

### R-2 — Cualquier modificación de atributo persistido requiere triple acción

Si la modificación afecta el valor canónico de un atributo persistido en `fund_master` (Sector_Focus, Type, Family, Subtype, Currency_Hedged, Hedging_Policy, Investment_Universe, Geography, Profile, etc.), la solución completa requiere las **tres acciones obligatorias**:

1. **Fix del classifier/characterizer** que produce el valor.
2. **Fix de pipeline.py** si hay reglas INTER que dependen del valor.
3. **Migración SQL one-shot** sobre la BD existente que sanea valores stale, OR re-ingesta forzada (`FORCE_REFRESH`) sobre los fondos afectados.

**Razón:** el modo `CACHED` de re-ejecución del pipeline no fuerza re-extracción ni re-clasificación. La cláusula UPSERT con COALESCE preserva el valor stale de BD cuando el record entrante tiene NULL. Solo (3) lo elimina.

**Excepción:** si `_post_upsert_normalize_db()` cubre el atributo afectado (Sector_Focus, Type, Subtype), la migración SQL es opcional para ese ciclo: bastará con que el ciclo siguiente ejecute el UPSERT sobre todos los fondos. Pero recomienda hacerla igualmente para evitar esperar al próximo ciclo.

### R-3 — Atributos CACHED y la condición de reclasificación

En `pipeline.py` el flag `_needs_char` (línea ~395) controla si `characterize_fund()` se invoca. Si se ha de añadir un nuevo atributo a `characterize_fund`, **se debe añadir su columna a la query SELECT de la línea ~402** que evalúa `_needs_char`. De lo contrario, los fondos CACHED con ese atributo NULL nunca llamarán a characterize y el atributo permanecerá NULL indefinidamente.

**Patrón obligatorio de implementación:**

```python
_v3_row = conn.execute(
    "SELECT Investment_Universe, Accumulation_Policy, Currency_Hedged, "
    "Investment_Focus, Credit_Quality, Geography, Fund_Nature, "
    "<NUEVO_ATRIBUTO> "
    "FROM fund_master WHERE ISIN=?", (isin,)
).fetchone()
# añadir el chequeo en el any() correspondiente
```

### R-4 — Las reglas INTER usan valores efectivos, no actuales

Cualquier regla INTER (validación cruzada de atributos: Profile-SRRI, Strategy-Replication, Currency_Hedged-Hedging_Policy, Universe-Geography, Nature-Type, etc.) debe usar el **valor efectivo**, definido como:

```python
_X_efectivo = fund_master_record.get("X") or _X_bd
```

donde `_X_bd` es el valor preservado por COALESCE de la BD previa. Ya están leídos en `pipeline.py` líneas 911–922 (`_sf_bd`, `_ch_bd`, `_hp_bd`, `_if_bd`, `_bench_bd`, `_benchtype_bd`).

**Prohibido:** usar `fund_master_record.get("X")` en aislamiento dentro de reglas INTER, porque para fondos CACHED ese valor puede ser None aunque BD tenga el valor real.

**Causa raíz que esta restricción previene:** todos los fixes BL-30, BL-31, BL-45, BL-46, BL-49/2 nacen del mismo defecto — clasificadores reglas INTER que ignoraban el valor BD.

### R-5 — Word boundary (`\b`) en regex sobre nombres de fondos

El metacaracter `\b` en regex Python requiere transición word→non-word. **Es inválido entre dos letras**. Los nombres de share classes están plagados de fusiones letra+letra:

| Nombre real | Patrón ingenuo | Resultado |
|-------------|----------------|-----------|
| `EURHDG` | `\bhdg\b` | **NO match** (E-U-R-H-D-G todo word chars) |
| `EURH ACC` | `\beurh\b` | match (boundary con espacio) |
| `EURHIGH` | `\beurh\b` | **NO match** (no boundary entre EURH y IGH) — bueno |
| `M&G GL` | `\bmg\b` | match (& es non-word) |

**Patrón correcto para detectar sufijos pegados a divisa:**
```python
r'(?:eur|usd|gbp|chf|jpy|cnh)(?:hdg|hgd|h(?=\s|$))'
```

(usar lookahead para asegurar que H solo va seguido de espacio o fin)

**Causa raíz que esta restricción previene:** los 7 fondos en regresión BL-49/2 (ciclo 25/04/2026).

### R-6 — Funciones de inferencia con condición global son frágiles

Cuando una función de inferencia sobre texto KIID (`_infer_X_from_structure`, etc.) verifica "ausencia de keyword Y", la verificación **debe acotarse a una ventana** alrededor del punto de interés, NO al texto completo.

**Razón:** los KIIDs reales contienen menciones incidentales en glosarios, FAQs, encabezados, índices. Una verificación global produce falsos negativos masivos (BL-55 v26 inicial: 3 capturas sobre 670 candidatos esperados).

**Patrón correcto:**
```python
section_pos = t_lower.find(SECTION_KEYWORD)  # primer match
window = t_lower[max(0, section_pos-1500):section_pos+1500]
if any(kw in window for kw in EXCLUSION_KEYWORDS):
    return None  # solo bloquea si la mención está cerca del contexto
```

### R-7 — Tests obligatorios antes de declarar BL completado

Toda implementación de un BL debe acompañarse de **tests funcionales que se ejecuten sin pipeline completo**. Estos tests deben:

1. Cubrir los casos positivos (esperado: el patrón se detecta).
2. Cubrir los casos de control (esperado: NO falsos positivos).
3. Cubrir los casos de regresión documentados en el backlog.
4. Ejecutarse aislados (sin imports de `pipeline.py`, `core.io`, `proyecto1.*`) — extraer la función a módulo independiente o usar mocks.

**Sin tests pasando no se aprueba el BL.**

### R-8 — Validación AST tras cada edit

Tras cualquier `str_replace` o edición sobre un módulo Python, ejecutar:

```python
python -c "import ast; ast.parse(open('archivo.py').read()); print('AST OK')"
```

Antes de declarar la edición completa. Si la AST falla, no se acepta el cambio.

---

## 3. PRINCIPIOS OPERATIVOS

### P-1 — Diagnóstico antes de codificación

Para cualquier BL que implique modificación de un atributo persistido o regla INTER, **antes de escribir código** verificar:

- [ ] ¿Cuál es la distribución actual del atributo en BD? (export Excel + `value_counts`)
- [ ] ¿Cuántos fondos están afectados por el defecto? (query SQL exacta)
- [ ] ¿Cuál es la causa raíz, no el síntoma? (Principio #1 ya documentado)
- [ ] ¿Qué módulos emiten ese atributo? (`grep` en todos los .py)
- [ ] ¿Hay COALESCE sobre ese atributo en sqlite_writer? (sí, mirar línea ~263 onwards)

Si alguna respuesta es "no sé", se hace el diagnóstico antes de codificar.

### P-2 — Minimización de cambios

Preferir parches quirúrgicos (`str_replace`) sobre regeneración completa de archivos. Cada cambio debe:
- Modificar mínimas líneas.
- Comentar la modificación con BL-XX y razón.
- Preservar el resto del módulo intacto.

**Razón:** la regeneración completa de archivos es la principal causa de:
- Pérdida de imports y funciones auxiliares no documentadas.
- Renumeración de líneas que rompe documentación.
- Reescritura de regex con escape errors.
- Tokens consumidos innecesariamente.

### P-3 — Coordinación Opus / Sonnet

Si la sesión usa el flujo Opus → Sonnet (planificación + codificación):

**Opus** (planificador) entrega un plan que incluye:
- BL afectados con prioridad.
- Para cada BL: causa raíz, especificación de código (no solo descriptiva), módulo y línea aproximada.
- Lista de tests funcionales a producir.
- Restricciones aplicables de este documento (citadas por número R-X).
- Plan de migración SQL si aplica (R-2).

**Sonnet** (codificador) recibe el plan + este documento + módulos relevantes (incluyendo siempre `sqlite_writer.py` y `pipeline.py`) y debe:
- Antes de tocar código, leer este documento.
- Reportar tensiones con las restricciones antes de codificar.
- Validar AST tras cada edit (R-8).
- Producir tests (R-7).
- Reportar cuando una restricción aplica al cambio.

### P-4 — Smoke test post-implementación

Antes de declarar un BL completado, ejecutar smoke test sobre 5–10 ISINs canónicos del defecto. Si el ciclo regular no se puede ejecutar, simular el flujo en SQLite memoria con datos sintéticos (como se hizo en BL-53/54 fix arquitectónico).

**El smoke test detecta el defecto COALESCE en menos de 30 segundos**; ejecutarlo evita ciclos completos infructuosos.

---

## 4. CHECKLIST PRE-COMMIT

Antes de aprobar un cambio:

- [ ] AST OK (R-8)
- [ ] Tests funcionales escritos y pasando (R-7)
- [ ] Si afecta atributo persistido: ¿incluye migración SQL o se puede esperar al próximo ciclo? (R-2)
- [ ] Si añade atributo: ¿se añadió a la query `_v3_row` de pipeline? (R-3)
- [ ] Si es regla INTER: ¿usa valores efectivos `_X_p = ... or _X_bd`? (R-4)
- [ ] Si tiene regex sobre nombres: ¿probado contra fusiones letra+letra como EURHDG? (R-5)
- [ ] Si es función de inferencia sobre texto: ¿usa ventana acotada? (R-6)
- [ ] No se han creado mapas duplicados en múltiples módulos? (R-1)
- [ ] Cabecera del módulo actualizada con versión y descripción del cambio?
- [ ] El cambio resuelve la causa raíz, no solo el síntoma? (Principio #1 del proyecto)

---

## 5. APÉNDICE — REGRESIONES HISTÓRICAS Y SUS LECCIONES

| Ciclo | BL | Síntoma | Causa raíz | Restricción derivada |
|-------|----|---------| -----------|----------------------|
| 23/04 | BL-49 v1 | Currency_Hedged NULL persistente (728) | `detect_currency_hedged` no leía KIID | R-3 |
| 23/04 | BL-50 | Universe poblado, Geography NULL (110) | Sin inferencia direccional Universe→Geography | — |
| 23/04 | BL-52 | Universe='Country' con Geography=región (12) | Sin auto-corrección Country↔Regional | — |
| 23/04 | BL-53 | Sector_Focus en inglés (20) | Mapa Theme→Sector duplicado en 2 módulos | R-1 |
| 25/04 | BL-49 v2 | 7 fondos Hedged → Unhedged | (a) `_HEDGED` sin variantes EURH; (b) regex `\b` falla en EURHDG; (c) default no consideraba `_ch_bd` | R-4, R-5 |
| 25/04 | BL-53/54 v2 | 20 fondos siguen en inglés tras Sonnet | COALESCE preserva valor stale; Sonnet añadió mapa duplicado en sqlite_writer | R-1, R-2 |
| 25/04 | BL-55 v1 | Inferencia Exit_Fee=0 captura solo 3/670 | Ventana global, no acotada | R-6 |

---

**Fin del documento. Versión 1.0 — 25 de abril de 2026.**
