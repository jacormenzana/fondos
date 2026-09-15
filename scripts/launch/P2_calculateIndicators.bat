@echo off
setlocal enabledelayedexpansion

:: Forzar UTF-8 en cmd
chcp 65001 > nul

:: ============================================================
:: P2_calculateIndicators.bat  (v29 -- audit gate post-export)
:: Ejecucion del pipeline de calculo de indicadores cuantitativos
:: (P2: risk_metrics, macro_sensitivity, regime_returns, rolling, ...)
::
:: v29 changes (P1, 2026-09-15):
::   - AUDIT task: AUDIT_statistical.bat p2 --mode report ejecutado tras
::     export OK -- --mode report SIEMPRE devuelve RC=0 (no bloquea el
::     build); es diagnostico, no gate, hasta que se promueva a --mode
::     check (ver AGENTS.md / doc/reglas/AUDITORIA_ESTADISTICA.md)
::   - RC_AUDIT capturado y propagado; FINAL_RC combina las 3 fases
:: v28 changes:
::   - Export task: export_metrics ejecutado tras pipeline OK
::   - RC_EXPORT capturado y propagado; FINAL_RC combina ambos
::   - Footer diferenciado por fase (pipeline / export)
:: v27 changes:
::   - Python -u flag: line-buffered stdout (no lost lines on kill)
::   - Sleep guard: powercfg desactiva standby AC antes del run
::   - Exit-code capture: RC capturado inmediatamente tras Python
::   - Footer diferenciado: Fin OK vs Fin ERROR (code N)
::   - RC emitido a ambos logs para trazabilidad post-mortem
:: ============================================================

set PYTHON=C:\data\envs\des\python.exe
set ROOT=C:\desarrollo\fondos
set LOG_DIR=%ROOT%\proyecto2\log

:: --force flag: bypass hash-cache AND quarterly OLS gate (use after CALC_VERSION bump
:: or after a macro-window redesign to force full OLS recompute within the same quarter).
:: Usage: P2_calculateIndicators.bat --force
set FORCE_FLAG=
if /i "%~1"=="--force" set FORCE_FLAG=--force

:: Timestamp YYYYMMDD_HHMMSS (wmic removed on newer Windows builds; PowerShell
:: is the portable replacement)
for /f %%a in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set STAMP=%%a
set LOG=%LOG_DIR%\log_P2_calcIndicators_%STAMP%.log
set ERR=%LOG_DIR%\log_P2_calcIndicators_%STAMP%_err.log

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo ============================================================ >> "%LOG%"
echo  P2 Calculate Indicators -- Inicio: %STAMP%                  >> "%LOG%"
echo  ROOT:   %ROOT%                                              >> "%LOG%"
echo  PYTHON: %PYTHON%                                            >> "%LOG%"
echo  FORCE:  %FORCE_FLAG% >> "%LOG%"
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

%PYTHON% -u -X utf8 -m proyecto2.src.pipeline.run_pipeline %FORCE_FLAG% >> "%LOG%" 2>> "%ERR%"

:: Capturar codigo de salida INMEDIATAMENTE (antes de cualquier otro comando)
set RC=!ERRORLEVEL!

:: -- EXPORT (solo si pipeline OK) -----------------------------------------------
echo. >> "%LOG%"
echo --- EXPORT: export_metrics ------------------------------- >> "%LOG%"
if !RC! EQU 0 (
    echo [%time%] Exportando indicadores calculados
    %PYTHON% -u -X utf8 -m proyecto2.src.analysis.export_metrics >> "%LOG%" 2>> "%ERR%"
    set RC_EXPORT=!ERRORLEVEL!
) else (
    echo [%time%] Pipeline con errores -- export omitido (RC=!RC!^)
    echo [OMITIDO] Export omitido por error en pipeline >> "%LOG%"
    set RC_EXPORT=0
)

:: -- AUDIT (solo si pipeline y export OK) -- report mode, no bloquea --------
:: NOTA: "if A if B (...) else (...)" es ambiguo en cmd.exe -- el else solo
:: liga al if interno (B); si A es falso no se ejecuta NADA y RC_AUDIT queda
:: sin definir. Por eso aqui cada rama esta anidada con sus propios parentesis,
:: garantizando que RC_AUDIT se fija en las 3 combinaciones posibles.
echo. >> "%LOG%"
echo --- AUDIT: AUDIT_statistical p2 --mode report ------------- >> "%LOG%"
if !RC! EQU 0 (
    if !RC_EXPORT! EQU 0 (
        echo [%time%] Ejecutando auditoria estadistica P2 (modo report^)
        call "%~dp0AUDIT_statistical.bat" p2 --mode report
        set RC_AUDIT=!ERRORLEVEL!
    ) else (
        echo [%time%] Export con errores -- auditoria omitida
        echo [OMITIDO] Auditoria omitida por error en export >> "%LOG%"
        set RC_AUDIT=0
    )
) else (
    echo [%time%] Pipeline con errores -- auditoria omitida
    echo [OMITIDO] Auditoria omitida por error en pipeline >> "%LOG%"
    set RC_AUDIT=0
)

popd

:: -- Restaurar standby AC al valor por defecto de Windows (30 min) -------------
echo [%time%] Restaurando suspension AC (powercfg standby 30 min)
powercfg -change -standby-timeout-ac 30 > nul 2>&1

:: -- Pie del log ---------------------------------------------------------------
for /f %%a in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set STAMP2=%%a

:: FINAL_RC: pipeline tiene precedencia; export en segundo lugar; audit
:: (report mode) solo se propaga si las dos fases anteriores fueron OK --
:: en report mode run_statistical_audit.py siempre devuelve 0, asi que en
:: la practica RC_AUDIT!=0 hoy solo puede significar un crash del propio
:: script de auditoria, no un finding bloqueante.
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
echo.

:: endlocal discards delayed expansion before !FINAL_RC! on the next line
:: could expand it (verified empirically) -- chain on one line so %FINAL_RC%
:: substitutes at parse time, while the scope is still active.
endlocal & exit /b %FINAL_RC%
