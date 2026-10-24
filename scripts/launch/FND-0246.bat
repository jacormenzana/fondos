@echo off
setlocal EnableExtensions EnableDelayedExpansion
call "%~dp0lib\common.bat" :init "%~nx0" & set "RC_BOOT=!ERRORLEVEL!"
if %RC_BOOT% NEQ 0 (endlocal & exit /b %RC_BOOT%)
call "%COMMON%" :utf8_on

:: ============================================================
:: FND-0246.bat -- Release EUR completa (FND-0243/0235/0240/0241/0242), desatendida: P1 -> divisas -> NAV -> P2 -> P3.
::
::   0. release_check.py      los interruptores de la release estan a True en shared/config.py (falla en segundos,
::                            antes de P1, si no lo estan; un "set X=1" en un .bat no los cambia)
::   1. P1_discoverAllFunds   clasificacion; persiste las divisas de clase corregidas
::   2. verify_fund_currency  puerta: ninguna Fund_Currency pendiente (si no, P2 convertiria con la divisa vieja)
::   3. P2_discoverLoadMetrics macro + NAV (sale con error si el token de Morningstar no esta disponible)
::   4. FND-0246_V2.bat       comprobacion de nuevo + P2 -> auditoria de betas -> P3 (P2P3_runCycle --no-prompts)
:: Se detiene en el primer paso que falla y devuelve su codigo. Toma el bloqueo de instancia unica del ciclo
:: (como P1P2_runCycle / P2P3_runCycle) y lo hereda a lo que invoca; gestiona la suspension como propietario.
:: Ejecutable desde cualquier directorio (las rutas salen de la ubicacion del propio fichero).
::
:: Uso:
::   FND-0246.bat              la secuencia completa
::   FND-0246.bat --no-pause   sin pausa final de P2P3_runCycle
::   FND-0246.bat -h           esta ayuda
:: Codigos de salida: 0 OK | 100 argumentos invalidos | 101 interprete no encontrado | 105 otra instancia en
::   ejecucion | 1-99 = RC propagado del paso que fallo.
:: ============================================================

set "LOCK_FILE=%STATE_DIR%\fondos_cycle.lock"
set "NO_PAUSE_ARG="
:parse
if "%~1"=="" goto :parsed
if /i "%~1"=="--no-pause" (
    set "NO_PAUSE_ARG=--no-pause"
    shift
    goto :parse
)
if /i "%~1"=="-h"     goto :help
if /i "%~1"=="--help" goto :help
echo [ERROR] Argumento desconocido: %~1
goto :usage
:parsed

:: Instancia unica (NORMAS_BATCH.md 9): el manejador 9 vive mientras corre :cycle; anidado bajo otro ciclo no se pide.
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
:: :cycle -- la secuencia; solo se ejecuta con el bloqueo adquirido.
:: ############################################################
:cycle
set "LOCK_HELD=1"
set "FONDOS_CYCLE_LOCK=1"
set "RC=0"
cd /d "%ROOT%"
call "%COMMON%" :standby_disable owner

echo ===================================================
echo FND-0246 0/4: configuracion de la release
echo ===================================================
"%PYTHON%" -X utf8 "%LAUNCH%\release_check.py"
set "RC=!ERRORLEVEL!"
if !RC! NEQ 0 goto :cycle_end

echo.
echo ===================================================
echo FND-0246 1/4: P1_discoverAllFunds
echo ===================================================
call "%LAUNCH%\P1_discoverAllFunds.bat"
set "RC=!ERRORLEVEL!"
if !RC! NEQ 0 goto :cycle_end

echo.
echo ===================================================
echo FND-0246 2/4: puerta de divisas de clase ^(verify_fund_currency^)
echo ===================================================
"%PYTHON%" -X utf8 "%ROOT%\scripts\audit\verify_fund_currency.py"
set "RC=!ERRORLEVEL!"
if !RC! NEQ 0 goto :cycle_end

echo.
echo ===================================================
echo FND-0246 3/4: P2_discoverLoadMetrics ^(macro + NAV^)
echo ===================================================
call "%LAUNCH%\P2_discoverLoadMetrics.bat"
set "RC=!ERRORLEVEL!"
if !RC! NEQ 0 goto :cycle_end

echo.
echo ===================================================
echo FND-0246 4/4: FND-0246_V2 ^(P2 -^> betas -^> P3^)
echo ===================================================
call "%LAUNCH%\FND-0246_V2.bat" !NO_PAUSE_ARG!
set "RC=!ERRORLEVEL!"

:cycle_end
call "%COMMON%" :standby_restore
if "!RC!"=="0" (echo [OK] FND-0246 terminado.) else (echo [ERROR] FND-0246 interrumpido ^(RC=!RC!^).)
exit /b !RC!


:usage
call :usage_text
call "%COMMON%" :utf8_off
endlocal & exit /b %RC_USAGE%

:help
call :usage_text
call "%COMMON%" :utf8_off
endlocal & exit /b 0

:usage_text
echo Uso: FND-0246.bat [--no-pause]
echo   --no-pause   sin pausa final de P2P3_runCycle
echo   -h, --help   esta ayuda
exit /b 0
