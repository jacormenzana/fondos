 @echo off
setlocal

:: 1. Navegar a la raíz del proyecto
cd /d "c:\desarrollo\fondos\proyecto1"

:: 2. Capturar el primer parámetro enviado al .bat
:: Si no se envía nada, se usará "mixtos" por defecto
set BLOCK=%1
if "%BLOCK%"=="" set BLOCK=mixtos

:: 3. Definir el resto de variables fijas
set DATABASE=../db/fondos.sqlite
set MASTER_FILE="c:\data\fondos\in\GestoresDeFondosv1.xlsx"
set SAMPLE=5

echo ==========================================
echo Ejecutando: src.run_block
echo Bloque:     %BLOCK%
echo ==========================================

:: 4. Ejecutar el comando
python -m src.run_block --block %BLOCK% --db %DATABASE% --master %MASTER_FILE% --sample %SAMPLE%

echo.
pause