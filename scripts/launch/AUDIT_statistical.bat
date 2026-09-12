@echo off
setlocal enabledelayedexpansion

:: Forzar UTF-8 en cmd
chcp 65001 > nul

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

set PYTHON=C:\data\envs\des\python.exe
set ROOT=C:\desarrollo\fondos
set LOG_DIR=%ROOT%\out\audit\log

set DOMAIN=%~1
if "%DOMAIN%"=="" (
    echo Uso: AUDIT_statistical.bat costs^|p2 [--mode check] [--persist]
    exit /b 2
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
:: is the portable replacement — see repo-wide wmic removal noted 2026-09-13).
for /f %%a in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set STAMP=%%a
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

%PYTHON% -u -X utf8 scripts\audit\run_statistical_audit.py --domain %DOMAIN%%EXTRA_ARGS% >> "%LOG%" 2>> "%ERR%"
set RC=!ERRORLEVEL!

popd

for /f %%a in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set STAMP2=%%a

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
:: expansion is reverted by endlocal too) — chaining on one line lets cmd
:: substitute %RC% at parse time, while the scope is still active, before
:: endlocal and exit run. Splitting this into two lines silently returns 0
:: regardless of RC (verified empirically on this machine's cmd.exe — the
:: same endlocal+exit/b!VAR! pattern in P2_calculateIndicators.bat and other
:: scripts/launch/*.bat launchers likely has this latent bug too).
endlocal & exit /b %RC%
