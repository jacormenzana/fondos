# NORMAS BATCH — Criterio único para los lanzadores `.bat`

**Propósito:** un único criterio, modular y comprobable, para todo `.bat` que se cree o se toque en este repo.
**Implementación de referencia:** `scripts/launch/lib/common.bat` (subrutinas) y `scripts/launch/P1P2_runCycle.bat`
(orquestador). **Plantilla de arranque:** `scripts/launch/_template.bat`. **Cumplimiento:** `tests/test_batch_standards.py`
(estático, sobre todos los `.bat`) y `tests/test_batch_launchers_e2e.py` (el orquestador ejecutado contra lanzadores simulados).
**Lectura obligatoria** antes de crear o modificar un `.bat`.

---

## §1. Principio

Un `.bat` es **pegamento fino**: secuencia pasos, propaga códigos de salida y escribe el log. Todo lo que se pueda
decidir (estado, reanudación, validaciones, informes, SQL) vive en **Python con tests** (`p1p2_state.py`,
`p1p2_cycle_report.py`, …) y el `.bat` solo lo invoca. Lo que se repite en dos lanzadores va a `lib/common.bat`
(es el principio P#11/DRY aplicado a batch). Un lanzador nuevo se crea **copiando `_template.bat`**, no otro lanzador.

## §2. Esqueleto obligatorio

```bat
@echo off
setlocal EnableExtensions EnableDelayedExpansion
call "%~dp0lib\common.bat" :init "%~nx0" & set "RC_BOOT=!ERRORLEVEL!"
if %RC_BOOT% NEQ 0 (endlocal & exit /b %RC_BOOT%)
call "%COMMON%" :utf8_on
:: ... cabecera de documentacion (§3), configuracion, argumentos, ejecucion ...
call "%COMMON%" :utf8_off
endlocal & exit /b %RC%
```

- Las cinco primeras líneas son idénticas en todos los lanzadores de `scripts/launch`. `:init` devuelve su propio código
  (`101` intérprete, `106` driver PostgreSQL) y el lanzador sale con él (`RC_BOOT`); la segunda línea se parsea ya con `RC_BOOT` fijado.
- **Guarda de entorno (`RC_ENV_BLOCKED = 106`).** `:init` comprueba que `psycopg` carga en el intérprete (el control de
  aplicaciones de Windows puede bloquear su DLL y, sin guarda, cada herramienta muere a mitad con un traceback ajeno).
  Una vez por árbol de procesos (`FONDOS_DB_DRIVER_OK`) y recordado 10 min entre ejecuciones (`proyecto1/log/env_driver_ok`);
  un entorno bloqueado nunca se recuerda. Registro local `proyecto1/log/env_blocked.log` (tope 1 MB: se trunca). La capa Python
  El bloqueo puede ser TRANSITORIO (Smart App Control bloquea un binario sin reputación conocida y lo libera al resolverla,
  medido una vez el 2026-10-12: ~32 min; diagnostico en `scripts/audit/preflight_check.py` seccion [0], evento CodeIntegrity
  3077). Sin reintento igualmente: relanzar mas tarde es legitimo, pero el lanzador sigue fallando rapido, nunca esperando.
  (`shared/env_guard.py::require_db_driver()`, usada por `run_pg_tests.py`, los `migrate_*.py` y el preflight de
  `p1p2_state.py`) hace siempre el import real y no usa el marcador. Sin reintentos, sin otro intérprete, sin DLL copiada.
- Solo ASCII (los mensajes van sin acentos) y finales de línea **CRLF** (los editores y `Write` generan LF: convertir
  siempre; un `.bat` con LF falla en `cmd.exe`).
- `endlocal & exit /b %RC%` va en **una sola línea**: `endlocal` descarta la expansión retardada, y `%RC%` se sustituye
  al parsear, con el ámbito aún activo. En dos líneas el lanzador devuelve 0 siempre.

## §3. Cabecera de documentación

Bloque `::` inmediatamente tras el esqueleto con: qué hace (una línea), **`Uso:`** con todas las opciones, y los
**códigos de salida**. Es contrato, no historial: los números de ticket y la historia van al `git log` y a
`AGENTS.md`. Una cabecera de más de ~60 líneas es señal de que la lógica debería estar en Python.

**`-h` / `--help` nunca ejecuta nada.** TODO lanzador (la plantilla incluida) reconoce `-h` y `--help` ANTES de cualquier trabajo: imprime su uso
y sale con RC 0, sin abrir log ni invocar ninguna herramienta. Los lanzadores con tabla de opciones lo hacen en su análisis de argumentos
(`:parse` → `:help`); los que reenvían sus argumentos a una ejecución real (`AUDIT_*`, `P1_refreshBenchmarks`, `P2_calculateIndicators`,
`P3_*`, `P1_diagCost`, `P1_discoverAllFundsPlusCostDiag`) llevan un bucle `for %%A in (%*)` al principio y `:show_help` al final, que imprime
**la propia cabecera** del fichero (`lib\batch_helpers.py usage`): una sola fuente, la que este apartado ya exige. Motivo (2026-10-08): una
sonda `--help` sobre los lanzadores reales arrancó ejecuciones reales (uno de ellos, la ruta heredada por bloques con Excel maestro, reescribió
37 filas de `fund_master`). `tests/test_batch_standards.py` exige ambos literales en el código de cada lanzador y
`tests/test_batch_launchers_e2e.py` demuestra, para cada uno y para ambas banderas, que no corre ninguna herramienta ni crea logs ni directorios.
**Y nunca se sondea un lanzador con argumentos sobre el repositorio real**: solo en el árbol simulado del e2e.

## §4. Rutas y entorno — nada fijo

| Qué | Cómo |
|---|---|
| Raíz del repo | `%ROOT%` (la fija `:init` desde la ubicación de la librería; funciona si el repo se mueve) |
| Intérprete | `"%PYTHON%"` siempre entrecomillado; **nunca** `python` desnudo (en Windows resuelve al shim de WindowsApps) |
| Otros lanzadores / scripts | `%LAUNCH%\X.bat`, `%ROOT%\ruta\script.py` |
| Rutas de DATOS fuera del repo | solo en un `set "NOMBRE=C:\..."` al principio (bloque de configuración), nunca en línea dentro de un comando; si deben poder cambiarse, con una variable de entorno (`if defined P1P2_AUDIT_DIR set ...`) |
| Versiones, fechas, sellos de baseline, recuentos | **nunca literales**: se leen del sistema (`p1p2_state.py calc-version`, `git rev-list --count`, …) o se generan por ejecución (`:get_time`) |

Prohibido en líneas no comentadas: `C:\desarrollo`, `C:\data\envs`, literales de 8 dígitos tipo fecha/versión.
`FONDOS_PYTHON` cambia el intérprete (pruebas); `FONDOS_ORCH=1` marca «hay un orquestador».

## §5. Argumentos

- Tabla de opciones: `BOOL_OPTS` (`--x-y` → `FLAG_x_y=1`) y `UINT_OPTS` (`--x-y N` → `OPT_x_y=N`, entero positivo
  validado con `:is_uint`). Añadir una opción = añadirla a la tabla **y** a `:usage_text` (el test lo comprueba).
- `-h` / `--help` imprimen el uso y salen con 0. Lo **desconocido se rechaza** (RC 100): un error tipográfico no
  debe lanzar un proceso de horas con otras opciones.
- Combinaciones sin sentido (`--no-x` con `--x-y`) se rechazan en el parseo, antes de tocar nada.
- Opciones compartidas entre lanzadores (el ciclo P1+P2 lo es de `P1P2_runCycle` y de `P1P2P3_runCycle`): **una sola tabla** en un fichero de
  datos `lib/p1p2_options.bat`, que ambos incluyen; el integrador las reenvía sin duplicarlas.
- Reenvío al sub-proceso: `-- args` (todo lo que sigue va tal cual). Se acumula **sin comillas externas**
  (`set EXTRA=!EXTRA! %1`) para que un argumento ya entrecomillado (`"A,B"`) conserve sus comillas.
- Listas con comas van entre comillas (cmd parte los argumentos por comas). Los argumentos se leen con `%~1`.

## §6. Códigos de salida

| Rango | Quién | Ejemplos |
|---|---|---|
| `0` | éxito | |
| `1-99` | **la herramienta que falló**, propagada sin tocar | `3` benchmark loader, `5/6` puerta del harvest, `2` frescura de P3 |
| `100-199` | **el propio lanzador** (constantes `RC_*` de `:init`) | `100` argumentos inválidos, `101` intérprete no encontrado, `102` reanudación rechazada, `103` estado no escribible, `104` preflight fallido, `105` otra instancia en ejecución, `106` driver PostgreSQL bloqueado, `107` baseline (auditoría PRE) no válido |

Así un código propio **nunca** coincide con el de una herramienta (antes un harvest fallido, RC 5, era
indistinguible de «intérprete no encontrado»). No se escriben literales 1-99 en `exit /b`. Las constantes de
`common.bat` y las de `p1p2_state.py` son las mismas (el test lo comprueba).

## §7. Log

Un log por ejecución: `:log_open DIR TAG TITULO` crea `DIR\log_TAG_STAMP.log` (stdout) y `…_err.log` (stderr) y
escribe la cabecera; `:log_close TITULO RC` el pie. El orquestador anota además el log de detalle de cada paso.
Sellos de tiempo con `:get_time` (la calcula Python, `lib/batch_helpers.py`: independiente del idioma, sin `wmic` —ya no
existe— y **sin PowerShell**). Ultimas lineas de un log: `:tail FICHERO N`.

## §8. Efectos globales — se guardan y se restauran

- **Suspensión (standby AC):** `:standby_disable owner` lee y guarda el valor **real**, lo pone a 0 y
  `:standby_restore owner` lo devuelve. Nunca se restaura a un valor escrito a mano. El valor guardado sobrevive a
  una interrupción (Ctrl+C): la ejecución siguiente no lo pisa y su restore recupera el original. Solo el lanzador
  más externo la gestiona: si `FONDOS_ORCH` ya está definida hay un propietario por encima y el resto no hace nada
  (`STANDBY_OWNED`, que reinicia cada `:standby_disable`, decide quién restaura). `owner` solo exporta `FONDOS_ORCH` a lo que se invoca.
- **Página de códigos:** `:utf8_on` / `:utf8_off` (restaura la que había al entrar).
- **PATH, variables de entorno persistentes:** no se tocan. Todo cambio es del `setlocal` del lanzador.
- **PowerShell: prohibido en los `.bat`.** Costaba ~0,3 s por llamada, solo servia para sacar la hora, y el 2026-10-04 se
  colgo en esta maquina (incluso `powershell -Command 1+1` no volvia): al estar en el camino de todos los lanzadores, los
  colgo a todos. Lo que antes hacia PowerShell vive en `lib/batch_helpers.py` (arranca en ~30 ms, tiene tests unitarios).

## §9. Instancia única

Los lanzadores de **nivel superior** que escriben estado o recalculan (`P1_runCycle`, `P2_runCycle`, `P3_runCycle`, `P1P2_runCycle`, `P2P3_runCycle`, `P1P2P3_runCycle`, y el de la
release `FND-0246`, que encadena P1 -> P2 -> P3) comparten un
bloqueo: `call :cycle 9>"%LOCK_FILE%"` mantiene un manejador abierto mientras corre `:cycle`; una segunda instancia no
puede abrirlo, `:cycle` no se ejecuta (`LOCK_HELD` sin definir) y sale con RC 105. Lo cierra el sistema aunque el proceso
muera: **no hay bloqueos huérfanos que limpiar**. Los sub-lanzadores **no** se bloquean (heredarían el manejador del padre). Un lanzador de nivel superior **anidado** bajo otro
(`P1P2P3_runCycle` invoca a `P1P2_runCycle`) no vuelve a pedirlo: el externo exporta `FONDOS_CYCLE_LOCK=1` y el interno ejecuta
`:cycle` sin la redirección (`if defined FONDOS_CYCLE_LOCK (call :cycle) else (call :cycle 9>"%LOCK_FILE%")`).
El test estático exige ese patrón en los lanzadores de nivel superior (`TOP_LEVEL_LOCKED` en `tests/test_batch_standards.py`).

## §10. Estado y reanudación

Procesos con varios pasos persisten su estado con un helper Python (`p1p2_state.py`: escritura atómica, guarda de
`--from N`, falla cerrado). La numeración de pasos es **estable** (estado, tests y documentación dependen de ella): una
fase nueva se añade como previa/posterior, no renumerando. Un fallo de preflight no escribe estado.

## §11. Trampas de `cmd.exe` verificadas en este repo

| Trampa | Regla |
|---|---|
| `shift` desplaza también `%0` | capturar `%~dp0` **antes** del `shift` (lo hace el despachador de `common.bat`) |
| `for /f ... in ('"a" "b" c')` con dos rutas entre comillas falla si hay espacios | doble comilla exterior: `('""%PYTHON%" "%X%\s.py" arg"')` (`:state_query`) |
| `4>>` / `2>>` pegado a un número es una redirección de manejador | separar con un espacio: `echo %* >> log` |
| `)` dentro de un bloque `( … )` cierra el bloque | escapar `^)` en `echo` dentro de bloques |
| `set "X=a "b""` con comillas internas | para valores ya entrecomillados usar `set X=...` sin comillas externas |
| `%ERRORLEVEL%` dentro de un bloque se expande al parsear el bloque | usar `!ERRORLEVEL!` (expansión retardada) |
| `!` en argumentos con expansión retardada activa | evitar `!` en lo reenviado, o no pasarlo por variables |
| `powershell` consume la entrada redirigida y puede colgarse | no se usa (§8): hora y `tail` por `batch_helpers.py` |
| `exit` / `endlocal` desde una subrutina llamada con `call :etiqueta 9>fichero` | la subrutina termina con `exit /b`; solo el principal hace `endlocal & exit /b` |
| `endlocal & exit /b !X!` devuelve 0 | `endlocal & exit /b %X%` en una línea |
| `find` / `sort` a secas resuelven a las de GNU si cmd se lanza desde Git Bash (`find` recorre el disco: 100 % CPU, cuelgue) | rutas explícitas: `"%WINFIND%"` (la fija `:init`); `findstr` y `type` son seguros |
| `cmd /c "ruta" "arg entrecomillado"` quita las comillas equivocadas (más de dos comillas en la línea) | desde un proceso externo: `cmd /s /c ""ruta" "arg""` |
| una herramienta que escribe el `.bat` convierte `\f`, `\t` en una ruta en caracteres de control | el test estático rechaza caracteres de control; revisar con `grep -P` |
| `exit /b %RC_USAGE%` dentro de un bloque `( )` ANIDADO del bucle de argumentos (`if` > `if` con `shift`/`goto`) devolvio 0 (P3_generateReport, 2026-10-11, verificado en el arbol simulado) | salir desde una etiqueta aparte: `if "%~2"=="" goto :bad_args` y `:bad_args` = mensaje + `call "%COMMON%" :utf8_off` + `endlocal & exit /b %RC_USAGE%` |
| `goto :etiqueta` a una etiqueta inexistente termina el batch | las subrutinas se despachan con etiquetas conocidas (`common.bat`) |
| Escribir `.bat` con una herramienta que genera LF | convertir a CRLF y verificar con `file` |

## §12. Pruebas

0. **Unitario** (`tests/test_batch_helpers.py`): la lógica de `batch_helpers.py` (formatos de hora, lectura del standby, `tail`).
1. **Estático** (`tests/test_batch_standards.py`): recorre todos los `.bat` de `scripts/launch` y `scripts/launch/lib`
   y hace cumplir §2, §4, §5, §6, §8 (CRLF, ASCII, esqueleto, sin rutas/versiones fijas, `python` entrecomillado desde
   `%PYTHON%`, `powercfg`/`chcp`/`powershell`/`wmic` solo en la librería (o nunca), sin literales de salida 1-99, uso documentado). Un lanzador
   **nuevo** no puede entrar con excepciones; las existentes se listan con motivo en el propio test.
2. **De extremo a extremo** (`tests/test_batch_launchers_e2e.py`): construye en un directorio temporal una copia del repo
   mínimo con **todos** los lanzadores y scripts simulados (sin BD, sin red, sin `powercfg` real) y ejecuta el orquestador:
   éxito, fallo, reanudación, bloqueo, restauración del standby. **Nunca** se prueba un lanzador ejecutando una copia que
   tenga rutas fijas: arrancaría los lanzadores reales (incidente del 2026-10-04); por eso §4 es obligatoria.
3. Antes de dar un `.bat` por terminado: `file X.bat` (CRLF, ASCII), `--help`, un argumento inválido (RC 100) y la
   suite de los dos tests anteriores.

## §13. Lista de comprobación previa al commit

- [ ] Copiado de `_template.bat` (o cambio sobre un lanzador conforme).
- [ ] Sin rutas del repo/intérprete, versiones, fechas ni recuentos escritos a mano.
- [ ] Códigos propios solo `RC_*` (100+); los de las herramientas se propagan.
- [ ] `--help`, tabla de opciones y `:usage_text` en sincronía; desconocido → RC 100.
- [ ] Efectos globales (standby, codepage) vía librería y restaurados.
- [ ] Lógica decidible en Python con test; el `.bat` solo la invoca.
- [ ] `tests/test_batch_standards.py` y `tests/test_batch_launchers_e2e.py` en verde.
- [ ] Nombre conforme a §15; si es un orquestador, auditorías PRE/POST por `lib\cyclePhases.bat` (§14) y en `tests/test_batch_cycle_audits_e2e.py`.

---

## §14. Auditoría en los orquestadores (PRE congelado, POST con deriva)

**Quién audita.** Solo los orquestadores `*_runCycle`; los lanzadores de paso (`P1_discoverAllFunds`, `P2_calculateIndicators`, …) **no auditan
por defecto** (opt-in `--audit`, modo report sin persistir; `--no-audit` se acepta por compatibilidad). Un `post` suelto, sin `pre` con el que
compararlo, no es evidencia.

**Qué y cuándo** (la lógica vive una sola vez en `scripts/launch/lib/cyclePhases.bat`, variable `PHASES`; los orquestadores no copian llamadas a `P1_audit`/`P2_audit`):

| Fase | Auditoría | Corrida |
|---|---|---|
| Antes de tocar nada | PRE de cada dominio que el flujo va a escribir (P1: costes + benchmarks B1-B7; P2: métricas) | `pre_<sello>_costs`, `pre_<sello>_p2` |
| Al salir del último escritor de P1 (paso 2) | POST P1 `--compare-to` su PRE | `post_<sello>_costs` |
| P2: **una sola vez**. Con P3 en el flujo, tras el DRAFT (`P3_buildPortfolio --audit-compare-to <pre>_p2`): es a la vez la deriva y la evidencia de promoción | POST P2 `--compare-to` su PRE | `post_<sello>_p2` |

- **Una auditoría completa de P2 por ciclo**, no dos: `P1P2_runCycle --defer-post-audit` omite la suya cuando P3 va a hacerla; si P3 no llega a auditar
  (falla antes, `--p3-dry-run`), `P1P2P3_runCycle` hace una de respaldo al terminar. Nada de esto cambia la puerta de promoción (FND-0306).
- **PRE válido o nada (RC 107).** Si un PRE falla o sale INCOMPLETO (RC 2: un chequeo obligatorio no pudo ejecutarse) no se ejecuta ningún paso que
  escriba: `RC_BASELINE = 107`, mismo número en `common.bat` y `p1p2_state.py` (lo comprueba el test). `--allow-no-baseline` lo avisa y sigue (la deriva
  de ese ciclo no será evidencia). Un POST que falla es informativo: nunca cambia el RC.
- **El PRE no se repite al reanudar** (`--from N > 1` reutiliza el baseline del ciclo fallido, `p1p2_state.py`); una reanudación desde el paso 3 no repite
  el POST de P1, que ya hizo la corrida que completó el paso 2. Un fallo del PRE se reanuda con `--from 1` (toma un PRE nuevo).
- **Dónde quedan los ficheros de una auditoría:** dentro del repo, **una carpeta por proyecto**: `out\audit\P1\` (benchmarks B1-B7) y `out\audit\P2\`
  (snapshots de betas macro `macro_betas_*.csv` y outliers `beta_shift_outliers_*.csv`). Las define una sola vez `lib\common.bat` (`AUDIT_P1_DIR`,
  `AUDIT_P2_DIR`; la variable `P1P2_AUDIT_DIR` mueve la raíz `out\audit`, p. ej. en las pruebas) y, para Python, `shared.config.AUDIT_DIRS`. Nunca bajo `C:\data`.
  Los logs de las auditorías siguen el registro de logs de cada proyecto (`LOG_DIRS`).
- **Benchmarks B1-B7:** `P1_audit --run-id X` guarda además `out\audit\P1\benchmark_audit_X.json` (pre y post son comparables) y refresca
  `benchmark_audit_findings.json` (el `latest` que leen las skills).
- Los orquestadores parciales (`P1_runCycle`, `P2_runCycle`, `P3_runCycle`) **no** escriben el estado del ciclo P1+P2 (`P1P2_runCycle.state`): un ciclo
  parcial no puede dar por bueno `P1P2_runCycle --from N` ni `P1P2P3_runCycle --only-p3`.

## §15. Nombres

`[Prefijo]_[primeraPalabra][SegundaPalabra]...bat`, camelCase con la primera palabra en minúscula. El prefijo es el proyecto (`P1`, `P2`, `P3`) o la
**concatenación** de los que abarca el flujo, en orden (`P1P2`, `P2P3`, `P1P2P3`); sin guiones bajos dentro del prefijo.

| Rol | Nombre |
|---|---|
| Orquestador (ciclo con auditoría PRE/POST) | `P1_runCycle`, `P2_runCycle`, `P3_runCycle`, `P1P2_runCycle`, `P2P3_runCycle`, `P1P2P3_runCycle` |
| Auditoría de un dominio | `P1_audit`, `P2_audit` (y `P1P2_auditStatistical` para el lanzador genérico por dominio) |
| Paso | `P1_harvestFunds`, `P1_refreshBenchmarks`, `P1_discoverAllFunds`, `P2_discoverLoadMetrics`, `P2_calculateIndicators`, `P3_buildPortfolio`, `P3_generateReport` |

Los números de ticket no van en el nombre de un lanzador permanente (los `FND-0246*` son lanzadores puntuales de una release: se retiran al cerrarla).
Renombrar un lanzador exige actualizar sus referencias en el mismo commit (documentación, pruebas, fichero de estado, etiquetas de log) y regenerar el
sentinel `launchers` de `AGENTS.md` (`scripts/audit/sync_agents_md.py --write`).

---

*Creado 2026-10-04. Origen: revisión de `P1P2_runCycle.bat` como lanzador de referencia (instancia única, standby real,*
*códigos de salida sin colisión, rutas derivadas) y de los valores prefijados que `P2P3_runCycle.bat` arrastraba.*
