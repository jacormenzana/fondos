"""FND-0409: estimate_rotation_cost mixes two units. fund_master fees are RATIOS (0.05 == 5 %), rotation_costs and the defaults are PERCENT.
The gate (ROTATION_COST_GATE_ENABLED) is OFF, so this pins the cost before anyone switches it on. No database: a fake connection."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import portfolio_builder as pb  # noqa: E402


class _Conn:
    """Answers the per-fund query with (fund_master fee, rotation_costs fee) by ISIN, and records the statements."""

    def __init__(self, rows):
        self.rows, self.sql = rows, []

    def execute(self, sql, params=()):
        self.sql.append(sql)
        row = self.rows.get(params[0])
        return type("Cur", (), {"fetchone": lambda s: row})()


def test_a_per_fund_ratio_is_used_as_it_is():
    assert pb.fee_fraction(0.05, 1.0, 0.5) == 0.05            # a 5 % fee is 5 %, not 0.05 %


def test_a_per_nature_percent_is_divided_by_100():
    assert pb.fee_fraction(None, 1.0, 0.5) == 0.01


def test_the_default_percent_is_divided_by_100():
    assert pb.fee_fraction(None, None, 0.5) == 0.005


def test_a_stored_zero_is_a_real_zero_and_beats_the_fallbacks():
    assert pb.fee_fraction(0.0, 1.0, 0.5) == 0.0


def test_the_estimate_for_a_fund_with_entry_5_percent_and_a_nature_fallback():
    # out: no per-fund exit fee, nature row exit 0.5 % -> 0.005 ; in: per-fund entry ratio 0.05 -> 0.05 ; spread 0.001
    conn = _Conn({"OUT": (None, 0.5), "IN": (0.05, 0.0)})
    assert pb.estimate_rotation_cost(conn, "OUT", "IN") == round(0.005 + 0.05 + 0.001, 4)


def test_the_estimate_with_both_per_fund_values_and_none_stored():
    conn = _Conn({"OUT": (0.01, 1.0), "IN": (0.0433, 0.0)})
    assert pb.estimate_rotation_cost(conn, "OUT", "IN") == round(0.01 + 0.0433 + 0.001, 4)
    conn = _Conn({})                                          # unknown funds: the explicit defaults (0.5 % exit, 0 % entry)
    assert pb.estimate_rotation_cost(conn, "A", "B") == round(0.005 + 0.0 + 0.001, 4)


def test_the_queries_read_both_columns_and_do_not_coalesce_across_units():
    conn = _Conn({"OUT": (None, 0.5), "IN": (None, 0.0)})
    pb.estimate_rotation_cost(conn, "OUT", "IN")
    assert all("COALESCE" not in s.upper() for s in conn.sql)
