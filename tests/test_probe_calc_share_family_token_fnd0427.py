"""FND-0427: the `calc_share` closing condition must count the family-versioned stamps `<CALC_VERSION>.<override>` as recomputed. Live 2026-11-17: 1,339,733 rows were stamped
`20261116.20261019` and 177,684 `20261116`; the exact-match probe said 11.6 % of a 99.5 % recomputed table. No database: a fake connection that evaluates the two parameters."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "gov" / "backlog"))
import rfd_matrix_probe as probe  # noqa: E402

CALC = re.search(r'CALC_VERSION\s*(?::\s*str\s*)?=\s*["\'](\d+)["\']', (ROOT / "proyecto2/src/pipeline/run_pipeline.py").read_text(encoding="utf-8")).group(1)


class _Conn:
    def __init__(self, versions):
        self.versions, self.sql = versions, []

    def execute(self, sql, params):
        self.sql.append(sql)
        exact, like = params
        assert like.endswith(".%") and like[:-2] == exact          # a LIKE on the dotted prefix, nothing broader
        hit = sum(1 for v in self.versions if v == exact or v.startswith(exact + "."))
        return type("Cur", (), {"fetchone": lambda s: (hit, len(self.versions))})()


def test_family_tokens_count_as_the_current_calc_version():
    conn = _Conn([CALC] * 2 + [f"{CALC}.20261019"] * 97 + ["20261112.20261019"])
    ok, text = probe.probe_db(conn, "calc_share", 0.95)
    assert ok and "99.0%" in text and "family token" in text and "LIKE" in conn.sql[0]


def test_an_older_version_does_not_count_even_if_it_starts_with_the_same_digits():
    conn = _Conn([f"{CALC}0"] * 50 + ["20261112.20261019"] * 50 + [CALC])        # `<CALC>0` is another version, not a family token of this one
    ok, text = probe.probe_db(conn, "calc_share", 0.95)
    assert not ok and "1.0%" in text


def test_an_empty_table_is_not_a_pass():
    ok, _t = probe.probe_db(_Conn([]), "calc_share", 0.95)
    assert not ok
