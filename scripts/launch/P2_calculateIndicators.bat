@echo off
setlocal enabledelayedexpansion

:: Forzar UTF-8 en cmd
chcp 65001 > nul

:: ============================================================
:: P2_calculateIndicators.bat  (v30 -- AUDIT_P2 launcher, flags, orchestrator-aware standby)
:: Ejecucion del pipeline de calculo de indicadores cuantitativos
:: (P2: risk_metrics, macro_sensitivity, regime_returns, rolling, ...)
::
:: Uso:
::   P2_calculateIndicators.bat                      pipeline + export + auditoria P2
::   P2_calculateIndicators.bat --force              bypass hash-cache Y gate trimestral OLS
::                                                   (tras bump de CALC_VERSION o rediseno de ventana macro)
::   P2_calculateIndicators.bat --no-audit           sin auditoria P2 (la usa P1_P2_Complete.bat, que
::                                                   ejecuta AUDIT_P2 con baseline/deriva)
::   P2_calculateIndicators.bat --no-export          sin export_metrics
::   P2_calculateIndicators.bat --isin "A,B" --no-export ...   cualquier otro argumento se reenvia a run_pipeline
::                                                   (listas con comas ENTRE COMILLAS: cmd parte los argumentos por comas)
::
:: v30 changes:
::   - La auditoria P2 pasa por AUDIT_P2.bat (antes AUDIT_statistical.bat p2): mismo motor,
::     log propio y misma interfaz --persist/--run-id/--compare-to que usa el orquestador
::   - --no-audit / --no-export / argumentos libres hacia run_pipeline (p. ej. --isin, --max-new-per-run)
::   - Bajo P1_P2_Complete.bat (FONDOS_ORCH=1) NO se toca powercfg: antes este script restauraba el
::     standby a 30 min al terminar el PASO 4, pero tambien lo hacia en solitario mientras el
::     orquestador aun tenia que ejecutar las auditorias finales
::   - v29: AUDIT --mode report tras export OK (diagnostico, no gate hasta promover a --mode check)
::   - v28: export_metrics tras pipeline OK; FINAL_RC combina fases
::   - v27: python -u, exit-code capture inmediato, footer diferenciado, RC a ambos logs
:: ============================================================

set PYTHON=C:\data\envs\des\python.exe
set ROOT=C:\desarrollo\fondos
set LAUNCH=%ROOT%\scripts\launch
set LOG_DIR=%ROOT%\proyecto2\log

set FORCE_FLAG=
set RUN_AUDIT=1
set RUN_EXPORT=1
set PIPE_ARGS=

:parse
if "%~1"=="" goto :parsed
if /i "%~1"=="--force" (
    set FORCE_FLAG=--force
    shift
    goto :parse
)
if /i "%~1"=="--no-audit" (
    set RUN_AUDIT=0
    shift
    goto :parse
)
if /i "%~1"=="--no-export" (
    set RUN_EXPORT=0
    shift
    goto :parse
)
set PIPE_ARGS=!PIPE_ARGS! %1
shift
goto :parse
:parsed

:: Timestamp YYYYMMDD_HHMMSS (wmic removed on newer Windows builds; PowerShell
:: is the portable replacement)
for /f %%a in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set STAMP=%%a
set LOG=%LOG_DIR%\log_P2_calcIndicators_%STAMP%.log
set ERR=%LOG_DIR%\log_P2_calcIndicators_%STAMP%_err.log
set AUDIT_LOG=%LOG_DIR%\log_P2_audit_%STAMP%.log

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo ============================================================ >> "%LOG%"
echo  P2 Calculate Indicators -- Inicio: %STAMP%                  >> "%LOG%"
echo  ROOT:   %ROOT%                                              >> "%LOG%"
echo  PYTHON: %PYTHON%                                            >> "%LOG%"
echo  FORCE:  %FORCE_FLAG%  extra:%PIPE_ARGS%  audit=%RUN_AUDIT% export=%RUN_EXPORT% >> "%LOG%"
echo ============================================================ >> "%LOG%"

echo.
echo [%STAMP%] P2 Calculate Indicators iniciado
echo   Log stdout : %LOG%
echo   Log stderr : %ERR%
echo.

pushd "%ROOT%"

:: -- Prevenir suspension/hibernacion durante la ejecucion ----------------------
:: Standby AC a 0 min = nunca suspender mientras hay alimentacion de red.
:: Bajo el orquestador (FONDOS_ORCH) lo gestiona el, no este script.
if not defined FONDOS_ORCH (
    echo [%time%] Desactivando suspension AC (powercfg standby 0 min^)
    powercfg -change -standby-timeout-ac 0 > nul 2>&1
)

:: -- PIPELINE ------------------------------------------------------------------
echo [%time%] Ejecutando pipeline de calculo de indicadores
echo. >> "%LOG%"
echo --- PIPELINE: run_pipeline ------------------------------- >> "%LOG%"

:: -u : salida sin buffering (cada linea llega al log en tiempo real).
:: Modo prueba (debug de un ISIN): P2_calculateIndicators.bat --isin LU0070214613 --dry-run --no-export --no-audit
"%PYTHON%" -u -X utf8 -m proyecto2.src.pipeline.run_pipeline %FORCE_FLAG% !PIPE_ARGS! >> "%LOG%" 2>> "%ERR%"

:: Capturar codigo de salida INMEDIATAMENTE (antes de cualquier otro comando)
set RC=!ERRORLEVEL!

:: -- EXPORT (solo si pipeline OK) -----------------------------------------------
set RC_EXPORT=0
echo. >> "%LOG%"
echo --- EXPORT: export_metrics ------------------------------- >> "%LOG%"
if "!RUN_EXPORT!"=="0" (
    echo [%time%] Export omitido (--no-export^)
    echo [OMITIDO] Export omitido por --no-export >> "%LOG%"
) else if !RC! NEQ 0 (
    echo [%time%] Pipeline con errores -- export omitido (RC=!RC!^)
    echo [OMITIDO] Export omitido por error en pipeline >> "%LOG%"
) else (
    echo [%time%] Exportando indicadores calculados
    "%PYTHON%" -u -X utf8 -m proyecto2.src.analysis.export_metrics >> "%LOG%" 2>> "%ERR%"
    set RC_EXPORT=!ERRORLEVEL!
)

:: -- AUDIT (solo si pipeline y export OK) -- report mode, no bloquea --------------
set RC_AUDIT=0
echo. >> "%LOG%"
echo --- AUDIT: AUDIT_P2 --mode report ------------------------- >> "%LOG%"
if "!RUN_AUDIT!"=="0" (
    echo [%time%] Auditoria omitida (--no-audit^)
    echo [OMITIDO] Auditoria omitida por --no-audit >> "%LOG%"
) else if !RC! NEQ 0 (
    echo [%time%] Pipeline con errores -- auditoria omitida
    echo [OMITIDO] Auditoria omitida por error en pipeline >> "%LOG%"
) else if !RC_EXPORT! NEQ 0 (
    echo [%time%] Export con errores -- auditoria omitida
    echo [OMITIDO] Auditoria omitida por error en export >> "%LOG%"
) else (
    echo [%time%] Ejecutando auditoria P2 (modo report^)
    echo   log de auditoria: %AUDIT_LOG% >> "%LOG%"
    call "%LAUNCH%\AUDIT_P2.bat" --mode report --log "%AUDIT_LOG%"
    set RC_AUDIT=!ERRORLEVEL!
)

popd

:: -- Restaurar standby AC al valor por defecto de Windows (30 min) -------------
if not defined FONDOS_ORCH (
    echo [%time%] Restaurando suspension AC (powercfg standby 30 min^)
    powercfg -change -standby-timeout-ac 30 > nul 2>&1
)

:: -- Pie del log ---------------------------------------------------------------
for /f %%a in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set STAMP2=%%a

:: FINAL_RC: pipeline tiene precedencia; export en segundo lugar; audit
:: (report mode) solo cuenta si las dos fases anteriores fueron OK -- en report mode
:: la auditoria siempre devuelve 0, asi que RC_AUDIT!=0 solo puede significar un crash
:: del propio script de auditoria, no un finding bloqueante.
set FINAL_RC=!RC!
if !RC! EQU 0 if !RC_EXPORT! NEQ 0 set FINAL_RC=!RC_EXPORT!
if !RC! EQU 0 if !RC_EXPORT! EQU 0 if !RC_AUDIT! NEQ 0 set FINAL_RC=!RC_AUDIT!

echo. >> "%LOG%"
echo ============================================================ >> "%LOG%"

if !FINAL_RC! EQU 0 (
    echo  P2 Calculate Indicators -- Fin OK: %STAMP2%               >> "%LOG%"
    echo  Pipeline RC: 0  /  Export RC: 0  /  Audit RC: 0            >> "%LOG%"
    echo ============================================================ >> "%LOG%"

    echo.
    echo [%STAMP2%] P2 Calculate Indicators -- Fin OK (pipeline=0, export=0, audit=0^)
) else (
    if !RC! NEQ 0 (
        echo  P2 Calculate Indicators -- Fin ERROR (pipeline^): %STAMP2% >> "%LOG%"
        echo  Pipeline RC: !RC!                                      >> "%LOG%"
        echo ============================================================ >> "%LOG%"
        echo  P2 Calculate Indicators -- Fin ERROR (pipeline^): %STAMP2% >> "%ERR%"
        echo  Pipeline RC: !RC!                                      >> "%ERR%"
        echo.
        echo [%STAMP2%] P2 Calculate Indicators -- Fin ERROR pipeline (RC=!RC!^)
    ) else (
        if !RC_EXPORT! NEQ 0 (
            echo  P2 Calculate Indicators -- Fin ERROR (export^): %STAMP2%  >> "%LOG%"
            echo  Pipeline RC: 0  /  Export RC: !RC_EXPORT!                >> "%LOG%"
            echo ============================================================ >> "%LOG%"
            echo  P2 Calculate Indicators -- Fin ERROR (export^): %STAMP2%  >> "%ERR%"
            echo  Export RC: !RC_EXPORT!                                   >> "%ERR%"
            echo.
            echo [%STAMP2%] P2 Calculate Indicators -- Fin ERROR export (RC=!RC_EXPORT!^)
        ) else (
            echo  P2 Calculate Indicators -- Fin ERROR (audit^): %STAMP2%   >> "%LOG%"
            echo  Pipeline RC: 0  /  Export RC: 0  /  Audit RC: !RC_AUDIT!  >> "%LOG%"
            echo ============================================================ >> "%LOG%"
            echo  P2 Calculate Indicators -- Fin ERROR (audit^): %STAMP2%   >> "%ERR%"
            echo  Audit RC: !RC_AUDIT!                                     >> "%ERR%"
            echo.
            echo [%STAMP2%] P2 Calculate Indicators -- Fin ERROR audit (RC=!RC_AUDIT!^)
        )
    )
    echo   Revisar: %ERR%
)

echo   Log stdout : %LOG%
echo   Log stderr : %ERR%
if "!RUN_AUDIT!"=="1" echo   Log audit  : %AUDIT_LOG%
echo.

:: endlocal discards delayed expansion before !FINAL_RC! on the next line
:: could expand it (verified empirically) -- chain on one line so %FINAL_RC%
:: substitutes at parse time, while the scope is still active.
endlocal & exit /b %FINAL_RC%
