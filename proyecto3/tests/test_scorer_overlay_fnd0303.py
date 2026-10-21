"""FND-0303: the scorer parameter overlay used by the PIT variants (V1 floor, V3 mandate limits) -- isolated, loud, never live."""
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from proyecto3.src import scorer_config as sc   # noqa: E402

V1 = {"MIN_REAL_RETURN_BY_SUB": {"Defensiva": 0.0, "Equilibrada": 0.0, "Dinamica": 0.0}}
V3 = {"MAX_DRAWDOWN_BY_SUB": {"Defensiva": -0.10, "Equilibrada": -0.15, "Dinamica": -0.20}}


@pytest.fixture
def no_env(monkeypatch):
    monkeypatch.delenv(sc.OVERLAY_ENV, raising=False)


def _write(tmp_path, data, name="ov.yaml"):
    p = tmp_path / name
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return p


def test_no_overlay_is_the_base_file(no_env):
    assert sc.overlay_path() is None
    base = sc.load_scorer_params()
    assert base == sc.load_scorer_params(sc.PARAMS_PATH)
    sc.assert_no_overlay("test")                      # does not raise


def test_overlay_changes_only_what_it_names(no_env, monkeypatch, tmp_path):
    base = sc.load_scorer_params()
    monkeypatch.setenv(sc.OVERLAY_ENV, str(_write(tmp_path, {**V1, **V3})))
    out = sc.load_scorer_params()
    assert out["MIN_REAL_RETURN_BY_SUB"] == V1["MIN_REAL_RETURN_BY_SUB"]
    assert out["MAX_DRAWDOWN_BY_SUB"] == V3["MAX_DRAWDOWN_BY_SUB"]
    for key in base:
        if key not in ("MIN_REAL_RETURN_BY_SUB", "MAX_DRAWDOWN_BY_SUB"):
            assert out[key] == base[key], key
    assert sc.params_hash(out) != sc.params_hash(base)          # the PIT cache key moves with the variant


def test_a_partial_dict_overlay_merges_instead_of_replacing(no_env, monkeypatch, tmp_path):
    base = sc.load_scorer_params()
    monkeypatch.setenv(sc.OVERLAY_ENV, str(_write(tmp_path, {"MAX_DRAWDOWN_BY_SUB": {"Defensiva": -0.10}})))
    out = sc.load_scorer_params()
    assert out["MAX_DRAWDOWN_BY_SUB"]["Defensiva"] == -0.10
    assert out["MAX_DRAWDOWN_BY_SUB"]["Equilibrada"] == base["MAX_DRAWDOWN_BY_SUB"]["Equilibrada"]


@pytest.mark.parametrize("bad", [{"NOT_A_PARAMETER": 1}, {"MAX_DRAWDOWN_BY_SUB": {"Typo": -0.1}}, {"MAX_DRAWDOWN_BY_SUB": -0.1}])
def test_a_typo_in_the_overlay_fails_loudly(no_env, monkeypatch, tmp_path, bad):
    monkeypatch.setenv(sc.OVERLAY_ENV, str(_write(tmp_path, bad)))
    with pytest.raises(ValueError, match="overlay"):
        sc.load_scorer_params()


def test_an_explicit_path_never_takes_the_overlay(no_env, monkeypatch, tmp_path):
    monkeypatch.setenv(sc.OVERLAY_ENV, str(_write(tmp_path, V3)))
    assert sc.load_scorer_params(sc.PARAMS_PATH)["MAX_DRAWDOWN_BY_SUB"] != V3["MAX_DRAWDOWN_BY_SUB"]


def test_the_variable_carries_an_absolute_existing_path_never_the_payload(no_env, monkeypatch, tmp_path):
    monkeypatch.setenv(sc.OVERLAY_ENV, "MAX_DRAWDOWN_BY_SUB: {Defensiva: -0.1}")        # a pasted payload is not a path
    with pytest.raises(ValueError, match="absolute path"):
        sc.load_scorer_params()
    monkeypatch.setenv(sc.OVERLAY_ENV, "relative/overlay.yaml")
    with pytest.raises(ValueError, match="absolute path"):
        sc.overlay_path()
    monkeypatch.setenv(sc.OVERLAY_ENV, str(tmp_path / "missing.yaml"))
    with pytest.raises(FileNotFoundError):
        sc.overlay_path()


def test_live_commands_refuse_while_the_overlay_is_set(no_env, monkeypatch, tmp_path):
    monkeypatch.setenv(sc.OVERLAY_ENV, str(_write(tmp_path, V1)))
    with pytest.raises(RuntimeError, match="PIT experiments only"):
        sc.assert_no_overlay("p3_build_portfolio")
    text = (ROOT / "scripts" / "launch" / "p3_build_portfolio.py").read_text(encoding="utf-8")
    assert "assert_no_overlay" in text
    assert "assert_no_overlay" in (ROOT / "scripts" / "launch" / "p3_promote_scenario.py").read_text(encoding="utf-8")


def _child_hash(env_extra):
    env = {k: v for k, v in os.environ.items() if k != sc.OVERLAY_ENV}
    env.update(env_extra)
    code = f"import sys; sys.path.insert(0, {str(ROOT)!r}); from proyecto3.src.scorer_config import SCORER_CONFIG_HASH; print(SCORER_CONFIG_HASH)"
    out = subprocess.run([sys.executable, "-I", "-c", code], env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_one_variant_cannot_leak_into_the_next_process(no_env, tmp_path):
    """The isolation the orchestrator relies on: each interpreter reads ITS OWN overlay and nothing carries over."""
    base = _child_hash({})
    v1 = _child_hash({sc.OVERLAY_ENV: str(_write(tmp_path, V1, "v1.yaml"))})
    v3 = _child_hash({sc.OVERLAY_ENV: str(_write(tmp_path, V3, "v3.yaml"))})
    assert len({base, v1, v3}) == 3
    assert _child_hash({}) == base                    # a later clean process is back on the base parameters
