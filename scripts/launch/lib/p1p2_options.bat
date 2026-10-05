@echo off
:: lib\p1p2_options.bat -- tabla de opciones del ciclo P1+P2, UNICA fuente para P1_P2_Complete.bat
:: y para P1_P2_P3.bat (que las reenvia): una opcion nueva se anade aqui y en el :usage_text de
:: P1_P2_Complete.bat, y el segundo la acepta sin tocarlo. Es un fichero de datos: sin setlocal,
:: fija las variables en el ambito de quien lo llama (call "%LAUNCH%\lib\p1p2_options.bat").
::   BOOL_OPTS  opciones sin valor  (--x-y -> FLAG_x_y)
::   UINT_OPTS  opciones con un entero positivo (--x-y N -> OPT_x_y)
set "BOOL_OPTS=--force --no-export --skip-macro --skip-preflight --no-harvest --harvest-sync --harvest-retire --benchmarks-load --benchmark-gaps --dashboard --diag-cost"
set "UINT_OPTS=--workers --harvest-limit --harvest-max-drop-pct --shadow"
exit /b 0
