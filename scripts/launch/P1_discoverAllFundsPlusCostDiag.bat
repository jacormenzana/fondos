@echo off
setlocal EnableExtensions EnableDelayedExpansion
call "%~dp0lib\common.bat" :init "%~nx0" & set "RC_BOOT=!ERRORLEVEL!"
if %RC_BOOT% NEQ 0 (endlocal & exit /b %RC_BOOT%)
call "%COMMON%" :utf8_on

:: ============================================================
:: P1_discoverAllFundsPlusCostDiag.bat -- Pipeline P1 completo + diagnostico de costes
::
:: Orquesta, sin duplicar logica, dos lanzadores que ya existen:
::   1. P1_discoverAllFunds.bat   pasada nature-first sobre el catalogo del harvest, constructor de familias,
::                                refresco de los atributos derivados, export Excel y auditoria P1
::   2. P1_diagCost.bat           diagnostico de extraccion de costes (solo lectura sobre los PDF)
:: Los dos pasos se ejecutan siempre; el codigo de salida es el del primer paso que falle.
:: No existe ninguna ruta por bloques ni lee el Excel maestro: la clasificacion es nature-first.
::
:: Uso:
::   P1_discoverAllFundsPlusCostDiag.bat [--no-audit] [--no-export]
::   --no-audit    se reenvia a P1_discoverAllFunds.bat (sin auditoria P1)
::   --no-export   se reenvia a P1_discoverAllFunds.bat (sin export Excel)
::   -h, --help    esta ayuda
:: Cualquier otra opcion la rechaza P1_discoverAllFunds.bat (RC 100) antes de ejecutar nada.
:: Codigos de salida: 0 OK | RC propagado de la primera fase que falle (P1_discoverAllFunds.bat y
::   P1_diagCost.bat propagan los suyos tal cual).
:: ============================================================

:: -h / --help: the usage (this header) and out, BEFORE anything runs (NORMAS_BATCH.md section 3).
for %%A in (%*) do (
    if /i "%%~A"=="-h" goto :show_help
    if /i "%%~A"=="--help" goto :show_help
)

set "FWD_ARGS=%*"

echo [%time%] Paso 1/2: P1_discoverAllFunds.bat !FWD_ARGS!
call "%LAUNCH%\P1_discoverAllFunds.bat" !FWD_ARGS!
set "RC_P1=!ERRORLEVEL!"

echo [%time%] Paso 2/2: P1_diagCost.bat
call "%LAUNCH%\P1_diagCost.bat"
set "RC_DIAG=!ERRORLEVEL!"

set "FINAL_RC=!RC_P1!"
if !FINAL_RC! EQU 0 set "FINAL_RC=!RC_DIAG!"
echo.
echo [%time%] Fin: P1_discoverAllFunds=!RC_P1! P1_diagCost=!RC_DIAG!

:: endlocal discards delayed expansion before !VAR! on the next line could
:: expand it -- chain on one line so %FINAL_RC% substitutes at parse time.
call "%COMMON%" :utf8_off
endlocal & exit /b %FINAL_RC%

:: ------------------------------------------------------------
:: :show_help -- usage = the header of this file (lib\batch_helpers.py usage); RC 0, nothing else runs.
:: ------------------------------------------------------------
:show_help
"%PYTHON%" "%LIB%\batch_helpers.py" usage "%~f0"
call "%COMMON%" :utf8_off
endlocal & exit /b 0
