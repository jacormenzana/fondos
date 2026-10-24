@echo off
setlocal EnableExtensions EnableDelayedExpansion
call "%~dp0lib\common.bat" :init "%~nx0" & set "RC_BOOT=!ERRORLEVEL!"
if %RC_BOOT% NEQ 0 (endlocal & exit /b %RC_BOOT%)
call "%COMMON%" :utf8_on

:: ============================================================
:: P1_runCycle.bat -- Ciclo P1 autonomo con auditoria PRE y POST (lanzador de PROPIETARIO; norma: doc/reglas/NORMAS_BATCH.md)
::
:: Es el subconjunto P1 del ciclo maestro (P1P2P3_runCycle.bat), sin P2 ni P3:
::   0. preflight                   p1p2_state.py preflight (BD alcanzable, DSN owner, disco)
::   1. auditoria P1 PRE            P1_audit --persist --run-id pre_<sello>_costs (costes + benchmarks B1-B7). Si no es
::                                  valida (fallo o INCOMPLETA) no se toca nada: RC 107 (--allow-no-baseline lo avisa y sigue)
::   2. harvest                     P1_harvestFunds.bat (solo lectura de PDF salvo --harvest-sync / --harvest-retire)
::   3. benchmarks                  P1_refreshBenchmarks.bat
::   4. pipeline P1                 P1_discoverAllFunds.bat
::   5. auditoria P1 POST           P1_audit --persist --run-id post_<sello>_costs --compare-to pre_<sello>_costs (informativa)
::   6. informe de ciclo            p1p2_cycle_report.py (lineas [ATENCION]; informativo)
:: Se detiene en el primer paso que falla y devuelve su codigo. NO escribe el estado del ciclo P1+P2 (un ciclo parcial no
:: puede dar por bueno a P1P2_runCycle.bat --from N ni a P1P2P3_runCycle.bat --only-p3). Comparte el bloqueo de instancia unica.
::
:: Uso:  P1_runCycle.bat [opciones]
::   --skip-preflight         no ejecutar el preflight
::   --allow-no-baseline      si la auditoria PRE no es valida: avisar y seguir (por defecto RC 107)
::   --no-harvest             no ejecutar el harvest
::   --harvest-sync           descargar los KIID nuevos
::   --harvest-retire         archivar los KIID huerfanos
::   --harvest-limit N        con --harvest-sync: maximo N descargas
::   --harvest-max-drop-pct N puerta de calidad del harvest
::   --benchmarks-load        recarga completa de benchmarks (modo load)
::   --no-export              sin export Excel de P1
::   -h, --help               esta ayuda
:: Codigos de salida: 0 OK | 100 argumentos invalidos | 101 interprete no encontrado | 104 preflight fallido |
::   105 otra instancia en ejecucion | 106 driver PostgreSQL bloqueado | 107 baseline (auditoria PRE) no valido |
::   1-99 = RC propagado del paso que fallo.
:: ============================================================

:: -- Configuracion --------------------------------------------------------------
set "LOG_DIR=%ROOT%\proyecto1\log"
set "LOCK_FILE=%STATE_DIR%\fondos_cycle.lock"
set "BOOL_OPTS=--skip-preflight --allow-no-baseline --no-harvest --harvest-sync --harvest-retire --benchmarks-load --no-export"
set "UINT_OPTS=--harvest-limit --harvest-max-drop-pct"

:: -- Argumentos: tabla de opciones, validacion y rechazo de lo desconocido ---------
for /f "delims==" %%V in ('set FLAG_ 2^>nul') do set "%%V="
for /f "delims==" %%V in ('set OPT_ 2^>nul') do set "%%V="
:parse
if "%~1"=="" goto :parsed
for %%O in (%BOOL_OPTS%) do if /i "%~1"=="%%O" goto :parse_bool
for %%O in (%UINT_OPTS%) do if /i "%~1"=="%%O" goto :parse_uint
if /i "%~1"=="-h"     goto :help
if /i "%~1"=="--help" goto :help
echo [ERROR] Argumento desconocido: %~1
goto :usage

:parse_bool
set "OPT=%~1"
set "OPT=!OPT:--=!"
set "OPT=!OPT:-=_!"
set "FLAG_!OPT!=1"
shift
goto :parse

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
:parsed

:: Combinaciones sin sentido: se rechazan antes de tocar nada.
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
set "ALLOW_NO_BASELINE="
if defined FLAG_allow_no_baseline set "ALLOW_NO_BASELINE=1"

:: Argumentos de cada paso.
set "HARVEST_ARGS="
if defined FLAG_harvest_sync set "HARVEST_ARGS=--sync"
if defined FLAG_harvest_retire set "HARVEST_ARGS=!HARVEST_ARGS! --retire-orphans"
if defined OPT_harvest_limit set "HARVEST_ARGS=!HARVEST_ARGS! --limit !OPT_harvest_limit!"
if defined OPT_harvest_max_drop_pct set "HARVEST_ARGS=!HARVEST_ARGS! --max-drop-pct !OPT_harvest_max_drop_pct!"
set "BENCH_ARGS="
if defined FLAG_benchmarks_load set "BENCH_ARGS=load"
set "P1_ARGS="
if defined FLAG_no_export set "P1_ARGS=--no-export"

:: -- Instancia unica (ver P1P2_runCycle.bat): el manejador 9 vive mientras corre :cycle ------
if not exist "%STATE_DIR%" mkdir "%STATE_DIR%"
set "LOCK_HELD="
if defined FONDOS_CYCLE_LOCK (call :cycle) else (call :cycle 9>"%LOCK_FILE%")
set "RC=!ERRORLEVEL!"
if not defined LOCK_HELD (
    echo [ERROR] Otro ciclo esta en ejecucion ^(%LOCK_FILE%^). No se hace nada.
    set "RC=%RC_BUSY%"
)
call "%COMMON%" :utf8_off
endlocal & exit /b %RC%


:: ############################################################
:: :cycle -- el proceso propiamente dicho; solo se ejecuta con el bloqueo adquirido.
:: ############################################################
:cycle
set "LOCK_HELD=1"
set "FONDOS_CYCLE_LOCK=1"
set "RC=0"
cd /d "%ROOT%"
call "%COMMON%" :log_open "%LOG_DIR%" P1_runCycle "P1 runCycle"
echo [%STAMP%] P1 runCycle iniciado
echo   Log: %LOG%
set FLAG_ >> "%LOG%" 2>nul
set OPT_ >> "%LOG%" 2>nul

:: -- 0. Preflight: un fallo no toca nada --------------------------------------
set "PRE_RC=0"
if defined FLAG_skip_preflight (
    echo --- PREFLIGHT: omitido ^(--skip-preflight^) >> "%LOG%"
) else (
    call "%PHASES%" :preflight "%LOG_DIR%\log_P1_runCycle_preflight_%STAMP%.log"
)
if !PRE_RC! NEQ 0 (
    echo [ERROR] Preflight fallido ^(RC=!PRE_RC!^). No se ejecuta ningun paso.
    set "RC=!PRE_RC!"
    goto :finish
)

call "%COMMON%" :standby_disable owner

:: -- 1. Auditoria P1 PRE (baseline congelado) ---------------------------------
set "PRE_ID=pre_%STAMP%"
echo [AUDITORIA] P1 PRE !PRE_ID!_costs
echo --- AUDITORIA P1 PRE: !PRE_ID!_costs >> "%LOG%"
call "%PHASES%" :audit_p1 !PRE_ID!_costs
set "PRE_AUD_RC=!AUD_RC!"
echo --- AUDITORIA P1 PRE fin: RC=!PRE_AUD_RC! >> "%LOG%"
call "%PHASES%" :baseline_verdict !PRE_AUD_RC! 0
if !BASELINE_RC! NEQ 0 (
    echo [ERROR] la auditoria PRE no es valida ^(RC=!BASELINE_RC!^): no se ejecuta ningun paso. Repetir, o --allow-no-baseline.
    set "RC=!BASELINE_RC!"
    goto :finish
)

:: -- 2. Harvest -----------------------------------------------------------------
if defined FLAG_no_harvest (
    echo --- harvest: omitido ^(--no-harvest^) >> "%LOG%"
) else (
    echo [HARVEST] P1_harvestFunds
    echo --- harvest: P1_harvestFunds !HARVEST_ARGS! >> "%LOG%"
    call "%LAUNCH%\P1_harvestFunds.bat" !HARVEST_ARGS!
    set "RC=!ERRORLEVEL!"
    echo --- harvest fin: RC=!RC! >> "%LOG%"
)
if !RC! NEQ 0 (
    echo [ERROR] el harvest fallo ^(RC=!RC!^). Ciclo abortado.
    goto :finish
)

:: -- 3. Benchmarks ----------------------------------------------------------------
echo [PASO 1] P1_refreshBenchmarks
echo --- PASO 1: P1_refreshBenchmarks !BENCH_ARGS! >> "%LOG%"
call "%LAUNCH%\P1_refreshBenchmarks.bat" !BENCH_ARGS!
set "RC=!ERRORLEVEL!"
echo --- PASO 1 fin: RC=!RC! >> "%LOG%"
if !RC! NEQ 0 (
    echo [ERROR] PASO 1 fallo ^(RC=!RC!^). Ciclo abortado.
    goto :finish
)

:: -- 4. Pipeline P1 -----------------------------------------------------------------
echo [PASO 2] P1_discoverAllFunds
echo --- PASO 2: P1_discoverAllFunds !P1_ARGS! >> "%LOG%"
call "%LAUNCH%\P1_discoverAllFunds.bat" !P1_ARGS!
set "RC=!ERRORLEVEL!"
echo --- PASO 2 fin: RC=!RC! >> "%LOG%"
if !RC! NEQ 0 (
    echo [ERROR] PASO 2 fallo ^(RC=!RC!^). Ciclo abortado.
    goto :finish
)

:: -- 5. Auditoria P1 POST: al salir del ultimo escritor de P1; informativa --------------
call "%COMMON%" :get_time POST_STAMP yyyyMMdd_HHmmss
echo [AUDITORIA] P1 POST post_!POST_STAMP!_costs vs !PRE_ID!_costs
echo --- AUDITORIA P1 POST: post_!POST_STAMP!_costs --compare-to !PRE_ID!_costs >> "%LOG%"
call "%PHASES%" :audit_p1 post_!POST_STAMP!_costs !PRE_ID!_costs
echo --- AUDITORIA P1 POST fin: RC=!AUD_RC! >> "%LOG%"
if !AUD_RC! NEQ 0 echo [WARN] la auditoria P1 POST no termino bien ^(RC=!AUD_RC!^): no hay deriva objetiva

:: -- 6. Informe de ciclo (informativo: nunca cambia el RC) --------------------------------
call "%PHASES%" :cycle_report %STAMP%

:finish
call "%COMMON%" :standby_restore
call "%COMMON%" :log_close "P1 runCycle" !RC!
echo.
if "!RC!"=="0" (
    echo [%STAMP2%] P1 runCycle -- Fin OK
) else (
    echo [%STAMP2%] P1 runCycle -- Fin ERROR ^(RC=!RC!^)
)
echo   Log: %LOG%
echo.
exit /b !RC!


:: ------------------------------------------------------------
:: Salidas de error de argumentos / ayuda
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
echo Uso: P1_runCycle.bat [opciones]
echo   --skip-preflight         no ejecutar el preflight
echo   --allow-no-baseline      si la auditoria PRE no es valida: avisar y seguir (por defecto RC 107)
echo   --no-harvest             no ejecutar el harvest
echo   --harvest-sync           descargar los KIID nuevos
echo   --harvest-retire         archivar los KIID huerfanos
echo   --harvest-limit N        con --harvest-sync: maximo N descargas
echo   --harvest-max-drop-pct N puerta de calidad del harvest
echo   --benchmarks-load        recarga completa de benchmarks (modo load)
echo   --no-export              sin export Excel de P1
echo   -h, --help               esta ayuda
exit /b 0
