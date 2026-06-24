"""List markets and fetch a ticker against the public gateway — no credentials.

python examples/public_market_data.py
"""

from __future__ import annotations

from nexus_exchange import Client


def main() -> None:
    with Client() as client:
        markets = client.fetch_markets()
        print(f"{len(markets)} markets")
        for market in markets[:5]:
            print(" -", market.market_id)

        if markets:
            ticker = client.fetch_ticker(markets[0].market_id)
            print(f"\nticker for {ticker.market_id}:")
            print(
                f"  last={ticker.last} mark={ticker.mark_price} bid={ticker.bid} ask={ticker.ask}"
            )


if __name__ == "__main__":
    main()
