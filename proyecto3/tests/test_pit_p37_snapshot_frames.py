"""Package L, P3-8 (doc/decisiones/PREREG_P3_8_CPI_REGIME_INPUT.md): the generic frozen snapshot of named frames (`pit_p37_snapshot.freeze_frames` / `load_frames`), used to freeze the regime histories of the four
arms and the CPI index levels BEFORE the first run (the lesson of P3-5A: inputs read live while a chain runs can change under it). Same guarantees as the typed snapshot of P3-7: a round trip returns the same
frames with their index, the id depends on the content only, a changed / missing / unlisted file or a missing manifest is refused, and a snapshot is never overwritten. Synthetic frames only (R-7)."""
import sys
from pathlib import Path

import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from proyecto3.src import pit_p37_snapshot as S  # noqa: E402


def frames():
    idx = pd.date_range("2020-01-31", periods=4, freq="ME")
    return {"hist_R0.parquet": pd.DataFrame({"regime": ["A", "A", "B", "B"], "w": [0.2, 0.2, 0.5, 0.5]}, index=idx),
            "levels.parquet": pd.DataFrame({"ES": [100.0, 101.0, 102.0, 103.0], "EU": [100.0, 100.5, 101.0, 101.5]}, index=idx)}


def test_generic_frames_round_trip_with_their_index(tmp_path):
    f = frames()
    m = S.freeze_frames(tmp_path / "g", f)
    assert set(m["files"]) == set(f) and len(m["snapshot_id"]) == 64
    back = S.load_frames(tmp_path / "g")
    assert set(back) == set(f)
    for k in f:
        pd.testing.assert_frame_equal(back[k], f[k], check_freq=False)                       # parquet keeps the dates, not the frequency attribute


def test_generic_frames_refuse_a_changed_a_missing_file_a_missing_manifest_and_an_overwrite(tmp_path):
    f = frames()
    S.freeze_frames(tmp_path / "g", f)
    with pytest.raises(FileExistsError):
        S.freeze_frames(tmp_path / "g", f)
    g = f["levels.parquet"].copy()
    g.iloc[0, 0] = 99.0
    g.to_parquet(tmp_path / "g" / "levels.parquet")
    with pytest.raises(S.SnapshotMismatch, match="levels.parquet"):
        S.load_frames(tmp_path / "g")
    S.freeze_frames(tmp_path / "h", f)
    (tmp_path / "h" / "hist_R0.parquet").unlink()
    with pytest.raises(S.SnapshotMismatch, match="hist_R0.parquet"):
        S.load_frames(tmp_path / "h")
    (tmp_path / "empty").mkdir()
    with pytest.raises(S.SnapshotMismatch, match="manifest"):
        S.load_frames(tmp_path / "empty")


def test_generic_snapshot_id_depends_on_content_only(tmp_path):
    f = frames()
    a, b = S.freeze_frames(tmp_path / "a", f), S.freeze_frames(tmp_path / "b", {k: v.copy() for k, v in f.items()})
    assert a["snapshot_id"] == b["snapshot_id"]
    f2 = {k: v.copy() for k, v in f.items()}
    f2["levels.parquet"].iloc[0, 0] = 100.5
    assert S.freeze_frames(tmp_path / "c", f2)["snapshot_id"] != a["snapshot_id"]
