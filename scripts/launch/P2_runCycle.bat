@echo off
setlocal EnableExtensions EnableDelayedExpansion
call "%~dp0lib\common.bat" :init "%~nx0" & set "RC_BOOT=!ERRORLEVEL!"
if %RC_BOOT% NEQ 0 (endlocal & exit /b %RC_BOOT%)
call "%COMMON%" :utf8_on

:: ============================================================
:: P2_runCycle.bat -- Ciclo P2 autonomo con auditoria PRE y POST (lanzador de PROPIETARIO; norma: doc/reglas/NORMAS_BATCH.md)
::
:: Es el subconjunto P2 del ciclo maestro (P1P2P3_runCycle.bat), sin P1 ni P3 (P2 de poblacion completa: ~2 h):
::   0. preflight                   p1p2_state.py preflight (BD alcanzable, DSN owner, disco)
::   1. auditoria P2 PRE            P2_audit --persist --run-id pre_<sello>_p2. Si no es valida (fallo o INCOMPLETA) no se
::                                  toca nada: RC 107 (--allow-no-baseline lo avisa y sigue)
::   2. snapshot de betas macro     beta_shift_audit.py --snapshot (out\audit\P2; P1P2_AUDIT_DIR cambia la raiz out\audit)
::   3. macro + NAV                 P2_discoverLoadMetrics.bat
::   4. indicadores P2              P2_calculateIndicators.bat
::   5. comparacion de betas        beta_shift_audit.py --compare con la CALC_VERSION vigente (informativa: sin P3 no bloquea)
::   6. auditoria P2 POST           P2_audit --persist --run-id post_<sello>_p2 --compare-to pre_<sello>_p2 (informativa)
:: Se detiene en el primer paso que falla y devuelve su codigo. NO escribe el estado del ciclo P1+P2. Comparte el bloqueo de
:: instancia unica. Si va a construirse P3 a continuacion usar P2P3_runCycle.bat: hace UNA sola auditoria POST, tras el DRAFT.
::
:: Uso:  P2_runCycle.bat [opciones] [-- argumentos_para_run_pipeline]
::   --skip-preflight         no ejecutar el preflight
::   --allow-no-baseline      si la auditoria PRE no es valida: avisar y seguir (por defecto RC 107)
::   --skip-macro             solo NAV, sin carga macro
::   --workers N              N hilos en el NAV load
::   --force                  recomputo total de P2 (cache hash + gate OLS)
::   --no-export              sin export Excel de P2
::   -- ...                   argumentos reenviados a run_pipeline
::   -h, --help               esta ayuda
:: Codigos de salida: 0 OK | 100 argumentos invalidos | 101 interprete no encontrado | 104 preflight fallido |
::   105 otra instancia en ejecucion | 106 driver PostgreSQL bloqueado | 107 baseline (auditoria PRE) no valido |
::   1-99 = RC propagado del paso que fallo.
:: ============================================================

:: -- Configuracion: rutas de DATOS solo aqui ---------------------------------------
set "LOG_DIR=%ROOT%\proyecto2\log"
set "LOCK_FILE=%STATE_DIR%\fondos_cycle.lock"
set "BOOL_OPTS=--skip-preflight --allow-no-baseline --skip-macro --force --no-export"
set "UINT_OPTS=--workers"
set "P2_EXTRA="

:: -- Argumentos ------------------------------------------------------------------------
for /f "delims==" %%V in ('set FLAG_ 2^>nul') do set "%%V="
for /f "delims==" %%V in ('set OPT_ 2^>nul') do set "%%V="
:parse
if "%~1"=="" goto :parsed
for %%O in (%BOOL_OPTS%) do if /i "%~1"=="%%O" goto :parse_bool
for %%O in (%UINT_OPTS%) do if /i "%~1"=="%%O" goto :parse_uint
if /i "%~1"=="-h"     goto :help
if /i "%~1"=="--help" goto :help
if "%~1"=="--" (
    shift
    goto :collect_p2_args
)
echo [ERROR] Argumento desconocido: %~1
goto :usage

:parse_bool
set "OPT=%~1"
set "OPT=!OPT:--=!"
set "OPT=!OPT:-=_!"
set "FLAG_!OPT!=1"
shift
goto :parse

:parse_uint
call "%COMMON%" :is_uint "%~2"
if errorlevel 1 (
    echo [ERROR] %~1 requiere un entero positivo: "%~2"
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
:parsed

set "ALLOW_NO_BASELINE="
if defined FLAG_allow_no_baseline set "ALLOW_NO_BASELINE=1"
set "S3_ARGS="
if defined FLAG_skip_macro set "S3_ARGS=--skip-macro"
if defined OPT_workers set "S3_ARGS=!S3_ARGS! --workers !OPT_workers!"
set "S4_ARGS="
if defined FLAG_force set "S4_ARGS=--force"
if defined FLAG_no_export set "S4_ARGS=!S4_ARGS! --no-export"
set "S4_ARGS=!S4_ARGS!!P2_EXTRA!"

:: -- Instancia unica (ver P1P2_runCycle.bat) --------------------------------------------
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
call "%COMMON%" :log_open "%LOG_DIR%" P2_runCycle "P2 runCycle"
echo [%STAMP%] P2 runCycle iniciado
echo   Log: %LOG%
set FLAG_ >> "%LOG%" 2>nul
set OPT_ >> "%LOG%" 2>nul

:: -- 0. Preflight: un fallo no toca nada --------------------------------------
set "PRE_RC=0"
if defined FLAG_skip_preflight (
    echo --- PREFLIGHT: omitido ^(--skip-preflight^) >> "%LOG%"
) else (
    call "%PHASES%" :preflight "%LOG_DIR%\log_P2_runCycle_preflight_%STAMP%.log"
)
if !PRE_RC! NEQ 0 (
    echo [ERROR] Preflight fallido ^(RC=!PRE_RC!^). No se ejecuta ningun paso.
    set "RC=!PRE_RC!"
    goto :finish
)

call "%COMMON%" :standby_disable owner

:: -- CALC_VERSION vigente (nunca escrita a mano) --------------------------------------
call "%COMMON%" :state_query calc-version CALC_VER
if not defined CALC_VER (
    echo [ERROR] no se pudo leer CALC_VERSION de proyecto2\src\pipeline\run_pipeline.py
    set "RC=%RC_ENV%"
    goto :finish
)

:: -- 1. Auditoria P2 PRE (baseline congelado) ---------------------------------
set "PRE_ID=pre_%STAMP%"
echo [AUDITORIA] P2 PRE !PRE_ID!_p2
echo --- AUDITORIA P2 PRE: !PRE_ID!_p2 >> "%LOG%"
call "%PHASES%" :audit_p2 !PRE_ID!_p2
set "PRE_AUD_RC=!AUD_RC!"
echo --- AUDITORIA P2 PRE fin: RC=!PRE_AUD_RC! >> "%LOG%"
call "%PHASES%" :baseline_verdict 0 !PRE_AUD_RC!
if !BASELINE_RC! NEQ 0 (
    echo [ERROR] la auditoria PRE no es valida ^(RC=!BASELINE_RC!^): no se ejecuta ningun paso. Repetir, o --allow-no-baseline.
    set "RC=!BASELINE_RC!"
    goto :finish
)

:: -- 2. Snapshot de betas macro --------------------------------------------------------
if not exist "%AUDIT_P2_DIR%" mkdir "%AUDIT_P2_DIR%"
set "BETA_CSV=%AUDIT_P2_DIR%\macro_betas_p2_%STAMP%.csv"
echo --- snapshot de betas: !BETA_CSV! >> "%LOG%"
"%PYTHON%" -X utf8 scripts\audit\beta_shift_audit.py --snapshot "!BETA_CSV!" >> "%LOG%" 2>&1
set "BS_RC=!ERRORLEVEL!"
if !BS_RC! NEQ 0 echo [WARN] el snapshot de betas fallo ^(RC=!BS_RC!^): no habra comparacion de betas >> "%LOG%"

:: -- 3. Macro + NAV ----------------------------------------------------------------------
echo [PASO 3] P2_discoverLoadMetrics
echo --- PASO 3: P2_discoverLoadMetrics !S3_ARGS! >> "%LOG%"
call "%LAUNCH%\P2_discoverLoadMetrics.bat" !S3_ARGS!
set "RC=!ERRORLEVEL!"
echo --- PASO 3 fin: RC=!RC! >> "%LOG%"
if !RC! NEQ 0 (
    echo [ERROR] PASO 3 fallo ^(RC=!RC!^). Ciclo abortado.
    goto :finish
)

:: -- 4. Indicadores P2 ---------------------------------------------------------------------
echo [PASO 4] P2_calculateIndicators
echo --- PASO 4: P2_calculateIndicators !S4_ARGS! >> "%LOG%"
call "%LAUNCH%\P2_calculateIndicators.bat" !S4_ARGS!
set "RC=!ERRORLEVEL!"
echo --- PASO 4 fin: RC=!RC! >> "%LOG%"
if !RC! NEQ 0 (
    echo [ERROR] PASO 4 fallo ^(RC=!RC!^). Ciclo abortado.
    goto :finish
)

:: -- 5. Comparacion de betas (informativa) --------------------------------------------------
if exist "!BETA_CSV!" (
    "%PYTHON%" -X utf8 scripts\audit\beta_shift_audit.py --compare "!BETA_CSV!" --version !CALC_VER! --out "%AUDIT_P2_DIR%\beta_shift_outliers_p2_%STAMP%.csv" >> "%LOG%" 2>&1
    set "BC_RC=!ERRORLEVEL!"
    echo --- comparacion de betas fin: RC=!BC_RC! >> "%LOG%"
    if !BC_RC! NEQ 0 echo [WARN] la comparacion de betas dio hallazgos ^(RC=!BC_RC!^): ver el log
) else (
    echo [WARN] sin snapshot de betas previo: comparacion omitida >> "%LOG%"
)

:: -- 6. Auditoria P2 POST: informativa -------------------------------------------------------
call "%COMMON%" :get_time POST_STAMP yyyyMMdd_HHmmss
echo [AUDITORIA] P2 POST post_!POST_STAMP!_p2 vs !PRE_ID!_p2
echo --- AUDITORIA P2 POST: post_!POST_STAMP!_p2 --compare-to !PRE_ID!_p2 >> "%LOG%"
call "%PHASES%" :audit_p2 post_!POST_STAMP!_p2 !PRE_ID!_p2
echo --- AUDITORIA P2 POST fin: RC=!AUD_RC! >> "%LOG%"
if !AUD_RC! NEQ 0 echo [WARN] la auditoria P2 POST no termino bien ^(RC=!AUD_RC!^): no hay deriva objetiva

:finish
call "%COMMON%" :standby_restore
call "%COMMON%" :log_close "P2 runCycle" !RC!
echo.
if "!RC!"=="0" (
    echo [%STAMP2%] P2 runCycle -- Fin OK
) else (
    echo [%STAMP2%] P2 runCycle -- Fin ERROR ^(RC=!RC!^)
)
echo   Log: %LOG%
echo.
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

:usage_text
echo Uso: P2_runCycle.bat [opciones] [-- argumentos_para_run_pipeline]
echo   --skip-preflight         no ejecutar el preflight
echo   --allow-no-baseline      si la auditoria PRE no es valida: avisar y seguir (por defecto RC 107)
echo   --skip-macro             solo NAV, sin carga macro
echo   --workers N              N hilos en el NAV load
echo   --force                  recomputo total de P2 (cache hash + gate OLS)
echo   --no-export              sin export Excel de P2
echo   -- ...                   argumentos reenviados a run_pipeline
echo   -h, --help               esta ayuda
exit /b 0
