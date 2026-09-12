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

:: Timestamp YYYYMMDD_HHMMSS (wmic removed on newer Windows builds; PowerShell
:: is the portable replacement)
for /f %%a in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set STAMP=%%a
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
set RC=!ERRORLEVEL!
popd

for /f %%a in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set STAMP2=%%a

echo. >> "%LOG%"
echo ============================================================ >> "%LOG%"
echo  P1_refreshBenchmarks - Fin: %STAMP2%                       >> "%LOG%"
echo ============================================================ >> "%LOG%"

echo.
if !RC! NEQ 0 (
    echo [%STAMP2%] Benchmark refresh FALLO (mode=%MODE%, RC=!RC!^) -- revisar %LOG%
) else (
    echo [%STAMP2%] Benchmark refresh completado (mode=%MODE%)
)
echo Log: %LOG%
echo.

:: endlocal discards delayed expansion before !VAR! on the next line could
:: expand it (verified empirically) -- chain on one line so %RC%
:: substitutes at parse time, while the scope is still active.
endlocal & exit /b %RC%
