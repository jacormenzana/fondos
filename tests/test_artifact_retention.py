"""FND-0268: the retention report is read-only and applies the documented rule (scripts/audit/artifact_retention.py)."""
import os
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "audit"))

import artifact_retention as ar  # noqa: E402

TODAY = date(2026, 10, 24)


def _touch(path: Path, day: date | None = None, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if day:
        ts = datetime(day.year, day.month, day.day, 12).timestamp()
        os.utime(path, (ts, ts))
    return path


def _names(paths) -> list:
    return sorted(p.name for p in paths)


def test_harvest_keeps_recent_and_newest_per_month(tmp_path):
    files = [_touch(tmp_path / n) for n in (
        "harvest_raw_20260601_000001.jsonl", "harvest_raw_20260601_000002.jsonl",   # same old month: only the newest stays
        "harvest_raw_20260915_100000.jsonl", "harvest_raw_20260915_110000.jsonl",   # inside 90 days: all stay
        "harvest_raw_20260301_000000.jsonl")]                                       # alone in its month: stays
    assert _names(ar.harvest_raw_candidates(files, TODAY, 90)) == ["harvest_raw_20260601_000001.jsonl"]


def test_harvest_newest_overall_is_never_a_candidate(tmp_path):
    files = [_touch(tmp_path / "harvest_raw_20240101_000000.jsonl")]
    assert ar.harvest_raw_candidates(files, TODAY, 90) == []


def test_incident_dir_removed_only_when_old_and_untouched(tmp_path):
    old = tmp_path / "incident_20250101"
    _touch(old / "a.log", date(2025, 1, 2))
    touched = tmp_path / "incident_20250101_b"
    _touch(touched / "a.log", date(2026, 10, 1))                                    # old name, recent file: stays
    recent = tmp_path / "incident_20261008"
    _touch(recent / "a.log", date(2026, 10, 8))
    got = ar.incident_candidates([old, touched, recent], TODAY, 180)
    assert [p.name for p in got] == ["incident_20250101"]


def test_diag_tmp_files_expire_sooner(tmp_path):
    tmp = _touch(tmp_path / "_tmp_5.csv", date(2026, 9, 1))                         # 53 days > 30
    keep = _touch(tmp_path / "cost_diag.csv", date(2026, 9, 1))                     # 53 days < 365
    old = _touch(tmp_path / "cost_diag_old.csv", date(2025, 1, 1))
    assert _names(ar.diag_candidates([tmp, keep, old], TODAY, "_tmp_", 30, 365)) == ["_tmp_5.csv", "cost_diag_old.csv"]


def test_report_never_deletes(tmp_path):
    _touch(tmp_path / "proyecto1" / "harvest" / "harvest_raw_20250101_000000.jsonl", date(2025, 1, 1))
    _touch(tmp_path / "proyecto1" / "harvest" / "harvest_raw_20250102_000000.jsonl", date(2025, 1, 2))
    before = sorted(p.as_posix() for p in tmp_path.rglob("*"))
    res = ar.report(tmp_path, TODAY)
    assert sorted(p.as_posix() for p in tmp_path.rglob("*")) == before
    assert res["harvest_raw"]["total"] == 2 and len(res["harvest_raw"]["candidates"]) == 1


def test_every_rule_has_its_parameters():
    assert {"harvest_raw", "incident", "diag"} <= set(ar.RETENTION)
    assert ar.RETENTION["harvest_raw"]["keep_all_days"] > 0 and ar.RETENTION["incident"]["keep_days"] > 0
