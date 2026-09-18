@echo off
setlocal enabledelayedexpansion

:: Forzar UTF-8 en cmd
chcp 65001 > nul

:: ============================================================
:: P3_buildPortfolio.bat
:: Construye la cartera maestra: clasifica el regimen actual,
:: puntua todos los fondos (fund_scores) y construye/persiste la
:: cartera (portfolio_scenarios, portfolio_weights).
:: Modulo: scripts.launch.p3_build_portfolio
::
:: Fase 3c (P3 optimization plan, 2026-09-18): antes, este ciclo solo se
:: ejecutaba a mano desde scripts/test/test_scorer.py + test_portfolio.py
:: (herramientas de debug, no un launcher canonico) -- P3_generateReport.bat
:: solo genera el informe a partir de lo que ya este persistido; nada
:: automatizaba clasificar -> puntuar -> construir en un solo paso.
::
:: Uso:
::   P3_buildPortfolio.bat                       scenario_id autogenerado
::                                                (cartera_<regimen>_<YYYYMM>)
::   P3_buildPortfolio.bat mi_escenario_id        scenario_id explicito
::   P3_buildPortfolio.bat --dry-run              no persiste (debug)
::   P3_buildPortfolio.bat mi_escenario_id --dry-run   ambos combinados
:: ============================================================

set PYTHON=C:\data\envs\des\python.exe
set ROOT=C:\desarrollo\fondos
set LOG_DIR=%ROOT%\proyecto3\log

for /f %%a in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set STAMP=%%a
set LOG=%LOG_DIR%\log_P3_buildPortfolio_%STAMP%.log
set ERR=%LOG_DIR%\log_P3_buildPortfolio_%STAMP%_err.log

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo ============================================================
echo  P3 Build Portfolio -- Inicio: %STAMP%
echo  Args    : %*
echo  Log     : %LOG%
echo ============================================================
echo.

pushd "%ROOT%"

%PYTHON% -X utf8 scripts\launch\p3_build_portfolio.py %* >> "%LOG%" 2>> "%ERR%"

set RC=%ERRORLEVEL%
popd

for /f %%a in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set STAMP2=%%a

echo.
if %RC% NEQ 0 (
    echo [%STAMP2%] ERROR rc=%RC% -- ver %ERR%
) else (
    echo [%STAMP2%] P3 Build Portfolio completado
    echo   Log: %LOG%
)
echo.

:: endlocal discards delayed expansion before !VAR! on the next line could
:: expand it (verified empirically, same pattern as the other P2/P3
:: launchers) -- chain on one line so %RC% substitutes at parse time, while
:: the scope is still active.
endlocal & exit /b %RC%
