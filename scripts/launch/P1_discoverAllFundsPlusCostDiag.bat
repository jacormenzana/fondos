@echo off
setlocal EnableExtensions EnableDelayedExpansion
call "%~dp0lib\common.bat" :init "%~nx0" & set "RC_BOOT=!ERRORLEVEL!"
if %RC_BOOT% NEQ 0 (endlocal & exit /b %RC_BOOT%)
call "%COMMON%" :utf8_on

:: ============================================================
:: P1_discoverAllFundsPlusCostDiag.bat -- Pipeline P1 por bloques (legacy) + Cost Diag
:: Uso: P1_discoverAllFundsPlusCostDiag.bat   (sin argumentos)
:: Ejecutar desde: C:\desarrollo\fondos\scripts\launch\
:: Log generado en: C:\desarrollo\fondos\proyecto1\log\
:: ============================================================

:: -h / --help: the usage (this header) and out, BEFORE anything runs (NORMAS_BATCH.md section 3). This launcher forwards its arguments to
:: a real run, so an argument it did not recognise used to start one (2026-10-08: `--help` over the real launchers).
for %%A in (%*) do (
    if /i "%%~A"=="-h" goto :show_help
    if /i "%%~A"=="--help" goto :show_help
)

set MASTER=c:\data\fondos\in\GestoresDeFondosv1.xlsx
set LOG_DIR=%ROOT%\proyecto1\log

:: --- LINEAS A ANADIR ---
set KIID_DIR=c:\data\fondos\kiid
set LOG_DIAG_OUT_DIR=%ROOT%\out\diag
set PYTHONPATH=%ROOT%\proyecto1;%ROOT%\proyecto1\core;%ROOT%\shared
:: -----------------------

:: Timestamp YYYYMMDD_HHMMSS (wmic removed on newer Windows builds; PowerShell
:: is the portable replacement)
call "%COMMON%" :get_time STAMP yyyyMMdd_HHmmss
set LOG=%LOG_DIR%\log_pipeline_%STAMP%.log

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo ============================================================ >> "%LOG%"
echo  Pipeline P1 - Inicio: %STAMP%                              >> "%LOG%"
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
"%PYTHON%" -X utf8 "%ROOT%\scripts\launch\mark_stale.py" --max-age 180 --max-funds 50 >> "%LOG%" 2>&1
set RC0=!ERRORLEVEL!

:: -- Bloques de clasificacion -------------------------------------------------
echo [%time%] Bloque: monetarios
echo. >> "%LOG%"
echo --- BLOQUE: monetarios ------------------------------------ >> "%LOG%"
pushd %ROOT%\proyecto1
"%PYTHON%" -X utf8 run_block.py --block monetarios     --master "%MASTER%" >> "%LOG%" 2>&1
set RC1=!ERRORLEVEL!

echo [%time%] Bloque: rf_corto
echo. >> "%LOG%"
echo --- BLOQUE: rf_corto -------------------------------------- >> "%LOG%"
"%PYTHON%" -X utf8 run_block.py --block rf_corto       --master "%MASTER%" >> "%LOG%" 2>&1
set RC2=!ERRORLEVEL!

echo [%time%] Bloque: rf_flexible
echo. >> "%LOG%"
echo --- BLOQUE: rf_flexible ----------------------------------- >> "%LOG%"
"%PYTHON%" -X utf8 run_block.py --block rf_flexible    --master "%MASTER%" >> "%LOG%" 2>&1
set RC3=!ERRORLEVEL!

echo [%time%] Bloque: renta_variable
echo. >> "%LOG%"
echo --- BLOQUE: renta_variable -------------------------------- >> "%LOG%"
"%PYTHON%" -X utf8 run_block.py --block renta_variable --master "%MASTER%" >> "%LOG%" 2>&1
set RC4=!ERRORLEVEL!

echo [%time%] Bloque: mixtos
echo. >> "%LOG%"
echo --- BLOQUE: mixtos ---------------------------------------- >> "%LOG%"
"%PYTHON%" -X utf8 run_block.py --block mixtos         --master "%MASTER%" >> "%LOG%" 2>&1
set RC5=!ERRORLEVEL!

echo [%time%] Bloque: alternativos
echo. >> "%LOG%"
echo --- BLOQUE: alternativos ---------------------------------- >> "%LOG%"
"%PYTHON%" -X utf8 run_block.py --block alternativos   --master "%MASTER%" >> "%LOG%" 2>&1
set RC6=!ERRORLEVEL!

echo [%time%] Bloque: restantes
echo. >> "%LOG%"
echo --- BLOQUE: restantes ------------------------------------- >> "%LOG%"
"%PYTHON%" -X utf8 run_block.py --block restantes      --master "%MASTER%" >> "%LOG%" 2>&1
set RC7=!ERRORLEVEL!
popd

:: -- fund_family_builder ------------------------------------------------------
echo [%time%] fund_family_builder
echo. >> "%LOG%"
echo --- fund_family_builder ----------------------------------- >> "%LOG%"
pushd %ROOT%
"%PYTHON%" -X utf8 -m proyecto1.core.fund_family_builder >> "%LOG%" 2>&1
set RC8=!ERRORLEVEL!
popd

:: -- Pie del log --------------------------------------------------------------
call "%COMMON%" :get_time STAMP2 yyyyMMdd_HHmmss
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
"%PYTHON%" -X utf8 "%ROOT%\scripts\diag\diag_cost_extraction.py" ^
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
call "%COMMON%" :utf8_off
endlocal & exit /b %FINAL_RC%


:: ------------------------------------------------------------
:: :show_help -- usage = the header of this file (lib\batch_helpers.py usage); RC 0, nothing else runs.
:: ------------------------------------------------------------
:show_help
"%PYTHON%" "%LIB%\batch_helpers.py" usage "%~f0"
call "%COMMON%" :utf8_off
endlocal & exit /b 0
