"""FND-0290 -- the backup task registration keeps the logical dump independent of the external disk (static; no Task Scheduler, no WSL).

Before 2026-10-17 the only logical dump was step (2) of backup_offbox.sh, after its D: mount guard: with D: detached no dump was taken and
the newest dumps were 305 h old. The registration now has a daily dump task of its own, and the off-box task only copies (--no-dump).
These tests pin the pair: removing the dump task, or letting the off-box task dump again, must be a deliberate change.
"""
import re
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ops" / "register_backup_tasks.ps1"


def _tasks() -> dict:
    text = SCRIPT.read_text(encoding="utf-8")
    out = {}
    for m in re.finditer(r"@\{\s*Name\s*=\s*'([^']+)';\s*Days\s*=\s*(\d+);\s*At\s*=\s*'(\d\d:\d\d)';\s*Args\s*=\s*\"((?:[^\"]|\"\")*)\"\s*\}", text):
        out[m.group(1)] = {"days": int(m.group(2)), "at": m.group(3), "args": m.group(4).replace('""', '"')}
    return out


def test_the_three_backup_tasks_are_registered():
    assert set(_tasks()) == {"Fondos PG basebackup+prune", "Fondos PG logical dump", "Fondos PG offbox copy"}


def test_the_logical_dump_is_daily_and_does_not_touch_the_external_disk():
    t = _tasks()["Fondos PG logical dump"]
    assert t["days"] == 1 and t["args"].endswith("backup_live_pg.sh")
    assert "/mnt/d" not in t["args"] and "offbox" not in t["args"]


def test_the_offbox_copy_does_not_take_its_own_dump_and_runs_after_the_dump_task():
    tasks = _tasks()
    off, dump = tasks["Fondos PG offbox copy"], tasks["Fondos PG logical dump"]
    assert "--no-dump" in off["args"] and "--mirror-prune" in off["args"]
    assert dump["at"] < off["at"], "the copy must find a fresh dump set"


def test_backup_offbox_still_supports_no_dump():
    script = (SCRIPT.parent / "backup_offbox.sh").read_text(encoding="utf-8")
    assert "--no-dump) DUMP=0" in script
