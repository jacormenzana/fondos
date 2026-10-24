@echo off
setlocal EnableExtensions EnableDelayedExpansion
call "%~dp0lib\common.bat" :init "%~nx0" & set "RC_BOOT=!ERRORLEVEL!"
if %RC_BOOT% NEQ 0 (endlocal & exit /b %RC_BOOT%)
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
::   P3_buildPortfolio.bat --no-audit             no persiste la auditoria P2 posterior al DRAFT
::   P3_buildPortfolio.bat --audit-compare-to ID  la auditoria posterior al DRAFT calcula la deriva contra la corrida ID
::                                                (el PRE <ID>_p2 que persistio el orquestador al empezar el ciclo)
:: Todos los argumentos se reenvian tal cual a p3_build_portfolio.py, salvo --no-audit y --audit-compare-to (son de este lanzador).
::
:: Auditoria P2 posterior al DRAFT: si la construccion termina con RC 0 y no es --dry-run, se ejecuta
:: P2_audit.bat --mode report --persist --run-id post_<sello>_p2 (log proyecto3\log\log_P3_auditP2_<sello>.log). La puerta
:: de promocion exige una corrida FULL, no INCOMPLETE, mas nueva que el DRAFT: esto solo da el orden temporal (la
:: identidad del DRAFT frente a la auditoria es otro control) y no cambia ninguna politica de promocion. Su resultado queda
:: en una linea `[AUDIT_P2_POST_DRAFT] OK|FAILED|SKIPPED ...` (consola y log). Un fallo de la auditoria NO cambia el RC de P3
:: y NO deja evidencia valida (una corrida caida o INCOMPLETE no cuenta); --no-audit, --dry-run o un RC distinto de 0 la omiten.
::
:: Codigos de salida: 0 ok; 2 entradas obsoletas (P3_EXIT_STALE_INPUTS); el resto, el de p3_build_portfolio.py; 100-199 los del
:: propio lanzador (NORMAS_BATCH.md).
:: ============================================================

:: -h / --help: the usage (this header) and out, BEFORE anything runs (NORMAS_BATCH.md section 3). This launcher forwards its arguments to
:: a real run, so an argument it did not recognise used to start one (2026-10-08: `--help` over the real launchers).
for %%A in (%*) do (
    if /i "%%~A"=="-h" goto :show_help
    if /i "%%~A"=="--help" goto :show_help
)

:: -- Argumentos: --no-audit es de este lanzador; el resto se reenvia tal cual (sin comillas externas, NORMAS_BATCH.md seccion 5) --
set "RUN_AUDIT=1"
set "AUD_CMP_ID="
set "IS_DRY="
set "PY_ARGS="
:arg_loop
if "%~1"=="" goto :args_done
if /i "%~1"=="--no-audit" set "RUN_AUDIT=0" & shift & goto :arg_loop
if /i "%~1"=="--audit-compare-to" if "%~2"=="" goto :bad_args
if /i "%~1"=="--audit-compare-to" set "AUD_CMP_ID=%~2" & shift & shift & goto :arg_loop
if /i "%~1"=="--dry-run" set "IS_DRY=1"
set PY_ARGS=!PY_ARGS! %1
shift
goto :arg_loop
:args_done

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

:: Telemetria de ciclo (opt-in: FONDOS_TELEMETRY=1; mejor esfuerzo, nunca cambia el RC). Solo si nadie mas es dueno del ciclo: bajo
:: P1P2P3_runCycle.bat / P2P3_runCycle.bat es el padre quien registra el paso P3_BUILD.
set "TELEM_OWNER="
if "%FONDOS_TELEMETRY%"=="1" if not defined FONDOS_CYCLE_ID (
    set "FONDOS_CYCLE_ID=%STAMP%"
    set "TELEM_OWNER=1"
    call "%COMMON%" :telemetry begin --launcher P3_buildPortfolio
    call "%COMMON%" :telemetry step-begin P3_BUILD
)

pushd "%ROOT%"
"%PYTHON%" -u -X utf8 scripts\launch\p3_build_portfolio.py !PY_ARGS! >> "%LOG%" 2>> "%ERR%"
set RC=!ERRORLEVEL!
popd

if defined TELEM_OWNER (
    call "%COMMON%" :telemetry step-end P3_BUILD !RC!
    if "!RC!"=="0" (set "TELEM_STATUS=OK") else (set "TELEM_STATUS=FAILED")
    call "%COMMON%" :telemetry evaluate-flags --status !TELEM_STATUS!
    if "!RC!"=="0" (call "%COMMON%" :telemetry end --status OK --rc !RC!) else (call "%COMMON%" :telemetry end --status FAILED --rc !RC! --failed-step P3_BUILD)
)

call "%COMMON%" :get_time STAMP2 yyyyMMdd_HHmmss

echo. >> "%LOG%"
echo  P3 Build Portfolio -- Fin: %STAMP2% (RC=!RC!^) >> "%LOG%"

echo.
if !RC! EQU 0 (
    echo [%STAMP2%] P3 Build Portfolio completado
    echo   Log: %LOG%
) else if !RC! EQU 2 (
    echo [%STAMP2%] P3 Build Portfolio -- ENTRADAS OBSOLETAS (P3_EXIT_STALE_INPUTS, RC=2^)
    echo   Nada se ha puntuado ni persistido. Refrescar con P1P2_runCycle.bat y repetir;
    echo   o --allow-stale si se asume el riesgo. Detalle: %LOG%
) else (
    echo [%STAMP2%] ERROR rc=!RC! -- ver %ERR%
    call "%COMMON%" :tail "%ERR%" 15
)
echo.

:: -- Auditoria P2 persistida DESPUES del DRAFT (orden temporal para la puerta de promocion; nunca cambia el RC de P3) --
set "AUD_WHY="
if "!RUN_AUDIT!"=="0" set "AUD_WHY=--no-audit"
if not defined AUD_WHY if defined IS_DRY set "AUD_WHY=--dry-run: no se construyo ningun DRAFT"
if not defined AUD_WHY if !RC! NEQ 0 set "AUD_WHY=la construccion termino con RC=!RC!: no hay DRAFT"
if defined AUD_WHY goto :audit_skipped
call "%COMMON%" :get_time AUD_STAMP yyyyMMdd_HHmmss
set "AUD_ID=post_!AUD_STAMP!_p2"
set "AUD_LOG=%LOG_DIR%\log_P3_auditP2_!AUD_STAMP!.log"
echo [!AUD_STAMP!] Auditoria P2 persistida tras el DRAFT, run-id !AUD_ID!
echo   Log: !AUD_LOG!
set "AUD_CMPARG="
if defined AUD_CMP_ID set "AUD_CMPARG=--compare-to !AUD_CMP_ID!"
call "%LAUNCH%\P2_audit.bat" --mode report --persist --run-id !AUD_ID! !AUD_CMPARG! --log "!AUD_LOG!" > nul
set "AUD_RC=!ERRORLEVEL!"
if !AUD_RC! EQU 0 goto :audit_ok
echo [AUDIT_P2_POST_DRAFT] FAILED rc=!AUD_RC! run-id=!AUD_ID! >> "%LOG%"
echo [AUDIT_P2_POST_DRAFT] FAILED rc=!AUD_RC! run-id=!AUD_ID!
echo   AVISO: la auditoria P2 posterior al DRAFT no termino bien: NO hay evidencia de promocion valida para este DRAFT
echo   (una corrida caida o INCOMPLETE no cuenta). El RC de P3 no cambia. Detalle: !AUD_LOG!
echo   Repetir: scripts\launch\P2_audit.bat --mode report --persist --run-id !AUD_ID!
goto :audit_done
:audit_ok
echo [AUDIT_P2_POST_DRAFT] OK run-id=!AUD_ID! >> "%LOG%"
echo [AUDIT_P2_POST_DRAFT] OK run-id=!AUD_ID!
echo   Persistida: su estado (PASS/FAIL/...) esta en control.audit_run; la puerta de promocion decide si basta.
goto :audit_done
:audit_skipped
echo [AUDIT_P2_POST_DRAFT] SKIPPED !AUD_WHY! >> "%LOG%"
echo [AUDIT_P2_POST_DRAFT] SKIPPED !AUD_WHY!
:audit_done
echo.

:: endlocal discards delayed expansion before !VAR! on the next line could
:: expand it (verified empirically, same pattern as the other P2/P3
:: launchers) -- chain on one line so %RC% substitutes at parse time, while
:: the scope is still active.
call "%COMMON%" :utf8_off
endlocal & exit /b %RC%


:: ------------------------------------------------------------
:: :bad_args -- --audit-compare-to sin valor: RC_USAGE, antes de tocar nada
:: ------------------------------------------------------------
:bad_args
echo [ERROR] --audit-compare-to requiere el id de la corrida de auditoria contra la que comparar
call "%COMMON%" :utf8_off
endlocal & exit /b %RC_USAGE%

:: ------------------------------------------------------------
:: :show_help -- usage = the header of this file (lib\batch_helpers.py usage); RC 0, nothing else runs.
:: ------------------------------------------------------------
:show_help
"%PYTHON%" "%LIB%\batch_helpers.py" usage "%~f0"
call "%COMMON%" :utf8_off
endlocal & exit /b 0
