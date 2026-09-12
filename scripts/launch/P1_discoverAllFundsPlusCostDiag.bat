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
set MASTER=c:\data\fondos\in\GestoresDeFondosv1.xlsx
set LOG_DIR=%ROOT%\proyecto1\log

:: Resolve bare 'python' calls below to the 'des' Conda env (bare python on
:: PATH otherwise hits the WindowsApps shim -> "Permission denied").
set PATH=C:\data\envs\des;C:\data\envs\des\Scripts;%PATH%

:: --- LÍNEAS A AÑADIR ---
set KIID_DIR=c:\data\fondos\kiid
set LOG_DIAG_OUT_DIR=%ROOT%\out\diag
set PYTHONPATH=%ROOT%\proyecto1;%ROOT%\proyecto1\core;%ROOT%\shared
:: -----------------------

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

:: -- Bloques de clasificacion -------------------------------------------------
echo [%time%] Bloque: monetarios
echo. >> "%LOG%"
echo --- BLOQUE: monetarios ------------------------------------ >> "%LOG%"
pushd %ROOT%\proyecto1
python -X utf8 run_block.py --block monetarios     --db "%DB%" --master "%MASTER%" >> "%LOG%" 2>&1
set RC1=!ERRORLEVEL!

echo [%time%] Bloque: rf_corto
echo. >> "%LOG%"
echo --- BLOQUE: rf_corto -------------------------------------- >> "%LOG%"
python -X utf8 run_block.py --block rf_corto       --db "%DB%" --master "%MASTER%" >> "%LOG%" 2>&1
set RC2=!ERRORLEVEL!

echo [%time%] Bloque: rf_flexible
echo. >> "%LOG%"
echo --- BLOQUE: rf_flexible ----------------------------------- >> "%LOG%"
python -X utf8 run_block.py --block rf_flexible    --db "%DB%" --master "%MASTER%" >> "%LOG%" 2>&1
set RC3=!ERRORLEVEL!

echo [%time%] Bloque: renta_variable
echo. >> "%LOG%"
echo --- BLOQUE: renta_variable -------------------------------- >> "%LOG%"
python -X utf8 run_block.py --block renta_variable --db "%DB%" --master "%MASTER%" >> "%LOG%" 2>&1
set RC4=!ERRORLEVEL!

echo [%time%] Bloque: mixtos
echo. >> "%LOG%"
echo --- BLOQUE: mixtos ---------------------------------------- >> "%LOG%"
python -X utf8 run_block.py --block mixtos         --db "%DB%" --master "%MASTER%" >> "%LOG%" 2>&1
set RC5=!ERRORLEVEL!

echo [%time%] Bloque: alternativos
echo. >> "%LOG%"
echo --- BLOQUE: alternativos ---------------------------------- >> "%LOG%"
python -X utf8 run_block.py --block alternativos   --db "%DB%" --master "%MASTER%" >> "%LOG%" 2>&1
set RC6=!ERRORLEVEL!

echo [%time%] Bloque: restantes
echo. >> "%LOG%"
echo --- BLOQUE: restantes ------------------------------------- >> "%LOG%"
python -X utf8 run_block.py --block restantes      --db "%DB%" --master "%MASTER%" >> "%LOG%" 2>&1
set RC7=!ERRORLEVEL!
popd

:: -- fund_family_builder ------------------------------------------------------
echo [%time%] fund_family_builder
echo. >> "%LOG%"
echo --- fund_family_builder ----------------------------------- >> "%LOG%"
pushd %ROOT%
python -X utf8 -m proyecto1.core.fund_family_builder >> "%LOG%" 2>&1
set RC8=!ERRORLEVEL!
popd

:: -- Pie del log --------------------------------------------------------------
for /f %%a in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set STAMP2=%%a
echo. >> "%LOG%"
echo ============================================================ >> "%LOG%"
echo  Pipeline P1 - Fin: %STAMP2% (RC0=!RC0! RC1=!RC1! RC2=!RC2! RC3=!RC3! RC4=!RC4! RC5=!RC5! RC6=!RC6! RC7=!RC7! RC8=!RC8!^) >> "%LOG%"
echo ============================================================ >> "%LOG%"

echo.
echo [%STAMP2%] Pipeline P1 completado
echo Log: %LOG%
echo.

:: ============================================================
:: Cost Diag
:: FIX: Corregidas redirecciones, variable %LOG_DIAG_OUT% y popd
:: ============================================================


set LOG_DIAG_OUT=%LOG_DIAG_OUT_DIR%\cost_diag_%STAMP2%_p1g.csv
set LOG_DIAG_SUMMARY=%LOG_DIAG_OUT_DIR%\cost_diag_summary_%STAMP2%.log

echo ============================================================ >> "%LOG%"
echo  Cost Diag - Inicio: %STAMP2%                                >> "%LOG%"
echo  PYTHONPATH: %PYTHONPATH%                                    >> "%LOG%"
echo  OUT: %LOG_DIAG_OUT%                                         >> "%LOG%"
echo  SUMMARY: %LOG_DIAG_SUMMARY%                                 >> "%LOG%"
echo ============================================================ >> "%LOG%"

pushd %ROOT%
python -X utf8 "%ROOT%\scripts\diag\diag_cost_extraction.py" ^
    --db "%DB%" ^
    --kiid-dir "%KIID_DIR%" ^
    --only-priips ^
    --out "%LOG_DIAG_OUT%" 
set DIAG_RC=!ERRORLEVEL!
popd

echo. >> "%LOG%"
if !DIAG_RC! NEQ 0 (
    echo [ERROR] Cost Diag fallo con codigo !DIAG_RC! - revisar PYTHONPATH/imports >> "%LOG%"
    echo [ERROR] Cost Diag fallo con codigo !DIAG_RC!
) else (
    echo [OK] Cost Diag completado. CSV: %LOG_DIAG_OUT% >> "%LOG%"
    echo [OK] Cost Diag completado. CSV: %LOG_DIAG_OUT%
    echo [OK] Cost Diag resumen: %LOG_DIAG_SUMMARY% >> "%LOG%"
    echo [OK] Cost Diag resumen: %LOG_DIAG_SUMMARY%
)

echo ============================================================ >> "%LOG%"
echo  Cost Diag - Fin: %STAMP2% (RC=!DIAG_RC!)                    >> "%LOG%"
echo ============================================================ >> "%LOG%"

:: FINAL_RC: primer paso con RC != 0 gana (todos los pasos se ejecutan
:: siempre, sin abortar entre ellos -- este bloque solo hace que el codigo
:: de salida del script refleje honestamente si algo fallo).
set FINAL_RC=0
if !RC0! NEQ 0 set FINAL_RC=!RC0!
if !FINAL_RC! EQU 0 if !RC1! NEQ 0 set FINAL_RC=!RC1!
if !FINAL_RC! EQU 0 if !RC2! NEQ 0 set FINAL_RC=!RC2!
if !FINAL_RC! EQU 0 if !RC3! NEQ 0 set FINAL_RC=!RC3!
if !FINAL_RC! EQU 0 if !RC4! NEQ 0 set FINAL_RC=!RC4!
if !FINAL_RC! EQU 0 if !RC5! NEQ 0 set FINAL_RC=!RC5!
if !FINAL_RC! EQU 0 if !RC6! NEQ 0 set FINAL_RC=!RC6!
if !FINAL_RC! EQU 0 if !RC7! NEQ 0 set FINAL_RC=!RC7!
if !FINAL_RC! EQU 0 if !RC8! NEQ 0 set FINAL_RC=!RC8!
if !FINAL_RC! EQU 0 if !DIAG_RC! NEQ 0 set FINAL_RC=!DIAG_RC!

:: endlocal discards delayed expansion before !VAR! on the next line could
:: expand it (verified empirically) -- chain on one line so %FINAL_RC%
:: substitutes at parse time, while the scope is still active.
endlocal & exit /b %FINAL_RC%
