"""
tests/test_batch_helpers.py -- scripts/launch/lib/batch_helpers.py: the logic behind lib\\common.bat's :get_time,
:tail and the standby read. Pure Python, no cmd.exe, no powercfg (any OS).
"""
from __future__ import annotations

import importlib.util
from datetime import datetime
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "batch_helpers", Path(__file__).resolve().parent.parent / "scripts" / "launch" / "lib" / "batch_helpers.py")
bh = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bh)


@pytest.mark.parametrize("net,expected", [
    ("yyyyMMdd_HHmmss", "20261004_153045"),
    ("HHmmss", "153045"),
    ("yyyy-MM-dd HH:mm:ss", "2026-10-04 15:30:45"),
    ("yyyyMMdd", "20261004"),
])
def test_dotnet_formats_become_the_same_stamp_the_powershell_calls_produced(net, expected):
    assert bh.now(net, datetime(2026, 10, 4, 15, 30, 45)) == expected


@pytest.mark.parametrize("bad", ["yyyyxx", "YYYYMMDD", "HHmmssfff", "%Y", ""])
def test_an_unsupported_format_is_rejected_instead_of_printing_a_wrong_stamp(bad):
    if bad == "":
        assert bh.strftime_format(bad) == ""                    # empty is harmless (prints an empty line)
    else:
        with pytest.raises(ValueError):
            bh.strftime_format(bad)


def test_cli_time_prints_a_stamp_and_rejects_a_bad_format(capsys):
    assert bh.main(["time", "yyyyMMdd_HHmmss"]) == 0
    out = capsys.readouterr().out.strip()
    assert len(out) == 15 and out[8] == "_" and out.replace("_", "").isdigit()
    assert bh.main(["time", "nope"]) == 2


_QUERY_ES = """GUID de plan de energia: 381b4222-f694-41f0-9685-ff5bb260df2e  (Equilibrado)
    GUID de configuracion de energia: 29f6c1db-86da-48c5-9fdb-f2b67b1f44da  (Suspender tras)
      Minima configuracion posible: 0x00000000
      Maxima configuracion posible: 0xffffffff
      Incremento de configuracion posible: 0x00000001
    Indice de configuracion de corriente alterna actual: 0x00000708
    Indice de configuracion de corriente continua actual: 0x00000258
"""


def test_standby_minutes_come_from_the_penultimate_hex_value_whatever_the_language():
    assert bh.parse_standby_minutes(_QUERY_ES) == 30                              # 0x708 = 1800 s
    english = _QUERY_ES.replace("Indice de configuracion de corriente alterna actual", "Current AC Power Setting Index")
    assert bh.parse_standby_minutes(english) == 30
    assert bh.parse_standby_minutes(_QUERY_ES.replace("0x00000708", "0x00000a8c")) == 45
    assert bh.parse_standby_minutes(_QUERY_ES.replace("0x00000708", "0x00000000")) == 0   # "never" is a real value


@pytest.mark.parametrize("text", ["", "no hex here", "only one 0x1"])
def test_unreadable_standby_output_is_unknown_not_zero(text):
    assert bh.parse_standby_minutes(text) is None


def test_a_missing_powercfg_is_unknown(tmp_path):
    assert bh.read_standby_minutes(str(tmp_path / "does_not_exist.exe")) is None


def test_tail_returns_the_last_lines_and_survives_a_missing_file(tmp_path):
    f = tmp_path / "log.txt"
    f.write_text("\n".join(f"line {i}" for i in range(1, 31)), encoding="utf-8")
    assert bh.tail(str(f), 3) == "line 28\nline 29\nline 30"
    assert bh.tail(str(f), 100).splitlines()[0] == "line 1"
    assert bh.tail(str(tmp_path / "nope.txt"), 5) == ""


def test_tail_tolerates_a_log_with_undecodable_bytes(tmp_path):
    f = tmp_path / "log.txt"
    f.write_bytes(b"ok line\n\xff\xfe broken\nlast\n")
    assert bh.tail(str(f), 2).splitlines()[-1] == "last"


def test_cli_tail_and_usage_errors(tmp_path, capsys):
    f = tmp_path / "a.log"
    f.write_text("x\ny\nz\n")
    assert bh.main(["tail", str(f), "2"]) == 0 and capsys.readouterr().out.split() == ["y", "z"]
    assert bh.main(["tail", str(tmp_path / "none.log")]) == 0 and capsys.readouterr().out == ""
    assert bh.main(["tail", str(f), "x"]) == 2
    assert bh.main(["frobnicate"]) == 2 and bh.main([]) == 2
