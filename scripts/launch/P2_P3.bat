@echo off
setlocal
REM P2 recalculation -> beta-shift audit -> P3 build + report -> optional git push -> optional FND-0088 loader sample.
REM Owner-run (full-population P2, ~4 h). Stops at the first failing step; the audit exits non-zero on NULL/NaN,
REM implausible betas (|beta| > 5) or an unfinished P2 run, so a bad recalculation never reaches P3.
cd /d "%~dp0..\.."

echo ===================================================
echo 3. Ejecutando recalculo P2 (CALC_VERSION 20261002)
echo ===================================================
call scripts\launch\P2_calculateIndicators.bat
if %ERRORLEVEL% neq 0 goto :error

echo.
echo ===================================================
echo 4. Ejecutando Beta-shift audit
echo ===================================================
C:\data\envs\des\python.exe -X utf8 scripts\audit\beta_shift_audit.py --compare C:\data\fondos\audit\macro_betas_20260930_baseline.csv --version 20261002 --out C:\data\fondos\audit\post_p2_audit.csv
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Beta-shift audit fallo con codigo %ERRORLEVEL%.
    goto :error
) else (
    echo [OK] Beta-shift audit paso correctamente.
)

echo.
echo ===================================================
echo 5. Ejecutando P3 (Build Portfolio y Generate Report)
echo ===================================================
call scripts\launch\P3_buildPortfolio.bat
if %ERRORLEVEL% neq 0 goto :error

call scripts\launch\P3_generateReport.bat
if %ERRORLEVEL% neq 0 goto :error

echo.
set /p PUSH_CHOICE="¿Estas satisfecho con los reportes? ¿Hacer git push a master (9 commits)? (S/N): "
if /i "%PUSH_CHOICE%"=="S" (
    echo Haciendo push a origin master...
    git push origin master
) else (
    echo Saltando el paso de git push.
)

echo.
echo ===================================================
echo 6. Test FND-0088 loader
echo ===================================================
echo AVISO: Esto consulta Morningstar y escribe en live.
echo Recomendado para ejecutar el 2026-10-03 o despues.
set /p LOADER_CHOICE="¿Deseas lanzar la prueba en una muestra de 40 ahora? (S/N): "
if /i "%LOADER_CHOICE%"=="S" (
    C:\data\envs\des\python.exe -X utf8 -m proyecto1.src.loaders.benchmark_loader --mode update --sample 40
    if %ERRORLEVEL% neq 0 goto :error
) else (
    echo Saltando el test del loader.
)

echo.
echo ===================================================
echo [OK] Todas las operaciones han finalizado con exito.
echo ===================================================
pause
exit /b 0

:error
echo.
echo ===================================================
echo [ERROR] El proceso se interrumpio debido a un error.
echo ===================================================
pause
exit /b %ERRORLEVEL%