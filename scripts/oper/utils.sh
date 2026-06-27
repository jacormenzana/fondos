rmdir /S /Q scripts\diag\__pycache__
python -m scripts.diag.dla2_dual_strategy_compare --pdf-dir "C:\data\fondos\kiid" --db "C:\desarrollo\fondos\db\fondos.sqlite"


findstr /C:"v2." dla2_xband_prototype.py
python -m scripts.diag.dla2_dual_strategy_compare --pdf-dir "C:\data\fondos\kiid" --db "C:\desarrollo\fondos\db\fondos.sqlite"


python -m scripts.diag.dla2_ocr_fallback "C:\data\fondos\kiid\LU0293294277.pdf"
python -m scripts.diag.dla2_dual_strategy_compare --pdf "C:\data\fondos\kiid\LU0293294277.pdf" --db "C:\desarrollo\fondos\db\fondos.sqlite"
python -m scripts.diag.both_fail_triage --pdfdir "C:\\data\\fondos\\kiid"

--- Codigo de test de atributo DLA2_Txt
python -X utf8 -c "import sys; sys.path.insert(0,r'C:\desarrollo\fondos\proyecto1\core'); from dla_table_serializer import serialize_tables as s; b=open(r'C:\data\fondos\kiid\LU0083138064.pdf','rb').read(); t,m=s(b, text='', debug=True); print('META',m); print('LEN',len(t))"

--- Codigo de diagnostico de costes
 C:\desarrollo\fondos\scripts\diag>set PYTHONPATH=C:\desarrollo\fondos\proyecto1;C:\desarrollo\fondos\proyecto1\core;C:\desarrollo\fondos\shared && python -X utf8 diag_cost_extraction.py --db ..\..\db\fondos.sqlite --kiid-dir c:\data\fondos\kiid --isins ES0126547035 --out C:\desarrollo\fondos\out\diag\diag_debug_es0126547035.csv

set PYTHONPATH=C:\desarrollo\fondos\proyecto1;C:\desarrollo\fondos\proyecto1\core;C:\desarrollo\fondos\shared && python -X utf8 -c "import sys,glob,pdfplumber; sys.path.insert(0,r'C:\desarrollo\fondos\proyecto1\core'); from dla_table_serializer import serialize_tables; f=glob.glob(r'c:\data\fondos\kiid\ES0126547035.pdf'); print('PDF:',f); pdf=pdfplumber.open(f[0]); t,m=serialize_tables(pdf); pdf.close(); lines=t.split(chr(10)); idx=next((i for i,l in enumerate(lines) if 'largo' in l.lower()),None); [print(repr(l)) for l in (lines[idx:idx+12] if idx is not None else ['OT NOT FOUND'])]"

python -X utf8 -c "import sys,glob,pdfplumber; sys.path.insert(0,r'C:\desarrollo\fondos\proyecto1\core'); from dla_table_serializer import serialize_tables; f=glob.glob(r'c:\data\fondos\kiid\*ES0126547035*'); pdf=pdfplumber.open(f[0]); t,m=serialize_tables(pdf); pdf.close(); print(repr(t[:2000])); print('META:',m)"

python -X utf8 -c "import sys,glob,pdfplumber; f=glob.glob(r'c:\data\fondos\kiid\*ES0126547035*'); pdf=pdfplumber.open(f[0]); [print(f'--PAGE {i}--',chr(10),pg.extract_text()[:1000]) for i,pg in enumerate(pdf.pages[:3])]; pdf.close()"

python -X utf8 -c "import sys,sqlite3; sys.path.insert(0,r'C:\desarrollo\fondos\proyecto1\core'); from dla_table_serializer import serialize_tables; from cost_table_parser import parse_costs_over_time; conn=sqlite3.connect(r'C:\desarrollo\fondos\db\fondos.sqlite'); raw=conn.execute(\"SELECT Raw_KIID_Text FROM fund_kiid_metadata WHERE ISIN='ES0126547035'\").fetchone()[0] or ''; conn.close(); pdf=open(r'c:\data\fondos\kiid\ES0126547035.pdf','rb').read(); grid,meta=serialize_tables(pdf,text=raw); fed=(raw+chr(10)+grid) if grid else raw; ot=parse_costs_over_time(fed); print('OT result:',ot); print('grid len:',len(grid))"

python -X utf8 -c "import sys,sqlite3; sys.path.insert(0,r'C:\desarrollo\fondos\proyecto1\core'); from dla_table_serializer import serialize_tables; from cost_table_parser import parse_costs_over_time; conn=sqlite3.connect(r'C:\desarrollo\fondos\db\fondos.sqlite'); raw=conn.execute(\"SELECT Raw_KIID_Text FROM fund_kiid_metadata WHERE ISIN='FR0000989626'\").fetchone()[0] or ''; conn.close(); pdf=open(r'c:\data\fondos\kiid\FR0000989626.pdf','rb').read(); grid,_=serialize_tables(pdf,text=raw); fed=(raw+chr(10)+grid) if grid else raw; ot=parse_costs_over_time(fed); print('OT:',ot)"

python -X utf8 -c "import sys; sys.path.insert(0,r'C:\desarrollo\fondos\proyecto1\core'); import cost_table_parser as p; import inspect; src=inspect.getsource(p._parse_costs_over_time_plain); print('FIX-P1-E present:', 'PLAIN_GLOBAL_FALLBACK' in src); print('---'); raw=open(r'c:\data\fondos\kiid\ES0126547035.pdf','rb').read(); print('PDF bytes ok')"

python -X utf8 -c "import sys,sqlite3; sys.path.insert(0,r'C:\desarrollo\fondos\proyecto1\core'); from dla_table_serializer import serialize_tables; conn=sqlite3.connect(r'C:\desarrollo\fondos\db\fondos.sqlite'); raw=conn.execute(\"SELECT Raw_KIID_Text FROM fund_kiid_metadata WHERE ISIN='ES0126547035'\").fetchone()[0] or ''; conn.close(); pdf=open(r'c:\data\fondos\kiid\ES0126547035.pdf','rb').read(); grid,_=serialize_tables(pdf,text=raw); [print(repr(l)) for l in grid.split(chr(10)) if '|||' in l and any(k in l.lower() for k in ['largo','costes tot','incidencia','horizonte','despues','salida'])]"

python -X utf8 diag_cost_extraction.py --db ..\..\db\fondos.sqlite --kiid-dir c:\data\fondos\kiid --only-priips --out C:\desarrollo\fondos\out\diag\cost_diag_20260615_p1f.csv

python -X utf8 -c "import sqlite3; conn=sqlite3.connect(r'C:\desarrollo\fondos\db\fondos.sqlite'); raw=conn.execute(\"SELECT Raw_KIID_Text FROM fund_kiid_metadata WHERE ISIN='FR0000989626'\").fetchone()[0] or ''; conn.close(); print('horizonte in RAW alone:', 'horizonte' in raw); idx=raw.find('horizonte'); print(repr(raw[max(0,idx-150):idx+150]) if idx>=0 else 'not present')"




python -X utf8 -c "import sys,sqlite3; sys.path.insert(0,r'C:\desarrollo\fondos\proyecto1\core'); from dla_table_serializer import serialize_tables; conn=sqlite3.connect(r'C:\desarrollo\fondos\db\fondos.sqlite'); isins=['FR0000989626','LU0048573561','IE00BYX5N771']; [print('====',isin,'====') or [print(repr(l)) for l in serialize_tables(open('c:\\\\data\\\\fondos\\\\kiid\\\\'+isin+'.pdf','rb').read(), text=(conn.execute('SELECT Raw_KIID_Text FROM fund_kiid_metadata WHERE ISIN=?',(isin,)).fetchone() or [''])[0] or '')[0].split(chr(10)) if '|||' in l and any(k in l.lower() for k in ['largo','costes tot','incidencia','horizonte','despues','salida','reduc'])] for isin in isins]; conn.close()"

python -X utf8 -c "import sys,sqlite3; sys.path.insert(0,r'C:\desarrollo\fondos\proyecto1\core'); from dla_table_serializer import serialize_tables; conn=sqlite3.connect(r'C:\desarrollo\fondos\db\fondos.sqlite'); raw=conn.execute(\"SELECT Raw_KIID_Text FROM fund_kiid_metadata WHERE ISIN='FR0000989626'\").fetchone()[0] or ''; conn.close(); pdf=open(r'c:\data\fondos\kiid\FR0000989626.pdf','rb').read(); grid,_=serialize_tables(pdf,text=raw); lines=grid.split(chr(10)); idx=next((i for i,l in enumerate(lines) if 'largo del tiempo' in l.lower()),None); [print(repr(l)) for l in lines[idx:idx+25]] if idx is not None else print('NOT FOUND')"

python -X utf8 -c "import sys; sys.path.insert(0,r'C:\desarrollo\fondos\proyecto1\core'); import cost_table_parser as p; import inspect; print('P1-H present:', 'FIX-P1-H' in inspect.getsource(p)); print(p.ACI_ROW.pattern)"

python -X utf8 -c "import sys; sys.path.insert(0,r'C:\desarrollo\fondos\proyecto1\core'); import dla_table_serializer as d; import inspect; src=inspect.getsource(d.serialize_tables); print('P1-A present (>=):', '>= _ot_completeness' in src); print('P3 present:', 'FIX-P3' in inspect.getsource(d))"

python -X utf8 -c "import sys; sys.path.insert(0,r'C:\desarrollo\fondos\proyecto1\core'); from dla_table_serializer import serialize_tables; pdf=open(r'c:\data\fondos\kiid\FR0000989626.pdf','rb').read(); t,m=serialize_tables(pdf, text='', debug=True)" > test1.txt 2>&1
type test1.txt

python -X utf8 -c "import sqlite3; conn=sqlite3.connect(r'C:\desarrollo\fondos\db\fondos.sqlite'); raw=conn.execute(\"SELECT Raw_KIID_Text FROM fund_kiid_metadata WHERE ISIN='FR0000989626'\").fetchone()[0] or ''; conn.close(); idx=raw.lower().find('costes a lo largo del tiempo|||'); print(repr(raw[idx:idx+400]) if idx>=0 else 'not found with that exact marker'); idx2=raw.lower().find('horizonte'); print(); print('around horizonte:', repr(raw[max(0,idx2-100):idx2+200]) if idx2>=0 else 'no horizonte')"

cd C:\desarrollo\fondos
set PYTHONPATH=proyecto1;proyecto1\core;shared
python -X utf8 proyecto1\scripts\diag\diag_cost_extraction.py --db C:\desarrollo\fondos\db\fondos.sqlite --kiid-dir C:\data\fondos\kiid --only-priips


python -X utf8 -c "import sqlite3; conn=sqlite3.connect(r'C:\desarrollo\fondos\db\fondos.sqlite'); rows=conn.execute('SELECT KIID_Status, COUNT(*) FROM fund_kiid_metadata GROUP BY KIID_Status ORDER BY 2 DESC').fetchall(); [print(r) for r in rows]"

C:\desarrollo\fondos>sqlite3 C:\desarrollo\fondos\db\fondos.sqlite "SELECT COUNT(*) FROM fund_master WHERE Sector_Focus IS NOT NULL AND Sector_Focus NOT IN ('Technology & Innovation','Healthcare & Life Sciences','Energy & Resources','Financial Services','Consumer','Materials & Mining','Utilities & Environment','Real Assets');"

findstr /N "DLA_TABLE_SERIALIZATION_ENABLED" C:\desarrollo\fondos\shared\config.py

python -X utf8 -c "import sqlite3; conn=sqlite3.connect(r'C:\desarrollo\fondos\db\fondos.sqlite'); r=conn.execute(\"SELECT KIID_Status, KIID_PDF_Hash, length(DLA2_Table_Text) FROM fund_kiid_metadata WHERE ISIN='ES0126547035'\").fetchone(); print(r)"
python -X utf8 -c "import sys; sys.path.insert(0,r'C:\desarrollo\fondos\proyecto1\core'); from dla_table_serializer import serialize_tables; pdf=open(r'c:\data\fondos\kiid\ES0126547035.pdf','rb').read(); t,m=serialize_tables(pdf,text='',debug=True)" 2>&1 | findstr "META"

python -X utf8 -c "import sys,sqlite3,re; sys.path.insert(0,r'C:\desarrollo\fondos\proyecto1'); sys.path.insert(0,r'C:\desarrollo\fondos\proyecto1\core'); sys.path.insert(0,r'C:\desarrollo\fondos\shared'); from core.cost_table_parser import TOTAL_COSTS_ROW, ACI_ROW; conn=sqlite3.connect(r'C:\desarrollo\fondos\db\fondos.sqlite'); rows=conn.execute(\"SELECT ISIN, DLA2_Table_Text FROM fund_kiid_metadata WHERE DLA2_Table_Text IS NOT NULL AND length(DLA2_Table_Text)>200\").fetchall(); conn.close(); [print(r[0], 'total_rows=', sum(1 for l in r[1].split(chr(10)) if TOTAL_COSTS_ROW.match(l.replace(chr(124)*3,'|').strip('|').split('|')[0].strip())), 'aci_rows=', sum(1 for l in r[1].split(chr(10)) if ACI_ROW.search(l))) for r in rows]"



cd C:\desarrollo\fondos\scripts\diag
set PYTHONPATH=C:\desarrollo\fondos\proyecto1;C:\desarrollo\fondos\proyecto1\core;C:\desarrollo\fondos\shared
python -X utf8 -c "import sqlite3; conn=sqlite3.connect(r'C:\desarrollo\fondos\db\fondos.sqlite'); isins=['LU0261952922','LU0781229069','DE000DWS0DZ7']; [(_:=print('='*50), print(i), [print(repr(l)) for l in (conn.execute('SELECT DLA2_Table_Text FROM fund_kiid_metadata WHERE ISIN=?',(i,)).fetchone() or [''])[0].split(chr(10)) if 'gesti' in l.lower() or 'composici' in l.lower() or 'operaci' in l.lower()]) for i in isins]; conn.close()"


cd C:\desarrollo\fondos\scripts\diag
python -X utf8 -c "import sqlite3; conn=sqlite3.connect(r'C:\desarrollo\fondos\db\fondos.sqlite'); t=(conn.execute(\"SELECT Raw_KIID_Text FROM fund_kiid_metadata WHERE ISIN='DE000DWS17J0'\").fetchone() or [''])[0] or ''; conn.close(); i=t.lower().find('composici'); print(repr(t[i:i+700]))"


python -X utf8 -c "import sys; sys.path.insert(0,r'C:\desarrollo\fondos\proyecto1'); sys.path.insert(0,r'C:\desarrollo\fondos\proyecto1\core'); sys.path.insert(0,r'C:\desarrollo\fondos\shared'); import core.cost_pct_anchored as c; import inspect; src=inspect.getsource(c); print('soport deployed:', 'soport' in src); print('BUYSELL_PAIR deployed:', '_BUYSELL_PAIR' in src)"


python -X utf8 -c "import sys; sys.path.insert(0,r'C:\desarrollo\fondos\proyecto1'); sys.path.insert(0,r'C:\desarrollo\fondos\proyecto1\core'); sys.path.insert(0,r'C:\desarrollo\fondos\shared'); import core.pipeline as p, config; import inspect; print('FIX-ARB-FALLBACK:', 'FIX-ARB-FALLBACK' in inspect.getsource(p)); print('DLA2_ARBITRATION_ENABLED:', getattr(config,'DLA2_ARBITRATION_ENABLED','MISSING'))"