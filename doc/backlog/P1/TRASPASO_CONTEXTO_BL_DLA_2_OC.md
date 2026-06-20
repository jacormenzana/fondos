# BL-DLA-2 — Traspaso de contexto (OC dual-extraction)

**Fecha:** 2026-06-02
**Workstream:** BL-DLA-2 — extracción de Ongoing Charges (OC) de KIID/KID PRIIPs
**Estado:** Extractores estabilizados. AGREE 89.2%. CONFLICT=3 (suelo de auditoría). Pendiente: subir ratio atacando ONLY_BANDS_X.

---

## 1. Objetivo y filosofía (de José, invariable)

Dos extractores **independientes** de OC que deben ser **fiables en TODOS los layouts conocidos**, para que la comparación AGREE/CONFLICT funcione como **procedimiento de auditoría**: detectar layouts nuevos/no soportados en el futuro. NO se trata de arbitrar conflictos; se trata de **arreglar el extractor que falla** en cada caso. Un CONFLICT residual = candidato a backlog de layout no soportado, no ruido a silenciar.

Regla metodológica crítica: **la inspección del PDF (tabla "Composición de los costes", normalmente pág. 3) es la ÚNICA verdad de campo.** Nunca usar BD como árbitro entre extractores. Nunca usar el acuerdo entre extractores como prueba de corrección sin muestra PDF (dos valores erróneos que coinciden ≠ validación).

Comunicación: **ejecutiva, concisa, técnica, español.** Sin restating, sin narrar intenciones, sin postambles. Leer ficheros completos antes de editar. AST-validar antes de entregar. Probar en PDFs reales antes de declarar "arreglado".

---

## 2. Arquitectura (2 ficheros, en `C:\desarrollo\fondos\scripts\diag\`)

- **`dla2_xband_prototype.py`** — extractor **bands-X** (reconstrucción por coordenadas de texto de la sección "Composición de los costes"). Para tablas SIN bordes (narrativas: HSBC, MS, etc.).
- **`dla2_dual_strategy_compare.py`** — **harness** + extractor **ruled** (`find_tables` strategy="lines", para tablas con bordes: DWS, etc.). Corre ambos extractores por PDF, clasifica AGREE/CONFLICT/ONLY_RULED/ONLY_BANDS_X/BOTH_FAIL, escribe CSV+log.

**Comando:**
```
python -m scripts.diag.dla2_dual_strategy_compare --pdf-dir "C:\data\fondos\kiid" --db "C:\desarrollo\fondos\db\fondos.sqlite"
```

OC = gestión + operación (componentes _OC_*). Tolerancia AGREE `_TOL=0.011` puntos-% (~1bp, absorbe redondeo 2 decimales).

---

## 3. VERSIONES ACTUALES (deben estar desplegadas juntas)

- **bands-X: v3.0** — `extract_from_open_pdf(pdf)` con merge cross-page; `extract_from_pdf` delega en ella.
- **ruled/harness: v2.0** — single-open por fondo; bands-X vía `extract_from_open_pdf` (NO el loop per-página antiguo).

Ambos ficheros validados en `/mnt/user-data/outputs/` (última entrega). md5 del bands-X de proyecto == outputs.

**Verificación de despliegue (CRÍTICA — ver §6):**
```
findstr /C:"v3." scripts\diag\dla2_xband_prototype.py        -> debe mostrar v3.0
findstr /C:"extract_from_open_pdf" scripts\diag\dla2_dual_strategy_compare.py  -> debe devolver líneas
```

---

## 4. RESULTADO ACTUAL (run 20260601_03, v3.0 + v2.0)

```
AGREE         : 2858  (89.2%)   <- mejor resultado histórico
ONLY_RULED    :  177  (5.5%)
BOTH_FAIL     :  121  (3.8%)
ONLY_BANDS_X  :   46  (1.4%)
CONFLICT      :    3  (0.1%)
Coincidencia BD: bands-X=953  ruled=944
```

Arco histórico CONFLICT: 180 -> 101 -> 3. AGREE: 73% -> 89.2%.

---

## 5. PRÓXIMO PASO (donde nos quedamos)

**Atacar ONLY_BANDS_X (46, todos LU, ruled falla).** 37/46 tienen `bx_vs_db=OK` (bands-X correcto, ruled no lee el layout). Dos sub-patrones:

### Sub-patrón MULTI (29 fondos) — TARGET PRINCIPAL
bands-X halla 2 componentes, ruled nada. ISINs consecutivos `LU01648...` = misma gestora/plantilla. Una muestra probablemente arregla casi todos.
Muestra pedida a José: **LU0164852419** (bx=2.40+0.46).
Lista completa (29):
```
LU0164852419,LU0164858028,LU0164865239,LU0164872284,LU0164880469,LU0164881194,
LU0164906959,LU0164939612,LU0165073775,LU0165074070,LU0165076018,LU0165129312,
LU0165289439,LU0196696453,LU0196696966,LU0210636733,LU0213961682,LU0213962813,
LU0551365645,LU0551366700,LU0551367260,LU0551369712,LU0579408591,LU0622164845,
LU0622164928,LU0708055370,LU1232087814,LU1339879162,LU2269308503
```

### Sub-patrón SINGLE (8 fondos) — EVALUAR, posiblemente DEJAR
bands-X halla 1 componente que coincide con BD (p.ej. 1.60, 1.80). Probablemente fondos genuinamente de OC único (sin línea de operación). Si es así, ruled abstenerse es ACEPTABLE (ONLY_BANDS_X-correcto); forzar ruled a emitir 1 componente arriesga que invente un 2º. Muestra pedida: **LU0033040782** (bx=1.60).
Lista (8):
```
LU0033040782,LU0049785446,LU0167295319,LU0167295749,LU0292585030,LU0292585626,
LU0658026603,LU0941351842
```

**Acción inmediata en la nueva sesión:** pedir a José los PDFs **LU0164852419** y **LU0033040782**. Diagnosticar por qué ruled falla. Si comparten plantilla de gestora, un fix dirigido en ruled mueve ~29 (multi) a AGREE. Riesgo: cualquier cambio en ruled afecta a 2858 AGREE -> validar contra TODOS los anchors (§7) antes de entregar.

---

## 6. CONFLICT residual (3) — suelo de auditoría

```
FR001400RZ04 : bx=0.00+0.19 (0.19) vs ruled=1.95+0.19 (2.14)  -> bands-X lee gestión=0.00 (debe 1.95). Fallo bands-X distinto. Necesita su PDF.
LU0687943661 : bx=1.76 vs ruled=direct:5.0  -> Pattern B: ruled coge el coste de ENTRADA 5%. bands-X correcto. Bug ruled.
LU0687944396 : bx=1.22 vs ruled=direct:5.0  -> idem Pattern B.
```
Los 2 Pattern B comparten mecanismo (un PDF arregla ambos). FR001400RZ04 es independiente. Cerrar para llegar a CONFLICT=0 en layouts conocidos. Pedir PDFs: **LU0687943661** y **FR001400RZ04**.

---

## 7. ANCHORS DE REGRESIÓN (validar SIEMPRE antes de entregar cualquier cambio)

Todos verificados PDF, con su OC correcto. Cualquier fix debe pasar 12/12:
```
LU2357626170=2.69 (cross-page: gestión pág2 2.11 + operación pág3 0.58)
ES0125756009=1.95 (1.80+0.15, ES borderless, dup-bug fixed)
ES0125757007=0.45 (0.37+0.08, ES, dup-bug fixed)
LU2278360750=1.82 (0.91+0.91 LEGÍTIMO, mgmt=op iguales reales — NO es bug)
LU1133289592=0.42 (0.29+0.13, valor-debajo)
LU1893894342=1.22 (0.95+0.27, ruled perf-fee barrier)
IE0002639551=0.16 (0.12+0.04, value-above)
FR0010251660=0.15 (0.12+0.03, cabecera accesorios centrada)
LU0329630130=1.61 (1.60+0.01, Vontobel etiqueta multilínea)
IE0005300136=0.95 (0.85+0.10, ruled block-collapse)
LU0173776047=1.86 (1.61+0.25, Nordea ruled text fallback)
DE0005152441=1.88 (1.45+0.43, DWS bordered)
```
PDFs disponibles en `/mnt/user-data/uploads/` durante esta sesión (re-subir en la nueva).

---

## 8. LÓGICA CLAVE DE LOS EXTRACTORES (para no romper al editar)

**bands-X (v3.0):**
- Asignación de valor OC por **CERCANÍA bidireccional con consumo único**: cada bloque _OC_* reclama la línea-valor MÁS CERCANA a su etiqueta (arriba o abajo), acotada por fronteras vecinas (bloque o cabecera de grupo), NO consumida por otro bloque. Esto resuelve value-above (ES/DWS/IE/FR) y value-below (LU1133289592) Y preserva X+X legítimo (LU2278360750) sin distinguir "igual" de "duplicado" a priori.
- Detección de cabecera de grupo contra full_text aunque `left` esté vacío (FR accesorios centrado x0>138).
- **Merge cross-page** en `extract_from_open_pdf`: si una página da 1 solo componente, intenta combinar con la siguiente y prefiere el resultado de 2 componentes.

**ruled (v2.0):**
- `_recover_oc_text`: fallback texto cuando find_tables degrada. **nearest-value acotado** por líneas-etiqueta de componente (no cruza a perf-fee/escenarios). Resolvió ES (op a +3 líneas).
- Barrera de cabecera de grupo en el rescue forward/backward (no coger perf-fee 20%).
- Recuperación por bloque para layout col0-colapsado (IE/PIMCO).

---

## 9. ANÁLISIS DE LOS OTROS BUCKETS

- **ONLY_RULED (177, 169 LU):** bands-X falla. PERO **149/177 son `ruled_vs_db=DIFF`** -> solapan el workstream de corrupción BD (BL-COST-5). Arreglar bands-X aquí produce AGREE en fondos cuyo BD ya se sabe erróneo. **Prioridad baja para ESTE workstream**; mejor input para BL-COST-5. NO forzar bands-X (riesgo regresión corpus).
- **BOTH_FAIL (121, LU/FR/IE/GB):** ninguno extrae. Probablemente PDFs imagen/escaneados/no-estándar. Rendimientos decrecientes. Caracterizar como fuera de alcance, no perseguir.

---

## 10. LECCIÓN OPERATIVA CRÍTICA (causó muchas iteraciones perdidas)

El bug que más costó: el **harness llamaba a bands-X por página** (`extract_cat2a_xband`) y rompía en la primera página con resultado — **NUNCA invocaba `extract_from_pdf`**, así que el merge cross-page era inalcanzable desde la comparación, por mucho que se redesplegara. Resultados "iguales que ayer" eran reales.

**Técnica de diagnóstico que funcionó:** comprobar la fila de **LU2357626170** en el CSV — `bx` debe leer `2.11+0.58` (v3.0 activo) y no `2.11`. Test one-liner aislado confirmó que el fichero funcionaba solo (`2.11+0.58`) mientras el harness daba `2.11`, aislando el bug de entry-point.

**Antes de cualquier run de 40 min, verificar que el harness usa la función correcta y las versiones en disco son las esperadas.** (Tarea ofrecida no construida: script self-check que valide path del harness + versiones de extractores ANTES del run.)

Otras notas Windows: QuickEdit Mode pausa el proceso al hacer click en la consola (Enter reanuda) — NO es bug, sin pérdida de datos. Desactivar en Propiedades de consola. Borrar `__pycache__` (`rmdir /S /Q scripts\diag\__pycache__`) y CSV/log viejos antes de re-run. `findstr` con path relativo desde donde se está (ficheros en `scripts\diag\`, no en raíz fondos).

---

## 11. CIERRE DEL WORKSTREAM (cuando CONFLICT=0 y ONLY_BANDS_X tratado)

Escribir `BL_DLA_2_DESIGN_DECISION.md`:
- Arquitectura dos-extractores (bands-X primario borderless, ruled complementario bordered).
- CONFLICT como métrica de auditoría permanente (objetivo ~0 en layouts conocidos; residual = candidato backlog layout no soportado).
- Corrupción BD `Ongoing_Charge_Recurrent` como workstream separado (BL-COST-5 / BL-DLA-2-BD-REMEDIATION): ~600 casos AGREE+DIFF (dos extractores concuerdan, BD difiere -> alta confianza BD erróneo).
- Plan integración "Estrategia 1.5" en `dla_table_serializer.py`.
- Check no-regresión contra los anchors (§7).
