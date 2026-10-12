# GOBIERNO_CONTROLES — catálogo de controles y matriz de decisión de los dos pilares

**Canónico para:** qué controles existen, de qué pilar son, quién los posee, qué criterio aplican y qué ocurre cuando no se cumplen;
qué estados y operaciones distingue el sistema de control de datos. **No** describe cómo se calculan las estadísticas
(`AUDITORIA_ESTADISTICA.md`) ni cómo se registran los tickets (`NORMAS_BACKLOG.md`).

Espejo legible por máquina: `shared/governance/controls.py` (solo datos). `tests/test_governance_controls.py` mantiene este
documento, el espejo y el código de las puertas (`GATE_*` de `proyecto3/src/scenario_lifecycle.py`, defectos de
`shared/statistical_audit/gating.py`) sincronizados: un control sin id, o un código de puerta sin control, rompe el test.

Plan y decisiones: `doc/backlog/GOBIERNO_DOS_PILARES_PLAN.md` (épica FND-0259; esta fase es FND-0260).

---

## 1. Modelo

| Pilar | Prefijo | Pregunta |
|---|---|---|
| Técnico | `T-` | ¿El artefacto que se promueve es el que se revisó? (código, versión, límites del mandato, evidencia de ejecución) |
| Datos | `D-` | ¿Los datos sobre los que se apoya son aptos? (auditoría estadística, frescura, excepciones) |
| Orquestación | `G-` | ¿Cómo se combinan? (completitud de la propia auditoría, selección de evidencia, override, remediación) |

El auditor estadístico **detecta**; nunca despliega, corrige ni promueve. Cada acción correctiva la hace su pipeline propietario y
se certifica con una auditoría posterior.

### 1.1 Operaciones

| Operación | Qué es |
|---|---|
| `CODE_DEPLOY` | Un launcher ejecuta código de producción desde el árbol de trabajo (ciclo P1/P2, construcción P3). |
| `PORTFOLIO_PROMOTION` | `scripts\launch\p3_promote_scenario.py` convierte un DRAFT en la cartera PRODUCTION. |

### 1.2 Estados

| Estado | Significado | Autoriza una promoción |
|---|---|---|
| `PASS` | todos los controles aplicables se cumplen | sí |
| `PASS_WITH_WARNINGS` | se cumplen; hay avisos (anomalías, plausibilidad, residuales aceptados) | sí, con los avisos listados |
| `FAIL` | un control bloqueante no se cumple | no |
| `INCOMPLETE` | no pudo establecerse (auditoría incompleta, almacén ilegible, evidencia ausente) | no |

Precedencia al combinar pilares: `INCOMPLETE` > `FAIL` > `PASS_WITH_WARNINGS` > `PASS`. «No pudo establecerse» es peor que «se
estableció y falló»; en ningún caso se trata como aprobado.

### 1.3 Consecuencias de un control

| Consecuencia | Efecto |
|---|---|
| `BLOCK` | rechaza la operación; no existe override |
| `BLOCK_UNLESS_OVERRIDE` | rechaza, salvo que la promoción lleve un motivo escrito **y** el ticket propietario (`--accept-alarms "motivo" --ap FND-nnnn`); queda en `ingestion_log` como `[OVERRIDE:<código>]` |
| `WARN` | se muestra, nunca rechaza |
| `RECORD` | se escribe en el registro de la ejecución |

Ningún control del pilar Técnico ni de Orquestación admite override.

---

## 2. Matriz de decisión (estado combinado × operación)

El estado combinado es el más severo de los dos pilares (§1.2).

| Estado combinado | `CODE_DEPLOY` | `PORTFOLIO_PROMOTION` |
|---|---|---|
| `PASS` | autorizado | autorizado |
| `PASS_WITH_WARNINGS` | autorizado | autorizado, avisos listados |
| `FAIL` | autorizado con aviso (se registra) | **rechazado** |
| `INCOMPLETE` | autorizado con aviso (se registra) | **rechazado** |

Decisión D3 (2026-10-11): el estado del código bloquea **solo** en la promoción; los launchers de ciclo lo registran y avisan. El
veredicto de la auditoría estadística nunca detiene un ciclo (las auditorías dentro de un launcher son diagnóstico).

---

## 3. Catálogo

`Estado` = `enforced` (existe código que lo aplica; `Fuente` lo nombra), `dormant:<INTERRUPTOR>` (el código existe tras un
interruptor de `shared/config.py` aún apagado; al encenderlo el control pasa a `enforced` en el mismo commit, el test lo exige) o
`planned:FND-nnnn` (lo posee ese ticket; aún sin código). `Fuente` `CODE:<módulo.función>` nombra la función que aplica un control
sin código de puerta propio.
`Consecuencia` es la de `PORTFOLIO_PROMOTION`; en `CODE_DEPLOY` solo se evalúan los controles con consecuencia propia indicada.

### 3.1 Pilar Técnico

| Id | Criterio | Consecuencia | Fuente | Estado |
|---|---|---|---|---|
| `T-STATUS` | el escenario existe, el ciclo de vida está instalado y su estado es DRAFT | `BLOCK` | `GATE:STATUS` | enforced |
| `T-WEIGHTS` | hay pesos y Σ pesos + liquidez declarada = 1.0000 (§4: tolerancia 5·10⁻⁵ cuando decide el orquestador, 10⁻³ en la puerta heredada) | `BLOCK` | `GATE:WEIGHTS` | enforced |
| `T-CASH` | liquidez estructural declarada ≤ `MAX_PORTFOLIO_CASH_WEIGHT` (10 %) | `BLOCK` | `GATE:CASH` | enforced |
| `T-FX` | exposición neta no-EUR sin cobertura ≤ `MAX_NON_EUR_UNHEDGED_WEIGHT` (30 %) | `BLOCK` | `GATE:FX` | enforced |
| `T-CODE` | `HEAD == origin/master` (según el último *fetch*) y ningún fichero rastreado modificado (los no rastreados se ignoran, D1; `run_identity.code_integrity`, el mismo predicado que usará el *push* controlado de FND-0273); en `CODE_DEPLOY`: `WARN` (el *preflight* del ciclo, D3) | `BLOCK` | `GATE:CODE` | dormant:CODE_INTEGRITY_GATE_ENABLED |
| `T-VERSION` | la cabecera `control.audit_run` de la auditoría de evidencia lleva sha de git, hash de configuración (`*_ENABLED` y `MAX_*`), versión del catálogo y manifiesto de datos (`CALC_VERSION`, corte, versiones de algoritmo); sin ellos no es evidencia; en `CODE_DEPLOY`: `RECORD` | `BLOCK` | `GATE:MANIFEST` | dormant:GOVERNANCE_ORCHESTRATOR_ENABLED |
| `T-EVIDENCE` | el registro de la promoción (`ingestion_log`, misma transacción que la promoción) cita la auditoría, el hash de configuración, las concesiones de excepción y la línea de veredicto de dos pilares (estado de cada pilar y decisión); en `CODE_DEPLOY`: `RECORD` | `BLOCK` | `CODE:shared.governance.orchestrator.verdict_line` | enforced |

### 3.2 Pilar Datos

| Id | Criterio | Consecuencia | Fuente | Estado |
|---|---|---|---|---|
| `D-FRESHNESS` | la puerta de frescura de P3 (`P3_FRESHNESS_MAX_AGE_DAYS`) no reporta ninguna entrada desactualizada | `BLOCK` | `GATE:FRESHNESS` | enforced |
| `D-AUDIT-STALE` | existe una auditoría `p2_metrics` persistida y es posterior a la construcción del DRAFT | `BLOCK` | `GATE:AUDIT_STALE` | enforced |
| `D-DATA-SCENARIO` | ningún defecto duro abierto toca un fondo de la cartera (ni uno sin ISIN atribuible) | `BLOCK_UNLESS_OVERRIDE` | `GATE:DATA_SCENARIO` | enforced |
| `D-RESIDUAL-SCENARIO` | ningún residual aceptado sobre un fondo de la cartera: se tolera en el universo, no en una cartera | `BLOCK_UNLESS_OVERRIDE` | `GATE:RESIDUAL_SCENARIO` | enforced |
| `D-COVERAGE` | todos los fondos de la cartera fueron auditados (ninguno fuera de los bloques transversales ni fuera de la población); cobertura conocida | `BLOCK_UNLESS_OVERRIDE` | `GATE:DATA_COVERAGE` | enforced |
| `D-DATA-UNIVERSE` | un defecto duro abierto fuera de la cartera exige residual aceptado vigente (`accept_audit_residual.py`: ticket + caducidad); sin override en la promoción | `BLOCK` | `GATE:DATA_UNIVERSE` | enforced |
| `D-INVARIANT` | clase de regla `HARD_INVARIANT` (valor fuera de cota dura, identidad entre columnas violada) | `BLOCK` | `RULE_CLASS:HARD_INVARIANT` | enforced |
| `D-IDENTITY` | bloque `BLOCK2`: identidades escalar-vs-serie temporal y de deflactación | `BLOCK` | `BLOCK:BLOCK2` | enforced |
| `D-TS-DUP` | `TIMESERIES_DUPLICATE`: serie append-only corrupta | `BLOCK` | `RULE_ID:TIMESERIES_DUPLICATE` | enforced |
| `D-PLAUSIBILITY` | clase `PLAUSIBILITY`: valor fuera de rango plausible, no imposible | `WARN` | `RULE_CLASS:PLAUSIBILITY` | enforced |
| `D-OUTLIER` | clase `STATISTICAL_ANOMALY` (MAD-Z, IQR): elementos de revisión, nunca defectos | `WARN` | `RULE_CLASS:STATISTICAL_ANOMALY` | enforced |
| `D-RESIDUAL` | defecto cuyos infractores están todos cubiertos por un residual aceptado vigente: se reporta rebajado, nunca se oculta | `WARN` | `RULE_CLASS:ACCEPTED_RESIDUAL` | enforced |
| `D-EXCEPTIONS` | todo residual aceptado es un `GRANT` / `RENEW` más un `APPROVE` separado en `control.audit_exception_event` (D6), revocado con causa; la puerta rechaza si ese historial no se puede leer o `control.audit_accepted_finding` no coincide con él (§5) | `BLOCK` | `GATE:EXCEPTIONS` | enforced |

`D-INVARIANT`, `D-IDENTITY` y `D-TS-DUP` son los tres criterios de `gating.is_blocking`; `D-DATA-SCENARIO`, `D-RESIDUAL-SCENARIO`
y `D-DATA-UNIVERSE` reparten esos defectos por alcance (sobre la cartera / solo en el universo).

### 3.3 Orquestación

| Id | Criterio | Consecuencia | Fuente | Estado |
|---|---|---|---|---|
| `G-OVERRIDE-AP` | `--accept-alarms` exige motivo y `--ap FND-nnnn` existente en `gestion.backlog`; sin ello el override no se aplica | `BLOCK` | `GATE:OVERRIDE_AP` | enforced |
| `G-COMPLETENESS` | cada comprobación obligatoria produjo resultado; una entrada `MISSING` (o una obligatoria `EMPTY` en corrida FULL) hace la corrida `INCOMPLETE`, código de salida 2, nunca un pase; el estado se escribe en `control.audit_run` con las filas de la corrida, en una sola transacción (§7) | `BLOCK` | `CODE:shared.statistical_audit.completeness.run_status` | enforced |
| `G-EVIDENCE-RUN` | la corrida que lee la puerta es la última cabecera de `control.audit_run` de ámbito FULL y estado distinto de `INCOMPLETE`, posterior al DRAFT; una corrida FAIL cuenta (D4); una `_sample` nunca (siempre activo: `gating.SAMPLE_RUN_SUFFIX`) | `BLOCK` | `CODE:shared.statistical_audit.gating.select_evidence_run` | enforced |
| `G-COST` | los defectos duros abiertos de la auditoría de evidencia de `cost_attributes` sobre fondos de la cartera rechazan como los de `p2_metrics` (D8: `scripts/audit/cost_gate_replay.py` sobre las corridas pasadas, un ciclo en sombra, luego el interruptor) | `BLOCK` | `GATE:COST` | dormant:COST_DOMAIN_GATE_ENABLED |
| `G-REMEDIATION` | un ticket vinculado a un hallazgo se cierra solo con una re-auditoría completa que cubra los ISIN con cero infractores | `WARN` | — | planned:FND-0264 |

---

## 4. Política de precisión

- Los pesos se construyen con **4 decimales**; la suma de pesos (más la liquidez declarada) es **1.0000** exacta tras el reparto por
  mayor resto (`portfolio_engine`). El constructor no cambia.
- Tolerancia de la puerta `T-WEIGHTS` en la promoción: **5·10⁻⁵** (media unidad del cuarto decimal; decisión D2,
  `scenario_lifecycle.WEIGHT_TOLERANCE`), aplicada por el orquestador de dos pilares (§8); la puerta heredada conserva 10⁻³ mientras
  `GOVERNANCE_ORCHESTRATOR_ENABLED` esté apagado.
- Los límites del mandato (`MAX_NON_EUR_UNHEDGED_WEIGHT`, `MAX_PORTFOLIO_CASH_WEIGHT`) cambian solo en `shared/config.py` por un
  commit revisado; nunca en el momento de la promoción.

## 5. Política de excepciones (FND-0263)

Una excepción acotada (residual aceptado) lleva ticket y caducidad ≤ 90 días, y es una **decisión registrada**, no una fila editable:

- **Historial append-only:** `control.audit_exception_event` (un disparador rechaza `UPDATE`, `DELETE` y `TRUNCATE`). Tipos:
  `GRANT` (ISIN, ticket, motivo, caducidad), `RENEW` (igual, como renovación), `APPROVE` (de un `GRANT`/`RENEW` pendiente) y
  `REVOKE` (de una concesión, total o de un subconjunto de ISIN, con causa `OWNER`, `AP_CLOSED` o `RESOLVED` y motivo). La caducidad
  es implícita por fecha.
- **Decisión D6:** dos pasos, `GRANT` y un `APPROVE` explícito y separado (otra orden: `accept_audit_residual.py --approve <id>`),
  ambos registrados; la misma persona puede aprobar y no hay colchón de tiempo. Una concesión no cubre nada hasta aprobarse; una
  concesión ya caducada no se aprueba (se concede de nuevo).
- **Proyección:** `control.audit_accepted_finding` sigue siendo lo que leen la auditoría, la puerta, el *scorer* y los KPI; es el
  estado actual del historial (`accepted_residuals.fold_events`) y el escritor la refresca en la misma transacción que el evento.
- **Puerta (`D-EXCEPTIONS`, `AUDIT_EXCEPTION_EVENTS_ENABLED` encendido desde 2026-10-12):** un historial ilegible o una proyección que no coincide con él
  bloquea la promoción (`[GATE:EXCEPTIONS]`, sin override); nunca se lee como «no hay residuales».
- **Reconstrucción:** la nota de la promoción cita la auditoría (su cabecera `control.audit_run` lleva código, catálogo, configuración y
  manifiesto de datos), el hash de configuración y los `event_id` de las concesiones en que se apoyó.

Un residual nunca cubre a un fondo seleccionado en una cartera (`D-RESIDUAL-SCENARIO`).

## 6. Cómo cambiar este catálogo

1. Un control nuevo o retirado se cambia **a la vez** en este documento y en `shared/governance/controls.py`; el test rechaza
   cualquier divergencia.
2. Una puerta nueva de promoción (`GATE_*`) o una clase de defecto bloqueante nueva exige su control en el catálogo antes de
   fusionarse.
3. Un control `planned:FND-nnnn` pasa a `enforced` cuando su ticket entrega el código; `Fuente` pasa a nombrarlo. Si el código
   llega tras un interruptor apagado, pasa a `dormant:<INTERRUPTOR>` y a `enforced` en el commit que lo enciende.

## 7. Completitud de la auditoría (`G-COMPLETENESS`, `G-EVIDENCE-RUN`)

Contrato y cabecera de corrida: `AUDITORIA_ESTADISTICA.md` §6.4. Resumen: cada comprobación omitida se clasifica (`MISSING` /
`EMPTY` / `NOT_APPLICABLE`); una corrida con una entrada obligatoria ausente es `INCOMPLETE`; su estado se guarda en
`control.audit_run` en la misma transacción que sus filas, sin recuentos (los de `control.cycle_audit_run` se unen por `run_id`).
Con `AUDIT_COMPLETENESS_CONTRACT_ENABLED` encendido (desde 2026-10-12), `INCOMPLETE` sale con código 2 y la promoción solo acepta como evidencia una
cabecera FULL no `INCOMPLETE` (D4).

## 8. Orquestador de dos pilares (FND-0265)

`shared/governance/orchestrator.py::authorise_promotion` compone el veredicto de `PORTFOLIO_PROMOTION` sin duplicar ninguna puerta:
toma las líneas `[GATE:<código>]` de `scenario_lifecycle.evaluate_promotion` (con la tolerancia D2) y añade las comprobaciones del pilar
Técnico que no dependen de la cartera — `GATE:CODE` (D1, `run_identity.code_integrity`) y `GATE:MANIFEST` (cabecera de la auditoría de
evidencia) — y el dominio de costes, `GATE:COST` (D8). Cada línea va al pilar del control cuya fuente es su código; el estado de cada pilar
es `INCOMPLETE` si alguna línea es de evidencia no establecida (`AUDIT_STALE`, `EXCEPTIONS`, `MANIFEST`), `FAIL` si hay otra línea,
`PASS_WITH_WARNINGS` con avisos y `PASS` en otro caso; la decisión sale de la matriz (§2). Una comprobación tras un interruptor apagado es
un aviso (sombra), nunca un bloqueo.

`p3_promote_scenario.py` calcula y muestra siempre ese veredicto y lo escribe en la nota de la promoción (`T-EVIDENCE`):

| Interruptor (`shared/config.py`) | Encendido | Apagado |
|---|---|---|
| `GOVERNANCE_ORCHESTRATOR_ENABLED` | el veredicto **decide** (tolerancia 5·10⁻⁵, `GATE:MANIFEST`) | sombra: decide la puerta heredada |
| `CODE_INTEGRITY_GATE_ENABLED` | `GATE:CODE` bloquea (haga *push* antes de promover) | aviso |
| `COST_DOMAIN_GATE_ENABLED` | `GATE:COST` bloquea | aviso; antes de encender: `scripts/audit/cost_gate_replay.py` y un ciclo en sombra |

Los dos últimos actúan dentro del orquestador. En `CODE_DEPLOY` el estado del código solo se avisa (`p1p2_state.py preflight`, D3).
