@echo off
setlocal enabledelayedexpansion

:: Forzar UTF-8 en cmd
chcp 65001 > nul

:: ============================================================
:: P1_P2_Complete.bat  -- Ciclo completo P1 + P2
::
:: Ejecuta en orden:
::   1. P1_refreshBenchmarks.bat   - refresh benchmarks Morningstar
::   2. P1_discoverAllFunds.bat    - pipeline P1 completo (classify + family)
::   3. P2_discoverLoadMetrics.bat - macro (BCE/FRED/Eurostat) + NAV discover/load
::   4. P2_calculateIndicators.bat - metricas cuantitativas P2 + export
::
:: Aborta en el primer paso que devuelva RC != 0.
:: Cada sub-script genera su propio log detallado en su directorio de log.
:: Este script genera un log de orquestacion con timestamps y RCs por paso.
::
:: Uso:
::   cd C:\desarrollo\fondos\scripts\launch
::   P1_P2_Complete.bat                 ciclo completo
::   P1_P2_Complete.bat --force         idem, con recomputo total de P2 (ver abajo)
::   P1_P2_Complete.bat --from N        REANUDAR en el paso N (1-4) tras un fallo
::   P1_P2_Complete.bat --from N --from-any   reanudar saltandose la guarda (ver abajo)
::
:: --from N: re-ejecuta el paso N COMPLETO y los siguientes. Es seguro porque todas
::   las escrituras de los cuatro pasos son idempotentes (upsert, reemplazo completo o
::   log append-only; tests/test_sql_explain_sweep_pg.py lo verifica). Solo se permite
::   si el ultimo ciclo FALLO en el paso N o en uno posterior: reanudar mas tarde que el
::   paso fallido se saltaria un paso cuya salida consumen los siguientes. Si no hay
::   estado legible del ultimo ciclo, se rechaza (falla cerrado).
:: --from-any: anula esa guarda. Queda en el log y en ingestion_log (P1P2_ORCH/FROM_ANY).
::
:: Auditoria estadistica (PASO 0 y final): antes de PASO 1 se ejecutan las auditorias estadisticas
::   de P2 y de costes con --persist (baseline pre_<STAMP>) y, si el ciclo termina OK, otra vez con
::   --persist --compare-to <baseline>. Asi el desplazamiento de metricas (p. ej. tras un cambio de
::   CALC_VERSION) queda cuantificado y registrado (control.audit_statistic / audit_finding) sin
::   comandos manuales; el informe de deriva va a log_P1_P2_audit_<STAMP>.log. Con --from N > 1 se
::   reutiliza el baseline del ciclo fallido. Un fallo de auditoria nunca aborta el ciclo.
:: Reparacion de costes (tras PASO 2): un pase P1 puede volver a escribir
::   ongoing_charge_recurrent = ACI_RHP en fondos cacheados (comportamiento previo, verificado con
::   el codigo commiteado). Se guarda la lista de fondos contaminados antes de PASO 2 y despues se
::   ejecuta `run_block.py --recompute-costs` (solo cache, sin descargas) SOLO sobre los que este
::   P1 acaba de contaminar; los ya contaminados de antes no se tocan.
:: La logica del estado vive en p1p2_state.py (testeable); este .bat solo lo invoca.
:: Estado: proyecto1\log\P1_P2_Complete.state (variable P1P2_STATE_FILE para cambiarlo).
:: Codigos de salida propios: 2 = --from rechazado, 3 = estado no escribible,
::   4 = argumentos invalidos. Cualquier otro = RC del paso que fallo.
:: ============================================================

set PYTHON=C:\data\envs\des\python.exe
set ROOT=C:\desarrollo\fondos
set LAUNCH=%ROOT%\scripts\launch
set LOG_DIR=%ROOT%\proyecto1\log

:: --force flag: passed through to P2_calculateIndicators.bat to bypass
:: hash-cache AND quarterly OLS gate (force full OLS recompute).
set FORCE_FLAG=
set FROM_STEP=1
set FROM_GIVEN=
set FROM_ANY=

:parse
if "%~1"=="" goto :parsed
if /i "%~1"=="--force" (
    set FORCE_FLAG=--force
    shift
    goto :parse
)
if /i "%~1"=="--from" (
    set FROM_STEP=%~2
    set FROM_GIVEN=1
    shift
    shift
    goto :parse
)
if /i "%~1"=="--from-any" (
    set FROM_ANY=1
    shift
    goto :parse
)
echo [ERROR] Argumento desconocido: %~1
echo Uso: P1_P2_Complete.bat [--force] [--from N [--from-any]]
endlocal & exit /b 4
:parsed

:: Comprobacion previa (antes de crear log o tocar nada): guarda de --from N y estado
:: escribible. Sale con RC 2/3/4 sin ejecutar ningun paso.
set CHECK_ARGS=
set FROM_ANY_USED=
if defined FROM_GIVEN set CHECK_ARGS=--from "!FROM_STEP!"
if defined FROM_GIVEN if defined FROM_ANY set CHECK_ARGS=!CHECK_ARGS! --from-any
if defined FROM_GIVEN if defined FROM_ANY set FROM_ANY_USED=--from-any-used
"%PYTHON%" "%LAUNCH%\p1p2_state.py" check !CHECK_ARGS!
set CHK_RC=!ERRORLEVEL!
if !CHK_RC! NEQ 0 goto :abort_check

:: Timestamp YYYYMMDD_HHMMSS (wmic removed on newer Windows builds; PowerShell
:: is the portable replacement)
for /f %%a in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set STAMP=%%a
set LOG=%LOG_DIR%\log_P1_P2_complete_%STAMP%.log

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo ============================================================ >> "%LOG%"
echo  P1+P2 Complete -- Inicio: %STAMP%                           >> "%LOG%"
echo  ROOT:   %ROOT%                                              >> "%LOG%"
echo  PYTHON: %PYTHON%                                            >> "%LOG%"
echo  FORCE:  %FORCE_FLAG% >> "%LOG%"
echo  FROM:   !FROM_STEP! (from-any=!FROM_ANY!) >> "%LOG%"
echo ============================================================ >> "%LOG%"

echo.
echo [%STAMP%] P1+P2 Complete iniciado
echo   Log orquestacion: %LOG%
echo.

:: Prevenir suspension/hibernacion durante la ejecucion (~30-90 min)
powercfg -change -standby-timeout-ac 0 > nul 2>&1

set FINAL_RC=0
set FAILED_STEP=0

set AUDIT_LOG=%LOG_DIR%\log_P1_P2_audit_%STAMP%.log
set RECOMP_LOG=%LOG_DIR%\log_P1_P2_recompute_%STAMP%.log
set BASELINE_ID=

:: ============================================================
:: PASO 0: auditoria estadistica BASELINE (antes de tocar nada)
:: ============================================================
if !FROM_STEP! GTR 1 goto :baseline_reuse
set BASELINE_ID=pre_%STAMP%
echo. >> "%LOG%"
echo --- PASO 0: auditoria estadistica baseline (run_id=!BASELINE_ID!^) ---- >> "%LOG%"
echo [%STAMP%] PASO 0: auditoria estadistica baseline (!BASELINE_ID!^)
"%PYTHON%" -u -X utf8 "%ROOT%\scripts\audit\run_statistical_audit.py" --domain p2 --mode report --persist --run-id !BASELINE_ID!_p2 >> "%AUDIT_LOG%" 2>&1
if errorlevel 1 echo [WARN] auditoria baseline p2 fallo: no se cuantificara la deriva >> "%LOG%"
"%PYTHON%" -u -X utf8 "%ROOT%\scripts\audit\run_statistical_audit.py" --domain costs --mode report --persist --run-id !BASELINE_ID!_costs >> "%AUDIT_LOG%" 2>&1
if errorlevel 1 echo [WARN] auditoria baseline costs fallo: no se cuantificara la deriva >> "%LOG%"
goto :baseline_done
:baseline_reuse
for /f "delims=" %%b in ('%PYTHON% %LAUNCH%\p1p2_state.py baseline') do set BASELINE_ID=%%b
echo --- PASO 0: baseline reutilizado del ciclo anterior: !BASELINE_ID! >> "%LOG%"
:baseline_done

:: ============================================================
:: PASO 1: P1_refreshBenchmarks.bat
:: ============================================================
set RC1=SALTADO
if !FROM_STEP! GTR 1 (
    echo. >> "%LOG%"
    echo --- PASO 1/4: SALTADO (--from !FROM_STEP!^) --------------------- >> "%LOG%"
    goto :step2
)
for /f %%a in ('powershell -NoProfile -Command "Get-Date -Format HHmmss"') do set T1=%%a
echo [%T1:~0,2%:%T1:~2,2%:%T1:~4,2%] PASO 1/4: P1_refreshBenchmarks
echo. >> "%LOG%"
echo --- PASO 1/4: P1_refreshBenchmarks -- Inicio: !T1! ---------- >> "%LOG%"

call "%LAUNCH%\P1_refreshBenchmarks.bat"
set RC1=!ERRORLEVEL!

for /f %%a in ('powershell -NoProfile -Command "Get-Date -Format HHmmss"') do set T1E=%%a
echo [%T1E:~0,2%:%T1E:~2,2%:%T1E:~4,2%] PASO 1/4 fin (RC=!RC1!)
echo --- PASO 1/4 fin: RC=!RC1!  Fin: !T1E! --------------------- >> "%LOG%"
"%PYTHON%" "%LAUNCH%\p1p2_state.py" step --step 1 --rc !RC1! --start !T1! --end !T1E! !FROM_ANY_USED!

if !RC1! NEQ 0 (
    set FINAL_RC=!RC1!
    set FAILED_STEP=1
    echo ABORTADO en PASO 1 -- RC=!RC1! >> "%LOG%"
    echo.
    echo [ERROR] PASO 1 fallo (RC=!RC1!^). Ciclo abortado. Reanudar: P1_P2_Complete.bat --from 1
    goto :fin
)

:: ============================================================
:: PASO 2: P1_discoverAllFunds.bat
:: ============================================================
:step2
set RC2=SALTADO
if !FROM_STEP! GTR 2 (
    echo. >> "%LOG%"
    echo --- PASO 2/4: SALTADO (--from !FROM_STEP!^) --------------------- >> "%LOG%"
    goto :step3
)
"%PYTHON%" "%LAUNCH%\p1p2_state.py" oc-before
for /f %%a in ('powershell -NoProfile -Command "Get-Date -Format HHmmss"') do set T2=%%a
echo [%T2:~0,2%:%T2:~2,2%:%T2:~4,2%] PASO 2/4: P1_discoverAllFunds
echo. >> "%LOG%"
echo --- PASO 2/4: P1_discoverAllFunds -- Inicio: !T2! ----------- >> "%LOG%"

call "%LAUNCH%\P1_discoverAllFunds.bat"
set RC2=!ERRORLEVEL!

for /f %%a in ('powershell -NoProfile -Command "Get-Date -Format HHmmss"') do set T2E=%%a
echo [%T2E:~0,2%:%T2E:~2,2%:%T2E:~4,2%] PASO 2/4 fin (RC=!RC2!)
echo --- PASO 2/4 fin: RC=!RC2!  Fin: !T2E! --------------------- >> "%LOG%"
"%PYTHON%" "%LAUNCH%\p1p2_state.py" step --step 2 --rc !RC2! --start !T2! --end !T2E! !FROM_ANY_USED!

if !RC2! NEQ 0 (
    set FINAL_RC=!RC2!
    set FAILED_STEP=2
    echo ABORTADO en PASO 2 -- RC=!RC2! >> "%LOG%"
    echo.
    echo [ERROR] PASO 2 fallo (RC=!RC2!^). Ciclo abortado. Reanudar: P1_P2_Complete.bat --from 2
    goto :fin
)

:: Reparacion de costes: solo los fondos que ESTE P1 ha contaminado (ver cabecera).
set OC_FIX=
for /f "delims=" %%i in ('%PYTHON% %LAUNCH%\p1p2_state.py oc-newly') do set OC_FIX=%%i
if not defined OC_FIX goto :oc_done
echo --- PASO 2b: recompute-costs de fondos contaminados por este P1: !OC_FIX! >> "%LOG%"
echo [PASO 2b] recompute-costs (solo cache) sobre fondos contaminados por este P1
pushd "%ROOT%\proyecto1"
"%PYTHON%" -X utf8 run_block.py --nature-first --master-db --recompute-costs --list-isin !OC_FIX! >> "%RECOMP_LOG%" 2>&1
set RC2B=!ERRORLEVEL!
popd
echo --- PASO 2b fin: RC=!RC2B!  log: %RECOMP_LOG% >> "%LOG%"
if !RC2B! NEQ 0 echo [WARN] recompute-costs fallo (RC=!RC2B!^): revisar %RECOMP_LOG% >> "%LOG%"
:: Verificacion: si el repair fallo o no bastó, el ciclo NO se detiene pero queda avisado.
set OC_LEFT=
for /f "delims=" %%i in ('%PYTHON% %LAUNCH%\p1p2_state.py oc-newly') do set OC_LEFT=%%i
if defined OC_LEFT echo [WARN] siguen contaminados por este P1 tras recompute-costs: !OC_LEFT! >> "%LOG%"
if defined OC_LEFT echo [WARN] fondos contaminados por este P1 sin reparar: !OC_LEFT!
:oc_done

:: ============================================================
:: PASO 3: P2_discoverLoadMetrics.bat
:: ============================================================
:step3
set RC3=SALTADO
if !FROM_STEP! GTR 3 (
    echo. >> "%LOG%"
    echo --- PASO 3/4: SALTADO (--from !FROM_STEP!^) --------------------- >> "%LOG%"
    goto :step4
)
for /f %%a in ('powershell -NoProfile -Command "Get-Date -Format HHmmss"') do set T3=%%a
echo [%T3:~0,2%:%T3:~2,2%:%T3:~4,2%] PASO 3/4: P2_discoverLoadMetrics
echo. >> "%LOG%"
echo --- PASO 3/4: P2_discoverLoadMetrics -- Inicio: !T3! -------- >> "%LOG%"

call "%LAUNCH%\P2_discoverLoadMetrics.bat"
set RC3=!ERRORLEVEL!

for /f %%a in ('powershell -NoProfile -Command "Get-Date -Format HHmmss"') do set T3E=%%a
echo [%T3E:~0,2%:%T3E:~2,2%:%T3E:~4,2%] PASO 3/4 fin (RC=!RC3!)
echo --- PASO 3/4 fin: RC=!RC3!  Fin: !T3E! --------------------- >> "%LOG%"
"%PYTHON%" "%LAUNCH%\p1p2_state.py" step --step 3 --rc !RC3! --start !T3! --end !T3E! !FROM_ANY_USED!

if !RC3! NEQ 0 (
    set FINAL_RC=!RC3!
    set FAILED_STEP=3
    echo ABORTADO en PASO 3 -- RC=!RC3! >> "%LOG%"
    echo.
    echo [ERROR] PASO 3 fallo (RC=!RC3!^). Ciclo abortado. Reanudar: P1_P2_Complete.bat --from 3
    goto :fin
)

:: ============================================================
:: PASO 4: P2_calculateIndicators.bat
:: ============================================================
:step4
for /f %%a in ('powershell -NoProfile -Command "Get-Date -Format HHmmss"') do set T4=%%a
echo [%T4:~0,2%:%T4:~2,2%:%T4:~4,2%] PASO 4/4: P2_calculateIndicators
echo. >> "%LOG%"
echo --- PASO 4/4: P2_calculateIndicators -- Inicio: !T4! -------- >> "%LOG%"

call "%LAUNCH%\P2_calculateIndicators.bat" %FORCE_FLAG%
set RC4=!ERRORLEVEL!

for /f %%a in ('powershell -NoProfile -Command "Get-Date -Format HHmmss"') do set T4E=%%a
echo [%T4E:~0,2%:%T4E:~2,2%:%T4E:~4,2%] PASO 4/4 fin (RC=!RC4!)
echo --- PASO 4/4 fin: RC=!RC4!  Fin: !T4E! --------------------- >> "%LOG%"
"%PYTHON%" "%LAUNCH%\p1p2_state.py" step --step 4 --rc !RC4! --start !T4! --end !T4E! !FROM_ANY_USED!

if !RC4! NEQ 0 (
    set FINAL_RC=!RC4!
    set FAILED_STEP=4
    echo ABORTADO en PASO 4 -- RC=!RC4! >> "%LOG%"
    echo.
    echo [ERROR] PASO 4 fallo (RC=!RC4!^). Revisar log P2_calculateIndicators. Reanudar: P1_P2_Complete.bat --from 4
    goto :fin
)

:fin

:: Restaurar suspension AC al valor por defecto de Windows (30 min)
powercfg -change -standby-timeout-ac 30 > nul 2>&1

:: Pie del log
for /f %%a in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set STAMP2=%%a

if !FINAL_RC! NEQ 0 goto :post_audit_done
if not defined BASELINE_ID goto :post_audit_done
echo. >> "%LOG%"
echo --- Auditoria estadistica final (compare-to !BASELINE_ID!^) ---- >> "%LOG%"
echo [AUDITORIA] deriva vs baseline !BASELINE_ID!
"%PYTHON%" -u -X utf8 "%ROOT%\scripts\audit\run_statistical_audit.py" --domain p2 --mode report --persist --run-id post_!STAMP2!_p2 --compare-to !BASELINE_ID!_p2 >> "%AUDIT_LOG%" 2>&1
if errorlevel 1 echo [WARN] auditoria final p2 fallo >> "%LOG%"
"%PYTHON%" -u -X utf8 "%ROOT%\scripts\audit\run_statistical_audit.py" --domain costs --mode report --persist --run-id post_!STAMP2!_costs --compare-to !BASELINE_ID!_costs >> "%AUDIT_LOG%" 2>&1
if errorlevel 1 echo [WARN] auditoria final costs fallo >> "%LOG%"
echo   Informe de deriva: %AUDIT_LOG% >> "%LOG%"
echo   Informe de deriva: %AUDIT_LOG%
:post_audit_done
:: Sello de fin real: las auditorias finales pueden tardar unos minutos tras calcularse STAMP2.
for /f %%a in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set STAMP2=%%a

echo. >> "%LOG%"
echo ============================================================ >> "%LOG%"

if !FINAL_RC! EQU 0 (
    echo  P1+P2 Complete -- Fin OK: %STAMP2%                          >> "%LOG%"
    echo  RC1=!RC1!  RC2=!RC2!  RC3=!RC3!  RC4=!RC4!                 >> "%LOG%"
    echo ============================================================ >> "%LOG%"
    echo.
    echo [%STAMP2%] P1+P2 Complete -- Fin OK
    set RESULT=OK
) else (
    echo  P1+P2 Complete -- Fin ERROR: %STAMP2%                       >> "%LOG%"
    echo  RC1=!RC1!  RC2=!RC2!  RC3=!RC3!  RC4=!RC4!                 >> "%LOG%"
    echo ============================================================ >> "%LOG%"
    echo.
    echo [%STAMP2%] P1+P2 Complete -- Fin ERROR (FINAL_RC=!FINAL_RC!^)
    set RESULT=FAILED
)

:: Estado del ciclo para la guarda de --from N (escritura atomica en p1p2_state.py). Un fallo
:: al escribirlo no cambia el RC del ciclo: solo se avisa (y el siguiente --from se rechazara).
set BASELINE_ARG=
if defined BASELINE_ID set BASELINE_ARG=--baseline-id !BASELINE_ID!
"%PYTHON%" "%LAUNCH%\p1p2_state.py" write --result !RESULT! --failed-step !FAILED_STEP! --stamp !STAMP2! !FROM_ANY_USED! !BASELINE_ARG!
if errorlevel 1 (
    echo [WARN] No se pudo escribir el estado del ciclo. --from N se rechazara hasta el siguiente ciclo completo.
    echo [WARN] No se pudo escribir el estado del ciclo >> "%LOG%"
)

echo   Log orquestacion: %LOG%
echo.

:: endlocal discards delayed expansion before !FINAL_RC! on the next line
:: could expand it (verified empirically) -- chain on one line so %FINAL_RC%
:: substitutes at parse time, while the scope is still active.
endlocal & exit /b %FINAL_RC%

:abort_check
echo.
echo [ERROR] Comprobacion previa fallida (RC=!CHK_RC!^). No se ejecuta ningun paso.
:: same one-line chaining trick as above: %CHK_RC% substitutes before endlocal runs
endlocal & exit /b %CHK_RC%
