@echo off
setlocal EnableExtensions EnableDelayedExpansion
call "%~dp0lib\common.bat" :init "%~nx0" & set "RC_BOOT=!ERRORLEVEL!"
if %RC_BOOT% NEQ 0 (endlocal & exit /b %RC_BOOT%)
call "%COMMON%" :utf8_on

:: ============================================================
:: P1P2P3_runCycle.bat -- Ciclo completo de datos y cartera: P1 -> P2 -> puerta de betas -> P3
::                 (lanzador de PROPIETARIO; norma: doc/reglas/NORMAS_BATCH.md)
::
:: Integra, sin repetir nada, lo que hacian P1P2_runCycle.bat y P2P3_runCycle.bat:
::   FASE A  P1P2_runCycle.bat   TODO el ciclo P1+P2 (preflight, baseline, snapshot de betas, harvest,
::                                pasos 1-4, auditoria final, diagnosticos) con TODAS sus opciones.
::   FASE B  puerta de betas      el beta_shift_audit de P2P3_runCycle.bat: bloqueante. Compara las
::                                betas actuales con el snapshot que el ciclo tomo al empezar y con la
::                                CALC_VERSION vigente; NULL/NaN, |beta| implausibles o un P2 sin terminar
::                                detienen el proceso antes de P3. (P1P2_runCycle ya hizo P2: no se
::                                recalcula, a diferencia de P2P3_runCycle.bat, que lo repite.)
::   FASE C  P3                   P3_buildPortfolio.bat (con su propia puerta de frescura) y
::                                P3_generateReport.bat.
::   FASE D  confirmaciones       push opcional (cuenta los commits pendientes, siempre con
::                                confirmacion) y prueba opcional del loader de benchmarks (FND-0088).
:: Se detiene en la primera fase que falla y devuelve su codigo tal cual.
::
:: Uso:  P1P2P3_runCycle.bat [opciones del ciclo P1+P2] [opciones de P3] [-- argumentos_para_run_pipeline]
::   Ciclo P1+P2: todas las de `P1P2_runCycle.bat --help` (--from N, --force, --no-harvest, --shadow N, ...),
::                que se reenvian tal cual (la tabla vive en lib\p1p2_options.bat).
::   --only-p3          solo FASES B-D, sobre el ultimo ciclo P1+P2 si termino OK (reanudar tras un
::                      fallo de P3, o construir una cartera nueva sin repetir el ciclo)
::   --baseline CSV     FASE B contra este snapshot de betas en vez del que tomo el ciclo
::   --scenario ID      scenario_id de la cartera (por defecto autogenerado: cartera_<regimen>_<YYYYMM>)
::   --report-dir DIR   directorio de salida del informe (por defecto out\reports\p3)
::   --p3-dry-run       P3 no persiste (debug); omite el informe y el push
::   --allow-stale      salta la puerta de frescura de P3 (con criterio)
::   --no-prompts       sin las confirmaciones de la FASE D (ejecucion programada)
::   --no-pause         sin pausa final
::   -h, --help         esta ayuda (incluye la del ciclo)
:: Comparte el bloqueo de instancia unica con P1P2_runCycle.bat y P2P3_runCycle.bat (RC 105).
:: La suspension la gestiona este lanzador para TODO el proceso (los que invoca no la tocan).
::
:: Codigos de salida: 0 OK | 100 argumentos invalidos | 101 interprete no encontrado |
::   102 --only-p3 rechazado (el ultimo ciclo no termino OK) | 103 sin baseline de betas |
::   105 otra instancia en ejecucion | 1-99 = RC propagado de la fase/herramienta que fallo
::   (P1P2_runCycle propaga los suyos: 100-104 de su guarda llegan tal cual).
:: ============================================================

:: ------------------------------------------------------------
:: CONFIGURACION
:: ------------------------------------------------------------
set "LOG_DIR=%STATE_DIR%"
set "LOCK_FILE=%STATE_DIR%\fondos_cycle.lock"

:: Opciones del ciclo P1+P2 (BOOL_OPTS / UINT_OPTS): una sola tabla, la de P1P2_runCycle.bat.
call "%LAUNCH%\lib\p1p2_options.bat"
:: Opciones propias: sin valor (--x-y -> FLAG_x_y) y con valor de texto (--x-y V -> OPT_x_y).
set "OWN_BOOL=--only-p3 --no-prompts --no-pause --allow-stale --p3-dry-run"
set "OWN_STR=--scenario --report-dir --baseline"

:: ------------------------------------------------------------
:: ARGUMENTOS
:: ------------------------------------------------------------
set "CYCLE_ARGS="
for /f "delims==" %%V in ('set FLAG_ 2^>nul') do set "%%V="
for /f "delims==" %%V in ('set OPT_ 2^>nul') do set "%%V="

:parse
if "%~1"=="" goto :parsed
for %%O in (%BOOL_OPTS%) do if /i "%~1"=="%%O" goto :fwd_bool
for %%O in (%UINT_OPTS%) do if /i "%~1"=="%%O" goto :fwd_uint
for %%O in (%OWN_BOOL%) do if /i "%~1"=="%%O" goto :parse_bool
for %%O in (%OWN_STR%) do if /i "%~1"=="%%O" goto :parse_str
if /i "%~1"=="--from" (
    if "%~2"=="" (
        echo [ERROR] --from requiere el numero de paso del ciclo P1+P2 ^(1-4^)
        goto :usage
    )
    set "CYCLE_ARGS=!CYCLE_ARGS! --from %~2"
    shift
    shift
    goto :parse
)
if /i "%~1"=="--from-any" (
    set "CYCLE_ARGS=!CYCLE_ARGS! --from-any"
    shift
    goto :parse
)
if /i "%~1"=="-h"     goto :help
if /i "%~1"=="--help" goto :help
if "%~1"=="--" (
    set "CYCLE_ARGS=!CYCLE_ARGS! --"
    shift
    goto :collect_extra
)
echo [ERROR] Argumento desconocido: %~1
goto :usage

:: Opciones del ciclo: se validan aqui (para fallar antes de empezar nada) y se reenvian.
:fwd_bool
set "CYCLE_ARGS=!CYCLE_ARGS! %~1"
shift
goto :parse

:fwd_uint
call "%COMMON%" :is_uint "%~2"
if errorlevel 1 (
    echo [ERROR] %~1 requiere un entero positivo: "%~2"
    goto :usage
)
set "CYCLE_ARGS=!CYCLE_ARGS! %~1 %~2"
shift
shift
goto :parse

:: --nombre-opcion -> FLAG_nombre_opcion=1
:parse_bool
set "OPT=%~1"
set "OPT=!OPT:--=!"
set "OPT=!OPT:-=_!"
set "FLAG_!OPT!=1"
shift
goto :parse

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

:: Todo lo que sigue a "--" va a run_pipeline (ver P1P2_runCycle.bat). SIN comillas externas en el set.
:collect_extra
if "%~1"=="" goto :parsed
set CYCLE_ARGS=!CYCLE_ARGS! %1
shift
goto :collect_extra

:parsed

:: Combinaciones sin sentido: se rechazan antes de tocar nada.
if defined FLAG_only_p3 if defined CYCLE_ARGS (
    echo [ERROR] las opciones del ciclo P1+P2 no se aplican con --only-p3 ^(no hay ciclo que ejecutar^)
    goto :usage
)
if defined OPT_scenario if not "!OPT_scenario: =!"=="!OPT_scenario!" (
    echo [ERROR] --scenario no admite espacios: "!OPT_scenario!"
    goto :usage
)
if defined OPT_baseline if not exist "!OPT_baseline!" (
    echo [ERROR] el baseline indicado no existe: !OPT_baseline!
    goto :usage
)

:: ------------------------------------------------------------
:: INSTANCIA UNICA (ver P1P2_runCycle.bat): el manejador 9 vive mientras corre :cycle. FONDOS_CYCLE_LOCK
:: lo heredan los lanzadores de nivel superior que este invoca (P1P2_runCycle.bat), que no vuelven a pedirlo.
:: ------------------------------------------------------------
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
:: :cycle -- el proceso propiamente dicho; solo se ejecuta con el bloqueo adquirido.
:: ############################################################
:cycle
set "LOCK_HELD=1"
set "FONDOS_CYCLE_LOCK=1"
set "RC=0"
cd /d "%ROOT%"
call "%COMMON%" :log_open "%LOG_DIR%" P1P2P3_runCycle "P1+P2+P3"
echo  Opciones del ciclo: !CYCLE_ARGS! >> "%LOG%"
echo  Opciones propias: >> "%LOG%"
set FLAG_ >> "%LOG%" 2>nul
set OPT_ >> "%LOG%" 2>nul
echo ============================================================ >> "%LOG%"
echo [%STAMP%] P1+P2+P3 iniciado
echo   Log: %LOG%
echo.

call "%COMMON%" :standby_disable owner

:: Telemetria de ciclo (opt-in: FONDOS_TELEMETRY=1; mejor esfuerzo, nunca cambia ningun RC). Este lanzador es el DUENO del ciclo: exporta
:: FONDOS_CYCLE_ID y P1P2_runCycle.bat, anidado, solo anade sus pasos; aqui se anaden la puerta de betas y P3.
set "TELEM_OWNER="
set "TELEM_STEP="
if "%FONDOS_TELEMETRY%"=="1" if not defined FONDOS_CYCLE_ID (
    set "FONDOS_CYCLE_ID=%STAMP%"
    set "TELEM_OWNER=1"
    call "%COMMON%" :telemetry begin --launcher P1P2P3_runCycle
)

:: ============================================================
:: FASE A: ciclo P1+P2 (o solo comprobar que el ultimo termino OK con --only-p3)
:: ============================================================
if defined FLAG_only_p3 goto :phase_a_skip
echo ===================================================
echo A. Ciclo P1+P2 ^(P1P2_runCycle.bat^)
echo ===================================================
echo --- FASE A: P1P2_runCycle.bat !CYCLE_ARGS! >> "%LOG%"
:: --defer-post-audit: la auditoria P2 POST la hace P3_buildPortfolio tras el DRAFT (una sola, que ademas es la evidencia de promocion).
call "%LAUNCH%\P1P2_runCycle.bat" --defer-post-audit !CYCLE_ARGS!
set "RC=!ERRORLEVEL!"
echo --- FASE A fin: RC=!RC! >> "%LOG%"
if !RC! NEQ 0 (
    echo [ERROR] el ciclo P1+P2 fallo ^(RC=!RC!^): no se construye P3.
    goto :finish
)
:: El ciclo termino OK y dejo la auditoria P2 POST pendiente: si P3 no llega a hacerla, :finish la hace de respaldo.
set "DEFERRED_POST=1"
goto :phase_b

:phase_a_skip
call "%COMMON%" :state_query last-result LAST_RES
echo --- FASE A omitida ^(--only-p3^); resultado del ultimo ciclo: !LAST_RES! >> "%LOG%"
if /i not "!LAST_RES!"=="OK" (
    echo [ERROR] --only-p3 exige que el ultimo ciclo P1+P2 haya terminado OK ^(resultado: !LAST_RES!^).
    echo         Ejecutar el ciclo completo o reanudarlo con P1P2_runCycle.bat --from N.
    set "RC=%RC_REFUSED%"
    goto :finish
)

:: ============================================================
:: FASE B: puerta de betas (bloqueante)
:: ============================================================
:phase_b
echo.
echo ===================================================
echo B. Puerta de betas macro ^(beta-shift audit^)
echo ===================================================
call "%COMMON%" :state_query calc-version CALC_VER
if not defined CALC_VER (
    echo [ERROR] no se pudo leer CALC_VERSION de proyecto2\src\pipeline\run_pipeline.py
    set "RC=%RC_ENV%"
    goto :finish
)
if defined OPT_baseline (
    set "BETA_CSV=!OPT_baseline!"
) else (
    call "%COMMON%" :state_query baseline BASELINE_ID
    set "BETA_CSV=%AUDIT_P2_DIR%\macro_betas_!BASELINE_ID!.csv"
)
if not exist "!BETA_CSV!" (
    echo [ERROR] no hay snapshot de betas contra el que comparar: !BETA_CSV!
    echo         Pasar uno existente con --baseline CSV.
    set "RC=%RC_STATE%"
    goto :finish
)
echo --- FASE B: betas contra !BETA_CSV! ^(CALC_VERSION !CALC_VER!^) >> "%LOG%"
call "%COMMON%" :telemetry step-begin BETA_GATE
"%PYTHON%" -X utf8 scripts\audit\beta_shift_audit.py --compare "!BETA_CSV!" --version !CALC_VER! --out "%AUDIT_P2_DIR%\beta_shift_outliers_gate_!STAMP!.csv"
set "BG_RC=!ERRORLEVEL!"
call "%COMMON%" :telemetry step-end BETA_GATE !BG_RC!
if !BG_RC! NEQ 0 (
    set "RC=!BG_RC!"
    set "TELEM_STEP=BETA_GATE"
    echo [ERROR] la puerta de betas fallo ^(RC=!RC!^): no se construye P3.
    echo --- FASE B fin: RC=!RC! >> "%LOG%"
    goto :finish
)
echo [OK] la puerta de betas paso (CALC_VERSION !CALC_VER!).
echo --- FASE B fin: RC=0 >> "%LOG%"

:: ============================================================
:: FASE C: P3
:: ============================================================
echo.
echo ===================================================
echo C. P3 ^(cartera e informe^)
echo ===================================================
set "P3_ARGS="
if defined OPT_scenario set "P3_ARGS=!OPT_scenario!"
if not defined BASELINE_ID call "%COMMON%" :state_query baseline BASELINE_ID
if defined BASELINE_ID set "P3_ARGS=!P3_ARGS! --audit-compare-to !BASELINE_ID!_p2"
if defined FLAG_p3_dry_run set "P3_ARGS=!P3_ARGS! --dry-run"
if defined FLAG_allow_stale set "P3_ARGS=!P3_ARGS! --allow-stale"
echo --- FASE C: P3_buildPortfolio.bat !P3_ARGS! >> "%LOG%"
call "%COMMON%" :telemetry step-begin P3_BUILD
call "%LAUNCH%\P3_buildPortfolio.bat" !P3_ARGS!
set "RC=!ERRORLEVEL!"
call "%COMMON%" :telemetry step-end P3_BUILD !RC!
echo --- FASE C fin build: RC=!RC! >> "%LOG%"
if !RC! NEQ 0 (
    set "TELEM_STEP=P3_BUILD"
    goto :finish
)
if not defined FLAG_p3_dry_run set "POST_DONE=1"

if defined FLAG_p3_dry_run (
    echo Informe omitido: --p3-dry-run no persiste nada.
    goto :finish
)
call "%COMMON%" :telemetry step-begin P3_REPORT
if defined OPT_report_dir (
    call "%LAUNCH%\P3_generateReport.bat" --draft "!OPT_report_dir!"
) else (
    call "%LAUNCH%\P3_generateReport.bat" --draft
)
set "RC=!ERRORLEVEL!"
call "%COMMON%" :telemetry step-end P3_REPORT !RC!
echo --- FASE C fin informe: RC=!RC! >> "%LOG%"
if !RC! NEQ 0 (
    set "TELEM_STEP=P3_REPORT"
    goto :finish
)

:: ============================================================
:: FASE D: confirmaciones (push y prueba del loader); --no-prompts las omite
:: ============================================================
if defined FLAG_no_prompts goto :finish
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

echo.
echo ===================================================
echo D. Test FND-0088 loader
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
:: Auditoria P2 POST pendiente (el ciclo la difirio y P3 no la hizo: fallo antes, --p3-dry-run): de respaldo, informativa.
if defined DEFERRED_POST if not defined POST_DONE call :post_audit_fallback
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
call "%COMMON%" :log_close "P1+P2+P3" !RC!
echo.
echo ===================================================
if "!RC!"=="0" (
    echo [OK] P1+P2+P3 finalizado con exito.
) else (
    echo [ERROR] P1+P2+P3 interrumpido ^(RC=!RC!^).
)
echo   Log: %LOG%
echo ===================================================
if not defined FLAG_no_pause pause
exit /b !RC!


:: ------------------------------------------------------------
:: :post_audit_fallback -- auditoria P2 POST contra el baseline del ciclo cuando P3 no la hizo. Nunca cambia RC.
:: ------------------------------------------------------------
:post_audit_fallback
if not defined BASELINE_ID call "%COMMON%" :state_query baseline BASELINE_ID
if not defined BASELINE_ID exit /b 0
call "%COMMON%" :get_time FB_STAMP yyyyMMdd_HHmmss
echo [AUDITORIA] P3 no hizo la auditoria P2 POST: auditoria de respaldo post_!FB_STAMP!_p2 vs !BASELINE_ID!_p2
echo --- Auditoria P2 POST de respaldo: post_!FB_STAMP!_p2 vs !BASELINE_ID!_p2 >> "%LOG%"
call "%PHASES%" :audit_p2 post_!FB_STAMP!_p2 !BASELINE_ID!_p2
echo --- Auditoria P2 POST de respaldo fin: RC=!AUD_RC! >> "%LOG%"
exit /b 0

:: ------------------------------------------------------------
:: Salidas de error de argumentos / ayuda
:: ------------------------------------------------------------
:usage
call :usage_text
call "%COMMON%" :utf8_off
endlocal & exit /b %RC_USAGE%

:help
call :usage_text
echo.
echo Opciones del ciclo P1+P2 ^(se reenvian a P1P2_runCycle.bat^):
call "%LAUNCH%\P1P2_runCycle.bat" --help
call "%COMMON%" :utf8_off
endlocal & exit /b 0

:usage_text
echo Uso: P1P2P3_runCycle.bat [opciones del ciclo P1+P2] [opciones de P3] [-- argumentos_para_run_pipeline]
echo   --only-p3          solo puerta de betas + P3, sobre el ultimo ciclo si termino OK
echo   --baseline CSV     puerta de betas contra este snapshot (por defecto, el del ciclo)
echo   --scenario ID      scenario_id de la cartera (por defecto autogenerado)
echo   --report-dir DIR   directorio de salida del informe
echo   --p3-dry-run       P3 no persiste (sin informe ni push)
echo   --allow-stale      salta la puerta de frescura de P3
echo   --no-prompts       sin confirmaciones de push ni de la prueba del loader
echo   --no-pause         sin pausa final
echo   -h, --help         esta ayuda (con las opciones del ciclo)
exit /b 0
