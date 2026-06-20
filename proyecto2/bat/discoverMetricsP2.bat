c:\desarrollo\fondos

REM PASO 1: CARGA DE FUENTES BCE FRED Y EUROESTAT
python -m proyecto2.src.loaders.macro_loader --source bce
python -m proyecto2.src.loaders.macro_loader --source fred
python -m proyecto2.src.loaders.macro_loader --source eurostat

REM PASO 2: CARGA FUENTES MORNINGSTAR
REM python -m proyecto2.src.loaders.nav_discovery --mode discover --isin LU1873127366 --dry-run --verbose
REM python -m proyecto2.src.loaders.nav_discovery --mode discover --sample 10
REM python -m proyecto2.src.loaders.nav_discovery --mode load --isin LU1873127366 --dry-run --verbose
python -m proyecto2.src.loaders.nav_discovery --mode discover
python -m proyecto2.src.loaders.nav_discovery --mode load --desde 2016-01-01 > logs\navLoad_hd.log 2>&1
REM En otra ventana CMD:
powershell -command "Get-Content logs\navLoad_hd.log -Wait -Tail 20"

REM python -m proyecto2.src.loaders.test_historia



python -m proyecto2.src.pipeline.run_pipeline --isin LU0070214613 --dry-run


--- RESTAURACION DE KIID_RAW_TEXT DESDE FICHERO
python scripts/restore_kiid_text.py --ref "C:\data\fondos\out\p1_output_sqlite.xlsx" --db "db\fondos.sqlite"


python -m proyecto1.core.fund_family_builder
python -m proyecto1.src.analysis.export_p1 --include-kiid-text