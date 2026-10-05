@echo off
setlocal EnableExtensions EnableDelayedExpansion
call "%~dp0lib\common.bat" :init || (endlocal & exit /b 101)
call "%COMMON%" :utf8_on

:: ============================================================
:: P2_discoverLoadMetrics.bat
:: Paso 1 -- Macro (INE, BCE, FRED, Eurostat) -- una invocacion por fuente: el fallo de una
::           fuente no impide cargar las demas, y cada una queda con su RC en el log
:: Paso 2 -- NAV discover (Morningstar securityID resolution)
:: Paso 3 -- NAV load    (chartservice, delta desde 2000-01-01)
::
:: INE (IPC Espana, base del deflactor ipc_yoy_es) esta registrada en macro_discovery y en
:: PROVENANCE_DATOS_MACRO.md (mensual, ~dia 13) pero este launcher no la cargaba: se incluye.
::
:: Uso:
::   P2_discoverLoadMetrics.bat                 ciclo completo
::   P2_discoverLoadMetrics.bat --workers N     N hilos en el NAV load (defecto 1; subir con
::                                              cuidado: limita Morningstar)
::   P2_discoverLoadMetrics.bat --skip-macro    solo NAV (discover + load)
:: ============================================================

set LOG_DIR=%ROOT%\proyecto2\log

set WORKERS_ARG=
set SKIP_MACRO=0

:parse
if "%~1"=="" goto :parsed
if /i "%~1"=="--workers" (
    if "%~2"=="" goto :bad_args
    set WORKERS_ARG=--workers %~2
    shift
    shift
    goto :parse
)
if /i "%~1"=="--skip-macro" (
    set SKIP_MACRO=1
    shift
    goto :parse
)
if /i "%~1"=="-h"     goto :help
if /i "%~1"=="--help" goto :help
goto :bad_args
:parsed

:: Timestamp YYYYMMDD_HHMMSS (wmic removed on newer Windows builds; PowerShell
:: is the portable replacement)
call "%COMMON%" :get_time STAMP yyyyMMdd_HHmmss
set LOG=%LOG_DIR%\log_P2_discoverMetrics_%STAMP%.log
set ERR=%LOG_DIR%\log_P2_discoverMetrics_%STAMP%_err.log

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo ============================================================ >> "%LOG%"
echo  P2 Discover Metrics -- Inicio: %STAMP%                      >> "%LOG%"
echo  ROOT:   %ROOT%                                              >> "%LOG%"
echo  PYTHON: %PYTHON%                                            >> "%LOG%"
echo  skip-macro=%SKIP_MACRO% %WORKERS_ARG%                       >> "%LOG%"
echo ============================================================ >> "%LOG%"

echo.
echo [%STAMP%] P2 Discover Metrics iniciado
echo   Log stdout : %LOG%
echo   Log stderr : %ERR%
echo.

pushd "%ROOT%"

:: -- PASO 1: MACRO -------------------------------------------------------------
set RC_INE=0
set RC_BCE=0
set RC_FRED=0
set RC_EUROSTAT=0
if "!SKIP_MACRO!"=="1" (
    echo --- PASO 1: MACRO omitido (--skip-macro^) ----------------- >> "%LOG%"
) else (
    echo [%time%] Paso 1/3: Macro (INE, BCE, FRED, Eurostat^)
    for %%S in (ine bce fred eurostat) do (
        echo. >> "%LOG%"
        echo --- PASO 1: MACRO (%%S^) ------------------------------- >> "%LOG%"
        "%PYTHON%" -u -X utf8 -m proyecto2.src.discovery.macro_discovery --source %%S >> "%LOG%" 2>> "%ERR%"
        set RC_%%S=!ERRORLEVEL!
    )
)
:: (cmd no distingue mayusculas en nombres de variable: RC_ine == RC_INE)

:: -- PASO 2: NAV DISCOVER ------------------------------------------------------
echo [%time%] Paso 2/3: NAV Discover (resolucion de securityID^)
echo. >> "%LOG%"
echo --- PASO 2: NAV DISCOVER --------------------------------- >> "%LOG%"

"%PYTHON%" -u -X utf8 -m proyecto2.src.discovery.nav_discovery --mode discover --skip-if-recent >> "%LOG%" 2>> "%ERR%"
set RC_DISCOVER=!ERRORLEVEL!

:: -- PASO 3: NAV LOAD ----------------------------------------------------------
echo [%time%] Paso 3/3: NAV Load (chartservice, desde 2000-01-01^)
echo. >> "%LOG%"
echo --- PASO 3: NAV LOAD (desde 2000-01-01) ------------------ >> "%LOG%"

"%PYTHON%" -u -X utf8 -m proyecto2.src.discovery.nav_discovery --mode load --desde 2000-01-01 %WORKERS_ARG% >> "%LOG%" 2>> "%ERR%"
set RC_LOAD=!ERRORLEVEL!

popd

:: FINAL_RC: primer paso con RC != 0 gana (todos los pasos se ejecutan
:: siempre, sin abortar entre ellos -- este bloque solo hace que el codigo
:: de salida del script refleje honestamente si algo fallo).
set FINAL_RC=0
for %%V in (RC_INE RC_BCE RC_FRED RC_EUROSTAT RC_DISCOVER RC_LOAD) do (
    if !FINAL_RC! EQU 0 if !%%V! NEQ 0 set FINAL_RC=!%%V!
)

set SUMMARY=INE=!RC_INE! BCE=!RC_BCE! FRED=!RC_FRED! EUROSTAT=!RC_EUROSTAT! DISCOVER=!RC_DISCOVER! LOAD=!RC_LOAD!

:: -- Pie del log ---------------------------------------------------------------
call "%COMMON%" :get_time STAMP2 yyyyMMdd_HHmmss

echo. >> "%LOG%"
echo ============================================================ >> "%LOG%"
echo  P2 Discover Metrics -- Fin: %STAMP2% (!SUMMARY!^) >> "%LOG%"
echo ============================================================ >> "%LOG%"

echo.
if !FINAL_RC! NEQ 0 (
    echo [%STAMP2%] P2 Discover Metrics -- Fin ERROR (!SUMMARY!^)
) else (
    echo [%STAMP2%] P2 Discover Metrics completado
)
echo   Log stdout : %LOG%
echo   Log stderr : %ERR%
echo.

:: endlocal discards delayed expansion before !VAR! on the next line could
:: expand it (verified empirically) -- chain on one line so %FINAL_RC%
:: substitutes at parse time, while the scope is still active.
call "%COMMON%" :utf8_off
endlocal & exit /b %FINAL_RC%
:bad_args
echo [ERROR] Argumento no valido: %~1
echo Uso: P2_discoverLoadMetrics.bat [--workers N] [--skip-macro] [-h]
call "%COMMON%" :utf8_off
endlocal & exit /b %RC_USAGE%

:help
echo Uso: P2_discoverLoadMetrics.bat [--workers N] [--skip-macro] [-h]
echo   --workers N   N hilos en el NAV load
echo   --skip-macro  solo NAV, sin carga macro
call "%COMMON%" :utf8_off
endlocal & exit /b 0
