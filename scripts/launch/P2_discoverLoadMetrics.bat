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

set PYTHON=C:\Users\Administrador\anaconda3\envs\des\python.exe
set ROOT=C:\desarrollo\fondos
set LOG_DIR=%ROOT%\proyecto2\log

:: Timestamp YYYYMMDD_HHMMSS
for /f "tokens=2 delims==" %%a in ('wmic OS Get localdatetime /value') do set DT=%%a
set STAMP=%DT:~0,8%_%DT:~8,6%
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

echo. >> "%LOG%"
echo --- PASO 1: MACRO (FRED) --------------------------------- >> "%LOG%"
%PYTHON% -X utf8 -m proyecto2.src.discovery.macro_discovery --source fred     >> "%LOG%" 2>> "%ERR%"

echo. >> "%LOG%"
echo --- PASO 1: MACRO (EUROSTAT) ----------------------------- >> "%LOG%"
%PYTHON% -X utf8 -m proyecto2.src.discovery.macro_discovery --source eurostat >> "%LOG%" 2>> "%ERR%"

:: -- PASO 2: NAV DISCOVER ------------------------------------------------------
echo [%time%] Paso 2/3: NAV Discover (resolucion de securityID)
echo. >> "%LOG%"
echo --- PASO 2: NAV DISCOVER --------------------------------- >> "%LOG%"

%PYTHON% -X utf8 -m proyecto2.src.discovery.nav_discovery --mode discover --skip-if-recent >> "%LOG%" 2>> "%ERR%"

:: -- PASO 3: NAV LOAD ----------------------------------------------------------
echo [%time%] Paso 3/3: NAV Load (chartservice, desde 2000-01-01)
echo. >> "%LOG%"
echo --- PASO 3: NAV LOAD (desde 2000-01-01) ------------------ >> "%LOG%"

%PYTHON% -X utf8 -m proyecto2.src.discovery.nav_discovery --mode load --desde 2000-01-01 >> "%LOG%" 2>> "%ERR%"

popd

:: -- Pie del log ---------------------------------------------------------------
for /f "tokens=2 delims==" %%a in ('wmic OS Get localdatetime /value') do set DT2=%%a
set STAMP2=%DT2:~0,8%_%DT2:~8,6%

echo. >> "%LOG%"
echo ============================================================ >> "%LOG%"
echo  P2 Discover Metrics -- Fin: %STAMP2%                        >> "%LOG%"
echo ============================================================ >> "%LOG%"

echo.
echo [%STAMP2%] P2 Discover Metrics completado
echo   Log stdout : %LOG%
echo   Log stderr : %ERR%
echo.

endlocal
