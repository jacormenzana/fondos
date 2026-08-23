# PROVENANCE_DATOS_MACRO — Macro Data Sourcing & Provenance

**Propósito:** Documento canónico que gobierna la adquisición, proveniencia y política de
actualización de todos los datos macroecómicos del sistema P2.

---

## 1. Fuentes de datos (SOURCES registry)

El registro canónico de fuentes vive en `proyecto2/src/discovery/macro_discovery.py` como el dict
`SOURCES`. La tabla siguiente es auto-generada por `sync_agents_md.py` desde ese registro; no
editar manualmente.

Ver sentinel `data-sources` en AGENTS.md o ejecutar:
```bash
python scripts/audit/sync_agents_md.py --write
```

| Source | API / Endpoint | Clave API | Indicadores principales |
|--------|---------------|-----------|------------------------|
| `ine` | INE API REST (`servicios.ine.es`) | Ninguna (pública) | IPC mensual España (`ipc_index`) |
| `bce` | BCE SDW REST (`data-api.ecb.europa.eu`) | Ninguna (pública) | IPC Eurozona, M3 YoY, M2, tipo depósito BCE, tipo refi |
| `fred` | FRED API (`api.stlouisfed.org`) | Opcional — ver §3 | IPC EE.UU./JP/CN/ES, M2 EE.UU., tipos Fed/BoJ/PBoC, WTI, cobre, VIX, spread HY/IG, DXY, oro, term spread, EUR/JPY/GBP/CNY |
| `eurostat` | Eurostat REST (`ec.europa.eu/eurostat/api`) | Ninguna (pública) | PIB nominal Eurozona, déficit/PIB |

---

## 2. Indicadores por fuente

### INE (España)
- `ipc_index` — IPC mensual España, base 2015=100 (serie `IPC251856`).
- Escrito en `series_inflation` (`write_inflation=True`).
- También escrito en `series_macro` (`indicator=ipc_index`, `geography=ES`) para contexto cruzado.

### BCE Statistical Data Warehouse (SDW)
Series definidas en `_BCE_SERIES` en `macro_discovery.py`:

| Serie interna | Indicador | Geography | Target DB |
|---------------|-----------|-----------|-----------|
| `ipc_yoy_EU` | `ipc_index` | EU | `series_inflation` + `series_macro` |
| `m3_yoy_EU` | `m3_yoy` | EU | `series_macro` |
| `m2_yoy_EU` | `m2_yoy` | EU | `series_macro` |
| `rate_deposit_EU` | `rate_deposit` | EU | `series_macro` |
| `m2_level_EU` | `m2_level` | EU | `series_macro` |
| `m3_level_EU` | `m3_level` | EU | `series_macro` |
| `rate_refi_EU` | `rate_policy` | EU | `series_macro` |

API pública sin clave. Rate limit: ~5 req/s.

### Fed FRED
Series definidas en `_FRED_SERIES` en `macro_discovery.py` (~25 series). Selección destacada:

| FRED ID | Indicador | Geography | Nota |
|---------|-----------|-----------|------|
| `CPIAUCSL` | `ipc_index` | US | Mensual |
| `CP0000ESM086NEST` | `ipc_index` | ES | HICP España (Eurostat via FRED) |
| `M2SL` | `m2_level` | US | bn USD; YoY calculado por builder |
| `FEDFUNDS` | `rate_policy` | US | — |
| `DCOILWTICO` | `oil_wti` | GLOBAL | WTI, USD/barril |
| `BAMLH0A0HYM2` (ICE BofA) | `spread_hy` | GLOBAL | **Limitada a ~3 años** — licencia ICE BofA, clave FRED no ayuda |
| `BAA10YM` (Moody's) | `spread_ig` | GLOBAL | Pública, sin clave, historial desde 1953. Proxy IG (Baa−10Y UST) |
| `VIXCLS` | `vix` | GLOBAL | — |
| `T10Y2Y` | `term_spread` | US | 10Y − 2Y UST |
| `DEXJPUS`, `DEXUSUK`, `DEXCHUS` | `eur_jpy`, `eur_gbp`, `eur_cny` | GLOBAL | Tipos de cambio |

### Eurostat
- `nama_10_gdp` — PIB nominal Eurozona (Q, interpolado a mensual).
- `gov_10dd_edpt1` — Déficit/PIB (anual, interpolado a mensual).
- Escrito en `series_macro`.

---

## 3. Gestión de la clave API de FRED

**Ubicación en el repositorio (no commiteada — solo referencia):**
- `doc/api_key/fred_santLouis_apikey.txt`
- `doc/memoria/fred_santLouis_apikey.txt`

**Impacto sin clave:**
- `spread_hy` (ICE BofA `BAMLH0A0HYM2`) queda limitada a ~3 años. La restricción de licencia aplica incluso con clave API.
- `spread_ig` usa `BAA10YM` (Moody's) — público, historial completo desde 1953, no afectado.
- El resto de series FRED son públicas y no se ven afectadas.

**Uso:**
```bash
# Opción 1: variable de entorno
export FRED_API_KEY=<tu_clave>
python -m proyecto2.src.discovery.macro_discovery --source fred

# Opción 2: argumento directo
python -m proyecto2.src.discovery.macro_discovery --source fred --fred-api-key <tu_clave>
```

**La clave NO debe committearse al repositorio.** Si se necesita en CI, usar `FRED_API_KEY` como
GitHub Secret y pasarla como variable de entorno al runner.

---

## 4. Política de escritura en DB

| Tabla | Qué recibe |
|-------|-----------|
| `series_inflation` | Filas con `write_inflation=True` — IPC España (INE), IPC Eurozona (BCE), IPC EE.UU. (FRED) |
| `series_macro` | Todos los indicadores + réplica de registros de inflation para contexto cruzado |
| `series_benchmark` | Populated by `P1_refreshBenchmarks.bat` (fuente: Morningstar) — no tocado por macro_discovery |
| `nav_sources` | Gestión de cobertura NAV por ISIN — populated by `nav_discovery`, no por macro_discovery |

Upsert: `INSERT OR REPLACE` sobre `(date, indicator, geography)` — **idempotente**.  
La re-ejecución de una fuente no duplica datos.

---

## 5. Cadencia de actualización recomendada

| Fuente | Frecuencia | Datos disponibles | Lag típico |
|--------|-----------|-------------------|-----------|
| INE | Mensual | ~día 13 del mes siguiente | 0 días desde publicación |
| BCE | Mensual | ~día 15 del mes siguiente | 0 días desde publicación |
| FRED | Mensual | ~día 15–20 del mes siguiente | Variable por serie |
| Eurostat | Trimestral | ~60 días tras fin de trimestre | 0 días desde publicación |

**Referencia:** ejecutar el día 20 de cada mes (`P2_discoverLoadMetrics.bat`). A esa fecha la
mayoría de fuentes han publicado los datos del mes anterior.

---

## 6. Comando de actualización

```batch
# Descarga y actualización incremental (todas las fuentes)
scripts\launch\P2_discoverLoadMetrics.bat

# Fuente individual (debug)
python -X utf8 -m proyecto2.src.discovery.macro_discovery --source bce
python -X utf8 -m proyecto2.src.discovery.macro_discovery --source fred
python -X utf8 -m proyecto2.src.discovery.macro_discovery --source eurostat
python -X utf8 -m proyecto2.src.discovery.macro_discovery --source ine
```

---

## 7. Relación con el modelo OLS de P2

`proyecto2/src/calculations/macro_sensitivity.py` consume las series de `series_macro` para
calcular los 24 betas OLS del modelo factor. Los `factor_key` que necesita (columna `indicator`
en `series_macro`) son los definidos en `_FACTOR_TO_METRIC` (ver sentinel `macro-factors` en AGENTS.md).

Un factor faltante en `series_macro` produce un coeficiente beta `NULL` en `fund_metrics` para
los fondos correspondientes — no es un error fatal, pero reduce la potencia del modelo.

---

---

## 8. Series discontinuadas y estado de cobertura (2026-08-19)

### China IPC — serie migrada

| | Old | New |
|---|---|---|
| FRED ID | `CHNCPALTT01IXNBM` | `CPALTT01CNM657N` |
| Última obs | 2023-11 | 2024-03 (at migration) |
| Concepto | OECD MEI (discontinuado en FRED) | OECD CPALTT01 all items, NSA, idx 2015=100 |
| Unidades | index | index — compatible; `pct_change(12)` es base-agnostic |

La migración extiende `ipc_yoy_cn` ~4 meses hacia el presente. Actualización posterior:
re-ejecutar `P2_discoverLoadMetrics.bat` o `macro_discovery --source fred`.

### Japan IPC — sin fuente mantenida en FRED (pendiente)

`JPNCPIALLMINMEI` (OECD MEI) está discontinuada en FRED con último dato **2021-06**. Todas las
variantes OECD de FRED para Japón (incluidas `CPALTT01JPM657N`, `CPALTT01JPM661S`) también
terminan en 2021-06 — es una discontinuación en origen, no sólo del identificador.

**Impacto mitigado por Layer 2 (commit d7925c0):** `compute_macro_sensitivity()` aplica
selección por ventana por fondo — `ipc_yoy_jp` se descarta automáticamente en fondos cuya
cobertura en ventana propia < 85 %, evitando que trunce el OLS de ningún fondo. Los fondos
de geografía Japan retienen `d_rate_jp`, `eur_jpy_yoy` y los demás factores regionales.

**Fuentes alternativas a evaluar (requieren nuevo loader):**
- OECD SDMX REST API (`sdmx.oecd.org`) — actualmente inaccesible desde el entorno de
  desarrollo (404); revisar en próximo ciclo de mantenimiento.
- Japan Statistics Bureau e-Stat API — requiere registro; datos mensuales actualizados.
- IMF IFS API (`PCPI_IX`, frecuencia M) — requiere nuevo `load_imf_series()`.

Hasta que se resuelva, `ipc_yoy_jp` permanece en `series_macro` con datos hasta 2021-06
como referencia histórica para fondos de geografía JP con ventana anterior a 2021.

*Última revisión: 2026-08-19*
