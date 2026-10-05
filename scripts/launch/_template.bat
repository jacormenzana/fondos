@echo off
setlocal EnableExtensions EnableDelayedExpansion
call "%~dp0lib\common.bat" :init || (endlocal & exit /b 101)
call "%COMMON%" :utf8_on

:: ============================================================
:: NOMBRE_DEL_LANZADOR.bat -- UNA linea: que hace y para quien.
::
:: PLANTILLA de lanzador nuevo (norma: doc/reglas/NORMAS_BATCH.md). Copiar a scripts\launch\,
:: renombrar y sustituir lo marcado con TODO. Esta copia tambien cumple la norma (la comprueba
:: tests/test_batch_standards.py), asi que sirve de ejemplo ejecutable:
::   - La linea 3 (init) es identica en todos los lanzadores de scripts\launch.
::
:: Uso:
::   NOMBRE.bat [--dry-run] [--n N]
::   --dry-run   TODO: que cambia
::   --n N       TODO: entero positivo
::   -h, --help  esta ayuda
:: Codigos de salida: 0 OK | 100 argumentos invalidos | 101 interprete no encontrado |
::   1-99 = RC propagado de la herramienta que falle (nunca se reinterpreta).
:: ============================================================

:: -- Configuracion: ninguna ruta del repo ni del interprete; rutas de DATOS solo aqui, en un set --
set "LOG_DIR=%ROOT%\proyecto1\log"
set "BOOL_OPTS=--dry-run"
set "UINT_OPTS=--n"

:: -- Argumentos: tabla de opciones, validacion y rechazo de lo desconocido ---------
for /f "delims==" %%V in ('set FLAG_ 2^>nul') do set "%%V="
for /f "delims==" %%V in ('set OPT_ 2^>nul') do set "%%V="
:parse
if "%~1"=="" goto :parsed
for %%O in (%BOOL_OPTS%) do if /i "%~1"=="%%O" goto :parse_bool
for %%O in (%UINT_OPTS%) do if /i "%~1"=="%%O" goto :parse_uint
if /i "%~1"=="-h"     goto :help
if /i "%~1"=="--help" goto :help
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
:parsed

:: -- Ejecucion ----------------------------------------------------------------
call "%COMMON%" :log_open "%LOG_DIR%" template "Plantilla"
echo [%STAMP%] Plantilla iniciada
echo   Log: %LOG%

:: Un proceso largo evita la suspension; el valor real se guarda y se restaura (owner = el
:: lanzador mas externo; los que este invoque no la tocan). Quitar si el proceso es corto.
call "%COMMON%" :standby_disable owner

pushd "%ROOT%"
"%PYTHON%" -u -X utf8 -c "print('TODO: llamar a la herramienta real')" >> "%LOG%" 2>> "%ERR%"
set "RC=!ERRORLEVEL!"
popd

call "%COMMON%" :standby_restore
call "%COMMON%" :log_close "Plantilla" !RC!
echo [%STAMP2%] Plantilla terminada (RC=!RC!)
if !RC! NEQ 0 echo   Revisar: %ERR%
echo.

call "%COMMON%" :utf8_off
:: Una SOLA linea: endlocal descarta la expansion retardada antes de que !RC! pudiera expandirse;
:: %RC% se sustituye al parsear, con el ambito aun activo.
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
echo Uso: NOMBRE.bat [--dry-run] [--n N]
echo   --dry-run   TODO
echo   --n N       TODO
echo   -h, --help  esta ayuda
exit /b 0
