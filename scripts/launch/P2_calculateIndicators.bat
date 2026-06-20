@echo off
setlocal enabledelayedexpansion

:: Forzar UTF-8 en cmd para evitar UnicodeEncodeError con caracteres no-ASCII
chcp 65001 > nul

:: ============================================================
:: P2_calculateIndicators.bat -- Ejecucion del Pipeline de Indicadores
:: ============================================================

set ROOT=C:\desarrollo\fondos
set LOG_DIR=%ROOT%\proyecto2\log

:: Timestamp YYYYMMDD_HHMMSS
for /f "tokens=2 delims==" %%a in ('wmic OS Get localdatetime /value') do set DT=%%a
set STAMP=%DT:~0,8%_%DT:~8,6%
set LOG=%LOG_DIR%\log_P2_calcIndicators_%STAMP%.log

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo ============================================================ >> "%LOG%"
echo  Pipeline P2 (Calculate Indicators) - Inicio: %STAMP%        >> "%LOG%"
echo  ROOT:   %ROOT%                                              >> "%LOG%"
echo ============================================================ >> "%LOG%"

echo.
echo [%STAMP%] Pipeline P2 (Calculate Indicators) iniciado
echo Log: %LOG%
echo.

pushd %ROOT%

:: -- EJECUCION DE PIPELINE ---------------------------------------------------
echo [%time%] Ejecutando pipeline de calculo
echo. >> "%LOG%"
echo --- EJECUCION DE PIPELINE --------------------------------- >> "%LOG%"

:: Modo prueba (comentado)
:: python -X utf8 -m proyecto2.src.pipeline.run_pipeline --isin LU0070214613 --dry-run >> "%LOG%" 2>&1

REM python -X utf8 -m proyecto2.src.pipeline.run_pipeline >> "%LOG%" 2>&1
python -X utf8 -m proyecto2.src.pipeline.run_pipeline --source eurostat 2>&1 | powershell -noprofile -command "Tee-Object -FilePath '%LOG%' -Append"

popd

:: -- Pie del log --------------------------------------------------------------
for /f "tokens=2 delims==" %%a in ('wmic OS Get localdatetime /value') do set DT2=%%a
set STAMP2=%DT2:~0,8%_%DT2:~8,6%
echo. >> "%LOG%"
echo ============================================================ >> "%LOG%"
echo  Pipeline P2 (Calculate Indicators) - Fin: %STAMP2%          >> "%LOG%"
echo ============================================================ >> "%LOG%"

echo.
echo [%STAMP2%] Pipeline P2 (Calculate Indicators) completado
echo Log: %LOG%
echo.
endlocal