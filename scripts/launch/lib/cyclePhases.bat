@echo off
:: ============================================================
:: lib\cyclePhases.bat -- fases de auditoria compartidas por los orquestadores *_runCycle (una sola copia: P#11)
:: Norma: doc/reglas/NORMAS_BATCH.md (seccion 14). Mismo contrato que lib\common.bat: sin setlocal, el resultado
:: queda en una variable documentada, nunca termina el proceso. El llamante ya tiene EnableDelayedExpansion.
::
::   call "%PHASES%" :audit_p1 RUN_ID [COMPARE_TO]   P1_audit.bat (costes + benchmarks B1-B7) persistido; resultado en AUD_RC
::   call "%PHASES%" :audit_p2 RUN_ID [COMPARE_TO]   P2_audit.bat (metricas) persistido; resultado en AUD_RC
::   call "%PHASES%" :preflight LOG_PATH             p1p2_state.py preflight (BD alcanzable, DSN, disco); PRE_RC (104 = fallo)
::   call "%PHASES%" :p3_freshness                   p3_freshness_check.py: la puerta de frescura de P3 sobre los datos de ahora (informativa)
::   call "%PHASES%" :cycle_report STAMP              p1p2_cycle_report.py desde la fecha del STAMP: lineas [ATENCION] (informativa)
::   call "%PHASES%" :baseline_verdict RC_P1 RC_P2   veredicto del PRE: BASELINE_RC = 0, o RC_BASELINE (107) si alguno
::                                                   de los dos no devolvio 0 (fallo o INCOMPLETA: no es evidencia).
::                                                   Con ALLOW_NO_BASELINE definida el fallo se avisa pero no bloquea.
::
:: Variables que lee (opcionales): AUDIT_LOG (--log de la auditoria; sin ella cada auditoria escribe su log por defecto),
:: LOG (log de orquestacion; sin el, los avisos solo van a consola), ALLOW_NO_BASELINE.
:: Nombres de corrida: pre_<sello>_p2 / pre_<sello>_costs y post_<sello>_p2 / post_<sello>_costs (los que lee gating.py).
:: Todas las auditorias van en modo report: diagnosticas, nunca cambian el RC de un ciclo salvo el veredicto del PRE.
:: ============================================================
set "_PH_LBL=%~1"
shift
if not defined _PH_LBL (
    echo [cyclePhases.bat] falta el nombre de la subrutina
    exit /b %RC_USAGE%
)
goto %_PH_LBL%

:audit_p1
set "AUD_CMP="
if not "%~2"=="" set "AUD_CMP=--compare-to %~2"
set "AUD_LOGARG="
if defined AUDIT_LOG set AUD_LOGARG=--log "%AUDIT_LOG%"
call "%LAUNCH%\P1_audit.bat" --mode report --persist --run-id %~1 !AUD_CMP! !AUD_LOGARG! > nul
set "AUD_RC=!ERRORLEVEL!"
exit /b 0

:audit_p2
set "AUD_CMP="
if not "%~2"=="" set "AUD_CMP=--compare-to %~2"
set "AUD_LOGARG="
if defined AUDIT_LOG set AUD_LOGARG=--log "%AUDIT_LOG%"
call "%LAUNCH%\P2_audit.bat" --mode report --persist --run-id %~1 !AUD_CMP! !AUD_LOGARG! > nul
set "AUD_RC=!ERRORLEVEL!"
exit /b 0

:preflight
set "PRE_LOG=%~1"
"%PYTHON%" "%LAUNCH%\p1p2_state.py" preflight > "%PRE_LOG%" 2>&1
set "PRE_RC=!ERRORLEVEL!"
if defined LOG echo --- PREFLIGHT: RC=!PRE_RC! >> "%LOG%"
type "%PRE_LOG%"
if defined LOG type "%PRE_LOG%" >> "%LOG%"
echo.
exit /b 0

:baseline_verdict
set "BASELINE_RC=0"
if "%~1"=="0" if "%~2"=="0" exit /b 0
set "BASELINE_RC=%RC_BASELINE%"
echo [BASELINE] la auditoria PRE no es valida (P1=%~1 P2=%~2; 2 = INCOMPLETA^): sin baseline no hay deriva objetiva
if defined LOG echo [BASELINE] la auditoria PRE no es valida (P1=%~1 P2=%~2^) >> "%LOG%"
if defined ALLOW_NO_BASELINE (
    set "BASELINE_RC=0"
    echo [WARN] --allow-no-baseline: se continua SIN baseline valido; la deriva de este ciclo no sera evidencia
    if defined LOG echo [WARN] --allow-no-baseline: se continua SIN baseline valido >> "%LOG%"
)
exit /b 0

:p3_freshness
"%PYTHON%" -X utf8 "%ROOT%\scripts\launch\p3_freshness_check.py"
set "FRESH_RC=!ERRORLEVEL!"
if defined LOG echo --- frescura de los datos para P3: RC=!FRESH_RC! >> "%LOG%"
if !FRESH_RC! NEQ 0 echo [WARN] P3 no aceptaria los datos del ciclo ^(RC=!FRESH_RC!^)
exit /b 0

:cycle_report
set "CR_STAMP=%~1"
set "CR_SINCE=!CR_STAMP:~0,4!-!CR_STAMP:~4,2!-!CR_STAMP:~6,2!"
"%PYTHON%" -X utf8 "%ROOT%\scripts\launch\p1p2_cycle_report.py" --stamp !CR_STAMP! --since !CR_SINCE!
set "REPORT_RC=!ERRORLEVEL!"
if defined LOG echo --- informe de ciclo: RC=!REPORT_RC! >> "%LOG%"
if !REPORT_RC! NEQ 0 echo [WARN] el informe de ciclo no se genero ^(RC=!REPORT_RC!^)
exit /b 0
