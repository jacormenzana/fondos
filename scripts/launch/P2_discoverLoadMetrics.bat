@echo off
setlocal enabledelayedexpansion

:: Forzar UTF-8 en cmd
chcp 65001 > nul

:: ============================================================
:: P2_discoverLoadMetrics.bat
:: Paso 1 -- Macro (BCE, FRED, Eurostat)
:: Paso 2 -- NAV discover (Morningstar securityID resolution)
:: Paso 3 -- NAV load    (chartservice, delta desde 2000-01-01)
:: ============================================================

set PYTHON=C:\data\envs\des\python.exe
set ROOT=C:\desarrollo\fondos
set LOG_DIR=%ROOT%\proyecto2\log

:: Timestamp YYYYMMDD_HHMMSS (wmic removed on newer Windows builds; PowerShell
:: is the portable replacement)
for /f %%a in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set STAMP=%%a
set LOG=%LOG_DIR%\log_P2_discoverMetrics_%STAMP%.log
set ERR=%LOG_DIR%\log_P2_discoverMetrics_%STAMP%_err.log

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo ============================================================ >> "%LOG%"
echo  P2 Discover Metrics -- Inicio: %STAMP%                      >> "%LOG%"
echo  ROOT:   %ROOT%                                              >> "%LOG%"
echo  PYTHON: %PYTHON%                                            >> "%LOG%"
echo ============================================================ >> "%LOG%"

echo.
echo [%STAMP%] P2 Discover Metrics iniciado
echo   Log stdout : %LOG%
echo   Log stderr : %ERR%
echo.

pushd "%ROOT%"

:: -- PASO 1: MACRO -------------------------------------------------------------
echo [%time%] Paso 1/3: Macro (BCE, FRED, Eurostat)
echo. >> "%LOG%"
echo --- PASO 1: MACRO (BCE) ---------------------------------- >> "%LOG%"
%PYTHON% -X utf8 -m proyecto2.src.discovery.macro_discovery --source bce      >> "%LOG%" 2>> "%ERR%"
set RC1=!ERRORLEVEL!

echo. >> "%LOG%"
echo --- PASO 1: MACRO (FRED) --------------------------------- >> "%LOG%"
%PYTHON% -X utf8 -m proyecto2.src.discovery.macro_discovery --source fred     >> "%LOG%" 2>> "%ERR%"
set RC2=!ERRORLEVEL!

echo. >> "%LOG%"
echo --- PASO 1: MACRO (EUROSTAT) ----------------------------- >> "%LOG%"
%PYTHON% -X utf8 -m proyecto2.src.discovery.macro_discovery --source eurostat >> "%LOG%" 2>> "%ERR%"
set RC3=!ERRORLEVEL!

:: -- PASO 2: NAV DISCOVER ------------------------------------------------------
echo [%time%] Paso 2/3: NAV Discover (resolucion de securityID)
echo. >> "%LOG%"
echo --- PASO 2: NAV DISCOVER --------------------------------- >> "%LOG%"

%PYTHON% -X utf8 -m proyecto2.src.discovery.nav_discovery --mode discover --skip-if-recent >> "%LOG%" 2>> "%ERR%"
set RC4=!ERRORLEVEL!

:: -- PASO 3: NAV LOAD ----------------------------------------------------------
echo [%time%] Paso 3/3: NAV Load (chartservice, desde 2000-01-01)
echo. >> "%LOG%"
echo --- PASO 3: NAV LOAD (desde 2000-01-01) ------------------ >> "%LOG%"

%PYTHON% -X utf8 -m proyecto2.src.discovery.nav_discovery --mode load --desde 2000-01-01 >> "%LOG%" 2>> "%ERR%"
set RC5=!ERRORLEVEL!

popd

:: FINAL_RC: primer paso con RC != 0 gana (todos los pasos se ejecutan
:: siempre, sin abortar entre ellos -- este bloque solo hace que el codigo
:: de salida del script refleje honestamente si algo fallo).
set FINAL_RC=0
if !RC1! NEQ 0 set FINAL_RC=!RC1!
if !FINAL_RC! EQU 0 if !RC2! NEQ 0 set FINAL_RC=!RC2!
if !FINAL_RC! EQU 0 if !RC3! NEQ 0 set FINAL_RC=!RC3!
if !FINAL_RC! EQU 0 if !RC4! NEQ 0 set FINAL_RC=!RC4!
if !FINAL_RC! EQU 0 if !RC5! NEQ 0 set FINAL_RC=!RC5!

:: -- Pie del log ---------------------------------------------------------------
for /f %%a in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set STAMP2=%%a

echo. >> "%LOG%"
echo ============================================================ >> "%LOG%"
echo  P2 Discover Metrics -- Fin: %STAMP2% (RC1=!RC1! RC2=!RC2! RC3=!RC3! RC4=!RC4! RC5=!RC5!^) >> "%LOG%"
echo ============================================================ >> "%LOG%"

echo.
if !FINAL_RC! NEQ 0 (
    echo [%STAMP2%] P2 Discover Metrics -- Fin ERROR (RC1=!RC1! RC2=!RC2! RC3=!RC3! RC4=!RC4! RC5=!RC5!^)
) else (
    echo [%STAMP2%] P2 Discover Metrics completado
)
echo   Log stdout : %LOG%
echo   Log stderr : %ERR%
echo.

:: endlocal discards delayed expansion before !VAR! on the next line could
:: expand it (verified empirically) -- chain on one line so %FINAL_RC%
:: substitutes at parse time, while the scope is still active.
endlocal & exit /b %FINAL_RC%
