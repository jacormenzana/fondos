@echo off
setlocal enabledelayedexpansion

:: Forzar UTF-8 en cmd
chcp 65001 > nul

:: ============================================================
:: P1_P2_Complete.bat  -- Ciclo completo P1 + P2
::
:: Ejecuta en orden:
::   1. P1_refreshBenchmarks.bat   - refresh benchmarks Morningstar
::   2. P1_discoverAllFunds.bat    - pipeline P1 completo (classify + family)
::   3. P2_discoverLoadMetrics.bat - macro (BCE/FRED/Eurostat) + NAV discover/load
::   4. P2_calculateIndicators.bat - metricas cuantitativas P2 + export
::
:: Aborta en el primer paso que devuelva RC != 0.
:: Cada sub-script genera su propio log detallado en su directorio de log.
:: Este script genera un log de orquestacion con timestamps y RCs por paso.
::
:: Uso:
::   cd C:\desarrollo\fondos\scripts\launch
::   P1_P2_Complete.bat
:: ============================================================

set PYTHON=C:\data\envs\des\python.exe
set ROOT=C:\desarrollo\fondos
set LAUNCH=%ROOT%\scripts\launch
set LOG_DIR=%ROOT%\proyecto1\log

:: --force flag: passed through to P2_calculateIndicators.bat to bypass
:: hash-cache AND quarterly OLS gate (force full OLS recompute).
:: Usage: P1_P2_Complete.bat --force
set FORCE_FLAG=
if /i "%~1"=="--force" set FORCE_FLAG=--force

:: Timestamp YYYYMMDD_HHMMSS
for /f "tokens=2 delims==" %%a in ('wmic OS Get localdatetime /value') do set DT=%%a
set STAMP=%DT:~0,8%_%DT:~8,6%
set LOG=%LOG_DIR%\log_P1_P2_complete_%STAMP%.log

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo ============================================================ >> "%LOG%"
echo  P1+P2 Complete -- Inicio: %STAMP%                           >> "%LOG%"
echo  ROOT:   %ROOT%                                              >> "%LOG%"
echo  PYTHON: %PYTHON%                                            >> "%LOG%"
echo  FORCE:  %FORCE_FLAG% >> "%LOG%"
echo ============================================================ >> "%LOG%"

echo.
echo [%STAMP%] P1+P2 Complete iniciado
echo   Log orquestacion: %LOG%
echo.

:: Prevenir suspension/hibernacion durante la ejecucion (~30-90 min)
powercfg -change -standby-timeout-ac 0 > nul 2>&1

set FINAL_RC=0

:: ============================================================
:: PASO 1: P1_refreshBenchmarks.bat
:: ============================================================
for /f "tokens=2 delims==" %%a in ('wmic OS Get localdatetime /value') do set DT=%%a
set T1=%DT:~8,6%
echo [%T1:~0,2%:%T1:~2,2%:%T1:~4,2%] PASO 1/4: P1_refreshBenchmarks
echo. >> "%LOG%"
echo --- PASO 1/4: P1_refreshBenchmarks -- Inicio: !T1! ---------- >> "%LOG%"

call "%LAUNCH%\P1_refreshBenchmarks.bat"
set RC1=!ERRORLEVEL!

for /f "tokens=2 delims==" %%a in ('wmic OS Get localdatetime /value') do set DT=%%a
set T1E=%DT:~8,6%
echo [%T1E:~0,2%:%T1E:~2,2%:%T1E:~4,2%] PASO 1/4 fin (RC=!RC1!)
echo --- PASO 1/4 fin: RC=!RC1!  Fin: !T1E! --------------------- >> "%LOG%"

if !RC1! NEQ 0 (
    set FINAL_RC=!RC1!
    echo ABORTADO en PASO 1 -- RC=!RC1! >> "%LOG%"
    echo.
    echo [ERROR] PASO 1 fallo (RC=!RC1!^). Ciclo abortado.
    goto :fin
)

:: ============================================================
:: PASO 2: P1_discoverAllFunds.bat
:: ============================================================
for /f "tokens=2 delims==" %%a in ('wmic OS Get localdatetime /value') do set DT=%%a
set T2=%DT:~8,6%
echo [%T2:~0,2%:%T2:~2,2%:%T2:~4,2%] PASO 2/4: P1_discoverAllFunds
echo. >> "%LOG%"
echo --- PASO 2/4: P1_discoverAllFunds -- Inicio: !T2! ----------- >> "%LOG%"

call "%LAUNCH%\P1_discoverAllFunds.bat"
set RC2=!ERRORLEVEL!

for /f "tokens=2 delims==" %%a in ('wmic OS Get localdatetime /value') do set DT=%%a
set T2E=%DT:~8,6%
echo [%T2E:~0,2%:%T2E:~2,2%:%T2E:~4,2%] PASO 2/4 fin (RC=!RC2!)
echo --- PASO 2/4 fin: RC=!RC2!  Fin: !T2E! --------------------- >> "%LOG%"

if !RC2! NEQ 0 (
    set FINAL_RC=!RC2!
    echo ABORTADO en PASO 2 -- RC=!RC2! >> "%LOG%"
    echo.
    echo [ERROR] PASO 2 fallo (RC=!RC2!^). Ciclo abortado.
    goto :fin
)

:: ============================================================
:: PASO 3: P2_discoverLoadMetrics.bat
:: ============================================================
for /f "tokens=2 delims==" %%a in ('wmic OS Get localdatetime /value') do set DT=%%a
set T3=%DT:~8,6%
echo [%T3:~0,2%:%T3:~2,2%:%T3:~4,2%] PASO 3/4: P2_discoverLoadMetrics
echo. >> "%LOG%"
echo --- PASO 3/4: P2_discoverLoadMetrics -- Inicio: !T3! -------- >> "%LOG%"

call "%LAUNCH%\P2_discoverLoadMetrics.bat"
set RC3=!ERRORLEVEL!

for /f "tokens=2 delims==" %%a in ('wmic OS Get localdatetime /value') do set DT=%%a
set T3E=%DT:~8,6%
echo [%T3E:~0,2%:%T3E:~2,2%:%T3E:~4,2%] PASO 3/4 fin (RC=!RC3!)
echo --- PASO 3/4 fin: RC=!RC3!  Fin: !T3E! --------------------- >> "%LOG%"

if !RC3! NEQ 0 (
    set FINAL_RC=!RC3!
    echo ABORTADO en PASO 3 -- RC=!RC3! >> "%LOG%"
    echo.
    echo [ERROR] PASO 3 fallo (RC=!RC3!^). Ciclo abortado.
    goto :fin
)

:: ============================================================
:: PASO 4: P2_calculateIndicators.bat
:: ============================================================
for /f "tokens=2 delims==" %%a in ('wmic OS Get localdatetime /value') do set DT=%%a
set T4=%DT:~8,6%
echo [%T4:~0,2%:%T4:~2,2%:%T4:~4,2%] PASO 4/4: P2_calculateIndicators
echo. >> "%LOG%"
echo --- PASO 4/4: P2_calculateIndicators -- Inicio: !T4! -------- >> "%LOG%"

call "%LAUNCH%\P2_calculateIndicators.bat" %FORCE_FLAG%
set RC4=!ERRORLEVEL!

for /f "tokens=2 delims==" %%a in ('wmic OS Get localdatetime /value') do set DT=%%a
set T4E=%DT:~8,6%
echo [%T4E:~0,2%:%T4E:~2,2%:%T4E:~4,2%] PASO 4/4 fin (RC=!RC4!)
echo --- PASO 4/4 fin: RC=!RC4!  Fin: !T4E! --------------------- >> "%LOG%"

if !RC4! NEQ 0 (
    set FINAL_RC=!RC4!
    echo ABORTADO en PASO 4 -- RC=!RC4! >> "%LOG%"
    echo.
    echo [ERROR] PASO 4 fallo (RC=!RC4!^). Revisar log P2_calculateIndicators.
    goto :fin
)

:fin

:: Restaurar suspension AC al valor por defecto de Windows (30 min)
powercfg -change -standby-timeout-ac 30 > nul 2>&1

:: Pie del log
for /f "tokens=2 delims==" %%a in ('wmic OS Get localdatetime /value') do set DT2=%%a
set STAMP2=%DT2:~0,8%_%DT2:~8,6%

echo. >> "%LOG%"
echo ============================================================ >> "%LOG%"

if !FINAL_RC! EQU 0 (
    echo  P1+P2 Complete -- Fin OK: %STAMP2%                          >> "%LOG%"
    echo  RC1=0  RC2=0  RC3=0  RC4=0                                 >> "%LOG%"
    echo ============================================================ >> "%LOG%"
    echo.
    echo [%STAMP2%] P1+P2 Complete -- Fin OK
) else (
    echo  P1+P2 Complete -- Fin ERROR: %STAMP2%                       >> "%LOG%"
    echo  RC1=!RC1!  RC2=!RC2!  RC3=!RC3!  RC4=!RC4!                 >> "%LOG%"
    echo ============================================================ >> "%LOG%"
    echo.
    echo [%STAMP2%] P1+P2 Complete -- Fin ERROR (FINAL_RC=!FINAL_RC!^)
)

echo   Log orquestacion: %LOG%
echo.

endlocal
exit /b !FINAL_RC!
