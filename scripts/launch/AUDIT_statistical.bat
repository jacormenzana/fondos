@echo off
setlocal EnableExtensions EnableDelayedExpansion
call "%~dp0lib\common.bat" :init || (endlocal & exit /b 101)
call "%COMMON%" :utf8_on

:: ============================================================
:: AUDIT_statistical.bat
:: Ejecucion del motor de auditoria estadistica de distribuciones
:: (doc/reglas/AUDITORIA_ESTADISTICA.md) sobre un dominio: costs (P1
:: fund_master/fund_cost_schedule) o p2 (P2 fund_metrics).
::
:: Uso:
::   AUDIT_statistical.bat costs
::   AUDIT_statistical.bat p2 --mode check
::   AUDIT_statistical.bat costs --persist
:: %1 = dominio (costs|p2), obligatorio. Argumentos adicionales (%2, %3, ...)
:: se reenvian tal cual a run_statistical_audit.py (--mode, --persist, --run-id).
:: ============================================================

set LOG_DIR=%ROOT%\out\audit\log

if /i "%~1"=="-h"     goto :help
if /i "%~1"=="--help" goto :help
set DOMAIN=%~1
if "%DOMAIN%"=="" (
    echo Uso: AUDIT_statistical.bat costs^|p2 [--mode check] [--persist]
    call "%COMMON%" :utf8_off
    endlocal & exit /b %RC_USAGE%
)
shift

set EXTRA_ARGS=
:collect_args
if "%~1"=="" goto args_done
set EXTRA_ARGS=%EXTRA_ARGS% %1
shift
goto collect_args
:args_done

:: Timestamp YYYYMMDD_HHMMSS (wmic removed on newer Windows builds; PowerShell
:: is the portable replacement -- see repo-wide wmic removal noted 2026-09-13).
call "%COMMON%" :get_time STAMP yyyyMMdd_HHmmss
set LOG=%LOG_DIR%\log_AUDIT_%DOMAIN%_%STAMP%.log
set ERR=%LOG_DIR%\log_AUDIT_%DOMAIN%_%STAMP%_err.log

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo ============================================================ >> "%LOG%"
echo  Statistical Audit [%DOMAIN%] -- Inicio: %STAMP%              >> "%LOG%"
echo  ROOT:   %ROOT%                                               >> "%LOG%"
echo  PYTHON: %PYTHON%                                             >> "%LOG%"
echo  ARGS:   --domain %DOMAIN%%EXTRA_ARGS%                        >> "%LOG%"
echo ============================================================ >> "%LOG%"

echo.
echo [%STAMP%] Statistical Audit [%DOMAIN%] iniciado
echo   Log stdout : %LOG%
echo   Log stderr : %ERR%
echo.

pushd "%ROOT%"

"%PYTHON%" -u -X utf8 scripts\audit\run_statistical_audit.py --domain %DOMAIN%%EXTRA_ARGS% >> "%LOG%" 2>> "%ERR%"
set RC=!ERRORLEVEL!

popd

call "%COMMON%" :get_time STAMP2 yyyyMMdd_HHmmss

echo. >> "%LOG%"
echo ============================================================ >> "%LOG%"

if !RC! EQU 0 (
    echo  Statistical Audit [%DOMAIN%] -- Fin OK: %STAMP2%             >> "%LOG%"
    echo ============================================================ >> "%LOG%"
    echo.
    echo [%STAMP2%] Statistical Audit [%DOMAIN%] -- Fin OK
) else (
    echo  Statistical Audit [%DOMAIN%] -- Fin ERROR (code !RC!^): %STAMP2% >> "%LOG%"
    echo ============================================================ >> "%LOG%"
    echo  Statistical Audit [%DOMAIN%] -- Fin ERROR (code !RC!^): %STAMP2% >> "%ERR%"
    echo.
    echo [%STAMP2%] Statistical Audit [%DOMAIN%] -- Fin ERROR (RC=!RC!^)
    echo   Revisar: %ERR%
)

echo   Log stdout : %LOG%
echo   Log stderr : %ERR%
echo.

:: endlocal discards the setlocal-scoped RC before !RC! can expand (delayed
:: expansion is reverted by endlocal too) -- chaining on one line lets cmd
:: substitute %RC% at parse time, while the scope is still active, before
:: endlocal and exit run. Splitting this into two lines silently returns 0
:: regardless of RC (verified empirically on this machine's cmd.exe -- the
:: same endlocal+exit/b!VAR! pattern in P2_calculateIndicators.bat and other
:: scripts/launch/*.bat launchers likely has this latent bug too).
call "%COMMON%" :utf8_off
endlocal & exit /b %RC%

:help
echo Uso: AUDIT_statistical.bat costs^|p2 [--mode check] [--persist] [-h]
echo   dominio obligatorio (costs o p2); el resto se reenvia a run_statistical_audit.py
call "%COMMON%" :utf8_off
endlocal & exit /b 0
