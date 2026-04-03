@echo off
:: Navega a la carpeta raíz del proyecto
cd /d "c:\desarrollo\fondos\proyecto1"

:: Ejecuta el script como módulo
python -m src.init_db

:: Pausa la ventana para que puedas ver si hubo errores o éxito
pause