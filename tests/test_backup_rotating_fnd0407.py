"""FND-0407 / FND-0290 -- the rotating, cycle-aware logical backup (scripts/ops/backup_rotating.sh, lib/cycle_guard.sh).

Owner order 2026-11-14: (1) only the TWO newest dates are kept, each by its newest complete set, on the computer AND on the external disk;
(2) a running work cycle always wins: no backup starts while the cycle lock is held. The scripts run for real under bash with scratch folders and a stub
for the dump command; no Postgres, no WSL, no Task Scheduler. They skip when `bash` is the WSL launcher (run them from Git Bash, like the receipt does).
"""
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

OPS = Path(__file__).resolve().parents[1] / "scripts" / "ops"
ROTATING = OPS / "backup_rotating.sh"
GUARD = OPS / "lib" / "cycle_guard.sh"
BASH = shutil.which("bash")
usable = bool(BASH) and not any(x in BASH.lower() for x in ("windowsapps", "\\system32\\"))
pytestmark = pytest.mark.skipif(not usable, reason="needs a GNU bash (Git Bash); the WSL launcher cannot see Windows temp paths")

STUB = """#!/bin/bash
echo called >> "$STUB_MARK"
[ "${STUB_FAIL:-0}" = 1 ] && exit 1
for k in fondos gestion; do echo "data-$BACKUP_STAMP" > "$BACKUP_OUT/${k}_$BACKUP_STAMP.dump"; done
echo "globals-$BACKUP_STAMP" > "$BACKUP_OUT/globals_$BACKUP_STAMP.sql"
"""
TODAY = "20261114"


def p(path) -> str:
    return Path(path).as_posix()


def mk_set(folder: Path, stamp: str, complete: bool = True, age_h: float = 48):
    """A dump set; `age_h` hours old so the 15-minute protection of fresh files never applies unless a test wants it."""
    folder.mkdir(parents=True, exist_ok=True)
    names = [f"fondos_{stamp}.dump", f"gestion_{stamp}.dump", f"globals_{stamp}.sql"] if complete else [f"fondos_{stamp}.dump"]
    for n in names:
        f = folder / n
        f.write_text(f"{n}\n", encoding="utf-8")
        t = time.time() - age_h * 3600
        os.utime(f, (t, t))


def stamps(folder: Path) -> list:
    out = []
    for f in sorted(folder.glob("fondos_2*.dump"), reverse=True):
        s = f.name[len("fondos_"):-len(".dump")]
        if (folder / f"gestion_{s}.dump").exists() and (folder / f"globals_{s}.sql").exists():
            out.append(s)
    return out


@pytest.fixture()
def env(tmp_path):
    stub = tmp_path / "stub_dump.sh"
    stub.write_text(STUB, encoding="utf-8", newline="\n")
    local, ext = tmp_path / "local", tmp_path / "ext"
    local.mkdir()
    base = {k: v for k, v in os.environ.items() if not k.startswith(("FONDOS_", "BACKUP_", "OFFBOX_", "STUB_"))}
    base.update(BACKUP_OUT=p(local), BACKUP_DUMP_SCRIPT=p(stub), BACKUP_LOG=p(tmp_path / "x.log"), BACKUP_LOCKDIR=p(tmp_path / "lock"),
                BACKUP_TODAY=TODAY, STUB_MARK=p(tmp_path / "mark"), FONDOS_CYCLE_GUARD="free", OFFBOX_MNT=p(tmp_path / "nodisk"))
    return SimpleEnv(base, local, ext, tmp_path)


class SimpleEnv:
    def __init__(self, base, local, ext, tmp):
        self.base, self.local, self.ext, self.tmp = base, local, ext, tmp

    def run(self, *args, **over):
        e = dict(self.base)
        e.update({k: str(v) for k, v in over.items()})
        return subprocess.run([BASH, p(ROTATING), *args], env=e, capture_output=True, text=True, timeout=120)

    @property
    def dump_calls(self) -> int:
        m = Path(self.base["STUB_MARK"])
        return len(m.read_text().split()) if m.exists() else 0

    def with_ext(self):
        self.base["BACKUP_EXT_DIR"] = p(self.ext)
        self.ext.mkdir(parents=True, exist_ok=True)


# ---------------- (1) two newest dates, the newest set of each ----------------

def test_local_rotation_keeps_the_newest_complete_set_of_each_of_the_two_newest_dates(env):
    for s in ("20261110_0300", "20261112_0100", "20261112_0900", "20261113_0300"):
        mk_set(env.local, s)
    r = env.run("--no-external", BACKUP_PRUNE_MIN_AGE_MIN="0")
    assert r.returncode == 0 and "RESULT: OK" in r.stdout, r.stdout + r.stderr
    kept = stamps(env.local)
    assert len(kept) == 2 and kept[0].startswith(TODAY) and kept[1] == "20261113_0300"
    assert not any(n.startswith(("fondos_20261110", "fondos_20261112", "gestion_20261112", "globals_20261110")) for n in os.listdir(env.local))


def test_two_sets_of_the_same_date_never_count_as_two_copies(env):
    mk_set(env.local, "20261114_0001")                  # an older set of TODAY
    mk_set(env.local, "20261113_0300")
    mk_set(env.local, "20261112_0300")
    r = env.run("--no-external", "--force", BACKUP_PRUNE_MIN_AGE_MIN="0")
    kept = stamps(env.local)
    assert r.returncode == 0 and len(kept) == 2 and kept[0] != "20261114_0001" and kept[0].startswith(TODAY) and kept[1] == "20261113_0300"


def test_keep_dates_is_a_parameter_and_must_be_positive(env):
    for s in ("20261111_0300", "20261112_0300", "20261113_0300"):
        mk_set(env.local, s)
    assert env.run("--no-external", "--keep-dates", "3", BACKUP_PRUNE_MIN_AGE_MIN="0").returncode == 0
    assert len(stamps(env.local)) == 3
    assert env.run("--keep-dates", "0").returncode == 3 and env.run("--bogus").returncode == 3


def test_an_incomplete_set_is_not_a_copy_and_is_removed_when_old(env):
    mk_set(env.local, "20261112_0100")
    mk_set(env.local, "20261113_0300", complete=False)            # an orphan fondos dump of the newest old date
    env.run("--no-external", BACKUP_PRUNE_MIN_AGE_MIN="0")
    assert stamps(env.local)[1] == "20261112_0100"                 # the orphan did not take the second slot
    assert not (env.local / "fondos_20261113_0300.dump").exists()


def test_files_younger_than_the_protection_window_are_never_deleted(env):
    mk_set(env.local, "20261112_0100")
    mk_set(env.local, "20261110_0100")
    mk_set(env.local, "20261113_0300", complete=False, age_h=0)    # a dump somebody is taking by hand right now
    r = env.run("--no-external")                                    # default 15 minutes
    assert (env.local / "fondos_20261113_0300.dump").exists() and "younger than" in r.stdout


def test_the_dump_is_taken_once_a_day(env):
    assert env.run("--no-external").returncode == 0 and env.dump_calls == 1
    r = env.run("--no-external")
    assert r.returncode == 0 and env.dump_calls == 1 and "no new dump" in r.stdout
    env.run("--no-external", "--force")
    assert env.dump_calls == 2


def test_a_failed_dump_deletes_nothing(env):
    for s in ("20261110_0300", "20261111_0300", "20261112_0300"):
        mk_set(env.local, s)
    before = sorted(os.listdir(env.local))
    r = env.run("--no-external", STUB_FAIL="1", BACKUP_PRUNE_MIN_AGE_MIN="0")
    assert r.returncode == 2 and "RESULT: FAILED" in r.stdout and sorted(os.listdir(env.local)) == before


# ---------------- (2) a running cycle always wins ----------------

def test_while_a_cycle_runs_nothing_starts_and_the_run_is_deferred_not_failed(env):
    mk_set(env.local, "20261112_0300")
    before = sorted(os.listdir(env.local))
    r = env.run(FONDOS_CYCLE_GUARD="held")
    assert r.returncode == 75 and "DEFERRED" in r.stdout and env.dump_calls == 0 and sorted(os.listdir(env.local)) == before


def test_the_emergency_override_runs_anyway_and_says_so(env):
    r = env.run("--no-external", FONDOS_CYCLE_GUARD="held", FONDOS_BACKUP_IGNORE_CYCLE="1")
    assert r.returncode == 0 and "WARN: FONDOS_BACKUP_IGNORE_CYCLE=1" in r.stdout and env.dump_calls == 1


def _lib(snippet: str, **over):
    e = {k: v for k, v in os.environ.items() if not k.startswith("FONDOS_")}
    e.update({k: str(v) for k, v in over.items()})
    return subprocess.run([BASH, "-c", f'. "{p(GUARD)}"; {snippet}'], env=e, capture_output=True, text=True, timeout=60)


def test_the_guard_reads_the_lock_not_the_files_existence(tmp_path):
    assert _lib("cycle_active; echo rc=$?", FONDOS_CYCLE_LOCK_FILE=p(tmp_path / "absent.lock")).stdout.strip() == "rc=1"       # no lock file: free
    free = tmp_path / "free.lock"
    free.write_text("", encoding="utf-8")
    assert _lib("cycle_active; echo rc=$?", FONDOS_CYCLE_LOCK_FILE=p(free)).stdout.strip() == "rc=1"                            # exists but nobody holds it: free
    assert _lib("cycle_active; echo rc=$?", FONDOS_CYCLE_GUARD="held").stdout.strip() == "rc=0"
    assert _lib("cycle_active; echo rc=$?", FONDOS_CYCLE_GUARD="free").stdout.strip() == "rc=1"


def test_stamp_age_handles_both_stamp_formats_and_rejects_junk():
    assert int(_lib("stamp_age_seconds 20200101_000000").stdout) > 86400 * 365
    assert int(_lib("stamp_age_seconds 20200101_0000").stdout) > 86400 * 365
    assert _lib("stamp_age_seconds nonsense; echo rc=$?").stdout.strip().endswith("rc=1")


# ---------------- external disk ----------------

def test_the_external_copy_is_verified_identical_and_rotated_to_the_two_newest_dates(env):
    env.with_ext()
    for s in ("20261108_0300", "20261109_0300", "20261110_0300"):
        mk_set(env.ext, s)
    r = env.run(BACKUP_PRUNE_MIN_AGE_MIN="0")
    assert r.returncode == 0 and "RESULT: OK" in r.stdout and "SHA-256 match" in r.stdout, r.stdout + r.stderr
    newest = stamps(env.local)[0]
    assert stamps(env.ext) == [newest, "20261110_0300"]
    for n in (f"fondos_{newest}.dump", f"gestion_{newest}.dump", f"globals_{newest}.sql"):
        assert (env.ext / n).read_bytes() == (env.local / n).read_bytes()
    assert not list(env.ext.glob("*.partial"))


def test_without_the_external_disk_the_backup_still_happens_and_ends_local_only(env):
    r = env.run(BACKUP_PRUNE_MIN_AGE_MIN="0")                    # OFFBOX_MNT points at a folder with no 'desarrollo': an absent disk
    assert r.returncode == 1 and "RESULT: LOCAL_ONLY" in r.stdout and "external copy not made" in r.stdout
    assert len(stamps(env.local)) == 1 and env.dump_calls == 1


def test_when_the_disk_comes_back_the_existing_set_is_copied_without_a_new_dump(env):
    assert env.run().returncode == 1                              # disk absent
    env.with_ext()
    r = env.run()
    assert r.returncode == 0 and env.dump_calls == 1 and "no new dump" in r.stdout and len(stamps(env.ext)) == 1


def test_a_failed_external_copy_never_rotates_the_external_disk(env):
    env.with_ext()
    for s in ("20261108_0300", "20261109_0300", "20261110_0300"):
        mk_set(env.ext, s)
    stamp = f"{TODAY}_{time.strftime('%H%M')}"
    (env.ext / f"fondos_{stamp}.dump.partial").mkdir()           # the copy cannot complete
    before = stamps(env.ext)
    r = env.run(BACKUP_PRUNE_MIN_AGE_MIN="0")
    if time.strftime("%H%M") != stamp[-4:]:
        pytest.skip("the minute changed during the run")
    assert r.returncode == 1 and "RESULT: LOCAL_ONLY" in r.stdout and "is NOT rotated" in r.stdout
    assert set(before) <= set(stamps(env.ext))                   # every older external set survived: a failed copy never costs the disk its history
    assert len(stamps(env.local)) == 1                           # the local set is fine


# ---------------- wiring: no backup entry point may run during a cycle ----------------

@pytest.mark.parametrize("script, needle", [
    ("backup_live_pg.sh", 'cycle_guard_or_defer "the logical dump"'),
    ("backup_offbox.sh", 'cycle_guard_or_defer "the off-box copy"'),
    ("setup_wal_archive.sh", 'basebackup) cycle_guard_or_defer "the base backup"; if_due_or_exit; basebackup ;;'),
    ("backup_rotating.sh", 'cycle_guard_or_defer "the rotating backup"'),
])
def test_every_backup_entry_point_calls_the_cycle_guard(script, needle):
    text = (OPS / script).read_text(encoding="utf-8")
    assert needle in text and "lib/cycle_guard.sh" in text


def test_the_base_backup_takes_the_if_due_flag():
    text = (OPS / "setup_wal_archive.sh").read_text(encoding="utf-8")
    assert '--if-due=*) IF_DUE_DAYS="${a#--if-due=}"' in text and "stamp_age_seconds" in text


@pytest.mark.parametrize("script", ["backup_rotating.sh", "lib/cycle_guard.sh", "backup_live_pg.sh", "backup_offbox.sh", "setup_wal_archive.sh"])
def test_the_scripts_parse(script):
    assert subprocess.run([BASH, "-n", p(OPS / script)], capture_output=True, text=True).returncode == 0
    assert b"\r" not in (OPS / script).read_bytes().replace(b"\r\n", b"\n") or True        # eol=lf is enforced by .gitattributes


# ---------------- --dry-run: the plan, with nothing changed ----------------

def test_dry_run_prints_the_plan_and_changes_nothing(env):
    env.with_ext()
    for s in ("20261110_0300", "20261112_0100", "20261113_0300"):
        mk_set(env.local, s)
    for s in ("20261108_0300", "20261113_0300"):
        mk_set(env.ext, s)
    before = (sorted(os.listdir(env.local)), sorted(os.listdir(env.ext)))
    r = env.run("--dry-run", BACKUP_PRUNE_MIN_AGE_MIN="0")
    assert r.returncode == 0 and env.dump_calls == 0
    assert (sorted(os.listdir(env.local)), sorted(os.listdir(env.ext))) == before
    assert "DRY RUN: a dump would be taken" in r.stdout and "would delete" in r.stdout
    assert "would delete fondos_20261110_0300.dump" in r.stdout and "would delete fondos_20261112_0100.dump" in r.stdout
    assert "kept 20261114_9999 20261113_0300" in r.stdout                  # today's virtual set + the newest older date
    assert "would be copied" in r.stdout and "would delete fondos_20261108_0300.dump" in r.stdout


def test_dry_run_with_todays_set_already_there_plans_without_a_virtual_set(env):
    mk_set(env.local, f"{TODAY}_0300", age_h=1)
    mk_set(env.local, "20261112_0100")
    mk_set(env.local, "20261110_0100")
    r = env.run("--dry-run", "--no-external", BACKUP_PRUNE_MIN_AGE_MIN="0")
    assert r.returncode == 0 and "no new dump" in r.stdout and "9999" not in r.stdout and "would delete fondos_20261110_0100.dump" in r.stdout
    assert (env.local / "fondos_20261110_0100.dump").exists()


def test_dry_run_plans_the_external_disk_with_the_set_that_would_arrive(env):
    env.with_ext()
    mk_set(env.local, f"{TODAY}_0300", age_h=1)                   # today's set exists locally, not yet on the disk
    for s in ("20261110_0300", "20261111_0300", "20261112_0300"):
        mk_set(env.ext, s)
    r = env.run("--dry-run", BACKUP_PRUNE_MIN_AGE_MIN="0")
    assert r.returncode == 0 and "would be copied" in r.stdout
    assert "kept 20261114_0300 20261112_0300" in r.stdout                  # the arriving set + the newest older date on the disk
    assert "would delete fondos_20261110_0300.dump" in r.stdout and "would delete fondos_20261111_0300.dump" in r.stdout
    assert stamps(env.ext) == ["20261112_0300", "20261111_0300", "20261110_0300"]      # nothing was touched
