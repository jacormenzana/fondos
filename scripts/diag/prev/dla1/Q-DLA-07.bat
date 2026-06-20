:: Consulta de agregación para entender la distribución de patrones:
:: Cuenta cuántos fondos tienen ≥2 páginas en 2-cols — son los candidatos más impactados, ideales para piloto.Informa sobre la distribución de "firmas de layout". Hipótesis a verificar:

python -c "import pandas as pd; df = pd.read_csv(r'c:\desarrollo\fondos\proyecto1\db\dla_layout_inventory.csv'); print((df['n_pages_two_col'] >= 2).sum())"