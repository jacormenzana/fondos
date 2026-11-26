"""FND-0441 Step 3: where a stored Geography comes from (`fund_master.Geography_Source`).

One function (`classify_utils.derive_geography_source`) answers it for the pipeline at the end of a pass and for the one-off backfill: the signal that REPRODUCES the stored value.
'default' = a `Global` that no signal supports; 'unreproduced' = any other value no signal supports; 'wrong_doc' = the document is not the fund's."""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from proyecto1.core import classify_utils as cu

PIPELINE = (ROOT / "proyecto1" / "core" / "pipeline.py").read_text(encoding="utf-8")
WRITER = (ROOT / "proyecto1" / "core" / "fund_writer.py").read_text(encoding="utf-8")


def doc(sentence: str) -> str:
    return "DOCUMENTO DE DATOS FUNDAMENTALES. ¿Qué es este producto? Objetivos " + sentence + " Indicador de riesgo. Costes."


def source(geography, text="", name="FUND X", benchmark=None, nature="Renta Fija Flexible", theme=None, status=None):
    return cu.derive_geography_source(geography, text, name, benchmark, nature, theme, status)


def kiid_text(sentence: str) -> str:
    return "x" * 250 + " " + sentence           # inside the extractor's default objective window (test_geography._kiid)


MANDATE_ONLY = doc("El Subfondo invierte al menos el 67 % de sus activos en instrumentos de deuda emitidos o garantizados por Gobiernos de la OCDE.")
SILENT = doc("El Subfondo invierte en bonos denominados en euros.")


def test_the_fixtures_reach_the_signal_they_are_meant_to():
    assert cu.detect_geography_from_kiid(MANDATE_ONLY) is None and cu.detect_geography_from_mandate(MANDATE_ONLY) == "Global"
    assert cu.detect_geography_from_kiid(SILENT) is None and cu.detect_geography_from_mandate(SILENT) is None
    assert cu.detect_geography("fund x") is None and cu.detect_geography("fund japan x") == "Japón"


def test_no_value_has_no_source():
    assert source(None, MANDATE_ONLY) is None
    assert source("", MANDATE_ONLY) is None
    assert source(None, MANDATE_ONLY, status="WRONG_DOC") is None


def test_wrong_document_beats_every_signal():
    assert source("Japan", doc("x"), name="FUND JAPAN X", status="WRONG_DOC") == "wrong_doc"
    assert source("Japan", doc("x"), name="FUND JAPAN X", status="CACHED") == "name"


def test_each_signal_is_named_when_it_reproduces_the_value():
    kiid = kiid_text("el fondo invierte principalmente en europa en renta variable.")
    assert cu.detect_geography_from_kiid(kiid) == "Europa"
    assert source("Europe", kiid) == "kiid"
    assert source("Japan", SILENT, name="FUND JAPAN X") == "name"
    assert source("Global", MANDATE_ONLY) == "mandate"
    assert source("North America", SILENT, benchmark="msci usa index", nature="Renta Variable") == "index"


def test_the_kiid_extractor_comes_before_the_name_when_both_say_it():
    kiid = kiid_text("el fondo invierte principalmente en europa en renta variable.")
    assert source("Europe", kiid, name="FUND EUROPE X") == "kiid"


def test_a_later_signal_is_credited_when_the_first_one_says_something_else():
    kiid = kiid_text("el fondo invierte principalmente en europa en renta variable.")
    assert source("Japan", kiid, name="FUND JAPAN X") == "name"


def test_values_compare_in_the_stored_vocabulary():
    emerging = doc("El fondo invierte principalmente en acciones de mercados emergentes.")
    assert cu._derive_geography_en(cu.detect_geography_from_kiid(emerging) or cu.detect_geography_from_mandate(emerging), "fund x") == "Global"
    assert source("Global", emerging) in ("kiid", "mandate")


def test_global_without_any_signal_is_the_default():
    assert source("Global", SILENT) == "default"
    assert source("Global", None) == "default"
    assert source("Global", "") == "default"


def test_a_value_no_signal_reproduces_is_unreproduced():
    assert source("Europe", SILENT) == "unreproduced"                                   # a named region from an old fallback
    assert source("Global", SILENT, name="FUND JAPAN X") == "unreproduced"              # 'Global' contradicted by a signal is not a plain default


def test_the_index_signal_keeps_its_scope_and_reads_the_theme_from_the_name():
    assert source("North America", SILENT, benchmark="msci usa index", nature="Renta Fija Flexible") == "unreproduced"
    assert source("North America", SILENT, benchmark="msci usa index", nature="Renta Variable", theme="Technology") == "unreproduced"


def test_every_answer_belongs_to_the_closed_vocabulary():
    assert cu.GEOGRAPHY_SOURCES == ("kiid", "name", "mandate", "index", "default", "unreproduced", "wrong_doc")


# ---- wiring ----------------------------------------------------------------------------------------------------------------------------------------------------------------------
def test_pipeline_derives_the_source_on_the_effective_value_just_before_publishing():
    publish = PIPELINE.index("publish_fund(conn, fund_master_record, None, kiid_record,")
    call = PIPELINE.index('fund_master_record["Geography_Source"] = derive_geography_source(')
    assert call < publish
    block = PIPELINE[call:publish]
    for needed in ('eff.get("Geography", fund_master_record)', 'eff.get("Benchmark_Declared", fund_master_record)', 'eff.get("Fund_Nature", fund_master_record)',
                   'eff.get("Theme", fund_master_record)', 'kiid_meta.get("KIID_Status")', "kiid_text"):
        assert needed in block, needed
    assert 'fund_master_record["Geography"] =' not in PIPELINE[call:publish], "no rule may change Geography after its source was derived"
    assert "derive_geography_source," in PIPELINE[:PIPELINE.index("def ")], "imported with the other classify_utils names"


def test_the_writer_keeps_the_source_under_the_same_coalesce_as_geography():
    assert re.search(r'\("Geography",\s+record\.get\("Geography"\),\s+\'co\'\)', WRITER)
    assert re.search(r'\("Geography_Source",\s+record\.get\("Geography_Source"\),\s+\'co\'\)', WRITER)


def test_schema_declares_the_column_with_the_same_vocabulary():
    from shared import schema_checks
    assert "Geography_Source" in schema_checks.FUND_MASTER_COLUMNS_CURRENT_SET
    for rel in ("db/pg/20_silver.sql", "db/migrations/20261126_fund_master_geography_source.sql"):
        sql = (ROOT / rel).read_text(encoding="utf-8")
        found = re.search(r"geography_source\s+IN\s*\(([^)]*)\)", sql)
        assert found, rel
        assert tuple(re.findall(r"'([a-z_]+)'", found.group(1))) == cu.GEOGRAPHY_SOURCES, rel
    assert "Geography_Source: geography_source" in (ROOT / "db" / "pg" / "rename_map.yaml").read_text(encoding="utf-8")


@pytest.mark.parametrize("rel", ["scripts/mig/fondos/migrate_fund_master_geography_source.py", "scripts/mig/fondos/backfill_geography_source.py"])
def test_the_one_off_scripts_default_to_a_dry_run(rel):
    src = (ROOT / rel).read_text(encoding="utf-8")
    assert '"--apply"' in src and "store_true" in src and "DRY RUN" in src


def test_the_backfill_writes_only_the_source_column():
    src = (ROOT / "scripts" / "mig" / "fondos" / "backfill_geography_source.py").read_text(encoding="utf-8")
    updates = re.findall(r"UPDATE\s+silver\.fund_master\s+SET\s+(.*?)\s+WHERE", src, flags=re.IGNORECASE | re.DOTALL)
    assert updates and all(re.fullmatch(r"geography_source = %s", u.strip()) for u in updates)


# ---- report: an unevidenced Geography is not printed as a fact ---------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("value, src, printed", [
    ("Global", "default", "not determined"), ("Europe", "wrong_doc", "not determined"), ("Global", "mandate", "Global"), ("Europe", "unreproduced", "Europe"),
    ("Global", None, "Global"), (None, None, None),
])
def test_report_rule_for_the_geography_cell(value, src, printed):
    import sqlite3                                                                     # in-memory evaluation of a portable CASE expression; no database of the project
    from proyecto3.src.monthly_report import GEOGRAPHY_REPORT_SQL
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE fm (Geography TEXT, Geography_Source TEXT)")
    db.execute("INSERT INTO fm VALUES (?, ?)", (value, src))
    assert db.execute(f"SELECT {GEOGRAPHY_REPORT_SQL} FROM fm AS fm").fetchone()[0] == printed


def test_report_query_uses_the_rule():
    src = (ROOT / "proyecto3" / "src" / "monthly_report.py").read_text(encoding="utf-8")
    assert "{GEOGRAPHY_REPORT_SQL}," in src and "\n               fm.Geography,\n" not in src
