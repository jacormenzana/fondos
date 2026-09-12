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

:: Timestamp YYYYMMDD_HHMMSS
for /f "tokens=2 delims==" %%a in ('wmic OS Get localdatetime /value') do set DT=%%a
set STAMP=%DT:~0,8%_%DT:~8,6%
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

%PYTHON% -X utf8 -c ^
"from shared.db import get_connection; from proyecto3.src.monthly_report import generate_report; print(generate_report(get_connection(), output_dir=r'%OUT_DIR%'))" ^
>> "%LOG%" 2>> "%ERR%"

set RC=%ERRORLEVEL%
popd

:: Timestamp de cierre
for /f "tokens=2 delims==" %%a in ('wmic OS Get localdatetime /value') do set DT2=%%a
set STAMP2=%DT2:~0,8%_%DT2:~8,6%

echo.
if %RC% NEQ 0 (
    echo [%STAMP2%] ERROR rc=%RC% -- ver %ERR%
) else (
    echo [%STAMP2%] P3 Generate Report completado
    echo   Excel en: %OUT_DIR%
    echo   Log     : %LOG%
)
echo.

endlocal
