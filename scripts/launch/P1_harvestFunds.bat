@echo off
setlocal enabledelayedexpansion

:: Forzar UTF-8 en cmd
chcp 65001 > nul

:: ============================================================
:: P1_harvestFunds.bat -- Proceso de harvest completo (descubrimiento + ciclo de vida KIID)
::
:: Industrializa el flujo de 5 fases de proyecto1\harvest\ (ver AGENTS.md):
::   1. p1_db_harvest.py --harvest        catalogo.xml Deutsche Bank -> db_document_catalogue
::   2. p1_db_harvest.py --report-codsus  informe de calidad: fondos nuevos / candidatos a baja
::   3. harvest_gate.py                   PUERTA: el catalogo no puede haberse encogido (ver abajo)
::   4. p1_kiid_sync.py --dry-run         delta: KIID por descargar / PDFs huerfanos
::   5. p1_kiid_sync.py --sync            descarga de KIID nuevos          (solo con --sync)
::   6. p1_kiid_sync.py --retire-orphans  archiva PDFs huerfanos           (solo con --retire-orphans)
::
:: Por defecto (sin flags) NO descarga ni archiva nada: ejecuta 1-4 y deja el delta en el log para
:: revisarlo (AGENTS.md exige revisar el informe antes de sincronizar).
::
:: PUERTA (paso 3): los scripts de harvest salen con RC 0 aunque el catalogo cargado sea erroneo, y
:: --retire-orphans trata como baja cualquier KIID local ausente del ULTIMO harvest: un catalogo
:: truncado retiraria todo el universo. La puerta compara el ultimo harvest con el anterior y aborta
:: (RC 5) si filas o ISIN caen mas de --max-drop-pct (defecto 10) o hay menos de 1000 filas. Nunca se
:: descarga ni archiva tras una puerta fallida.
::
:: Uso:
::   P1_harvestFunds.bat                          harvest + informe + puerta + delta (solo lectura de PDFs)
::   P1_harvestFunds.bat --sync                   ... + descargar KIID nuevos
::   P1_harvestFunds.bat --sync --limit 20        ... humo: maximo 20 descargas
::   P1_harvestFunds.bat --retire-orphans         ... + archivar huerfanos
::   P1_harvestFunds.bat --full                   = --sync --retire-orphans (ciclo desatendido)
::   P1_harvestFunds.bat --skip-harvest --sync    reutiliza el ultimo harvest (reanudar tras fallo)
::   P1_harvestFunds.bat --max-drop-pct 5         puerta mas estricta
::
:: Tras un --sync con descargas, ejecutar P1_discoverAllFunds.bat (o P1_P2_Complete.bat) para que el
:: pase nature-first clasifique los fondos nuevos.
:: Log: proyecto1\log\log_harvest_<STAMP>.log
:: Codigos de salida: 4 = argumentos invalidos, 5 = puerta fallida, 6 = puerta no evaluable;
::   cualquier otro = RC del paso que fallo. Aborta en el primer paso con RC != 0.
:: ============================================================

set PYTHON=C:\data\envs\des\python.exe
set ROOT=C:\desarrollo\fondos
set LAUNCH=%ROOT%\scripts\launch
set HARVEST=%ROOT%\proyecto1\harvest
set LOG_DIR=%ROOT%\proyecto1\log

set RUN_SYNC=0
set RUN_RETIRE=0
set SKIP_HARVEST=0
set SYNC_LIMIT=
set MAX_DROP=10

:parse
if "%~1"=="" goto :parsed
if /i "%~1"=="--sync" (
    set RUN_SYNC=1
    shift
    goto :parse
)
if /i "%~1"=="--retire-orphans" (
    set RUN_RETIRE=1
    shift
    goto :parse
)
if /i "%~1"=="--full" (
    set RUN_SYNC=1
    set RUN_RETIRE=1
    shift
    goto :parse
)
if /i "%~1"=="--skip-harvest" (
    set SKIP_HARVEST=1
    shift
    goto :parse
)
if /i "%~1"=="--limit" (
    if "%~2"=="" goto :bad_args
    set SYNC_LIMIT=--limit %~2
    shift
    shift
    goto :parse
)
if /i "%~1"=="--max-drop-pct" (
    if "%~2"=="" goto :bad_args
    set MAX_DROP=%~2
    shift
    shift
    goto :parse
)
goto :bad_args
:parsed

for /f %%a in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set STAMP=%%a
set LOG=%LOG_DIR%\log_harvest_%STAMP%.log
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo ============================================================ >> "%LOG%"
echo  P1 Harvest -- Inicio: %STAMP%                                >> "%LOG%"
echo  sync=%RUN_SYNC% retire=%RUN_RETIRE% skip-harvest=%SKIP_HARVEST% max-drop-pct=%MAX_DROP% %SYNC_LIMIT% >> "%LOG%"
echo ============================================================ >> "%LOG%"

echo.
echo [%STAMP%] P1 Harvest iniciado (sync=%RUN_SYNC% retire=%RUN_RETIRE% skip-harvest=%SKIP_HARVEST%^)
echo   Log: %LOG%
echo.

:: Prevenir suspension durante descargas largas, salvo que lo gestione un orquestador.
if not defined FONDOS_ORCH powercfg -change -standby-timeout-ac 0 > nul 2>&1

set FINAL_RC=0
set RC1=SALTADO
set RC2=SALTADO
set RC3=SALTADO
set RC4=SALTADO
set RC5=SALTADO
set RC6=SALTADO

pushd "%ROOT%"

:: -- PASO 1: harvest ----------------------------------------------------------
if "!SKIP_HARVEST!"=="1" (
    echo --- PASO 1/6: harvest SALTADO (--skip-harvest^) ------------- >> "%LOG%"
) else (
    echo [%time%] PASO 1/6: harvest catalogo.xml
    echo. >> "%LOG%"
    echo --- PASO 1/6: p1_db_harvest --harvest ---------------------- >> "%LOG%"
    "%PYTHON%" -u -X utf8 "%HARVEST%\p1_db_harvest.py" --harvest >> "%LOG%" 2>&1
    set RC1=!ERRORLEVEL!
    if !RC1! NEQ 0 (
        set FINAL_RC=!RC1!
        goto :fin
    )
)

:: -- PASO 2: informe de calidad codSus ----------------------------------------
echo [%time%] PASO 2/6: informe codSus (fondos nuevos / candidatos a baja)
echo. >> "%LOG%"
echo --- PASO 2/6: p1_db_harvest --report-codsus ---------------- >> "%LOG%"
"%PYTHON%" -u -X utf8 "%HARVEST%\p1_db_harvest.py" --report-codsus >> "%LOG%" 2>&1
set RC2=!ERRORLEVEL!
if !RC2! NEQ 0 (
    set FINAL_RC=!RC2!
    goto :fin
)

:: -- PASO 3: puerta de calidad ------------------------------------------------
echo [%time%] PASO 3/6: puerta de calidad (caida maxima !MAX_DROP!%%^)
echo. >> "%LOG%"
echo --- PASO 3/6: harvest_gate --------------------------------- >> "%LOG%"
"%PYTHON%" -u -X utf8 "%LAUNCH%\harvest_gate.py" --max-drop-pct !MAX_DROP! >> "%LOG%" 2>&1
set RC3=!ERRORLEVEL!
if !RC3! NEQ 0 (
    set FINAL_RC=!RC3!
    echo [ERROR] Puerta de calidad fallida (RC=!RC3!^). No se descarga ni archiva nada. Ver %LOG%
    goto :fin
)

:: -- PASO 4: delta (siempre, solo lectura) ------------------------------------
echo [%time%] PASO 4/6: delta KIID (dry-run^)
echo. >> "%LOG%"
echo --- PASO 4/6: p1_kiid_sync --dry-run ----------------------- >> "%LOG%"
"%PYTHON%" -u -X utf8 "%HARVEST%\p1_kiid_sync.py" --dry-run >> "%LOG%" 2>&1
set RC4=!ERRORLEVEL!
if !RC4! NEQ 0 (
    set FINAL_RC=!RC4!
    goto :fin
)

:: -- PASO 5: descarga de KIID nuevos ------------------------------------------
if "!RUN_SYNC!"=="1" (
    echo [%time%] PASO 5/6: descarga de KIID nuevos
    echo. >> "%LOG%"
    echo --- PASO 5/6: p1_kiid_sync --sync -------------------------- >> "%LOG%"
    "%PYTHON%" -u -X utf8 "%HARVEST%\p1_kiid_sync.py" --sync !SYNC_LIMIT! >> "%LOG%" 2>&1
    set RC5=!ERRORLEVEL!
    if !RC5! NEQ 0 (
        set FINAL_RC=!RC5!
        goto :fin
    )
) else (
    echo --- PASO 5/6: omitido (sin --sync^) ------------------------ >> "%LOG%"
)

:: -- PASO 6: archivo de huerfanos ---------------------------------------------
if "!RUN_RETIRE!"=="1" (
    echo [%time%] PASO 6/6: archivar KIID huerfanos
    echo. >> "%LOG%"
    echo --- PASO 6/6: p1_kiid_sync --retire-orphans ---------------- >> "%LOG%"
    "%PYTHON%" -u -X utf8 "%HARVEST%\p1_kiid_sync.py" --retire-orphans >> "%LOG%" 2>&1
    set RC6=!ERRORLEVEL!
    if !RC6! NEQ 0 set FINAL_RC=!RC6!
) else (
    echo --- PASO 6/6: omitido (sin --retire-orphans^) -------------- >> "%LOG%"
)

:fin
popd
if not defined FONDOS_ORCH powercfg -change -standby-timeout-ac 30 > nul 2>&1

for /f %%a in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set STAMP2=%%a
echo. >> "%LOG%"
echo ============================================================ >> "%LOG%"
echo  P1 Harvest -- Fin: %STAMP2% (RC1=!RC1! RC2=!RC2! RC3=!RC3! RC4=!RC4! RC5=!RC5! RC6=!RC6!^) >> "%LOG%"
echo ============================================================ >> "%LOG%"

echo.
if !FINAL_RC! NEQ 0 (
    echo [%STAMP2%] P1 Harvest -- Fin ERROR (RC1=!RC1! RC2=!RC2! RC3=!RC3! RC4=!RC4! RC5=!RC5! RC6=!RC6!^)
    echo   Ultimas lineas del log:
    powershell -NoProfile -Command "Get-Content -LiteralPath '%LOG%' -Tail 15"
) else (
    echo [%STAMP2%] P1 Harvest completado
    if "!RUN_SYNC!"=="0" echo   Revisar el delta en el log; relanzar con --sync [--retire-orphans] para aplicarlo.
)
echo   Log: %LOG%
echo.

endlocal & exit /b %FINAL_RC%

:bad_args
echo [ERROR] Argumento no valido: %~1
echo Uso: P1_harvestFunds.bat [--sync] [--retire-orphans] [--full] [--skip-harvest] [--limit N] [--max-drop-pct N]
endlocal & exit /b 4
