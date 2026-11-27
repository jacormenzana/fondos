@echo off
setlocal EnableExtensions EnableDelayedExpansion
call "%~dp0lib\common.bat" :init "%~nx0" & set "RC_BOOT=!ERRORLEVEL!"
if %RC_BOOT% NEQ 0 (endlocal & exit /b %RC_BOOT%)
call "%COMMON%" :utf8_on

:: ============================================================
:: P3_monthlyDraft.bat -- Construccion mensual programada, SOLO DRAFT (orden permanente del propietario): una vez por mes calendario.
::
:: Lo lanza la tarea programada FondosP3MonthlyDraft (scripts\mnt\sched\register_monthly_cycle_tasks.ps1), que puede dispararse cada dia:
::   1. p3_monthly_draft.py status   si este mes (UTC) ya tiene su DRAFT y una fila de cada sombra encendida, no hace nada (RC 0)
::   2. P3_runCycle.bat              DRAFT + auditoria P2 posterior + informe del DRAFT. NUNCA promueve: la promocion es una orden
::                                   del propietario (p3_promote_scenario.py), que este lanzador no invoca
::   3. p3_monthly_draft.py record   una fila de ingestion_log (paso P3_MONTHLY_DRAFT): OK, FAILED o INCOMPLETE
:: Un dia fallido (entradas obsoletas, otra instancia, BD caida) se reintenta solo al dia siguiente. Comparte el bloqueo de instancia unica.
:: FND-0454: si al DRAFT del mes le falta la auditoria P2 posterior o el informe, se reintenta SOLO eso, sin reconstruir; y si una sombra sigue sin fila
:: tras 3 construcciones del mes, ya no se reconstruye (RC 7, visible en la tarea; una construccion a mano la captura).
::
:: Uso:  P3_monthlyDraft.bat [--check]
::   --check     solo dice si este mes necesita construccion; no construye ni escribe nada
::   -h, --help  esta ayuda
:: Codigos de salida: 0 OK (construido y capturado, o ya capturado) | 7 sombra sin fila tras los reintentos del mes | 100 argumentos invalidos | 101 interprete no encontrado |
::   105 otra instancia en ejecucion | 106 driver PostgreSQL bloqueado | 1-99 = RC propagado (2 = entradas obsoletas de P3,
::   5 = construccion RC 0 pero falta una fila del mes, otros = el paso que fallo).
:: ============================================================

:: -- Configuracion --------------------------------------------------------------
set "LOG_DIR=%ROOT%\proyecto3\log"
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
set "FOLLOW_AUDIT="
set "FOLLOW_REPORT="
cd /d "%ROOT%"
call "%COMMON%" :log_open "%LOG_DIR%" P3_monthlyDraft "P3 monthlyDraft"
echo [%STAMP%] P3 monthlyDraft iniciado
echo   Log: %LOG%

:: -- 1. Hace falta construir este mes? ------------------------------------------------
"%PYTHON%" -u -X utf8 scripts\launch\p3_monthly_draft.py status >> "%LOG%" 2>> "%ERR%"
set "ST=!ERRORLEVEL!"
call "%COMMON%" :tail "%LOG%" 1
if "!ST!"=="3" (
    echo Este mes ya esta capturado: no se construye nada.
    goto :finish
)
if "!ST!"=="7" (
    echo [ERROR] Una sombra sigue sin fila tras los reintentos del mes: no se reconstruye. Detalle: %LOG%
    set "RC=!ST!"
    goto :finish
)
if "!ST!"=="8" set "FOLLOW_AUDIT=1"
if "!ST!"=="9" set "FOLLOW_REPORT=1"
if "!ST!"=="10" set "FOLLOW_AUDIT=1"
if "!ST!"=="10" set "FOLLOW_REPORT=1"
if defined FOLLOW_AUDIT goto :followup
if defined FOLLOW_REPORT goto :followup
if !ST! NEQ 0 (
    echo [ERROR] La comprobacion del mes fallo ^(RC=!ST!^). Detalle: %ERR%
    set "RC=!ST!"
    goto :finish
)
if defined FLAG_check (
    echo --check: este mes necesita construccion; no se construye nada.
    goto :finish
)

:: -- 2. DRAFT + auditoria P2 posterior + informe (nunca promueve) ------------------------
echo --- P3_runCycle >> "%LOG%"
call "%LAUNCH%\P3_runCycle.bat"
set "RC=!ERRORLEVEL!"
echo --- P3_runCycle fin: RC=!RC! >> "%LOG%"

:: -- 3. Registro del intento ----------------------------------------------------------
"%PYTHON%" -u -X utf8 scripts\launch\p3_monthly_draft.py record --rc !RC! >> "%LOG%" 2>> "%ERR%"
set "REC=!ERRORLEVEL!"
call "%COMMON%" :tail "%LOG%" 1
if "!RC!"=="0" if !REC! NEQ 0 (
    echo [ERROR] La construccion termino bien pero falta una fila del mes ^(RC=!REC!^). Detalle: %LOG%
    set "RC=!REC!"
)

goto :finish

:: -- Reintento de la evidencia del DRAFT del mes, SIN reconstruir (no mueve ninguna fila de sombra) -------------
:followup
if defined FLAG_check (
    echo --check: al DRAFT del mes le falta evidencia; no se ejecuta nada.
    goto :finish
)
call "%COMMON%" :standby_disable owner
if not defined FOLLOW_AUDIT goto :followup_report
call "%COMMON%" :get_time AUD_STAMP yyyyMMdd_HHmmss
echo --- P2_audit post_!AUD_STAMP!_p2 >> "%LOG%"
call "%LAUNCH%\P2_audit.bat" --mode report --persist --run-id post_!AUD_STAMP!_p2 --log "%LOG_DIR%\log_P3_auditP2_!AUD_STAMP!.log" > nul
set "RC=!ERRORLEVEL!"
echo --- P2_audit fin: RC=!RC! >> "%LOG%"
:followup_report
if not defined FOLLOW_REPORT goto :followup_record
echo --- P3_generateReport --draft >> "%LOG%"
call "%LAUNCH%\P3_generateReport.bat" --draft
set "REP=!ERRORLEVEL!"
echo --- P3_generateReport fin: RC=!REP! >> "%LOG%"
if "!RC!"=="0" set "RC=!REP!"
:followup_record
call "%COMMON%" :standby_restore
"%PYTHON%" -u -X utf8 scripts\launch\p3_monthly_draft.py record --rc !RC! --followup >> "%LOG%" 2>> "%ERR%"
set "REC=!ERRORLEVEL!"
call "%COMMON%" :tail "%LOG%" 1
if "!RC!"=="0" if !REC! NEQ 0 (
    echo [ERROR] El reintento termino bien pero aun falta algo del mes ^(RC=!REC!^). Detalle: %LOG%
    set "RC=!REC!"
)

:finish
call "%COMMON%" :log_close "P3 monthlyDraft" !RC!
echo [%STAMP2%] P3 monthlyDraft -- Fin ^(RC=!RC!^)
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
echo Uso: P3_monthlyDraft.bat [--check]
echo   --check     solo dice si este mes necesita construccion; no construye ni escribe nada
echo   -h, --help  esta ayuda
exit /b 0
