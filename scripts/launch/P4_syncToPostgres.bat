@echo off
setlocal enabledelayedexpansion

:: Forzar UTF-8 en cmd
chcp 65001 > nul

:: ============================================================
:: P4_syncToPostgres.bat
:: Copia todas las tablas de fondos.sqlite al PostgreSQL Docker
:: (fondos DB, puerto 5433) usando shared/load_fondos_to_postgres.py.
::
:: Ejecutar DESPUES de P2_calculateIndicators.bat.
:: El ETL descubre automaticamente todas las tablas (incluyendo
:: fund_metric_timeseries y fund_metric_alerts sin cambios en el script).
::
:: Prerequisito: contenedor Docker Postgres activo en localhost:5433.
:: Prerequisito Postgres DDL (primera vez): ejecutar
::   db/postgres_analytics_ddl.sql contra la BD fondos para que
::   fund_metric_timeseries y fund_metric_alerts tengan tipos correctos.
:: ============================================================

set PYTHON=C:\data\envs\des\python.exe
set ROOT=C:\desarrollo\fondos
set LOG_DIR=%ROOT%\logs

:: Timestamp YYYYMMDD_HHMMSS
for /f "tokens=2 delims==" %%a in ('wmic OS Get localdatetime /value') do set DT=%%a
set STAMP=%DT:~0,8%_%DT:~8,6%
set LOG=%LOG_DIR%\log_P4_syncPostgres_%STAMP%.log
set ERR=%LOG_DIR%\log_P4_syncPostgres_%STAMP%_err.log

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo ============================================================ >> "%LOG%"
echo  P4 Sync to Postgres -- Inicio: %STAMP%                      >> "%LOG%"
echo  ROOT:   %ROOT%                                              >> "%LOG%"
echo  PYTHON: %PYTHON%                                            >> "%LOG%"
echo  Target: postgresql://superset:***@localhost:5433/fondos     >> "%LOG%"
echo ============================================================ >> "%LOG%"

echo.
echo [%STAMP%] P4 Sync to Postgres iniciado
echo   Log stdout : %LOG%
echo   Log stderr : %ERR%
echo.

pushd "%ROOT%"

:: -- SYNC ----------------------------------------------------------------------
echo [%time%] Sincronizando SQLite a Postgres...
echo. >> "%LOG%"
echo --- SYNC: load_fondos_to_postgres ----------------------- >> "%LOG%"

%PYTHON% -X utf8 shared\load_fondos_to_postgres.py >> "%LOG%" 2>> "%ERR%"

popd

:: -- Pie del log ---------------------------------------------------------------
for /f "tokens=2 delims==" %%a in ('wmic OS Get localdatetime /value') do set DT2=%%a
set STAMP2=%DT2:~0,8%_%DT2:~8,6%

echo. >> "%LOG%"
echo ============================================================ >> "%LOG%"
echo  P4 Sync to Postgres -- Fin: %STAMP2%                        >> "%LOG%"
echo ============================================================ >> "%LOG%"

echo.
echo [%STAMP2%] P4 Sync to Postgres completado
echo   Log stdout : %LOG%
echo   Log stderr : %ERR%
echo.

endlocal
