"""Package L, P3-7 (doc/decisiones/PREREG_P3_7.md section 8): the frozen snapshot of the three DB-derived inputs of the trials (IPC, entry / exit fees, geography). P3-5A read them from the LIVE database at
the start of every trial and another session's cycle changed two of them during the chain (an IPC row, a bulk rewrite of fund_master); the lesson was to freeze them BEFORE the first run.
Invariants pinned: (1) a round trip returns the same frames (values, index, dtypes); (2) the snapshot id is a function of the CONTENT only (same inputs, same id; one changed value, another id); (3) a file
changed after the freeze is refused on load, and so is a missing file or a manifest that lacks one; (4) a snapshot is never overwritten; (5) the loaded fees frame has the shape `PitInputs.fees` expects.
Synthetic frames only (R-7): the live read (`read_live_inputs`) is a thin wrapper over loaders that have their own tests."""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto3.src import pit_p37_snapshot as S  # noqa: E402


def inputs():
    ipc = pd.DataFrame({"date": pd.to_datetime(["2020-01-31", "2020-02-29", "2020-03-31"]), "ipc_index": [100.0, 100.4, 100.9]})
    fees = pd.DataFrame({"Entry_Fee_Pct": [0.03, np.nan, 0.0], "Exit_Fee_Pct": [0.0, 0.01, np.nan]}, index=pd.Index(["A", "B", "C"], name="isin"))
    geo = pd.Series(["Europe", None, "USA"], index=pd.Index(["A", "B", "C"], name="isin"), name="Geography")
    return ipc, fees, geo


def test_the_round_trip_returns_the_same_frames(tmp_path):
    ipc, fees, geo = inputs()
    S.freeze_inputs(tmp_path / "snap", ipc, fees, geo)
    ipc2, fees2, geo2 = S.load_snapshot(tmp_path / "snap")
    pd.testing.assert_frame_equal(ipc2, ipc)
    pd.testing.assert_frame_equal(fees2, fees)
    pd.testing.assert_series_equal(geo2, geo)


def test_the_loaded_fees_frame_has_the_shape_pit_inputs_expects(tmp_path):
    ipc, fees, geo = inputs()
    S.freeze_inputs(tmp_path / "snap", ipc, fees, geo)
    _i, fees2, _g = S.load_snapshot(tmp_path / "snap")
    assert list(fees2.columns) == ["Entry_Fee_Pct", "Exit_Fee_Pct"] and fees2.index.name == "isin"


def test_the_snapshot_id_depends_on_the_content_and_nothing_else(tmp_path):
    ipc, fees, geo = inputs()
    m1 = S.freeze_inputs(tmp_path / "a", ipc, fees, geo)
    m2 = S.freeze_inputs(tmp_path / "b", ipc.copy(), fees.copy(), geo.copy())
    assert m1["snapshot_id"] == m2["snapshot_id"]                              # the timestamp is in the manifest, never in the id
    fees2 = fees.copy()
    fees2.loc["A", "Entry_Fee_Pct"] = 0.04
    m3 = S.freeze_inputs(tmp_path / "c", ipc, fees2, geo)
    assert m3["snapshot_id"] != m1["snapshot_id"]


def test_the_manifest_records_a_hash_a_row_count_and_a_utc_time_per_file(tmp_path):
    ipc, fees, geo = inputs()
    m = S.freeze_inputs(tmp_path / "snap", ipc, fees, geo)
    assert set(m["files"]) == {"ipc.parquet", "fees.parquet", "geography.parquet"}
    assert all(len(v["sha256"]) == 64 and v["rows"] > 0 for v in m["files"].values())
    assert m["frozen_utc"].endswith("Z")
    assert json.loads((tmp_path / "snap" / "manifest.json").read_text(encoding="utf-8"))["snapshot_id"] == m["snapshot_id"]


def test_a_file_changed_after_the_freeze_is_refused(tmp_path):
    ipc, fees, geo = inputs()
    S.freeze_inputs(tmp_path / "snap", ipc, fees, geo)
    other = fees.copy()
    other.loc["A", "Entry_Fee_Pct"] = 0.5
    other.to_parquet(tmp_path / "snap" / "fees.parquet")
    with pytest.raises(S.SnapshotMismatch, match="fees.parquet"):
        S.load_snapshot(tmp_path / "snap")


def test_a_missing_file_or_a_manifest_without_it_is_refused(tmp_path):
    ipc, fees, geo = inputs()
    S.freeze_inputs(tmp_path / "snap", ipc, fees, geo)
    (tmp_path / "snap" / "geography.parquet").unlink()
    with pytest.raises(S.SnapshotMismatch, match="geography.parquet"):
        S.load_snapshot(tmp_path / "snap")
    S.freeze_inputs(tmp_path / "snap2", ipc, fees, geo)
    mf = json.loads((tmp_path / "snap2" / "manifest.json").read_text(encoding="utf-8"))
    del mf["files"]["ipc.parquet"]
    (tmp_path / "snap2" / "manifest.json").write_text(json.dumps(mf), encoding="utf-8")
    with pytest.raises(S.SnapshotMismatch, match="ipc.parquet"):
        S.load_snapshot(tmp_path / "snap2")


def test_a_snapshot_is_never_overwritten(tmp_path):
    ipc, fees, geo = inputs()
    S.freeze_inputs(tmp_path / "snap", ipc, fees, geo)
    with pytest.raises(FileExistsError):
        S.freeze_inputs(tmp_path / "snap", ipc, fees, geo)


def test_a_folder_without_a_manifest_is_refused(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(S.SnapshotMismatch, match="manifest"):
        S.load_snapshot(tmp_path / "empty")
