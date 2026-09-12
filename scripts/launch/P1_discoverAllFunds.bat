@echo off
setlocal enabledelayedexpansion

:: Forzar UTF-8 en cmd para evitar UnicodeEncodeError con caracteres no-ASCII
:: en el fichero de log generado por >> redireccion
chcp 65001 > nul

:: ============================================================
:: P1_discoverAllFundsPlusExport.bat  -- Pipeline P1 completo + export Excel
:: Ejecutar desde: C:\desarrollo\fondos\scripts\launch\
:: Log generado en: C:\desarrollo\fondos\proyecto1\log\
:: Excel generado en: C:\desarrollo\fondos\out\export\
:: ============================================================

set ROOT=C:\desarrollo\fondos
set DB=%ROOT%\db\fondos.sqlite
set LOG_DIR=%ROOT%\proyecto1\log

:: Resolve bare 'python' calls below to the 'des' Conda env (bare python on
:: PATH otherwise hits the WindowsApps shim -> "Permission denied").
set PATH=C:\data\envs\des;C:\data\envs\des\Scripts;%PATH%

:: Timestamp YYYYMMDD_HHMMSS (wmic removed on newer Windows builds; PowerShell
:: is the portable replacement)
for /f %%a in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set STAMP=%%a
set LOG=%LOG_DIR%\log_pipeline_%STAMP%.log

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo ============================================================ >> "%LOG%"
echo  Pipeline P1 - Inicio: %STAMP%                              >> "%LOG%"
echo  DB:     %DB%                                                >> "%LOG%"
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
python -X utf8 "%ROOT%\scripts\launch\mark_stale.py" --db "%DB%" --max-age 180 --max-funds 50 >> "%LOG%" 2>&1
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
::   python -X utf8 run_block.py --block monetarios --db "%DB%" --master-db
::   popd
:: Para usar el Excel maestro legacy en debug puntual:
::   python -X utf8 run_block.py --block monetarios --db "%DB%" --master "c:\data\fondos\in\GestoresDeFondosv1.xlsx"
echo [%time%] Clasificacion: NATURE_FIRST (OPT-B3, pasada unica)
echo. >> "%LOG%"
echo --- NATURE_FIRST (OPT-B3) --------------------------------- >> "%LOG%"
pushd %ROOT%\proyecto1
python -X utf8 run_block.py --nature-first --db "%DB%" --master-db >> "%LOG%" 2>&1
set RC1=!ERRORLEVEL!
popd

:: -- fund_family_builder ------------------------------------------------------
echo [%time%] fund_family_builder
echo. >> "%LOG%"
echo --- fund_family_builder ----------------------------------- >> "%LOG%"
pushd %ROOT%
python -X utf8 -m proyecto1.core.fund_family_builder >> "%LOG%" 2>&1
set RC2=!ERRORLEVEL!
popd

:: -- export_p1 (Excel dump de tablas P1, incl. texto KIID bruto) --------------
echo [%time%] export_p1 (--include-kiid-text)
echo. >> "%LOG%"
echo --- export_p1 --------------------------------------------- >> "%LOG%"
pushd %ROOT%
python -X utf8 -m proyecto1.src.analysis.export_p1 --include-kiid-text --db "%DB%" >> "%LOG%" 2>&1
set RC3=!ERRORLEVEL!
popd

:: FINAL_RC: primer paso con RC != 0 gana (todos los pasos se ejecutan
:: siempre, sin abortar entre ellos -- este bloque solo hace que el codigo
:: de salida del script refleje honestamente si algo fallo).
set FINAL_RC=0
if !RC0! NEQ 0 set FINAL_RC=!RC0!
if !FINAL_RC! EQU 0 if !RC1! NEQ 0 set FINAL_RC=!RC1!
if !FINAL_RC! EQU 0 if !RC2! NEQ 0 set FINAL_RC=!RC2!
if !FINAL_RC! EQU 0 if !RC3! NEQ 0 set FINAL_RC=!RC3!

:: -- Pie del log --------------------------------------------------------------
for /f %%a in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set STAMP2=%%a
echo. >> "%LOG%"
echo ============================================================ >> "%LOG%"
echo  Pipeline P1 - Fin: %STAMP2% (RC0=!RC0! RC1=!RC1! RC2=!RC2! RC3=!RC3!^) >> "%LOG%"
echo ============================================================ >> "%LOG%"

echo.
if !FINAL_RC! NEQ 0 (
    echo [%STAMP2%] Pipeline P1 -- Fin ERROR (RC0=!RC0! RC1=!RC1! RC2=!RC2! RC3=!RC3!^)
) else (
    echo [%STAMP2%] Pipeline P1 completado
)
echo Log: %LOG%
echo.

:: endlocal discards delayed expansion before !VAR! on the next line could
:: expand it (verified empirically) -- chain on one line so %FINAL_RC%
:: substitutes at parse time, while the scope is still active.
endlocal & exit /b %FINAL_RC%
