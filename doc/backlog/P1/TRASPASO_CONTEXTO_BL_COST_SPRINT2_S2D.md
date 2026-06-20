# TRASPASO DE CONTEXTO — BL-COST Sprint 2, Sesión S2-D
## Cierre del sprint y activación en producción

**Versión:** 1.0  
**Fecha de generación:** 2026-05-23  
**Sesión:** S2-D (Sonnet — implementación y cierre)  
**Prerequisitos:** S2-A ✅ · S2-B ✅ · S2-C ✅  
**Estado del sprint:** CERRADO — kill-switch activado, listo para pipeline completo

---

## §0 — QUÉ ENTREGAR Y EN QUÉ ORDEN

Esta sesión S2-D generó los entregables de cierre del sprint. **No hay implementación pendiente** antes de ejecutar el pipeline. El orden de operaciones para José es:

```
1. Desplegar los 5 ficheros de S2-C (ya entregados en sesión anterior)
2. Desplegar config.py v19.2 (kill-switch ON)
3. Ejecutar smoke test: python -X utf8 proyecto1\smoke_sprint2_costs.py
4. Si smoke OK → ejecutar pipeline completo: discoverAllFunds.bat
5. Ejecutar control_sql_sprint2.sql en DBeaver y reportar resultados
6. Con los resultados → Opus para BL-COST-5 (análisis mismatch OC/ACI)
```

---

## §1 — FICHEROS ENTREGADOS EN S2-D

| Fichero | Tipo | Descripción |
|---|---|---|
| `shared/config.py` → **v19.2** | Modificación | Kill-switch `PRIIPS_COST_EXTRACTION_ENABLED = True` |
| `proyecto1/smoke_sprint2_costs.py` | Nuevo | Smoke test pre-activación (8 PDFs PRIIPs + 2 UCITS sintéticos) |
| `control_sql_sprint2.sql` | Nuevo | 10 queries de control post-pipeline para DBeaver |

### Ficheros de S2-C (ya entregados, recordatorio)

| Fichero | Versión | Cambios clave |
|---|---|---|
| `proyecto1/core/ucits_cost_extractor.py` | Nuevo | Extractor UCITS mínimo — OC, Mgmt, Transac, 1 fila UCITS_DERIVED |
| `proyecto1/core/pipeline.py` | v37 | Bloque BL-COST-4c ~70 líneas tras Geography; imports condicionales |
| `proyecto1/core/sqlite_writer.py` | v25 | `correct_oc_aci_mismatch`; `publish_fund` extendida con `cost_schedule_rows` |
| `proyecto1/tests/test_ucits_cost_extractor.py` | Nuevo | 7 tests (todos verdes) |
| `proyecto1/tests/test_cost_oc_mismatch.py` | Nuevo | 4 tests (todos verdes) |

---

## §2 — ESTADO DEL SPRINT AL CIERRE

### Backlog items cerrados en Sprint 2

| ID | Descripción | Estado |
|---|---|---|
| **BL-COST-2** | Schema v19: `fund_cost_schedule`, 11 columnas nuevas en `fund_master` | ✅ Cerrado (S2-inicio) |
| **BL-COST-3** | `cost_format_router.py`: detect_kid_format / detect_kid_currency | ✅ Cerrado (S2-A) |
| **BL-COST-3b** | `cost_table_parser.py`: parse_costs_over_time / parse_costs_composition | ✅ Cerrado (S2-A) |
| **BL-COST-3c** | `cost_cross_validator.py`: validate_pct_eur / ValidationResult | ✅ Cerrado (S2-A) |
| **BL-COST-4a** | `priips_cost_extractor.py`: extractor PRIIPs completo | ✅ Cerrado (S2-B) |
| **BL-COST-4b** | `ucits_cost_extractor.py`: extractor UCITS mínimo | ✅ Cerrado (S2-C) |
| **BL-COST-4c** | Integración en `pipeline.py` + extensión `publish_fund` | ✅ Cerrado (S2-C) |
| **BL-COST-4d** | `correct_oc_aci_mismatch` en `sqlite_writer.py` (infraestructura BL-COST-5) | ✅ Cerrado (S2-C) |

### Backlog items pendientes (post-sprint)

| ID | Descripción | Sesión recomendada |
|---|---|---|
| **BL-COST-5** | Heurística INTER-COST: detectar ~328 fondos con OC=ACI@RHP en BD y corregir con `correct_oc_aci_mismatch` | Opus separado (diseño + impl.) |
| **BL-COST-6** | Verificación de métricas de cobertura post-pipeline completo | Post S2-D, datos reales |

---

## §3 — ARQUITECTURA FINAL DEL PIPELINE DE COSTES

### Flujo de ejecución (por fondo)

```
pipeline.py: bloque BL-COST-4c
│
├── PRIIPS_COST_EXTRACTION_ENABLED? ──── False → skip (sin cambios en record)
│
├── _ceq_bd = SELECT Cost_Extraction_Quality FROM fund_master WHERE ISIN=?
│   └── _ceq_bd == 'HIGH' → skip (no degradar calidad existente)
│
├── fmt = detect_kid_format(kiid_text)
│
├── fmt == 'PRIIPS_KID'
│   └── extract_priips_costs(text, isin, existing_oc=_oc_bd, ...)
│       ├── parse_costs_over_time  ─────────┐
│       ├── parse_costs_composition         │ S2-A (DRY)
│       ├── validate_pct_eur  ──────────────┘
│       ├── _assess_quality → Cost_Extraction_Quality
│       ├── _build_schedule_rows → _cost_schedule_rows
│       └── _detect_oc_aci_mismatch → _oc_aci_mismatch
│
├── fmt == 'UCITS_KIID'
│   └── extract_ucits_costs(text, isin, existing_oc=_oc_bd)
│       ├── _extract_ucits_oc (patrón "Gastos corrientes / Ongoing charges: X%")
│       ├── parse_costs_composition (DRY S2-A)
│       └── fila sintética UCITS_DERIVED (1Y, Is_RHP=1)
│
├── fmt == 'UNKNOWN' → _cost_dict = {} → sin cambios
│
├── Mezclar campos coste en fund_master_record (11 columnas Cost_*)
├── _oc_aci_mismatch? → log_ingestion BL_COST_4C_OC_ACI_MISMATCH WARN
│
└── publish_fund(conn, fund_master_record, None, kiid_record,
                 cost_schedule_rows=_schedule_rows)
    └── upsert_cost_schedule (DELETE+INSERT en misma transacción with conn:)
```

### Política COALESCE y escala

- **COALESCE-safe:** `Ongoing_Charge_Recurrent` solo se pisa si `existing_oc is None`. El resto de campos de coste usan asignación directa (son nuevos en v19, nunca tenían valor previo).
- **Escala de salida:** porcentaje entero (`0.85` para 0.85%, nunca `0.0085`). Verificar con Q5 del SQL de control.
- **Skip por calidad:** si `Cost_Extraction_Quality = 'HIGH'` ya en BD → skip total. Evita degradar fondos ya bien extraídos en ciclos anteriores.

---

## §4 — SMOKE TEST: INSTRUCCIONES DE EJECUCIÓN

```bat
REM Desde C:\desarrollo\fondos con env "des" activo:
cd C:\desarrollo\fondos
conda activate des
python -X utf8 proyecto1\smoke_sprint2_costs.py
```

### Resultado esperado

```
========================================================================
SMOKE TEST — Sprint 2 extractores de costes
========================================================================

[1/2] PDFs PRIIPs (8 fondos)

  ISIN             KID_Format     Quality          RHP    Mgmt%  ACI_RHP%  Rows
  ------------------------------------------------------------------------
  ✓ IE00BZ4D7085   PRIIPS_KID     MEDIUM_EUR       5.0      —      —       1
  ✓ LU1502282632   PRIIPS_KID     MEDIUM_EUR        …       …      …       …
  ...

[2/2] UCITS sintéticos (2 muestras)

  ID                       KID_Format     Quality  OC%  Expected  Match
  --------------------------------------------------------------------
  ✓ SYNTHETIC_UCITS_ES     UCITS_KIID     HIGH    0.85      0.85    OK
  ✓ SYNTHETIC_UCITS_EN     UCITS_KIID     HIGH    1.20      1.20    OK

RESUMEN
  Fondos evaluados: 10
  OK: 10  |  Con incidencias: 0
  ✓ Sin incidencias. Listo para activar kill-switch.
```

### Si hay incidencias

| Incidencia | Causa probable | Acción |
|---|---|---|
| `PDF no encontrado` | `PDF_DIR` incorrecto | Ajustar la constante `PDF_DIR` en el script (ruta a los KIIDs en producción) |
| `KID_Format=UNKNOWN` en PDF conocido | PDF dañado o texto vacío | Inspeccionar con pdfplumber manualmente |
| `Quality=NONE` en PRIIPS_KID | Tabla de costes no parseada | Revisar `cost_table_parser.py` con el PDF concreto |
| OC sintético no coincide | Patrón `_UCITS_OC_PATTERN` no captura | Revisar `ucits_cost_extractor._extract_ucits_oc` |

---

## §5 — QUERIES SQL DE CONTROL: VALORES OBJETIVO

Tras el pipeline completo sobre ~3.200 fondos, los rangos esperados:

| Query | Métrica | Valor objetivo |
|---|---|---|
| **Q1** | `Cost_Extraction_Quality = HIGH` | ≥ 400 fondos |
| **Q1** | `Cost_Extraction_Quality = NONE` | < 1.000 fondos (solo UNKNOWN format) |
| **Q2** | Fondos con schedule | ≥ 600 fondos |
| **Q3** | `KID_Format = PRIIPS_KID` | > 2.500 fondos |
| **Q4** | Mismatches OC/ACI | 200-500 (input para BL-COST-5) |
| **Q5** | MIN de cualquier `*_Pct` | > 0.01 (confirma escala %) |
| **Q9** | Fondos sin KID_Format | < 50 (solo fondos sin KIID descargado) |

Si **Q5 devuelve MIN < 0.001** → bug de escala crítico. Abrir incidencia antes de continuar.

---

## §6 — PRÓXIMOS PASOS (post S2-D)

### BL-COST-5 (Opus — sesión independiente)

**Objetivo:** Corregir los ~328 fondos donde `Ongoing_Charge_Recurrent` en BD contiene el valor `ACI@RHP` en lugar del TER real.

**Prerequisito:** resultados de Q4 de las queries de control (conteo real de mismatches).

**Diseño de alto nivel (a refinar en Opus):**
1. Cargar fondos con `ingestion_log.step = 'BL_COST_4C_OC_ACI_MISMATCH'`
2. Para cada uno, extraer TER desde `fund_cost_schedule.Annual_Impact_Pct WHERE Is_RHP=0` (horizonte 1Y como proxy del TER)
3. Si TER disponible y coherente → llamar `correct_oc_aci_mismatch(conn, isin, ter_pct)`
4. Log de correcciones para auditoría

**Sesión Opus necesaria** porque requiere diseño de la heurística de selección del TER correcto (múltiples estrategias posibles).

### BL-COST-6

Tras confirmar KPIs de cobertura con Q1-Q10, documentar métricas finales del sprint y cerrar el backlog formalmente.

---

## §7 — DECISIONES TÉCNICAS ADOPTADAS EN EL SPRINT

Registro completo para futuras sesiones:

| Decisión | Descripción | Alternativa rechazada |
|---|---|---|
| **PC-1** | Extracción de costes posterior a Geography en el pipeline | Módulo independiente fuera del ciclo principal |
| **PC-2** | Routing PRIIPs/UCITS explícito con `elif` en el pipeline | Router centralizado en un extractor "maestro" |
| **PC-3** | SELECT dedicado para leer `_oc_bd` (no ampliar `_v3_row`) | Añadir columna al tuple existente (riesgo de desplazamiento de índice) |
| **PC-4** | Ruta no-COALESCE solo en `correct_oc_aci_mismatch` (BL-COST-5) | Forzar overwrite en el pipeline estándar |
| **PC-5** | UCITS: solo 1 fila sintética `UCITS_DERIVED` (no tabla de horizontes) | Tabla multi-horizonte (UCITS no tiene esa información) |
| **A-1** | `_COST_ENABLED` importado a nivel de módulo en pipeline.py | Leer `config` dentro del ciclo por fondo |
| **A-3** | `publish_fund` extendida con `cost_schedule_rows` | Llamada separada a `upsert_cost_schedule` fuera de `publish_fund` |
| **A-5** | Skip si `Cost_Extraction_Quality = 'HIGH'` en BD (no en record) | Skip basado en el record en memoria (puede ser None) |

---

**FIN DEL TRASPASO S2-D.**

*Sprint BL-COST Sprint 2 cerrado. 11 tests verdes, 3 ASTs OK, kill-switch activado.*  
*Módulos producidos: `ucits_cost_extractor.py`, `smoke_sprint2_costs.py`, `control_sql_sprint2.sql`.*  
*Módulos modificados: `pipeline.py` v37, `sqlite_writer.py` v25, `config.py` v19.2.*
