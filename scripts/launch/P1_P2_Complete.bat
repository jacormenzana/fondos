@echo off
setlocal EnableExtensions EnableDelayedExpansion
call "%~dp0lib\common.bat" :init || (endlocal & exit /b 101)
call "%COMMON%" :utf8_on

:: ============================================================
:: P1_P2_Complete.bat -- Ciclo completo P1 + P2 (lanzador de REFERENCIA: ver NORMAS_BATCH.md)
::
:: FASE PREVIA (solo ciclo nuevo, salvo el preflight):
::   P.  preflight                  BD alcanzable, DSN owner, FRED_API_KEY, espacio en disco
::   0.  auditoria baseline         AUDIT_P1/AUDIT_P2 --persist + punto de restauracion
::   0b. snapshot de betas macro    beta_shift_audit.py --snapshot (para comparar tras P2)
::   0c. harvest                    P1_harvestFunds.bat: catalogo DB, informe codSus, puerta, delta KIID
:: PASOS (numeracion estable: la usan --from y el fichero de estado):
::   1. P1_refreshBenchmarks.bat    refresh benchmarks Morningstar
::   2. P1_discoverAllFunds.bat     pipeline P1 (mark_stale + classify + family + export)
::   3. P2_discoverLoadMetrics.bat  macro (INE/BCE/FRED/Eurostat) + NAV discover/load
::   4. P2_calculateIndicators.bat  metricas cuantitativas P2 + export
:: FASE POSTERIOR (solo si el ciclo termina OK; nunca cambia el RC ni el estado):
::   auditoria final con deriva, comparacion de betas, frescura de datos para P3, informe de ciclo
::   y, a peticion, shadow reconciliation, cobertura de benchmarks, dashboard y diagnostico de costes.
::
:: Aborta en el primer paso que devuelva RC != 0. Cada sub-lanzador escribe su propio log; este
:: escribe el log de orquestacion (RC y log de detalle de cada paso).
::
:: Uso:  P1_P2_Complete.bat [opciones] [-- argumentos_para_run_pipeline]      (-h / --help: lista completa)
::   --from N [--from-any]  reanudar en el paso N (1-4) tras un fallo; --from-any anula la guarda
::   --skip-preflight       no ejecutar el preflight
::   Pasos:    --benchmarks-load (PASO 1: recarga completa) | --no-export (PASO 2 y 4) |
::             --skip-macro, --workers N (PASO 3) | --force (PASO 4) | -- args (PASO 4, a run_pipeline)
::   Harvest:  --no-harvest | --harvest-sync | --harvest-retire | --harvest-limit N |
::             --harvest-max-drop-pct N
::   Opcional: --shadow N | --benchmark-gaps | --dashboard | --diag-cost    (fase posterior)
::   Las listas con comas van ENTRE COMILLAS (cmd parte los argumentos por comas).
::   Los argumentos desconocidos se rechazan: un error tipografico no debe lanzar un ciclo de horas.
::
:: Diseno (detalle y motivos: AGENTS.md, "Cycle phases around the four steps"):
::   - Preflight: un fallo de BD aborta SIN escribir estado (no debe pisar el paso fallido que se
::     quiere reanudar). Un fallo del harvest cuenta como fallo del paso 1.
::   - Reanudacion: la guarda y el estado viven en p1p2_state.py (testeable); --from N solo se
::     permite si el ultimo ciclo fallo en N o despues; sin estado legible se rechaza.
::   - Baseline y snapshot de betas se reutilizan al reanudar (--from N > 1).
::   - Instancia unica: un segundo ciclo (o P2_P3_complete.bat) se rechaza con RC 105 mientras
::     otro corre; el bloqueo es un manejador abierto, asi que lo libera el sistema aunque el
::     proceso muera.
::   - Suspension: se guarda y restaura el valor REAL (lib\common.bat :standby_*).
::   - Los diagnosticos de la fase posterior son informativos.
::
:: Codigos de salida: 0 OK | 100 argumentos invalidos | 101 interprete no encontrado |
::   102 --from rechazado por la guarda | 103 estado no escribible | 104 preflight fallido |
::   105 otra instancia en ejecucion | 1-99 = RC propagado del paso (o del harvest previo,
::   que cuenta como paso 1) que fallo. Los propios nunca coinciden con los de las herramientas.
:: ============================================================

:: ------------------------------------------------------------
:: CONFIGURACION
:: ------------------------------------------------------------
set "LOG_DIR=%STATE_DIR%"
set "DATA_AUDIT_DIR=C:\data\fondos\audit"
if defined P1P2_AUDIT_DIR set "DATA_AUDIT_DIR=%P1P2_AUDIT_DIR%"
set "LOCK_FILE=%STATE_DIR%\fondos_cycle.lock"
set "STEP_TOTAL=4"

:: Tabla de opciones (BOOL_OPTS / UINT_OPTS): compartida con P1_P2_P3.bat, que las reenvia.
call "%LAUNCH%\lib\p1p2_options.bat"

:: ------------------------------------------------------------
:: ARGUMENTOS
:: ------------------------------------------------------------
set "P2_EXTRA="
set "FROM_STEP=1"
set "FROM_GIVEN="
set "FROM_ANY="
for /f "delims==" %%V in ('set FLAG_ 2^>nul') do set "%%V="
for /f "delims==" %%V in ('set OPT_ 2^>nul') do set "%%V="

:parse
if "%~1"=="" goto :parsed
for %%O in (%BOOL_OPTS%) do if /i "%~1"=="%%O" goto :parse_bool
for %%O in (%UINT_OPTS%) do if /i "%~1"=="%%O" goto :parse_uint
if /i "%~1"=="--from" (
    if "%~2"=="" (
        echo [ERROR] --from requiere el numero de paso ^(1-%STEP_TOTAL%^)
        goto :usage
    )
    set "FROM_STEP=%~2"
    set "FROM_GIVEN=1"
    shift
    shift
    goto :parse
)
if /i "%~1"=="--from-any" (
    set "FROM_ANY=1"
    shift
    goto :parse
)
if /i "%~1"=="-h"     goto :help
if /i "%~1"=="--help" goto :help
if "%~1"=="--" (
    shift
    goto :collect_p2_args
)
echo [ERROR] Argumento desconocido: %~1
goto :usage

:: --nombre-opcion -> FLAG_nombre_opcion=1
:parse_bool
set "OPT=%~1"
set "OPT=!OPT:--=!"
set "OPT=!OPT:-=_!"
set "FLAG_!OPT!=1"
shift
goto :parse

:: --nombre-opcion N -> OPT_nombre_opcion=N (N entero positivo)
:parse_uint
call "%COMMON%" :is_uint "%~2"
if errorlevel 1 (
    echo [ERROR] %~1 requiere un entero positivo: "%~2"
    goto :usage
)
set "OPT=%~1"
set "OPT=!OPT:--=!"
set "OPT=!OPT:-=_!"
set "OPT_!OPT!=%~2"
shift
shift
goto :parse

:: Todo lo que sigue a "--" va a run_pipeline. SIN comillas externas en el set: un argumento ya
:: entrecomillado ("A,B") conserva sus comillas y cmd no interpreta & | dentro de ellas.
:collect_p2_args
if "%~1"=="" goto :parsed
set P2_EXTRA=!P2_EXTRA! %1
shift
goto :collect_p2_args

:parsed

:: Combinaciones sin sentido (mejor un error ahora que un ciclo de horas con otra cosa distinta).
set "HARVEST_OPTS="
if defined FLAG_harvest_sync set "HARVEST_OPTS=1"
if defined FLAG_harvest_retire set "HARVEST_OPTS=1"
if defined OPT_harvest_limit set "HARVEST_OPTS=1"
if defined OPT_harvest_max_drop_pct set "HARVEST_OPTS=1"
if defined FLAG_no_harvest if defined HARVEST_OPTS (
    echo [ERROR] las opciones --harvest-* no se pueden combinar con --no-harvest
    goto :usage
)
if defined OPT_harvest_limit if not defined FLAG_harvest_sync (
    echo [ERROR] --harvest-limit solo tiene sentido con --harvest-sync
    goto :usage
)

:: Argumentos de cada paso (el lanzador de cada uno los recibe tal cual desde :run_step).
set "S1_ARGS="
if defined FLAG_benchmarks_load set "S1_ARGS=load"
set "S2_ARGS=--no-audit"
if defined FLAG_no_export set "S2_ARGS=!S2_ARGS! --no-export"
set "S3_ARGS="
if defined FLAG_skip_macro set "S3_ARGS=--skip-macro"
if defined OPT_workers set "S3_ARGS=!S3_ARGS! --workers !OPT_workers!"
set "S4_ARGS=--no-audit"
if defined FLAG_force set "S4_ARGS=!S4_ARGS! --force"
if defined FLAG_no_export set "S4_ARGS=!S4_ARGS! --no-export"
set "S4_ARGS=!S4_ARGS!!P2_EXTRA!"
set "HARVEST_ARGS="
if defined FLAG_harvest_sync set "HARVEST_ARGS=--sync"
if defined FLAG_harvest_retire set "HARVEST_ARGS=!HARVEST_ARGS! --retire-orphans"
if defined OPT_harvest_limit set "HARVEST_ARGS=!HARVEST_ARGS! --limit !OPT_harvest_limit!"
if defined OPT_harvest_max_drop_pct set "HARVEST_ARGS=!HARVEST_ARGS! --max-drop-pct !OPT_harvest_max_drop_pct!"

:: ------------------------------------------------------------
:: GUARDA DE REANUDACION (antes de crear log o tocar nada): sale con RC 102/103/100 sin ejecutar
:: ningun paso. Esos codigos son los de p1p2_state.py y coinciden con RC_REFUSED/RC_STATE/RC_USAGE.
:: ------------------------------------------------------------
set "CHECK_ARGS="
set "FROM_ANY_USED="
if defined FROM_GIVEN set CHECK_ARGS=--from "!FROM_STEP!"
if defined FROM_GIVEN if defined FROM_ANY set CHECK_ARGS=!CHECK_ARGS! --from-any
if defined FROM_GIVEN if defined FROM_ANY set "FROM_ANY_USED=--from-any-used"
"%PYTHON%" "%LAUNCH%\p1p2_state.py" check !CHECK_ARGS!
set "CHK_RC=!ERRORLEVEL!"
if !CHK_RC! NEQ 0 goto :abort_check

:: ------------------------------------------------------------
:: INSTANCIA UNICA: el manejador 9 queda abierto mientras corre :cycle; una segunda instancia no
:: puede abrirlo y :cycle no llega a ejecutarse (LOCK_HELD sin definir). Al morir el proceso, por
:: la causa que sea, el sistema lo cierra: no existe un bloqueo huerfano que limpiar a mano.
:: Anidado (lo invoca P1_P2_P3.bat, que ya lo tiene y lo hereda): FONDOS_CYCLE_LOCK evita pedirlo otra vez.
:: ------------------------------------------------------------
if not exist "%STATE_DIR%" mkdir "%STATE_DIR%"
set "LOCK_HELD="
if defined FONDOS_CYCLE_LOCK (call :cycle) else (call :cycle 9>"%LOCK_FILE%")
set "RC=!ERRORLEVEL!"
if not defined LOCK_HELD (
    echo [ERROR] Otra instancia de un ciclo esta en ejecucion ^(%LOCK_FILE%^). No se hace nada.
    set "RC=%RC_BUSY%"
)
call "%COMMON%" :utf8_off
endlocal & exit /b %RC%


:: ############################################################
:: :cycle -- el ciclo propiamente dicho; solo se ejecuta con el bloqueo adquirido.
:: ############################################################
:cycle
set "LOCK_HELD=1"
set "FONDOS_CYCLE_LOCK=1"

call "%COMMON%" :log_open "%LOG_DIR%" P1_P2_complete "P1+P2 Complete"
set "AUDIT_LOG=%LOG_DIR%\log_P1_P2_audit_%STAMP%.log"
set "RECOMP_LOG=%LOG_DIR%\log_P1_P2_recompute_%STAMP%.log"

echo  ROOT:      %ROOT% >> "%LOG%"
echo  PYTHON:    %PYTHON% >> "%LOG%"
echo  FROM:      !FROM_STEP! (from-any=!FROM_ANY!) >> "%LOG%"
echo  PASO 4:    run_pipeline extra=!P2_EXTRA! >> "%LOG%"
echo  Opciones activas: >> "%LOG%"
set FLAG_ >> "%LOG%" 2>nul
set OPT_ >> "%LOG%" 2>nul
echo ============================================================ >> "%LOG%"
if defined P2_EXTRA echo [WARN] argumentos extra para run_pipeline: ciclo posiblemente parcial, no es un ciclo normal >> "%LOG%"

echo.
echo [%STAMP%] P1+P2 Complete iniciado
echo   Log orquestacion: %LOG%
if defined P2_EXTRA echo   [WARN] run_pipeline recibira:!P2_EXTRA!
echo.

:: ------------------------------------------------------------
:: PREFLIGHT: un fallo de BD aborta SIN escribir estado (ver cabecera)
:: ------------------------------------------------------------
set "PRE_RC=0"
if defined FLAG_skip_preflight (
    echo --- PREFLIGHT: omitido ^(--skip-preflight^) >> "%LOG%"
) else (
    call :preflight
)
if !PRE_RC! NEQ 0 (
    echo.
    echo [ERROR] Preflight fallido ^(RC=!PRE_RC!^). No se ejecuta ningun paso ni se escribe estado. Ver !PRE_LOG!
    call "%COMMON%" :utf8_off
    exit /b !PRE_RC!
)

:: Prevenir suspension/hibernacion durante la ejecucion (~30-90 min); owner = este lanzador la
:: gestiona y los que invoca no la tocan (FONDOS_ORCH).
call "%COMMON%" :standby_disable owner

set "FINAL_RC=0"
set "FAILED_STEP=0"
set "BASELINE_ID="
set "RC_BETA=n/a"
set "RC_FRESH=n/a"
set "RC_REPORT=n/a"
for %%N in (1 2 3 4) do set "RC%%N=n/a"

:: ============================================================
:: PASO 0: auditoria estadistica BASELINE (antes de tocar nada)
:: ============================================================
if !FROM_STEP! GTR 1 (call :baseline_reuse) else (call :baseline_create)
set "BETA_CSV=%DATA_AUDIT_DIR%\macro_betas_!BASELINE_ID!.csv"

:: ============================================================
:: PASO 0b/0c: snapshot de betas y harvest (solo ciclo nuevo)
:: ============================================================
if !FROM_STEP! GTR 1 goto :previous_done
call :beta_snapshot

if defined FLAG_no_harvest (
    echo --- PASO 0c: harvest omitido ^(--no-harvest^) >> "%LOG%"
    goto :previous_done
)
set "AUX_ARGS=!HARVEST_ARGS!"
call :aux_bat harvest "PASO 0c harvest del catalogo DB" P1_harvestFunds
call :log_step_detail "%ROOT%\proyecto1\log" "log_harvest_*.log" "!AUX_RC!"
if !AUX_RC! NEQ 0 (
    set "FINAL_RC=!AUX_RC!"
    set "FAILED_STEP=1"
    echo ABORTADO en el harvest previo -- RC=!AUX_RC! >> "%LOG%"
    echo.
    echo [ERROR] el harvest fallo ^(RC=!AUX_RC!^). Ciclo abortado antes de tocar nada mas. Reanudar: P1_P2_Complete.bat --from 1 ^(--no-harvest reutiliza el ultimo harvest^)
    goto :fin
)
:previous_done

:: ============================================================
:: PASOS 1-4: cada uno via :run_step (salta, ejecuta, anota RC/estado/log de detalle)
::   call :run_step N  nombre_del_launcher  patron_log_de_detalle  directorio_log_de_detalle
:: Los argumentos del launcher son S<N>_ARGS (ver ARGUMENTOS). Si el paso falla, :run_step deja
:: FAILED_STEP != 0.
:: ============================================================

:: -- PASO 1: refresh de benchmarks ---------------------------------------------
call :run_step 1 P1_refreshBenchmarks "log_benchmarks_*.log" "%ROOT%\proyecto1\log"
if !FAILED_STEP! NEQ 0 goto :fin

:: -- PASO 2: pipeline P1 (+ reparacion de costes) ------------------------------
if !FROM_STEP! LEQ 2 "%PYTHON%" "%LAUNCH%\p1p2_state.py" oc-before
call :run_step 2 P1_discoverAllFunds "log_pipeline_*.log" "%ROOT%\proyecto1\log"
if !FAILED_STEP! NEQ 0 goto :fin
if !FROM_STEP! LEQ 2 call :repair_oc_contamination

:: -- PASO 3: macro + NAV discover/load -----------------------------------------
call :run_step 3 P2_discoverLoadMetrics "log_P2_discoverMetrics_*.log" "%ROOT%\proyecto2\log"
if !FAILED_STEP! NEQ 0 goto :fin

:: -- PASO 4: indicadores P2 + export -------------------------------------------
call :run_step 4 P2_calculateIndicators "log_P2_calcIndicators_*.log" "%ROOT%\proyecto2\log"

:: ============================================================
:: FIN: auditoria y diagnosticos finales, restaurar standby, pie del log y estado
:: ============================================================
:fin

if !FINAL_RC! EQU 0 (
    if defined BASELINE_ID call :audit_final
    call :post_diagnostics
) else (
    echo. >> "%LOG%"
    echo --- Auditoria y diagnosticos finales omitidos ^(el ciclo fallo^) >> "%LOG%"
)

:: Restaura la suspension AC al valor REAL guardado al empezar (si este lanzador la gestiono: bajo
:: P1_P2_P3.bat lo hace el), DESPUES de los diagnosticos.
call "%COMMON%" :standby_restore

:: Sello de fin real: las auditorias y diagnosticos finales pueden tardar unos minutos.
call "%COMMON%" :get_time STAMP_END yyyyMMdd_HHmmss

set "SUMMARY=RC1=!RC1!  RC2=!RC2!  RC3=!RC3!  RC4=!RC4!"
set "DIAG_SUMMARY=beta=!RC_BETA!  frescura-P3=!RC_FRESH!  informe=!RC_REPORT!"
if !FINAL_RC! EQU 0 (
    set "RESULT=OK"
    set "RESULT_TXT=OK"
) else (
    set "RESULT=FAILED"
    set "RESULT_TXT=ERROR"
)

echo. >> "%LOG%"
echo ============================================================ >> "%LOG%"
echo  P1+P2 Complete -- Fin !RESULT_TXT!: !STAMP_END! >> "%LOG%"
echo  !SUMMARY! >> "%LOG%"
echo  Diagnosticos: !DIAG_SUMMARY! >> "%LOG%"
echo ============================================================ >> "%LOG%"

echo.
if !FINAL_RC! EQU 0 (
    echo [!STAMP_END!] P1+P2 Complete -- Fin OK
) else (
    echo [!STAMP_END!] P1+P2 Complete -- Fin ERROR ^(FINAL_RC=!FINAL_RC!^)
)

:: Estado del ciclo para la guarda de --from N (escritura atomica en p1p2_state.py). Un fallo al
:: escribirlo no cambia el RC del ciclo: solo se avisa (y el siguiente --from se rechazara).
set "BASELINE_ARG="
if defined BASELINE_ID set "BASELINE_ARG=--baseline-id !BASELINE_ID!"
"%PYTHON%" "%LAUNCH%\p1p2_state.py" write --result !RESULT! --failed-step !FAILED_STEP! --stamp !STAMP_END! !FROM_ANY_USED! !BASELINE_ARG!
if errorlevel 1 (
    echo [WARN] No se pudo escribir el estado del ciclo. --from N se rechazara hasta el siguiente ciclo completo.
    echo [WARN] No se pudo escribir el estado del ciclo >> "%LOG%"
)

echo   Log orquestacion: %LOG%
echo.
exit /b !FINAL_RC!


:: ############################################################
:: SUBRUTINAS ESPECIFICAS DE ESTE LANZADOR (las genericas estan en lib\common.bat)
:: Solo se alcanzan con call / goto, nunca por caida de flujo.
:: ############################################################

:: ------------------------------------------------------------
:: :preflight -- p1p2_state.py preflight. Salida a su log (se muestra y se anota en el de orquestacion).
::   Salida: PRE_RC (104 = BD no alcanzable; los avisos no cuentan).
:: ------------------------------------------------------------
:preflight
set "PRE_LOG=%LOG_DIR%\log_P1_P2_preflight_%STAMP%.log"
"%PYTHON%" "%LAUNCH%\p1p2_state.py" preflight > "%PRE_LOG%" 2>&1
set "PRE_RC=!ERRORLEVEL!"
echo --- PREFLIGHT: RC=!PRE_RC! >> "%LOG%"
type "%PRE_LOG%"
type "%PRE_LOG%" >> "%LOG%"
echo.
exit /b 0

:: ------------------------------------------------------------
:: :aux_py TAG ETIQUETA -- ejecuta un script Python auxiliar con AUX_TARGET (ruta entrecomillada o
::   `-m modulo`) y AUX_ARGS, desde ROOT. Su salida va a log_P1_P2_<TAG>_<STAMP>.log; en el log de
::   orquestacion y en consola quedan solo el RC y las lineas clave ([ATENCION], veredicto de P3).
::   Salida: AUX_RC. Nunca aborta: quien llama decide.
:: ------------------------------------------------------------
:aux_py
set "AUX_LOG=%LOG_DIR%\log_P1_P2_%~1_%STAMP%.log"
echo [%~1] %~2
echo. >> "%LOG%"
echo --- %~2 -- log: %AUX_LOG% >> "%LOG%"
pushd "%ROOT%"
"%PYTHON%" -u -X utf8 !AUX_TARGET! !AUX_ARGS! >> "%AUX_LOG%" 2>&1
set "AUX_RC=!ERRORLEVEL!"
popd
echo --- %~2 fin: RC=!AUX_RC! >> "%LOG%"
call :key_lines "%AUX_LOG%"
exit /b 0

:: ------------------------------------------------------------
:: :aux_bat TAG ETIQUETA LANZADOR -- idem para un lanzador .bat auxiliar (escribe su propio log).
::   Argumentos en AUX_ARGS. Salida: AUX_RC.
:: ------------------------------------------------------------
:aux_bat
echo [%~1] %~2
echo. >> "%LOG%"
echo --- %~2 >> "%LOG%"
call "%LAUNCH%\%~3.bat" !AUX_ARGS!
set "AUX_RC=!ERRORLEVEL!"
echo --- %~2 fin: RC=!AUX_RC! >> "%LOG%"
exit /b 0

:: ------------------------------------------------------------
:: :key_lines FICHERO -- copia a consola y al log de orquestacion las lineas clave de un informe.
:: ------------------------------------------------------------
:key_lines
findstr /b /c:"[ATENCION]" /c:"P3 NO aceptaria" /c:"P3 aceptaria" "%~1" >> "%LOG%" 2>nul
findstr /b /c:"[ATENCION]" /c:"P3 NO aceptaria" /c:"P3 aceptaria" "%~1" 2>nul
exit /b 0

:: ------------------------------------------------------------
:: :run_step N NOMBRE PATRON_LOG DIR_LOG -- ejecuta el launcher NOMBRE.bat con S<N>_ARGS.
::   Si FROM_STEP > N lo salta (RCN=SALTADO). Si no: cabecera, call, RC, estado (p1p2_state.py step),
::   log de detalle y, con RC != 0, deja FINAL_RC / FAILED_STEP para que el flujo principal aborte.
::   Salida: RCN, y en fallo FINAL_RC y FAILED_STEP.
:: ------------------------------------------------------------
:run_step
if !FROM_STEP! GTR %~1 (
    set "RC%~1=SALTADO"
    echo. >> "%LOG%"
    echo --- PASO %~1/%STEP_TOTAL%: SALTADO ^(--from !FROM_STEP!^) --------------------- >> "%LOG%"
    exit /b 0
)

set "STEP_ARGS=!S%~1_ARGS!"
call "%COMMON%" :get_time T_START HHmmss
echo [!T_START:~0,2!:!T_START:~2,2!:!T_START:~4,2!] PASO %~1/%STEP_TOTAL%: %~2
echo. >> "%LOG%"
echo --- PASO %~1/%STEP_TOTAL%: %~2 -- Inicio: !T_START! ---------- >> "%LOG%"

call "%LAUNCH%\%~2.bat" !STEP_ARGS!
set "STEP_RC=!ERRORLEVEL!"

call "%COMMON%" :get_time T_END HHmmss
echo [!T_END:~0,2!:!T_END:~2,2!:!T_END:~4,2!] PASO %~1/%STEP_TOTAL% fin ^(RC=!STEP_RC!^)
echo --- PASO %~1/%STEP_TOTAL% fin: RC=!STEP_RC!  Fin: !T_END! --------------------- >> "%LOG%"
"%PYTHON%" "%LAUNCH%\p1p2_state.py" step --step %~1 --rc !STEP_RC! --start !T_START! --end !T_END! !FROM_ANY_USED!
call :log_step_detail "%~4" "%~3" "!STEP_RC!"
set "RC%~1=!STEP_RC!"

if !STEP_RC! NEQ 0 (
    set "FINAL_RC=!STEP_RC!"
    set "FAILED_STEP=%~1"
    echo ABORTADO en PASO %~1 -- RC=!STEP_RC! >> "%LOG%"
    echo.
    echo [ERROR] PASO %~1 fallo ^(RC=!STEP_RC!^). Ciclo abortado. Reanudar: P1_P2_Complete.bat --from %~1
)
exit /b 0

:: ------------------------------------------------------------
:: :log_step_detail DIR PATRON RC -- anota en %LOG% el log de detalle real de un paso (FND-0109).
::   DIR = directorio de log del paso, PATRON = glob (el mas reciente por fecha de modificacion;
::   se ignoran los *_err.log), RC = RC del paso (si != 0, tambien vuelca sus ultimas 15 lineas).
::   Si existe el log de stderr hermano (<log>_err.log) tambien se anota su ruta.
:: ------------------------------------------------------------
:log_step_detail
setlocal
set "SLD_DIR=%~1"
set "SLD_PAT=%~2"
set "SLD_RC=%~3"
set "SLD_FILE="
for /f "delims=" %%f in ('dir /b /o-d "%SLD_DIR%\%SLD_PAT%" 2^>nul ^| findstr /v /i /c:"_err.log"') do if not defined SLD_FILE set "SLD_FILE=%%f"
if not defined SLD_FILE (
    echo   [WARN] no se encontro log de detalle para %SLD_PAT% en %SLD_DIR% >> "%LOG%"
    endlocal
    exit /b 0
)
set "SLD_PATH=%SLD_DIR%\%SLD_FILE%"
set "SLD_ERRPATH=%SLD_PATH:~0,-4%_err.log"
set "SLD_WARN=0"
set "SLD_ERR=0"
for /f %%c in ('findstr /c:"[WARN]" "%SLD_PATH%" 2^>nul ^| "%WINFIND%" /c /v ""') do set "SLD_WARN=%%c"
for /f %%c in ('findstr /c:"[ERROR]" "%SLD_PATH%" 2^>nul ^| "%WINFIND%" /c /v ""') do set "SLD_ERR=%%c"
echo   log detalle: %SLD_PATH%  (WARN=%SLD_WARN% ERROR=%SLD_ERR%) >> "%LOG%"
if exist "%SLD_ERRPATH%" echo   log stderr : %SLD_ERRPATH% >> "%LOG%"
if not "%SLD_RC%"=="0" (
    echo   --- ultimas lineas del log de detalle -------------------------- >> "%LOG%"
    "%PYTHON%" "%LIB%\batch_helpers.py" tail "%SLD_PATH%" 15 >> "%LOG%" 2>nul
    echo   ------------------------------------------------------------------ >> "%LOG%"
)
endlocal
exit /b 0

:: ------------------------------------------------------------
:: :baseline_create -- ciclo NUEVO: punto de restauracion + auditoria baseline (pre_<STAMP>).
:: ------------------------------------------------------------
:baseline_create
set "BASELINE_ID=pre_!STAMP!"
"%PYTHON%" "%LAUNCH%\p1p2_state.py" restore-point --stamp !STAMP! >> "%LOG%" 2>&1
echo. >> "%LOG%"
echo --- PASO 0: auditoria estadistica baseline (run_id=!BASELINE_ID!) ---- >> "%LOG%"
echo [!STAMP!] PASO 0: auditoria estadistica baseline (!BASELINE_ID!)
call "%LAUNCH%\AUDIT_P2.bat" --mode report --persist --run-id !BASELINE_ID!_p2 --log "%AUDIT_LOG%" > nul
if errorlevel 1 echo [WARN] AUDIT_P2 baseline fallo: no se cuantificara la deriva >> "%LOG%"
call "%LAUNCH%\AUDIT_P1.bat" --no-benchmark --mode report --persist --run-id !BASELINE_ID!_costs --log "%AUDIT_LOG%" > nul
if errorlevel 1 echo [WARN] AUDIT_P1 baseline fallo: no se cuantificara la deriva >> "%LOG%"
exit /b 0

:: ------------------------------------------------------------
:: :baseline_reuse -- reanudacion (--from N > 1): reutiliza el baseline del ciclo fallido.
:: ------------------------------------------------------------
:baseline_reuse
call "%COMMON%" :state_query baseline BASELINE_ID
echo --- PASO 0: baseline reutilizado del ciclo anterior: !BASELINE_ID! >> "%LOG%"
exit /b 0

:: ------------------------------------------------------------
:: :beta_snapshot -- PASO 0b: betas macro actuales a CSV (BETA_CSV) para compararlas tras P2.
::   Un fallo solo deja sin comparacion final (:beta_compare avisa si falta el fichero).
:: ------------------------------------------------------------
:beta_snapshot
if not exist "%DATA_AUDIT_DIR%" mkdir "%DATA_AUDIT_DIR%"
set AUX_TARGET="%ROOT%\scripts\audit\beta_shift_audit.py"
set AUX_ARGS=--snapshot "!BETA_CSV!"
call :aux_py beta_snapshot "PASO 0b snapshot de betas macro"
if !AUX_RC! NEQ 0 echo [WARN] snapshot de betas fallo ^(RC=!AUX_RC!^): no habra comparacion de betas al final >> "%LOG%"
exit /b 0

:: ------------------------------------------------------------
:: :beta_compare -- compara las betas de ahora con el snapshot, exigiendo la CALC_VERSION actual.
::   RC de beta_shift_audit: 0 = limpio, 1 = hallazgos (P2 sin terminar, NaN, |beta| implausible).
:: ------------------------------------------------------------
:beta_compare
if not exist "!BETA_CSV!" (
    echo [WARN] sin snapshot de betas previo ^(!BETA_CSV!^): comparacion omitida >> "%LOG%"
    exit /b 0
)
call "%COMMON%" :state_query calc-version CALC_VER
if not defined CALC_VER (
    echo [WARN] no se pudo leer CALC_VERSION de run_pipeline.py: comparacion de betas omitida >> "%LOG%"
    exit /b 0
)
set AUX_TARGET="%ROOT%\scripts\audit\beta_shift_audit.py"
set AUX_ARGS=--compare "!BETA_CSV!" --version !CALC_VER! --out "%DATA_AUDIT_DIR%\beta_shift_outliers_!BASELINE_ID!.csv"
call :aux_py beta_compare "comparacion de betas macro (CALC_VERSION !CALC_VER!)"
set "RC_BETA=!AUX_RC!"
exit /b 0

:: ------------------------------------------------------------
:: :repair_oc_contamination -- PASO 2b: recompute-costs (solo cache) sobre los fondos que ESTE P1 ha
::   contaminado con ongoing_charge_recurrent = ACI_RHP. Nunca aborta el ciclo: si falla o no basta,
::   avisa en el log. Los ya contaminados de antes no se tocan.
:: ------------------------------------------------------------
:repair_oc_contamination
call "%COMMON%" :state_query oc-newly OC_FIX
if not defined OC_FIX exit /b 0

echo --- PASO 2b: recompute-costs de fondos contaminados por este P1: !OC_FIX! >> "%LOG%"
echo [PASO 2b] recompute-costs ^(solo cache^) sobre fondos contaminados por este P1
pushd "%ROOT%\proyecto1"
"%PYTHON%" -X utf8 run_block.py --nature-first --master-db --recompute-costs --list-isin "!OC_FIX!" >> "%RECOMP_LOG%" 2>&1
set "RC2B=!ERRORLEVEL!"
popd
echo --- PASO 2b fin: RC=!RC2B!  log: %RECOMP_LOG% >> "%LOG%"
if !RC2B! NEQ 0 echo [WARN] recompute-costs fallo ^(RC=!RC2B!^): revisar %RECOMP_LOG% >> "%LOG%"

:: Verificacion: si la reparacion fallo o no basto, el ciclo NO se detiene pero queda avisado.
call "%COMMON%" :state_query oc-newly OC_LEFT
if defined OC_LEFT echo [WARN] siguen contaminados por este P1 tras recompute-costs: !OC_LEFT! >> "%LOG%"
if defined OC_LEFT echo [WARN] fondos contaminados por este P1 sin reparar: !OC_LEFT!
exit /b 0

:: ------------------------------------------------------------
:: :audit_final -- auditoria estadistica final con --compare-to del baseline. Solo si el ciclo fue OK.
:: ------------------------------------------------------------
:audit_final
call "%COMMON%" :get_time AUDIT_STAMP yyyyMMdd_HHmmss
echo. >> "%LOG%"
echo --- Auditoria estadistica final (compare-to !BASELINE_ID!) ---- >> "%LOG%"
echo [AUDITORIA] deriva vs baseline !BASELINE_ID!
call "%LAUNCH%\AUDIT_P2.bat" --mode report --persist --run-id post_!AUDIT_STAMP!_p2 --compare-to !BASELINE_ID!_p2 --log "%AUDIT_LOG%" > nul
if errorlevel 1 echo [WARN] AUDIT_P2 final fallo >> "%LOG%"
call "%LAUNCH%\AUDIT_P1.bat" --mode report --persist --run-id post_!AUDIT_STAMP!_costs --compare-to !BASELINE_ID!_costs --log "%AUDIT_LOG%" > nul
if errorlevel 1 echo [WARN] AUDIT_P1 final fallo >> "%LOG%"
echo   Informe de deriva: %AUDIT_LOG% >> "%LOG%"
echo   Informe de deriva: %AUDIT_LOG%
exit /b 0

:: ------------------------------------------------------------
:: :post_diagnostics -- fase posterior (solo ciclo OK). Informativa: no toca FINAL_RC ni el estado.
::   Comparacion de betas, frescura para P3, informe de ciclo y los diagnosticos opcionales.
:: ------------------------------------------------------------
:post_diagnostics
echo. >> "%LOG%"
echo --- Diagnosticos posteriores al ciclo ------------------------- >> "%LOG%"

call :beta_compare

set AUX_TARGET="%ROOT%\scripts\launch\p3_freshness_check.py"
set "AUX_ARGS="
call :aux_py p3_freshness "frescura de los datos para P3"
set "RC_FRESH=!AUX_RC!"
if !RC_FRESH! NEQ 0 echo [WARN] P3 no aceptaria los datos del ciclo ^(RC=!RC_FRESH!^): ver !AUX_LOG! >> "%LOG%"

:: El informe cuenta incidencias DQ desde el dia en que empezo el ciclo (el del baseline).
set "SINCE_DATE=!STAMP:~0,8!"
if "!BASELINE_ID:~0,4!"=="pre_" set "SINCE_DATE=!BASELINE_ID:~4,8!"
set AUX_TARGET="%ROOT%\scripts\launch\p1p2_cycle_report.py"
set "AUX_ARGS=--stamp !STAMP! --since !SINCE_DATE:~0,4!-!SINCE_DATE:~4,2!-!SINCE_DATE:~6,2!"
call :aux_py cycle_report "informe de ciclo"
set "RC_REPORT=!AUX_RC!"

if defined OPT_shadow (
    set AUX_TARGET="%ROOT%\scripts\audit\shadow_reconciliation.py"
    set "AUX_ARGS=--stratified-sample !OPT_shadow!"
    call :aux_py shadow "shadow reconciliation (muestra estratificada de !OPT_shadow! por celda)"
)
if defined FLAG_benchmark_gaps (
    set AUX_TARGET=-m proyecto1.src.loaders.benchmark_loader
    set "AUX_ARGS=--mode gaps"
    call :aux_py benchmark_gaps "cobertura de benchmarks"
)
if defined FLAG_dashboard (
    set AUX_TARGET=-m proyecto2.src.reports.rolling_dashboard
    set "AUX_ARGS="
    call :aux_py dashboard "dashboard rolling P2"
)
if defined FLAG_diag_cost (
    set "AUX_ARGS="
    call :aux_bat diag_cost "diagnostico de extraccion de costes" P1_diagCost
)
exit /b 0

:: ------------------------------------------------------------
:: Salidas de error de argumentos / ayuda / guarda
:: ------------------------------------------------------------
:usage
call :usage_text
call "%COMMON%" :utf8_off
endlocal & exit /b %RC_USAGE%

:help
call :usage_text
call "%COMMON%" :utf8_off
endlocal & exit /b 0

:usage_text
echo Uso: P1_P2_Complete.bat [opciones] [-- argumentos_para_run_pipeline]
echo Ciclo:
echo   --from N [--from-any]    reanudar en el paso N (1-4) tras un fallo
echo   --skip-preflight         no ejecutar el preflight
echo   -h, --help               esta ayuda
echo Opciones de paso:
echo   --benchmarks-load        PASO 1: recarga completa de benchmarks (modo load)
echo   --no-export              PASO 2 y 4: sin export Excel de P1 ni de P2
echo   --skip-macro             PASO 3: solo NAV, sin carga macro
echo   --workers N              PASO 3: N hilos en el NAV load
echo   --force                  PASO 4: recomputo total de P2 (cache hash + gate OLS)
echo   -- ...                   PASO 4: argumentos reenviados a run_pipeline
echo Harvest (fase previa; activo por defecto, solo lectura de PDF):
echo   --no-harvest             no ejecutar el harvest
echo   --harvest-sync           descargar los KIID nuevos
echo   --harvest-retire         archivar los KIID huerfanos
echo   --harvest-limit N        con --harvest-sync: maximo N descargas
echo   --harvest-max-drop-pct N puerta de calidad del harvest
echo Diagnosticos opcionales (fase posterior):
echo   --shadow N               shadow reconciliation, N ISIN por celda (tope 60)
echo   --benchmark-gaps         cobertura de benchmarks
echo   --dashboard              dashboard HTML rolling de P2
echo   --diag-cost              diagnostico de extraccion de costes (lento)
exit /b 0

:abort_check
echo.
echo [ERROR] Comprobacion previa fallida (RC=!CHK_RC!). No se ejecuta ningun paso.
call "%COMMON%" :utf8_off
:: una sola linea: %CHK_RC% se sustituye antes de que endlocal se ejecute
endlocal & exit /b %CHK_RC%
