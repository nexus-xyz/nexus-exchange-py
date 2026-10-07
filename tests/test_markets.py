"""Tick/lot rounding helpers (``nexus_exchange.markets``).

``CASES`` mirrors the ``round_*`` fixtures in nexus-exchange-rs
``tests/markets.rs`` (tick 0.5, lot 0.001), so a drift between the SDKs shows up
as a diff against that file. The ts and go SDKs carry the same table.

Not carried over: rs's ``Rounding::Nearest`` cases (no side maps to it) and its
``Decimal::MAX`` overflow case (Python's ``Decimal`` has no fixed width to
overflow). The "extra" rows are not in rs.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from nexus_exchange import Market, round_price, round_size
from nexus_exchange.markets import _round_to_increment

# (value, increment, away_from_zero (rs Rounding::Up, else Down), expected)
CASES = [
    # round_price_snaps_to_tick
    ("50000.30", "0.5", False, "50000"),
    ("50000.30", "0.5", True, "50000.5"),
    ("50000.5", "0.5", False, "50000.5"),
    # round_size_snaps_to_lot
    ("1.23456", "0.001", False, "1.234"),
    ("1.23456", "0.001", True, "1.235"),
    # round_is_sign_symmetric_for_negatives
    ("-50000.3", "0.5", False, "-50000"),
    ("-50000.3", "0.5", True, "-50000.5"),
    ("-1.23456", "0.001", False, "-1.234"),
    ("-1.23456", "0.001", True, "-1.235"),
    # zero_increment_passes_through
    ("50000.3", "0", False, "50000.3"),
    ("1.23456", "0", True, "1.23456"),
    # round_result_is_clean_scale
    ("50001.0", "0.5", False, "50001"),
    # extra: ticks that float division gets wrong (ENG-19697), and -0
    ("2345.1", "0.1", False, "2345.1"),
    ("0.3", "0.1", True, "0.3"),
    ("123.456", "0.01", False, "123.45"),
    ("123.456", "0.01", True, "123.46"),
    ("7", "0.25", True, "7"),
    ("-0.0004", "0.001", False, "0"),
]


@pytest.mark.parametrize(("value", "inc", "up", "want"), CASES)
def test_round_to_increment_matches_rust_fixtures(
    value: str, inc: str, up: bool, want: str
) -> None:
    # Compare the string: it is what goes on the wire, so "50001.0" or "5E+4"
    # would be a failure even though both equal 50001.
    assert str(_round_to_increment(Decimal(value), Decimal(inc), away_from_zero=up)) == want


def _market(tick: str, lot: str) -> Market:
    return Market.from_dict(
        {
            "id": "BTC-USDX-PERP",
            "base": "BTC",
            "quote": "USDX",
            "tick_size": tick,
            "lot_size": lot,
            "min_order_size": "0.01",
            "max_order_size": "100",
            "initial_margin_rate": "0.05",
            "maintenance_margin_rate": "0.03",
            "max_leverage": 20,
        }
    )


def test_round_price_is_side_aware_and_round_size_truncates() -> None:
    m = _market("0.5", "0.001")
    assert str(round_price(m, Decimal("50000.30"), "Buy")) == "50000"
    assert str(round_price(m, Decimal("50000.30"), "sell")) == "50000.5"
    assert str(round_size(m, Decimal("1.23456"))) == "1.234"
    with pytest.raises(ValueError):
        round_price(m, Decimal("50000.30"), "Long")
