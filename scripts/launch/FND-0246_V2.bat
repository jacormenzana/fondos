@echo off
setlocal EnableExtensions EnableDelayedExpansion
call "%~dp0lib\common.bat" :init "%~nx0" & set "RC_BOOT=!ERRORLEVEL!"
if %RC_BOOT% NEQ 0 (endlocal & exit /b %RC_BOOT%)
call "%COMMON%" :utf8_on

:: ============================================================
:: FND-0246_V2.bat -- Release EUR (FND-0243/0235/0240/0241/0242): comprobacion de la configuracion -> P2 -> P3.
::
:: Tramo final de la release, desatendido. 1) release_check.py: los interruptores de la release estan a True en
:: shared/config.py y son coherentes (se leen del CODIGO; un "set X=1" en un .bat no los cambia). 2) P2P3_runCycle.bat
:: --no-prompts: baseline de betas -> P2_calculateIndicators (sin --force: el cambio de interruptores y de CALC_VERSION ya
:: obliga al recalculo completo) -> auditoria de betas contra CALC_VERSION -> P3 cartera -> P3 informe; sin preguntas.
:: El bloqueo de instancia unica y la suspension los gestiona P2P3_runCycle.bat (o FND-0246.bat si lo invoca).
:: Ejecutable desde cualquier directorio (las rutas salen de la ubicacion del propio fichero).
::
:: Uso:
::   FND-0246_V2.bat              comprobacion + P2 + P3
::   FND-0246_V2.bat --no-pause   sin pausa final
::   FND-0246_V2.bat -h           esta ayuda
:: Codigos de salida: 0 OK | 100 argumentos invalidos | 101 interprete no encontrado | 105 otra instancia en
::   ejecucion | 1-99 = RC propagado del paso que fallo (1 = la configuracion no es la de la release).
:: ============================================================

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

echo ===================================================
echo FND-0246_V2 1/2: configuracion de la release
echo ===================================================
pushd "%ROOT%"
"%PYTHON%" -X utf8 "%LAUNCH%\release_check.py"
set "RC=!ERRORLEVEL!"
popd
if !RC! NEQ 0 (
    echo [ERROR] La configuracion no es la de la release ^(RC=!RC!^): no se recalcula nada.
    goto :finish
)

echo.
echo ===================================================
echo FND-0246_V2 2/2: P2 -^> auditoria de betas -^> P3 ^(P2P3_runCycle.bat^)
echo ===================================================
call "%LAUNCH%\P2P3_runCycle.bat" --no-prompts !NO_PAUSE_ARG!
set "RC=!ERRORLEVEL!"

:finish
if "!RC!"=="0" (echo [OK] FND-0246_V2 terminado.) else (echo [ERROR] FND-0246_V2 interrumpido ^(RC=!RC!^).)
call "%COMMON%" :utf8_off
endlocal & exit /b %RC%

:usage
call :usage_text
call "%COMMON%" :utf8_off
endlocal & exit /b %RC_USAGE%

:help
call :usage_text
call "%COMMON%" :utf8_off
endlocal & exit /b 0

:usage_text
echo Uso: FND-0246_V2.bat [--no-pause]
echo   --no-pause   sin pausa final
echo   -h, --help   esta ayuda
exit /b 0
