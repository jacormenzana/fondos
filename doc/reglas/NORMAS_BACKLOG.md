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
- **Id:** `FND-NNNN`, acuñado con `SELECT 'FND-' || LPAD(nextval('backlog.seq_backlog_fnd')::text, 4, '0')`; nunca a mano.
- **Alta:** `INSERT` en `BACKLOG` con `status='OPEN'` (`schema_name`/`object_name` = nombres físicos reales) y un segundo `INSERT`
  en `BACKLOG_LOGS` que justifica la creación.
- **Flujo normativo:** `OPEN` → `TODO` → `IN_PROGRESS` (↔ `BLOCKED`) → `READY_FOR_DEPLOY` → `CLOSED`; se retrocede si falla QA.
  Empezar a trabajar o comentar pasa a `IN_PROGRESS`. **Nunca sobrescribir progreso previo:** cada avance es un `INSERT` en
  `BACKLOG_LOGS`; `update_ts` lo mantiene un trigger.
- **Cierre:** desarrollado y no desplegado → `READY_FOR_DEPLOY`. Terminado (desplegado, rechazado o duplicado) → `CLOSED` con
  `closure_ts` y `solution_type` obligatorios; en despliegue real, además `deployment_ts` y `release_version`.
- **Cierre de tickets vinculados a un defecto de auditoría (FND-0264):** un ticket enlazado a un incidente de `control.audit_incident` no se cierra hasta que el incidente esté `CERTIFIED` (re-auditoría posterior y completa) o `ACCEPTED` (residuales vigentes). `scripts/ops/close_release.py` (envoltorio de `sp_close_release`) lo aplica; ver `GOBIERNO_CONTROLES.md` §9.
- **Captura automática** (`report_incident()`, `capture_exceptions()`, `install_excepthook()`): ante una excepción sin tratar abre
  un ticket `OPEN`/`BUG`/`HIGH`/`SYSTEM_AUTODETECT` o, si ya hay uno abierto con el mismo `(project_code, object_name)` en 24 h,
  añade una nota de reincidencia. Es no-op (nunca lanza ni enmascara la excepción original) si `FONDOS_BACKLOG_PG_DSN` no está
  definida. Política de solapamiento: §7.
- **Analítica:** `backlog.vw_backlog_kpis` (lead time, despliegue, desgloses) y `backlog.sp_close_release(p_release_version)`
  (cierra en bloque los `READY_FOR_DEPLOY` de una release, fija `deployment_ts`, deja log).

## §3. Relaciones entre tickets

Un enlace va **de → a** (una dirección por par), con uno de seis tipos:

| Tipo | Significa |
|---|---|
| `DEPENDS_ON` | `from` no puede terminarse/validarse antes que `to` |
| `RELEASE_WITH` | ambos deben activarse en la misma release |
| `OVERLAPS` | mismo objeto de código, columna, interruptor o señal: un cambio en uno cambia el otro (`absorbs` dice qué alcance) |
| `DUPLICATES` | `from` duplica a `to` (`to` es el que se conserva) |
| `SPLIT_FROM` | `from` se separó de `to` |
| `RELATED` | solo contexto |

**Almacén:** una tabla **append-only** `backlog.backlog_link_event` — cada propuesta, confirmación, rechazo o recalificación es
una fila nueva (quién, cuándo, evidencia, `absorbs`); nada se actualiza ni se borra. Estados de evento: `PROPOSED`, `CONFIRMED`,
`REJECTED`. El estado **vigente** de un par es su último evento: vista `backlog.v_backlog_link`
(`ORDER BY event_ts DESC, event_id DESC`, desempate determinista — salvaguarda S-3). `backlog.v_open_link_graph` lista los
enlaces confirmados que tocan algún ticket abierto y marca `blocked_by_open` (un `DEPENDS_ON` cuyo prerrequisito sigue abierto).
No hay trigger que bloquee cambios de estado: pasar de `BLOCKED` a `IN_PROGRESS` es una decisión deliberada, la herramienta solo avisa.

El DDL **lo aplica el propietario** (`python scripts/ops/migrate_backlog_links.py --apply`; simulacro por defecto). Hasta
entonces las herramientas funcionan en solo lectura y avisan «almacén sin aplicar».

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

Estado de aplicación: son reglas de procedimiento. Las herramientas que las verifican (revisión de caducidad de estados y de
decisiones pendientes, barrido de premisas en cada liberación, filtro de población en el informe de ciclo) están pendientes en
FND-0323; hasta entonces las comprueba quien abre, empieza o cierra el ticket.

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
| Puerta de la política automática | `python scripts/audit/backlog_links_calibration.py --gate` |

Salidas en `out/reports/backlog_links/` (ignorado por git). Tests: puros (`test_backlog_links_*.py`, `test_backlog_overlap.py`,
`test_backlog_links_calibration.py`) y herméticos PG (`python scripts/ops/run_pg_tests.py -- tests/test_backlog_links_ddl_pg.py
tests/test_backlog_links_client_pg.py tests/test_backlog_incident_overlap_pg.py`).
