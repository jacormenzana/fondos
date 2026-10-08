@echo off
setlocal EnableExtensions EnableDelayedExpansion
call "%~dp0lib\common.bat" :init "%~nx0" & set "RC_BOOT=!ERRORLEVEL!"
if %RC_BOOT% NEQ 0 (endlocal & exit /b %RC_BOOT%)
call "%COMMON%" :utf8_on

:: ============================================================
:: P1_discoverAllFunds.bat  -- Pipeline P1 completo + export Excel + auditoria P1
:: Ejecutar desde cualquier ruta.
:: Log generado en: C:\desarrollo\fondos\proyecto1\log\
:: Excel generado en: C:\desarrollo\fondos\out\export\
::
:: Pasos: mark_stale -> nature-first -> fund_family_builder -> export_p1 -> AUDIT_P1
::
:: Uso:
::   P1_discoverAllFunds.bat                  ciclo completo
::   P1_discoverAllFunds.bat --no-audit       sin auditoria P1 (la usa P1_P2_Complete.bat, que
::                                            ejecuta AUDIT_P1 por su cuenta con baseline/deriva)
::   P1_discoverAllFunds.bat --no-export      sin export Excel
:: La auditoria P1 (AUDIT_P1.bat: estadistica de costes + consistencia de benchmarks) es
:: diagnostica: va a su propio log (log_P1_audit_<STAMP>.log), no al log del pipeline, para no
:: mezclar sus [WARN] con el triage del pipeline.
:: ============================================================

set LOG_DIR=%ROOT%\proyecto1\log

set RUN_AUDIT=1
set RUN_EXPORT=1

:parse
if "%~1"=="" goto :parsed
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
if /i "%~1"=="-h"     goto :help
if /i "%~1"=="--help" goto :help
echo [ERROR] Argumento desconocido: %~1
echo Uso: P1_discoverAllFunds.bat [--no-audit] [--no-export] [-h]
call "%COMMON%" :utf8_off
endlocal & exit /b %RC_USAGE%
:help
echo Uso: P1_discoverAllFunds.bat [--no-audit] [--no-export] [-h]
echo   --no-audit    sin auditoria P1
echo   --no-export   sin export Excel
call "%COMMON%" :utf8_off
endlocal & exit /b 0
:parsed

:: Timestamp YYYYMMDD_HHMMSS (wmic removed on newer Windows builds; PowerShell
:: is the portable replacement)
call "%COMMON%" :get_time STAMP yyyyMMdd_HHmmss
set LOG=%LOG_DIR%\log_pipeline_%STAMP%.log
set AUDIT_LOG=%LOG_DIR%\log_P1_audit_%STAMP%.log

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo ============================================================ >> "%LOG%"
echo  Pipeline P1 - Inicio: %STAMP%  (audit=%RUN_AUDIT% export=%RUN_EXPORT%^) >> "%LOG%"
echo ============================================================ >> "%LOG%"

echo.
echo [%STAMP%] Pipeline P1 iniciado
echo Log: %LOG%
echo.

:: -- PASO 0: Marcar fondos antiguos para re-descarga (anti-avalancha) --------
:: Marca como FORCE_REFRESH un maximo de 50 fondos con KIID > 180 dias.
:: Distribuye el refresh en ~64 ciclos en lugar de una avalancha de 3000 requests.
echo [%time%] Paso 0: mark_stale (max 50 fondos, antiguedad ^> 180 dias)
echo. >> "%LOG%"
echo --- PASO 0: mark_stale ------------------------------------ >> "%LOG%"
"%PYTHON%" -u -X utf8 "%ROOT%\scripts\launch\mark_stale.py" --max-age 180 --max-funds 50 >> "%LOG%" 2>&1
set RC0=!ERRORLEVEL!

:: -- OPT-B3: single nature-first pass (replaces 7 sequential block runs) -----
:: Nature is resolved by resolve_nature_evidence() per fund: KIID-primary +
:: guarded name-override (Monetario/RFC) + benchmark coverage/corroboration +
:: realized-volatility veto (srri_nav band). The correct block's classify_fund()
:: is then called once. Retires INTER-DBLCLAIM/INTER-VOTE3 (nature resolved once,
:: with a confidence + evidence trace); low-confidence funds get a
:: NATURE_LOW_CONFIDENCE DQ WARNING in ingestion_log instead of silent patching.
::
:: DEPENDENCY (deliberate P1<-P2 feedback, degrades gracefully): the vol veto
:: reads fund_metrics.srri_nav, a P2 output. For it to use FRESH behaviour, run
:: P2 (P2_calculateIndicators.bat) before this pass. Funds without NAV history
:: (srri_nav NULL) are classified ex-ante only (name/KIID/benchmark) -- no error.
::
:: Per-block debug (kept for single-block testing):
::   pushd %ROOT%\proyecto1
::   python -X utf8 run_block.py --block monetarios --master-db
::   popd
:: Para usar el Excel maestro legacy en debug puntual:
::   python -X utf8 run_block.py --block monetarios --master "c:\data\fondos\in\GestoresDeFondosv1.xlsx"
echo [%time%] Clasificacion: NATURE_FIRST (OPT-B3, pasada unica)
echo. >> "%LOG%"
echo --- NATURE_FIRST (OPT-B3) --------------------------------- >> "%LOG%"
pushd "%ROOT%\proyecto1"
"%PYTHON%" -u -X utf8 run_block.py --nature-first --master-db >> "%LOG%" 2>&1
set RC1=!ERRORLEVEL!
popd

:: -- fund_family_builder ------------------------------------------------------
echo [%time%] fund_family_builder
echo. >> "%LOG%"
echo --- fund_family_builder ----------------------------------- >> "%LOG%"
pushd "%ROOT%"
"%PYTHON%" -u -X utf8 -m proyecto1.core.fund_family_builder >> "%LOG%" 2>&1
set RC2=!ERRORLEVEL!
popd

:: -- export_p1 (Excel dump de tablas P1, incl. texto KIID bruto) --------------
set RC3=0
if "!RUN_EXPORT!"=="1" (
    echo [%time%] export_p1 (--include-kiid-text^)
    echo. >> "%LOG%"
    echo --- export_p1 --------------------------------------------- >> "%LOG%"
    pushd "%ROOT%"
    "%PYTHON%" -u -X utf8 -m proyecto1.src.analysis.export_p1 --include-kiid-text >> "%LOG%" 2>&1
    set RC3=!ERRORLEVEL!
    popd
) else (
    echo --- export_p1: omitido (--no-export^) ---------------------- >> "%LOG%"
)

:: -- AUDIT P1 (diagnostico: estadistica de costes + benchmarks B1-B7) ---------
:: Corre siempre que RUN_AUDIT=1, incluso si un paso previo fallo: auditar el estado en que
:: ha quedado la BD es justo lo que ayuda a diagnosticar el fallo.
set RC4=0
if "!RUN_AUDIT!"=="1" (
    echo [%time%] AUDIT P1
    echo. >> "%LOG%"
    echo --- AUDIT P1 (log: %AUDIT_LOG%^) --------------------------- >> "%LOG%"
    call "%LAUNCH%\AUDIT_P1.bat" --mode report --log "%AUDIT_LOG%"
    set RC4=!ERRORLEVEL!
) else (
    echo --- AUDIT P1: omitido (--no-audit^) ------------------------ >> "%LOG%"
)

:: FINAL_RC: primer paso con RC != 0 gana (todos los pasos se ejecutan
:: siempre, sin abortar entre ellos -- este bloque solo hace que el codigo
:: de salida del script refleje honestamente si algo fallo).
set FINAL_RC=0
if !RC0! NEQ 0 set FINAL_RC=!RC0!
if !FINAL_RC! EQU 0 if !RC1! NEQ 0 set FINAL_RC=!RC1!
if !FINAL_RC! EQU 0 if !RC2! NEQ 0 set FINAL_RC=!RC2!
if !FINAL_RC! EQU 0 if !RC3! NEQ 0 set FINAL_RC=!RC3!
if !FINAL_RC! EQU 0 if !RC4! NEQ 0 set FINAL_RC=!RC4!

:: -- Pie del log --------------------------------------------------------------
call "%COMMON%" :get_time STAMP2 yyyyMMdd_HHmmss
echo. >> "%LOG%"
echo ============================================================ >> "%LOG%"
echo  Pipeline P1 - Fin: %STAMP2% (RC0=!RC0! RC1=!RC1! RC2=!RC2! RC3=!RC3! RC4=!RC4!^) >> "%LOG%"
echo ============================================================ >> "%LOG%"

echo.
if !FINAL_RC! NEQ 0 (
    echo [%STAMP2%] Pipeline P1 -- Fin ERROR (RC0=!RC0! RC1=!RC1! RC2=!RC2! RC3=!RC3! RC4=!RC4!^)
) else (
    echo [%STAMP2%] Pipeline P1 completado
)
echo Log: %LOG%
if "!RUN_AUDIT!"=="1" echo Audit log: %AUDIT_LOG%
echo.

:: endlocal discards delayed expansion before !VAR! on the next line could
:: expand it (verified empirically) -- chain on one line so %FINAL_RC%
:: substitutes at parse time, while the scope is still active.
call "%COMMON%" :utf8_off
endlocal & exit /b %FINAL_RC%
