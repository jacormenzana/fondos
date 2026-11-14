"""FND-0290 / FND-0407 -- the backup task registration (static; no Task Scheduler, no WSL).

Before 2026-10-17 the only logical dump was step (2) of backup_offbox.sh, after its D: mount guard: with D: detached no dump was taken and the newest dumps were
305 h old. The registration has a dump task of its own, and the off-box task only copies (--no-dump).
Since 2026-11-14 (owner order): the dump task is the ROTATING, cycle-aware script (only the two newest dates are kept), and every task repeats hourly so a run
deferred by a work cycle happens in the first free hour. These tests pin that: dropping the rotating task, putting back the direct dump (keep 7), or letting
the off-box task dump again must be a deliberate change.
"""
import re
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "mnt" / "sched" / "register_backup_tasks.ps1"
ROTATING, BASE, OFFBOX = "Fondos PG backup (rotating)", "Fondos PG basebackup+prune", "Fondos PG offbox copy"


def _tasks() -> dict:
    text = SCRIPT.read_text(encoding="utf-8")
    out = {}
    pat = (r"@\{\s*Name\s*=\s*'([^']+)';\s*Days\s*=\s*(\d+);\s*At\s*=\s*'(\d\d:\d\d)';\s*RepeatMin\s*=\s*(\d+);\s*ForHours\s*=\s*(\d+);\s*"
           r"Args\s*=\s*\"((?:[^\"]|\"\")*)\"\s*\}")
    for m in re.finditer(pat, text):
        out[m.group(1)] = {"days": int(m.group(2)), "at": m.group(3), "repeat_min": int(m.group(4)), "for_hours": int(m.group(5)),
                           "args": m.group(6).replace('""', '"')}
    return out


def test_the_three_backup_tasks_are_registered():
    assert set(_tasks()) == {BASE, ROTATING, OFFBOX}


def test_the_old_direct_dump_task_is_removed_on_register_so_two_tasks_never_dump():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "$legacy = @('Fondos PG logical dump')" in text and "removed (superseded)" in text
    assert "Fondos PG logical dump" not in _tasks()


def test_the_dump_task_is_the_rotating_script_and_does_not_touch_the_external_disk_itself():
    t = _tasks()[ROTATING]
    assert t["days"] == 1 and t["args"].endswith("backup_rotating.sh")
    assert "/mnt/d" not in t["args"] and "offbox" not in t["args"] and "backup_live_pg.sh" not in t["args"]


def test_the_offbox_copy_does_not_take_its_own_dump_and_runs_after_the_dump_task():
    tasks = _tasks()
    off, dump = tasks[OFFBOX], tasks[ROTATING]
    assert "--no-dump" in off["args"] and "--mirror-prune" in off["args"]
    assert dump["at"] < off["at"], "the copy must find a fresh dump set"


def test_every_task_repeats_hourly_so_a_run_deferred_by_a_cycle_happens_by_itself():
    for name, t in _tasks().items():
        assert t["repeat_min"] == 60 and t["for_hours"] >= 8, name
    text = SCRIPT.read_text(encoding="utf-8")
    assert "$trigger.Repetition = $repeat.Repetition" in text and "-MultipleInstances IgnoreNew" in text


def test_the_base_backup_is_only_taken_when_due_so_the_repetition_never_makes_extra_bases():
    args = _tasks()[BASE]["args"]
    assert "basebackup --if-due=3" in args and "prune --yes" in args and "&&" in args


def test_the_registration_says_the_cycle_priority_rule():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "RUNNING WORK CYCLE ALWAYS WINS" in text and "exits 75 = DEFERRED" in text


def test_backup_offbox_still_supports_no_dump():
    script = (SCRIPT.parent.parent / "backup" / "backup_offbox.sh").read_text(encoding="utf-8")
    assert "--no-dump) DUMP=0" in script
