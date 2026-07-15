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

:: -- Bloques de clasificacion -------------------------------------------------
echo [%time%] Bloque: monetarios
echo. >> "%LOG%"
echo --- BLOQUE: monetarios ------------------------------------ >> "%LOG%"
pushd %ROOT%\proyecto1
python -X utf8 run_block.py --block monetarios     --db "%DB%" --master "%MASTER%" >> "%LOG%" 2>&1

echo [%time%] Bloque: rf_corto
echo. >> "%LOG%"
echo --- BLOQUE: rf_corto -------------------------------------- >> "%LOG%"
python -X utf8 run_block.py --block rf_corto       --db "%DB%" --master "%MASTER%" >> "%LOG%" 2>&1

echo [%time%] Bloque: rf_flexible
echo. >> "%LOG%"
echo --- BLOQUE: rf_flexible ----------------------------------- >> "%LOG%"
python -X utf8 run_block.py --block rf_flexible    --db "%DB%" --master "%MASTER%" >> "%LOG%" 2>&1

echo [%time%] Bloque: renta_variable
echo. >> "%LOG%"
echo --- BLOQUE: renta_variable -------------------------------- >> "%LOG%"
python -X utf8 run_block.py --block renta_variable --db "%DB%" --master "%MASTER%" >> "%LOG%" 2>&1

echo [%time%] Bloque: mixtos
echo. >> "%LOG%"
echo --- BLOQUE: mixtos ---------------------------------------- >> "%LOG%"
python -X utf8 run_block.py --block mixtos         --db "%DB%" --master "%MASTER%" >> "%LOG%" 2>&1

echo [%time%] Bloque: alternativos
echo. >> "%LOG%"
echo --- BLOQUE: alternativos ---------------------------------- >> "%LOG%"
python -X utf8 run_block.py --block alternativos   --db "%DB%" --master "%MASTER%" >> "%LOG%" 2>&1

echo [%time%] Bloque: restantes
echo. >> "%LOG%"
echo --- BLOQUE: restantes ------------------------------------- >> "%LOG%"
python -X utf8 run_block.py --block restantes      --db "%DB%" --master "%MASTER%" >> "%LOG%" 2>&1
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
