:: Consulta de agregación para entender la distribución de patrones:
:: Informa sobre la distribución de "firmas de layout". Hipótesis a verificar:

::	Si T,T,T es mayoritaria (todas las páginas en 2-cols) → patrón uniforme, fácil de gestionar.
::	Si hay mezcla S,T,T o T,S,T significativa → layouts heterogéneos, el módulo DLA debe clasificar página por página (que es lo que ya hace, así que está bien).
::	Si aparecen muchas M,... (Mixed) → la heurística de detección necesita refinamiento antes de Fase 1.


python -c "import pandas as pd; df = pd.read_csv(r'c:\desarrollo\fondos\proyecto1\db\dla_layout_inventory.csv'); print(df['layout_signature'].value_counts().head(20))"