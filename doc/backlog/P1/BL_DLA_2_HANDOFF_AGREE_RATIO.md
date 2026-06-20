# BL-DLA-2 — Handoff: continuar mejorando ratio AGREE

**Fecha:** 2026-06-04
**Owner:** Jose
**Objetivo de esta línea de trabajo:** subir el ratio AGREE del comparador dual de extracción de OC (Ongoing Charge) sobre los 3.205 KIID/KID del corpus.
**Modelo:** Sonnet para implementación contra spec cerrada; Opus solo para decisiones de arquitectura (p.ej. la remediación BD, que NO es esta línea).

---

## 0. Cómo trabaja esta línea (reglas que el nuevo chat DEBE respetar)

- **El PDF (tabla "Composición de los costes") es el ÚNICO árbitro de verdad.** Nunca asumir que un extractor está bien porque extrajo algo, ni porque coincide con el otro, ni porque coincide con BD. BD es poco fiable (~600+ fondos corruptos). Acuerdo entre extractores ≠ validación.
- **Principio #1 (causa raíz, no síntoma):** los mejores fixes han sido de *clase* (un cambio que arregla un patrón entero), no parches por-fondo.
- **Disciplina de entrega:** leer el fichero completo antes de editar; `str_replace` quirúrgico; `python -c "import ast; ast.parse(...)"`; validar SIEMPRE los **12 anchors + targets previos** sobre PDFs reales antes de declarar entrega.
- **§9 (rendimientos decrecientes):** caracterizar y dejar como residual de backlog los clusters de bajo valor (p.ej. PDFs escaneados/imagen → OCR fuera de alcance). El residual CONFLICT visible es la métrica de auditoría funcionando, no ruido a eliminar a toda costa.
- **§10 (trampa de despliegue) — CRÍTICA, ha recurrido varias veces:** la consola/log/cabecera-de-versión pueden mentir. El ÚNICO modo de saber qué versión corrió es mirar los valores reales del CSV. Antes de cada run: `rmdir /S /Q scripts\diag\__pycache__`, borrar CSV/log viejos, `findstr "v3.2" ...xband...`, `findstr "v2.3" ...compare...`, correr, y verificar en el nuevo CSV una fila-prueba (ver §4).
- **Este chat (y el siguiente) SOLO puede leer ficheros adjuntos AL CHAT** (no hay montaje a `C:\desarrollo\fondos\`). Subir al *project folder* NO llega al asistente. Hay que **adjuntar en el mensaje del chat**: el CSV/log del último run y los PDFs que se pidan.

---

## 1. Estado actual (run validado v3.2/v2.3, fichero `dla2_dual_strategy_compare_20260604_00.*`)

```
AGREE         : 3038  (94.8%)
ONLY_RULED    :   32  (1.0%)
BOTH_FAIL     :  123  (3.8%)
ONLY_BANDS_X  :   10  (0.3%)
CONFLICT      :    2  (0.1%)
n = 3205
```

Evolución del ratio AGREE en la sesión: 90.1% → **94.8%** (+150 AGREE).

Deltas vs run previo (v2.2, AGREE 2888):
- ONLY_RULED 177 → 32 (**−145**) — fix NFC (ver §3) convirtió el bucket dominado por Fidelity.
- CONFLICT 9 → 2 (**−7**) — fixes E/F (ver §3) resolvieron familias AXA/BNPP y BlackRock + hermanos.
- BOTH_FAIL +2, ONLY_BANDS_X +0 (ruido despreciable).

---

## 2. Ficheros de producción (versión vigente, validada)

Ubicación real: `C:\desarrollo\fondos\scripts\diag\`

- **`dla2_xband_prototype.py` v3.2** — md5 `0b8102dfe65f238b7e802e6e58ba76ab`
  Extractor bands-X (reconstrucción por coordenadas, tablas SIN bordes). Entry: `extract_from_open_pdf(pdf)` (incluye merge cross-page). Núcleo: `extract_cat2a_xband(page)`.
- **`dla2_dual_strategy_compare.py` v2.3** — md5 `96dc0ade8fc16061929368c22c131233`
  Harness + extractor ruled (`find_tables`). `extract_ruled_from_pdf(pdf, max_pages=3)` con fallback de texto `_recover_oc_text`. `classify(bx,rl)` → AGREE/CONFLICT/ONLY_RULED/ONLY_BANDS_X/BOTH_FAIL. Tolerancia `_TOL=0.011` pp.

Comando de run:
```
python -m scripts.diag.dla2_dual_strategy_compare --pdf-dir "C:\data\fondos\kiid" --db "C:\desarrollo\fondos\db\fondos.sqlite"
```
Salidas: `dla2_dual_strategy_compare.csv` + `.log`. Columnas CSV: ISIN, bands_x_oc, bands_x_breakdown, ruled_oc, ruled_breakdown, ruled_ncomp, arbitration, db_oc, db_oc_pct, bx_vs_db, ruled_vs_db.

---

## 3. Fixes ya aplicados esta sesión (NO re-hacer)

- **Fix NFC (v3.2, bands-X) — el de mayor impacto (+145 ONLY_RULED→AGREE).**
  Causa raíz: algunas gestoras (FIL/Fidelity y otras) emiten acentos DESCOMPUESTOS (NFD): "ó" = `o` + U+0301 (combining acute), no el precompuesto U+00F3. Las clases `[oó]` de TODOS los patrones (sección, etiquetas) fallaban → "Composición" no abría → bands-X devolvía `{}`.
  Fix: `unicodedata.normalize("NFC", w["text"])` por word en `extract_cat2a_xband` (import `unicodedata` añadido). Arregla la clase entera (composición/gestión/operación) de una vez.
  Espejo en ruled `_recover_oc_text`: NFC sobre `extract_text()` antes de `split`.
- **Fix E (v2.2/2.3, ruled `_R_TXT_GEST`):** aceptar "Gastos de gestión" + variante con coma; **anclado a inicio de línea** (`^\s*`) para que la prosa "objetivo de gestión" no case. (BNPP/AXA.)
- **Fix F (v2.2/2.3, ruled `_R_TXT_OPER`):** etiquetas de operación/transacción **ancladas a inicio** (`^\s*(?:costes?|co[uû]ts?|frais)\s*de\s*…`) para que la prosa "previsiones de costes de transacción" no robe un % de escenario. (BlackRock LU0278718100 pasaba de 24.7 a 2.20.)
- **Fixes previos ya en v3.1/v2.1** (no tocar): A (\s+→\s* texto narrativo HSBC), B (is_ocdir excluye entrada/salida, GAMCO), C (op += transacción + FR), D (bands-X pre-consume de bloques no-OC).

---

## 4. Prueba de despliegue (one-liner anti-§10)

En cualquier CSV nuevo, **LU0115759606 debe mostrar `bands_x_oc=2.8` y `arbitration=AGREE`.**
Si sale en blanco / ONLY_RULED → el bands-X parcheado NO corrió (pycache/fichero stale), aunque la cabecera diga v3.2.

---

## 5. PRÓXIMO PASO (lever de mayor valor) — ruled `[0.0+X]`: gestión leída como 0.0

**Qué es:** dentro de ONLY_RULED (32) hay **26 fondos** donde ruled devuelve `ruled_breakdown = "0.0+X"` — es decir, captura **gestión = 0.0** y solo el componente de operación. El OC resultante es erróneo-bajo (0.08, 0.89…) mientras BD reporta ~2.5–3.7. bands-X está en silencio en estos.

**Por qué es alto valor:** no es solo recuperar AGREE; estos 26 producen **datos OC actualmente MAL** (gestión perdida). Arreglarlos corrige registros además de subir AGREE.

**Hipótesis (NO confirmada, requiere PDFs):** es el espejo del bug NFC en el **camino de TABLA de ruled** (`extract_ruled_from_pdf` vía `find_tables`/`extract_tables`): las celdas con `Comisiones de gesti**ó**n` en NFD no casan `_R_GESTION` (`gesti[oó]n`) → gestión no se detecta → 0.0. Es la "parity gap" que se señaló y se dejó sin parchear por falta de caso fallido; ahora hay casos fallidos.
**OJO:** confirmar contra el PDF que la gestión real NO es 0 y por qué se lee 0. No asumir que ruled=0 es el fallo y BD~3 la verdad (ni al revés). PDF manda.

**Los 26 ISIN (`0.0+X`, db):**
```
LU0133085943 [0.0+0.08] db=2.8     LU1438969195 [0.0+0.08] db=2.8
LU0133096635 [0.0+0.53] db=3.4     LU1438969351 [0.0+0.89] db=3.7
LU0143551892 [0.0+0.89] db=3.7     LU1438969518 [0.0+0.31] db=3.1
LU0174119429 [0.0+0.18] db=2.9     LU1453466739 [0.0+0.44] db=1.4
LU0230817339 [0.0+0.24] db=3.0     LU1493953001 [0.0+0.32] db=3.1
LU0596127604 [0.0+0.14] db=2.5     LU1582221328 [0.0+0.53] db=3.4
LU0918140210 [0.0+0.53] db=3.4     LU1602119973 [0.0+0.45] db=3.4
LU1044871579 [0.0+0.48] db=3.3     LU1683326703 [0.0+0.26] db=3.1
LU1047868630 [0.0+0.53] db=1.4     LU1737526100 [0.0+0.53] db=1.2
LU1244140163 [0.0+0.4]  db=1.3     LU1737526365 [0.0+0.53] db=1.2
LU1382644323 [0.0+0.4]  db=1.3     LU1756323520 [0.0+0.24] db=3.1
                                    LU1777971893 [0.0+0.18] db=2.9
                                    LU1785826154 [0.0+0.45] db=1.2
                                    LU1956839481 [0.0+0.53] db=1.3
                                    LU1956839564 [0.0+0.53] db=1.3
```
(`0.0+0.53` se repite 7×, `0.0+0.89` 2× → pocos templates compartidos; cluster LU1438969× = 3.)

**PDFs a pedir a Jose (adjuntar AL CHAT), 3 que cubren variedad:**
1. **LU0143551892** (`0.0+0.89`, db 3.7) — mayor gap de gestión, señal más clara.
2. **LU0133085943** (`0.0+0.08`, db 2.8) — op pequeña, posible subtipo distinto.
3. **LU1438969351** (`0.0+0.89`, db 3.7) — representante del cluster LU1438.

**Plan:** confirmar gestión real en cada PDF → diagnosticar por qué ruled la lee 0.0 (probable NFC en celdas de tabla; si es eso, normalizar NFC las celdas en `extract_ruled_from_pdf`, espejo de v3.2) → validar 12 anchors + targets Fidelity/AXA/BlackRock → entregar.
**Proyección si es la clase NFC:** ONLY_RULED 32 → ~6, AGREE → ~3060 (~95.5%), **+26 registros OC corregidos.**

---

## 6. Resto de buckets (caracterizados; menor prioridad)

- **ONLY_RULED restantes (4):** `direct:X` FR con `ruled_vs_db=OK` (FR0013534898=0.85, FR0013535077=0.95, +2). Aparentan OC único genuino, bands-X abstiene correctamente. **Probable LEAVE** (como los UBS singles); confirmar con 1 PDF si se quiere cerrar.
- **CONFLICT (2):**
  - **FR001400I0X7** — bx=1.26[0.73+0.53] vs ruled=3.28[2.75+0.53], db=2.5. Hermano de FR001400RZ04 (que SÍ quedó AGREE 2.14). Desacuerdo en gestión (0.73 vs 2.75). **Sin resolver, requiere PDF** para decidir cuál es correcto. (Corrección honesta: no todos los hermanos FR001400 convirtieron.)
  - **FR0000447823** (AXA Trésor, monetario VNAV) — bx=0.02 vs ruled=0.08[0.06+0.02 CORRECTO], db=1.0. Truth=0.08. ruled acierta; bands-X acota la sección en p2 (layout monetario de texto disperso) y no llega a la tabla de p3. **Caracterizado §9 → backlog "BL-DLA-2 bands-X section-bounding en KID monetarios de texto disperso".** 1 fondo inmaterial; no perseguir.
- **BOTH_FAIL (123):** 93 LU / 14 IE / 13 FR / 3 GB; clusters pequeños (LU1548×6, LU0256×5, FR0011×4, LU1997×4, LU2286×4). Imagen/escaneado/sin texto → **OCR, fuera de alcance §9.** No es el próximo lever.

---

## 7. 12 ANCHORS (deben quedar AGREE@truth en CADA entrega) + targets previos

Anchors (ISIN → OC truth pp): LU2357626170=2.69, ES0125756009=1.95, ES0125757007=0.45, LU2278360750=1.82, LU1133289592=0.42, LU1893894342=1.22, IE0002639551=0.16, FR0010251660=0.15, LU0329630130=1.61, IE0005300136=0.95, LU0173776047=1.86, DE0005152441=1.88.

Targets sesión (deben mantenerse): LU0164852419=2.86, LU0687943661=1.84, LU0687944396=1.30, FR001400RZ04=2.14, FR0012903276=1.35, LU0278718100=2.20; LU0033040782=1.60 (correctamente ONLY_BANDS_X, op=0); Fidelity: LU0115759606=2.8, LU0261946445=2.5, LU0251127410=2.1, LU0114720955=2.3, LU0048573561=2.1.

Los PDFs de todos estos estuvieron en `/mnt/user-data/uploads/` (re-pedir a Jose si se necesitan para re-validar).

---

## 8. Snippet de medición (para arrancar el nuevo chat con el CSV adjunto)

```python
import csv
from collections import Counter
rows=list(csv.DictReader(open('<CSV>',encoding='utf-8')))
print(Counter(r['arbitration'] for r in rows))
# prueba v3.2:
r=next(x for x in rows if x['ISIN']=='LU0115759606'); print(r['bands_x_oc'], r['arbitration'])  # 2.8 AGREE
# lever:
print([r['ISIN'] for r in rows if r['arbitration']=='ONLY_RULED' and r['ruled_breakdown'].startswith('0.0+')])
```

---

## 9. Fuera de alcance de ESTA línea (no mezclar)

- **Remediación BD (BL-COST-5, decisión Opus):** verificado este corpus — de los 1940 AGREE-pero-DIFF-vs-BD: **α=508 funds BD==componente gestión exacto** (479 LU+29 IE, BD guardó TER sin operación); **γ′=167 funds BD==componente operación exacto** (106 LU,44 IE,16 ES incl. anchors DWS-España); β=800 BD-sobreestima difuso (sin constante única). → **675 funds (α+γ′) = BD-mal mecánicamente confirmado, regla determinista BD := OC del extractor.** Además 2 mislabels x100: LU2171252351 (db_oc_pct=270), LU2178498619 (234). Esto es política de sobre-escritura de BD (R-2/R-4 COALESCE, universo completo vs ciclo en `sqlite_writer.publish_fund()`) → sesión Opus dedicada, NO esta línea.
- **OCR para BOTH_FAIL** (imagen/escaneado) → workstream separado.
- Al cerrar (CONFLICT en suelo, ONLY_RULED agotado): escribir `BL_DLA_2_DESIGN_DECISION.md` (arquitectura dos-niveles bands-X primario + ruled complementario, arbitraje por calidad, validación por familia-layout, no-regresión).
