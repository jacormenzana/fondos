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

**IAM (FND-0354, `db/pg_backlog/50_iam.sql`, fase `iam`; runbook `doc/operativos/BACKLOG_IAM_ROLLOUT.md`):** un login por identidad. `backlog_reader` (solo lectura: la única identidad que una sesión de agente conserva en `.env`), `backlog_logger` y `backlog_incident` (arriba) y un login por operador (`human:a.gomez_devops`, `human:j.cormenzana_data`, `human:j.cormenzana_owner`, `human:m.ruiz_qa`; `human:jacor` sigue como alias de la historia). `backlog.actor_login_event` (solo añade) liga login e identidad; `fn_actor_bound`, llamada por `fn_actor_authorized`, hace que un login ligado escriba SOLO como su identidad y que una identidad humana solo se acepte desde un login ligado a ella. Cada vinculación declara quién la hace: `sp_bind_login` exige la identidad humana canónica declarante (`p_declared_by`, guardada en `declared_by`; un login ligado solo puede declarar la suya) y `backlog.v_binding_audit` marca `SELF_BINDING`, `DECLARER_NOT_BOUND_TO_SET_BY`, `LEGACY_NO_DECLARER` y `RECENT` (últimas 24 h; el pre-flight imprime una línea `[ATENCION]` por cada una reciente). Varios logins de una persona cuentan como UNA persona (`person` es un CHECK de la base de datos, no texto libre: exactamente `human:a.gomez`, `human:j.cormenzana` o `human:m.ruiz`, y la identidad ligada debe ser suya). Un agente nunca cuenta como operador. El login compartido `backlog_writer` se retira después, por el propietario: `backlog_phase_a_acceptance.py --verify retire` falla mientras algún login pueda abrir o mover tickets sin estar ligado, o mientras menos de TRES personas distintas ligadas hayan hecho transiciones reales (reserva A-2). Lo «criptográfico» es el certificado de cliente TLS de cada login (`pg_hba.conf`, DevOps); el cliente pasa `sslmode`, `sslcert`, `sslkey` y `sslrootcert` de la cadena de conexión sin tocarlos. Los nombres históricos de
  `BACKLOG_LOGS` no se reescriben: `backlog.actor_alias` los traduce (`python -m shared.backlog_client --check-actors` debe dar 0).
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
| **Cada ciclo P1+P2** | Una línea en el informe de ciclo (`Relaciones del backlog`) y un `[ATENCION]` si hay tickets en curso con prerrequisito `DEPENDS_ON` abierto | `p1p2_cycle_report.py` |

### §4.1. Higiene del registro (origen: FND-0323)

Un ticket deja de ser fiable cuando su texto, su estado o sus cifras divergen del sistema. Reglas de procedimiento:

1. **Condición de cierre → entrada de salida.** Cuando un log declara una condición de cierre («cerrar tras el próximo ciclo con deriva
   0», «sigue abierto hasta que el pase P1 escriba los 14»), la entrada que la cumple se escribe **en la misma sesión que la
   observa**, con el recuento y su fecha. Todo recuento en un log lleva **fecha y población**.
2. **Estado sincronizado.** Un `BLOCKED` por instrucción del propietario nombra la condición que lo levanta; al cumplirse (p. ej.
   la liberación) el estado se mueve en esa sesión. Los tickets `RELEASE_WITH` o acoplados en código se cierran como **una sola
   acción**. Una decisión pendiente del propietario se registra como «DECISION PENDIENTE» con fecha, para que la revisión periódica
   la liste.
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

Salidas en `out/reports/backlog_links/` (ignorado por git). Tests: puros (`test_backlog_links_*.py`, `test_backlog_overlap.py`,
`test_backlog_links_calibration.py`) y herméticos PG (`python scripts/ops/run_pg_tests.py -- tests/test_backlog_links_ddl_pg.py
tests/test_backlog_links_client_pg.py tests/test_backlog_incident_overlap_pg.py`).

**Huecos en la numeración (2026-10-28):** los ids `FND-0339` a `FND-0349` los consumieron dos pruebas sintéticas de la compuerta ejecutadas dentro de transacciones revertidas; la secuencia `seq_backlog_fnd` no es transaccional, así que no existen tickets, logs ni eventos de esas pruebas: no es información perdida (detalle: `doc/especificacion/LIMITACIONES_CONOCIDAS_BACKLOG_V2.md`, L7). Desde esa fecha están prohibidas las pruebas sintéticas contra el backlog en vivo: se verifican en un contenedor de preparación con una copia de producción (FND-0351).
