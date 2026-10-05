@echo off
setlocal EnableExtensions EnableDelayedExpansion
call "%~dp0lib\common.bat" :init || (endlocal & exit /b 101)
call "%COMMON%" :utf8_on

:: ============================================================
:: P1_refreshBenchmarks.bat  -- Recarga periodica de benchmarks Morningstar
::
:: Ejecutar manualmente con periodicidad ~mensual para mantener
:: la senal independiente de SC-H actualizada.
::
:: Modos:
::   update  (default) -- solo ISINs sin fila MORNINGSTAR (rapido, ~1 min)
::   load              -- fuerza recarga de todos los ISINs con MS data
::                        (lento, ~30-60 min; recomendado cada 1-3 meses)
::
:: Uso:
::   P1_refreshBenchmarks.bat                    -> --mode update (nuevos)
::   P1_refreshBenchmarks.bat load               -> --mode load   (todos)
::   P1_refreshBenchmarks.bat update --sample 40 -> cualquier argumento extra se reenvia
::                                                  tal cual a benchmark_loader (p. ej. muestra)
:: RC 3 = el loader aborto por exceso de fallos de Morningstar (mas de una decima parte o 10
:: seguidos); P1_P2_Complete.bat detiene el ciclo en ese caso.
:: ============================================================

set LOG_DIR=%ROOT%\proyecto1\log

set MODE=update
set EXTRA_ARGS=
if /i "%~1"=="load"   (set MODE=load& shift)
if /i "%~1"=="update" (set MODE=update& shift)

:collect
if "%~1"=="" goto :collected
set EXTRA_ARGS=!EXTRA_ARGS! %1
shift
goto :collect
:collected

:: Timestamp YYYYMMDD_HHMMSS (wmic removed on newer Windows builds; PowerShell
:: is the portable replacement)
call "%COMMON%" :get_time STAMP yyyyMMdd_HHmmss
set LOG=%LOG_DIR%\log_benchmarks_%STAMP%.log

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo ============================================================ >> "%LOG%"
echo  P1_refreshBenchmarks - Inicio: %STAMP%                     >> "%LOG%"
echo  Modo: %MODE%  Extra:%EXTRA_ARGS%                            >> "%LOG%"
echo  Backend: resuelto por shared/db.py (FONDOS_DB_BACKEND / .env)     >> "%LOG%"
echo ============================================================ >> "%LOG%"

echo.
echo [%STAMP%] Benchmark refresh iniciado (mode=%MODE%%EXTRA_ARGS%^)
echo Log: %LOG%
echo.

pushd "%ROOT%"
"%PYTHON%" -u -X utf8 -m proyecto1.src.loaders.benchmark_loader --mode %MODE% !EXTRA_ARGS! >> "%LOG%" 2>&1
set RC=!ERRORLEVEL!
popd

call "%COMMON%" :get_time STAMP2 yyyyMMdd_HHmmss

echo. >> "%LOG%"
echo ============================================================ >> "%LOG%"
echo  P1_refreshBenchmarks - Fin: %STAMP2% (RC=!RC!^)             >> "%LOG%"
echo ============================================================ >> "%LOG%"

echo.
if !RC! NEQ 0 (
    echo [%STAMP2%] Benchmark refresh FALLO (mode=%MODE%, RC=!RC!^) -- revisar %LOG%
) else (
    echo [%STAMP2%] Benchmark refresh completado (mode=%MODE%^)
)
echo Log: %LOG%
echo.

:: endlocal discards delayed expansion before !VAR! on the next line could
:: expand it (verified empirically) -- chain on one line so %RC%
:: substitutes at parse time, while the scope is still active.
call "%COMMON%" :utf8_off
endlocal & exit /b %RC%
