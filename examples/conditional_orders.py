"""Build the conditional order types and show the exact JSON each one sends.

Offline: no credentials, no network, no order placed. It builds a stop-loss and
take-profit pair for a long position, plus a trailing stop, and prints the
``POST /orders`` body for each, so you can see what ``trigger_price`` looks like
on the wire before sending one for real with ``client.create_order(...)``.

    python examples/conditional_orders.py [MARKET_ID]

MARKET_ID defaults to BTC-USDX-PERP.

The pinned spec says stop types fire when the mark crosses ``trigger_price`` in
the "adverse" direction and take-profit types in the "favorable" direction. It
does not say which way that is for each side. The prices below assume the
common reading for exiting a long (stop below the mark, take-profit above it).
That reading is an assumption, not a documented rule, so check it on a
play-funds network before relying on it.
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal

from nexus_exchange import OrderRequest


def main() -> None:
    market_id = sys.argv[1] if len(sys.argv) > 1 else "BTC-USDX-PERP"
    size = Decimal("0.01")

    legs = {
        # Stop-loss: fire a market sell if the mark falls through 58,000.
        "stop-loss (StopMarket)": OrderRequest.stop_market(
            market_id, "Sell", Decimal("58000"), size, reduce_only=True
        ),
        # Stop-loss variant that rests a limit at 57,900 once triggered.
        "stop-loss (StopLimit)": OrderRequest.stop_limit(
            market_id, "Sell", Decimal("58000"), Decimal("57900"), size, reduce_only=True
        ),
        # Take-profit: fire a market sell once the mark reaches 72,000.
        "take-profit (TakeProfitMarket)": OrderRequest.take_profit_market(
            market_id, "Sell", Decimal("72000"), size, reduce_only=True
        ),
        # Take-profit variant that rests a limit at 72,000 once triggered.
        "take-profit (TakeProfitLimit)": OrderRequest.take_profit_limit(
            market_id, "Sell", Decimal("72000"), Decimal("72000"), size, reduce_only=True
        ),
        # Trailing stop: market sell once the mark retraces 1.5% from its best.
        "trailing stop (TrailingStop)": OrderRequest.trailing_stop(
            market_id, "Sell", size, 150, reduce_only=True
        ),
    }
    for label, order in legs.items():
        print(f"{label}:\n{json.dumps(order.to_payload(), indent=2)}\n")

    # Missing fields fail locally, before any request is made.
    try:
        OrderRequest(market_id, "Sell", "StopMarket", size, "GTC")
    except ValueError as exc:
        print(f"rejected locally: {exc}")


if __name__ == "__main__":
    main()
