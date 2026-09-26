@echo off
setlocal enabledelayedexpansion

:: Forzar UTF-8 en cmd
chcp 65001 > nul

:: ============================================================
:: P3_generateReport.bat
:: Genera el informe mensual de cartera (Excel, 5 hojas)
:: Modulo: proyecto3.src.monthly_report.generate_report
:: Output:  C:\desarrollo\fondos\out\export\informe_cartera_YYYYMMDD.xlsx
:: ============================================================

set PYTHON=C:\data\envs\des\python.exe
set ROOT=C:\desarrollo\fondos
set OUT_DIR=%ROOT%\out\export
set LOG_DIR=%ROOT%\proyecto3\log

:: Timestamp YYYYMMDD_HHMMSS (wmic removed on newer Windows builds; PowerShell
:: is the portable replacement)
for /f %%a in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set STAMP=%%a
set LOG=%LOG_DIR%\log_P3_report_%STAMP%.log
set ERR=%LOG_DIR%\log_P3_report_%STAMP%_err.log

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
if not exist "%OUT_DIR%"  mkdir "%OUT_DIR%"

echo ============================================================
echo  P3 Generate Report -- Inicio: %STAMP%
echo  Export : %OUT_DIR%
echo  Log    : %LOG%
echo ============================================================
echo.

pushd "%ROOT%"

%PYTHON% -X utf8 -c "from shared.db import get_connection; from proyecto3.src.monthly_report import generate_report; print(generate_report(get_connection(), output_dir=r'%OUT_DIR%'))" >> "%LOG%" 2>> "%ERR%"

set RC=%ERRORLEVEL%
popd

:: Timestamp de cierre
for /f %%a in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set STAMP2=%%a

echo.
if %RC% NEQ 0 (
    echo [%STAMP2%] ERROR rc=%RC% -- ver %ERR%
) else (
    echo [%STAMP2%] P3 Generate Report completado
    echo   Excel en: %OUT_DIR%
    echo   Log     : %LOG%
)
echo.

:: endlocal discards delayed expansion before !VAR! on the next line could
:: expand it (verified empirically) -- chain on one line so %RC%
:: substitutes at parse time, while the scope is still active.
endlocal & exit /b %RC%
