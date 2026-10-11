@echo off
setlocal EnableExtensions EnableDelayedExpansion
call "%~dp0lib\common.bat" :init "%~nx0" & set "RC_BOOT=!ERRORLEVEL!"
if %RC_BOOT% NEQ 0 (endlocal & exit /b %RC_BOOT%)
call "%COMMON%" :utf8_on

:: ============================================================
:: P3_generateReport.bat
:: Genera el informe mensual de cartera (Excel, 6 hojas) a partir de lo ya persistido
:: Modulo: proyecto3.src.monthly_report.generate_report
:: Output:  C:\desarrollo\fondos\out\export\informe_cartera_YYYYMMDD.xlsx
::
:: Informa el escenario en PRODUCCION (proyecto3\src\scenario_lifecycle.py). Sin escenario en PRODUCCION no genera nada y
:: termina con RC 3; --scenario ID informa otro y --draft el DRAFT mas reciente (el que acaba de construir el ciclo, antes de
:: promoverlo): ambos quedan marcados NO PRODUCCION y con otro nombre de fichero.
::
:: Uso:
::   P3_generateReport.bat                         informe del escenario en PRODUCCION, en out\export
::   P3_generateReport.bat C:\otra\ruta            directorio de salida alternativo
::   P3_generateReport.bat --scenario ID           informe de un escenario concreto (marcado NO PRODUCCION)
::   P3_generateReport.bat --draft                 informe del DRAFT mas reciente (marcado NO PRODUCCION)
::   P3_generateReport.bat -h                      esta ayuda
:: Codigos de salida: 0 OK | 3 sin escenario que informar | 100 argumentos invalidos | 101 interprete no encontrado |
::   106 driver PostgreSQL bloqueado | 1-99 = RC de la herramienta.
:: ============================================================

:: -h / --help: the usage (this header) and out, BEFORE anything runs (NORMAS_BATCH.md section 3). This launcher forwards its arguments to
:: a real run, so an argument it did not recognise used to start one (2026-10-08: `--help` over the real launchers).
for %%A in (%*) do (
    if /i "%%~A"=="-h" goto :show_help
    if /i "%%~A"=="--help" goto :show_help
)

set "OUT_DIR=%ROOT%\out\export"
set "SCEN_ARG="
:parse_args
if "%~1"=="" goto :args_done
if /i "%~1"=="--scenario" (
    if "%~2"=="" goto :bad_args
    set "SCEN_ARG=--scenario %~2"
    shift
    shift
    goto :parse_args
)
if /i "%~1"=="--draft" (
    set "SCEN_ARG=--draft"
    shift
    goto :parse_args
)
set "OUT_DIR=%~1"
shift
goto :parse_args
:args_done
set LOG_DIR=%ROOT%\proyecto3\log

:: Timestamp YYYYMMDD_HHMMSS (wmic removed on newer Windows builds; PowerShell
:: is the portable replacement)
call "%COMMON%" :get_time STAMP yyyyMMdd_HHmmss
set LOG=%LOG_DIR%\log_P3_report_%STAMP%.log
set ERR=%LOG_DIR%\log_P3_report_%STAMP%_err.log

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
if not exist "%OUT_DIR%"  mkdir "%OUT_DIR%"

echo ============================================================ >> "%LOG%"
echo  P3 Generate Report -- Inicio: %STAMP%                       >> "%LOG%"
echo  Export : %OUT_DIR%                                          >> "%LOG%"
echo ============================================================ >> "%LOG%"

echo ============================================================
echo  P3 Generate Report -- Inicio: %STAMP%
echo  Export : %OUT_DIR%
echo  Log    : %LOG%
echo ============================================================
echo.

:: Telemetria de ciclo (opt-in: FONDOS_TELEMETRY=1; mejor esfuerzo, nunca cambia el RC). Solo si nadie mas es dueno del ciclo.
set "TELEM_OWNER="
if "%FONDOS_TELEMETRY%"=="1" if not defined FONDOS_CYCLE_ID (
    set "FONDOS_CYCLE_ID=%STAMP%"
    set "TELEM_OWNER=1"
    call "%COMMON%" :telemetry begin --launcher P3_generateReport
    call "%COMMON%" :telemetry step-begin P3_REPORT
)

pushd "%ROOT%"

:: p3_generate_report.py: sin escenario que informar sale con RC 3 (mensaje en el log); una excepcion sale por stderr (RC 1).
"%PYTHON%" -u -X utf8 "%LAUNCH%\p3_generate_report.py" --output-dir "%OUT_DIR%" !SCEN_ARG! >> "%LOG%" 2>> "%ERR%"
set RC=!ERRORLEVEL!
popd

if defined TELEM_OWNER (
    call "%COMMON%" :telemetry step-end P3_REPORT !RC!
    if "!RC!"=="0" (call "%COMMON%" :telemetry end --status OK --rc !RC!) else (call "%COMMON%" :telemetry end --status FAILED --rc !RC! --failed-step P3_REPORT)
)

:: Timestamp de cierre
call "%COMMON%" :get_time STAMP2 yyyyMMdd_HHmmss

set "SHOWN_DIR=!OUT_DIR!"
if defined SCEN_ARG set "SHOWN_DIR=!OUT_DIR! (un escenario que no esta en PRODUCCION va a la subcarpeta borradores)"

echo.
if !RC! NEQ 0 (
    echo [%STAMP2%] ERROR rc=!RC! -- ver %ERR%
    call "%COMMON%" :tail "%ERR%" 15
) else (
    echo [%STAMP2%] P3 Generate Report completado
    echo   Excel en: !SHOWN_DIR!
    echo   Log     : %LOG%
)
echo.

:: endlocal discards delayed expansion before !VAR! on the next line could
:: expand it (verified empirically) -- chain on one line so %RC%
:: substitutes at parse time, while the scope is still active.
call "%COMMON%" :utf8_off
endlocal & exit /b %RC%


:: ------------------------------------------------------------
:: :bad_args -- argumentos invalidos: RC_USAGE (100), nada se ejecuta. Etiqueta aparte: un exit /b dentro de bloques anidados
:: no devolvia el codigo (verificado en el arbol simulado).
:: ------------------------------------------------------------
:bad_args
echo [ERROR] --scenario requiere un id de escenario
call "%COMMON%" :utf8_off
endlocal & exit /b %RC_USAGE%


:: ------------------------------------------------------------
:: :show_help -- usage = the header of this file (lib\batch_helpers.py usage); RC 0, nothing else runs.
:: ------------------------------------------------------------
:show_help
"%PYTHON%" "%LIB%\batch_helpers.py" usage "%~f0"
call "%COMMON%" :utf8_off
endlocal & exit /b 0
