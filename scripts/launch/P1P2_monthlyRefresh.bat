@echo off
setlocal EnableExtensions EnableDelayedExpansion
call "%~dp0lib\common.bat" :init "%~nx0" & set "RC_BOOT=!ERRORLEVEL!"
if %RC_BOOT% NEQ 0 (endlocal & exit /b %RC_BOOT%)
call "%COMMON%" :utf8_on

:: ============================================================
:: P1P2_monthlyRefresh.bat -- Ciclo P1+P2 mensual programado (refresco de NAV) para que la construccion mensual SOLO DRAFT tenga datos frescos.
::
:: Lo lanza la tarea programada FondosP1P2MonthlyRefresh (scripts\mnt\sched\register_monthly_cycle_tasks.ps1), que puede dispararse cada dia:
::   1. p1p2_monthly_refresh.py status   fuera de la ventana (desde el dia 25) o con datos que la construccion del mes siguiente aceptaria
::                                       hasta su ultimo dia de reintento, no hace nada (RC 0)
::   2. P1P2_runCycle.bat                el ciclo P1+P2 con sus opciones por defecto (harvest en solo lectura: nunca --harvest-sync ni --harvest-retire)
::   3. p1p2_monthly_refresh.py record   una fila de ingestion_log (paso P1P2_MONTHLY_REFRESH): OK, FAILED o STALE
:: Un dia fallido (otra instancia, BD caida, fuentes de NAV aun sin datos) se reintenta solo al dia siguiente. Comparte el bloqueo de instancia unica.
:: La orden permanente del propietario cubre SOLO este ciclo de ingestion y calculo: nada aqui promueve, cambia interruptores ni sincroniza KIID.
::
:: Uso:  P1P2_monthlyRefresh.bat [--check]
::   --check     solo dice si el ciclo toca hoy; no ejecuta ni escribe nada
::   -h, --help  esta ayuda
:: Codigos de salida: 0 OK (ciclo hecho con datos frescos, o nada que hacer) | 100 argumentos invalidos | 101 interprete no encontrado |
::   105 otra instancia en ejecucion | 106 driver PostgreSQL bloqueado | 1-99 = RC propagado (6 = ciclo RC 0 pero los datos siguen viejos,
::   otros = el paso que fallo) | 102-107 propios del ciclo P1+P2, propagados.
:: ============================================================

:: -- Configuracion --------------------------------------------------------------
set "LOG_DIR=%ROOT%\proyecto1\log"
set "LOCK_FILE=%STATE_DIR%\fondos_cycle.lock"
set "BOOL_OPTS=--check"

:: -- Argumentos ------------------------------------------------------------------------
for /f "delims==" %%V in ('set FLAG_ 2^>nul') do set "%%V="
:parse
if "%~1"=="" goto :parsed
for %%O in (%BOOL_OPTS%) do if /i "%~1"=="%%O" goto :parse_bool
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
:parsed

:: -- Instancia unica (ver P1P2_runCycle.bat) --------------------------------------------
if not exist "%STATE_DIR%" mkdir "%STATE_DIR%"
set "LOCK_HELD="
if defined FONDOS_CYCLE_LOCK (call :cycle) else (call :cycle 9>"%LOCK_FILE%")
set "RC=!ERRORLEVEL!"
if not defined LOCK_HELD (
    echo [ERROR] Otro ciclo esta en ejecucion ^(%LOCK_FILE%^). No se hace nada; la tarea lo reintenta manana.
    set "RC=%RC_BUSY%"
)
call "%COMMON%" :utf8_off
endlocal & exit /b %RC%


:: ############################################################
:: :cycle -- solo se ejecuta con el bloqueo adquirido.
:: ############################################################
:cycle
set "LOCK_HELD=1"
set "FONDOS_CYCLE_LOCK=1"
set "RC=0"
cd /d "%ROOT%"
call "%COMMON%" :log_open "%LOG_DIR%" P1P2_monthlyRefresh "P1P2 monthlyRefresh"
echo [%STAMP%] P1P2 monthlyRefresh iniciado
echo   Log: %LOG%

:: -- 1. Toca el ciclo hoy? ------------------------------------------------------------
"%PYTHON%" -u -X utf8 scripts\launch\p1p2_monthly_refresh.py status >> "%LOG%" 2>> "%ERR%"
set "ST=!ERRORLEVEL!"
call "%COMMON%" :tail "%LOG%" 1
if "!ST!"=="3" (
    echo Nada que hacer hoy.
    goto :finish
)
if !ST! NEQ 0 (
    echo [ERROR] La comprobacion fallo ^(RC=!ST!^). Detalle: %ERR%
    set "RC=!ST!"
    goto :finish
)
if defined FLAG_check (
    echo --check: el ciclo toca hoy; no se ejecuta nada.
    goto :finish
)

:: -- 2. Ciclo P1+P2 (opciones por defecto: harvest en solo lectura) ---------------------
echo --- P1P2_runCycle >> "%LOG%"
call "%LAUNCH%\P1P2_runCycle.bat"
set "RC=!ERRORLEVEL!"
echo --- P1P2_runCycle fin: RC=!RC! >> "%LOG%"

:: -- 3. Registro del intento ----------------------------------------------------------
"%PYTHON%" -u -X utf8 scripts\launch\p1p2_monthly_refresh.py record --rc !RC! >> "%LOG%" 2>> "%ERR%"
set "REC=!ERRORLEVEL!"
call "%COMMON%" :tail "%LOG%" 1
if "!RC!"=="0" if !REC! NEQ 0 (
    echo [ERROR] El ciclo termino bien pero los datos siguen viejos para la proxima construccion ^(RC=!REC!^). Detalle: %LOG%
    set "RC=!REC!"
)

:finish
call "%COMMON%" :log_close "P1P2 monthlyRefresh" !RC!
echo [%STAMP2%] P1P2 monthlyRefresh -- Fin ^(RC=!RC!^)
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
echo Uso: P1P2_monthlyRefresh.bat [--check]
echo   --check     solo dice si el ciclo toca hoy; no ejecuta ni escribe nada
echo   -h, --help  esta ayuda
exit /b 0
