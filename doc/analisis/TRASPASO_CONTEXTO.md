# DOCUMENTO DE TRASPASO — PLATAFORMA FONDOS DE INVERSIÓN
## Fecha: 22 marzo 2026 | Castro Urdiales, Cantabria

---

## 1. DESCRIPCIÓN DEL PROYECTO

Plataforma de análisis y gestión de cartera de fondos de inversión europeos.
- **BD:** `c:\desarrollo\fondos\db\fondos.sqlite` (SQLite)
- **Raíz:** `c:\desarrollo\fondos\`
- **Python:** 3.13, entorno conda `des`
- **Objetivo inversor:** Preservar patrimonio vs IPC + M3 (~6-7% nominal anual)
- **Horizonte:** 3-5 años | **Max drawdown tolerable:** 15%

---

## 2. ARQUITECTURA DE FASES

### P1 — Descubrimiento y tipificación (COMPLETADO)
- 3.107 fondos en `fund_master` con caracterización completa
- NAV mensual en `fund_nav_monthly`
- SRRI desde KIIDs en `fund_master.SRRI`

### P2 — Enriquecimiento cuantitativo (COMPLETADO — pipeline v10 PENDIENTE)
- **526.250 métricas** calculadas (pipeline v9)
- 35+ tipos de métrica por fondo
- Módulos: risk_metrics, consistency, deflation, macro_sensitivity, momentum, capture_ratios, persistence, currency_factor
- **Factores macro OLS:** 17 factores (ver sección 5)
- **MIN_OBS = 60** meses para betas OLS
- **VIF threshold = 10** para filtrar multicolinealidad

### P3 — Selección y cartera (OPERATIVO — mejoras en curso)
- Clasificador de régimen macro con 6 regímenes
- Scorer con pesos diferenciados por sub-cartera
- Portfolio builder con deduplicación
- Backtesting simple (sin look-ahead bias correction)
- Informe mensual Excel con 5 hojas

---

## 3. ESTADO DE FICHEROS EN PRODUCCIÓN

### proyecto2/src/loaders/
- `macro_loader.py` — **ACTUALIZADO** con DXY, Oro (PPICMM), M2 CN/JP/EU nivel, BoJ corregido
- `nav_loader.py` — sin cambios

### proyecto2/src/calculations/
- `macro_sensitivity.py` — **ACTUALIZADO** MIN_OBS=60, VIF=10, 17 factores
- `m2_global_builder.py` — **NUEVO** construye M2 Global desde 4 componentes
- `risk_metrics.py`, `consistency.py`, `deflation.py` — sin cambios
- `momentum.py`, `capture_ratios.py`, `persistence.py`, `currency_factor.py` — sin cambios

### proyecto2/src/analysis/
- `export_metrics.py` — **ACTUALIZADO** con Beta CLI EU/US, hojas 9_Persistencia y 10_Divisa

### proyecto3/src/
- `regime_classifier.py` — **ACTUALIZADO** con DXY, Oro, M2 Global
- `fund_scorer.py` — **ACTUALIZADO** pesos diferenciados por sub-cartera, normalización por naturaleza, filtros dinámicos
- `portfolio_builder.py` — sin cambios en esta sesión
- `backtesting.py` — sin cambios en esta sesión
- `monthly_report.py` — **ACTUALIZADO** 5 hojas, gráficos macro, SRRI doble, formato %

### shared/
- `config.py` — sin cambios

---

## 4. SERIES MACRO EN BD (series_macro)

| Indicador | Geography | Registros | Fuente | Notas |
|---|---|---|---|---|
| ipc_index | ES, EU, US, JP, CN | 258-313 | FRED/BCE | CN hasta nov-2023 |
| rate_deposit | EU | 313 | BCE | |
| rate_policy | EU, US, JP, CN | 288-314 | BCE/FRED | BoJ: IRSTCI01JPM156N hasta feb-2026 |
| m2_yoy | EU, US* | 313 | BCE/FRED | *US renombrado a m2_level |
| m3_yoy | EU | 313 | BCE | |
| m2_level | US, EU, CN, JP | 206-313 | FRED/BCE | CN hasta ago-2019, JP hasta dic-2017 |
| m2_global_yoy | GLOBAL | 289 | CALC | Construido desde 4 componentes |
| oil_wti | GLOBAL | 315 | FRED | Resampleado diario->mensual |
| copper | GLOBAL | 277 | FRED | |
| gold | GLOBAL | 314 | FRED | PPICMM (PPI Metales) como proxy |
| dxy | GLOBAL | 243 | FRED | DTWEXBGS desde 2006 |
| unemployment | US | 313 | FRED | |
| cli | EU, US, JP, CN, ES | 314 | FRED | EU=Alemania proxy |
| fx_usd_eur, fx_jpy_usd, fx_usd_gbp, fx_cny_usd | GLOBAL | 314 | FRED | |

**NOTA:** m2_level US contiene nivel absoluto en bn USD (fue m2_yoy por naming incorrecto, renombrado via UPDATE)

---

## 5. FACTORES MACRO EN REGRESIÓN OLS (17 factores)

```
d_rate_eu, m3_yoy, ipc_yoy_es, ipc_yoy_eu, ipc_yoy_us, ipc_yoy_jp, ipc_yoy_cn,
d_rate_us, d_rate_jp, d_rate_cn, oil_yoy, copper_yoy, cli_yoy_eu, cli_yoy_us,
dxy_yoy, gold_yoy, m2_global_yoy
```

**PENDIENTE AÑADIR (antes de pipeline v10):**
- `spread_hy` — FRED: BAMLH0A0HYM2
- `vix_yoy` — FRED: VIXCLS
- `term_spread` — FRED: T10Y2YM

---

## 6. RÉGIMEN ACTUAL Y CARTERA

**Régimen:** Shock_Energetico (marzo 2026)
- WTI YoY: +29.9% | IPC avg: 2.5% | Tipo BCE: 2.00% | CLI EU: 101.6

**Pesos:** Defensiva 55% / Equilibrada 35% / Dinámica 10%

**Cartera activa:** `shock_energia_2026Q1` en `portfolio_scenarios`

---

## 7. SCORING — ESTADO ACTUAL

### Pesos por sub-cartera (SUBPORTFOLIO_WEIGHTS):
| Métrica | Defensiva | Equilibrada | Dinámica |
|---|---|---|---|
| return_ann_real | 20% | 25% | 30% |
| sharpe | 25% | 20% | 15% |
| max_drawdown | 30% | 20% | 15% |
| alpha_persistence | 15% | 15% | 15% |
| capture_ratio | 5% | 10% | 15% |
| momentum_rank | 5% | 10% | 10% |

### Bonus de naturaleza (NATURE_PROFILE_BONUS):
- Defensiva: Monetario ×1.25, RF Corto ×1.10, RF Flexible ×1.00
- Equilibrada: Mixtos ×1.10
- Dinámica: RV ×1.10, Alternativo ×1.05

### Filtros duros por sub-cartera:
- Drawdown: Defensiva -20%, Equilibrada -30%, Dinámica -40%
- Retorno real mínimo: Defensiva -5%, Equilibrada -2%, Dinámica 0%

---

## 8. PARCHES IDENTIFICADOS (DEUDA TÉCNICA)

| ID | Descripción | Estado | Solución prevista |
|---|---|---|---|
| P01 | Bonus ×1.25 monetarios en Defensiva | Parche temporal | Sustituir por scoring basado en rentabilidad histórica por régimen |
| P02 | Filtro drawdown Defensiva -0.20 | Parche temporal | Derivar umbral empírico del análisis por régimen |
| P03 | Filtro retorno real Defensiva -5% | Parche temporal | Hacer dinámico por régimen |
| P04 | M2 CN extendido con YoY estimados PBoC desde sep-2019 | Parche datos | Sin fuente gratuita disponible. Documentar en informe |
| P05 | M2 JP extendido con YoY estimados BoJ desde ene-2018 | Parche datos | Sin fuente gratuita disponible. Documentar en informe |
| P06 | CLI EU = Alemania (DEULOLITOAASTSAM) como proxy EA | Parche datos | Serie EA19 OCDE discontinuada. Monitorizar |
| P07 | Tipo CN constante (INTDSRCNM193N) no refleja LPR real | Parche datos | Nota en informe. Sin alternativa gratuita |
| P08 | Oro = PPICMM (PPI Metales) no es precio spot oro | Parche datos | FRED retiró series spot. Monitorizar correlación |
| P09 | Backtesting con look-ahead bias | Parche modelo | Implementar ventanas rolling sin look-ahead |
| P10 | Fund_Currency vacío para muchos fondos | Parche P1 | P1 v2: extraer de KIIDs |

---

## 9. PRÓXIMOS PASOS PRIORIZADOS

### Inmediato (hoy):
1. Añadir spread_hy, vix, term_spread al macro_loader
2. Lanzar pipeline v10 (DELETE betas + run_pipeline)
3. Crear PENDIENTES.md en raíz del proyecto

### Corto plazo (esta semana):
4. **Análisis de rentabilidad por régimen** — NÚCLEO PENDIENTE
   - Cruzar fund_nav_monthly con clasificación histórica de regímenes
   - Calcular rentabilidad media por fondo y régimen
   - Identificar características predictivas por régimen
   - Derivar pesos de scoring empíricos (eliminar parches P01-P03)
5. Regenerar cartera con pipeline v10 y nuevo scorer
6. Regenerar informe mensual definitivo

### Medio plazo:
7. P1 v2: extraer comisiones y Fund_Currency de KIIDs
8. Backtesting riguroso sin look-ahead bias
9. Entry point como módulo para monthly_report

---

## 10. COMANDOS DE REFERENCIA

```cmd
# Pipeline P2
python -m proyecto2.src.pipeline.run_pipeline > logs\p2_pipeline_vXX.log 2>&1

# M2 Global builder
python proyecto2/src/calculations/m2_global_builder.py

# Scorer
python test_scorer.py

# Portfolio builder  
python test_portfolio.py

# Informe mensual
python test_report.py

# Loaders
python -m proyecto2.src.loaders.macro_loader --source fred --desde 2000-01
python -m proyecto2.src.loaders.macro_loader --source bce --desde 2000-01
```

---

## 11. DECISIONES ARQUITECTÓNICAS CLAVE

- [D01] Tres sub-carteras con pesos dinámicos según régimen macro
- [D02] Scoring normalizado por naturaleza de fondo dentro de cada sub-cartera
- [D03] MIN_OBS=60 para betas OLS (ratio obs/variables ~4)
- [D04] VIF threshold=10 para eliminar multicolinealidad en OLS
- [D05] M2 Global = suma US+EU+CN+JP en USD con extensiones estimadas para CN/JP
- [D06] DXY y Gold como factores macro independientes en regresión
- [D07] Objetivo mínimo = IPC_yoy + M3_yoy (~6% en contexto actual)
- [D08] Max 2 fondos por gestora entre todas las sub-carteras
- [D09] Deduplicación por primeros 3 tokens del nombre base
- [D10] rotation_costs con valores estándar hasta P1 v2

---

*Documento generado el 22/03/2026 para traspaso de contexto al nuevo chat del proyecto*
