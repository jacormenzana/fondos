# NORMAS BACKLOG — Puntos de acción y relaciones entre ellos

**Propósito:** un único criterio para registrar, relacionar y cerrar puntos de acción (`gestion.backlog`), de modo que una
corrección diseñada para un ticket tenga en cuenta los demás tickets pendientes que toca (caso FND-0243 ↔ FND-0246: ambos leen la
cabecera del KIID y se diseñaron por separado). **Implementación:** `shared/backlog_client.py` (único camino a la BD `gestion`),
`shared/backlog_overlap.py` (similitud de texto), `scripts/audit/backlog_links.py` (descubrimiento, revisión, aplicación),
`scripts/audit/backlog_links_calibration.py` (puerta de calibración). **Almacén:** `db/pg_backlog/00_schema.sql` +
`db/pg_backlog/20_links.sql`. **Lectura obligatoria** antes de abrir, empezar o cerrar un ticket y antes de tocar quien reporta
incidencias automáticas.

---

## §1. Principio

Un ticket no vive solo. Antes de **abrir** uno se comprueba si ya existe; antes de **empezar** uno se listan los tickets a los que
afecta, por los que está afectado y cuyo alcance puede absorber; al **cerrar** se dice qué queda. Las relaciones se registran
como **eventos append-only** decididos por una persona, no como texto libre. Lo que se pueda calcular (señales, candidatas,
grupos) lo calcula la herramienta; la decisión es humana.

## §2. Datos, dominios y máquina de estados

Base `gestion`, esquema `backlog`, mismo servidor que P1/P2/P3 pero **otra base** que `fondos`. Conexión: `FONDOS_BACKLOG_PG_DSN`
(distinta de `FONDOS_PG_DSN`; `shared.config` carga el `.env` del repo). Tablas: `backlog.BACKLOG` (una fila por ticket),
`backlog.BACKLOG_LOGS` (historial append-only, FK `ON DELETE CASCADE`), `backlog.backlog_link_event` (§3).

Dominios categóricos, **todos con `CHECK` en BD** — nunca inventar un valor fuera de estas listas:

| Columna | Valores |
|---|---|
| `nature` | `BUG` `REQ` `CHG` `OPT` `INV` `DOC` `TSK` |
| `object_type` | `TAB` `VW` `SP` `UDF` `TVF` `JOB` `SCHEMA` `N/A` |
| `priority` | `HIGH` `MEDIUM` `LOW` |
| `status` | `OPEN` `TODO` `IN_PROGRESS` `READY_FOR_DEPLOY` `BLOCKED` `DEFERRED` `CLOSED` |
| `solution_type` | `CODE_FIX` `DATA_FIX` `WORKAROUND` `REJECTED` `DUPLICATED` `NULL` |

- **Sesión:** confirmar al empezar `[PROYECTO]_[YYYYMMDD]_[TIPO_SESION]_[TEMA_CORTO]` (`TIPO_SESION` ∈ `WKSH` `DEV` `REVB`
  `SYNC` `ANL`).
- **Id:** `FND-NNNN`, acuñado por `backlog.sp_open_ticket` (la forma manual heredada con `nextval` se retiró el 2026-10-28); nunca inventado.
- **Alta:** `shared.backlog_client.open_ticket()` (función `backlog.sp_open_ticket`): crea el ticket `OPEN` con reportero canónico, sesión en el formato de la norma,
  su primer log y su primer evento de estado en una sola llamada (`schema_name`/`object_name` = nombres físicos reales). Ya no existe forma heredada con `INSERT` en `BACKLOG` (retirada el 2026-10-28).
- **Flujo normativo:** `OPEN` → `TODO` → `IN_PROGRESS` (↔ `BLOCKED`) → `READY_FOR_DEPLOY` → `CLOSED`; se retrocede si falla QA. Un `BLOCKED` cuyo bloqueo resulta inexistente vuelve a `TODO`
  (`BLOCKED` → `TODO` es un movimiento legal de la matriz desde la decisión del propietario del 2026-10-28; antes solo salía a `IN_PROGRESS`, `DEFERRED` o `CLOSED`).
  Empezar a trabajar o comentar pasa a `IN_PROGRESS`. **Nunca sobrescribir progreso previo:** cada avance es un `INSERT` en
  `BACKLOG_LOGS`; `update_ts` lo mantiene un trigger.
- **Cierre:** desarrollado y no desplegado → `READY_FOR_DEPLOY`. Terminado (desplegado, rechazado o duplicado) → `CLOSED` con
  `closure_ts` y `solution_type` obligatorios; en despliegue real, además `deployment_ts` y `release_version`.
- **Movimientos (Fase A):** `shared.backlog_client.transition()` (función `backlog.sp_transition`) es la única vía para cambiar estado, campos de cierre, versión de
  release y responsable; escribe el log (el comentario, literal) y el evento de estado (`backlog.status_event`) en una transacción. Reglas: solo los movimientos legales
  del flujo anterior (reabrir un `CLOSED` y cualquier atajo exigen `p_force` y un motivo de 40+ caracteres, y quedan marcados); un cierre de entrega (`CODE_FIX`,
  `DATA_FIX`, `WORKAROUND`) viene de `READY_FOR_DEPLOY` y exige etiqueta `R<aaaa>.<mm>-<TAG>` (el procedimiento fija `closure_ts` y `deployment_ts`); `WORKAROUND`
  cita su ticket de seguimiento o abre uno `OPT`; `REJECTED` cita el ticket que lo sustituye; `DUPLICATED` cita el que se conserva y registra el enlace `DUPLICATES`;
  `BLOCKED` nombra su bloqueo (un ticket, con su `DEPENDS_ON` garantizado, o una condición escrita) y `DEFERRED` su fecha o suceso de revisión (`backlog.wait_event`).
  `scripts/ops/close_release.py` (dry run primero) sigue siendo la vía de los cierres de release.
- **Identidad (FND-0330, D5):** todo actor es canónico: `human:<login>` · `agent:<modelo>` · `system:<nombre>`. **Es una sola identidad por proceso y viene del entorno (orden del propietario, 2026-10-28):** `FONDOS_ACTOR` es obligatoria, el cliente se niega a escribir (`ActorUnknown`, sin tocar la BD) cuando falta o no es canónica, un `actor=` explícito debe ser igual a ella,
  y nunca se deduce del usuario del sistema operativo (una sesión de agente corre bajo el login del propietario). Para una sesión de agente se declara en la clave `FONDOS_AGENT_ACTOR` del `.env` (ignorado por git; `shared.config` lo carga) y el cliente la usa SOLO si `CLAUDECODE=1` (el arnés lo fija; la terminal de una persona no), así que una orden del propietario nunca hereda la identidad del agente y `FONDOS_ACTOR`, si existe, manda; debe nombrar el modelo que EJECUTA (con `"model": "opusplan"` el modo de planificación, de solo lectura, usa Opus y toda orden ejecutada, es decir toda escritura, usa Sonnet); si se cambia el modelo de la configuración hay que cambiar la variable a la vez. El entorno garantiza presencia, formato y coherencia, no la verdad: el arnés no expone el modelo a los procesos hijos (limitación L3). Las pruebas fijan `FONDOS_ACTOR` y redirigen el registro local de reserva a un directorio temporal (`shared/testing/sandbox.py`: ninguna prueba deja huella en el repositorio, la sesión falla si la deja, y las cadenas de conexión del backlog en vivo se quitan del proceso de pruebas). **Escritura en vivo (orden del propietario, 2026-10-29):** una persona en una terminal REAL debe teclear una vez por proceso `I CONFIRM THIS IS NOT A SYNTHETIC TEST` (`backlog_client.confirm_live_write`); un proceso NO interactivo (la herramienta de un agente, un script, una tubería, un hook) recibe un **fallo duro con código de salida 1** y no escribe nada (`LiveWriteForbidden`): `close_release.py` (también su simulacro, que escribe y revierte y gasta identificadores), `backlog_step0.py --apply` y `backlog_links.py --apply-review --confirm` lo aplican antes de abrir ninguna conexión, y un test falla si un script nuevo escribe sin pasar por la compuerta. Excepciones, y sólo estas dos, por decisión del propietario (2026-10-29; `NON_INTERACTIVE_OPERATIONS`, lista blanca en la fábrica de conexiones): anotar un log en un ticket EXISTENTE con identidad `agent:`/`system:` (`append_log`, login `backlog_logger`, rutina `sp_append_log`) y la captura automática de incidencias (`report_incident`, login `backlog_incident`, rutina `sp_report_incident`, solo inserción). Las acota la base de datos, no el cliente: ninguno de los dos logins puede abrir, mover, enlazar ni editar nada. «Terminal real» es una consola: en Windows `isatty()` es verdadero para el dispositivo NUL (el stdin de una herramienta de shell), así que se comprueba además `GetConsoleMode`. Límite reconocido: es una guarda del cliente, no una frontera de seguridad: quien posea la cadena de conexión puede saltarla; la frontera real es que la identidad del agente no tenga permiso de escritura (FND-0354: un login de solo lectura).

**IAM (FND-0354, `db/pg_backlog/50_iam.sql`, fase `iam`; runbook `doc/operativos/BACKLOG_IAM_ROLLOUT.md`):** un login por identidad. `backlog_reader` (solo lectura: la única identidad que una sesión de agente conserva en `.env`), `backlog_logger` y `backlog_incident` (arriba) y un login por operador (`human:a.gomez_devops`, `human:j.cormenzana_data`, `human:j.cormenzana_owner`, `human:m.ruiz_qa`; `human:jacor` sigue como alias de la historia). `backlog.actor_login_event` (solo añade) liga login e identidad; `fn_actor_bound`, llamada por `fn_actor_authorized`, hace que un login ligado escriba SOLO como su identidad y que una identidad humana solo se acepte desde un login ligado a ella. Cada vinculación declara quién la hace: `sp_bind_login` exige la identidad humana canónica declarante (`p_declared_by`, guardada en `declared_by`; un login ligado solo puede declarar la suya) y `backlog.v_binding_audit` marca `SELF_BINDING`, `DECLARER_NOT_BOUND_TO_SET_BY`, `LEGACY_NO_DECLARER`, `OFF_HOURS` (fuera de la ventana operativa: por defecto lunes a viernes de 07:00 a 19:00, hora del servidor; la cambia el propietario con `sp_set_binding_window`) y `RECENT` (últimas 24 h; el pre-flight imprime una línea `[ATENCION]` por cada una reciente). Varios logins de una persona cuentan como UNA persona (`person` es un CHECK de la base de datos, no texto libre: exactamente `human:a.gomez`, `human:j.cormenzana` o `human:m.ruiz`, y la identidad ligada debe ser suya). Un agente nunca cuenta como operador. El login compartido `backlog_writer` se retira después, por el propietario: `backlog_phase_a_acceptance.py --verify retire` falla mientras algún login pueda abrir o mover tickets sin estar ligado, o mientras menos de TRES personas distintas ligadas hayan hecho transiciones reales (reserva A-2). Lo «criptográfico» es el certificado de cliente TLS de cada login (`pg_hba.conf`, DevOps); el cliente pasa `sslmode`, `sslcert`, `sslkey` y `sslrootcert` de la cadena de conexión sin tocarlos. Los nombres históricos de
  `BACKLOG_LOGS` no se reescriben: `backlog.actor_alias` los traduce (`python -m shared.backlog_client --check-actors` debe dar 0).
- **Autoridad de escritura de los agentes (Backlog v3 W2, FND-0357; decisión del propietario, 2026-10-31; DDL `97_autonomy.sql`, `migrate_backlog_v2.py --phase autonomy`).** La lista blanca
  `NON_INTERACTIVE_OPERATIONS` suma la operación `agent` (con `gate`, la del gancho, y las dos de 2026-10-29): una identidad `agent:<modelo>` abre y mueve tickets, registra enlaces y notas, crea
  grupos y condiciones de cierre **sin terminal**, con su propio login `backlog_agent` (cadena `FONDOS_BACKLOG_PG_DSN_AGENT` en el `.env` ignorado por git; **sin retroceso a otro login**: sin esa
  cadena, o con `BACKLOG_AGENT_WRITES_ENABLED = False`, todo sigue por la consola como antes). La frontera la pone la base de datos, no el cliente: `backlog_agent` solo ejecuta
  `sp_open_ticket`, `sp_agent_transition`, `sp_record_link`, `sp_append_log`, `sp_annotate_wait`, `sp_create_group`, `sp_group_member`, `sp_add_close_condition` y `sp_retire_close_condition`,
  y no puede escribir ninguna tabla ni llamar a `sp_transition`, a la compuerta, a las retenciones ni a la adjudicación. **`sp_agent_transition`** es `sp_transition` sin lo que un agente no
  puede hacer: ni `p_force` (así que ni movimiento ilegal, ni atajo, ni anulación de la compuerta), ni reabrir un ticket `CLOSED`, ni un **cierre de entrega** (`CODE_FIX`, `DATA_FIX`,
  `WORKAROUND`). Este último queda fuera a propósito: la compuerta de incidentes de auditoría (FND-0264) lee otra base de datos (`fondos`) y la base del backlog no puede imponerla, así que los
  cierres de entrega siguen en `close_release.py` (una persona) hasta que la W3 los lleve al autopiloto, que la lleva consigo (`shared/backlog_release.py`). `REJECTED`, `DUPLICATED` y el resto
  de movimientos legales sí los hace el agente. **`sp_record_link`:** `PROPOSED` libre; `CONFIRMED` exige evidencia escrita de 40+ caracteres; y un par cuyo último evento es una decisión de una
  **persona** (`CONFIRMED` o `REJECTED` de un `human:*`) no lo toca un agente: el enlace lo decide una persona y el propietario siempre puede rechazar después (solo añadir). Las transiciones del
  agente cuentan para la reserva A-2 (`v_shadow_stage`). Hallazgo corregido en el mismo cambio: `create_group`, `group_member`, `add_close_condition` y `retire_close_condition` escribían sin
  pasar por la compuerta de escritura en vivo de la orden del 2026-10-29 (les faltaba `write=True`); ahora la pasan.
- **El autopiloto (Backlog v3 W3, FND-0358; decisión del propietario, 2026-10-31; el bloque W3 de `97_autonomy.sql`, misma fase `autonomy`).** Un ticket se mueve por **evidencia que el sistema registró**,
  no por una declaración. Los tipos de hecho de `ops_event` suman `MERGE` (una fusión en `master` que entrega el ticket: contiene un commit con `Closes: FND-nnnn`), `DEPLOY` (su DDL está aplicado) y
  `CYCLE` (corrió un ciclo tras la fusión). El login `backlog_autopilot` (sin contraseña: la fija el propietario; cadena `FONDOS_BACKLOG_PG_DSN_AUTOPILOT`, **sin retroceso a otro login**; operación
  `autopilot` de la lista blanca) solo ejecuta `sp_record_ops_event`, `sp_append_log` y `sp_auto_transition`, lee `fn_gate_blockers` y no escribe ninguna tabla. **`sp_auto_transition`** es su única
  forma de mover un ticket: fija el actor `system:autopilot`, nunca pasa `p_force` y acepta SOLO dos aristas, siempre citando un evento `MERGE` registrado para ese mismo ticket (sin él, rechaza):
  `IN_PROGRESS` → `READY_FOR_DEPLOY`, y `READY_FOR_DEPLOY` → `CLOSED` como `CODE_FIX` o `DATA_FIX` con etiqueta de release. Para cerrar, la base de datos vuelve a comprobar lo que puede: si la fusión
  toca `db/**` debe existir el `DEPLOY` del ticket; un `DATA_FIX` necesita un `CYCLE` posterior a la fusión y una condición de cierre activa. Las compuertas de la Fase B siguen aplicándose dentro. La
  compuerta de incidentes de auditoría (FND-0264) no puede imponerla la base de datos (lee `fondos`): la aplica el motor de reglas (`shared/backlog_autopilot.py`, puro, probado arista a arista) con la
  regla compartida de `shared/backlog_release.py`, la misma que usa `close_release.py`. `WORKAROUND`, `REJECTED`, `DUPLICATED` y reabrir **nunca** son automáticos. Un commit declara la entrega con
  los tráileres `Closes: FND-nnnn` (cuenta también como `Refs:`) y, solo si no es `CODE_FIX`, `Solution: DATA_FIX`; un valor desconocido no cierra nada. **El proceso** es
  `scripts/audit/backlog_autopilot.py` (identidad `system:autopilot`, un proceso, una identidad): lee los merges de `origin/master` de los últimos 45 días, toma como entrega cada merge cuya rama trae un
  commit con `Closes:`, registra un hecho `MERGE` por (merge, ticket) con el número de PR, los ficheros cambiados y el `Solution:`, reúne los veredictos de las compuertas (la de entrega de la Fase B, las
  condiciones de cierre de `rfd_matrix_probe.py`, la de incidentes de `fondos`; **lo que no se puede leer es un motivo para esperar, nunca un pase**) y aplica el motor. Sin `--apply` solo imprime. El
  **modo** (`BACKLOG_AUTOPILOT_MODE` en `shared/config.py`, un techo que `--mode` solo puede bajar): `OFF` imprime; `SHADOW` (el de salida) registra los hechos y escribe **una nota por decisión** en el
  ticket («movería X a Y, evidencia ...») sin mover nada, para que el propietario revise las 20 primeras (O-V3-4); `ON` hace además los dos movimientos mediante `auto_transition`, y un movimiento que la
  base de datos rechaza (una compuerta, un hecho que falta) pasa a ser una nota de espera, una sola vez. Solo escribe con su login (`FONDOS_BACKLOG_PG_DSN_AUTOPILOT`): sin él, o sin el DDL, `--apply` se
  niega con código 2 y no escribe nada; nunca cae a otro login ni pide terminal. **No fabrica `DEPLOY` ni `CYCLE`** (no puede dar fe de su propio cierre): un ticket cuyo merge toca `db/**` espera a que
  la migración del propietario registre el `DEPLOY`, y un `DATA_FIX` a que se registre un `CYCLE`. El gancho `reference-transaction` (`bash scripts/audit/reference-transaction.sh`, lo instala el
  propietario porque se dispara con **cada** actualización de referencia del clon) lanza el proceso en segundo plano cuando un `fetch` mueve `refs/remotes/origin/master`; no bloquea nunca git.
- **Estado de aplicación de la Fase A (FND-0331):** el propietario aplica el DDL por pasos, cada uno con simulacro y acuerdo expreso. **A-1** (`30_transitions.sql`,
  `migrate_backlog_v2.py --phase transitions`) es aditivo: crea el rol `backlog_owner` (sin superusuario ni login) como propietario de las rutinas privilegiadas
  (`SECURITY DEFINER`, `search_path` fijado), las tablas de auditoría y el disparador de la etapa de sombra, que **registra sin bloquear** (V-00) toda escritura directa en
  una columna gobernada. **A-2** (`31_revoke_raw_update.sql`, `--phase revoke`) retira al escritor el `INSERT` y el `UPDATE` directos, y el script **se niega** a aplicarlo
  salvo que haya 50 transiciones consecutivas no sistémicas sin V-00 (`backlog.v_shadow_stage`). A-1 está aplicado y las formas heredadas de escritura directa se retiraron del código (orden del propietario, 2026-10-28):
  sin A-1 las funciones de escritura del cliente y `close_release.py` fallan en voz alta y no escriben nada; `report_incident` deja la incidencia en el registro local de reserva.
- **Excepciones de escritura directa tras A-2 (D3, enumeradas y justificadas):** **E1** `INSERT` en `BACKLOG_LOGS` (notas de progreso, solo añadir; el autor debe ser
  canónico o tener alias); **E2** `INSERT` en `backlog_link_event` (solo añadir, con FK y `CHECK`; la Fase B lo lleva a una rutina; mismo control de autor); **E3**
  `UPDATE` de `title`, `nature`, `schema_name`, `object_type`, `object_name`, `priority`, `session_id` y `scenario_description` (atributos descriptivos y de triaje; `update_ts`
  registra que cambió algo pero no qué: laguna de auditoría conocida). Todo lo demás pasa por rutinas.
- **Identidad: humanos solo por el rol del propietario; agentes con su modelo (FND-0337, decisión del propietario 2026-10-27).** Un actor `human:*` solo se acepta desde una
  conexión cuyo *login* es `backlog_human` (o miembro suyo; un superusuario pasa): lo exigen `sp_open_ticket` y `sp_transition` (y por ellas `sp_close_release`) mediante
  `backlog.fn_actor_authorized`, que mira `session_user` y no `current_user` (un `SET ROLE` no convierte una sesión del escritor compartido en la del propietario), y, tras A-2,
  el disparador de autor de logs y eventos de enlace. La identidad genérica `agent:claude` (a la que resuelven `claude`, `claude-code`, `CLAUDE_SESSION`...) se rechaza en toda
  escritura nueva: un agente nombra su modelo. Asignar trabajo a una persona sigue permitido (no es escribir *como* ella). El rol se crea con el paso 32
  (`migrate_backlog_v2.py --phase human`) **sin contraseña**: la fija el propietario en su propia sesión y su cadena de conexión (`FONDOS_BACKLOG_PG_DSN_HUMAN`) vive fuera de
  `.env` y del entorno que heredan los agentes; el cliente la usa solo para actores humanos y falla en voz alta si falta (`HumanDsnMissing`), sin volver al escritor compartido.
  Mientras el rol no exista, las identidades humanas no se imponen (nada se rompe antes de aplicarlo). Los agentes siguen siendo declarativos entre sí.
- **Historial de cambios descriptivos (FND-0338, decisión del propietario 2026-10-27):** `backlog.field_change_event` (solo añadir, propiedad de `backlog_owner`) registra, mediante
  un disparador, cada cambio de las ocho columnas de la excepción E3 (valor anterior y nuevo, actor declarado, login, aplicación). El actor lo declara el cliente por transacción
  (`backlog.actor`; `backlog_client.update_attributes()` lo hace); una declaración no canónica o no permitida desde esa conexión se registra como actor `NULL`, de modo que la
  proporción de cambios sin atribuir se mide (`v_field_change_attribution`). Paso 33 (`--phase history`). La limitación E3 solo se da por resuelta cuando hay cambios reales
  registrados y esa proporción se ha medido y anotado en el ticket.
- **Compuerta de entrega, banderas de revisión y retenciones (FND-0333, Fase B; autorizada por el propietario el 2026-10-28; DDL `40_relations_v2.sql`, `--phase relations`).**
  `READY_FOR_DEPLOY` y los cierres de entrega (`CODE_FIX`, `DATA_FIX`, `WORKAROUND`) se evalúan contra los prerrequisitos `DEPENDS_ON` **confirmados** aún abiertos, las retenciones abiertas y las
  banderas de revisión sin reconocer. `REJECTED` y `DUPLICATED` **nunca** pasan por la compuerta. **Despliegue por fases (decisión del propietario, 2026-10-28):** la compuerta tiene un **modo**
  (`backlog.gate_switch`, solo añadir; el despliegue siembra `AUDIT`): en `AUDIT` cada movimiento evaluado se **registra** (`backlog.gate_audit_event`, `v_gate_audit_violation`; el evento de estado
  lleva `detail.gate_audit`) y **no se rechaza nada**; en `ENFORCE` un bloqueo rechaza el movimiento (el error lista los bloqueos) salvo `p_force` con motivo de 40+ caracteres, que queda en el
  evento de estado (`forced`, `detail.gate_override`) y se cuenta (`V-FORCED`); `OFF` no evalúa. **Solo un actor humano** (el rol del propietario) cambia el modo con `backlog.sp_set_gate`, y
  `ENFORCE` se rechaza hasta que la ventana de auditoría esté limpia (`v_gate_audit_window.clear`): 48 horas o más en `AUDIT`, ninguna violación registrada en las últimas 48 horas y movimientos
  evaluados en esas 48 horas sobre **al menos cinco tickets distintos** (decisión del propietario, 2026-10-28: un solo movimiento trivial no prueba la lógica bajo carga real); volver a `AUDIT` abre
  una ventana nueva. **Anulación ejecutiva (FND-0350, especificación aprobada por el propietario el 2026-10-28):** `backlog_client.set_gate('ENFORCE', motivo, executive_override=True)` (función `backlog.sp_set_gate` con `p_executive_override`) omite SOLO la condición de la ventana: únicamente para `ENFORCE`, únicamente un actor humano por el rol del propietario (un agente no puede pasarla nunca: la ejecuta el propietario en su sesión), con un motivo de 80+ caracteres que cite la directiva escrita; la fila queda marcada (`gate_switch.override`), `v_gate_override_event` las lista (código `V-GATE-OVERRIDE`, se cuenta y nunca bloquea) y el pre-flight lo muestra. Ya no hace falta ninguna inserción directa en `gate_switch` (un test de repositorio lo vigila). Una **bandera** (`backlog.review_flag`) se levanta en el
  otro extremo de un enlace `IMPACTS`, o de un `OVERLAPS` que lleve `absorbs`, cuando un ticket llega a `READY_FOR_DEPLOY` o se cierra; se reconoce con `backlog_client.ack_review()`.
  Una **retención** (`backlog.hold_event`) la levanta cualquiera (`raise_hold`) y **solo un humano la libera** (`release_hold`). `SUPERSEDES` solo sugiere cerrar el ticket sustituido
  (`v_supersede_suggestion`). **`deployment_ts` es hora del sistema** (limitación L1 de `doc/especificacion/LIMITACIONES_CONOCIDAS_BACKLOG_V2.md`): las rutinas lo sellan con la hora del cierre y **no existe ninguna rutina que lo corrija** (la única corrección, la de FND-0193, es el evento de estado 353; el propietario retiró la rutina el 2026-10-28). Un `UPDATE` suelto es una escritura directa (V-00) y reinicia el contador del paso A-2. `open_ticket()` propone como `PROPOSED RELATED` (origen `SOURCE`) los `FND-nnnn` citados en el texto. El paso 0 (`scripts/ops/backlog_step0.py` y su manifiesto) lo
  ejecuta el propietario tras desplegar la fase: añadir-solo, idempotente, sin revertir ningún cierre ni enlace.
- **Cierre de tickets vinculados a un defecto de auditoría (FND-0264):** un ticket enlazado a un incidente de `control.audit_incident` no se cierra hasta que el incidente esté `CERTIFIED` (re-auditoría posterior y completa) o `ACCEPTED` (residuales vigentes). `scripts/ops/close_release.py` (envoltorio de `sp_close_release`) lo aplica; ver `GOBIERNO_CONTROLES.md` §9.
- **Captura automática** (`report_incident()`, `capture_exceptions()`, `install_excepthook()`): ante una excepción sin tratar abre
  un ticket `OPEN`/`BUG`/`HIGH`/`SYSTEM_AUTODETECT` o, si ya hay uno abierto con el mismo `(project_code, object_name)` en 24 h,
  añade una nota de reincidencia. Es no-op (nunca lanza ni enmascara la excepción original) si `FONDOS_BACKLOG_PG_DSN` no está
  definida. Política de solapamiento: §7.
- **Analítica:** `backlog.vw_backlog_kpis` (lead time, despliegue, desgloses) y `backlog.sp_close_release(p_release_version)`
  (cierra en bloque los `READY_FOR_DEPLOY` de una release, fija `deployment_ts`, deja log).

## §3. Relaciones entre tickets

Un enlace va **de → a** (una dirección por par), con uno de los tipos de la tabla:

| Tipo | Significa |
|---|---|
| `DEPENDS_ON` | `from` no puede terminarse/validarse antes que `to` |
| `RELEASE_WITH` | ambos deben activarse en la misma release |
| `OVERLAPS` | mismo objeto de código, columna, interruptor o señal: un cambio en uno cambia el otro (`absorbs` dice qué alcance) |
| `DUPLICATES` | `from` duplica a `to` (`to` es el que se conserva) |
| `SPLIT_FROM` | `from` se separó de `to` |
| `RELATED` | solo contexto |
| `IMPACTS` | un cambio en `from` cambia `to` (Fase B: levanta una bandera de revisión en `to` cuando `from` llega a `READY_FOR_DEPLOY` o se cierra) |
| `SUPERSEDES` | `from` sustituye a `to` (Fase B: solo sugiere cerrar `to`) |

**Almacén:** una tabla **append-only** `backlog.backlog_link_event` — cada propuesta, confirmación, rechazo o recalificación es
una fila nueva (quién, cuándo, evidencia, `absorbs`); nada se actualiza ni se borra. Estados de evento: `PROPOSED`, `CONFIRMED`,
`REJECTED`. El estado **vigente** de un par es su último evento: vista `backlog.v_backlog_link`
(`ORDER BY event_ts DESC, event_id DESC`, desempate determinista — salvaguarda S-3). `backlog.v_open_link_graph` lista los
enlaces confirmados que tocan algún ticket abierto y marca `blocked_by_open` (un `DEPENDS_ON` cuyo prerrequisito sigue abierto).
Hasta la Fase B no hay trigger que bloquee cambios de estado: pasar de `BLOCKED` a `IN_PROGRESS` es una decisión deliberada, la herramienta solo avisa. **Desde la Fase B** (§2, compuerta de entrega) un
`DEPENDS_ON` confirmado y abierto sí impide `READY_FOR_DEPLOY` y los cierres de entrega (con `p_force` y motivo como única salida contada); el resto de cambios de estado sigue sin bloqueo. Un
`DEPENDS_ON` nuevo (`PROPOSED` o `CONFIRMED`) que cerraría un ciclo se rechaza al registrarlo. La tabla gana la columna `origin` (diccionario abajo). Un enlace se
retira con un evento `REJECTED` nuevo (nunca se edita): así se hizo con `FND-0333 → FND-0331` el 2026-10-28, porque la Fase B necesita el paso A-1 (vivo y aceptado) y no el A-2.

El DDL **lo aplica el propietario** (`python scripts/ops/migrate_backlog_links.py --apply`; simulacro por defecto). Hasta
entonces las herramientas funcionan en solo lectura y avisan «almacén sin aplicar».

**Diccionario de datos: `backlog.backlog_link_event.origin`** (columna nueva de la Fase B; valores fijados por un `CHECK`, por `backlog_client.LINK_ORIGINS` y por esta tabla — un test exige que
los tres coincidan; ningún consumidor existente lee la columna ni la vista `v_backlog_link` la expone):

| Valor | Cómo nació el evento |
|---|---|
| `REVIEW` | la revisión fuera de línea de `backlog_links.py` (valor por defecto: todo evento anterior a la Fase B) |
| `GATE` | registrado por una compuerta (reservado para la Fase C) |
| `STEP0` | las correcciones históricas de `scripts/ops/backlog_step0.py` |
| `SOURCE` | propuesto por `backlog_client.open_ticket()` a partir de los `FND-nnnn` citados en el texto de un ticket nuevo (siempre `PROPOSED` / `RELATED`) |

## §4. Procedimiento

| Momento | Qué se hace | Herramienta |
|---|---|---|
| **Antes de abrir** un ticket | Mirar los 3 tickets abiertos más cercanos al texto. Es un **ranking, no un veredicto**: añadir al existente o abrir uno enlazado | `backlog_links.py --new "<texto>"` |
| **Al empezar** un ticket | Listar vecinos y candidatas; **sale con 1** mientras un `DEPENDS_ON` confirmado tenga el prerrequisito abierto. Escribir en el primer log `afecta / afectado por / puede absorber` y, en el plan, qué alcance relacionado se absorbe, cuál se deja deliberadamente y la interfaz que el otro debe respetar | `backlog_links.py --for FND-nnnn` |
| **Durante** el trabajo | Un hallazgo fuera de alcance se anota en el log del ticket relacionado existente; no se abre ticket nuevo ni se descarta | — |
| **Al cerrar** | El log dice qué queda (alcance absorbido cumplido, o lo que pasa al otro ticket) y sigue la higiene de §4.1 | — |
| **Cada sesión de revisión** | Barrido: `--all`, revisar candidatas por lotes, aplicar | §6 |
| **Cada ciclo P1+P2** | Una línea en el informe de ciclo (`Relaciones del backlog`) y un `[ATENCION]` si hay tickets en curso con prerrequisito `DEPENDS_ON` abierto; otra (`Violaciones del backlog`) con el total y los 3 códigos principales de §4.2, y otra (`Lecturas de estado del backlog`) con las copias de estado leídas antes del último cambio del ticket (§4.3) | `p1p2_cycle_report.py` |

### §4.1. Higiene del registro (origen: FND-0323)

Un ticket deja de ser fiable cuando su texto, su estado o sus cifras divergen del sistema. Reglas de procedimiento:

1. **Condición de cierre → entrada de salida.** Cuando un log declara una condición de cierre («cerrar tras el próximo ciclo con deriva
   0», «sigue abierto hasta que el pase P1 escriba los 14»), la entrada que la cumple se escribe **en la misma sesión que la
   observa**, con el recuento y su fecha. Todo recuento en un log lleva **fecha y población**.
2. **Estado sincronizado.** Un `BLOCKED` por instrucción del propietario nombra la condición que lo levanta; al cumplirse (p. ej.
   la liberación) el estado se mueve en esa sesión. Los tickets `RELEASE_WITH` o acoplados en código se cierran como **una sola
   acción** (con la fase D, un grupo `TRAIN`, §4.2). Una decisión pendiente del propietario se registra como grupo `DECISION` (§4.2;
   sustituye a la etiqueta de texto «DECISION PENDIENTE» mientras la fase D no esté aplicada), para que la revisión periódica la liste.
3. **Premisa vigente.** En cada liberación o promoción se listan los tickets abiertos que citan los interruptores o parámetros
   cambiados (la señal S3 de §6 ya extrae ese vocabulario); cada uno recibe un log «premisa cambiada» (con qué hay que volver a
   medir) o se cierra como obsoleto (`REJECTED`) citando el ticket que lo supera.
4. **Relaciones declaradas en el código.** Si un comentario o un módulo dice que un ticket protege o depende de otro, el enlace se
   registra en la siguiente revisión (§6); `--review-out` solo propone lo que ve en el texto de los tickets.
5. **Población declarada.** Todo recuento nombra su población. Por defecto el alcance **excluye** los ISIN con
   `KIID_Status = WRONG_DOC` (regla del propietario, 2026-10-25: sin documento válido no se descubren los atributos que salen del
   KIID) y los retirados, salvo que el ticket trate precisamente de ellos.
6. **Cifras reproducibles.** La evidencia numérica nombra su definición (consulta, patrón, fuente de datos) o la ruta del script que
   la produce; sin ella la cifra no se puede volver a medir ni comparar.
7. **Cierre y referencias.** Al cerrar se busca el id del ticket y sus interruptores en comentarios de código y documentos; cada
   hallazgo se actualiza o se lista en el log de cierre.

Estado de aplicación: son reglas de procedimiento y las comprueba quien abre, empieza o cierra el ticket. Herramientas (FND-0323), todas
**bajo demanda y de solo lectura**, elegidas por medición y no integradas en ningún ciclo: `scripts/audit/backlog_hygiene.py
--premise-sweep` (regla 3; encuentra los tickets que citan un identificador cambiado, **no** los que no citan ninguno) y
`--stale-status` (regla 2; señal débil: encuentra lo que nadie toca, no lo que ya está hecho); y el filtro de población (regla 5) en la
línea «sin divisa de clase» del informe de ciclo. **No existe** detección automática de decisiones pendientes: un marcador de texto
libre acertó en torno a 1-2 de 5 y sería ruido; la etiqueta estructurada «DECISION PENDIENTE» de la regla 2 es la vía, y se
automatizará solo si aparecen entradas que lo justifiquen. El barrido de premisas no se ejecuta solo en cada liberación: lo lanza quien
libera.

Reglas que la fase D (§4.2) ya comprueba con un código de `backlog.v_backlog_violations`: regla 2, `BLOCKED` sin bloqueador estructurado
(`V-BLOCKED-NO-BLOCKER`), `DEFERRED` sin revisión (`V-DEFERRED-NO-REVISIT`) o con la fecha de revisión vencida (`V-REVISIT-EXPIRED`);
la apertura retroactiva de §4 «Antes de abrir» (`V-FAST`). Las reglas 1 y 3 a 7 siguen siendo de procedimiento.

### §4.2. Grupos, violaciones y métricas (fase D, FND-0335)

DDL `db/pg_backlog/60_groups_violations.sql`, aplicado por el propietario con `scripts/ops/migrate_backlog_v2.py --phase groups` (simulación
por defecto; reversión `--down`, que conserva los grupos). Especificación: `PROPUESTA_BACKLOG_V2.md` §6.5 y §7.

- **Grupos.** `backlog.group_tag` con `kind` ∈ {`TRAIN`, `THEME`, `DECISION`} y altas y bajas solo de inserción (`group_member`), siempre
  por `shared.backlog_client.create_group()` / `group_member()`. `v_group_status` da `EMPTY`, `OPEN` o `READY_TO_CLOSE` (todos los
  miembros `CLOSED`: una persona decide lo que sigue; nada se cierra solo). Al aplicar la fase, los enlaces `RELEASE_WITH` confirmados
  pasan **una vez** a grupos `TRAIN-<id menor>` (el tipo de enlace se mantiene); una baja hecha después no se deshace al reaplicar.
- **Violaciones.** `v_backlog_violations`, una fila por violación: `V-00`, `V-FORCED`, `V-FAST` (`CODE_FIX`/`DATA_FIX` creado y cerrado
  en menos de 1 h, por las columnas registradas, así que cuenta la historia), `V-BLOCKED-NO-BLOCKER`, `V-DEFERRED-NO-REVISIT`,
  `V-REVISIT-EXPIRED` (se lista; nada transita solo), `V-WORKAROUND-NO-OPT` (sin seguimiento registrado por `sp_transition` ni enlace
  a un ticket `OPT`), `V-DUP-NO-LINK`, `V-HOLD-OPEN` (retención abierta más de 48 h: `ATENCION`; más de 96 h: `ALERTA`). Los códigos
  de la fase C (`V-RETRO`, `V-WORK-ON-CLOSED`, `V-HOOK-BYPASS`, `V-GATE-*`) se añaden con ella y `V-STALE-READ` vive en `fondos`
  (FND-0332); `tests/test_backlog_violation_catalogue.py` falla si un código del catálogo no está en ninguna de las dos listas.
- **Métricas lean.** `v_backlog_lean_metrics`, en horas: plazo total (`creation_ts` → `closure_ts`, vale para la historia); tiempo de
  ciclo (primer `IN_PROGRESS` → último cierre) y espera en `READY_FOR_DEPLOY`, solo con eventos `LIVE`: un ticket sin ellos da NULL, no
  una cifra inventada con el `BACKFILL`.
- **Cierre de impacto.** `v_impact_closure`: desde cada ticket, los tickets **abiertos** a los que llega un cambio por enlaces
  confirmados, hasta 5 niveles. `IMPACTS` va de origen a destino; `DEPENDS_ON` del prerrequisito al dependiente; `OVERLAPS` y
  `RELEASE_WITH` en ambos sentidos; `RELATED`, `SPLIT_FROM`, `DUPLICATES` y `SUPERSEDES` no propagan.

### §4.3. Lectura del estado en el momento de decidir (FND-0332)

El estado de un ticket se lee **en el punto de decisión** (promoción P3, residuales aceptados, informe de ciclo:
`shared.backlog_client.get_action_point_status()`); `gestion` no guarda copias para nadie. Toda copia guardada en `fondos` lleva **la hora
en que se leyó**: `control.cycle_flag.ap_status_read_ts` (bloque `cycle_flag_read_ts` de `db/pg/35_control.sql`, aplicado por el
propietario con `scripts/ops/migrate_cycle_flag_read_ts.py`; NULL si la lectura falló o la fila es anterior). El informe de ciclo compara
cada copia del último ciclo con el último evento de estado del ticket (`backlog_client.latest_status_events()`): una copia leída antes
de que el ticket se moviera es `V-STALE-READ` (línea `Lecturas de estado del backlog` y un `[ATENCION]` con los tickets); un evento
`BACKFILL` no cuenta como movimiento. La reacción al cierre ya existe: un residual aceptado bajo un ticket `CLOSED` sale como
`[ATENCION]` (`accepted_residuals.attention_lines`). No hace falta ningún proceso periódico.

### §4.4. Sincronización con el sistema: commits, condiciones de cierre y estado (fase C, tramo 1, FND-0334)

DDL `db/pg_backlog/70_sync.sql`, aplicado por el propietario con `migrate_backlog_v2.py --phase sync` (necesita la fase D; la reversión
conserva `ops_event` y `close_condition`). Especificación: `PROPUESTA_BACKLOG_V2.md` §6.4.8.

- **Hechos operativos.** `scripts/audit/backlog_sync.py` lee `git log origin/master` y registra en `backlog.ops_event` un evento `COMMIT` por
  (commit, ticket), con la hora de autor, como `system:sync`. Es idempotente (recorre toda la historia cada vez; no hay marca de agua que
  perder). Simulación por defecto; `--apply` escribe. El id se cita de dos formas: el tráiler `Refs: FND-nnnn` (origen `REFS`: «este
  commit es trabajo en el ticket») o el asunto (origen `SUBJECT`: una mención, que no distingue trabajo de referencia). Los ids que no
  existen en el backlog (el hueco FND-0339..FND-0349) y los tokens mal formados (`fnd0193`, `FND_0193`) se informan y se omiten.
- **Avance automático** (interruptor `BACKLOG_AUTO_FORWARD_ENABLED`, activado). Solo para un commit registrado **por primera vez** cuyo
  tráiler `Refs:` cita un ticket en `OPEN` o `TODO`: pasa a `IN_PROGRESS` como `system:sync`, con el commit como evidencia. Nunca desde
  `BLOCKED` ni `DEFERRED`, nunca hacia atrás, nunca por una mención en el asunto y nunca otra vez por un commit ya registrado: un ticket
  que una persona devolvió a `TODO` se queda donde lo puso. Los cierres siguen siendo manuales. Con el interruptor apagado se registran
  los hechos y las violaciones y no se mueve nada.
- **Violaciones** (`v_violations_c`, unidas a `v_backlog_violations`): `V-RETRO` (el primer commit que cita el ticket es anterior a su
  `creation_ts`: el trabajo se hizo antes de que el ticket existiera) y `V-WORK-ON-CLOSED` (un commit que cita un ticket `CLOSED`, posterior
  a su cierre; si se reabre, deja de contar).
- **Condiciones de cierre.** `backlog.close_condition` (alta y baja solo de inserción) sustituye al diccionario `SPEC` de
  `rfd_matrix_probe.py`: añadir un ticket a la matriz ya no es un cambio de código (`backlog_client.add_close_condition()` /
  `retire_close_condition()`). `rfd_matrix_probe.py --seed` copia `SPEC` una vez (solo tickets aún en `READY_FOR_DEPLOY`); `--close-ready` lista
  los tickets cuyas sondas pasan todas (cerrar sigue siendo una decisión). Hasta aplicar la fase, la sonda usa `SPEC`.
- **Flujo de estado.** `v_status_feed`: cada evento de estado con su hora, para los consumidores de `fondos` (lectura desde una marca:
  `event_id > n`).
- **Pendiente de los tramos 3 y 4:** la regla de colisión con adjudicación y modos, la atestación firmada y el CI. El gancho `commit-msg`
  (tramo 2) está en §4.5.

### §4.5. El gancho `commit-msg` y la detección de elusión (fase C, tramo 2, FND-0334)

DDL `db/pg_backlog/80_hook.sql`, aplicado por el propietario con `migrate_backlog_v2.py --phase hook` (necesita el tramo 1 y la fase B; la
reversión conserva `gate_check` y `hook_bypass` y **no libera** ningún bloqueo). Especificación: `PROPUESTA_BACKLOG_V2.md` §6.4.1 y §6.4.6.
Lectura común del mensaje (gancho y sincronización): `shared/backlog_gate.py`.

- **Instalación.** `bash scripts/audit/commit-msg.sh`, una vez por clon (mismo patrón que `pre-commit.sh`). Instala `commit-msg`, que
  ejecuta `scripts/audit/commit_msg_gate.py`, y `pre-rebase`, que se niega a reorganizar `master`. Un `git commit --no-verify` se salta ambos.
- **Qué hace el gancho**, solo en un commit cuyo asunto o tráiler `Refs:` cita un ticket (los demás no tocan la base de datos):
  (1) un token mal formado (`fnd0193`, `FND_0193`, `FND-193`) **rechaza el commit** —es un error de escritura que se corrige solo— y un id
  que no existe en el backlog se avisa; (2) comprobación de colisión: hueco hasta el tramo 3; (3) un tráiler `Refs:` sobre un ticket en
  `OPEN`/`TODO` lo pasa a `IN_PROGRESS` como `system:gate` (interruptor `BACKLOG_AUTO_FORWARD_ENABLED`; nunca desde `BLOCKED`/`DEFERRED`,
  nunca por una mención en el asunto); (4) escribe una fila en `backlog.gate_check` (`sp_gate_check`) y añade el tráiler
  `Backlog-Gate: <nonce>`, con el nonce ligado al árbol preparado (`git write-tree`); un tráiler anterior (un `--amend`) se sustituye.
- **Nunca bloquea, salvo el punto 1** (y la trazabilidad estricta, si el propietario la activa: ver abajo). Base no alcanzable, más de 3 s o cualquier error: el commit sale con `Backlog-Gate: offline` y
  `backlog_sync.py` hace después el movimiento y la comprobación, sin penalización.
- **El gancho es no interactivo y escribe con su propio acceso acotado** (autorización del propietario, 2026-10-31, excepción a la orden del
  2026-10-29, en los mismos términos que `agent_log` e `incident`: la operación permitida es `gate`). Corta la entrada estándar (nada pide
  nunca una confirmación) y solo escribe por el rol `backlog_gate`, que **ejecuta tres rutinas y nada más**: `fn_gate_statuses` (estado de los
  tickets citados), `sp_gate_forward` (`OPEN`/`TODO` → `IN_PROGRESS` como `system:gate` mediante `sp_transition`; cualquier otro estado queda
  como está y el actor está fijado) y `sp_gate_check`. No tiene permiso sobre ninguna tabla, ni sobre `sp_transition`, bloqueos o aperturas.
  Lo crea el DDL de la fase `hook` **sin contraseña** y sin pertenecer a `backlog_writer`; el propietario fija la contraseña en su sesión
  (`ALTER ROLE backlog_gate PASSWORD '...'`) y pone la cadena de conexión en `FONDOS_BACKLOG_PG_DSN_GATE` (`.env`, ignorado por git). **Sin
  esa variable no hay escritura viva y nunca se cae al acceso general:** el commit sale con `Backlog-Gate: offline` y `backlog_sync.py
  --apply` hace el movimiento. `offline` solo ocurre, pues, si el acceso no está provisionado, la base no responde o se agota el
  presupuesto de 3 s; no es el camino por defecto. Riesgo aceptado: cualquier sesión que lea `.env` puede usar ese acceso, cuyo alcance es
  mover un ticket abierto a en curso como `system:gate` y escribir nonces.
- **Trazabilidad estricta** (interruptor `BACKLOG_REQUIRE_TICKET_ENABLED`, **desactivado** por decisión del propietario, 2026-10-31).
  Activado, el gancho **rechaza** también el commit cuyo asunto y tráiler `Refs:` no citan ningún `FND-nnnn`. **Quedan exentos y no necesitan
  ticket:** los commits de fusión (`Merge ...` o con una fusión en curso), los de reversión (`Revert ...`), los de tipo `chore` (asunto
  `chore:` o `chore(ámbito):`) y cualquier commit en una rama `chore/`. Con el interruptor apagado solo se rechaza un token mal formado.
- **Detección de elusión** (`backlog_sync.py`). Cada commit posterior a `BACKLOG_HOOK_EPOCH` que cita un ticket se juzga por su tráiler:
  *comprobado* (el nonce está en `gate_check` y su árbol es el del commit), *pendiente* (`offline`, sin penalización) o **elusión**
  (`TRAILER_MISSING`, `UNKNOWN_NONCE`, `TREE_MISMATCH`: `--no-verify`, otro clon, un tráiler falsificado). Los commits de fusión están
  exentos. Una elusión nueva se registra en `backlog.hook_bypass` (violación `V-HOOK-BYPASS`, vista `v_violations_c2`) y levanta un
  **bloqueo** (`hold_event`, código `V-HOOK-BYPASS`) sobre el ticket citado, una sola vez por commit y ticket; en un ticket `CLOSED` se
  registra sin bloqueo (ya lo cubre `V-WORK-ON-CLOSED`). El bloqueo lo libera una persona (`sp_release_hold`).
- **Armado.** `BACKLOG_HOOK_EPOCH` (ISO con desfase, p. ej. `2026-11-02T09:00:00+01:00`) vacío = **no armado**: la sincronización solo informa
  de los tráilers que ve y no registra ni bloquea nada. Lo arma el propietario cuando el gancho está instalado y la fase `hook` aplicada;
  la historia anterior nunca se juzga. Un reordenamiento (`rebase`) que cambie el árbol de un commit lo hace parecer una elusión
  (`TREE_MISMATCH`): límite aceptado por la especificación; el propietario lo resuelve con la liberación del bloqueo.

### §4.6. La regla de colisión, la adjudicación y los modos (fase C, tramo 3, FND-0334)

DDL `db/pg_backlog/90_collision.sql`, aplicado por el propietario con `migrate_backlog_v2.py --phase collision` (necesita el tramo 2; la reversión conserva
`gate_state`, `gate_collision` y `gate_adjudication`, y los enlaces y bloqueos que el gancho ya creó). Especificación: `PROPUESTA_BACKLOG_V2.md` §6.4.2 a §6.4.5.

- **Apagado por defecto.** El techo es `BACKLOG_COLLISION_GATE_MODE` en `shared/config.py` (`OFF`, `AUDIT`, `BLOCK`). Con `OFF` el gancho ni siquiera pregunta a la base.
  El modo efectivo es el **menor** entre el techo y el último evento de `backlog.gate_state`; sin evento, el techo y el nivel `T0`.
- **La regla** (`fn_collision_pairs`). **T0:** el commit toca un fichero de código (no `.md` ni `.txt`) cuya ruta completa aparece en el texto o los logs de **otro** ticket
  `IN_PROGRESS`; los ficheros citados por 5 o más tickets abiertos se ignoran; se omiten las parejas con un enlace `CONFIRMED` y las ya adjudicadas. **T1:** T0 y al menos 2
  ficheros compartidos. La búsqueda ignora mayúsculas. «Coincidencia a nivel de función» no es un nivel.
- **Qué hace el gancho `commit-msg`** (paso 2 de §4.5, tras obtener el nonce para que la evidencia lo nombre): `sp_gate_collision` registra cada pareja nueva como un evento
  `OVERLAPS` `PROPOSED` con `origin = 'GATE'` (orden canónico: el id menor primero), apunta una fila en `gate_collision` y **avisa por stderr; nunca bloquea el commit**. Una
  pareja es *no decidida* mientras su último evento sea esa propuesta. En `BLOCK` levanta además un bloqueo (`V-COLLISION`) sobre el ticket que hace el commit, una sola vez;
  en `AUDIT` no hay bloqueo. Un fallo de la regla nunca cuesta el nonce.
- **Adjudicación** (`sp_adjudicate_overlap(a, b, decisión, absorbs, razón, actor)`). Solo la ejecuta el rol `backlog_adjudicator` (o un miembro): se comprueba `session_user`,
  no un parámetro, porque una rutina `SECURITY DEFINER` corre como su propietario. **El rol y su cadena de conexión los crea el propietario y viven fuera de `.env`**
  (`FONDOS_BACKLOG_PG_DSN_ADJUDICATOR`, que se teclea en su terminal); hasta entonces nadie adjudica, que es el estado seguro. El actor es una identidad `human:<login>`.
  Decisiones: `OVERLAPS` (enlace `CONFIRMED`, con `absorbs`), `RELATED` y `DEPENDS_ON` (se rechaza la propuesta y se confirma ese enlace), `NONE` (se rechaza) y `REVERT` (como
  `OVERLAPS`; la razón debe llevar el sha de 40 caracteres del revert). Cada decisión escribe un log en ambos tickets, queda en `gate_adjudication` y libera los bloqueos
  `V-COLLISION` del ticket que se queda sin parejas no decididas. Una pareja decidida no se vuelve a proponer. La herramienta es `scripts/audit/gate_adjudicate.py`
  (`--list`, `--queue-out` / `--apply-decisions` con un CSV, `--decide`; simulacro por defecto en el CSV).
- **Cola.** `v_gate_queue`: parejas no decididas con su edad; 48 h `[ATENCION]` y 96 h `[ALERTA]`. El pre-vuelo (`preflight_check.py`) imprime el `[ALERTA]` como **primera línea**
  y sale con código 2; nada se relaja porque la cola envejezca.
- **Modos** (`sp_set_collision_mode`, solo añade): una persona fija cualquier modo; **`BLOCK` solo tras 14 días de `AUDIT` y con una proporción de commits que introducen una
  pareja de 10 % o menos** (`fn_gate_share(3)`, medida en `gate_collision`), comprobado por la base; `system:calibrator` solo puede retirar la puerta (`OFF`) o apretar `T0` a
  `T1` en el modo vigente: nunca baja `BLOCK` a `AUDIT`, nunca afloja. Rehabilitar es un evento de una persona con razón. El propietario lo hace con
  `scripts/audit/gate_mode.py` (`--show` lee el reloj de la auditoría y lo que aún le falta a `BLOCK`; `--set MODO --reason ...` apunta el evento, con identidad `human:*`, el acceso
  solo del propietario y la confirmación escrita). Poner la puerta en marcha exige **las dos cosas**: subir el techo en `shared/config.py` (cambio revisado) y ese evento.
- **Qué acceso usa cada pieza** (fijado por `tests/test_backlog_gate_rbac.py`): los dos ganchos usan **solo** el acceso acotado `backlog_gate` (`FONDOS_BACKLOG_PG_DSN_GATE`, sin
  volver al general) y solo estas funciones del cliente: `gate_statuses`, `gate_forward`, `gate_check`, `gate_collision`, `collision_state(scoped=True)` y `gate_undecided(scoped=True)`;
  `gate_adjudicate.py` escribe solo con `backlog_adjudicator`; el calibrador (`system:calibrator`) y `gate_mode.py` escriben solo `sp_set_collision_mode`, el calibrador por el acceso
  compartido (no hay otro acotado) y siempre con una persona delante, `gate_mode.py` por el acceso solo del propietario; ninguno usa el de adjudicación.
- **`pre-push`** (`scripts/audit/pre_push_gate.py`, lo instala `commit-msg.sh`). En `BLOCK` se niega a publicar commits cuyos tickets tengan una pareja no decidida y escribe los
  shas y las parejas exactas; en `AUDIT` avisa; con la puerta apagada no hace nada. Se borra una rama sin problema. Base inaccesible o error del script: el push pasa con un
  aviso (la atestación del tramo 4 repite la comprobación).
- **Un fallo de un gancho nunca bloquea.** Los dos ganchos solo convierten en rechazo el estado de salida **3** del script; una caída (sintaxis, importación) deja pasar. Quien
  ya instaló el gancho debe **volver a ejecutar `bash scripts/audit/commit-msg.sh`** para tener esta versión.
- **Decisiones de diseño que se apartan de la letra de la especificación**, todas hacia lo más seguro: el apretado `T0 → T1` y la retirada se apuntan como eventos de
  `gate_state` (no de `ops_event`, cuyo `ticket` es obligatorio); un ticket solo recibe el bloqueo en `BLOCK` (en `AUDIT` «no se bloquea nada»); una pareja ya adjudicada
  `NONE` no se vuelve a proponer.
- **Calibrador y retirada** (`scripts/audit/gate_calibrator.py`; simulacro por defecto, `--apply` pide la confirmación escrita; identidad del proceso `system:calibrator`). Solo
  **aprieta** y retira: (1) *apretado*: desde el día 10 de `AUDIT`, si la proporción de commits de los últimos 3 días que introdujeron una pareja nueva supera el 10 %, pasa
  `T0 → T1`, una sola vez; (2) *retirada*: las ventanas empiezan en el último evento de `gate_state` (inicio de la auditoría, un apretado, una rehabilitación) y necesitan 14
  días; un veredicto exige al menos **15 parejas adjudicadas y 10 OVERLAPS confirmados nuevos** (la ventana se alarga hasta 42 días; pasado eso informa «datos insuficientes» y
  **no retira nada**); con muestra, una *precisión* (parejas decididas `OVERLAPS`, `DEPENDS_ON` o `REVERT` sobre las adjudicadas) por debajo del 30 % o un *suelo de cobertura*
  por debajo del 5 % ponen la puerta en `OFF`, con los números como evidencia y un `[ALERTA]` en la primera línea; vale en `AUDIT` y en `BLOCK`. **Suelo de cobertura:** de los
  `OVERLAPS` que una persona confirma por otra vía (no por la propia puerta: sería circular), qué parte había propuesto ya la puerta. La retirada se evalúa antes que el apretado.
  Nunca baja `BLOCK` a `AUDIT` ni afloja un nivel; reactivar es un evento de una persona con razón. Sale con código 3 cuando decide una retirada.
- **Verificación de `REVERT` y línea del informe de ciclo.** `backlog_sync.py` comprueba cada pareja adjudicada `REVERT`: el sha de 40 caracteres de la razón debe estar en la historia
  inspeccionada y ser un revert (su asunto empieza por `Revert` o dice `This reverts commit`); si no, imprime una línea `[ATENCION] REVERT adjudication ... is not verified` (sin sha,
  sha ausente o que no es un revert). Solo informa: no registra ni bloquea nada. El informe de ciclo (`p1p2_cycle_report.py`) lleva la línea «Cola de colisiones», un punto de atención a
  las 48 h y un `[ALERTA]` como **primera línea** a las 96 h.
- **Pendiente del tramo 3:** conectar la tabla CSV de `backlog_links.py --apply-review` (hoy la herramienta propia `gate_adjudicate.py` lleva su CSV).

### §4.7. Atestación firmada, CI y protección de la rama (fase C, tramo 4, FND-0334)

DDL `db/pg_backlog/95_attest.sql`, aplicado por el propietario con `migrate_backlog_v2.py --phase attest` (necesita el tramo 2; la reversión conserva
`gate_protection_event`). Especificación: `PROPUESTA_BACKLOG_V2.md` §6.4.6 y §6.4.7. El flujo de publicación completo, de principio a fin:

**Backlog v3, W1 (decisión del propietario, 2026-10-31): la publicación es automática y la atestación es consultiva.** (a) Un push a una rama `feat/**`, `fix/**`, `chore/**` o `docs/**`
dispara `.github/workflows/auto-pr.yml`: abre el pull request contra `master` si no hay uno abierto y activa la fusión automática, de modo que GitHub fusiona cuando pasan las comprobaciones
obligatorias de la protección (que no cambia). El propietario conserva el veto: cierra el pull request o desactiva la fusión automática. Un pull request que modifica `.github/workflows/**`
no puede fusionarse con el `GITHUB_TOKEN` integrado: recibe la etiqueta `needs-owner` y **no** se activa la fusión automática. Las ramas `publish/**` no se tratan aquí. Ajustes únicos del
repositorio (acción O-V3-2, pasos en `ACTION_PLAN_BACKLOG_V3.md`): permitir que Actions cree pull requests, permitir la fusión automática y borrar las ramas fusionadas. (b) `backlog-gate` ejecuta `verify_attestation.py --advisory`:
una atestación ausente, un commit no cubierto, un hash de árbol distinto o una pareja sin decidir son **avisos** (`::warning::`), no fallos, y nadie firma en cada push; una firma que **no
verifica** sigue fallando. `backlog-gate` no es una comprobación obligatoria de `master` (`github_protect_master.py --with-backlog-gate` queda obsoleto). La garantía descansa en la detección
dentro de una sincronización y en la retención (*hold*) del ticket (`PROPUESTA` §10.1: «detectado y retenido», nunca «aplicado»). Las viñetas siguientes describen el mecanismo firmado, que
sigue disponible cuando el propietario lo ejecuta a mano (`--attest`).

- **Atestación** (`backlog_sync.py --attest --key <clave privada>`, la ejecuta el propietario al publicar). Escribe `backlog_gate/attestation.json` para los commits de
  `origin/master..HEAD` y lo firma con `ssh-keygen -Y sign -n backlog-gate` (`backlog_gate/attestation.json.sig`). Por cada commit que cita un ticket: sha, hash del árbol, nonce y las
  parejas de colisión de sus tickets, decididas o no. **Solo identificadores: ni títulos ni texto** (el remoto es GitHub). Solo lee la base de datos. **La clave privada nunca está en el
  repositorio ni en `.env`**: el script se niega si está dentro del árbol. El commit de la atestación (todos sus cambios bajo `backlog_gate/`) queda fuera del rango: se commitean los dos
  ficheros juntos y se publica.
- **CI** (`.github/workflows/backlog-gate.yml`, tarea `backlog-gate`, que es el contexto que debe exigir la rama). `scripts/audit/verify_attestation.py` falla si un commit del rango que
  cita un ticket falta en la atestación o su hash de árbol o su nonce difieren, si alguna pareja está sin decidir, o si la firma no verifica. **Verifica con el `allowed_signers` de la BASE del
  rango**, así un cambio no puede traer su propia clave. Mientras ese fichero no liste ninguna clave la puerta **no se aplica** y el trabajo pasa con un aviso `NOT ENFORCED` (nunca un verde
  silencioso). Límite declarado: la CI prueba que la clave del propietario firmó una declaración; no puede comprobar la base de datos.
- **Protección de la rama.** Una Action sobre un push no puede bloquearlo: solo bloquea si `master` está protegida y `backlog-gate` es una comprobación **obligatoria** (acción del
  propietario O-5; con ella se publica con una rama `publish/<fecha>`, se espera al verde y se avanza `master`). `backlog_sync.py` lo lee de la API de GitHub con un token de **solo lectura**
  de grano fino que se guarda **fuera del repositorio** (`BACKLOG_GATE_GITHUB_TOKEN`, que se teclea en su terminal, nunca en `.env`); el repositorio y la rama son
  `BACKLOG_GATE_GITHUB_REPO` y `BACKLOG_GATE_GITHUB_BRANCH`. Tres lecturas: `PROTECTED`; `UNPROTECTED` (`V-GATE-UNPROTECTED` y un `[ALERTA]`); `UNVERIFIED` (sin token, la API no responde,
  permiso insuficiente o una respuesta que no permite decidir: `V-GATE-UNVERIFIED`, de modo que un token ausente nunca pasa en silencio). La configuración local de git no ve la protección
  remota y no es una fuente. **El token es opcional mientras el repositorio sea público:** sin token se lee el objeto público de la rama
  (`GET /repos/{repo}/branches/{rama}`, que lista las comprobaciones obligatorias y su nivel de aplicación) y la respuesta es igual de concluyente; solo si tampoco se puede leer
  (sin red, o repositorio privado sin token) la lectura es `UNVERIFIED`. Con token se usa antes el punto de administración. Un nivel de aplicación `off` no cuenta como obligatoria. Con `--apply` la lectura se apunta en `gate_protection_event` (solo si cambia o tiene más de 24 h).
- **Eventos del propietario** (`scripts/audit/gate_protection.py`, con identidad `human:*` y el acceso solo del propietario): `--verified "razón"` (ha mirado él mismo los ajustes del
  repositorio) despeja `V-GATE-UNVERIFIED` durante **30 días**, pero nunca `V-GATE-UNPROTECTED`; `--emergency "razón"` registra un push de emergencia (`V-GATE-EMERGENCY`, que queda en el
  historial). Protocolo de emergencia (`PROPUESTA` §6.4.7): el propietario como administrador salta la regla, registra el evento el mismo día y **en 48 h** genera una clave nueva, commitea
  su parte pública en `.github/backlog_gate_allowed_signers`, firma una atestación de recuperación del rango y relanza la comprobación con `workflow_dispatch` (campo `base`). La pérdida o la
  rotación de la clave siguen el mismo camino.
- **Acciones del propietario, en orden:** (1) generar la clave (`ssh-keygen -t ed25519 -C backlog-gate-owner`, con frase de paso, fuera del repositorio); (2) commitear la línea pública
  `backlog-gate-owner namespaces="backlog-gate" ssh-ed25519 AAAA...` en `.github/backlog_gate_allowed_signers` con un cambio revisado (vale para el rango SIGUIENTE); (3) crear el token de
  solo lectura y exportarlo solo en su terminal; (4) tras el primer push con CI verde, añadir `backlog-gate` a las comprobaciones obligatorias de `master`
  (`scripts/ops/github_protect_master.py` describe y verifica el resto de la regla); (5) aplicar `--phase attest`.

### §4.8. Verificación de aceptación de la fase C (FND-0334)

`python scripts/audit/backlog_phase_c_acceptance.py --verify hook|collision|attest|all` comprueba, **solo con lectura**, que lo que el propietario aplicó es exactamente lo diseñado, y deja
la evidencia en `out/audit/fnd0334_acceptance_<fase>_<UTC>.txt` (sale con 0 si todo pasa, 1 si algo falla, 2 si no hay conexión). Úsese tras cada `--phase hook|collision|attest --apply`.
Para cada fase: existen las tablas, vistas y rutinas (las listas son las de `migrate_backlog_v2.py`: una sola fuente); cada tabla es de `backlog_owner` y lleva el disparador de solo-añadir
**habilitado** (uno deshabilitado es un fallo); cada rutina privilegiada es `SECURITY DEFINER`, de `backlog_owner`, con `search_path` fijo y no ejecutable por `PUBLIC`. Los accesos acotados
son acotados: `backlog_gate` puede entrar, no es superusuario, no crea roles ni bases, tiene su límite de conexiones y de tiempo, no pertenece a otro rol, **no tiene ningún privilegio sobre
ninguna tabla o vista de todo el esquema `backlog`** y ejecuta exactamente sus rutinas y ninguna de las privilegiadas; `backlog_adjudicator` (el rol lo crea el propietario, no esta
herramienta) no es superusuario, ejecuta solo la adjudicación y la lectura de las parejas sin decidir, y no escribe en ninguna tabla; el acceso compartido lee y no escribe directamente,
puede fijar un modo y **no** adjudicar. Una fase sin aplicar falla sus comprobaciones de existencia: nunca pasa en silencio. La contraseña de un rol no es legible sin ser superusuario: que
el propietario la fijó lo demuestra que `gate_mode.py` o `gate_adjudicate.py` se conecten.

## §5. Protocolo de absorción de alcance

Cuando un enlace `OVERLAPS` lleva nota `absorbs`, el ticket que absorbe **debe**:

1. copiar **literalmente** en su log de arranque los criterios de aceptación absorbidos del otro ticket;
2. citar el ticket absorbido en el plan indicando qué se cubre y qué no;
3. al cerrar, dejar en el log del absorbido qué quedó cubierto y qué queda pendiente.

Sin esto, un requisito se pierde en silencio cuando el ticket absorbido se cierra como cubierto.

## §6. Descubrimiento y revisión (`scripts/audit/backlog_links.py`)

**Señales** (una pareja puede llevar varias; ninguna decide sola): **S1** mención explícita de un `FND-nnnn` en título/descripción/
logs (+ pista de tipo por las palabras que la rodean; «split out to FND-x» invierte la dirección); **S2** huella de ficheros
(commits que nombran el ticket + rutas citadas, solape ponderado por IDF); **S3** vocabulario del repo (interruptores `*_ENABLED`,
tablas/columnas DDL, módulos y funciones; se reconstruye en cada ejecución — medido 1,8 s en total, sin caché); **S4** coseno
TF-IDF de título+descripción+logs (`shared/backlog_overlap.py`, solo stdlib: no se añaden dependencias al entorno `des`).

**Candidatas:** parejas con un extremo **pendiente** (dos cerrados no se revisan), sin enlace previo en ningún estado (un par
`REJECTED` no se repropone), con S1 o con señales corroboradas; las no explícitas se limitan a **5 por ticket pendiente**. Los
umbrales son constantes del código (`T_S2`, `T_S3`, `T_S4`…), calibradas con `--evaluate` sobre datos vivos; cambiarlas es una
revisión de código.

**`--evaluate`** informa: *recall* de cada señal S2–S4 sobre las parejas explícitas S1 (no es precisión: una mención no siempre es
dependencia) y *precisión* de las propuestas ya decididas (n/d hasta que se aplique una revisión). `--new` es solo ranking: sobre
36 tickets abiertos con vecino declarado el vecino cae en el top 3 en el 72 %, pero las puntuaciones de vecinos y no vecinos
se solapan, así que ningún umbral puede ser puerta.

**Revisión** (`--review-out` → `out/reports/backlog_links/review_<YYYYMMDD_HHMMSS>.{md,csv}`; nunca sobrescribe): se rellena la
columna `decision` con `confirm`, `reject` o `retype:<TIPO>` (vacío = omitir; `absorbs` y `note` opcionales). Se aplica con
`--apply-review FILE.csv` (simulacro) y `--confirm` (inserta eventos). Reglas:

- **S-1 caducidad:** el fichero guarda estado y `update_ts` de ambos tickets al generarse. Antes de insertar se consulta el
  backlog vivo: una fila cuyo ticket ya no existe o **cambió de estado** (p. ej. se cerró) se **omite** con aviso. Un ticket ya
  cerrado al generar sigue siendo válido (los destinos de `SPLIT_FROM`/`DUPLICATES` suelen estarlo).
- **D-2 higiene:** un fichero ya archivado se rechaza; tras aplicar con éxito se mueve a `archived/`.
- **D-3 forzado:** `--force-row FROM:TO:TIPO` (repetible) mantiene una fila marcada; queda en la evidencia del evento
  (`FORCED despite: …`) y exige **una línea con el motivo** en el log de backlog de la sesión (quién, fila, por qué no se
  regeneró). `--all` informa el % de decididos forzados para que el forzado sistemático sea visible.

`--mermaid [--with-candidates]` escribe `out/reports/backlog_links/<stamp>.md` (el informe de ciclo es texto plano, Mermaid no
se renderiza ahí).

## §7. Política de solapamiento de incidencias automáticas (`report_incident`)

Interruptor `BACKLOG_INCIDENT_AUTO_APPEND_ENABLED` (**False** por defecto; umbrales `BACKLOG_INCIDENT_HIGH_COSINE`,
`BACKLOG_INCIDENT_MEDIUM_COSINE` en `shared/config.py`). Con él activo, antes de abrir ticket se compara la incidencia con los
abiertos (`shared/backlog_overlap.classify_incident`):

- **HIGH** (salvaguarda S-2): mismo `object_name` **y** similitud ≥ `HIGH_COSINE` **y** mismos códigos de salida si ambos los
  declaran (`RC=2` ≠ `RC=101`: son defectos distintos aunque el texto casi coincida) → se **añade** al ticket con una nota que dice
  por qué.
- **MEDIUM:** se abre el ticket nuevo y su primer log solo **sugiere** el relacionado («Possibly related (NOT merged)»).
- **NONE:** comportamiento de siempre.

**Puerta de activación (D-1)** — `python scripts/audit/backlog_links_calibration.py --gate` debe dar **cero falsas fusiones** en
(a) la reproducción histórica (cada ticket tratado como incidencia nueva contra los abiertos en su `creation_ts`; positivos =
cerrados como `DUPLICATED` con destino citado + `DUPLICATES` confirmados) y (b) el fixture sintético
`tests/fixtures/backlog_incident_cases.json` (mismo módulo/otro defecto, mismo defecto/otra redacción, RC de lanzadores,
tracebacks truncados). El historial solo tiene 1 ticket `SYSTEM_AUTODETECT` y 0 reincidencias, por eso (a) sola no valida nada y
(b) es obligatorio. Adjuntar el informe al log del ticket que active el interruptor.

**Hallazgo de la calibración (2026-10-11):** la regla «el escenario nombra un ticket abierto → HIGH» hizo **53 falsas fusiones**
sobre los 218 tickets históricos (se citan tickets relacionados constantemente: una cita no es un duplicado). Ahora una mención es
solo **MEDIUM**. Con las reglas vigentes: 0 HIGH en el histórico y 9/9 casos del fixture correctos.

## §8. Lista de verificación antes de commit

Cambio en un componente que reporta incidencias (`report_incident(` / `capture_exceptions(` / `install_excepthook(`) o en su
formato de error/log:

- [ ] su `object_name` (o el prefijo constante del f-string) figura en `object_name_prefixes` del fixture y tiene al menos un caso
  duro; **lo hace cumplir** `tests/test_backlog_overlap.py::test_every_incident_reporter_has_a_fixture_case` (AST sobre
  `shared/`, `proyecto*/`, `scripts/`), no solo esta lista;
- [ ] `python scripts/audit/backlog_links_calibration.py --gate` en verde;
- [ ] si cambia `LINK_TYPES`/`LINK_STATUSES`: el `CHECK` de `20_links.sql` y `backlog_client` se mantienen iguales (lo verifica
  `tests/test_backlog_links_client.py`).

Cambio en `backlog_links.py`: tests puros (`tests/test_backlog_links_signals.py`) y `--evaluate` sobre datos vivos informado en el
log del ticket; el SQL de `gestion` vive **solo** en `shared/backlog_client.py` (el barrido EXPLAIN lo exime con motivo
`DESIGN`).

Al cerrar o actualizar un ticket (§4.1):

- [ ] la entrada que cumple su condición de cierre está escrita y lleva fecha y población;
- [ ] el estado refleja la realidad (nada `BLOCKED` tras la liberación que lo levantaba; los `RELEASE_WITH` cerrados juntos);
- [ ] las cifras citadas nombran su definición o su script, y excluyen `WRONG_DOC` salvo que el ticket trate de ellos;
- [ ] el id y los interruptores del ticket se han buscado en código y documentos, y lo encontrado está actualizado o listado.

## §9. Operación

| Qué | Comando |
|---|---|
| Aplicar el almacén (propietario) | `python scripts/ops/migrate_backlog_links.py` (simulacro) · `--apply` |
| Barrido general | `python scripts/audit/backlog_links.py --all` |
| Antes de abrir / al empezar | `--new "<texto>"` · `--for FND-nnnn` |
| Revisión | `--review-out` → rellenar CSV → `--apply-review F.csv` → `--confirm` |
| Gráfico | `--mermaid [--with-candidates]` |
| Higiene del registro (§4.1) | `python scripts/audit/backlog_hygiene.py --premise-sweep "<rev>^..<rev>"` · `--stale-status [--days N]` |
| Puerta de la política automática | `python scripts/audit/backlog_links_calibration.py --gate` |
| Verificar el estado y la puerta sin tocar el vivo (§9.1) | `python scripts/ops/backlog_staging.py` · `--scenario a2_revoke` · `--synthetic` |
| ¿El vivo tiene todo el DDL de master? (§9.1, solo lectura) | `python scripts/ops/backlog_drift.py` |

Salidas en `out/reports/backlog_links/` (ignorado por git). Tests: puros (`test_backlog_links_*.py`, `test_backlog_overlap.py`,
`test_backlog_links_calibration.py`) y herméticos PG (`python scripts/ops/run_pg_tests.py -- tests/test_backlog_links_ddl_pg.py
tests/test_backlog_links_client_pg.py tests/test_backlog_incident_overlap_pg.py`).

### §9.1. Regla de pruebas: nada sintético contra el backlog vivo (FND-0351)

**Regla (mandato del propietario, 2026-10-28):** está **prohibido** probar con datos sintéticos que consumen ids de producción (secuencias de tickets, logs y eventos). Toda verificación de la
máquina de estados o de la lógica de la puerta se hace en un **contenedor de preparación aislado** cargado con una instantánea del backlog, nunca en el vivo. Leer el vivo en solo lectura (una
exportación) sigue permitido. Esto incluye las transacciones con retroceso que consumieron `FND-0339` a `FND-0349` (ver abajo).

- **Un solo comando:** `python scripts/ops/backlog_staging.py` exporta una instantánea nueva (solo lectura), levanta el contenedor desechable de `run_pg_tests.py`, aplica el DDL real de las fases
  elegidas (`--phases`, por defecto `transitions,relations`), ejecuta los escenarios y lo destruye. **Demuestra** que no dejó rastro: lee los valores de las secuencias vivas antes y después y deben
  ser idénticos (`live sequences after the run: UNCHANGED`). `--synthetic` o `--snapshot FILE` no tocan el vivo en absoluto.
- **Escenarios** (`tests/test_backlog_staging_pg.py`): `gate_clean`, `gate_blocked`, `gate_forced` (el camino excepcional con motivo de 40+ caracteres, contado), `gate_audit_window` (en AUDIT no se
  rechaza nada y se registra lo que habría bloqueado), `a2_revoke` (el paso pendiente `--phase revoke` ensayado sobre la población real: cerrado con menos de 50 transiciones reales, abierto con 50,
  y tras la revocación el acceso compartido ya no actualiza las columnas gobernadas mientras las rutinas siguen funcionando) y `replay` (la historia real por la puerta real; requiere instantánea real).
- **Guarda programática** (`shared/backlog_scratch.py`, `assert_scratch(conn, who=...)`): todo guion que altere estado para simular carga o reproducir historia la llama **antes de su primera
  sentencia**. Se niega salvo que la conexión **no** sea un objetivo vivo del entorno (host, puerto y nombre de `FONDOS_BACKLOG_PG_DSN`, `..._HUMAN`, `FONDOS_PG_DSN` y el DSN del propietario, con los
  alias de localhost iguales), **no** esté en el mismo **servidor** (host y puerto) que un objetivo vivo, y su nombre esté en una lista exacta (`fondos_test`, `backlog_staging_*`). La identificación es
  positiva: el backlog vivo se llama `gestion`, así que «el nombre no es producción» no sirve. La idea de proteger por convención sobre los títulos de los tickets queda **rechazada**: una convención se
  olvida y un error tipográfico la salta.
- **Deriva entre master y el vivo (solo lectura):** `python scripts/ops/backlog_drift.py` aplica las fases de `migrate_backlog_v2.py` (en el orden en que las recibió el vivo; sin `iam` ni `revoke`) al contenedor
  desechable, toma una huella (md5 de cada rutina y vista, columnas de cada relación del esquema `backlog`) y la compara con el catálogo vivo, que solo lee. Por cada diferencia nombra la fase que define el
  objeto, es decir, el comando que el propietario debe ejecutar (primero en seco). Códigos: 0 sin deriva, 1 deriva, 2 no se pudo leer el vivo o construir master. Una fase fusionada y no aplicada (caso FND-0350)
  no la detecta nada más en el repositorio. Conviene ejecutarlo tras cada fusión que toque `db/pg_backlog/`.
- **Qué no sustituye:** un guion en preparación prueba la lógica; solo el uso vivo prueba la realidad operativa, por eso la aceptación de A-2 conserva su solape VIVO de tres operadores reales (FND-0354).

**Huecos en la numeración (2026-10-28):** los ids `FND-0339` a `FND-0349` los consumieron dos pruebas sintéticas de la compuerta ejecutadas dentro de transacciones revertidas; la secuencia `seq_backlog_fnd` no es transaccional, así que no existen tickets, logs ni eventos de esas pruebas: no es información perdida (detalle: `doc/especificacion/LIMITACIONES_CONOCIDAS_BACKLOG_V2.md`, L7). Desde esa fecha están prohibidas las pruebas sintéticas contra el backlog en vivo: se verifican en un contenedor de preparación con una copia de producción (FND-0351).

### §9.2. El vault del propietario: cadenas solo del propietario, sin escribirlas ni borrarlas (2026-11-01)

**Problema.** Las cadenas que dan DDL o escritura con el rol del propietario (`FONDOS_PG_DSN_OWNER`, `FONDOS_BACKLOG_PG_DSN_HUMAN`, `FONDOS_BACKLOG_PG_DSN_ADJUDICATOR`, `GITHUB_ADMIN_TOKEN`,
`BACKLOG_GATE_GITHUB_TOKEN`) no pueden estar en `.env`: lo carga cada sesión de agente (orden del propietario, 2026-10-29). Escribirlas y borrarlas en cada terminal es ineficiente (decisión del
propietario, 2026-11-01: «debe ser transparente y automático»).

**Solución.** `shared/owner_vault.py` y `scripts/ops/owner_vault.py`:
- **Alta, una sola vez por cadena:** `python scripts/ops/owner_vault.py set FONDOS_PG_DSN_OWNER` (entrada oculta, dos veces; exige una consola real). Se guarda cifrada con DPAPI de Windows para el usuario
  actual en `%LOCALAPPDATA%\fondos\owner_vault.json`, **fuera del repositorio**. `list` muestra solo nombres, `check` comprueba que cada valor se descifra, `remove` borra una.
- **Uso, sin ningún paso del operador:** una herramienta del propietario (la lista `OWNER_TOOLS`: las migraciones, `close_release.py`, `backlog_v3_bootstrap.py`, `gate_adjudicate.py`, `gate_mode.py`,
  `github_protect_master.py`, el estado de ciclo de los lanzadores...) recibe **solo sus variables**, en **su propio proceso**, cuando una persona la ejecuta en una **consola real** (la misma
  definición que la guarda de escritura en vivo). Al terminar el proceso desaparecen: no hay nada que borrar. Escribe en stderr los NOMBRES inyectados, nunca un valor.
- **Sin consola** (la herramienta de shell de un agente, un script, una tubería, una tarea programada) no se inyecta nada y la herramienta dice que la variable no está definida. Bajo pytest nunca.
- **Completitud:** `tests/test_owner_vault.py` falla si un script de `scripts/` lee una variable del vault y no figura en `OWNER_TOOLS`. Las cadenas `FONDOS_BACKLOG_PG_DSN_AGENT` y `_AUTOPILOT` son de
  alcance limitado, **sí** van en `.env` y no son del vault.
- **El preflight avisa** (`CAUTION`, solo los nombres) si el `.env` contiene alguna variable del vault: así se detecta una cadena que se ha colado en el fichero que cargan los agentes.

**Límite reconocido, dicho sin adornos.** Es una **guarda, no una frontera de seguridad**: las sesiones de agente corren con el mismo usuario de Windows y DPAPI descifra para ese usuario, de modo que un
código escrito a propósito para leer el fichero y llamar a `CryptUnprotectData` lo conseguiría. Lo que impide es el uso accidental o automático (el comando normal de un agente ya no alcanza una cadena
del propietario) y saca todos los secretos de los ficheros y entornos que cargan las sesiones. La frontera real sería una cuenta de Windows aparte (descartada el 2026-10-30 por desproporcionada). Una
herramienta del propietario que llame a una función de escritura sigue pasando además por la guarda de escritura en vivo (§2).
