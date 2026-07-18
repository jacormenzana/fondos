@echo off
setlocal enabledelayedexpansion

:: Forzar UTF-8 en cmd para evitar UnicodeEncodeError con caracteres no-ASCII
:: en el fichero de log generado por >> redireccion
chcp 65001 > nul

:: ============================================================
:: discoverAllFunds.bat  -- Pipeline P1 completo
:: Ejecutar desde: C:\desarrollo\fondos\scripts\launch\
:: Log generado en: C:\desarrollo\fondos\proyecto1\log\
:: ============================================================

set ROOT=C:\desarrollo\fondos
set DB=%ROOT%\db\fondos.sqlite
set LOG_DIR=%ROOT%\proyecto1\log

:: Timestamp YYYYMMDD_HHMMSS
for /f "tokens=2 delims==" %%a in ('wmic OS Get localdatetime /value') do set DT=%%a
set STAMP=%DT:~0,8%_%DT:~8,6%
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
echo [%time%] Paso 0: mark_stale (max 50 fondos, antiguedad > 180 dias)
echo. >> "%LOG%"
echo --- PASO 0: mark_stale ------------------------------------ >> "%LOG%"
python -X utf8 "%ROOT%\scripts\launch\mark_stale.py" --db "%DB%" --max-age 180 --max-funds 50 >> "%LOG%" 2>&1

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
:: (srri_nav NULL) are classified ex-ante only (name/KIID/benchmark) — no error.
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
popd

:: -- fund_family_builder ------------------------------------------------------
echo [%time%] fund_family_builder
echo. >> "%LOG%"
echo --- fund_family_builder ----------------------------------- >> "%LOG%"
pushd %ROOT%
python -X utf8 -m proyecto1.core.fund_family_builder >> "%LOG%" 2>&1
popd

:: -- Pie del log --------------------------------------------------------------
for /f "tokens=2 delims==" %%a in ('wmic OS Get localdatetime /value') do set DT2=%%a
set STAMP2=%DT2:~0,8%_%DT2:~8,6%
echo. >> "%LOG%"
echo ============================================================ >> "%LOG%"
echo  Pipeline P1 - Fin: %STAMP2%                                >> "%LOG%"
echo ============================================================ >> "%LOG%"

echo.
echo [%STAMP2%] Pipeline P1 completado
echo Log: %LOG%
echo.
endlocal
