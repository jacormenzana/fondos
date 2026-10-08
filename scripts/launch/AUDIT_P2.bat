@echo off
setlocal EnableExtensions EnableDelayedExpansion
call "%~dp0lib\common.bat" :init "%~nx0" & set "RC_BOOT=!ERRORLEVEL!"
if %RC_BOOT% NEQ 0 (endlocal & exit /b %RC_BOOT%)
call "%COMMON%" :utf8_on

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

:: -h / --help: the usage (this header) and out, BEFORE anything runs (NORMAS_BATCH.md section 3). This launcher forwards its arguments to
:: a real run, so an argument it did not recognise used to start one (2026-10-08: `--help` over the real launchers).
for %%A in (%*) do (
    if /i "%%~A"=="-h" goto :show_help
    if /i "%%~A"=="--help" goto :show_help
)

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

call "%COMMON%" :get_time STAMP yyyyMMdd_HHmmss
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

call "%COMMON%" :get_time STAMP2 yyyyMMdd_HHmmss
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
call "%COMMON%" :utf8_off
endlocal & exit /b %RC%


:: ------------------------------------------------------------
:: :show_help -- usage = the header of this file (lib\batch_helpers.py usage); RC 0, nothing else runs.
:: ------------------------------------------------------------
:show_help
"%PYTHON%" "%LIB%\batch_helpers.py" usage "%~f0"
call "%COMMON%" :utf8_off
endlocal & exit /b 0
