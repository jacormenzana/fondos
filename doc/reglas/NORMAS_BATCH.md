# NORMAS BATCH — Criterio único para los lanzadores `.bat`

**Propósito:** un único criterio, modular y comprobable, para todo `.bat` que se cree o se toque en este repo.
**Implementación de referencia:** `scripts/launch/lib/common.bat` (subrutinas) y `scripts/launch/P1_P2_Complete.bat`
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
call "%~dp0lib\common.bat" :init || (endlocal & exit /b 101)
call "%COMMON%" :utf8_on
:: ... cabecera de documentacion (§3), configuracion, argumentos, ejecucion ...
call "%COMMON%" :utf8_off
endlocal & exit /b %RC%
```

- Las cuatro primeras líneas son idénticas en todos los lanzadores de `scripts/launch`.
- Solo ASCII (los mensajes van sin acentos) y finales de línea **CRLF** (los editores y `Write` generan LF: convertir
  siempre; un `.bat` con LF falla en `cmd.exe`).
- `endlocal & exit /b %RC%` va en **una sola línea**: `endlocal` descarta la expansión retardada, y `%RC%` se sustituye
  al parsear, con el ámbito aún activo. En dos líneas el lanzador devuelve 0 siempre.

## §3. Cabecera de documentación

Bloque `::` inmediatamente tras el esqueleto con: qué hace (una línea), **`Uso:`** con todas las opciones, y los
**códigos de salida**. Es contrato, no historial: los números de ticket y la historia van al `git log` y a
`AGENTS.md`. Una cabecera de más de ~60 líneas es señal de que la lógica debería estar en Python.

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
- Opciones compartidas entre lanzadores (el ciclo P1+P2 lo es de `P1_P2_Complete` y de `P1_P2_P3`): **una sola tabla** en un fichero de
  datos `lib/p1p2_options.bat`, que ambos incluyen; el integrador las reenvía sin duplicarlas.
- Reenvío al sub-proceso: `-- args` (todo lo que sigue va tal cual). Se acumula **sin comillas externas**
  (`set EXTRA=!EXTRA! %1`) para que un argumento ya entrecomillado (`"A,B"`) conserve sus comillas.
- Listas con comas van entre comillas (cmd parte los argumentos por comas). Los argumentos se leen con `%~1`.

## §6. Códigos de salida

| Rango | Quién | Ejemplos |
|---|---|---|
| `0` | éxito | |
| `1-99` | **la herramienta que falló**, propagada sin tocar | `3` benchmark loader, `5/6` puerta del harvest, `2` frescura de P3 |
| `100-199` | **el propio lanzador** (constantes `RC_*` de `:init`) | `100` argumentos inválidos, `101` intérprete no encontrado, `102` reanudación rechazada, `103` estado no escribible, `104` preflight fallido, `105` otra instancia en ejecución |

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

Los lanzadores de **nivel superior** que escriben estado o recalculan (`P1_P2_Complete`, `P2_P3_complete`) comparten un
bloqueo: `call :cycle 9>"%LOCK_FILE%"` mantiene un manejador abierto mientras corre `:cycle`; una segunda instancia no
puede abrirlo, `:cycle` no se ejecuta (`LOCK_HELD` sin definir) y sale con RC 105. Lo cierra el sistema aunque el proceso
muera: **no hay bloqueos huérfanos que limpiar**. Los sub-lanzadores **no** se bloquean (heredarían el manejador del padre). Un lanzador de nivel superior **anidado** bajo otro
(`P1_P2_P3` invoca a `P1_P2_Complete`) no vuelve a pedirlo: el externo exporta `FONDOS_CYCLE_LOCK=1` y el interno ejecuta
`:cycle` sin la redirección (`if defined FONDOS_CYCLE_LOCK (call :cycle) else (call :cycle 9>"%LOCK_FILE%")`).
El test estático exige ese patrón en los tres lanzadores de nivel superior.

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

---

*Creado 2026-10-04. Origen: revisión de `P1_P2_Complete.bat` como lanzador de referencia (instancia única, standby real,*
*códigos de salida sin colisión, rutas derivadas) y de los valores prefijados que `P2_P3_complete.bat` arrastraba.*
