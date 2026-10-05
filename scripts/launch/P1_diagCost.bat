@echo off
setlocal EnableExtensions EnableDelayedExpansion
call "%~dp0lib\common.bat" :init || (endlocal & exit /b 101)
call "%COMMON%" :utf8_on

:: ============================================================
:: P1_diagCost.bat -- Cost Diag standalone (solo diag_cost_extraction)
:: Uso: P1_diagCost.bat   (sin argumentos)
:: Ejecutar desde cualquier ruta.
:: Log generado en: C:\desarrollo\fondos\proyecto1\log\
:: ============================================================

set LOG_DIR=%ROOT%\proyecto1\log
set KIID_DIR=c:\data\fondos\kiid
set DIAG_OUT_DIR=%ROOT%\out\diag

:: -- FIX FATAL: PYTHONPATH requerido por diag_cost_extraction._import_modules()
::    Sin esto: "No module named 'dla_table_serializer' / 'core'".
::    Rutas ABSOLUTAS para ser independientes del CWD.
set PYTHONPATH=%ROOT%\proyecto1;%ROOT%\proyecto1\core;%ROOT%\shared

:: Timestamp YYYYMMDD_HHMMSS (wmic removed on newer Windows builds; PowerShell
:: is the portable replacement)
call "%COMMON%" :get_time STAMP yyyyMMdd_HHmmss
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
echo  KIID_DIR:   %KIID_DIR%                                      >> "%LOG%"
echo  PYTHONPATH: %PYTHONPATH%                                    >> "%LOG%"
echo  OUT:        %DIAG_OUT%                                      >> "%LOG%"
echo ============================================================ >> "%LOG%"

pushd %ROOT%
"%PYTHON%" -X utf8 "%ROOT%\scripts\diag\diag_cost_extraction.py" ^
    --kiid-dir "%KIID_DIR%" ^
    --only-priips ^
    --out "%DIAG_OUT%" >> "%LOG%" 2>&1
set DIAG_RC=!ERRORLEVEL!
popd

call "%COMMON%" :get_time STAMP2 yyyyMMdd_HHmmss

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

:: endlocal discards delayed expansion before !VAR! on the next line could
:: expand it (verified empirically) -- chain on one line so %DIAG_RC%
:: substitutes at parse time, while the scope is still active.
call "%COMMON%" :utf8_off
endlocal & exit /b %DIAG_RC%
