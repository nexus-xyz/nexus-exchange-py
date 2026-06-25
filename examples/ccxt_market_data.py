"""CCXT-style public market data via the Nexus Exchange adapter.

The adapter follows CCXT's unified method names and return shapes, so this reads
like any CCXT public-data script. No credentials needed.

    python examples/ccxt_market_data.py
"""

from __future__ import annotations

from nexus_exchange.ccxt_adapter import NexusExchange


def main() -> None:
    with NexusExchange() as ex:
        print("capabilities:", {k: v for k, v in ex.describe()["has"].items() if v is True})

        markets = ex.load_markets()
        symbol = next(iter(markets), "BTC-USDX-PERP")
        print("first market:", symbol)

        ticker = ex.fetch_ticker(symbol)
        print("last:", ticker["last"], "bid/ask:", ticker["bid"], ticker["ask"])

        book = ex.fetch_order_book(symbol, limit=5)
        print("top bid:", book["bids"][:1], "top ask:", book["asks"][:1])

        candles = ex.fetch_ohlcv(symbol, timeframe="1m", limit=3)
        print("candles [ts,o,h,l,c,v]:", candles)

        trades = ex.fetch_trades(symbol, limit=3)
        print("recent trades:", [(t["side"], t["price"], t["amount"]) for t in trades])


if __name__ == "__main__":
    main()
