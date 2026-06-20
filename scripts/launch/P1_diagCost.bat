@echo off
setlocal enabledelayedexpansion

:: Forzar UTF-8 en cmd para evitar UnicodeEncodeError con caracteres no-ASCII
chcp 65001 > nul

:: ============================================================
:: P1_diagCost.bat -- Cost Diag standalone (solo diag_cost_extraction)
:: Ejecutar desde cualquier ruta.
:: Log generado en: C:\desarrollo\fondos\proyecto1\log\
:: ============================================================

set ROOT=C:\desarrollo\fondos
set DB=%ROOT%\db\fondos.sqlite
set LOG_DIR=%ROOT%\proyecto1\log
set KIID_DIR=c:\data\fondos\kiid
set DIAG_OUT_DIR=%ROOT%\out\diag

:: -- FIX FATAL: PYTHONPATH requerido por diag_cost_extraction._import_modules()
::    Sin esto: "No module named 'dla_table_serializer' / 'core'".
::    Rutas ABSOLUTAS para ser independientes del CWD.
set PYTHONPATH=%ROOT%\proyecto1;%ROOT%\proyecto1\core;%ROOT%\shared

:: Timestamp YYYYMMDD_HHMMSS
for /f "tokens=2 delims==" %%a in ('wmic OS Get localdatetime /value') do set DT=%%a
set STAMP=%DT:~0,8%_%DT:~8,6%
set LOG=%LOG_DIR%\log_diagcost_%STAMP%.log
set DIAG_OUT=%DIAG_OUT_DIR%\cost_diag_%STAMP%_p1g.csv

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
if not exist "%DIAG_OUT_DIR%" mkdir "%DIAG_OUT_DIR%"

echo.
echo [%STAMP%] Cost Diag iniciado
echo Log: %LOG%
echo.

echo ============================================================ >> "%LOG%"
echo  Cost Diag - Inicio: %STAMP%                                 >> "%LOG%"
echo  DB:         %DB%                                            >> "%LOG%"
echo  KIID_DIR:   %KIID_DIR%                                      >> "%LOG%"
echo  PYTHONPATH: %PYTHONPATH%                                    >> "%LOG%"
echo  OUT:        %DIAG_OUT%                                      >> "%LOG%"
echo ============================================================ >> "%LOG%"

pushd %ROOT%
python -X utf8 "%ROOT%\scripts\diag\diag_cost_extraction.py" ^
    --db "%DB%" ^
    --kiid-dir "%KIID_DIR%" ^
    --only-priips ^
    --out "%DIAG_OUT%" >> "%LOG%" 2>&1
set DIAG_RC=!ERRORLEVEL!
popd

for /f "tokens=2 delims==" %%a in ('wmic OS Get localdatetime /value') do set DT2=%%a
set STAMP2=%DT2:~0,8%_%DT2:~8,6%

echo. >> "%LOG%"
if !DIAG_RC! NEQ 0 (
    echo [ERROR] Cost Diag fallo con codigo !DIAG_RC! - revisar PYTHONPATH/imports >> "%LOG%"
    echo [ERROR] Cost Diag fallo con codigo !DIAG_RC!
) else (
    echo [OK] Cost Diag completado. CSV: %DIAG_OUT% >> "%LOG%"
    echo [OK] Cost Diag completado. CSV: %DIAG_OUT%
)

echo ============================================================ >> "%LOG%"
echo  Cost Diag - Fin: %STAMP2% (RC=!DIAG_RC!)                    >> "%LOG%"
echo ============================================================ >> "%LOG%"

echo.
echo Log: %LOG%
echo.

endlocal
