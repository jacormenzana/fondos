@echo off
setlocal enabledelayedexpansion

:: Forzar UTF-8 en cmd
chcp 65001 > nul

:: ============================================================
:: P2_calculateIndicators.bat  (v27 — fault-tolerant)
:: Ejecucion del pipeline de calculo de indicadores cuantitativos
:: (P2: risk_metrics, macro_sensitivity, regime_returns, rolling, ...)
::
:: v27 changes:
::   - Python -u flag: line-buffered stdout (no lost lines on kill)
::   - Sleep guard: powercfg desactiva standby AC antes del run
::   - Exit-code capture: RC capturado inmediatamente tras Python
::   - Footer diferenciado: Fin OK vs Fin ERROR (code N)
::   - RC emitido a ambos logs para trazabilidad post-mortem
:: ============================================================

set PYTHON=C:\Users\Administrador\anaconda3\envs\des\python.exe
set ROOT=C:\desarrollo\fondos
set LOG_DIR=%ROOT%\proyecto2\log

:: Timestamp YYYYMMDD_HHMMSS
for /f "tokens=2 delims==" %%a in ('wmic OS Get localdatetime /value') do set DT=%%a
set STAMP=%DT:~0,8%_%DT:~8,6%
set LOG=%LOG_DIR%\log_P2_calcIndicators_%STAMP%.log
set ERR=%LOG_DIR%\log_P2_calcIndicators_%STAMP%_err.log

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo ============================================================ >> "%LOG%"
echo  P2 Calculate Indicators -- Inicio: %STAMP%                  >> "%LOG%"
echo  ROOT:   %ROOT%                                              >> "%LOG%"
echo  PYTHON: %PYTHON%                                            >> "%LOG%"
echo ============================================================ >> "%LOG%"

echo.
echo [%STAMP%] P2 Calculate Indicators iniciado
echo   Log stdout : %LOG%
echo   Log stderr : %ERR%
echo.

pushd "%ROOT%"

:: -- Prevenir suspension/hibernacion durante la ejecucion ----------------------
:: Standbay AC a 0 min = nunca suspender mientras hay alimentacion de red.
:: Se restaura en el pie del script.
echo [%time%] Desactivando suspension AC (powercfg standby 0 min)
powercfg -change -standby-timeout-ac 0 > nul 2>&1

:: -- PIPELINE ------------------------------------------------------------------
echo [%time%] Ejecutando pipeline de calculo de indicadores
echo. >> "%LOG%"
echo --- PIPELINE: run_pipeline ------------------------------- >> "%LOG%"

:: -u : salida sin buffering (cada linea llega al log en tiempo real).
:: Modo prueba (descomentar para debug de un ISIN):
:: %PYTHON% -u -X utf8 -m proyecto2.src.pipeline.run_pipeline --isin LU0070214613 --dry-run >> "%LOG%" 2>> "%ERR%"

%PYTHON% -u -X utf8 -m proyecto2.src.pipeline.run_pipeline >> "%LOG%" 2>> "%ERR%"

:: Capturar codigo de salida INMEDIATAMENTE (antes de cualquier otro comando)
set RC=!ERRORLEVEL!

popd

:: -- Restaurar standby AC al valor por defecto de Windows (30 min) -------------
echo [%time%] Restaurando suspension AC (powercfg standby 30 min)
powercfg -change -standby-timeout-ac 30 > nul 2>&1

:: -- Pie del log ---------------------------------------------------------------
for /f "tokens=2 delims==" %%a in ('wmic OS Get localdatetime /value') do set DT2=%%a
set STAMP2=%DT2:~0,8%_%DT2:~8,6%

echo. >> "%LOG%"
echo ============================================================ >> "%LOG%"

if !RC! EQU 0 (
    echo  P2 Calculate Indicators -- Fin OK: %STAMP2%               >> "%LOG%"
    echo  Codigo de salida: 0                                        >> "%LOG%"
    echo ============================================================ >> "%LOG%"

    echo.
    echo [%STAMP2%] P2 Calculate Indicators -- Fin OK (RC=0^)
) else (
    echo  P2 Calculate Indicators -- Fin ERROR: %STAMP2%             >> "%LOG%"
    echo  Codigo de salida: !RC!                                     >> "%LOG%"
    echo ============================================================ >> "%LOG%"
    echo  P2 Calculate Indicators -- Fin ERROR: %STAMP2%             >> "%ERR%"
    echo  Codigo de salida: !RC!                                     >> "%ERR%"

    echo.
    echo [%STAMP2%] P2 Calculate Indicators -- Fin ERROR (RC=!RC!^)
    echo   Revisar: %ERR%
)

echo   Log stdout : %LOG%
echo   Log stderr : %ERR%
echo.

endlocal
exit /b !RC!
