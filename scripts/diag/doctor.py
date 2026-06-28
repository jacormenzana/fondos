"""doctor.py — schema + imports health check."""
import sys, sqlite3
sys.path.insert(0, r'C:\desarrollo\fondos')
sys.path.insert(0, r'C:\desarrollo\fondos\proyecto1')
sys.path.insert(0, r'C:\desarrollo\fondos\proyecto1\core')
sys.path.insert(0, r'C:\desarrollo\fondos\shared')

DB = r'C:\desarrollo\fondos\db\fondos.sqlite'
PASS = '\033[92mPASS\033[0m'
FAIL = '\033[91mFAIL\033[0m'

results = []

def check(label, fn):
    try:
        msg = fn()
        results.append((True, label, msg or ''))
        print(f'  {PASS}  {label}' + (f'  — {msg}' if msg else ''))
    except Exception as e:
        results.append((False, label, str(e)))
        print(f'  {FAIL}  {label}  — {e}')

print('\n=== IMPORTS ===')

def imp_shared():
    import shared.config as cfg
    import shared.schema_checks as sc
    return f'config v{getattr(cfg,"VERSION","?")}, schema EXPECTED_V19={len(sc.EXPECTED_COLUMNS_V19)} cols'
check('shared.config + schema_checks', imp_shared)

def imp_ctp():
    import cost_table_parser  # noqa: F401
    return 'parse_costs_composition + parse_costs_over_time present'
check('cost_table_parser', imp_ctp)

def imp_pipeline():
    import pipeline  # noqa: F401
    return 'pipeline module OK'
check('pipeline', imp_pipeline)

def imp_classify():
    import classify_utils  # noqa: F401
    return 'classify_utils OK'
check('classify_utils', imp_classify)

def imp_arbitration():
    import cost_arbitration  # noqa: F401
    return 'cost_arbitration OK'
check('cost_arbitration', imp_arbitration)

def imp_writer():
    import sqlite_writer  # noqa: F401
    return 'sqlite_writer OK'
check('sqlite_writer', imp_writer)

def imp_dla():
    import dla_table_serializer  # noqa: F401
    return 'dla_table_serializer OK'
check('dla_table_serializer', imp_dla)

print('\n=== DATABASE ===')

def db_connect():
    conn = sqlite3.connect(DB)
    row = conn.execute("SELECT COUNT(*) FROM fund_master").fetchone()
    conn.close()
    return f'{row[0]} funds in fund_master'
check('DB connect + fund_master', db_connect)

def db_schema():
    from shared.schema_checks import verify_db_schema
    conn = sqlite3.connect(DB)
    result = verify_db_schema(conn)
    conn.close()
    missing = (result.get('missing_fund_master', [])
               + result.get('missing_kiid_meta', [])
               + result.get('missing_ingestion_log', []))
    if missing:
        raise ValueError(f'Missing columns: {missing}')
    return 'all tables aligned'
check('verify_db_schema', db_schema)

def db_v20_job_b():
    from shared.schema_checks import check_schema_v20_job_b
    conn = sqlite3.connect(DB)
    result = check_schema_v20_job_b(conn)
    conn.close()
    missing = result.get('missing', [])
    if missing:
        return f'WARN v20 Job B missing cols: {missing}'
    return f'v20 Job B columns present ({result.get("present", 0)} cols)'
check('schema v20 Job B', db_v20_job_b)

def db_kiid_coverage():
    conn = sqlite3.connect(DB)
    total = conn.execute("SELECT COUNT(*) FROM fund_master").fetchone()[0]
    with_kiid = conn.execute(
        "SELECT COUNT(*) FROM fund_master m "
        "JOIN fund_kiid_metadata k ON k.ISIN=m.ISIN"
    ).fetchone()[0]
    conn.close()
    pct = round(with_kiid / total * 100, 1) if total else 0
    return f'{with_kiid}/{total} funds have KIID metadata ({pct}%)'
check('KIID metadata coverage', db_kiid_coverage)

def db_cost_quality():
    conn = sqlite3.connect(DB)
    rows = conn.execute(
        "SELECT Cost_Extraction_Quality, COUNT(*) as n "
        "FROM fund_master GROUP BY Cost_Extraction_Quality ORDER BY n DESC"
    ).fetchall()
    conn.close()
    if not rows:
        return 'Cost_Extraction_Quality column empty'
    return '  '.join(f'{q or "NULL"}:{n}' for q, n in rows)
check('Cost_Extraction_Quality breakdown', db_cost_quality)

print('\n=== SUMMARY ===')
ok   = sum(1 for r in results if r[0])
fail = len(results) - ok
print(f'  {PASS if fail == 0 else FAIL}  {ok}/{len(results)} checks passed')
sys.exit(0 if fail == 0 else 1)
