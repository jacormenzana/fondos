@echo off
setlocal EnableExtensions EnableDelayedExpansion
call "%~dp0lib\common.bat" :init "%~nx0" & set "RC_BOOT=!ERRORLEVEL!"
if %RC_BOOT% NEQ 0 (endlocal & exit /b %RC_BOOT%)
call "%COMMON%" :utf8_on

:: ============================================================
:: P2P3_runCycle.bat -- Ciclo P2 + P3: preflight -> auditoria P2 PRE -> snapshot de betas -> macro + NAV -> recalculo P2
::                      -> puerta de betas -> diagnosticos -> P3 (cartera + auditoria POST + informe)
::                      -> push opcional -> prueba opcional del loader de benchmarks
::
:: Subconjunto P2+P3 del ciclo maestro (P1P2P3_runCycle.bat): las fases son las del maestro filtradas por proyecto, en su orden
:: (scripts/launch/cycle_sequence.py; tests/test_cycle_sequence_e2e.py lo comprueba ejecutando cada orquestador).
:: Lanzador de PROPIETARIO (P2 de poblacion completa, ~4 h). Se detiene en el primer paso que falla;
:: la auditoria de betas sale con RC != 0 si hay NULL/NaN, betas implausibles (|beta| > umbral) o un
:: P2 sin terminar, de modo que un recalculo defectuoso nunca llega a P3.
::
:: Ningun valor de version o de baseline esta escrito aqui (norma: NORMAS_BATCH.md):
::   - CALC_VERSION se lee de proyecto2\src\pipeline\run_pipeline.py (p1p2_state.py calc-version).
::   - El baseline de betas se toma AHORA, antes del recalculo (macro_betas_p2p3_<STAMP>.csv en
::     out\audit\P2; la variable P1P2_AUDIT_DIR cambia la raiz out\audit), de modo que la comparacion
::     mide exactamente lo que este recalculo cambia. --baseline CSV usa uno existente (p. ej. el
::     macro_betas_pre_*.csv que P1P2_runCycle.bat guarda al empezar un ciclo).
::   - El numero de commits pendientes de push se cuenta en el momento (git).
:: Comparte el bloqueo de instancia unica con P1P2_runCycle.bat (ambos recalculan P2): mientras
:: uno corre, el otro se rechaza con RC 105. La suspension se gestiona como en aquel.
::
:: Uso:
::   P2P3_runCycle.bat                    ciclo completo
::   P2P3_runCycle.bat --baseline CSV     comparar contra un snapshot de betas existente
::   P2P3_runCycle.bat --skip-preflight    no ejecutar el preflight (BD alcanzable, DSN owner, disco)
::   P2P3_runCycle.bat --skip-macro        macro + NAV: solo NAV (sin carga macro)
::   P2P3_runCycle.bat --workers N         N hilos en el NAV load
::   P2P3_runCycle.bat --no-discover       sin refresco de macro + NAV: recalculo puro (tras un cambio de codigo o de flags)
::   P2P3_runCycle.bat --force             recomputo total de P2 (cache hash + gate OLS; tras subir CALC_VERSION)
::   P2P3_runCycle.bat --no-export         sin export Excel de P2
::   P2P3_runCycle.bat -- ARGS             argumentos reenviados a run_pipeline (P2)
::   P2P3_runCycle.bat --scenario ID       scenario_id de la cartera (por defecto autogenerado)
::   P2P3_runCycle.bat --report-dir DIR    directorio de salida del informe (por defecto out\reports\p3)
::   P2P3_runCycle.bat --p3-dry-run        P3 no persiste (debug): sin informe ni preguntas; auditoria POST de respaldo
::   P2P3_runCycle.bat --allow-stale       salta la puerta de frescura de P3 (con criterio)
::   P2P3_runCycle.bat --no-pause         sin pausa final (ejecucion programada)
::   P2P3_runCycle.bat --no-prompts        sin las preguntas finales (push y prueba del loader): ninguna se ejecuta
::   P2P3_runCycle.bat -h                 esta ayuda
:: Codigos de salida: 0 OK | 100 argumentos invalidos | 101 interprete no encontrado |
::   104 preflight fallido | 105 otra instancia en ejecucion | 106 driver PostgreSQL bloqueado | 107 baseline (auditoria PRE) no valido |
::   1-99 = RC propagado del paso que fallo.
:: ============================================================

set "LOCK_FILE=%STATE_DIR%\fondos_cycle.lock"

set "BASELINE="
set "NO_PAUSE="
set "NO_PROMPTS="
set "ALLOW_NO_BASELINE="
set "FLAG_skip_preflight="
set "FLAG_skip_macro="
set "FLAG_no_discover="
set "OPT_workers="
set "FLAG_force="
set "FLAG_no_export="
set "FLAG_p3_dry_run="
set "FLAG_allow_stale="
set "OPT_scenario="
set "OPT_report_dir="
set "P2_EXTRA="
set "LOG_DIR=%ROOT%\proyecto2\log"

:parse
if "%~1"=="" goto :parsed
if /i "%~1"=="--baseline" (
    if "%~2"=="" (
        echo [ERROR] --baseline requiere la ruta de un CSV de betas
        goto :usage
    )
    set "BASELINE=%~2"
    shift
    shift
    goto :parse
)
if /i "%~1"=="--no-pause" (
    set "NO_PAUSE=1"
    shift
    goto :parse
)
if /i "%~1"=="--no-prompts" (
    set "NO_PROMPTS=1"
    shift
    goto :parse
)
if /i "%~1"=="--allow-no-baseline" (
    set "ALLOW_NO_BASELINE=1"
    shift
    goto :parse
)
if /i "%~1"=="--skip-preflight" (
    set "FLAG_skip_preflight=1"
    shift
    goto :parse
)
if /i "%~1"=="--skip-macro" (
    set "FLAG_skip_macro=1"
    shift
    goto :parse
)
if /i "%~1"=="--no-discover" (
    set "FLAG_no_discover=1"
    shift
    goto :parse
)
if /i "%~1"=="--force" (
    set "FLAG_force=1"
    shift
    goto :parse
)
if /i "%~1"=="--no-export" (
    set "FLAG_no_export=1"
    shift
    goto :parse
)
if /i "%~1"=="--p3-dry-run" (
    set "FLAG_p3_dry_run=1"
    shift
    goto :parse
)
if /i "%~1"=="--allow-stale" (
    set "FLAG_allow_stale=1"
    shift
    goto :parse
)
if /i "%~1"=="--scenario" goto :parse_str
if /i "%~1"=="--report-dir" goto :parse_str
if "%~1"=="--" (
    shift
    goto :collect_p2_args
)
if /i "%~1"=="--workers" goto :parse_workers
if /i "%~1"=="-h"     goto :help
if /i "%~1"=="--help" goto :help
echo [ERROR] Argumento desconocido: %~1
goto :usage

:: --nombre-opcion VALOR -> OPT_nombre_opcion=VALOR (no vacio y que no parezca otra opcion)
:parse_str
set "VAL=%~2"
if "!VAL!"=="" (
    echo [ERROR] %~1 requiere un valor
    goto :usage
)
if "!VAL:~0,2!"=="--" (
    echo [ERROR] %~1 requiere un valor, no la opcion "%~2"
    goto :usage
)
set "OPT=%~1"
set "OPT=!OPT:--=!"
set "OPT=!OPT:-=_!"
set "OPT_!OPT!=%~2"
shift
shift
goto :parse

:: Todo lo que sigue a "--" va a run_pipeline, SIN comillas externas (ver P1P2_runCycle.bat).
:collect_p2_args
if "%~1"=="" goto :parsed
set P2_EXTRA=!P2_EXTRA! %1
shift
goto :collect_p2_args

:parse_workers
call "%COMMON%" :is_uint "%~2"
if errorlevel 1 (
    echo [ERROR] --workers requiere un entero positivo: "%~2"
    goto :usage
)
set "OPT_workers=%~2"
shift
shift
goto :parse
:parsed

if defined FLAG_no_discover if defined FLAG_skip_macro (
    echo [ERROR] --no-discover y --skip-macro se excluyen: el primero omite todo el refresco de datos
    goto :usage
)
set "S3_ARGS="
if defined FLAG_skip_macro set "S3_ARGS=--skip-macro"
if defined OPT_workers set "S3_ARGS=!S3_ARGS! --workers !OPT_workers!"
set "S4_ARGS="
if defined FLAG_force set "S4_ARGS=--force"
if defined FLAG_no_export set "S4_ARGS=!S4_ARGS! --no-export"
set "S4_ARGS=!S4_ARGS!!P2_EXTRA!"
if defined OPT_scenario if not "!OPT_scenario: =!"=="!OPT_scenario!" (
    echo [ERROR] --scenario no admite espacios: "!OPT_scenario!"
    goto :usage
)

if defined BASELINE if not exist "!BASELINE!" (
    echo [ERROR] el baseline indicado no existe: !BASELINE!
    call "%COMMON%" :utf8_off
    endlocal & exit /b %RC_USAGE%
)

:: Instancia unica (ver P1P2_runCycle.bat): el manejador 9 vive mientras corre :cycle.
if not exist "%STATE_DIR%" mkdir "%STATE_DIR%"
set "LOCK_HELD="
if defined FONDOS_CYCLE_LOCK (call :cycle) else (call :cycle 9>"%LOCK_FILE%")
set "RC=!ERRORLEVEL!"
if not defined LOCK_HELD (
    echo [ERROR] Otro ciclo esta en ejecucion ^(%LOCK_FILE%^). No se hace nada.
    set "RC=%RC_BUSY%"
)
call "%COMMON%" :utf8_off
endlocal & exit /b %RC%


:: ############################################################
:: :cycle -- el ciclo propiamente dicho; solo se ejecuta con el bloqueo adquirido.
:: ############################################################
:cycle
set "LOCK_HELD=1"
set "FONDOS_CYCLE_LOCK=1"
set "RC=0"
cd /d "%ROOT%"
call "%COMMON%" :get_time STAMP yyyyMMdd_HHmmss
if not exist "%AUDIT_P2_DIR%" mkdir "%AUDIT_P2_DIR%"

:: -- 0. Preflight: un fallo no toca nada (el mismo contrato que el resto de orquestadores) -------------
set "PRE_RC=0"
if defined FLAG_skip_preflight (
    echo --- PREFLIGHT: omitido ^(--skip-preflight^)
) else (
    if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
    call "%PHASES%" :preflight "%LOG_DIR%\log_P2P3_runCycle_preflight_%STAMP%.log"
)
if !PRE_RC! NEQ 0 (
    echo [ERROR] Preflight fallido ^(RC=!PRE_RC!^). No se ejecuta ningun paso.
    set "RC=!PRE_RC!"
    goto :finish
)

call "%COMMON%" :standby_disable owner

:: Telemetria de ciclo (opt-in: FONDOS_TELEMETRY=1; mejor esfuerzo, nunca cambia ningun RC). Dueno del ciclo si nadie lo es ya.
set "TELEM_OWNER="
set "TELEM_STEP="
if "%FONDOS_TELEMETRY%"=="1" if not defined FONDOS_CYCLE_ID (
    set "FONDOS_CYCLE_ID=%STAMP%"
    set "TELEM_OWNER=1"
    call "%COMMON%" :telemetry begin --launcher P2P3_runCycle
)

:: -- CALC_VERSION vigente (nunca escrita a mano) ----------------------------------
call "%COMMON%" :state_query calc-version CALC_VER
if not defined CALC_VER (
    echo [ERROR] no se pudo leer CALC_VERSION de proyecto2\src\pipeline\run_pipeline.py
    set "RC=%RC_ENV%"
    goto :finish
)

:: -- 1. Auditoria P2 PRE (baseline congelado, antes de tocar nada) -------------------
echo.
echo ===================================================
echo 1. Auditoria P2 PRE (pre_!STAMP!_p2^)
echo ===================================================
set "PRE_ID=pre_!STAMP!_p2"
call "%COMMON%" :telemetry step-begin AUDIT_BASE
call "%PHASES%" :audit_p2 !PRE_ID!
set "PRE_AUD_RC=!AUD_RC!"
call "%COMMON%" :telemetry step-end AUDIT_BASE !PRE_AUD_RC!
call "%COMMON%" :telemetry audit-run p2_metrics pre !PRE_ID!
call "%PHASES%" :baseline_verdict 0 !PRE_AUD_RC!
if !BASELINE_RC! NEQ 0 (
    echo [ERROR] la auditoria PRE no es valida ^(RC=!BASELINE_RC!^): no se recalcula nada. Repetir, o --allow-no-baseline.
    set "RC=!BASELINE_RC!"
    set "TELEM_STEP=AUDIT_BASE"
    goto :finish
)

:: -- 1b. Baseline de betas (antes de recalcular) -----------------------------------
echo ===================================================
echo 1b. Baseline de betas macro (antes del recalculo P2)
echo ===================================================
if defined BASELINE (
    echo Usando el baseline indicado: !BASELINE!
) else (
    set "BASELINE=%AUDIT_P2_DIR%\macro_betas_p2p3_!STAMP!.csv"
    call "%COMMON%" :telemetry step-begin BETA_SNAPSHOT
    "%PYTHON%" -X utf8 scripts\audit\beta_shift_audit.py --snapshot "!BASELINE!"
    set "BS_RC=!ERRORLEVEL!"
    call "%COMMON%" :telemetry step-end BETA_SNAPSHOT !BS_RC!
    if !BS_RC! NEQ 0 (
        echo [ERROR] no se pudo tomar el baseline de betas.
        set "RC=!BS_RC!"
        set "TELEM_STEP=BETA_SNAPSHOT"
        goto :finish
    )
)

:: -- 1c. Macro + NAV (mismo paso que P2_runCycle y P1P2_runCycle: P2 recalcula sobre datos frescos) -----------
if defined FLAG_no_discover (
    echo --- macro + NAV: omitido ^(--no-discover: recalculo sin refrescar datos^)
    goto :after_discover
)
echo.
echo ===================================================
echo 1c. Macro + NAV (P2_discoverLoadMetrics^)
echo ===================================================
call "%COMMON%" :telemetry step-begin P2_DISCOVER
call "%LAUNCH%\P2_discoverLoadMetrics.bat" !S3_ARGS!
set "DL_RC=!ERRORLEVEL!"
call "%COMMON%" :telemetry step-end P2_DISCOVER !DL_RC!
if !DL_RC! NEQ 0 (
    set "RC=!DL_RC!"
    set "TELEM_STEP=P2_DISCOVER"
    goto :finish
)
:after_discover

:: -- 2. Recalculo P2 -------------------------------------------------------------
echo.
echo ===================================================
echo 2. Ejecutando recalculo P2 (CALC_VERSION !CALC_VER!)
echo ===================================================
call "%COMMON%" :telemetry step-begin P2_CALC
call "%LAUNCH%\P2_calculateIndicators.bat" !S4_ARGS!
set "P2_RC=!ERRORLEVEL!"
call "%COMMON%" :telemetry step-end P2_CALC !P2_RC!
call "%COMMON%" :telemetry ingest-p2
if !P2_RC! NEQ 0 (
    set "RC=!P2_RC!"
    set "TELEM_STEP=P2_CALC"
    goto :finish
)

:: -- 3. Auditoria de betas -------------------------------------------------------
echo.
echo ===================================================
echo 3. Beta-shift audit (CALC_VERSION !CALC_VER!)
echo ===================================================
call "%COMMON%" :telemetry step-begin BETA_COMPARE
"%PYTHON%" -X utf8 scripts\audit\beta_shift_audit.py --compare "!BASELINE!" --version !CALC_VER! --out "%AUDIT_P2_DIR%\beta_shift_outliers_!STAMP!.csv"
set "BC_RC=!ERRORLEVEL!"
call "%COMMON%" :telemetry step-end BETA_COMPARE !BC_RC!
if !BC_RC! NEQ 0 (
    echo [ERROR] Beta-shift audit fallo con codigo !BC_RC!.
    set "RC=!BC_RC!"
    set "TELEM_STEP=BETA_COMPARE"
    call :post_audit_fallback
    goto :finish
)
echo [OK] Beta-shift audit paso correctamente.

:: -- 3b. Diagnosticos informativos (los del ciclo maestro; nunca cambian el RC) ------------------------
call "%PHASES%" :p3_freshness
call "%PHASES%" :cycle_report !STAMP!

:: -- 4. P3 -----------------------------------------------------------------------
echo.
echo ===================================================
echo 4. Ejecutando P3 (Build Portfolio y Generate Report)
echo ===================================================
call "%COMMON%" :telemetry step-begin P3_BUILD
:: La auditoria P2 POST se hace UNA vez, tras el DRAFT (P3_buildPortfolio): es a la vez la deriva y la evidencia de promocion.
set "P3_ARGS="
if defined OPT_scenario set "P3_ARGS=!OPT_scenario!"
if defined PRE_ID if "!PRE_AUD_RC!"=="0" set "P3_ARGS=!P3_ARGS! --audit-compare-to !PRE_ID!"
if defined FLAG_p3_dry_run set "P3_ARGS=!P3_ARGS! --dry-run"
if defined FLAG_allow_stale set "P3_ARGS=!P3_ARGS! --allow-stale"
call "%LAUNCH%\P3_buildPortfolio.bat" !P3_ARGS!
set "P3B_RC=!ERRORLEVEL!"
call "%COMMON%" :telemetry step-end P3_BUILD !P3B_RC!
if !P3B_RC! NEQ 0 (
    set "RC=!P3B_RC!"
    set "TELEM_STEP=P3_BUILD"
    call :post_audit_fallback
    goto :finish
)
if defined FLAG_p3_dry_run (
    echo Informe omitido: --p3-dry-run no persiste nada.
    call :post_audit_fallback
    goto :finish
)
call "%COMMON%" :telemetry step-begin P3_REPORT
:: --draft: el ciclo informa el DRAFT que acaba de construir (marcado NO PRODUCCION); el informe oficial lo genera el owner con
:: P3_generateReport.bat tras promover (p3_promote_scenario.py). Sin esto el informe fallaria (RC 3) en cada ciclo hasta promover.
if defined OPT_report_dir (
    call "%LAUNCH%\P3_generateReport.bat" --draft "!OPT_report_dir!"
) else (
    call "%LAUNCH%\P3_generateReport.bat" --draft
)
set "P3R_RC=!ERRORLEVEL!"
call "%COMMON%" :telemetry step-end P3_REPORT !P3R_RC!
if !P3R_RC! NEQ 0 (
    set "RC=!P3R_RC!"
    set "TELEM_STEP=P3_REPORT"
    goto :finish
)

:: -- 5. Push opcional (siempre con confirmacion) -----------------------------------
if defined NO_PROMPTS (
    echo --no-prompts: sin push ni prueba del loader.
    goto :finish
)
echo.
set "BRANCH="
set "AHEAD="
for /f "delims=" %%b in ('git rev-parse --abbrev-ref HEAD 2^>nul') do set "BRANCH=%%b"
if defined BRANCH for /f "delims=" %%n in ('git rev-list --count origin/!BRANCH!..!BRANCH! 2^>nul') do set "AHEAD=%%n"
if not defined AHEAD (
    echo No se pudo contar los commits pendientes de push: se omite el push.
) else if "!AHEAD!"=="0" (
    echo No hay commits pendientes de push en !BRANCH!.
) else (
    set "PUSH_CHOICE="
    set /p "PUSH_CHOICE=Estas satisfecho con los reportes? Hacer git push de !AHEAD! commits a origin/!BRANCH!? (S/N): "
    if /i "!PUSH_CHOICE!"=="S" (
        echo Haciendo push a origin !BRANCH!...
        git push origin !BRANCH!
        if errorlevel 1 (
            set "RC=!ERRORLEVEL!"
            goto :finish
        )
    ) else (
        echo Saltando el paso de git push.
    )
)

:: -- 6. Prueba opcional del loader de benchmarks (FND-0088) -------------------------
echo.
echo ===================================================
echo 5. Test FND-0088 loader
echo ===================================================
echo AVISO: Esto consulta Morningstar y escribe en live.
set "LOADER_CHOICE="
set /p "LOADER_CHOICE=Deseas lanzar la prueba en una muestra de 40 ahora? (S/N): "
if /i "!LOADER_CHOICE!"=="S" (
    "%PYTHON%" -X utf8 -m proyecto1.src.loaders.benchmark_loader --mode update --sample 40
    if errorlevel 1 (
        set "RC=!ERRORLEVEL!"
        goto :finish
    )
) else (
    echo Saltando el test del loader.
)

:finish
call "%COMMON%" :standby_restore
if defined TELEM_OWNER (
    set "TELEM_STATUS=OK"
    set "TFS="
    if "!RC!" NEQ "0" (
        set "TELEM_STATUS=FAILED"
        if defined TELEM_STEP set "TFS=--failed-step !TELEM_STEP!"
    )
    call "%COMMON%" :telemetry evaluate-flags --status !TELEM_STATUS!
    call "%COMMON%" :telemetry end --status !TELEM_STATUS! --rc !RC! !TFS!
)
echo.
echo ===================================================
if "!RC!"=="0" (
    echo [OK] Todas las operaciones han finalizado con exito.
) else (
    echo [ERROR] El proceso se interrumpio debido a un error ^(RC=!RC!^).
)
echo ===================================================
if not defined NO_PAUSE pause
exit /b !RC!


:: ------------------------------------------------------------
:: Salidas de error de argumentos / ayuda
:: ------------------------------------------------------------
:usage
call :usage_text
call "%COMMON%" :utf8_off
endlocal & exit /b %RC_USAGE%

:help
call :usage_text
call "%COMMON%" :utf8_off
endlocal & exit /b 0

:: ------------------------------------------------------------
:: :post_audit_fallback -- auditoria P2 POST contra el PRE de este ciclo cuando P3 no la hizo (build fallido, puerta de betas, --p3-dry-run).
::   Informativa: nunca cambia el RC.
:: ------------------------------------------------------------
:post_audit_fallback
if not defined PRE_ID exit /b 0
if not "!PRE_AUD_RC!"=="0" exit /b 0
call "%COMMON%" :get_time FB_STAMP yyyyMMdd_HHmmss
echo [AUDITORIA] P3 no hizo la auditoria P2 POST: auditoria de respaldo post_!FB_STAMP!_p2 vs !PRE_ID!
call "%PHASES%" :audit_p2 post_!FB_STAMP!_p2 !PRE_ID!
if !AUD_RC! NEQ 0 echo [WARN] la auditoria P2 POST de respaldo no termino bien ^(RC=!AUD_RC!^)
exit /b 0

:usage_text
echo Uso: P2P3_runCycle.bat [--baseline CSV] [--allow-no-baseline] [--skip-preflight] [--skip-macro] [--workers N] [--no-discover] [--no-pause] [--no-prompts]
echo   --skip-preflight     no ejecutar el preflight
echo   --skip-macro         macro + NAV: solo NAV
echo   --workers N          N hilos en el NAV load
echo   --no-discover        sin refresco de macro + NAV (recalculo puro)
echo   --force              recomputo total de P2 (cache hash + gate OLS)
echo   --no-export          sin export Excel de P2
echo   -- ARGS              argumentos reenviados a run_pipeline
echo   --scenario ID        scenario_id de la cartera (por defecto autogenerado)
echo   --report-dir DIR     directorio de salida del informe
echo   --p3-dry-run         P3 no persiste (sin informe ni preguntas; auditoria POST de respaldo)
echo   --allow-stale        salta la puerta de frescura de P3
echo   --allow-no-baseline  si la auditoria P2 PRE no es valida: avisar y seguir (por defecto RC 107)
echo   --baseline CSV   comparar las betas contra un snapshot existente (por defecto se toma uno antes del recalculo)
echo   --no-pause       sin pausa final
echo   --no-prompts      sin las preguntas finales (push y prueba del loader)
echo   -h, --help       esta ayuda
exit /b 0
