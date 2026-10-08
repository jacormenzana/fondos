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
:: Uso:
::   P3_generateReport.bat                 informe en out\export
::   P3_generateReport.bat C:\otra\ruta    directorio de salida alternativo
:: ============================================================

set OUT_DIR=%ROOT%\out\export
if not "%~1"=="" set OUT_DIR=%~1
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

:: Un one-liner: cmd no admite cadenas multilinea. Si generate_report falla, la excepcion sale por
:: stderr (RC 1) y el proceso termina, lo que cierra la conexion.
"%PYTHON%" -u -X utf8 -c "from shared.db import get_connection; from proyecto3.src.monthly_report import generate_report; c = get_connection(); print(generate_report(c, output_dir=r'%OUT_DIR%')); c.close()" >> "%LOG%" 2>> "%ERR%"
set RC=!ERRORLEVEL!
popd

if defined TELEM_OWNER (
    call "%COMMON%" :telemetry step-end P3_REPORT !RC!
    if "!RC!"=="0" (call "%COMMON%" :telemetry end --status OK --rc !RC!) else (call "%COMMON%" :telemetry end --status FAILED --rc !RC! --failed-step P3_REPORT)
)

:: Timestamp de cierre
call "%COMMON%" :get_time STAMP2 yyyyMMdd_HHmmss

echo.
if !RC! NEQ 0 (
    echo [%STAMP2%] ERROR rc=!RC! -- ver %ERR%
    call "%COMMON%" :tail "%ERR%" 15
) else (
    echo [%STAMP2%] P3 Generate Report completado
    echo   Excel en: %OUT_DIR%
    echo   Log     : %LOG%
)
echo.

:: endlocal discards delayed expansion before !VAR! on the next line could
:: expand it (verified empirically) -- chain on one line so %RC%
:: substitutes at parse time, while the scope is still active.
call "%COMMON%" :utf8_off
endlocal & exit /b %RC%
