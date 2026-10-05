@echo off
setlocal EnableExtensions EnableDelayedExpansion
call "%~dp0lib\common.bat" :init || (endlocal & exit /b 101)
call "%COMMON%" :utf8_on

:: ============================================================
:: P2_P3_complete.bat -- Recalculo P2 -> auditoria de betas -> P3 (cartera + informe)
::                      -> push opcional -> prueba opcional del loader de benchmarks
::
:: Lanzador de PROPIETARIO (P2 de poblacion completa, ~4 h). Se detiene en el primer paso que falla;
:: la auditoria de betas sale con RC != 0 si hay NULL/NaN, betas implausibles (|beta| > umbral) o un
:: P2 sin terminar, de modo que un recalculo defectuoso nunca llega a P3.
::
:: Ningun valor de version o de baseline esta escrito aqui (norma: NORMAS_BATCH.md):
::   - CALC_VERSION se lee de proyecto2\src\pipeline\run_pipeline.py (p1p2_state.py calc-version).
::   - El baseline de betas se toma AHORA, antes del recalculo (macro_betas_p2p3_<STAMP>.csv en
::     C:\data\fondos\audit; variable P1P2_AUDIT_DIR para cambiarlo), de modo que la comparacion
::     mide exactamente lo que este recalculo cambia. --baseline CSV usa uno existente (p. ej. el
::     macro_betas_pre_*.csv que P1_P2_Complete.bat guarda al empezar un ciclo).
::   - El numero de commits pendientes de push se cuenta en el momento (git).
:: Comparte el bloqueo de instancia unica con P1_P2_Complete.bat (ambos recalculan P2): mientras
:: uno corre, el otro se rechaza con RC 105. La suspension se gestiona como en aquel.
::
:: Uso:
::   P2_P3_complete.bat                    ciclo completo
::   P2_P3_complete.bat --baseline CSV     comparar contra un snapshot de betas existente
::   P2_P3_complete.bat --no-pause         sin pausa final (ejecucion programada)
::   P2_P3_complete.bat -h                 esta ayuda
:: Codigos de salida: 0 OK | 100 argumentos invalidos | 101 interprete no encontrado |
::   105 otra instancia en ejecucion | 1-99 = RC propagado del paso que fallo.
:: ============================================================

set "AUDIT_DIR=C:\data\fondos\audit"
if defined P1P2_AUDIT_DIR set "AUDIT_DIR=%P1P2_AUDIT_DIR%"
set "LOCK_FILE=%STATE_DIR%\fondos_cycle.lock"

set "BASELINE="
set "NO_PAUSE="

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
if /i "%~1"=="-h"     goto :help
if /i "%~1"=="--help" goto :help
echo [ERROR] Argumento desconocido: %~1
goto :usage
:parsed

if defined BASELINE if not exist "!BASELINE!" (
    echo [ERROR] el baseline indicado no existe: !BASELINE!
    call "%COMMON%" :utf8_off
    endlocal & exit /b %RC_USAGE%
)

:: Instancia unica (ver P1_P2_Complete.bat): el manejador 9 vive mientras corre :cycle.
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
if not exist "%AUDIT_DIR%" mkdir "%AUDIT_DIR%"
call "%COMMON%" :standby_disable owner

:: -- CALC_VERSION vigente (nunca escrita a mano) ----------------------------------
call "%COMMON%" :state_query calc-version CALC_VER
if not defined CALC_VER (
    echo [ERROR] no se pudo leer CALC_VERSION de proyecto2\src\pipeline\run_pipeline.py
    set "RC=%RC_ENV%"
    goto :finish
)

:: -- 1. Baseline de betas (antes de recalcular) -----------------------------------
echo ===================================================
echo 1. Baseline de betas macro (antes del recalculo P2)
echo ===================================================
if defined BASELINE (
    echo Usando el baseline indicado: !BASELINE!
) else (
    set "BASELINE=%AUDIT_DIR%\macro_betas_p2p3_!STAMP!.csv"
    "%PYTHON%" -X utf8 scripts\audit\beta_shift_audit.py --snapshot "!BASELINE!"
    if errorlevel 1 (
        echo [ERROR] no se pudo tomar el baseline de betas.
        set "RC=!ERRORLEVEL!"
        goto :finish
    )
)

:: -- 2. Recalculo P2 -------------------------------------------------------------
echo.
echo ===================================================
echo 2. Ejecutando recalculo P2 (CALC_VERSION !CALC_VER!)
echo ===================================================
call "%LAUNCH%\P2_calculateIndicators.bat"
if errorlevel 1 (
    set "RC=!ERRORLEVEL!"
    goto :finish
)

:: -- 3. Auditoria de betas -------------------------------------------------------
echo.
echo ===================================================
echo 3. Beta-shift audit (CALC_VERSION !CALC_VER!)
echo ===================================================
"%PYTHON%" -X utf8 scripts\audit\beta_shift_audit.py --compare "!BASELINE!" --version !CALC_VER! --out "%AUDIT_DIR%\beta_shift_outliers_!STAMP!.csv"
if errorlevel 1 (
    echo [ERROR] Beta-shift audit fallo con codigo !ERRORLEVEL!.
    set "RC=!ERRORLEVEL!"
    goto :finish
)
echo [OK] Beta-shift audit paso correctamente.

:: -- 4. P3 -----------------------------------------------------------------------
echo.
echo ===================================================
echo 4. Ejecutando P3 (Build Portfolio y Generate Report)
echo ===================================================
call "%LAUNCH%\P3_buildPortfolio.bat"
if errorlevel 1 (
    set "RC=!ERRORLEVEL!"
    goto :finish
)
call "%LAUNCH%\P3_generateReport.bat"
if errorlevel 1 (
    set "RC=!ERRORLEVEL!"
    goto :finish
)

:: -- 5. Push opcional (siempre con confirmacion) -----------------------------------
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

:usage_text
echo Uso: P2_P3_complete.bat [--baseline CSV] [--no-pause]
echo   --baseline CSV   comparar las betas contra un snapshot existente (por defecto se toma uno antes del recalculo)
echo   --no-pause       sin pausa final
echo   -h, --help       esta ayuda
exit /b 0
