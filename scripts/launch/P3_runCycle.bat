@echo off
setlocal EnableExtensions EnableDelayedExpansion
call "%~dp0lib\common.bat" :init "%~nx0" & set "RC_BOOT=!ERRORLEVEL!"
if %RC_BOOT% NEQ 0 (endlocal & exit /b %RC_BOOT%)
call "%COMMON%" :utf8_on

:: ============================================================
:: P3_runCycle.bat -- Ciclo P3 autonomo: DRAFT + auditoria P2 posterior + informe (lanzador de PROPIETARIO)
::
:: Es el subconjunto P3 del ciclo maestro (P1P2P3_runCycle.bat), sobre los datos que ya hay (equivale a la parte P3 de
:: P1P2P3_runCycle.bat --only-p3, sin su puerta de betas ni la comprobacion del ultimo ciclo):
::   1. P3_buildPortfolio.bat   puerta de frescura propia (RC 2 si las entradas estan obsoletas), DRAFT y auditoria P2
::                              persistida tras el (post_<sello>_p2: es la evidencia de promocion; P3 no cambia los datos de P2,
::                              asi que no hay PRE ni deriva que medir)
::   2. P3_generateReport.bat --draft   informe del DRAFT recien construido (marcado NO PRODUCCION)
:: La promocion es una orden del propietario (p3_promote_scenario.py). Se detiene en el primer paso que falla. Comparte el
:: bloqueo de instancia unica.
::
:: Uso:  P3_runCycle.bat [opciones]
::   --skip-preflight    no ejecutar el preflight (BD alcanzable, DSN owner, disco)
::   --scenario ID       scenario_id de la cartera (por defecto autogenerado)
::   --report-dir DIR    directorio de salida del informe (por defecto out\reports\p3)
::   --p3-dry-run        P3 no persiste (debug; sin auditoria ni informe)
::   --allow-stale       salta la puerta de frescura de P3 (con criterio)
::   -h, --help          esta ayuda
:: Codigos de salida: 0 OK | 100 argumentos invalidos | 101 interprete no encontrado | 105 otra instancia en ejecucion |
::   104 preflight fallido | 106 driver PostgreSQL bloqueado | 1-99 = RC propagado del paso que fallo (2 = entradas obsoletas).
:: ============================================================

:: -- Configuracion --------------------------------------------------------------
set "LOG_DIR=%ROOT%\proyecto3\log"
set "LOCK_FILE=%STATE_DIR%\fondos_cycle.lock"
set "BOOL_OPTS=--skip-preflight --p3-dry-run --allow-stale"
set "STR_OPTS=--scenario --report-dir"

:: -- Argumentos ------------------------------------------------------------------------
for /f "delims==" %%V in ('set FLAG_ 2^>nul') do set "%%V="
for /f "delims==" %%V in ('set OPT_ 2^>nul') do set "%%V="
:parse
if "%~1"=="" goto :parsed
for %%O in (%BOOL_OPTS%) do if /i "%~1"=="%%O" goto :parse_bool
for %%O in (%STR_OPTS%) do if /i "%~1"=="%%O" goto :parse_str
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

:: --nombre-opcion VALOR -> OPT_nombre_opcion=VALOR (no vacio y que no parezca otra opcion)
:parse_str
set "VAL=%~2"
if "!VAL!"=="" (
    echo [ERROR] %~1 requiere un valor
    goto :usage
)
if "!VAL:~0,2!"=="--" (
    echo [ERROR] %~1 requiere un valor, no la opcion "%~2"
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

if defined OPT_scenario if not "!OPT_scenario: =!"=="!OPT_scenario!" (
    echo [ERROR] --scenario no admite espacios: "!OPT_scenario!"
    goto :usage
)
set "P3_ARGS="
if defined OPT_scenario set "P3_ARGS=!OPT_scenario!"
if defined FLAG_p3_dry_run set "P3_ARGS=!P3_ARGS! --dry-run"
if defined FLAG_allow_stale set "P3_ARGS=!P3_ARGS! --allow-stale"

:: -- Instancia unica (ver P1P2_runCycle.bat) --------------------------------------------
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
call "%COMMON%" :log_open "%LOG_DIR%" P3_runCycle "P3 runCycle"
echo [%STAMP%] P3 runCycle iniciado
echo   Log: %LOG%
set FLAG_ >> "%LOG%" 2>nul
set OPT_ >> "%LOG%" 2>nul

:: -- 0. Preflight: un fallo no toca nada ----------------------------------------------------
set "PRE_RC=0"
if defined FLAG_skip_preflight (
    echo --- PREFLIGHT: omitido ^(--skip-preflight^) >> "%LOG%"
) else (
    call "%PHASES%" :preflight "%LOG_DIR%\log_P3_runCycle_preflight_%STAMP%.log"
)
if !PRE_RC! NEQ 0 (
    echo [ERROR] Preflight fallido ^(RC=!PRE_RC!^). No se ejecuta ningun paso.
    set "RC=!PRE_RC!"
    goto :finish
)

call "%COMMON%" :standby_disable owner

:: -- 1. DRAFT + auditoria P2 posterior ---------------------------------------------------
echo [P3] P3_buildPortfolio
echo --- P3_buildPortfolio !P3_ARGS! >> "%LOG%"
call "%LAUNCH%\P3_buildPortfolio.bat" !P3_ARGS!
set "RC=!ERRORLEVEL!"
echo --- P3_buildPortfolio fin: RC=!RC! >> "%LOG%"
if !RC! NEQ 0 (
    echo [ERROR] P3_buildPortfolio fallo ^(RC=!RC!^). No se genera el informe.
    goto :finish
)
if defined FLAG_p3_dry_run (
    echo Informe omitido: --p3-dry-run no persiste nada.
    goto :finish
)

:: -- 2. Informe del DRAFT (NO PRODUCCION) ----------------------------------------------------
echo [P3] P3_generateReport --draft
echo --- P3_generateReport --draft >> "%LOG%"
if defined OPT_report_dir (
    call "%LAUNCH%\P3_generateReport.bat" --draft "!OPT_report_dir!"
) else (
    call "%LAUNCH%\P3_generateReport.bat" --draft
)
set "RC=!ERRORLEVEL!"
echo --- P3_generateReport fin: RC=!RC! >> "%LOG%"

:finish
call "%COMMON%" :standby_restore
call "%COMMON%" :log_close "P3 runCycle" !RC!
echo.
if "!RC!"=="0" (
    echo [%STAMP2%] P3 runCycle -- Fin OK
) else (
    echo [%STAMP2%] P3 runCycle -- Fin ERROR ^(RC=!RC!^)
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
echo Uso: P3_runCycle.bat [opciones]
echo   --skip-preflight    no ejecutar el preflight
echo   --scenario ID       scenario_id de la cartera (por defecto autogenerado)
echo   --report-dir DIR    directorio de salida del informe
echo   --p3-dry-run        P3 no persiste (sin auditoria ni informe)
echo   --allow-stale       salta la puerta de frescura de P3
echo   -h, --help          esta ayuda
exit /b 0
