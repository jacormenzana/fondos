@echo off
setlocal enabledelayedexpansion

:: Forzar UTF-8 en cmd para evitar UnicodeEncodeError con caracteres no-ASCII
:: en el fichero de log generado por >> redireccion
chcp 65001 > nul

:: ============================================================
:: P2_discoverMetrics.bat -- Carga de Fuentes (Macro y NAV)
:: ============================================================

set ROOT=C:\desarrollo\fondos
set LOG_DIR=%ROOT%\proyecto2\log

:: Timestamp YYYYMMDD_HHMMSS
for /f "tokens=2 delims==" %%a in ('wmic OS Get localdatetime /value') do set DT=%%a
set STAMP=%DT:~0,8%_%DT:~8,6%
set LOG=%LOG_DIR%\log_P2_discoverMetrics_%STAMP%.log

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo ============================================================ >> "%LOG%"
echo  Pipeline P2 (Discover Metrics) - Inicio: %STAMP%            >> "%LOG%"
echo  ROOT:   %ROOT%                                              >> "%LOG%"
echo ============================================================ >> "%LOG%"

echo.
echo [%STAMP%] Pipeline P2 (Discover Metrics) iniciado
echo Log: %LOG%
echo.

pushd %ROOT%

:: -- PASO 1: CARGA DE FUENTES BCE FRED Y EUROESTAT ---------------------------
echo [%time%] Paso 1: Carga de fuentes macro (BCE, FRED, EUROSTAT)
echo. >> "%LOG%"
echo --- PASO 1: CARGA DE FUENTES MACRO ------------------------ >> "%LOG%"

python -X utf8 -m proyecto2.src.loaders.macro_loader --source bce >> "%LOG%" 2>&1
python -X utf8 -m proyecto2.src.loaders.macro_loader --source fred >> "%LOG%" 2>&1
python -X utf8 -m proyecto2.src.loaders.macro_loader --source eurostat >> "%LOG%" 2>&1

REM python -X utf8 -m proyecto2.src.loaders.macro_loader --source bce 2>&1 | powershell -noprofile -command "Tee-Object -FilePath '%LOG%' -Append"
REM python -X utf8 -m proyecto2.src.loaders.macro_loader --source fred 2>&1 | powershell -noprofile -command "Tee-Object -FilePath '%LOG%' -Append"
REM python -X utf8 -m proyecto2.src.loaders.macro_loader --source eurostat 2>&1 | powershell -noprofile -command "Tee-Object -FilePath '%LOG%' -Append"

:: -- PASO 2: CARGA FUENTES MORNINGSTAR ---------------------------------------
echo [%time%] Paso 2: Carga de fuentes Morningstar (NAV)
echo. >> "%LOG%"
echo --- PASO 2: CARGA FUENTES MORNINGSTAR --------------------- >> "%LOG%"

:: Ejecuciones de prueba (Comentadas)
:: python -X utf8 -m proyecto2.src.loaders.nav_discovery --mode discover --isin LU1873127366 --dry-run --verbose >> "%LOG%" 2>&1
:: python -X utf8 -m proyecto2.src.loaders.nav_discovery --mode discover --sample 10 >> "%LOG%" 2>&1
:: python -X utf8 -m proyecto2.src.loaders.nav_discovery --mode load --isin LU1873127366 --dry-run --verbose >> "%LOG%" 2>&1

python -X utf8 -m proyecto2.src.loaders.nav_discovery --mode discover >> "%LOG%" 2>&1
python -X utf8 -m proyecto2.src.loaders.nav_discovery --mode load --desde 2016-01-01 >> "%LOG%" 2>&1

REM python -X utf8 -m proyecto2.src.loaders.nav_discovery --mode discover  2>&1  | powershell -noprofile -command "Tee-Object -FilePath '%LOG%' -Append"
REM python -X utf8 -m proyecto2.src.loaders.nav_discovery --mode load --desde 2016-01-01  2>&1  | powershell -noprofile -command "Tee-Object -FilePath '%LOG%' -Append" 


:: Test historia (Comentado)
:: python -X utf8 -m proyecto2.src.loaders.test_historia >> "%LOG%" 2>&1

popd

:: -- Pie del log --------------------------------------------------------------
for /f "tokens=2 delims==" %%a in ('wmic OS Get localdatetime /value') do set DT2=%%a
set STAMP2=%DT2:~0,8%_%DT2:~8,6%
echo. >> "%LOG%"
echo ============================================================ >> "%LOG%"
echo  Pipeline P2 (Discover Metrics) - Fin: %STAMP2%              >> "%LOG%"
echo ============================================================ >> "%LOG%"

echo.
echo [%STAMP2%] Pipeline P2 (Discover Metrics) completado
echo Log: %LOG%
echo.
endlocal