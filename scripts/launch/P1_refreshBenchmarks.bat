@echo off
setlocal enabledelayedexpansion

:: Forzar UTF-8 en cmd para evitar UnicodeEncodeError con caracteres no-ASCII
chcp 65001 > nul

:: ============================================================
:: P1_refreshBenchmarks.bat  -- Recarga periódica de benchmarks Morningstar
::
:: Ejecutar manualmente con periodicidad ~mensual para mantener
:: la señal independiente de SC-H actualizada.
::
:: Modos:
::   update  (default) — solo ISINs sin fila MORNINGSTAR (rápido, ~1 min)
::   load              — fuerza recarga de todos los ISINs con MS data
::                       (lento, ~30-60 min; recomendado cada 1-3 meses)
::
:: Uso:
::   P1_refreshBenchmarks.bat           -> --mode update (nuevos)
::   P1_refreshBenchmarks.bat load      -> --mode load   (todos)
:: ============================================================

set ROOT=C:\desarrollo\fondos
set DB=%ROOT%\db\fondos.sqlite
set LOG_DIR=%ROOT%\proyecto1\log

:: Resolve bare 'python' calls below to the 'des' Conda env (bare python on
:: PATH otherwise hits the WindowsApps shim -> "Permission denied").
set PATH=C:\data\envs\des;C:\data\envs\des\Scripts;%PATH%

set MODE=update
if /i "%~1"=="load" set MODE=load

:: Timestamp YYYYMMDD_HHMMSS
for /f "tokens=2 delims==" %%a in ('wmic OS Get localdatetime /value') do set DT=%%a
set STAMP=%DT:~0,8%_%DT:~8,6%
set LOG=%LOG_DIR%\log_benchmarks_%STAMP%.log

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo ============================================================ >> "%LOG%"
echo  P1_refreshBenchmarks - Inicio: %STAMP%                     >> "%LOG%"
echo  Modo: %MODE%                                                >> "%LOG%"
echo  DB:   %DB%                                                  >> "%LOG%"
echo ============================================================ >> "%LOG%"

echo.
echo [%STAMP%] Benchmark refresh iniciado (mode=%MODE%)
echo Log: %LOG%
echo.

pushd %ROOT%
python -X utf8 -m proyecto1.src.loaders.benchmark_loader --mode %MODE% >> "%LOG%" 2>&1
popd

for /f "tokens=2 delims==" %%a in ('wmic OS Get localdatetime /value') do set DT2=%%a
set STAMP2=%DT2:~0,8%_%DT2:~8,6%

echo. >> "%LOG%"
echo ============================================================ >> "%LOG%"
echo  P1_refreshBenchmarks - Fin: %STAMP2%                       >> "%LOG%"
echo ============================================================ >> "%LOG%"

echo.
echo [%STAMP2%] Benchmark refresh completado (mode=%MODE%)
echo Log: %LOG%
echo.
endlocal
