@echo off
setlocal enabledelayedexpansion

:: Forzar UTF-8 en cmd
chcp 65001 > nul

:: ============================================================
:: AUDIT_P1.bat -- Auditoria P1 (clasificacion + costes) en un solo comando
::
:: Pasos:
::   1. Auditoria estadistica del dominio costs (fund_master / fund_cost_schedule):
::      scripts\audit\run_statistical_audit.py --domain costs
::   2. Consistencia de benchmarks B1-B7 (proyecto1\tools\audit_benchmark_consistency.py,
::      solo lectura; JSON en out\audit\benchmark_audit_findings.json)
::
:: Es diagnostico: en --mode report (defecto) el paso 1 siempre devuelve 0, asi que un RC != 0
:: solo significa que el propio script de auditoria se ha roto (o --mode check con hallazgos).
:: Quien la llama decide si propaga ese RC; P1_P2_Complete.bat nunca aborta por ella.
::
:: Uso:
::   AUDIT_P1.bat                                   informe, sin persistir
::   AUDIT_P1.bat --persist --run-id pre_X_costs    persiste en control.audit_statistic/finding
::   AUDIT_P1.bat --persist --run-id post_X_costs --compare-to pre_X_costs   cuantifica deriva
::   AUDIT_P1.bat --no-benchmark                    solo el paso 1
::   AUDIT_P1.bat --log C:\ruta\fichero.log         anade la salida a ese log (lo usa el orquestador)
:: Cualquier otro argumento (--mode, --persist, --run-id, --compare-to, --isin ...) se reenvia
:: tal cual al paso 1. Listas con comas entre comillas: --isin "A,B" (cmd parte por comas).
:: ============================================================

set PYTHON=C:\data\envs\des\python.exe
set ROOT=C:\desarrollo\fondos
set LOG_DIR=%ROOT%\out\audit\log

set SHARED_LOG=
set RUN_BENCH=1
set STAT_ARGS=

:parse
if "%~1"=="" goto :parsed
if /i "%~1"=="--log" (
    set SHARED_LOG=%~2
    shift
    shift
    goto :parse
)
if /i "%~1"=="--no-benchmark" (
    set RUN_BENCH=0
    shift
    goto :parse
)
set STAT_ARGS=!STAT_ARGS! %1
shift
goto :parse
:parsed

for /f %%a in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set STAMP=%%a
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
if defined SHARED_LOG (set LOG=%SHARED_LOG%) else (set LOG=%LOG_DIR%\log_AUDIT_P1_%STAMP%.log)

echo ============================================================ >> "%LOG%"
echo  AUDIT P1 -- Inicio: %STAMP%                                  >> "%LOG%"
echo  ARGS statistical:%STAT_ARGS%   benchmark=%RUN_BENCH%         >> "%LOG%"
echo ============================================================ >> "%LOG%"

echo.
echo [%STAMP%] AUDIT P1 iniciado
echo   Log: %LOG%

pushd "%ROOT%"

echo [%time%] Paso 1/2: auditoria estadistica costs
echo. >> "%LOG%"
echo --- PASO 1: statistical audit (costs^) ---------------------- >> "%LOG%"
"%PYTHON%" -u -X utf8 scripts\audit\run_statistical_audit.py --domain costs !STAT_ARGS! >> "%LOG%" 2>&1
set RC1=!ERRORLEVEL!

set RC2=0
if "!RUN_BENCH!"=="1" (
    echo [%time%] Paso 2/2: consistencia de benchmarks B1-B7
    echo. >> "%LOG%"
    echo --- PASO 2: audit_benchmark_consistency ------------------- >> "%LOG%"
    "%PYTHON%" -u -X utf8 proyecto1\tools\audit_benchmark_consistency.py >> "%LOG%" 2>&1
    set RC2=!ERRORLEVEL!
) else (
    echo --- PASO 2: omitido (--no-benchmark^) ---------------------- >> "%LOG%"
)

popd

set FINAL_RC=0
if !RC1! NEQ 0 set FINAL_RC=!RC1!
if !FINAL_RC! EQU 0 if !RC2! NEQ 0 set FINAL_RC=!RC2!

for /f %%a in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set STAMP2=%%a
echo. >> "%LOG%"
echo ============================================================ >> "%LOG%"
echo  AUDIT P1 -- Fin: %STAMP2% (statistical=!RC1! benchmark=!RC2!^) >> "%LOG%"
echo ============================================================ >> "%LOG%"

echo.
if !FINAL_RC! NEQ 0 (
    echo [%STAMP2%] AUDIT P1 -- Fin ERROR (statistical=!RC1! benchmark=!RC2!^) -- revisar %LOG%
) else (
    echo [%STAMP2%] AUDIT P1 -- Fin OK
)
echo.

:: endlocal discards delayed expansion before !FINAL_RC! could expand -- chain on one line
:: so %FINAL_RC% substitutes at parse time, while the scope is still active.
endlocal & exit /b %FINAL_RC%
