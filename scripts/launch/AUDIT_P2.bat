@echo off
setlocal enabledelayedexpansion

:: Forzar UTF-8 en cmd
chcp 65001 > nul

:: ============================================================
:: AUDIT_P2.bat -- Auditoria P2 (metricas cuantitativas) en un solo comando
::
:: Auditoria estadistica del dominio p2 (fund_metrics / fund_metric_timeseries):
::   scripts\audit\run_statistical_audit.py --domain p2
::
:: Es diagnostico: en --mode report (defecto) siempre devuelve 0, asi que un RC != 0 solo
:: significa que el propio script de auditoria se ha roto (o --mode check con hallazgos).
:: Quien la llama decide si propaga ese RC; P1_P2_Complete.bat nunca aborta por ella.
::
:: Uso:
::   AUDIT_P2.bat                                   informe, sin persistir
::   AUDIT_P2.bat --persist --run-id pre_X_p2       persiste en control.audit_statistic/finding
::   AUDIT_P2.bat --persist --run-id post_X_p2 --compare-to pre_X_p2   cuantifica deriva
::   AUDIT_P2.bat --log C:\ruta\fichero.log         anade la salida a ese log (lo usa el orquestador)
:: Cualquier otro argumento (--mode, --persist, --run-id, --compare-to, --isin ...) se reenvia
:: tal cual a run_statistical_audit.py. Listas con comas entre comillas: --isin "A,B" (cmd parte por comas).
:: ============================================================

set PYTHON=C:\data\envs\des\python.exe
set ROOT=C:\desarrollo\fondos
set LOG_DIR=%ROOT%\out\audit\log

set SHARED_LOG=
set STAT_ARGS=

:parse
if "%~1"=="" goto :parsed
if /i "%~1"=="--log" (
    set SHARED_LOG=%~2
    shift
    shift
    goto :parse
)
set STAT_ARGS=!STAT_ARGS! %1
shift
goto :parse
:parsed

for /f %%a in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set STAMP=%%a
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
if defined SHARED_LOG (set LOG=%SHARED_LOG%) else (set LOG=%LOG_DIR%\log_AUDIT_P2_%STAMP%.log)

echo ============================================================ >> "%LOG%"
echo  AUDIT P2 -- Inicio: %STAMP%                                  >> "%LOG%"
echo  ARGS statistical:%STAT_ARGS%                                 >> "%LOG%"
echo ============================================================ >> "%LOG%"

echo.
echo [%STAMP%] AUDIT P2 iniciado
echo   Log: %LOG%

pushd "%ROOT%"
echo [%time%] Auditoria estadistica p2
echo. >> "%LOG%"
echo --- statistical audit (p2^) --------------------------------- >> "%LOG%"
"%PYTHON%" -u -X utf8 scripts\audit\run_statistical_audit.py --domain p2 !STAT_ARGS! >> "%LOG%" 2>&1
set RC=!ERRORLEVEL!
popd

for /f %%a in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set STAMP2=%%a
echo. >> "%LOG%"
echo ============================================================ >> "%LOG%"
echo  AUDIT P2 -- Fin: %STAMP2% (RC=!RC!^)                         >> "%LOG%"
echo ============================================================ >> "%LOG%"

echo.
if !RC! NEQ 0 (
    echo [%STAMP2%] AUDIT P2 -- Fin ERROR (RC=!RC!^) -- revisar %LOG%
) else (
    echo [%STAMP2%] AUDIT P2 -- Fin OK
)
echo.

:: endlocal discards delayed expansion before !RC! could expand -- chain on one line
:: so %RC% substitutes at parse time, while the scope is still active.
endlocal & exit /b %RC%
