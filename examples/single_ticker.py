"""Fetch the latest ticker for one market against the public gateway.

python examples/single_ticker.py [MARKET_ID]
"""

from __future__ import annotations

import sys

from nexus_exchange import Client


def main() -> None:
    market_id = sys.argv[1] if len(sys.argv) > 1 else "BTC-USD"
    with Client() as client:
        ticker = client.fetch_ticker(market_id)
        print(f"ticker for {ticker.market_id}:")
        print(ticker.raw)


if __name__ == "__main__":
    main()
