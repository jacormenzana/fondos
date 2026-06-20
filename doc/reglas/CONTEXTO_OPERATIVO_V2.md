# CONTEXTO OPERATIVO — Plataforma Análisis de Fondos

**Versión:** 2.0 (5 abril 2026)  
**Propósito:** Referencia rápida para sesiones operativas (95% de consultas)  
**Documentación completa:** `docs/TRASPASO_CONTEXTO_APR2026.md`

---

## 1. IDENTIFICACIÓN RÁPIDA

| Parámetro | Valor |
|-----------|-------|
| **Proyecto** | Análisis de Fondos de Inversión Europeos |
| **Stack** | Python 3.13, SQLite, Windows 10, Conda env: `des` |
| **Raíz** | `c:\desarrollo\fondos\` |
| **Base de datos** | `db/fondos.sqlite` |
| **Universo** | 3.204 fondos europeos |
| **Schema** | v16 (31-mar-2026) |
| **Objetivo negocio** | Preservación capital relativo a IPC+M3 (~6-7% anual, max drawdown 15%, horizonte 3-5 años) |

---

## 2. ARQUITECTURA (3 FASES)

```
P1: Ingesta, parsing KIID/DDF, clasificación estructural  → ACTIVA
P2: Enriquecimiento cuantitativo (métricas, sensibilidades macro) → DISEÑADA
P3: Scoring regime-aware, selección de cartera → DISEÑADA

Flujo: P1 → P2 → P3 (unidireccional, sin ciclos)
```

**P1 - Bloques de clasificación (secuencial, excluyente):**
1. Monetarios
2. RF Corto Plazo
3. RF Flexible
4. Renta Variable
5. Mixtos
6. Alternativos
7. Restantes

**Pipeline principal:** `proyecto1\run_block.py --block <bloque> --db <ruta> --master <xlsx>`

---

## 3. MÓDULOS CORE P1 (estructura directorio)

```
c:\desarrollo\fondos\
├── db\
│   ├── fondos.sqlite          ← BD principal
│   └── schema_fondos.sql      ← v16, fuente de verdad
├── proyecto1\
│   ├── run_block.py           ← Lanzador bloques
│   ├── core\
│   │   ├── io.py              ← get_kiid_for_isin(), mark_stale_for_refresh()
│   │   ├── pipeline_cache.py  ← run_block(), fund_master_record
│   │   ├── kiid_parser.py     ← parse_kiid_generic(), v17
│   │   ├── classify_utils.py  ← classify_fund(), detect_geography()
│   │   ├── sqlite_writer.py   ← publish_fund(), COALESCE logic
│   │   ├── srri_v4_geometric.py ← Extractor visual SRRI (MAX_BAND_ITER=15)
│   │   ├── fund_family_builder.py ← build_fund_families(), Regla 4
│   │   └── benchmark_normalizer.py ← ~97% cobertura benchmarks
│   ├── blocks\                ← monetarios.py, rf_corto.py, ..., restantes.py
│   └── shared\
│       ├── schema_checks.py   ← assert_schema_alignment()
│       └── export_tables.py   ← Exportación a XLSX
└── scripts\
    └── launch\
        ├── discoverAllFunds.bat ← Pipeline completo con logging
        └── mark_stale.py        ← Transición controlada a FORCE_REFRESH (max 50/ciclo)
```

---

## 4. ESTADO ACTUAL BD (31-mar-2026)

### Universo y KIID
| Métrica | Valor | % |
|---------|-------|---|
| Total fondos | 3.204 | 100% |
| KIID CACHED | 2.932 | 91,5% |
| KIID OK | 272 | 8,5% |
| KIID_Downloaded_At NULL | 478 | 14,9% (herencia bug, autosanante) |

### SRRI Validation
| Status | n | % | Nota |
|--------|---|---|------|
| MATCH (HIGH) | 3.043 | 94,9% | Visual=Textual |
| VISUAL_ONLY (MEDIUM) | 77 | 2,4% | 83% Nordea, fix implementado |
| CONFLICT (LOW) | 64 | 2,0% | Encolados FORCE_REFRESH |
| NOT_AVAILABLE | 18 | 0,6% | Irreducibles |
| TEXT_ONLY | 2 | 0,1% | - |

### Atributos — Cobertura
| Atributo | % Cobertura | Estado |
|----------|-------------|--------|
| SRRI | 99,4% | ✓ Óptimo |
| Fund_Nature | 100% | ✓ Óptimo |
| Geography | 87,7% | ✓ Bueno |
| Investment_Universe | 94,0% | ✓ Óptimo |
| Benchmark_Declared | 58,0% | Bajo (gap P3) |
| Ongoing_Charge (TER) | 73,8% | Bajo (gap P3) |
| Sfdr_Article | 61,9% → ~100% | Fix implementado (imputation Art.6/8) |
| Leverage_Used | 24,3% → ~65% | Fix implementado |
| Accumulation_Policy | 73,9% | Medio |
| Currency_Hedged | 19,8% | Bajo (estructural) |
| Market_Cap_Focus | 4,3% | Bajo (estructural, solo RV) |
| Sector_Focus | 8,2% | Bajo (estructural, solo RV) |

### Fund Families
- 2.626 familias, 375 multi-clase
- 9 familias con naturaleza inconsistente → Regla 4 corrige 6/9 automáticamente

---

## 5. MÁQUINA DE ESTADOS KIID_STATUS

```
CACHED         → Texto en BD, sin descarga HTTP (<1s proceso)
OK             → Descargado correctamente (igual que CACHED para el pipeline)
FORCE_REFRESH  → Re-descarga obligatoria en próximo ciclo
WRONG_DOC      → PDF no corresponde al ISIN
NOT_FOUND      → URL no responde
```

**Regla crítica de `io.py`:**
- Si `KIID_Status IN ('OK','CACHED')` y existe `Raw_KIID_Text` → devuelve de BD **sin HTTP**
- Solo `FORCE_REFRESH` y fondos nuevos disparan descarga HTTP
- `mark_stale_for_refresh()` marca max 50 fondos/ciclo para renovación (antigüedad >180 días)

**Política HTTP (io.py):**
- 3 reintentos, backoff 1s/2s/4s, timeout=15s
- Status forcelist: 500, 502, 503, 504
- 429 (throttling) NO reintenta → queda como DOWNLOAD_ERROR

---

## 6. PRINCIPIOS NO NEGOCIABLES (7 REGLAS)

Consultar `PRINCIPIOS_DISENO.md` para detalles completos. Resumen:

1. **COALESCE obligatorio** — Nunca NULL override en SQLite
2. **Root cause > síntomas** — No parches, solo fixes estructurales
3. **Verificar ficheros antes de modificar** — Leer producción, no asumir
4. **Scoring regime-aware** — No métricas globales sin contexto macro
5. **Señales genéricas** — No nombres específicos de fondo en clasificación
6. **SRRI no es fallback** — Clasificación por contenido semántico, no por riesgo
7. **Corrección en módulo correcto** — No SQL ad-hoc para fix de clasificación

---

## 7. MÓDULOS PENDIENTES DESPLIEGUE (12 archivos actualizados)

**Prioridad ALTA (pendiente desde 31-mar-2026):**

| Fichero | Ruta | Cambio principal |
|---------|------|------------------|
| `mark_stale.py` | `scripts/launch/` | **PENDIENTE** (Paso 0 falló en último ciclo) |
| `io.py` | `proyecto1/core/` | Sin Opción B automática; mark_stale_for_refresh(); retry 1/2/4s |
| `sqlite_writer.py` | `proyecto1/core/` | COALESCE KIID_Downloaded_At |
| `srri_v4_geometric.py` | `proyecto1/core/` | MAX_BAND_ITER=15 (fix Robeco blob) |

**Prioridad MEDIA:**

| Fichero | Ruta | Cambio principal |
|---------|------|------------------|
| `pipeline_cache.py` | `proyecto1/core/` | SFDR imputation Art.6/8; srri_textual_prev |
| `kiid_parser.py` | `proyecto1/core/` | +8 patrones SRRI EN (Nordea); _detect_leverage expandida |
| `fund_family_builder.py` | `proyecto1/core/` | Regla 4: nombre+SRRI para familias 50/50 |
| `classify_utils.py` | `proyecto1/core/` | Ventana DDF [500:5000]; señales P1 |
| `fund_characterizer.py` | `proyecto1/core/` | Atributos v3 (Market_Cap, Sector, Hedged...) |

**Prioridad BAJA (soporte):**

| Fichero | Ruta | Cambio principal |
|---------|------|------------------|
| `schema_fondos.sql` | `db/` | v16: columnas v3 + telemetría |
| `schema_checks.py` | `proyecto1/shared/` | assert_schema_alignment(); v16 columns |
| `migrate_schema_v16.py` | `scripts/mig/` | Añade 6 columnas v16 (idempotente) |

**Orden de despliegue:**
1. `migrate_schema_v16.py --dry-run` → `migrate_schema_v16.py`
2. Resto de módulos core
3. SQL manual: `UPDATE fund_kiid_metadata SET KIID_Status='FORCE_REFRESH' WHERE ISIN='LU2306921227' AND KIID_Class=1;` (DWS ESG Blue Economy)

---

## 8. BACKLOG ACTIVO (SÓLO IDS)

**Sprint completado (31-mar-2026):**
- [x] P01, P05, P06, P07, P08 (KIID/HTTP fixes)
- [x] P2b, P2c, P3 (Nordea, SFDR, Leverage, Familias)

**Pendiente inmediato:**
- [ ] **P09** — Desplegar mark_stale.py (Paso 0 pipeline)
- [ ] **P10** — Verificar impacto: Nordea 64 VISUAL_ONLY → MATCH
- [ ] **P11** — Verificar impacto: Sfdr_Article 61,9% → ~100%
- [ ] **P12** — Verificar impacto: Leverage_Used 24,3% → ~65%
- [ ] **P13** — Processing_Time_Ms almacena segundos (no ms) — renombrar o re-instrumentar
- [ ] **I-08** — DWS ESG Blue Economy SRRI=7 sospechoso → SQL FORCE_REFRESH

**Pendiente P3 (régimen-aware scoring):**
- [ ] Implementar scoring regime-aware (5 fases diseñadas)
- [ ] Macro factores P2: BAMLH0A0HYM2 (HY spread), VIXCLS, T10Y2YM (term spread)
- [ ] Completar TER/Benchmark vía API Morningstar (~840/1.345 fondos)
- [ ] Benchmark_Declared 58% → objetivo 65%

**Detalles completos:** `docs/TRASPASO_CONTEXTO_APR2026.md` sección 8

---

## 9. QUERIES SQL FRECUENTES

### Estado SRRI
```sql
SELECT COUNT(*) AS total,
    SUM(CASE WHEN SRRI_Visual IS NOT NULL THEN 1 ELSE 0 END) AS con_visual,
    SUM(CASE WHEN SRRI_Validation_Status='MATCH' THEN 1 ELSE 0 END) AS match,
    SUM(CASE WHEN SRRI_Validation_Status='CONFLICT' THEN 1 ELSE 0 END) AS conflict,
    SUM(CASE WHEN SRRI_Validation_Status='NOT_AVAILABLE' THEN 1 ELSE 0 END) AS not_available
FROM fund_kiid_metadata WHERE KIID_Class=1;
```

### Fondos lentos (telemetría)
```sql
SELECT km.ISIN, fm.Fund_Name, km.Processing_Time_Ms, km.Processing_Breakdown, km.KIID_Status
FROM fund_kiid_metadata km JOIN fund_master fm ON km.ISIN=fm.ISIN
WHERE km.Processing_Time_Ms IS NOT NULL
ORDER BY km.Processing_Time_Ms DESC LIMIT 20;
```

### Marcar fondos para re-descarga
```sql
UPDATE fund_kiid_metadata SET KIID_Status='FORCE_REFRESH'
WHERE SRRI_Visual IS NULL AND KIID_Class=1;
```

### Verificar schema v16 (columnas atributos v3)
```sql
SELECT name FROM pragma_table_info('fund_master')
WHERE name IN ('Market_Cap_Focus','Sector_Focus','Currency_Hedged','Investment_Universe');
```

### Familias con naturaleza inconsistente
```sql
SELECT ff.family_id, ff.family_name, ff.Fund_Nature, 
       GROUP_CONCAT(DISTINCT fm.Fund_Nature) AS natures_in_family
FROM fund_families ff
JOIN fund_master fm ON fm.fund_family_id = ff.family_id
GROUP BY ff.family_id
HAVING COUNT(DISTINCT fm.Fund_Nature) > 1;
```

---

## 10. CICLO EJECUCIÓN TÍPICO

**Comando pipeline completo:**
```batch
cd c:\desarrollo\fondos\scripts\launch
discoverAllFunds.bat
```

**Duración esperada:** 8-12 minutos (3.204 fondos, ~100% <1s/fondo)  
**Log ubicación:** `proyecto1\log\log_pipeline_YYYYMMDD_HHMMSS.log`

**Pasos del pipeline (discoverAllFunds.bat):**
```
Paso 0: mark_stale.py (max 50 fondos → FORCE_REFRESH)
Paso 1: monetarios
Paso 2: rf_corto
Paso 3: rf_flexible
Paso 4: renta_variable
Paso 5: mixtos
Paso 6: alternativos
Paso 7: restantes
Paso 8: fund_family_builder (validación consistencia)
```

**Ciclo último (31-mar-2026):**
- Duración: 8m 20s
- Fondos procesados: 3.204 (100%)
- Errores: Paso 0 falló (mark_stale.py no encontrado), 1 fondo no publicado en RESTANTES

---

## 11. UBICACIÓN DE DOCUMENTACIÓN COMPLETA

| Documento | Ubicación | Propósito |
|-----------|-----------|-----------|
| Este archivo | `docs/operativo/CONTEXTO_OPERATIVO_V2.md` | Referencia rápida operativa |
| Schema | `docs/operativo/SCHEMA_REFERENCE.md` | Tablas y columnas v16 |
| Principios | `docs/operativo/PRINCIPIOS_DISENO.md` | 7 reglas no negociables |
| Workflows | `docs/operativo/WORKFLOWS_ESTRUCTURADOS.md` | Templates por tipo de tarea |
| Contexto completo | `docs/TRASPASO_CONTEXTO_APR2026.md` | Detalle exhaustivo |
| Especificación funcional | `docs/estrategia/DOCUMENTO_FUNCIONAL_*.odt` | Arquitectura formal |
| Análisis comparativos | `docs/estrategia/AnalisisCaracterizacion*.ods` | Propuestas ChatGPT/Gemini/Claude |

---

## 12. INSTRUCCIONES PARA CLAUDE

**Al recibir consulta operativa:**
1. Identificar NIVEL de complejidad (0-4) según `WORKFLOWS_ESTRUCTURADOS.md`
2. Validar que contexto cargado sea mínimo viable para ese nivel
3. Seguir workflow estructurado según tipo de tarea
4. Aplicar principios de diseño (especialmente #1 COALESCE, #2 Root cause)

**Restricciones críticas:**
- 0 errores de sintaxis en código Python (validar con `ast.parse()`)
- 0 referencias a columnas/tablas inexistentes (verificar contra `SCHEMA_REFERENCE.md`)
- Nunca proponer soluciones que violen principios de diseño
- Leer siempre fichero de producción antes de modificarlo (no asumir contenido)

**Nivel de calidad esperado:**
- Root cause analysis completo antes de proponer fix
- Código listo para producción (no prototipos)
- SQL validado contra schema real
- Trazabilidad de decisiones (comentarios en código)

---

**FIN CONTEXTO OPERATIVO V2**

*Última actualización: 5 abril 2026*  
*Versión schema: v16 (31-mar-2026)*  
*Tokens estimados: ~4.200*
