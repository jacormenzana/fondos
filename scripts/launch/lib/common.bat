@echo off
:: ============================================================
:: lib\common.bat -- libreria de subrutinas compartidas por TODOS los lanzadores .bat
:: Norma: doc/reglas/NORMAS_BATCH.md (este fichero es su implementacion de referencia).
::
:: Uso (el llamante ya tiene `setlocal EnableExtensions EnableDelayedExpansion`):
::   call "%~dp0lib\common.bat" :init "%~nx0" & set "RC_BOOT=!ERRORLEVEL!"   <- UNA vez, al principio
::   if %RC_BOOT% NEQ 0 (endlocal & exit /b %RC_BOOT%)                       <- y salir con el RC de :init (101 / 106)
::   call "%COMMON%" :etiqueta [argumentos]                                  <- despues, en cualquier sitio
::
:: Contrato de TODAS las subrutinas:
::   - No llaman a setlocal: las variables que fijan quedan visibles para el llamante.
::   - Devuelven su resultado por errorlevel (exit /b N) o en una variable documentada;
::     nunca terminan el proceso.
::   - Los argumentos empiezan en %1 (la etiqueta se consume aqui, en el despachador).
::
:: Subrutinas:
::   :init NOMBRE               ROOT LIB COMMON LAUNCH STATE_DIR PYTHON y las constantes RC_*; comprueba interprete y driver PostgreSQL
::   :get_time VAR FORMATO      hora actual con formato .NET en VAR (p. ej. yyyyMMdd_HHmmss)
::   :tail FICHERO N            ultimas N lineas de un fichero
::   :is_uint VALOR             RC 0 si es un entero positivo
::   :utf8_on / :utf8_off       pagina de codigos 65001 y restauracion de la original
::   :standby_disable [owner]   suspension AC a 0, guardando el valor REAL; solo el lanzador mas externo la gestiona
::   :standby_restore            restaura el valor guardado si este lanzador la desactivo (tambien tras una interrupcion)
::   :state_query SUB VAR        salida de `p1p2_state.py SUB` en VAR (baseline, calc-version, last-result...)
::   :log_open DIR TAG TITULO   fija STAMP, LOG, ERR y escribe la cabecera del log
::   :log_close TITULO RC       fija STAMP2 y escribe el pie del log
::   :telemetry SUBCMD [args]   telemetria de ciclo (shared/cycle_telemetry.py); nada sin FONDOS_CYCLE_ID
:: ============================================================
:: `shift` desplaza tambien %0: la ubicacion de la libreria se captura ANTES de consumir la etiqueta.
set "_HERE=%~dp0"
set "_LBL=%~1"
shift
if not defined _LBL (
    echo [common.bat] falta el nombre de la subrutina
    exit /b 1
)
goto %_LBL%

:: ------------------------------------------------------------
:: :init -- entorno comun. Ninguna ruta del repo ni del interprete se escribe fuera de este fichero.
::   ROOT        raiz del repo, calculada desde la ubicacion de esta libreria (funciona si se mueve)
::   PYTHON      interprete del entorno `des` (FONDOS_PYTHON lo cambia, p. ej. en las pruebas)
::   WINFIND     find.exe de Windows por ruta: lanzado desde Git Bash, `find` a secas es el GNU y recorre el disco
::   POWERCFG    ejecutable de energia (FONDOS_POWERCFG lo sustituye en las pruebas, para no tocar el real)
::   STATE_DIR   donde viven el estado, el bloqueo y el valor guardado de la suspension
::   RC_*        codigos de salida PROPIOS de un lanzador (rango 100-199, ver la norma): los de las
::               herramientas que invoca (1-99) se propagan sin tocar y nunca pueden coincidir.
::   RC 101 si no existe el interprete. RC 106 (RC_ENV_BLOCKED) si el driver PostgreSQL (psycopg) no carga:
::   el control de aplicaciones de Windows puede bloquear su DLL y toda herramienta de BD moriria a mitad de
::   ejecucion con un traceback ajeno. NOMBRE (%~nx0 del lanzador) solo se usa para el registro local
::   STATE_DIR\env_blocked.log (tope 1 MB). El control cuesta una vez por arbol de procesos
::   (FONDOS_DB_DRIVER_OK la exporta el lanzador mas externo) y se recuerda 10 minutos entre ejecuciones
::   (marcador STATE_DIR\env_driver_ok); un entorno bloqueado nunca se recuerda. Logica en shared/env_guard.py.
:: ------------------------------------------------------------
:init
for %%I in ("%_HERE%.") do set "LIB=%%~fI"
for %%I in ("%_HERE%..\..\..") do set "ROOT=%%~fI"
set "COMMON=%LIB%\common.bat"
set "LAUNCH=%ROOT%\scripts\launch"
set "STATE_DIR=%ROOT%\proyecto1\log"
set "STANDBY_FILE=%STATE_DIR%\standby_ac.saved"
set "PYTHON=C:\data\envs\des\python.exe"
if defined FONDOS_PYTHON set "PYTHON=%FONDOS_PYTHON%"
set "WINFIND=%SystemRoot%\System32\find.exe"
set "POWERCFG=powercfg"
if defined FONDOS_POWERCFG set "POWERCFG=%FONDOS_POWERCFG%"
set "RC_USAGE=100"
set "RC_ENV=101"
set "RC_REFUSED=102"
set "RC_STATE=103"
set "RC_PREFLIGHT=104"
set "RC_BUSY=105"
set "RC_ENV_BLOCKED=106"
if not exist "%PYTHON%" (
    echo [ERROR] Interprete Python no encontrado: %PYTHON%
    exit /b 101
)
if defined FONDOS_DB_DRIVER_OK exit /b 0
"%PYTHON%" "%LIB%\batch_helpers.py" driver-check "%STATE_DIR%" "%~1"
if errorlevel 107 exit /b 101
if errorlevel 106 exit /b 106
if errorlevel 1 exit /b 101
set "FONDOS_DB_DRIVER_OK=1"
exit /b 0

:: ------------------------------------------------------------
:: :get_time VAR FORMATO -- hora actual con formato .NET (yyyy MM dd HH mm ss y separadores). La calcula
::   Python (lib\batch_helpers.py): independiente del idioma (%time% no lo es), sin wmic (ya no existe) y
::   sin PowerShell (0,3 s por llamada y, cuando se cuelga, cuelga, cuelgan con ella todos los lanzadores).
:: ------------------------------------------------------------
:get_time
for /f %%a in ('""%PYTHON%" "%LIB%\batch_helpers.py" time %~2"') do set "%~1=%%a"
exit /b 0

:: ------------------------------------------------------------
:: :tail FICHERO N -- imprime las ultimas N lineas de FICHERO (nada si no existe).
:: ------------------------------------------------------------
:tail
"%PYTHON%" "%LIB%\batch_helpers.py" tail "%~1" %~2
exit /b 0

:: ------------------------------------------------------------
:: :is_uint VALOR -- RC 0 si VALOR es un entero positivo, RC 1 si no.
:: ------------------------------------------------------------
:is_uint
echo "%~1"| findstr /r /x "\"[1-9][0-9]*\"" > nul
exit /b %ERRORLEVEL%

:: ------------------------------------------------------------
:: :utf8_on / :utf8_off -- los logs redirigidos con >> pueden llevar caracteres no ASCII.
::   CP_PREV recuerda la pagina de codigos vigente al entrar (la de cada lanzador, no la original
::   de la consola), de modo que un sub-lanzador restaura la del orquestador que lo llama.
:: ------------------------------------------------------------
:utf8_on
for /f "tokens=2 delims=:." %%c in ('chcp') do set /a CP_PREV=%%c
chcp 65001 > nul
exit /b 0

:utf8_off
if defined CP_PREV chcp %CP_PREV% > nul
exit /b 0

:: ------------------------------------------------------------
:: :standby_disable [owner] / :standby_restore
::   Evita la suspension durante un proceso largo SIN asumir cual era el valor original (antes se
::   restauraba a un 30 escrito a mano). Se lee el valor real de la corriente alterna (en minutos,
::   lib\batch_helpers.py), se guarda en STANDBY_FILE y solo entonces se pone a 0. El fichero
::   sobrevive a una interrupcion (Ctrl+C): la siguiente ejecucion no lo pisa y su restore recupera
::   el valor ORIGINAL.
::   Si no se puede leer el valor actual no se toca nada (mejor dejar la suspension como estaba).
::   PROPIEDAD: solo el lanzador mas externo la gestiona. Si FONDOS_ORCH ya esta definida hay un
::   propietario por encima y no se hace nada (ni al desactivar ni al restaurar); si no, este
::   lanzador la desactiva y queda como propietario (STANDBY_OWNED=1), y :standby_restore restaura.
::   Con el argumento `owner` ademas se fija FONDOS_ORCH=1 para que los lanzadores que invoca (sub-
::   lanzadores u otros orquestadores) no la toquen. STANDBY_OWNED se reinicia en cada llamada: un
::   lanzador anidado hereda el valor del padre y no debe tomarlo por suyo.
:: ------------------------------------------------------------
:standby_disable
set "STANDBY_OWNED="
if defined FONDOS_ORCH exit /b 0
if exist "%STANDBY_FILE%" goto :standby_zero
set "SB_MIN="
for /f %%m in ('""%PYTHON%" "%LIB%\batch_helpers.py" standby-minutes"') do set "SB_MIN=%%m"
if not defined SB_MIN (
    echo [WARN] no se pudo leer la suspension AC actual: se deja como esta
    exit /b 0
)
if not exist "%STATE_DIR%" mkdir "%STATE_DIR%"
>"%STANDBY_FILE%" echo %SB_MIN%
:standby_zero
call "%POWERCFG%" -change -standby-timeout-ac 0 > nul 2>&1
set "STANDBY_OWNED=1"
if /i "%~1"=="owner" set "FONDOS_ORCH=1"
exit /b 0

:standby_restore
if not defined STANDBY_OWNED exit /b 0
set "STANDBY_OWNED="
if not exist "%STANDBY_FILE%" exit /b 0
set "SB_MIN="
set /p SB_MIN=<"%STANDBY_FILE%"
if defined SB_MIN call "%POWERCFG%" -change -standby-timeout-ac %SB_MIN% > nul 2>&1
del "%STANDBY_FILE%" > nul 2>&1
exit /b 0

:: ------------------------------------------------------------
:: :state_query SUBCOMANDO VAR -- guarda en VAR la salida (una linea) de `p1p2_state.py SUBCOMANDO`
::   (baseline, calc-version, last-result, oc-newly...); VAR queda vacia si no imprime nada. Las
::   comillas dobles EXTERIORES son imprescindibles: sin ellas cmd /c descarta las de los extremos y
::   rompe el comando cuando hay mas de dos pares (rutas con espacios).
:: ------------------------------------------------------------
:state_query
set "%~2="
for /f "delims=" %%i in ('""%PYTHON%" "%LAUNCH%\p1p2_state.py" %~1"') do set "%~2=%%i"
exit /b 0

:: ------------------------------------------------------------
:: :log_open DIR TAG TITULO -- DIR\log_TAG_STAMP.log (stdout) y ..._err.log (stderr).
::   Fija STAMP, LOG, ERR; crea DIR; escribe la cabecera estandar.
:: ------------------------------------------------------------
:log_open
call :get_time STAMP yyyyMMdd_HHmmss
if not exist "%~1" mkdir "%~1"
set "LOG=%~1\log_%~2_%STAMP%.log"
set "ERR=%~1\log_%~2_%STAMP%_err.log"
echo ============================================================ >> "%LOG%"
echo  %~3 -- Inicio: %STAMP% >> "%LOG%"
echo ============================================================ >> "%LOG%"
exit /b 0

:: ------------------------------------------------------------
:: :log_close TITULO RC -- pie estandar; fija STAMP2.
:: ------------------------------------------------------------
:log_close
call :get_time STAMP2 yyyyMMdd_HHmmss
echo. >> "%LOG%"
echo ============================================================ >> "%LOG%"
echo  %~1 -- Fin: %STAMP2% (RC=%~2) >> "%LOG%"
echo ============================================================ >> "%LOG%"
exit /b 0

:: ------------------------------------------------------------
:: :telemetry SUBCMD [args] -- telemetria de ciclo, mejor esfuerzo. Sin FONDOS_CYCLE_ID (lo exporta el orquestador solo con
::   FONDOS_TELEMETRY=1) no hace nada. SIEMPRE devuelve 0 y descarta su salida: no puede cambiar el RC de ningun lanzador.
::   Llamarla DESPUES de capturar el RC del paso (ERRORLEVEL se pierde al invocar Python). Hasta 9 argumentos (%* ignora el shift
::   del despachador); un valor con '=' o espacios debe ir entrecomillado.
:: ------------------------------------------------------------
:telemetry
if not defined FONDOS_CYCLE_ID exit /b 0
"%PYTHON%" -X utf8 "%ROOT%\shared\cycle_telemetry.py" %1 %2 %3 %4 %5 %6 %7 %8 %9 >nul 2>nul
exit /b 0
