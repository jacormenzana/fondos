@echo off
setlocal EnableExtensions EnableDelayedExpansion
call "%~dp0lib\common.bat" :init || (endlocal & exit /b 101)
call "%COMMON%" :utf8_on

:: ============================================================
:: P3_buildPortfolio.bat
:: Construye la cartera maestra: clasifica el regimen actual,
:: puntua todos los fondos (fund_scores) y construye/persiste la
:: cartera (portfolio_scenarios, portfolio_weights).
:: Modulo: scripts.launch.p3_build_portfolio
::
:: Antes de puntuar/persistir, p3_build_portfolio.py comprueba la frescura de las entradas
:: (data_freshness.py) y sale con RC 2 si estan obsoletas: este launcher lo explica y dice
:: como resolverlo.
::
:: Uso:
::   P3_buildPortfolio.bat                       scenario_id autogenerado
::                                                (cartera_<regimen>_<YYYYMM>)
::   P3_buildPortfolio.bat mi_escenario_id        scenario_id explicito
::   P3_buildPortfolio.bat --dry-run              no persiste (debug; la frescura solo avisa)
::   P3_buildPortfolio.bat mi_escenario_id --dry-run   ambos combinados
::   P3_buildPortfolio.bat --allow-stale          salta la puerta de frescura (con criterio)
:: Todos los argumentos se reenvian tal cual a p3_build_portfolio.py.
:: ============================================================

set LOG_DIR=%ROOT%\proyecto3\log

call "%COMMON%" :get_time STAMP yyyyMMdd_HHmmss
set LOG=%LOG_DIR%\log_P3_buildPortfolio_%STAMP%.log
set ERR=%LOG_DIR%\log_P3_buildPortfolio_%STAMP%_err.log

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo ============================================================ >> "%LOG%"
echo  P3 Build Portfolio -- Inicio: %STAMP%                       >> "%LOG%"
echo  Args    : %*                                                >> "%LOG%"
echo ============================================================ >> "%LOG%"

echo ============================================================
echo  P3 Build Portfolio -- Inicio: %STAMP%
echo  Args    : %*
echo  Log     : %LOG%
echo  Err     : %ERR%
echo ============================================================
echo.

pushd "%ROOT%"
"%PYTHON%" -u -X utf8 scripts\launch\p3_build_portfolio.py %* >> "%LOG%" 2>> "%ERR%"
set RC=!ERRORLEVEL!
popd

call "%COMMON%" :get_time STAMP2 yyyyMMdd_HHmmss

echo. >> "%LOG%"
echo  P3 Build Portfolio -- Fin: %STAMP2% (RC=!RC!^) >> "%LOG%"

echo.
if !RC! EQU 0 (
    echo [%STAMP2%] P3 Build Portfolio completado
    echo   Log: %LOG%
) else if !RC! EQU 2 (
    echo [%STAMP2%] P3 Build Portfolio -- ENTRADAS OBSOLETAS (P3_EXIT_STALE_INPUTS, RC=2^)
    echo   Nada se ha puntuado ni persistido. Refrescar con P1_P2_Complete.bat y repetir;
    echo   o --allow-stale si se asume el riesgo. Detalle: %LOG%
) else (
    echo [%STAMP2%] ERROR rc=!RC! -- ver %ERR%
    call "%COMMON%" :tail "%ERR%" 15
)
echo.

:: endlocal discards delayed expansion before !VAR! on the next line could
:: expand it (verified empirically, same pattern as the other P2/P3
:: launchers) -- chain on one line so %RC% substitutes at parse time, while
:: the scope is still active.
call "%COMMON%" :utf8_off
endlocal & exit /b %RC%
